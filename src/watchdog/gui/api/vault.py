"""`vault.*` — reading an investigation: the registries, notes, timeline, pipeline state and the
journalist-owned files. Everything is read from the vault's own files, in-process.

Writes are limited to three, each deliberately narrow: `vault.saveNotes` (only the body of a
note's `## Notes` section — the one part the pipeline never writes), and `vault.writeFile` (only
`context.md` and `watchlist.md`). Both go through a temp file and `os.replace`.
"""

from __future__ import annotations

import datetime
import hashlib
import re
from pathlib import Path

from watchdog.gui import vaultio
from watchdog.gui.rpc import RpcError, method
from watchdog.gui.vaultio import require_vault, resolve_in_vault
from watchdog.vault_paths import SET_ASIDE_NAMES, context_dir, incoming_dir, incoming_failed_dir, incoming_skipped_dir
from watchdog.vault_paths import preprocessing_lock, processing_lock, processing_log

_BRIEFING_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})-(\d{2})-(\d{2})(?:-\d+)?$")
_DATE_IN_NAME = re.compile(r"(\d{4}-\d{2}-\d{2})")
_NOTES_PLACEHOLDER = {
    "documents": "<!-- Reserved for journalist annotations — never overwritten by ingestion. -->",
    "entities": "<!-- Journalist annotations — never overwritten by ingestion. -->",
}
_READABLE_FILES = {"context.md", "watchlist.md", "requests.md", "hot.md", "log.md", "timeline.md",
                   "index.md"}
_READABLE_DIRS = ("briefings/", "queries/", "wiki/")
_WRITABLE_FILES = {"context.md", "watchlist.md"}
_NOTE_KINDS = (("entities/", "entity"), ("documents/", "document"), ("briefings/", "briefing"),
               ("queries/", "query"), ("wiki/", "wiki"))


def _iso_mtime(path: Path) -> str | None:
    try:
        return datetime.datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
    except OSError:
        return None


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


# ── summary ──────────────────────────────────────────────────────────────────────

def _briefing_date(name: str, path: Path) -> str | None:
    m = _BRIEFING_NAME.match(name)
    if m:
        return f"{m.group(1)}T{m.group(2)}:{m.group(3)}:00"
    m = _DATE_IN_NAME.search(name)
    if m:
        return m.group(1)
    return _iso_mtime(path)


@method("vault.summary")
def summary(vault: str) -> dict:
    from watchdog.cmd import home

    v = require_vault(vault)
    s = home.summary(v)
    docs, ents = vaultio.load_documents(v), vaultio.load_entities(v)
    latest = s["briefing"]
    briefing = None
    if latest is not None:
        briefing = {"path": f"briefings/{latest.name}", "name": latest.stem,
                    "date": _briefing_date(latest.stem, latest)}
    return {
        "name": vaultio.vault_name(v),
        "path": str(v),
        "briefing": briefing,
        "headline": s["headline"],
        "contradictions": s["contradictions"],
        "leads": s["leads"],
        "near_duplicates": s["near_duplicates"],
        "alerts": s["alerts"],
        "incoming": s["incoming"],
        "awaiting_dig": s["awaiting_dig"],
        "awaiting_bark": s["awaiting_bark"],
        "pending_finalize": bool(s["pending_finalize"]),
        "failed": s["failed"],
        "research_urls": s["research_urls"],
        "context_unseeded": bool(s["context_unseeded"]),
        "has_work": bool(home.has_work(s)),
        "totals": {
            "documents": len(docs),
            "entities": len(ents),
            "pages": sum(d.get("page_count") or 0 for d in docs.values()
                         if isinstance(d, dict) and isinstance(d.get("page_count"), int)),
            "events": len(vaultio.raw_timeline_events(v, docs)),
        },
        "recent_documents": vaultio.sorted_document_rows(v, docs, limit=8),
        "top_entities": vaultio.sorted_entity_rows(v, ents, limit=8),
    }


# ── documents ────────────────────────────────────────────────────────────────────

@method("vault.documents")
def documents(vault: str) -> list[dict]:
    return vaultio.sorted_document_rows(require_vault(vault))


def _strip_note_markup(text: str) -> str:
    return re.sub(r"^\s*\*\(|\)\*\s*$", "", text or "").strip()


def _facts_from_extraction(vault: Path, sha: str, ents: dict) -> list[dict] | None:
    from watchdog.pipeline.write_vault import _figure_verification_note, _quote_verification_note

    doc = vaultio.extracted(vault, sha).get("document")
    if not isinstance(doc, dict) or not isinstance(doc.get("key_facts"), list):
        return None
    out = []
    for f in doc["key_facts"]:
        if not isinstance(f, dict) or not (f.get("fact") or "").strip():
            continue
        figure = _strip_note_markup(_figure_verification_note(f))
        quote_note = _strip_note_markup(_quote_verification_note(f))
        out.append({
            "fact": f["fact"],
            "page": f.get("page") if isinstance(f.get("page"), int) else None,
            "basis": "inferred" if f.get("basis") == "inferred" else "stated",
            "date": f.get("date") or None,
            "quote": (f.get("quote") or "").strip() or None,
            "entities": [vaultio.entity_ref(ents, e) for e in (f.get("entities") or []) if e],
            "figure_note": figure or None,
            "quote_note": quote_note or None,
            "added_by": f.get("added_by") or None,
        })
    return out


def _facts_from_note(sections: dict) -> list[dict]:
    """Key facts read back from a document note's `## Key facts` bullets — for a vault that
    predates the staged extraction artifacts."""
    out = []
    for line in (sections.get("key facts") or "").splitlines():
        if not line.startswith("- "):
            if line.startswith(">") and out and not out[-1]["quote"]:
                out[-1]["quote"] = line.lstrip("> ").strip() or None
            continue
        text = line[2:].strip()
        page_m = re.search(r"\[\[[^\]]*#page=(\d+)[^\]]*\]\]|p\. (\d+)", text)
        page = int(page_m.group(1) or page_m.group(2)) if page_m else None
        inferred = "*(inferred)*" in text
        text = re.sub(r"\s*\(\[\[[^\]]*\|p\. \d+\]\]\)", "", text)
        text = re.sub(r"\s*\*\(.*?\)\*", "", text).strip()
        out.append({"fact": text, "page": page, "basis": "inferred" if inferred else "stated",
                    "date": None, "quote": None, "entities": [], "figure_note": None,
                    "quote_note": None, "added_by": None})
    return out


def _doc_entities(sha: str, rec: dict, ents: dict) -> list[dict]:
    out = []
    for eid in rec.get("entities_extracted") or []:
        if not eid:
            continue
        ref = vaultio.entity_ref(ents, eid)
        roles = []
        for r in (ents.get(eid) or {}).get("roles") or []:
            if r.get("source_sha256") == sha and not r.get("is_reverse") and r.get("relationship"):
                roles.append(f"{r['relationship']} {r.get('target_name') or r.get('target_id') or ''}".strip())
        out.append({**ref, "role": "; ".join(roles[:3]) or None})
    return out


def _duplicates(sha: str, rec: dict, docs: dict) -> list[dict]:
    """Documents this one nearly duplicates, in either direction. A document's
    `near_duplicate_of` is a link to the earlier document's note (or just a filename)."""
    mine = vaultio.doc_note_stem(rec)
    found: dict[str, dict] = {}

    def add(other_sha: str, other: dict) -> None:
        found.setdefault(other_sha, {"sha": other_sha, "filename": other.get("filename") or other_sha[:12],
                                     "note": vaultio.doc_note_stem(other)})

    link = rec.get("near_duplicate_of")
    target = vaultio.wikilink_target(link) if link else None
    for other_sha, other in docs.items():
        if other_sha == sha or not isinstance(other, dict):
            continue
        if link:
            if target and vaultio.doc_note_stem(other) == target:
                add(other_sha, other)
            elif not target and other.get("filename") == link:
                add(other_sha, other)
        other_target = vaultio.wikilink_target(other.get("near_duplicate_of"))
        if mine and other_target == mine:
            add(other_sha, other)
    return list(found.values())


def _sidecar(value) -> dict | None:
    if not value:
        return None
    if isinstance(value, dict):
        return vaultio.plain(value)
    import yaml
    try:
        data = yaml.safe_load(value)
    except yaml.YAMLError:
        data = None
    return vaultio.plain(data) if isinstance(data, dict) else {"text": str(value)}


@method("vault.document")
def document(vault: str, sha: str) -> dict:
    v = require_vault(vault)
    docs, ents = vaultio.load_documents(v), vaultio.load_entities(v)
    full = vaultio.resolve_sha(docs, sha)
    rec = docs[full]
    row = vaultio.document_row(v, full, rec)
    parsed = vaultio.parse_note_file(vaultio.note_file(v, rec.get("document_note")) or Path("/nonexistent"))
    extraction = vaultio.extracted(v, full)
    ex_doc = extraction.get("document") if isinstance(extraction.get("document"), dict) else {}

    facts = _facts_from_extraction(v, full, ents)
    if facts is None:
        facts = _facts_from_note(parsed["sections"]) if parsed else []

    pages: list[dict] = []
    if row["fulltext"]:
        pages = vaultio.split_pages(vaultio.read_text(resolve_in_vault(v, row["fulltext"])))

    queue = vaultio.read_json(v / ".watchdog" / "queue" / f"{full}.json", {})
    metadata = queue.get("metadata") if isinstance(queue, dict) else None
    file_metadata = rec.get("file_metadata") or ex_doc.get("file_metadata") or {}

    return {
        **row,
        "frontmatter": parsed["frontmatter"] if parsed else {},
        "body": parsed["body"] if parsed else "",
        "facts": facts,
        "entities": _doc_entities(full, rec, ents),
        "pages": pages,
        "file_metadata": vaultio.plain(file_metadata) if isinstance(file_metadata, dict) else {},
        "sidecar": _sidecar(ex_doc.get("sidecar") or (queue.get("sidecar") if isinstance(queue, dict) else None)),
        "metadata": vaultio.plain(metadata) if isinstance(metadata, dict) else None,
        "extract_model": rec.get("extract_model") or None,
        "extract_effort": rec.get("extract_effort") or None,
        "record_skill_hash": rec.get("record_skill_hash") or None,
        "duplicates": _duplicates(full, rec, docs),
    }


# ── entities ─────────────────────────────────────────────────────────────────────

@method("vault.entities")
def entities(vault: str) -> list[dict]:
    return vaultio.sorted_entity_rows(require_vault(vault))


def _contradictions(ent: dict, note_section: str | None, resolved: frozenset[str]) -> list[dict]:
    from watchdog.pipeline import resolutions
    from watchdog.pipeline.leads import _callout_summary

    callouts = [c for c in (ent.get("contradictions") or []) if isinstance(c, str) and c.strip()]
    callouts += resolutions.split_callouts(note_section or "")
    out, seen = [], set()
    for c in callouts:
        rid = resolutions.contradiction_id(c)
        if rid in seen:
            continue
        seen.add(rid)
        out.append({"rid": rid, "summary": _callout_summary(c), "text": c.strip(),
                    "resolved": rid in resolved})
    return out


def _relationships(ent: dict, ents: dict) -> list[dict]:
    out = []
    for r in ent.get("roles") or []:
        if not isinstance(r, dict):
            continue
        tid = r.get("target_id")
        target = ents.get(tid) if tid else None
        sha = r.get("source_sha256")
        out.append({
            "role": r.get("relationship") or "",
            "target_id": tid,
            "target_name": (target or {}).get("name") or r.get("target_name") or None,
            "target_type": (vaultio.entity_type((target or {}).get("type") or r.get("target_type"))
                            if (target or r.get("target_type")) else None),
            "direction": "in" if r.get("is_reverse") else "out",
            "docs": [sha] if sha else [],
        })
    return out


def _entity_timeline(eid: str, ent: dict, docs: dict, ents: dict) -> list[dict]:
    me = vaultio.entity_ref(ents, eid)
    events = []
    for ev in ent.get("timeline_events") or []:
        if not isinstance(ev, dict) or not (ev.get("event") or "").strip():
            continue
        events.append(vaultio.timeline_event(
            {"date": ev.get("date"), "event": ev["event"], "page": ev.get("page"),
             "source_sha256": ev.get("source_sha256"), "entity_ids": [eid]}, docs, ents))
        events[-1]["entities"] = [me]
    events.sort(key=lambda e: vaultio.date_sort_key(e["date"]))
    return events


@method("vault.entity")
def entity(vault: str, id: str) -> dict:
    from watchdog.pipeline import resolutions

    v = require_vault(vault)
    docs, ents = vaultio.load_documents(v), vaultio.load_entities(v)
    ent = ents.get(id)
    if not isinstance(ent, dict):
        raise RpcError("That entity isn't in this investigation.", code="not_found")
    resolved = resolutions.resolved_ids(v)
    row = vaultio.entity_row(v, id, ent, resolved)
    parsed = vaultio.parse_note_file(vaultio.note_file(v, ent.get("note_path")) or Path("/nonexistent"))
    sections = parsed["sections"] if parsed else {}
    appears = [s for s in (ent.get("appears_in") or []) if s in docs]
    doc_rows = vaultio.sorted_document_rows(v, {s: docs[s] for s in appears})
    return {
        **row,
        "frontmatter": parsed["frontmatter"] if parsed else {},
        "body": parsed["body"] if parsed else "",
        "sections": {k: (sections.get(k) or None)
                     for k in ("summary", "analysis", "contradictions", "timeline", "relationships",
                               "notes")},
        "documents": doc_rows,
        "relationships": _relationships(ent, ents),
        "contradictions": _contradictions(ent, sections.get("contradictions"), resolved),
        "timeline": _entity_timeline(id, ent, docs, ents),
    }


# ── graph and timeline ───────────────────────────────────────────────────────────

@method("vault.graph")
def graph(vault: str) -> dict:
    from watchdog.cmd.export import _forward_edges

    v = require_vault(vault)
    ents = {k: e for k, e in vaultio.load_entities(v).items() if isinstance(e, dict)}
    nodes = [{"id": eid, "name": e.get("name") or eid, "type": vaultio.entity_type(e.get("type")),
              "doc_count": len(e.get("appears_in") or [])} for eid, e in ents.items()]
    nodes.sort(key=lambda n: (-n["doc_count"], n["name"].lower()))
    edges, _dangling = _forward_edges(ents)
    merged: dict[tuple, dict] = {}
    for e in edges:
        key = (e["start"], e["end"], e["type"])
        item = merged.setdefault(key, {"source": e["start"], "target": e["end"], "role": e["type"],
                                       "docs": []})
        sha = e.get("source_sha256")
        if sha and sha not in item["docs"]:
            item["docs"].append(sha)
    return {"nodes": nodes, "edges": list(merged.values())}


@method("vault.timeline")
def timeline(vault: str) -> dict:
    return {"events": vaultio.timeline_events(require_vault(vault))}


# ── notes ────────────────────────────────────────────────────────────────────────

def _note_kind(rel: str) -> str:
    for prefix, kind in _NOTE_KINDS:
        if rel.startswith(prefix):
            return kind
    return "other"


def _note_candidates(vault: Path, path: str) -> tuple[str, Path | None]:
    """`(normalised vault-relative path with .md, existing file or None)` for a note path that
    may omit the extension. Only markdown outside hidden folders can be a note."""
    target = resolve_in_vault(vault, path)
    rel = vaultio.rel_posix(vault, target)
    if any(part.startswith(".") for part in rel.split("/")):
        raise RpcError("That isn't a note.", code="bad_path")
    if rel.lower().endswith(".md"):
        return rel, target if target.is_file() else None
    with_md = resolve_in_vault(vault, rel + ".md")
    if with_md.is_file():
        return vaultio.rel_posix(vault, with_md), with_md
    if target.is_file():
        raise RpcError("Only markdown notes can be opened here.", code="bad_path")
    return vaultio.rel_posix(vault, with_md), None


@method("vault.note")
def note(vault: str, path: str) -> dict:
    v = require_vault(vault)
    rel, file = _note_candidates(v, path)
    if file is None:
        return {"path": rel, "exists": False, "frontmatter": {}, "body": "", "title": None,
                "kind": _note_kind(rel)}
    fm, body = vaultio.split_frontmatter(vaultio.read_text(file))
    return {"path": rel, "exists": True, "frontmatter": fm, "body": body,
            "title": vaultio.note_title(fm, body), "kind": _note_kind(rel)}


def replace_notes_body(text: str, new_body: str, placeholder: str) -> str:
    """`text` with the body of its `## Notes` section replaced (the section runs to the end of
    the file, as the pipeline preserves it). With no such section, one is appended. An empty
    `new_body` keeps the placeholder comment that was already there, else `placeholder`."""
    lines = text.split("\n")
    idx, in_fence = None, False
    for i, line in enumerate(lines):
        if vaultio._FENCE.match(line):
            in_fence = not in_fence
        if not in_fence and re.match(r"^##[ \t]+Notes[ \t]*$", line):
            idx = i
            break
    existing = "\n".join(lines[idx + 1:]) if idx is not None else ""
    body = new_body.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if not body.strip():
        comment = re.search(r"<!--.*?-->", existing, flags=re.DOTALL)
        body = comment.group(0) if comment else placeholder
    head = "\n".join(lines[:idx]).rstrip("\n") if idx is not None else text.rstrip("\n")
    return f"{head}\n\n## Notes\n\n{body}\n"


@method("vault.saveNotes")
def save_notes(vault: str, path: str, text: str) -> dict:
    v = require_vault(vault)
    if not isinstance(text, str):
        raise RpcError("The notes must be text.", code="bad_params")
    rel, file = _note_candidates(v, path)
    top = rel.split("/", 1)[0]
    if top not in ("entities", "documents") or "/" not in rel:
        raise RpcError("Notes can only be saved on entity and document notes.", code="forbidden")
    if file is None:
        raise RpcError("That note doesn't exist.", code="not_found")
    old = vaultio.read_text(file)
    vaultio.write_text_atomic(file, replace_notes_body(old, text, _NOTES_PLACEHOLDER[top]))
    return {"ok": True}


# ── link resolution ──────────────────────────────────────────────────────────────

def _doc_by_note(docs: dict, stem: str) -> tuple[str, dict] | None:
    for sha, rec in docs.items():
        if isinstance(rec, dict) and vaultio.doc_note_stem(rec) == stem:
            return sha, rec
    return None


def _doc_by_morgue(docs: dict, rel: str) -> str | None:
    base = Path(rel).with_suffix("")
    for sha, rec in docs.items():
        morgue = rec.get("morgue_path") if isinstance(rec, dict) else None
        if isinstance(morgue, str) and (morgue == rel or Path(morgue).with_suffix("") == base):
            return sha
    return None


@method("vault.resolveLink")
def resolve_link(vault: str, target: str) -> dict:
    """What a wikilink target points at: `documents/x`, `entities/person/y`,
    `morgue/…/f.pdf#page=3`, or a bare name Obsidian would resolve to a note."""
    v = require_vault(vault)
    missing = {"path": None, "kind": "missing", "sha": None, "page": None}
    if not isinstance(target, str) or not target.strip():
        return missing
    text = target.strip().split("|", 1)[0]
    base, _, frag = text.partition("#")
    page = None
    m = re.match(r"page=(\d+)", frag)
    if m:
        page = int(m.group(1))
    base = base.strip().replace("\\", "/")
    docs, ents = vaultio.load_documents(v), vaultio.load_entities(v)

    def found(rel: str, kind: str, sha: str | None = None) -> dict:
        return {"path": rel, "kind": kind, "sha": sha, "page": page}

    if base:
        try:
            direct = resolve_in_vault(v, base)
        except RpcError:
            return missing
        rel = vaultio.rel_posix(v, direct)
        top = rel.split("/", 1)[0]
        if any(part.startswith(".") for part in rel.split("/")):
            return missing
        if top == "morgue" and direct.is_file():
            kind = "fulltext" if rel.lower().endswith(".md") else "original"
            return found(rel, kind, _doc_by_morgue(docs, rel))
        md_rel = rel if rel.lower().endswith(".md") else rel + ".md"
        md_file = resolve_in_vault(v, md_rel)
        if md_file.is_file():
            stem = md_rel[:-3]
            kind = {"documents": "document", "entities": "entity", "briefings": "briefing",
                    "queries": "query", "wiki": "wiki"}.get(top, "note")
            sha = None
            if kind == "document":
                hit = _doc_by_note(docs, stem)
                sha = hit[0] if hit else None
            return found(md_rel, kind, sha)
        if direct.is_file():
            return found(rel, "original", _doc_by_morgue(docs, rel))
        if "/" not in base:
            return _resolve_bare_name(v, base, docs, ents, found) or missing
    return missing


def _resolve_bare_name(vault: Path, name: str, docs: dict, ents: dict, found) -> dict | None:
    """Obsidian's shortest-path linking: a bare name matches an entity (id, name or alias), a
    document (note slug, title or filename) or a top-level note."""
    low = name.lower()
    for eid, e in ents.items():
        if not isinstance(e, dict):
            continue
        names = [eid, e.get("name") or "", *(e.get("aliases") or [])]
        if any(isinstance(n, str) and n.lower() == low for n in names):
            note = e.get("note_path")
            if note:
                return found(note if note.endswith(".md") else note + ".md", "entity")
    for sha, rec in docs.items():
        if not isinstance(rec, dict):
            continue
        stem = vaultio.doc_note_stem(rec) or ""
        keys = [stem.rsplit("/", 1)[-1], rec.get("title") or "", rec.get("filename") or ""]
        if any(isinstance(k, str) and k.lower() == low for k in keys):
            if stem:
                return found(stem + ".md", "document", sha)
    top = resolve_in_vault(vault, name + ".md" if not name.lower().endswith(".md") else name)
    if top.is_file():
        return found(vaultio.rel_posix(vault, top), "note")
    return None


# ── pipeline state ───────────────────────────────────────────────────────────────

def _files(directory: Path) -> list[Path]:
    """Visible, non-sidecar files directly under `directory` (sidecars are `<name>.yml`)."""
    if not directory.is_dir():
        return []
    out = []
    for f in sorted(directory.iterdir(), key=lambda p: p.name.lower()):
        if f.is_file() and not f.name.startswith(".") and not f.name.endswith(".yml"):
            out.append(f)
    return out


def _incoming_files(incoming: Path) -> list[Path]:
    """Everything chew would pick up: files anywhere under `incoming/` except in its `failed`
    and `skipped` folders (the same rule `cmd.base._count_incoming` counts by)."""
    import os
    out: list[Path] = []
    if not incoming.is_dir():
        return out
    for root, dirs, files in os.walk(incoming):
        top = Path(root) == incoming
        dirs[:] = sorted(d for d in dirs
                         if not (top and d in SET_ASIDE_NAMES) and not d.startswith("."))
        for name in sorted(files):
            if name.startswith(".") or name.endswith(".yml"):
                continue
            out.append(Path(root) / name)
    return out


def _sha256_of(path: Path) -> str | None:
    h = hashlib.sha256()
    try:
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _failure_reasons(vault: Path, names: list[str]) -> dict[str, str]:
    """The latest `FAILED <name>: <reason>` line in `ingest.log` for each of `names`."""
    reasons: dict[str, str] = {}
    log = vaultio.read_text(processing_log(vault))
    for line in reversed(log.splitlines()):
        _, sep, rest = line.partition("] FAILED ")
        if not sep:
            continue
        for name in names:
            if name not in reasons and rest.startswith(f"{name}: "):
                reasons[name] = rest[len(name) + 2:].strip()
    return reasons


@method("vault.pipeline")
def pipeline(vault: str) -> dict:
    from watchdog.pipeline import batch_extract, orchestrate, research
    from watchdog.pipeline.ingest_setup import scan_queue

    v = require_vault(vault)
    docs = vaultio.load_documents(v)
    inc_dir = incoming_dir(v)

    incoming = [{"name": f.name, "path": vaultio.rel_posix(v, f), "size": _size(f),
                 "modified": _iso_mtime(f), "sidecar": f.with_name(f.name + ".yml").exists()}
                for f in _incoming_files(inc_dir)]

    chew_failed = [{"name": f.name, "path": vaultio.rel_posix(v, f), "size": _size(f)}
                   for f in _files(incoming_failed_dir(v))]

    queued_shas = {p.stem for p in (v / ".watchdog" / "queue").glob("*.json")} \
        if (v / ".watchdog" / "queue").is_dir() else set()
    skipped = []
    for f in _files(incoming_skipped_dir(v)):
        sha = _sha256_of(f) if _size(f) <= 512 * 1024 * 1024 else None
        reason = ("already ingested" if sha in docs else "already queued" if sha in queued_shas else None)
        skipped.append({"name": f.name, "path": vaultio.rel_posix(v, f), "size": _size(f),
                        "reason": reason})

    extracted_dir = v / ".watchdog" / "extracted"
    queued = [{"sha": q["sha256"], "filename": q["filename"],
               "page_count": q["page_count"] or None, "est_tokens": q["est_tokens"] or None,
               "staged": (extracted_dir / f"{q['sha256']}.json").exists()}
              for q in scan_queue(v)]

    failed_dir = v / ".watchdog" / "queue" / "_failed"
    failed = []
    if failed_dir.is_dir():
        for qf in sorted(failed_dir.glob("*.json")):
            data = vaultio.read_json(qf, {})
            failed.append({"sha": qf.stem,
                           "filename": (data.get("filename") if isinstance(data, dict) else None) or qf.stem[:12]})
        reasons = _failure_reasons(v, [f["filename"] for f in failed])
        for f in failed:
            f["reason"] = reasons.get(f["filename"])

    pending = (orchestrate.pending_finalization(v) if orchestrate.has_pending_finalization(v) else None)
    return {
        "incoming": incoming,
        "chew_failed": chew_failed,
        "skipped": skipped,
        "queued": queued,
        "failed": failed,
        "pending_finalization": pending,
        "locks": {"chew": preprocessing_lock(v).exists(),
                  "ingest": processing_lock(v).exists()},
        "research_urls": research.pending_count(v),
        "batch_pending": batch_extract.read_state(v),
    }


# ── briefings ────────────────────────────────────────────────────────────────────

def _briefing_kind(stem: str) -> str:
    for prefix in ("leads", "alerts", "research"):
        if stem.startswith(prefix + "-"):
            return prefix
    return "briefing"


@method("vault.briefings")
def briefings(vault: str) -> list[dict]:
    v = require_vault(vault)
    d = v / "briefings"
    out = []
    if d.is_dir():
        for f in d.glob("*.md"):
            if f.name.startswith("."):
                continue
            fm, body = vaultio.split_frontmatter(vaultio.read_text(f))
            date = _briefing_date(f.stem, f)
            out.append({"path": f"briefings/{f.name}", "name": f.stem, "kind": _briefing_kind(f.stem),
                        "date": date, "title": vaultio.note_title(fm, body) or f.stem})
    out.sort(key=lambda b: (b["date"] or "", b["name"]), reverse=True)
    return out


@method("vault.notes")
def notes(vault: str) -> list[dict]:
    """Pages Claude sessions write: saved answers in `queries/` and thread pages in `wiki/`,
    most recently modified first."""
    v = require_vault(vault)
    out = []
    for folder, kind in (("queries", "query"), ("wiki", "wiki")):
        d = v / folder
        if not d.is_dir():
            continue
        for f in d.rglob("*.md"):
            if any(part.startswith(".") for part in f.relative_to(d).parts):
                continue
            fm, body = vaultio.split_frontmatter(vaultio.read_text(f))
            modified = datetime.datetime.fromtimestamp(f.stat().st_mtime, datetime.timezone.utc)
            out.append({"path": f.relative_to(v).as_posix(), "kind": kind,
                        "title": vaultio.note_title(fm, body) or f.stem,
                        "modified": modified.isoformat(timespec="seconds")})
    out.sort(key=lambda n: n["modified"], reverse=True)
    return out


# ── journalist-owned files ───────────────────────────────────────────────────────

def _normalise_rel(path: str) -> str:
    if not isinstance(path, str):
        raise RpcError("That path isn't valid.", code="bad_path")
    return "/".join(p for p in path.strip().replace("\\", "/").split("/") if p not in ("", "."))


@method("vault.readFile")
def read_file(vault: str, path: str) -> dict:
    v = require_vault(vault)
    rel = _normalise_rel(path)
    if rel not in _READABLE_FILES and not rel.startswith(_READABLE_DIRS):
        raise RpcError("That file can't be read from here.", code="forbidden")
    target = resolve_in_vault(v, rel)
    if not target.is_file():
        return {"text": "", "exists": False}
    return {"text": vaultio.read_text(target), "exists": True}


@method("vault.writeFile")
def write_file(vault: str, path: str, text: str) -> dict:
    v = require_vault(vault)
    rel = _normalise_rel(path)
    if rel not in _WRITABLE_FILES:
        raise RpcError("Only context.md and watchlist.md can be written from here.", code="forbidden")
    if not isinstance(text, str):
        raise RpcError("The file contents must be text.", code="bad_params")
    vaultio.write_text_atomic(resolve_in_vault(v, rel), text)
    return {"ok": True}


# ── requests and context ─────────────────────────────────────────────────────────

@method("vault.requests")
def requests_(vault: str) -> dict:
    from watchdog.pipeline import requests as _requests
    from watchdog.pipeline import resolutions

    v = require_vault(vault)
    open_ = [{
        "rid": r["rid"],
        "type": r.get("type") or None,
        "what": r.get("what") or "",
        "why": r.get("why_it_matters") or None,
        "likely_source": r.get("likely_source") or None,
        "cited_in": [{"sha": s.get("sha256"), "filename": s.get("filename"),
                      "note": s.get("document_note")}
                     for s in (r.get("sources") or []) if isinstance(s, dict)],
        "added": r.get("added") or None,
    } for r in _requests.open_requests(v)]
    resolved = resolutions.resolved_ids(v)
    resolved_count = sum(1 for rid in _requests.load(v).get("requests", {}) if rid in resolved)
    return {"open": open_, "resolved_count": resolved_count}


@method("vault.contextFiles")
def context_files(vault: str) -> list[dict]:
    v = require_vault(vault)
    d = context_dir(v)
    out = []
    if d.is_dir():
        for f in sorted(d.rglob("*")):
            rel = f.relative_to(d)
            if not f.is_file() or any(p.startswith(".") for p in rel.parts):
                continue
            out.append({"name": rel.as_posix(), "size": _size(f), "modified": _iso_mtime(f)})
    out.sort(key=lambda x: x["name"].lower())
    return out


@method("vault.migrate")
def migrate(vault: str) -> dict:
    """Bring an older vault's folder names up to date (`_INCOMING` -> `incoming`, `_CONTEXT` ->
    `context`; D266). Opening a vault already does this once per process; this is the explicit
    call. Returns `{changes: [str]}` — empty when the vault was already current."""
    from watchdog.vault_paths import ensure_current_layout
    return {"changes": ensure_current_layout(require_vault(vault))}
