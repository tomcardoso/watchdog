"""Web research capture (#186) — the deterministic egress gate for /watchdog-research.

The interactive research skill curates URLs; this module is the single writer that turns a
URL into a sanitized source document under `_INCOMING/`, so web findings flow through the
normal chew → ingest pipeline (preserving dedup, provenance, and registry bookkeeping) and
never reach the vault as a direct note. Two properties make this safe and durable:

- **Fetching happens here, in Python, so URL validation runs *before* the network call** — the
  real SSRF guard (reject non-http(s) schemes and hosts that resolve to private/loopback/
  link-local space, re-checked on every redirect hop). The fetched body is sanitized before it
  becomes vault content, since fetched web text is an injection surface.
- **Each deposit is written synchronously the instant it is captured**, so a long research
  session that runs out of tokens never loses what it already pulled; `deposit_many` re-pulls a
  durable worklist idempotently (deposit filenames are a stable hash of the URL).

Provenance rides the existing `.yml` sidecar convention (orchestrate `_sidecar_provenance`):
`source`/`obtained` are stamped deterministically at ingest and the whole sidecar reaches the
extractor as `notes` context, then travels to the morgue. See ARCHITECTURE §15 and DECISIONS D45.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import yaml

from watchdog.pipeline.preprocess import DIRECT_TEXT_SUFFIXES, DOCLING_SUFFIXES
from watchdog.pipeline.write_vault import slugify


class ResearchError(Exception):
    """A URL was rejected or a fetch failed — nothing is written when this is raised."""


_ALLOWED_SCHEMES = {"http", "https"}
_MAX_BYTES_DEFAULT = 5 * 1024 * 1024  # 5 MiB — caps both the network read and the deposited file
_MAX_REDIRECTS = 5
_TIMEOUT = 30
_USER_AGENT = "watchdog-research/1.0 (+https://github.com/tomcardoso/watchdog)"

_SUPPORTED_SUFFIXES = DIRECT_TEXT_SUFFIXES | DOCLING_SUFFIXES
_HTML_EXTS = {".html", ".xhtml"}

# Content-Type → extension, restricted to suffixes chew already understands (preprocess.py).
_CONTENT_TYPE_EXT = {
    "text/html": ".html",
    "application/xhtml+xml": ".xhtml",
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "text/plain": ".txt",
    "text/csv": ".csv",
    "text/markdown": ".md",
    "application/xml": ".xml",
    "text/xml": ".xml",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/tiff": ".tiff",
    "image/bmp": ".bmp",
    "image/webp": ".webp",
}


# ── URL validation (SSRF guard) ───────────────────────────────────────────────

def _check_host_public(host: str) -> None:
    """Reject a host that resolves to any non-public address. Run before every fetch and on
    each redirect target, so a URL can never reach localhost, RFC1918, or link-local space."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise ResearchError(f"refused: cannot resolve host {host!r}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast or ip.is_reserved:
            raise ResearchError(f"refused: {host!r} resolves to non-public address {ip}")


def validate_url(url: str) -> str:
    """Return `url` if it is a fetchable public http(s) URL; raise ResearchError otherwise."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        got = f"{scheme}://" if scheme else "scheme-less"
        raise ResearchError(f"refused: only http/https URLs allowed (got {got} URL)")
    if not parts.hostname:
        raise ResearchError("refused: URL has no host")
    _check_host_public(parts.hostname)
    return url


# ── Fetch ─────────────────────────────────────────────────────────────────────

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Surface 3xx as HTTPError so we validate each redirect target ourselves."""

    def redirect_request(self, *args, **kwargs):  # noqa: D401
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def fetch(url: str, *, max_bytes: int = _MAX_BYTES_DEFAULT, timeout: int = _TIMEOUT,
          max_redirects: int = _MAX_REDIRECTS) -> tuple[bytes, str, str]:
    """Fetch `url` with SSRF-validated redirects and a hard body-size cap.

    Returns `(body, content_type, final_url)`. Raises ResearchError on any rejection or failure.
    """
    current = validate_url(url)
    for _ in range(max_redirects + 1):
        req = urllib.request.Request(current, headers={"User-Agent": _USER_AGENT})
        try:
            resp = _opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            location = e.headers.get("Location") if e.headers else None
            if e.code in (301, 302, 303, 307, 308) and location:
                current = validate_url(urljoin(current, location))
                continue
            raise ResearchError(f"refused: HTTP {e.code} for {current}")
        except (urllib.error.URLError, ssl.SSLError, OSError) as e:
            raise ResearchError(f"refused: could not fetch {current}: {e}")
        with resp:
            body = resp.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise ResearchError(f"refused: response exceeds {max_bytes}-byte cap")
            return body, resp.headers.get_content_type(), current
    raise ResearchError("refused: too many redirects")


def extension_for(content_type: str, url: str) -> str:
    """Pick a chew-supported extension from the Content-Type, falling back to the URL suffix."""
    ext = _CONTENT_TYPE_EXT.get((content_type or "").split(";")[0].strip().lower())
    if ext:
        return ext
    suffix = Path(urlsplit(url).path).suffix.lower()
    if suffix in _SUPPORTED_SUFFIXES:
        return suffix
    raise ResearchError(f"refused: unsupported content type {content_type!r} for {url}")


# ── Sanitization ──────────────────────────────────────────────────────────────

_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script\s*>", re.IGNORECASE | re.DOTALL)
_IFRAME_RE = re.compile(r"<iframe\b[^>]*>.*?</iframe\s*>", re.IGNORECASE | re.DOTALL)
_SCRIPT_TAG_RE = re.compile(r"</?script\b[^>]*/?>", re.IGNORECASE)
_IFRAME_TAG_RE = re.compile(r"</?iframe\b[^>]*/?>", re.IGNORECASE)


def sanitize_html(text: str) -> str:
    """Strip <script>/<iframe> blocks (and any stray, unclosed tags) from fetched HTML."""
    text = _SCRIPT_RE.sub("", text)
    text = _IFRAME_RE.sub("", text)
    text = _SCRIPT_TAG_RE.sub("", text)
    text = _IFRAME_TAG_RE.sub("", text)
    return text


def neutralize(value: str) -> str:
    """Defang wikilink and frontmatter-delimiter injection in a sidecar value.

    The sidecar reaches the extractor as `notes` context, and some values (the URL, the page
    title) are attacker-influenced. `yaml.safe_dump` already quotes YAML-special characters, so
    this is belt-and-suspenders: break `[[ ]]` so a crafted title can't forge a wikilink, and
    collapse whitespace so a value can't introduce a stray `---` frontmatter break."""
    return " ".join(value.replace("[[", "[ [").replace("]]", "] ]").split())


def build_sidecar(*, source: str, title: str, source_type: str, relevance: str,
                  obtained: str) -> str:
    """Render the provenance `.yml` sidecar. `source`/`obtained` are stamped deterministically at
    ingest; `retrieved_by`/`source_type`/`title`/`relevance` reach the extractor as notes."""
    data = {
        "source": source,
        "obtained": obtained,
        "retrieved_by": "research-mode",
        "source_type": neutralize(source_type) if source_type else "unverified",
        "title": neutralize(title) if title else "",
        "relevance": neutralize(relevance) if relevance else "",
    }
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


# ── Deposit ───────────────────────────────────────────────────────────────────

@dataclass
class Deposit:
    url: str
    path: Path | None
    error: str | None = None


def _deposit_name(url: str, title: str) -> str:
    """A filesystem-safe, stable-per-URL stem: `<slug>-<sha1[:8]>`. Stability makes re-pulling a
    worklist idempotent (a re-pull overwrites the same _INCOMING file rather than duplicating)."""
    base = slugify(title) or slugify(Path(urlsplit(url).path).stem) or "web-source"
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    return f"{base[:60]}-{digest}"


def deposit_one(vault: Path, url: str, *, title: str = "", source_type: str = "",
                relevance: str = "", max_bytes: int = _MAX_BYTES_DEFAULT,
                obtained: str | None = None, fetcher=fetch) -> Path:
    """Validate, fetch, sanitize, and write a source document + its `.yml` sidecar to
    `_INCOMING/`. Returns the deposited document path; raises ResearchError on rejection."""
    obtained = obtained or date.today().isoformat()
    body, content_type, final_url = fetcher(url, max_bytes=max_bytes)
    ext = extension_for(content_type, final_url)
    if ext in _HTML_EXTS:
        body = sanitize_html(body.decode("utf-8", "replace")).encode("utf-8")

    incoming = vault / "_INCOMING"
    incoming.mkdir(parents=True, exist_ok=True)
    name = _deposit_name(final_url, title)
    doc_path = incoming / f"{name}{ext}"
    doc_path.write_bytes(body)
    (incoming / f"{name}{ext}.yml").write_text(
        build_sidecar(source=final_url, title=title, source_type=source_type,
                      relevance=relevance, obtained=obtained),
        encoding="utf-8",
    )
    return doc_path


def parse_worklist(text: str) -> list[dict]:
    """Parse a TSV worklist: `url[<TAB>title[<TAB>source_type[<TAB>relevance]]]` per line.
    Blank lines and `#` comments are skipped."""
    entries = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cols = line.split("\t")
        entries.append({
            "url": cols[0].strip(),
            "title": cols[1].strip() if len(cols) > 1 else "",
            "source_type": cols[2].strip() if len(cols) > 2 else "",
            "relevance": cols[3].strip() if len(cols) > 3 else "",
        })
    return entries


def deposit_many(vault: Path, entries: list[dict], *, max_bytes: int = _MAX_BYTES_DEFAULT,
                 fetcher=fetch) -> list[Deposit]:
    """Deposit every entry, continuing past individual failures so one bad URL can't lose the
    rest of the list. Idempotent: re-pulling overwrites same-named deposits."""
    results = []
    for e in entries:
        try:
            path = deposit_one(vault, e["url"], title=e.get("title", ""),
                               source_type=e.get("source_type", ""), relevance=e.get("relevance", ""),
                               max_bytes=max_bytes, fetcher=fetcher)
            results.append(Deposit(e["url"], path))
        except ResearchError as ex:
            results.append(Deposit(e["url"], None, str(ex)))
    return results
