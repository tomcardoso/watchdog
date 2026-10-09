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
from pathlib import Path

from watchdog.pipeline import resolutions
from watchdog.pipeline.json_io import _read_json, _read_json_or
from watchdog.pipeline.write_vault import (
    _write_json_atomic,
    _defang,
    _extract_contradictions,
    _today,
    _update_manifest,
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


def _run_unlocked(vault: Path, entity_id: str, label: str,
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

    a_title = a_entry.get("title") or a_entry.get("filename", a_slug)
    b_title = b_entry.get("title") or b_entry.get("filename", b_slug)
    callout = build_callout(label, a_value, a_slug, a_title, a_page, b_value, b_slug, b_title, b_page)
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
        return {"added": False, "rid": rid, "entity_name": entry["name"], "sources": [a_title, b_title],
                "note_path": entry["note_path"] + ".md"}

    entry["date_last_updated"] = _today()

    # Re-render the note from data (D280): the registry ledger, with the resolved-contradiction
    # overlay applied to the body, plus the facts, synthesis, relationships and journalist notes.
    from watchdog.pipeline import entity_notes
    written = entity_notes.write_entities(vault, [entity_id], entities_reg, documents_reg)
    entity_notes.index_notes(vault, written)

    _write_json_atomic(entities_path, entities_reg)
    _update_manifest(vault, entities_reg)

    return {"added": True, "rid": rid, "entity_name": entry["name"], "sources": [a_title, b_title],
            "note_path": entry["note_path"] + ".md"}


def run(vault: Path, entity_id: str, label: str,
        a_value: str, a_doc: str, a_page,
        b_value: str, b_doc: str, b_page) -> dict:
    """`_run_unlocked` under the registry lock every registry writer takes (D258). It can run
    from a Claude Code session or a second terminal while `watchdog bark` commits, and an
    unlocked read-modify-write here could write back a stale `entities.json` over that commit."""
    from watchdog.pipeline.write_vault import _registry_lock
    registry_dir = Path(vault) / ".watchdog" / "registry"
    if not registry_dir.is_dir():
        return _run_unlocked(vault, entity_id, label, a_value, a_doc, a_page, b_value, b_doc, b_page)
    with _registry_lock(registry_dir):
        return _run_unlocked(vault, entity_id, label, a_value, a_doc, a_page, b_value, b_doc, b_page)
