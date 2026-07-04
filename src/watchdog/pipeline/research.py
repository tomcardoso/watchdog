"""Web research capture (#186) — the deterministic egress gate for /watchdog-research.

The interactive research skill curates URLs; this module is the single writer that turns a
URL into a sanitized source document under `_INCOMING/`, so web findings flow through the
normal chew → ingest pipeline (preserving dedup, provenance, and registry bookkeeping) and
never reach the vault as a direct note. Two properties make this safe and durable:

- **Fetching happens here, in Python, so URL validation runs *before* the network call** — the
  real SSRF guard (reject non-http(s) schemes and hosts that resolve to private/loopback/
  link-local space, re-checked on every redirect hop). The fetched body is sanitized before it
  becomes vault content, since fetched web text is an injection surface: HTML gets a rendered,
  script-stripped Chromium capture when Playwright is available (`pipeline/capture.py`, #200), or
  else an nh3-cleaned plain fetch.
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
import json
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import nh3
import yaml

from watchdog.pipeline.preprocess import DIRECT_TEXT_SUFFIXES, DOCLING_SUFFIXES
from watchdog.pipeline.write_vault import slugify


class ResearchError(Exception):
    """A URL was rejected or a fetch failed — nothing is written when this is raised."""


_ALLOWED_SCHEMES = {"http", "https"}
_MAX_BYTES_DEFAULT = 20 * 1024 * 1024  # 20 MiB — caps both the network read and the deposited file
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
                raise ResearchError(
                    f"refused: {current} exceeds the {max_bytes // (1024 * 1024)} MiB download cap")
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

def sanitize_html(text: str) -> str:
    """Strip scripts, iframes, event handlers, and other active/exfil surface from fetched HTML
    with nh3 (a Rust `ammonia` binding), while keeping document structure — headings, tables,
    links — intact for Docling. This is the fallback path's sanitizer (#200); a rendered capture
    (`pipeline/capture.py`) is already script-stripped and asset-inlined by the DOM rewrite that
    produces it, so it never reaches this function."""
    return nh3.clean(text)


def neutralize(value: str) -> str:
    """Defang wikilink and frontmatter-delimiter injection in a sidecar value.

    The sidecar reaches the extractor as `notes` context, and some values (the URL, the page
    title) are attacker-influenced. `yaml.safe_dump` already quotes YAML-special characters, so
    this is belt-and-suspenders: break `[[ ]]` so a crafted title can't forge a wikilink, and
    collapse whitespace so a value can't introduce a stray `---` frontmatter break."""
    return " ".join(value.replace("[[", "[ [").replace("]]", "] ]").split())


def build_sidecar(*, source: str, title: str, source_type: str, relevance: str,
                  obtained: str, archived: str = "", capture: str = "",
                  retrieved_by: str = "research-mode") -> str:
    """Render the provenance `.yml` sidecar. `source`/`obtained` are stamped deterministically at
    ingest; `retrieved_by`/`source_type`/`title`/`relevance` reach the extractor as notes.
    `retrieved_by` records how the source was acquired (`research-mode` vs `fetch`). When the source
    was saved to the Wayback Machine (#201), `archived` carries the snapshot URL. `capture` (#200)
    records how an HTML body was obtained — `rendered` (Playwright/Chromium snapshot) or `plain`
    (nh3-sanitized fetch fallback); omitted for non-HTML deposits."""
    data = {
        "source": source,
        "obtained": obtained,
        "retrieved_by": retrieved_by,
        "source_type": neutralize(source_type) if source_type else "unverified",
        "title": neutralize(title) if title else "",
        "relevance": neutralize(relevance) if relevance else "",
    }
    if archived:
        data["archived"] = archived
    if capture:
        data["capture"] = capture
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


# ── Wayback Machine — Save Page Now (#201) ─────────────────────────────────────
# Optional, off by default, gated on archive.org S3 keys set via `watchdog configure`. A best-effort
# provenance win: alongside the local deposit, ask the Wayback Machine to archive the source so a
# citable public snapshot survives even if the original is later changed or taken down. The snapshot
# URL is recorded in the `.yml` sidecar's `archived:` field. Never fails a deposit — archiving is a
# bonus, not a gate.

_WAYBACK_SAVE_URL = "https://web.archive.org/save"
_WAYBACK_TIMEOUT = 30


def save_to_wayback(url: str, access_key: str, secret_key: str, *,
                    timeout: int = _WAYBACK_TIMEOUT, opener=urllib.request.urlopen) -> str | None:
    """Submit `url` to the Wayback Machine's Save Page Now (SPN2) API and return a citable snapshot
    URL, or None if the submission didn't succeed. Best-effort: catches every error and returns None
    rather than raising, so archiving never sinks a deposit. Fires the save and records the
    latest-capture URL (`…/web/<url>`, which resolves to the newest snapshot once the async job
    completes) — it does not poll for the timestamped permalink, keeping the download loop fast."""
    try:
        data = urllib.parse.urlencode({"url": url}).encode("utf-8")
        req = urllib.request.Request(
            _WAYBACK_SAVE_URL, data=data,
            headers={
                "Accept": "application/json",
                "Authorization": f"LOW {access_key}:{secret_key}",
                "User-Agent": _USER_AGENT,
            },
        )
        with opener(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, ssl.SSLError, OSError, ValueError):
        return None
    if not isinstance(body, dict) or not body.get("job_id"):
        return None  # no job accepted (bad keys, rate limit) — record nothing rather than a dead link
    return f"https://web.archive.org/web/{url}"


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
                obtained: str | None = None, fetcher=fetch,
                wayback: tuple[str, str] | None = None,
                retrieved_by: str = "research-mode") -> Path:
    """Validate, fetch, sanitize, and write a source document + its `.yml` sidecar to
    `_INCOMING/`. Returns the deposited document path; raises ResearchError on rejection. HTML
    deposits first try a rendered Chromium capture (`pipeline/capture.py`, #200) — script-stripped,
    assets inlined as data URIs; when Playwright isn't installed or the render fails for any reason,
    falls back to the plain, nh3-sanitized fetch body. Either way the sidecar's `capture` field
    records which path was taken (`rendered` / `plain`). When `wayback` (access_key, secret_key) is
    given, best-effort-archives the source to the Wayback Machine and records the snapshot URL in
    the sidecar (#201). `retrieved_by` records the acquisition path in the sidecar (`research-mode`
    vs `fetch`)."""
    obtained = obtained or date.today().isoformat()
    body, content_type, final_url = fetcher(url, max_bytes=max_bytes)
    ext = extension_for(content_type, final_url)
    capture_mode = ""
    if ext in _HTML_EXTS:
        from watchdog.pipeline import capture
        rendered = capture.try_render(final_url, max_bytes=max_bytes)
        if rendered is not None:
            body = rendered
            capture_mode = "rendered"
        else:
            body = sanitize_html(body.decode("utf-8", "replace")).encode("utf-8")
            capture_mode = "plain"

    archived = save_to_wayback(final_url, wayback[0], wayback[1]) or "" if wayback else ""

    incoming = vault / "_INCOMING"
    incoming.mkdir(parents=True, exist_ok=True)
    name = _deposit_name(final_url, title)
    doc_path = incoming / f"{name}{ext}"
    doc_path.write_bytes(body)
    (incoming / f"{name}{ext}.yml").write_text(
        build_sidecar(source=final_url, title=title, source_type=source_type,
                      relevance=relevance, obtained=obtained, archived=archived,
                      capture=capture_mode, retrieved_by=retrieved_by),
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
                 fetcher=fetch, wayback: tuple[str, str] | None = None,
                 retrieved_by: str = "research-mode") -> list[Deposit]:
    """Deposit every entry, continuing past individual failures so one bad URL can't lose the
    rest of the list. Idempotent: re-pulling overwrites same-named deposits. `wayback` credentials,
    when given, archive each source to the Wayback Machine (#201). `retrieved_by` records the
    acquisition path (`research-mode` vs `fetch`) in each sidecar."""
    results = []
    for e in entries:
        try:
            path = deposit_one(vault, e["url"], title=e.get("title", ""),
                               source_type=e.get("source_type", ""), relevance=e.get("relevance", ""),
                               max_bytes=max_bytes, fetcher=fetcher, wayback=wayback,
                               retrieved_by=retrieved_by)
            results.append(Deposit(e["url"], path))
        except ResearchError as ex:
            results.append(Deposit(e["url"], None, str(ex)))
    return results


# ── Durable worklist store (#196) ──────────────────────────────────────────────
# The pending-download list is the one URL-specific piece of state: a queued URL has no file yet,
# so — unlike a pending PDF, which is tracked by its presence in _INCOMING/ — it needs an explicit
# store. It lives under `.watchdog/research/` (durable), not `.watchdog/tmp/` (swept by setup), so a
# crashed session's queued URLs survive. Once downloaded a URL becomes an _INCOMING/ file and is
# tracked like any pending document; once ingested it lands in documents.json `source`. There is no
# separate "done" ledger — done-ness is derived from those artifacts (see `seen_urls`).

QUEUE_REL = Path(".watchdog") / "research" / "queue.tsv"
_OLD_QUEUE_REL = Path(".watchdog") / "tmp" / "research-queue.tsv"  # pre-#196 location, read once


def queue_path(vault: Path) -> Path:
    return vault / QUEUE_REL


def read_queue_text(vault: Path) -> str | None:
    """Return the worklist text, falling back to the pre-#196 `.watchdog/tmp/` path so a queue left
    by an older session isn't lost across the move. Returns None when no worklist exists."""
    new = queue_path(vault)
    if new.exists():
        return new.read_text(encoding="utf-8")
    old = vault / _OLD_QUEUE_REL
    if old.exists():
        return old.read_text(encoding="utf-8")
    return None


def pending_count(vault: Path) -> int:
    """Count queued-but-not-downloaded URLs — what the `watchdog`/`chew`/`status` warnings surface."""
    text = read_queue_text(vault)
    return len(parse_worklist(text)) if text else 0


def serialize_worklist(entries: list[dict]) -> str:
    """Inverse of `parse_worklist`: render entries back to TSV, trimming trailing empty columns."""
    lines = []
    for e in entries:
        cols = [e.get("url", ""), e.get("title", ""), e.get("source_type", ""), e.get("relevance", "")]
        while len(cols) > 1 and not cols[-1]:
            cols.pop()
        lines.append("\t".join(cols))
    return "\n".join(lines) + "\n" if lines else ""


def retain_pending(vault: Path, keep: list[dict]) -> None:
    """Rewrite the worklist to just `keep` (the rows still to download); delete it when empty. Used
    after a download pass to drop the URLs now captured while leaving failures queued for retry."""
    path = queue_path(vault)
    old = vault / _OLD_QUEUE_REL
    if not keep:
        path.unlink(missing_ok=True)
        old.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(serialize_worklist(keep), encoding="utf-8")
    old.unlink(missing_ok=True)  # consumed the fallback; the durable path is now canonical


def seen_urls(vault: Path) -> set[str]:
    """URLs already captured — so research can skip re-fetching them. Derived, not stored: the union
    of every ingested document's `source` (documents.json) and every in-flight `_INCOMING/**.yml`
    sidecar `source` (downloaded, not yet ingested). Mirrors how chew dedups against the registry."""
    urls: set[str] = set()

    docs_file = vault / ".watchdog" / "Registry" / "documents.json"
    if docs_file.exists():
        try:
            for entry in json.loads(docs_file.read_text(encoding="utf-8")).values():
                src = entry.get("source") if isinstance(entry, dict) else None
                if src:
                    urls.add(str(src))
        except (OSError, json.JSONDecodeError, AttributeError):
            pass

    incoming = vault / "_INCOMING"
    if incoming.is_dir():
        for sidecar in incoming.rglob("*.yml"):
            try:
                data = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError):
                continue
            if isinstance(data, dict) and data.get("source"):
                urls.add(str(data["source"]))

    return urls
