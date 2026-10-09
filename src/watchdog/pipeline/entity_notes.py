"""Entity notes are views, rebuilt from stored data every time they are written (D280).

An entity note is rendered entirely from data the vault already keeps:

* **Summary** — the registry entry's `synthesis` record: the model's summary and analysis, its
  citations rendered as links to the facts (D283). Which model wrote it, when, and from how many
  facts stays in the record, not in the note (D284).
* **Facts** — every fact tagged to the entity in the stored extractions (`entity_facts`), with its
  document, page link, flags, passage and the reporter's mark. Code renders it; no model writes it.
* **Contradictions** — the registry's contradiction ledger, minus the ones marked handled.
* **Relationships** — the registry's roles.
* **Notes** — the journalist's own section, carried over from the note on disk, never changed.

Because nothing in a note is the only copy of anything (except the journalist's Notes), any note
can be deleted and rebuilt with no model call (`rebuild`), and rebuilding gives the same file.

The note's frontmatter carries `note_format`; a reader ignores fields it does not know. A note
written before format 2 may hold model prose that exists nowhere else — the old `## Summary` and,
for entities that had one, the prose `## Analysis`. The first time such a note is rewritten, that
prose is moved into the registry's `synthesis` record, labelled as carried over, so it is kept
rather than lost (`carry_old_prose`).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from watchdog.pipeline import citations, entity_facts
from watchdog.pipeline.json_io import _read_json_or

# 3 (D283): document notes carry a block id on each fact line, so a citation can link to it, and
# summaries render their citations as links. A vault below 3 is rebuilt once at its next commit.
NOTE_FORMAT = 3
# Notes below this format hold model prose that exists nowhere else (`carry_old_prose`).
PROSE_IN_NOTE_BELOW = 2
SYNTHESIS_VERSION = 1

# A short entity note lists every fact in date order. Past FLAT_MAX facts the list is grouped by
# document, GROUP_FACTS per document (plus every fact the reporter has marked), and only the
# FULL_GROUPS most recent documents get a group; earlier documents get one line each with a count.
FLAT_MAX = 40
GROUP_FACTS = 5
FULL_GROUPS = 40

_MARKS = {"verified": "✓ verified", "disputed": "✗ disputed", "unverifiable": "? can't verify"}
_NOTES_DEFAULT = "\n## Notes\n\n<!-- Journalist annotations — never overwritten by ingestion. -->\n"
_NOTES_RE = re.compile(r"^## Notes[ \t]*$", re.MULTILINE)
SUMMARY_HEADING = "Summary"


# ── reading the note on disk ─────────────────────────────────────────────────────────────────

def notes_section(text: str | None) -> str:
    """The journalist's `## Notes` heading and everything after it, or the empty placeholder."""
    if not text:
        return _NOTES_DEFAULT
    m = _NOTES_RE.search(text)
    return "\n" + text[m.start():] if m else _NOTES_DEFAULT


def _frontmatter_format(text: str) -> int | None:
    m = re.match(r"---\n(.*?)\n---\n", text or "", re.DOTALL)
    if not m:
        return None
    f = re.search(r"^note_format:\s*(\d+)\s*$", m.group(1), re.MULTILINE)
    return int(f.group(1)) if f else None


def carry_old_prose(entry: dict, text: str | None, documents: dict | None = None,
                    has_facts=None) -> bool:
    """Move what only an older note holds into the registry entry, once. Returns True if it did.

    Only a note written before format 2 is read. Its `## Summary` is model prose (synthesis or a
    Claude session) and becomes `entry["synthesis"]`, labelled as carried over; a prose
    `## Analysis` beside it goes with it. An `## Analysis` made of the old writer's per-document
    claim blocks is superseded by the Facts section, except for blocks whose document has no
    stored extraction (`has_facts(sha)` false): those exist nowhere else and are kept, as
    written, in `entry["legacy_claims"]`."""
    from watchdog.pipeline.write_vault import _ANALYSIS_HEADER_RE, _extract_section
    if not text or (_frontmatter_format(text) or 1) >= PROSE_IN_NOTE_BELOW:
        return False
    changed = False
    summary = _extract_section(text, "Summary")
    analysis = _extract_section(text, "Analysis")
    blocks = list(_ANALYSIS_HEADER_RE.finditer(analysis)) if analysis else []
    if summary and "synthesis" not in entry:
        entry["synthesis"] = {
            "version": SYNTHESIS_VERSION, "summary": summary,
            "analysis": "" if blocks else analysis,
            "by": "carried", "model": None, "made_at": entry.get("date_last_updated"),
            "facts_total": None, "facts_shown": None, "fact_refs": {},
        }
        changed = True
    if blocks and "legacy_claims" not in entry:
        by_note = {d.get("document_note"): sha for sha, d in (documents or {}).items()
                   if isinstance(d, dict)}
        kept = []
        for i, m in enumerate(blocks):
            end = blocks[i + 1].start() if i + 1 < len(blocks) else len(analysis)
            sha = by_note.get(m.group(1))
            if sha is None or has_facts is None or not has_facts(sha):
                kept.append(analysis[m.start():end].strip())
        if kept:
            entry["legacy_claims"] = "\n\n".join(kept)
            changed = True
    return changed


# ── rendering ────────────────────────────────────────────────────────────────────────────────

block_id = citations.block_id


def mark_label(mark: dict | None) -> str:
    return _MARKS.get((mark or {}).get("status"), "not checked")


def _fact_line(f: dict, *, with_source: bool, with_passage: bool) -> str:
    from watchdog.pipeline.write_vault import (
        _defang, _figure_verification_note, _page_link, _quote_verification_note, _render_date,
    )
    head = f"**{_render_date(f['date'])}** — " if f.get("date") else ""
    inferred = "*(inferred)* " if f.get("basis") == "inferred" else ""
    page = _page_link(f.get("morgue") or "", f.get("page"))
    if with_source and f.get("note"):
        source = f" — [[{f['note']}|{_defang(f.get('title') or '')}]]" + (f", {page}" if page else "")
    else:
        source = f" — {page}" if page else ""
    line = (f"- {head}{inferred}{_defang(f.get('fact') or '')}{source}"
            f"{_figure_verification_note(f)} · {mark_label(f.get('mark'))} ^{block_id(f['id'])}")
    quote = _defang((f.get("quote") or "").strip())
    if quote:
        line += f"\n  > {quote}{_quote_verification_note(f)}"
    elif with_passage and f.get("passage_method") == "matched" and (f.get("passage") or "").strip():
        where = f", p. {f['passage_page']}" if f.get("passage_page") else ""
        line += f"\n  > *Matched passage{where}:* {_defang(f['passage'])}"
    return line


def facts_section(facts: list[dict]) -> str:
    """The `## Facts` body for an entity's facts (already resolved), or "" when there are none."""
    if not facts:
        return ""
    facts = entity_facts.chronological(facts)
    if len(facts) <= FLAT_MAX:
        return "\n".join(_fact_line(f, with_source=True, with_passage=True) for f in facts)

    from watchdog.pipeline.write_vault import _defang, _render_date
    groups: dict[str, list[dict]] = {}
    for f in facts:
        groups.setdefault(f["sha"], []).append(f)
    order = sorted(groups, key=lambda s: (entity_facts._date_key(groups[s][0].get("doc_date")),
                                          (groups[s][0].get("title") or "").casefold(), s))
    full, early = order[-FULL_GROUPS:], order[:-FULL_GROUPS] if len(order) > FULL_GROUPS else []

    def label(sha: str) -> str:
        first = groups[sha][0]
        date = f"{_render_date(first['doc_date'])} · " if first.get("doc_date") else ""
        return f"{date}{_defang(first.get('title') or '')}"

    def link(sha: str, text: str) -> str:
        note = groups[sha][0].get("note")
        return f"[[{note}|{text}]]" if note else text

    out, shown = [], 0
    if early:
        out.append("### Earlier documents\n")
        for sha in early:
            fs = groups[sha]
            out.append(f"- {link(sha, label(sha))} — {len(fs)} fact{'s' if len(fs) != 1 else ''}")
            for f in fs:
                if (f.get("mark") or {}).get("status") == "disputed":
                    out.append("  " + _fact_line(f, with_source=False, with_passage=False))
                    shown += 1
        out.append("")
    for sha in full:
        fs = sorted(groups[sha], key=lambda f: f.get("index", 0))
        keep = [f for i, f in enumerate(fs) if i < GROUP_FACTS or f.get("mark")]
        out.append(f"### {label(sha)}\n")
        out += [_fact_line(f, with_source=False, with_passage=False) for f in keep]
        shown += len(keep)
        if len(keep) < len(fs):
            more = len(fs) - len(keep)
            noun = "fact" if more == 1 else "facts"
            out.append(f"- {link(sha, f'{more} more {noun} in this document')}")
        out.append("")
    head = (f"*{shown} of {len(facts)} facts shown, grouped by document in date order. The "
            f"Watchdog app lists every fact about this entity with its source passage, and each "
            f"document's note lists all of that document's facts.*\n")
    return head + "\n" + "\n".join(out).rstrip()


_WIKILINK = re.compile(r"\[\[([^\]|#\n]+)(#[^\]|\n]*)?(\|[^\]\n]+)?\]\]")


def safe_prose(text: str, known: set[str] | None) -> str:
    """Model prose for a note: a wikilink survives only when it points at a note the registry
    knows (a document or entity note); every other `[[`/`]]` is broken, so model- or
    document-supplied text can never forge a link (D241)."""
    from watchdog.pipeline.write_vault import _defang_links
    known = known or set()
    kept: list[str] = []

    def keep(m: re.Match) -> str:
        if m.group(1).strip().removesuffix(".md") in known:
            kept.append(m.group(0))
            return f"\x00{len(kept) - 1}\x00"
        return m.group(0)
    out = _defang_links(_WIKILINK.sub(keep, (text or "").replace("\x00", "")))
    return re.sub(r"\x00(\d+)\x00", lambda m: kept[int(m.group(1))], out)


def _cited(text: str, synthesis: dict, known: set[str] | None, facts: dict[str, dict] | None,
           resolver: citations.Resolver | None, stats: dict) -> str:
    """Model prose with its citations made into links (D283): wikilinks it was given are kept only
    when they name a known note, links written as fact links are checked, and short refs
    (`[f:3a9c]`) are looked up in the synthesis's stored map and the entity's current facts."""
    out = safe_prose(text, known)
    if resolver is not None:
        out, _ = citations.annotate_prose(out, resolver, stats)
    out, _ = citations.render_short(out, synthesis.get("fact_refs") or {},
                                    lambda fid: (facts or {}).get(fid), stats)
    return out


# A summary written before the record it describes changed (a merge into it, or an undo of one)
# says so: a warning about its accuracy, not a byline (D285). Synthesis rewrites such a summary at
# the next post-processing run (`synthesis_bundle.build_bundle`), which drops the flag.
STALE_NOTICES = {
    "merge": ("This summary was written before another record was merged into this one; it will "
              "be rewritten the next time documents are added."),
    "undo": ("This summary was written before a merge was undone; it will be rewritten the next "
             "time documents are added."),
}


def stale_notice(synthesis: dict | None) -> str | None:
    """The warning for a summary written before its record changed, or None."""
    return STALE_NOTICES.get((synthesis or {}).get("stale")) if isinstance(synthesis, dict) else None


def summary_section(synthesis: dict | None, known: set[str] | None = None,
                    facts: dict[str, dict] | None = None,
                    resolver: citations.Resolver | None = None,
                    stats: dict | None = None) -> str:
    """The `## Summary` body, or "" when the entity has no synthesis. No line says who wrote it
    (D284): the model, date and counts stay in the registry's `synthesis` record. `facts` maps a
    D271 id to the entity's current fact, so a citation links only to a fact the entity still has;
    `stats` (when given) receives the citation counts."""
    if not isinstance(synthesis, dict) or not (synthesis.get("summary") or "").strip():
        return ""
    stats = stats if stats is not None else citations.new_stats()
    parts = [_cited(synthesis["summary"].strip(), synthesis, known, facts, resolver, stats)]
    if (synthesis.get("analysis") or "").strip():
        parts.append(_cited(synthesis["analysis"].strip(), synthesis, known, facts, resolver, stats))
    notice = stale_notice(synthesis)
    if notice:
        parts.append(f"> [!warning] Out of date\n> {notice}")
    return "\n\n".join(parts)


def render(entry: dict, facts: list[dict], documents: dict, contradictions: str, notes: str,
           resolver: citations.Resolver | None = None, stats: dict | None = None) -> str:
    """The whole note for one registry entry. `resolver` checks fact links in the summary and the
    contradictions (D283); `stats` receives the summary's citation counts."""
    from watchdog.pipeline.write_vault import _defang, _frontmatter, _role_line, _today
    appears = []
    for sha in entry.get("appears_in", []):
        doc = documents.get(sha, {})
        note, title = doc.get("document_note"), doc.get("title") or doc.get("filename", "")
        appears.append(f"[[{note}|{_defang(title)}]]" if note and title else sha[:16] + "…")
    fm = _frontmatter({
        "id": entry["id"], "name": entry["name"], "type": entry["type"],
        "aliases": entry.get("aliases", []), "appears_in": appears,
        "date_first_seen": entry.get("date_first_seen", _today()),
        "date_last_updated": entry.get("date_last_updated", _today()),
        "note_format": NOTE_FORMAT,
    })
    body = f"\n# {_defang(entry['name'])}\n"
    known = {d.get("document_note") for d in documents.values() if isinstance(d, dict)}
    summary = summary_section(entry.get("synthesis"), known, {f["id"]: f for f in facts},
                              resolver, stats)
    if summary:
        body += f"\n## {SUMMARY_HEADING}\n\n{summary}\n"
    listed = facts_section(facts)
    if listed:
        body += f"\n## Facts\n\n{listed}\n"
    if (entry.get("legacy_claims") or "").strip():
        body += ("\n## Earlier claims\n\n*Recorded by an earlier version of Watchdog from "
                 "documents whose extraction was not kept, so they cannot be rebuilt as facts. "
                 f"Kept as written.*\n\n{entry['legacy_claims'].strip()}\n")
    if contradictions:
        if resolver is not None:
            contradictions = citations.annotate_callouts(contradictions, resolver)
        body += f"\n## Contradictions\n\n{contradictions}\n"
    roles = entry.get("roles", [])
    if roles:
        body += "\n## Relationships\n\n" + "\n".join(_role_line(r, documents) for r in roles) + "\n"
    return fm + body + notes


# ── writing ──────────────────────────────────────────────────────────────────────────────────

def write_entities(vault: Path, ids, entities: dict, documents: dict, *,
                   index: entity_facts.FactIndex | None = None,
                   resolved: frozenset[str] | None = None,
                   notes: dict[str, str] | None = None) -> list[tuple[str, str, str]]:
    """Render and write the notes of `ids` from the registries the caller holds, mutating each
    entry only to fold note-only contradiction callouts into its ledger (as every writer always
    has) and to carry an older note's prose into `synthesis`. Returns (note_path, name, content)
    for the search indexes, which the caller updates once its registries are persisted. `notes`
    maps an id to a Notes section to use instead of the one on disk (a merge carrying the merged
    record's notes over)."""
    from watchdog.pipeline import resolutions
    from watchdog.pipeline.write_vault import _assert_in_vault, _extract_section
    vault = Path(vault)
    index = index or entity_facts.FactIndex(vault, entities, documents)
    resolver = citations.Resolver(vault, index)
    resolved = resolved if resolved is not None else resolutions.resolved_ids(vault)
    out = []
    for eid in sorted(set(ids)):
        entry = entities.get(eid)
        if not isinstance(entry, dict) or not entry.get("note_path"):
            continue
        path = _assert_in_vault(vault / f"{entry['note_path']}.md", vault, "entity note_path")
        old = path.read_text(encoding="utf-8") if path.exists() else None
        carry_old_prose(entry, old, documents, lambda sha: index.document(sha) is not None)
        callouts = resolutions.dedup_callouts(
            list(entry.get("contradictions") or [])
            + resolutions.split_callouts(_extract_section(old or "", "Contradictions")))
        entry["contradictions"] = callouts
        body = "\n\n".join(resolutions.filter_callouts(callouts, resolved))
        own_notes = (notes or {}).get(eid) or notes_section(old)
        stats = citations.new_stats()
        content = render(entry, index.facts_for(eid), documents, body, own_notes,
                         resolver=resolver, stats=stats)
        synthesis = entry.get("synthesis")
        if isinstance(synthesis, dict) and synthesis.get("citations") != stats:
            # What the rendered summary could link (D283): citations whose fact is gone or whose
            # short ref was never in the map are dropped from the text and counted here.
            synthesis["citations"] = stats
        if content != old:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        out.append((entry["note_path"], entry["name"], content))
    return out


def index_notes(vault: Path, written: list[tuple[str, str, str]], kind: str = "entity",
                embed: bool = True) -> None:
    """Refresh the search indexes for notes just written (best effort, as every writer does). An
    embedding failure (the local model is missing) is reported once, not once per note. With
    `embed` false only the full-text index is refreshed."""
    embed_ok = embed
    for note_path, name, content in written:
        if embed_ok:
            try:
                from watchdog.pipeline.embed import add_note
                add_note(vault, note_path, content)
            except Exception as e:  # noqa: BLE001
                embed_ok = False
                print(f"  Warning: embed index update failed for {note_path}: {e}", file=sys.stderr)
        try:
            from watchdog.pipeline.fulltext import add_note as fts_add_note
            fts_add_note(vault, note_path, kind, name, content)
        except Exception as e:  # noqa: BLE001
            print(f"  Warning: full-text index update failed for {note_path}: {e}", file=sys.stderr)


def _write_document_notes(vault: Path, shas, entities: dict, documents: dict) -> list[tuple[str, str, str]]:
    from watchdog.pipeline import verification
    from watchdog.pipeline.write_vault import _assert_in_vault, _build_document_note
    marks = verification.marks(vault)
    out = []
    for sha in sorted(shas):
        rec = documents.get(sha)
        if not isinstance(rec, dict) or not rec.get("document_note"):
            continue
        try:
            art = json.loads((vault / ".watchdog" / "extracted" / f"{sha}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue    # no stored extraction: the note on disk is the only copy, so leave it
        doc = dict(art.get("document") or {})
        doc.setdefault("filename", rec.get("filename") or sha[:12])
        ents = [entities[e] for e in rec.get("entities_extracted") or [] if e in entities]
        path = _assert_in_vault(vault / f"{rec['document_note']}.md", vault, "document note_path")
        old = path.read_text(encoding="utf-8") if path.exists() else None
        content = _build_document_note(doc, ents, rec.get("morgue_path"), rec=rec, old=old, sha=sha,
                                       marks=marks)
        if content != old:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        out.append((rec["document_note"], rec.get("title") or rec.get("filename") or sha[:12], content))
    return out


def _rebuild_unlocked(vault: Path, ids=None, documents_too: bool = True, index_search: bool = True,
                      embed: bool = True) -> dict:
    from watchdog.pipeline.write_vault import _write_json_atomic, _update_manifest
    reg = vault / ".watchdog" / "registry"
    entities = _read_json_or(reg / "entities.json", {})
    documents = _read_json_or(reg / "documents.json", {})
    wanted = set(entities) if ids is None else {i for i in ids if i in entities}
    before = json.dumps({e: entities[e] for e in sorted(wanted)}, sort_keys=True)
    entity_facts.clear_cache()
    written = write_entities(vault, wanted, entities, documents)
    docs_written = []
    if documents_too and ids is None:
        docs_written = _write_document_notes(vault, documents, entities, documents)
    if json.dumps({e: entities[e] for e in sorted(wanted)}, sort_keys=True) != before:
        _write_json_atomic(reg / "entities.json", entities)
        _update_manifest(vault, entities)
    if index_search:
        index_notes(vault, written, "entity", embed=embed)
        index_notes(vault, docs_written, "document", embed=embed)
    registry = _read_json_or(reg / "registry.json", {})
    if registry.get("entity_note_format") != NOTE_FORMAT and ids is None:
        registry["entity_note_format"] = NOTE_FORMAT
        _write_json_atomic(reg / "registry.json", registry)
    return {"entities": len(written), "documents": len(docs_written)}


def rebuild(vault: Path, ids=None, documents_too: bool = True, index_search: bool = True) -> dict:
    """Rewrite entity notes (all, or `ids`) and, for a full rebuild, every document note that has a
    stored extraction, from the registries and the stored extractions alone — no model call. Takes
    the registry lock every registry writer takes. Returns counts of notes written."""
    from watchdog.pipeline.write_vault import _registry_lock
    vault = Path(vault)
    reg = vault / ".watchdog" / "registry"
    if not reg.is_dir():
        return {"entities": 0, "documents": 0}
    from watchdog.pipeline import history
    with _registry_lock(reg), history.recording(vault, {"kind": "rebuild"}):
        return _rebuild_unlocked(vault, ids, documents_too, index_search)


def needs_upgrade(vault: Path) -> bool:
    """True when the vault's entity notes were last written in an older format."""
    reg = Path(vault) / ".watchdog" / "registry"
    if not _read_json_or(reg / "entities.json", {}):
        return False
    return _read_json_or(reg / "registry.json", {}).get("entity_note_format") != NOTE_FORMAT


# ── keeping notes current outside a commit ───────────────────────────────────────────────────

STALE_FILE = "notes-stale.json"


def _stale_path(vault: Path) -> Path:
    return Path(vault) / ".watchdog" / "registry" / STALE_FILE


DOC_PREFIX = "doc:"     # a queued document note, by sha, beside the entity ids


def take_stale(vault: Path) -> set[str]:
    """The notes a write outside the commit could not refresh, cleared: entity ids, and documents
    as `doc:<sha>`."""
    path = _stale_path(vault)
    ids = set(_read_json_or(path, []) or [])
    if ids:
        path.unlink(missing_ok=True)
    return ids


def split_stale(items) -> tuple[set[str], set[str]]:
    """(entity ids, document shas) from a stale-notes list."""
    ents, docs = set(), set()
    for i in items:
        (docs.add(i[len(DOC_PREFIX):]) if i.startswith(DOC_PREFIX) else ents.add(i))
    return ents, docs


def write_queued_documents(vault: Path, shas, entities: dict, documents: dict, embed: bool = True) -> int:
    """Re-render these documents' notes (a mark's label changed) and refresh their search rows."""
    written = _write_document_notes(vault, set(shas) & set(documents), entities, documents)
    index_notes(vault, written, "document", embed=embed)
    return len(written)


def refresh(vault: Path, ids, docs=()) -> bool:
    """Re-render these entities' notes, and these documents' notes, now if the registry lock is
    free; otherwise remember them for the next commit pass, which renders them before it persists.
    Used after a reporter's mark, which takes only the ledger's own lock (D271) and must never wait
    on a long commit."""
    from watchdog.pipeline.write_vault import _try_registry_lock, _write_json_atomic
    vault = Path(vault)
    ids, docs = set(ids), set(docs)
    reg = vault / ".watchdog" / "registry"
    if not (ids or docs) or not (reg / "entities.json").exists():
        return True
    with _try_registry_lock(reg) as got:
        if got:
            # Full-text only: a mark changes a word or two of the note, not what it is about, and
            # loading the embedding model for it would stall the app. The next commit re-embeds.
            stale_ids, stale_docs = split_stale(take_stale(vault))
            _rebuild_unlocked(vault, ids | stale_ids, documents_too=False, embed=False)
            if docs | stale_docs:
                write_queued_documents(vault, docs | stale_docs,
                                       _read_json_or(reg / "entities.json", {}),
                                       _read_json_or(reg / "documents.json", {}), embed=False)
            return True
    pending = set(_read_json_or(_stale_path(vault), []) or []) | ids | {DOC_PREFIX + s for s in docs}
    _write_json_atomic(_stale_path(vault), sorted(pending))
    return False


def fact_entities(vault: Path, sha: str, fact: dict) -> set[str]:
    """The current entities one fact of document `sha` is tagged to."""
    index = entity_facts.FactIndex(vault, marks={})
    return {r for t in fact.get("entities") or [] if isinstance(t, str)
            for r in [index.resolve(sha, t)] if r}


def main(argv: list[str] | None = None) -> int:
    """`python -m watchdog.pipeline.entity_notes [--vault DIR]`: rebuild every entity and document
    note of the vault (default: the current folder) from stored data, with no model call. The app
    runs this as the Maintenance job "Rebuild notes"."""
    import argparse
    from watchdog.vault_paths import is_vault
    parser = argparse.ArgumentParser(description="Rebuild every entity and document note from stored data.")
    parser.add_argument("--vault", default=".")
    args = parser.parse_args(argv)
    vault = Path(args.vault).resolve()
    if not is_vault(vault):
        print(f"Error: {vault} is not a Watchdog investigation.", file=sys.stderr)
        return 1
    from watchdog import progress
    progress.emit("stage", stage="rebuild-notes", done=None, total=None)
    out = rebuild(vault)
    print(f"Rebuilt {out['entities']} entity notes and {out['documents']} document notes "
          "from the stored facts. No AI model was used.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
