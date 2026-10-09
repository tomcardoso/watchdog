"""Shared helpers for the desktop app's read-side API modules.

Everything here is a pure function of vault files — no model calls, no writes except
`write_text_atomic`, which only the narrow, spec-listed mutations use (`vault.saveNotes`,
`vault.writeFile`). The helpers are deliberately forgiving: a new empty vault, an old vault
without `morgue_path`, a missing extracted JSON or a half-written note all yield empty values
rather than exceptions, because the app calls these on every navigation.

Path safety lives here too (`resolve_in_vault`): any path that arrives from the app is relative to
a vault, and a note path can be typed by a user into a wikilink, so `..`, absolute paths and
symlinks that lead out of the vault are refused.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import yaml

from watchdog.gui.rpc import RpcError
from watchdog.pipeline.json_io import _read_json_or
from watchdog.vault_paths import ensure_current_layout_once, is_vault

# ── small text helpers ───────────────────────────────────────────────────────────

# CSI sequences (colours, cursor) and OSC sequences (the OSC 8 terminal hyperlinks `note_link`
# emits), which the CLI's `_format_*` helpers can contain when colour is on.
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text or "")


_PAGE_MARKER = re.compile(r"<!--\s*PAGE\s+(\d+)\s*-->")
_WIKILINK = re.compile(r"^\[\[(?P<target>[^|\]]+)(?:\|(?P<text>.+))?\]\]$")
_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_HEADING_H2 = re.compile(r"^##[ \t]+(.+?)[ \t]*#*[ \t]*$")
_FENCE = re.compile(r"^\s{0,3}(```|~~~)")

SHA_PREFIX_MIN = 6


def plain(value: Any) -> Any:
    """YAML/JSON values made JSON-safe: dates become ISO strings, keys become strings."""
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [plain(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def split_frontmatter(text: str) -> tuple[dict, str]:
    """`(frontmatter dict, body)` of a note. Broken or absent YAML reads as `{}` with the
    remaining text as the body — a hand-edited note must never break a screen."""
    m = _FRONTMATTER.match(text or "")
    if not m:
        return {}, text or ""
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        data = None
    return (plain(data) if isinstance(data, dict) else {}), text[m.end():]


def split_sections(body: str) -> dict[str, str]:
    """`{lowercased ## heading: raw markdown}` for a note body, in order.

    Headings inside code fences don't split. Everything from `## Notes` to the end of the file
    belongs to `notes` — the same rule `write_vault._extract_notes_section` applies when it
    preserves the journalist's annotations — so a `## ` heading typed inside the notes stays
    part of them. A repeated heading keeps its first occurrence."""
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    in_fence = False

    def flush() -> None:
        if current is not None and current not in sections:
            sections[current] = "\n".join(buf).strip()

    for line in (body or "").splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        m = None if in_fence or current == "notes" else _HEADING_H2.match(line)
        if m:
            flush()
            current, buf = m.group(1).strip().lower(), []
        elif current is not None:
            buf.append(line)
    flush()
    return sections


def first_paragraph(text: str | None) -> str | None:
    """The first paragraph of a markdown block, with blank lines and HTML comments dropped."""
    if not text:
        return None
    cleaned = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()
    if not cleaned:
        return None
    return re.split(r"\n\s*\n", cleaned, maxsplit=1)[0].strip() or None


def note_title(frontmatter: dict, body: str) -> str | None:
    for key in ("title", "name"):
        v = frontmatter.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    in_fence = False
    for line in (body or "").splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        if not in_fence and line.startswith("# "):
            return line[2:].strip() or None
    return None


def wikilink_text(link: str | None) -> str | None:
    """Display text of a `[[target|text]]` link; any other string comes back unchanged."""
    if not link:
        return None
    m = _WIKILINK.match(link.strip())
    if not m:
        return link
    return m.group("text") or m.group("target")


def wikilink_target(link: str | None) -> str | None:
    if not link:
        return None
    m = _WIKILINK.match(link.strip())
    return m.group("target") if m else None


def split_pages(text: str) -> list[dict]:
    """A morgue full-text sibling split on `<!-- PAGE n -->`. Text with no markers is one page."""
    if not (text or "").strip():
        return []
    parts = _PAGE_MARKER.split(text)
    if len(parts) == 1:
        return [{"page": 1, "text": text.strip()}]
    pages = []
    # parts = [preamble, n1, text1, n2, text2, ...]
    for i in range(1, len(parts) - 1, 2):
        pages.append({"page": int(parts[i]), "text": parts[i + 1].strip()})
    return pages


def precision(date: str | None) -> str | None:
    n = len((date or "").strip())
    return {4: "year", 7: "month", 10: "day"}.get(n)


def date_sort_key(date: str | None) -> str:
    """Pad a partial date so a plain string sort is chronological (`write_vault._date_sort_key`)."""
    d = (date or "").strip()
    if len(d) == 4:
        return d + "-00-00"
    if len(d) == 7:
        return d + "-00"
    return d


# ── vault and path validation ────────────────────────────────────────────────────

def require_granted(path: Path) -> None:
    """`RpcError(code="not_granted")` when the app enforces folder access (watchdog/access.py) and
    the user hasn't allowed Watchdog to work in `path`. The app answers it by asking the user."""
    from watchdog import access
    if access.enforced() and not access.is_granted(path):
        raise RpcError(f"Watchdog hasn't been allowed to work in {path} yet.", code="not_granted",
                       data={"path": str(path)})


def require_vault(path: Any) -> Path:
    """The vault folder `path` names, or `RpcError(code="not_a_vault")`."""
    if not isinstance(path, str) or not path.strip():
        raise RpcError("No investigation folder was given.", code="not_a_vault")
    p = Path(path).expanduser()
    if not p.is_absolute():
        raise RpcError("The investigation folder must be an absolute path.", code="not_a_vault")
    if not p.is_dir() or not is_vault(p):
        raise RpcError(f"{p} is not a Watchdog investigation folder.", code="not_a_vault")
    require_granted(p)
    ensure_current_layout_once(p)       # an older vault's folders are renamed on first open (D266)
    return p


def resolve_in_vault(vault: Path, rel: Any) -> Path:
    """`vault / rel`, refusing anything that would leave the vault: absolute paths, `..`
    segments, NUL bytes, and symlinks (at any depth) that resolve outside it. The result need
    not exist."""
    if not isinstance(rel, str) or not rel.strip() or "\x00" in rel:
        raise RpcError("That path isn't valid.", code="bad_path")
    text = rel.strip().replace("\\", "/")
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        raise RpcError("Paths inside an investigation are relative to its folder.", code="bad_path")
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise RpcError("That path leaves the investigation folder.", code="bad_path")
    root = Path(os.path.realpath(vault))
    target = Path(os.path.realpath(root.joinpath(*parts)))
    if target != root and root not in target.parents:
        raise RpcError("That path leaves the investigation folder.", code="bad_path")
    return target


def rel_posix(vault: Path, path: Path) -> str:
    try:
        return Path(os.path.realpath(path)).relative_to(os.path.realpath(vault)).as_posix()
    except ValueError:
        return path.as_posix()


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def write_text_atomic(path: Path, text: str) -> None:
    """Write `text` to `path` through a temp file in the same folder and `os.replace`, so a
    crash or a concurrent reader never sees a half-written note."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        try:
            os.chmod(tmp, path.stat().st_mode & 0o777)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_json(path: Path, default: Any) -> Any:
    return _read_json_or(path, default, catch=(OSError, ValueError))


# ── registered project lookup ────────────────────────────────────────────────────

def registered_projects() -> dict:
    """`projects.json` as a dict; a corrupt or unreadable file reads as empty."""
    from watchdog.cmd.base import load_projects
    try:
        data = load_projects()
    except SystemExit:
        return {}
    return data if isinstance(data, dict) else {}


def project_for_vault(vault: Path) -> tuple[str, dict] | None:
    target = os.path.realpath(vault)
    for slug, info in registered_projects().items():
        try:
            if os.path.realpath(info["path"]) == target:
                return slug, info
        except (KeyError, TypeError, OSError):
            continue
    return None


def vault_name(vault: Path) -> str:
    found = project_for_vault(vault)
    return (found[1].get("name") if found else None) or vault.name


# ── registries ───────────────────────────────────────────────────────────────────

def registry_dir(vault: Path) -> Path:
    return vault / ".watchdog" / "registry"


def load_documents(vault: Path) -> dict:
    data = read_json(registry_dir(vault) / "documents.json", {})
    return data if isinstance(data, dict) else {}


def load_entities(vault: Path) -> dict:
    data = read_json(registry_dir(vault) / "entities.json", {})
    return data if isinstance(data, dict) else {}


def entity_type(raw: str | None) -> str:
    """The six canonical types (or `other`): registry values are already canonical, but a vault
    from before the closed vocabulary may not be."""
    from watchdog.pipeline.entity_type import canonical_type
    return canonical_type(raw or "")


def entity_ref(ents: dict, eid: str) -> dict:
    e = ents.get(eid)
    if not e:
        return {"id": eid, "name": eid, "type": "other"}
    return {"id": eid, "name": e.get("name") or eid, "type": entity_type(e.get("type"))}


def resolve_sha(docs: dict, sha: Any) -> str:
    """A full document sha, from a full sha or a unique prefix (at least six characters)."""
    if not isinstance(sha, str) or not sha:
        raise RpcError("No document was named.", code="not_found")
    if sha in docs:
        return sha
    if len(sha) >= SHA_PREFIX_MIN:
        matches = [k for k in docs if k.startswith(sha)]
        if len(matches) == 1:
            return matches[0]
    raise RpcError("That document isn't in this investigation.", code="not_found")


def doc_note_stem(rec: dict) -> str | None:
    note = rec.get("document_note")
    return note[:-3] if isinstance(note, str) and note.endswith(".md") else (note or None)


# ── parsed notes (cached on mtime) ───────────────────────────────────────────────

_NOTE_CACHE: dict[str, tuple[int, int, dict]] = {}
_NOTE_CACHE_MAX = 20000


def parse_note_file(path: Path) -> dict | None:
    """`{frontmatter, body, sections}` of a note file, or None when it doesn't exist. Cached on
    (mtime, size), so listing thousands of documents re-reads only what changed."""
    try:
        st = path.stat()
    except OSError:
        return None
    key = str(path)
    hit = _NOTE_CACHE.get(key)
    if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2]
    text = read_text(path)
    fm, body = split_frontmatter(text)
    parsed = {"frontmatter": fm, "body": body, "sections": split_sections(body)}
    if len(_NOTE_CACHE) >= _NOTE_CACHE_MAX:
        _NOTE_CACHE.clear()
    _NOTE_CACHE[key] = (st.st_mtime_ns, st.st_size, parsed)
    return parsed


def note_file(vault: Path, note: str | None) -> Path | None:
    """The `.md` file behind a registry `note_path` (which omits the extension)."""
    if not note:
        return None
    rel = note if note.endswith(".md") else f"{note}.md"
    try:
        return resolve_in_vault(vault, rel)
    except RpcError:
        return None


def extracted(vault: Path, sha: str) -> dict:
    """The staged extraction artifact for a document, or `{}`."""
    data = read_json(vault / ".watchdog" / "extracted" / f"{sha}.json", {})
    return data if isinstance(data, dict) else {}


# ── row builders ─────────────────────────────────────────────────────────────────

def _ext(filename: str | None) -> str:
    return Path(filename or "").suffix.lower().lstrip(".")


def _vault_file(vault: Path, rel: str | None) -> bool:
    if not rel:
        return False
    try:
        return resolve_in_vault(vault, rel).is_file()
    except RpcError:
        return False


def document_paths(vault: Path, rec: dict) -> tuple[str | None, str | None]:
    """`(original, fulltext)` vault-relative paths from the registry's `morgue_path`. The
    original is reported as registered; the full-text sibling only when it exists."""
    morgue = rec.get("morgue_path")
    if not isinstance(morgue, str) or not morgue.strip():
        return None, None
    original = morgue.replace("\\", "/")
    sibling = str(Path(original).with_suffix(".md")).replace("\\", "/")
    fulltext = sibling if sibling != original and _vault_file(vault, sibling) else None
    return original, fulltext


def _doc_text_fields(vault: Path, sha: str, rec: dict) -> dict:
    """Fields the registry doesn't carry — date, source, obtained, summary — from the document
    note, else the staged extraction."""
    parsed = parse_note_file(note_file(vault, rec.get("document_note")) or Path("/nonexistent"))
    if parsed:
        fm = parsed["frontmatter"]
        summary = parsed["sections"].get("summary")
        return {"date_of_document": fm.get("date_of_document"), "source": fm.get("source"),
                "obtained": fm.get("obtained"), "summary": summary or None}
    doc = (extracted(vault, sha).get("document") or {})
    return {"date_of_document": doc.get("date_of_document"), "source": doc.get("source"),
            "obtained": doc.get("obtained"), "summary": doc.get("summary") or None}


def _str_or_none(v: Any) -> str | None:
    if v is None or v == "":
        return None
    return v if isinstance(v, str) else str(v)


def document_row(vault: Path, sha: str, rec: dict) -> dict:
    """The app's `DocumentRow` for one `documents.json` record."""
    original, fulltext = document_paths(vault, rec)
    fields = _doc_text_fields(vault, sha, rec)
    return {
        "sha": sha,
        "filename": rec.get("filename") or sha[:12],
        "title": _str_or_none(rec.get("title")),
        "document_type": _str_or_none(rec.get("document_type")),
        "date_of_document": _str_or_none(plain(fields["date_of_document"])),
        "page_count": rec.get("page_count") if isinstance(rec.get("page_count"), int) else None,
        "record_skill": _str_or_none(rec.get("record_skill")),
        "ingested_at": _str_or_none(rec.get("ingested_at")),
        "near_duplicate_of": _str_or_none(wikilink_text(rec.get("near_duplicate_of"))),
        "note": doc_note_stem(rec),
        "original": original,
        "fulltext": fulltext,
        "ext": _ext(rec.get("filename")),
        "entity_count": len(rec.get("entities_extracted") or []),
        "source": _str_or_none(fields["source"]),
        "obtained": _str_or_none(plain(fields["obtained"])),
        "summary": _str_or_none(fields["summary"]),
        "media_kind": (media_info(rec) or {}).get("kind"),
        "duration_seconds": (media_info(rec) or {}).get("duration_seconds"),
    }


def media_info(rec: dict) -> dict | None:
    """A recording's `media` block (D273) as the app reads it: kind, duration and each page's
    time range, plus what transcribed it. Reads only the fields it knows, whatever the block's
    `format`, so a newer block still shows; None for a document that is not a recording."""
    media = rec.get("media") if isinstance(rec, dict) else None
    if not isinstance(media, dict):
        return None

    def num(v):
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    pages = []
    for p in media.get("pages") or []:
        if isinstance(p, dict) and isinstance(p.get("page"), int) and num(p.get("start")) is not None:
            end = num(p.get("end"))
            pages.append({"page": p["page"], "start": num(p["start"]),
                          "end": end if end is not None else num(p["start"])})
    tx = media.get("transcription") if isinstance(media.get("transcription"), dict) else {}
    kind = media.get("kind")
    return {
        "kind": kind if kind in ("audio", "video") else "audio",
        "duration_seconds": num(media.get("duration_seconds")),
        "page_seconds": num(media.get("page_seconds")),
        "pages": pages,
        "language": _str_or_none(media.get("language")),
        "model": _str_or_none(tx.get("model")),
    }


def sorted_document_rows(vault: Path, docs: dict | None = None, limit: int | None = None) -> list[dict]:
    """Documents newest-ingested first (ties by filename). Sorted on registry fields before any
    row is built, so `limit` only reads the notes it returns."""
    docs = load_documents(vault) if docs is None else docs
    items = [(sha, rec) for sha, rec in docs.items() if isinstance(rec, dict)]
    items.sort(key=lambda kv: (kv[1].get("filename") or kv[0]).lower())
    items.sort(key=lambda kv: kv[1].get("ingested_at") or "", reverse=True)
    if limit is not None:
        items = items[:limit]
    return [document_row(vault, sha, rec) for sha, rec in items]


def unresolved_contradictions(ent: dict, resolved: frozenset[str]) -> list[str]:
    from watchdog.pipeline import resolutions
    return [c for c in (ent.get("contradictions") or [])
            if isinstance(c, str) and resolutions.contradiction_id(c) not in resolved]


def entity_row(vault: Path, eid: str, ent: dict, resolved: frozenset[str]) -> dict:
    """The app's `EntityRow` for one `entities.json` record."""
    note_stem = ent.get("note_path")
    synthesis = ent.get("synthesis") if isinstance(ent.get("synthesis"), dict) else None
    if synthesis is not None or "synthesis" in ent:
        # The AI-written summary lives in the registry (D280); the note is a view of it.
        summary_text = (synthesis or {}).get("summary")
    else:   # a note written before D280
        parsed = parse_note_file(note_file(vault, note_stem) or Path("/nonexistent"))
        summary_text = (parsed or {}).get("sections", {}).get("summary") if parsed else None
    summary = first_paragraph(summary_text)
    return {
        "id": eid,
        "name": ent.get("name") or eid,
        "type": entity_type(ent.get("type")),
        "aliases": [a for a in (ent.get("aliases") or []) if isinstance(a, str)],
        "doc_count": len(ent.get("appears_in") or []),
        "role_count": len(ent.get("roles") or []),
        "contradiction_count": len(unresolved_contradictions(ent, resolved)),
        "first_seen": _str_or_none(ent.get("date_first_seen")),
        "last_updated": _str_or_none(ent.get("date_last_updated")),
        "note": note_stem[:-3] if isinstance(note_stem, str) and note_stem.endswith(".md") else (note_stem or None),
        "has_summary": bool(summary),
        "summary": summary,
    }


def sorted_entity_rows(vault: Path, ents: dict | None = None, limit: int | None = None) -> list[dict]:
    """Entities with the most documents first, then by name — sorted on registry fields before
    any row is built, so `limit` only reads the notes it returns."""
    from watchdog.pipeline import resolutions
    ents = load_entities(vault) if ents is None else ents
    resolved = resolutions.resolved_ids(vault)
    items = [(eid, e) for eid, e in ents.items() if isinstance(e, dict)]
    items.sort(key=lambda kv: (-len(kv[1].get("appears_in") or []), (kv[1].get("name") or kv[0]).lower()))
    if limit is not None:
        items = items[:limit]
    return [entity_row(vault, eid, e, resolved) for eid, e in items]


# ── timeline ─────────────────────────────────────────────────────────────────────

def _ndjson(path: Path) -> list[dict]:
    out = []
    for line in read_text(path).splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def raw_timeline_events(vault: Path, docs: dict) -> list[dict]:
    """Canonical events plus the raw per-document events of committed documents that a failed
    dedup left behind — the set `timeline.cmd_rebuild_timeline` renders into `timeline.md`."""
    td = vault / ".watchdog" / "timeline"
    if not td.is_dir():
        return []
    events: list[dict] = []
    for cf in sorted(f for f in td.glob("*.ndjson") if "_" not in f.stem):
        events.extend(_ndjson(cf))

    queue_dir, tmp_dir = vault / ".watchdog" / "queue", vault / ".watchdog" / "tmp"

    def re_extracted(sha: str) -> bool:
        try:
            return ((tmp_dir / f"result_{sha}.json").stat().st_mtime
                    >= (queue_dir / f"{sha}.json").stat().st_mtime)
        except OSError:
            return False

    committed = {sha for sha in docs if not re_extracted(sha)}
    seen = {(e.get("date"), e.get("event"), e.get("source_sha256")) for e in events}
    for rf in sorted(td.glob("*_*.ndjson")):
        for ev in _ndjson(rf):
            if ev.get("source_sha256") not in committed:
                continue
            key = (ev.get("date"), ev.get("event"), ev.get("source_sha256"))
            if key not in seen:
                seen.add(key)
                events.append(ev)
    return events


def timeline_event(ev: dict, docs: dict, ents: dict, index=None) -> dict:
    """The app's `TimelineEvent` for one canonical NDJSON record. With a `FactIndex`, `disputed`
    says whether the reporter disputes the fact behind it (D285)."""
    from watchdog.pipeline.timeline import event_disputed
    sha = ev.get("source_sha256") or None
    rec = docs.get(sha) if sha else None
    date = (ev.get("date") or "").strip()
    page = ev.get("page")
    return {
        "date": date,
        "precision": precision(date),
        "text": ev.get("event") or "",
        "entities": [entity_ref(ents, eid) for eid in (ev.get("entity_ids") or []) if eid],
        "sha": sha,
        "filename": (rec or {}).get("filename"),
        "page": page if isinstance(page, int) else None,
        "note": doc_note_stem(rec) if rec else None,
        "disputed": event_disputed(ev, index),
    }


def timeline_events(vault: Path, docs: dict | None = None, ents: dict | None = None) -> list[dict]:
    """Every global-timeline event, earliest first."""
    docs = load_documents(vault) if docs is None else docs
    ents = load_entities(vault) if ents is None else ents
    raw = raw_timeline_events(vault, docs)
    raw.sort(key=lambda e: date_sort_key(e.get("date")))
    from watchdog.pipeline.entity_facts import FactIndex
    index = FactIndex(vault, entities=ents, documents=docs)
    return [timeline_event(e, docs, ents, index) for e in raw]
