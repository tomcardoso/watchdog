"""Promote a surface-found contradiction candidate into an entity note (#312).

`/watchdog-surface` reports cross-document contradictions as labelled *candidates* in its
report rather than writing `[!contradiction]` callouts into entity notes — those notes are
pipeline-owned, and a hand-inserted callout bypasses both extraction-time verification and the
resolutions layer (D81). That left no sanctioned way to get a *verified* candidate into the
note. This is the escape hatch D81's tradeoff note anticipated: a small deterministic writer
that emits the callout in the exact format extraction produces, through the pipeline's own
note builder, into the entity's ``## Contradictions`` section — so the journalist stays the
gate (they run the command) but the pipeline stays the sole writer of the note.

No model calls. Run from inside the vault it mutates, the same convention
``watchdog merge-entities`` / ``watchdog resolve`` use.
"""

import json
import sys
from pathlib import Path

from watchdog.pipeline import resolutions
from watchdog.pipeline.json_io import _read_json, _read_json_or
from watchdog.pipeline.write_vault import (
    _defang,
    _extract_analysis,
    _extract_contradictions,
    _extract_notes_section,
    _extract_summary,
    _today,
    _update_manifest,
    build_entity_note,
)


def _cite(value: str, slug: str, title: str, page) -> str:
    """One callout line: ``> - **<value>** — [[documents/<slug>|<title>]], p. <n>``.

    The document title is registry-sourced, so it is defanged before interpolation into the
    wikilink display text (matching ``build_entity_note`` / #305); ``value`` is journalist-typed
    and left as given. The page suffix is omitted when no page was supplied."""
    cite = f"[[documents/{slug}|{_defang(title)}]]"
    if page is not None:
        cite += f", p. {page}"
    return f"> - **{value}** — {cite}"


def build_callout(label, a_value, a_slug, a_title, a_page, b_value, b_slug, b_title, b_page) -> str:
    """Assemble a ``[!contradiction]`` callout block in the exact shape extraction emits
    (see ``extract_instructions.md``)."""
    return (
        f"> [!contradiction] {label}\n"
        f"{_cite(a_value, a_slug, a_title, a_page)}\n"
        f"{_cite(b_value, b_slug, b_title, b_page)}"
    )


def _doc_index(documents_reg: dict) -> dict:
    """Map each document's slug (the segment after ``documents/`` in its note link) to its
    registry entry, so a ``--a-doc``/``--b-doc`` slug can be validated and its title resolved."""
    index: dict[str, dict] = {}
    for entry in documents_reg.values():
        note = entry.get("document_note")  # "documents/<slug>"
        if note:
            index[note.split("/")[-1]] = entry
    return index


def _resolve_doc(doc_index: dict, doc_arg: str) -> tuple[str, dict]:
    """Resolve a user-supplied document reference (bare slug or ``documents/<slug>``) to its
    (slug, entry). Raises ValueError if no document with that slug exists in the vault."""
    slug = doc_arg.strip()
    if slug.startswith("documents/"):
        slug = slug[len("documents/"):]
    entry = doc_index.get(slug)
    if entry is None:
        raise ValueError(f"document '{doc_arg}' not found — no document with slug '{slug}' in the vault")
    return slug, entry


def run(vault: Path, entity_id: str, label: str,
        a_value: str, a_doc: str, a_page,
        b_value: str, b_doc: str, b_page) -> dict:
    """Write a verified contradiction callout into an entity's note and registry ledger.

    Validates that the entity id and both document slugs exist, builds the callout, folds it
    into the entity's registry ``contradictions`` list (deduped), and re-renders the note with
    the resolved-contradiction overlay applied — exactly as the ingest writer does. Returns a
    result dict with ``added`` (False if the callout was already present), the callout's
    resolution ``rid``, the entity name, and the note path. Raises ValueError on bad input.
    """
    registry_dir = vault / ".watchdog" / "registry"
    entities_path = registry_dir / "entities.json"
    documents_path = registry_dir / "documents.json"

    try:
        entities_reg = _read_json(entities_path)
    except (OSError, json.JSONDecodeError):
        raise ValueError("entities.json not found or unreadable — is this a Watchdog vault?")
    if entity_id not in entities_reg:
        raise ValueError(f"entity '{entity_id}' not found in entities.json")

    documents_reg = _read_json_or(documents_path, {})

    doc_index = _doc_index(documents_reg)
    a_slug, a_entry = _resolve_doc(doc_index, a_doc)
    b_slug, b_entry = _resolve_doc(doc_index, b_doc)

    callout = build_callout(
        label,
        a_value, a_slug, a_entry.get("title") or a_entry.get("filename", a_slug), a_page,
        b_value, b_slug, b_entry.get("title") or b_entry.get("filename", b_slug), b_page,
    )
    rid = resolutions.contradiction_id(callout)

    entry = entities_reg[entity_id]
    note_path = vault / f"{entry['note_path']}.md"

    # The registry entry is the contradiction ledger (#282); fold in any note-only callouts too
    # (self-healing backfill, matching the ingest writer) before adding this one.
    existing = list(entry.get("contradictions") or []) + resolutions.split_callouts(
        _extract_contradictions(note_path)
    )
    already_present = rid in {resolutions.contradiction_id(c) for c in existing}
    all_callouts = resolutions.dedup_callouts(existing + [callout])
    entry["contradictions"] = all_callouts

    if already_present:
        return {"added": False, "rid": rid, "entity_name": entry["name"],
                "note_path": entry["note_path"] + ".md"}

    entry["date_last_updated"] = _today()

    # Re-render the note from the registry, applying the resolved-contradiction overlay to the
    # body (the ledger keeps the full list; the body is a filtered render) and preserving the
    # note-only prose sections the registry does not carry.
    resolved = resolutions.resolved_ids(vault)
    contradictions_body = "\n\n".join(resolutions.filter_callouts(all_callouts, resolved))
    summary = _extract_summary(note_path)
    analysis = _extract_analysis(note_path)
    notes_section = _extract_notes_section(note_path)

    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_content = build_entity_note(
        entry, notes_section, documents_reg, summary, analysis, contradictions_body
    )
    note_path.write_text(note_content, encoding="utf-8")

    try:
        from watchdog.pipeline.embed import add_note
        add_note(vault, entry["note_path"], note_content)
    except Exception as e:
        print(f"  Warning: embed index update failed for {entry['note_path']}: {e}", file=sys.stderr)
    try:
        from watchdog.pipeline.fulltext import add_note as fts_add_note
        fts_add_note(vault, entry["note_path"], "entity", entry["name"], note_content)
    except Exception as e:
        print(f"  Warning: full-text index update failed for {entry['note_path']}: {e}", file=sys.stderr)

    entities_path.write_text(
        json.dumps(entities_reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    _update_manifest(vault, entities_reg)

    return {"added": True, "rid": rid, "entity_name": entry["name"],
            "note_path": entry["note_path"] + ".md"}
