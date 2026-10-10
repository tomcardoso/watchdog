"""
Refresh a single entity note from a Claude-synthesized extraction.

Used by /watchdog-entity to rewrite an entity's Summary and Timeline
after re-reading all documents the entity appears in. Unlike
the processing writer (which accumulates), this replaces
Summary and Timeline in full — it's a fresh synthesis.

The refreshed events replace the entity note's own Timeline. timeline.md is re-rendered too, but
from the deduplicated per-date timeline files, so the refreshed events don't change it.

An Ask Claude session calls it through the `write_entity` tool (`watchdog/session_tools.py`, D299).

Extraction schema:
{
  "entity_id": str,
  "summary": str,
  "timeline_events": [
    {
      "date": str,         // YYYY-MM-DD, YYYY-MM, or YYYY
      "event": str,
      "source_sha256": str,
      "page": int|null,
      "basis": "stated"|"inferred"
    }
  ]
}
"""

import json
from pathlib import Path

from watchdog.pipeline.write_vault import (
    _defang_links,
    _now_iso,
    _registry_lock,
    _today,
    _update_manifest,
    _write_json_atomic,
)
from watchdog.pipeline.timeline import cmd_rebuild_timeline


def _clean_events(events: list) -> list[dict]:
    """Keep well-formed events, with dates normalized the way post-flight does — an unparseable
    date is dropped (the event stays, undated) rather than reaching the note's year grouping."""
    from watchdog.pipeline.postflight import _DATE_RE, _parse_precise_date
    out = []
    for ev in events or []:
        if not isinstance(ev, dict) or not (ev.get("event") or "").strip():
            continue
        date = (ev.get("date") or "").strip()
        if date and not _DATE_RE.match(date):
            date = _parse_precise_date(date) or ""
        out.append({**ev, "date": date})
    return out


def apply(vault_path: Path, extraction: dict) -> dict:
    """Store a session's refreshed summary and timeline for one entity and re-render its note.
    Returns `{"entity_id", "name", "timeline_events"}`. Raises ValueError when the vault has no
    entity registry or the entity isn't in it."""
    vault_path = Path(vault_path)
    entity_id  = extraction.get("entity_id")
    if not isinstance(entity_id, str) or not entity_id:
        raise ValueError("entity_id is required")
    new_summary = _defang_links(extraction.get("summary") or "") or None
    new_events  = _clean_events(extraction.get("timeline_events", []))

    registry_dir   = vault_path / ".watchdog" / "registry"
    entities_path  = registry_dir / "entities.json"
    documents_path = registry_dir / "documents.json"

    if not entities_path.exists():
        raise ValueError("entities.json not found — is this a Watchdog vault?")

    # Under the same lock every other registry writer takes, and written atomically: a session
    # can overlap a post-processing run committing a batch.
    with _registry_lock(registry_dir):
        entities_reg  = json.loads(entities_path.read_text(encoding="utf-8"))
        documents_reg = json.loads(documents_path.read_text(encoding="utf-8")) if documents_path.exists() else {}

        if entity_id not in entities_reg:
            raise ValueError(f"entity '{entity_id}' not found in entities.json")

        entry = entities_reg[entity_id]
        # Replace timeline events entirely (full refresh from all documents)
        entry["timeline_events"] = new_events
        entry["date_last_updated"] = _today()

        # The session's summary is AI-written prose: stored as the entity's synthesis record and
        # shown under "Summary"; the rest of the note is rendered from data (D280).
        from watchdog.pipeline import entity_notes
        if new_summary:
            old = entry.get("synthesis") or {}
            entry["synthesis"] = {
                "version": entity_notes.SYNTHESIS_VERSION, "summary": new_summary,
                "analysis": old.get("analysis") or "",
                "by": "session", "model": None, "made_at": _now_iso(),
                "facts_total": None, "facts_shown": None, "fact_refs": {},
            }
        written = entity_notes.write_entities(vault_path, [entity_id], entities_reg, documents_reg)
        _write_json_atomic(entities_path, entities_reg)
        _update_manifest(vault_path, entities_reg)
        cmd_rebuild_timeline(vault_path, quiet=True)

    entity_notes.index_notes(vault_path, written)
    return {"entity_id": entity_id, "name": entry.get("name"), "timeline_events": len(new_events)}
