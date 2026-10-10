"""`vault.*` — reading an investigation: the registries, notes, timeline, pipeline state and the
journalist-owned files. Everything is read from the vault's own files, in-process.

Writes are limited to two, each deliberately narrow: `vault.saveNotes` (only the body of the
`## Notes` section of an entity note, a document note or a saved page in `queries/` or `wiki/` —
the one part neither the pipeline nor a session's skills ever write), and `vault.writeFile` (only
`context.md` and `watchlist.md`). Both go through a temp file and `os.replace`, and each is
recorded as a version of the file's history (D286).
"""

from __future__ import annotations

import datetime
import hashlib
import re
from contextlib import contextmanager
from pathlib import Path

from watchdog.gui import vaultio
from watchdog.gui.rpc import RpcError, method
from watchdog.pipeline import text_positions
from watchdog.gui.vaultio import require_vault, resolve_in_vault
from watchdog.vault_paths import SET_ASIDE_NAMES, context_dir, incoming_dir, incoming_failed_dir, incoming_skipped_dir
from watchdog.gui.runlocks import run_locks
from watchdog.vault_paths import processing_log

_BRIEFING_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})-(\d{2})-(\d{2})(?:-\d+)?$")
_DATE_IN_NAME = re.compile(r"(\d{4}-\d{2}-\d{2})")
_NOTES_PLACEHOLDER = {
    "documents": "<!-- Reserved for journalist annotations — never overwritten by ingestion. -->",
    "entities": "<!-- Journalist annotations — never overwritten by ingestion. -->",
    # Saved pages, as the query and wiki skills write them.
    "queries": "<!-- Journalist annotations — never overwritten. -->",
    "wiki": "<!-- Journalist annotations — never overwritten. -->",
}
# Notes the pipeline's commit pass rewrites around (it keeps the section by reading the file
# under the registry lock), so a save takes that lock too.
_PIPELINE_NOTES = ("entities", "documents")
_NOTES_LOCK_WAIT = 3.0
_READABLE_FILES = {"context.md", "watchlist.md", "requests.md", "log.md", "timeline.md",
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
        "possible_same": s["possible_same"],
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
        "verification": _verification_summary(v, docs),
    }


def _verification_summary(v: Path, docs: dict) -> dict:
    from watchdog.pipeline import verification
    return verification.summary(v, verification.all_facts(v, docs))


# ── documents ────────────────────────────────────────────────────────────────────

@method("vault.documents")
def documents(vault: str) -> list[dict]:
    return vaultio.sorted_document_rows(require_vault(vault))


def _strip_note_markup(text: str) -> str:
    return re.sub(r"^\s*\*\(|\)\*\s*$", "", text or "").strip()


def fact_row(f: dict, fid: str, ents: dict, mark: dict | None) -> dict:
    """One fact as the app shows it (`Fact` in gui/API.md)."""
    from watchdog.pipeline.write_vault import _figure_verification_note, _quote_verification_note

    figure = _strip_note_markup(_figure_verification_note(f))
    quote_note = _strip_note_markup(_quote_verification_note(f))
    method = f.get("passage_method")
    passage_page = f.get("passage_page")
    return {
        "id": fid,
        "fact": f["fact"],
        "page": f.get("page") if isinstance(f.get("page"), int) else None,
        "basis": "inferred" if f.get("basis") == "inferred" else "stated",
        "date": f.get("date") or None,
        "quote": (f.get("quote") or "").strip() or None,
        "entities": [vaultio.entity_ref(ents, e) for e in (f.get("entities") or []) if e],
        "figure_note": figure or None,
        "quote_note": quote_note or None,
        "added_by": f.get("added_by") or None,
        "passage": (f.get("passage") or "").strip() or None,
        "passage_page": passage_page if isinstance(passage_page, int) else None,
        "passage_method": method if method in ("quote", "matched", "unlocated") else None,
        "passage_score": f.get("passage_score") if isinstance(f.get("passage_score"), (int, float)) else None,
        "mark": ({k: mark.get(k) for k in ("status", "note", "by", "at")}
                 if mark and mark.get("status") else None),
    }


def _facts(vault: Path, sha: str, rec: dict, ents: dict) -> list[dict]:
    """The document's facts with their ids, passages and verification marks — from the saved
    extraction, or the note's `## Key facts` bullets when there is none."""
    from watchdog.pipeline import verification

    facts = verification.document_facts(vault, sha, rec)
    marks = verification.marks(vault)
    return [fact_row(f, fid, ents, verification.attach(marks.get(fid), f))
            for fid, f in zip(verification.fact_ids(sha, facts), facts)]


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

    facts = _facts(v, full, rec, ents)

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
        "media": vaultio.media_info(rec),
        "extract_model": rec.get("extract_model") or None,
        "extract_effort": rec.get("extract_effort") or None,
        "record_skill_hash": rec.get("record_skill_hash") or None,
        "duplicates": _duplicates(full, rec, docs),
        "positions_pages": text_positions.available_pages(v, full),
    }


@method("vault.textPositions")
def text_positions_(vault: str, sha: str, pages: list | None = None) -> dict:
    """Where the OCR'd lines sit on the requested pages of a scanned document (D289), read from
    `.watchdog/text-positions/<sha>.json`; the app asks for a few pages at a time as they are
    shown or searched, never the whole document at once."""
    v = require_vault(vault)
    full = vaultio.resolve_sha(vaultio.load_documents(v), sha)
    if pages is not None and (not isinstance(pages, list)
                              or not all(isinstance(n, int) and not isinstance(n, bool) for n in pages)):
        raise RpcError("Pages must be a list of page numbers.", code="bad_params")
    return text_positions.read(v, full, pages)


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


def _wordings(row: dict) -> list[dict]:
    return [{"text": w["text"], "sources": [{"sha": x["sha"], "page": x["page"]} for x in w["sources"]]}
            for w in row["wordings"]]


def _relationships(v: Path, eid: str, ent: dict) -> list[dict]:
    """The entity's relationships, one row per counterpart, direction and meaning (D291): `role`
    is the canonical label, `wordings` the documents' own words with their sources, `group` the
    grouping's id when the wordings were grouped (so the reporter can split it)."""
    from watchdog.pipeline import relationships
    view = relationships.View(v, docs=ent.get("appears_in") or [])
    out = []
    for r in view.for_entity(eid):
        out.append({
            "role": r["label"],
            "target_id": r["other"],
            "target_name": r["other_name"] if r["profiled"] or r["other_name"] != r["other"] else None,
            "target_type": vaultio.entity_type(r["other_type"]) if r["other_type"] != "Unknown" else None,
            "direction": r["direction"],
            "docs": r["docs"],
            "group": r["group"],
            "wordings": _wordings(r),
            "sources": [{"sha": x["sha"], "page": x["page"], "wording": x["label"]} for x in r["sources"]],
        })
    return out


def _entity_timeline(v: Path, eid: str, ent: dict, docs: dict, ents: dict) -> list[dict]:
    from watchdog.pipeline import entity_facts
    index = entity_facts.FactIndex(v, ents, docs)
    me = vaultio.entity_ref(ents, eid)
    events = []
    for ev in ent.get("timeline_events") or []:
        if not isinstance(ev, dict) or not (ev.get("event") or "").strip():
            continue
        events.append(vaultio.timeline_event(
            {"date": ev.get("date"), "event": ev["event"], "page": ev.get("page"),
             "source_sha256": ev.get("source_sha256"), "entity_ids": [eid]}, docs, ents, index))
        events[-1]["entities"] = [me]
    events.sort(key=lambda e: vaultio.date_sort_key(e["date"]))
    return events


def _entity_facts(v: Path, eid: str, ents: dict, docs: dict) -> dict:
    """The entity's facts from the stored extractions, in date order, as the app shows them, plus
    its AI-written synthesis from the registry (D280)."""
    from watchdog.pipeline import entity_facts
    index = entity_facts.FactIndex(v, ents, docs)
    rows = []
    for f in entity_facts.chronological(index.facts_for(eid)):
        row = fact_row({**f, "entities": f.get("entities") or []}, f["id"], ents, f.get("mark"))
        row.update({"sha": f["sha"], "title": f.get("title"), "doc_date": f.get("doc_date") or None,
                    "note": f.get("note")})
        rows.append(row)
    synth = (ents.get(eid) or {}).get("synthesis")
    synthesis = None
    if isinstance(synth, dict) and (synth.get("summary") or "").strip():
        synthesis = {k: synth.get(k) for k in ("summary", "analysis", "by", "model", "made_at",
                                               "facts_total", "facts_shown", "stale")}
        from watchdog.pipeline.entity_notes import stale_notice
        synthesis["stale_notice"] = stale_notice(synth)
        # The summary as the note shows it (D283): citations rendered as links to the facts the
        # entity still has, dangling ones dropped and counted.
        from watchdog.pipeline import citations, entity_notes
        known = {d.get("document_note") for d in docs.values() if isinstance(d, dict)}
        by_id = {f["id"]: f for f in index.facts_for(eid)}
        resolver = citations.Resolver(v, index)
        stats = citations.new_stats()
        synthesis["summary_md"] = entity_notes._cited(synth["summary"].strip(), synth, known,
                                                      by_id, resolver, stats)
        synthesis["analysis_md"] = (entity_notes._cited(synth["analysis"].strip(), synth, known,
                                                        by_id, resolver, stats)
                                    if (synth.get("analysis") or "").strip() else None)
        synthesis["citations"] = stats
    legacy = (ents.get(eid) or {}).get("legacy_claims")
    return {"facts": rows, "synthesis": synthesis,
            "legacy_claims": legacy if isinstance(legacy, str) and legacy.strip() else None}


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
    rels = _relationships(v, id, ent)
    return {
        **_entity_facts(v, id, ents, docs),
        **row,
        "frontmatter": parsed["frontmatter"] if parsed else {},
        "body": parsed["body"] if parsed else "",
        "sections": {k: (sections.get(k) or None)
                     for k in ("summary", "analysis", "contradictions", "timeline", "relationships",
                               "notes")}
        | {"summary": sections.get("summary") or sections.get("summary (ai-written)") or None},
        "documents": doc_rows,
        "relationships": rels,
        "role_count": len(rels),
        "contradictions": _contradictions(ent, sections.get("contradictions"), resolved),
        "timeline": _entity_timeline(v, id, ent, docs, ents),
    }


# ── graph and timeline ───────────────────────────────────────────────────────────

def _edge_labels(rows: list[dict]) -> list[str]:
    """Every distinct relationship on a pair, best documented first; none is ever left out."""
    labels: list[str] = []
    for r in rows:
        if r["label"].casefold() not in {x.casefold() for x in labels}:
            labels.append(r["label"])
    return labels


@method("vault.graph")
def graph(vault: str) -> dict:
    """One edge per pair of profiled entities, whatever the direction or wording (D291), with each
    relationship between them, its documents and pages, and the documents' own wordings."""
    from watchdog.pipeline import relationships

    v = require_vault(vault)
    docs = vaultio.load_documents(v)
    ents = {k: e for k, e in vaultio.load_entities(v).items() if isinstance(e, dict)}
    nodes = [{"id": eid, "name": e.get("name") or eid, "type": vaultio.entity_type(e.get("type")),
              "doc_count": len(e.get("appears_in") or [])} for eid, e in ents.items()]
    nodes.sort(key=lambda n: (-n["doc_count"], n["name"].lower()))
    view = relationships.View(v)
    edges, cited = [], set()
    for e in view.edges():
        rels = [{"from": r["from"], "to": r["to"], "label": r["label"], "group": r["group"],
                 "docs": r["docs"], "basis": r["basis"], "date_ranges": r["date_ranges"],
                 "wordings": _wordings(r)} for r in e["relationships"]]
        cited.update(e["docs"])
        labels = _edge_labels(e["relationships"])
        edges.append({"source": e["a"], "target": e["b"], "role": " · ".join(labels),
                      "labels": labels, "docs": e["docs"], "directed": e["directed"],
                      "relationships": rels})
    documents = {sha: {"title": (docs.get(sha) or {}).get("title") or (docs.get(sha) or {}).get("filename") or sha[:12],
                       "note": (docs.get(sha) or {}).get("document_note") or None}
                 for sha in sorted(cited) if sha in docs}
    return {"nodes": nodes, "edges": edges, "documents": documents}


@method("vault.relationshipSplit")
def relationship_split(vault: str, group: str) -> dict:
    """Show a grouping's wordings apart again (D291), through `relationships.split`, the library
    function that records it (an app-only operation, I10). Later runs never regroup them."""
    from watchdog.pipeline import relationships

    v = require_vault(vault)
    if not isinstance(group, str) or not group.startswith("rel:"):
        raise RpcError("That is not a relationship grouping.", code="bad_params")
    try:
        entry = relationships.split(v, group)
    except LookupError:
        raise RpcError("That grouping is no longer in effect.", code="not_found")
    except ValueError as e:
        raise RpcError(str(e), code="log_too_new")
    return {"group": entry["id"], "status": entry["status"], "by": entry.get("split_by"),
            "at": entry.get("split_at")}


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


@contextmanager
def notes_write_lock(vault: Path, rel: str):
    """Hold what a write to `rel`'s Notes section must hold. An entity or document note is
    rewritten by the commit pass, which keeps the Notes section it reads under the registry
    lock; a save waits a few seconds for that lock, then reports `busy` (the app retries) rather
    than racing the pass. Saved pages are saved by `page_notes.app_save` instead (D296)."""
    if rel.split("/", 1)[0] not in _PIPELINE_NOTES:
        yield
        return
    import time
    from watchdog.pipeline.write_vault import _try_registry_lock
    reg = vault / ".watchdog" / "registry"
    if not reg.is_dir():
        yield
        return
    deadline = time.monotonic() + _NOTES_LOCK_WAIT
    while True:
        with _try_registry_lock(reg) as got:
            if got:
                yield
                return
        if time.monotonic() >= deadline:
            raise RpcError("Watchdog is writing this investigation's notes. Your notes will be "
                           "saved when it has finished.", code="busy")
        time.sleep(0.2)


@method("vault.saveNotes")
def save_notes(vault: str, path: str, text: str) -> dict:
    v = require_vault(vault)
    if not isinstance(text, str):
        raise RpcError("The notes must be text.", code="bad_params")
    rel, file = _note_candidates(v, path)
    top = rel.split("/", 1)[0]
    if top not in _NOTES_PLACEHOLDER or "/" not in rel:
        raise RpcError("Notes can only be saved on entity and document notes and on saved pages.",
                       code="forbidden")
    if file is None:
        raise RpcError("That note doesn't exist.", code="not_found")
    from watchdog.pipeline import history, page_notes
    if page_notes.is_page(rel):
        # A saved page: compare-and-swap against a session's concurrent write, under the lock
        # the vault's edit hooks take (D296).
        with history.recording(v, {"kind": "notes"}, [rel]):
            try:
                page_notes.app_save(v, rel, file,
                                    lambda old: replace_notes_body(old, text, _NOTES_PLACEHOLDER[top]))
            except FileNotFoundError:
                raise RpcError("That note doesn't exist.", code="not_found") from None
            except RuntimeError:
                raise RpcError("Claude is changing this page. Your notes will be saved when it "
                               "has finished.", code="busy") from None
        return {"ok": True}
    with notes_write_lock(v, rel), history.recording(v, {"kind": "notes"}, [rel]):
        old = vaultio.read_text(file)
        new = replace_notes_body(old, text, _NOTES_PLACEHOLDER[top])
        if new != old:
            vaultio.write_text_atomic(file, new)
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


@method("vault.citations")
def fact_citations(vault: str, links: list) -> dict:
    """What each fact citation names (D283). `links` are `<note>#^<block id>` strings, as written
    in a page's `[[<note>#^f-…|…]]` links; each maps to `{status: "found", fact, disputed}` or
    `{status: "not_found"}`. Read-only; at most 500 per call."""
    from watchdog.pipeline import citations
    v = require_vault(vault)
    resolver = citations.Resolver(v)
    out: dict = {}
    for key in (links if isinstance(links, list) else [])[:500]:
        if not isinstance(key, str) or key in out:
            continue
        target, sep, block = key.partition("#^")
        if sep and target.strip() and block.startswith("f-"):
            out[key] = citations.check_link(resolver, target.strip(), block.strip())
    return out


@method("vault.checkCitations")
def check_citations(vault: str) -> dict:
    """Every fact citation in `queries/`, `wiki/` and `briefings/`, resolved against the
    stored facts (what a session's `check_citations` tool reports). Changes nothing."""
    from watchdog.pipeline import citations
    return citations.check_vault(require_vault(vault))


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
        **run_locks(v),
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


@method("vault.sessionPrimer")
def session_primer(vault: str) -> dict:
    """The primer every Ask Claude session starts with (D285), built now from the vault's records
    exactly as a session's system prompt carries it (D299). Read-only, no model."""
    from watchdog.cmd import primer
    text = primer.build(require_vault(vault))
    return {"text": text, "chars": len(text), "budget": primer.BUDGET_CHARS}


@method("vault.currentState")
def current_state(vault: str) -> dict:
    """Briefings → Current state: the primer's picture of the investigation written for the
    reporter, without the instructions meant for Claude (`gui/current_state.py`). Read-only."""
    from watchdog.gui import current_state as cs
    return {"text": cs.build(require_vault(vault))}


@method("vault.writeFile")
def write_file(vault: str, path: str, text: str) -> dict:
    v = require_vault(vault)
    rel = _normalise_rel(path)
    if rel not in _WRITABLE_FILES:
        raise RpcError("Only context.md and watchlist.md can be written from here.", code="forbidden")
    if not isinstance(text, str):
        raise RpcError("The file contents must be text.", code="bad_params")
    from watchdog.pipeline import history
    with history.recording(v, {"kind": "edit", "file": rel}, [rel]):
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
