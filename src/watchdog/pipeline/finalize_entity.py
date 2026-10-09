#!/usr/bin/env python3
"""
Store a finalizer's synthesized prose for an entity and re-render its note (D280).

Used by post-ingest after synthesis. The prose is stored in the registry entry's `synthesis`
record and shown in the note under "Summary"; every other section of the note is
rendered from data (`entity_notes`), and the journalist's ## Notes are never touched. It backs the
bulk synthesis path (``synthesis_bundle.apply_bundle``, called from `orchestrate.py`); this
module's own ``main()`` below is a standalone single-entity entry point (not wired into the
`watchdog` CLI), runnable as ``python -m watchdog.pipeline.finalize_entity``.

Usage:
    python -m watchdog.pipeline.finalize_entity --entity-id alice-smith --extraction .watchdog/tmp/wdg_synth-alice-smith.json [--vault .]

Extraction JSON schema:
{
  "entity_id": str,
  "summary": str,
  "analysis": str|null
}
"""

import json
import sys
from pathlib import Path

from watchdog.pipeline.write_vault import (
    _update_manifest,
    _today,
)


def apply_one(
    entity_id: str,
    new_summary: str | None,
    new_analysis: str,
    vault_path: Path,
    entities_reg: dict,
    documents_reg: dict,
    meta: dict | None = None,
) -> bool:
    """Store one entity's synthesized prose and re-render its note.

    The prose is kept in the registry entry's `synthesis` record (D280), with who wrote it and
    from how many facts (`meta`), and the note is rendered from data: the prose under "Summary",
    the facts, contradictions, relationships and the journalist's Notes untouched.
    Mutates ``entities_reg`` but does not persist entities.json — the caller writes the registry
    once after applying every entity. Returns False if the entity is unknown."""
    from watchdog.pipeline import entity_notes
    from watchdog.pipeline.write_vault import _now_iso
    if entity_id not in entities_reg:
        return False

    entry = entities_reg[entity_id]
    meta = dict(meta or {})
    entry["synthesis"] = {
        "version": entity_notes.SYNTHESIS_VERSION,
        "summary": (new_summary or "").strip(),
        "analysis": (new_analysis or "").strip(),
        "by": meta.pop("by", "model"),
        "model": meta.pop("model", None),
        "made_at": meta.pop("made_at", None) or _now_iso(),
        "facts_total": meta.pop("facts_total", None),
        "facts_shown": meta.pop("facts_shown", None),
        "fact_refs": meta.pop("fact_refs", {}) or {},
        **meta,
    }
    entry["date_last_updated"] = _today()
    written = entity_notes.write_entities(vault_path, [entity_id], entities_reg, documents_reg)
    entity_notes.index_notes(vault_path, written)
    return True


def run(extraction_path: Path, vault_path: Path) -> None:
    extraction  = json.loads(extraction_path.read_text(encoding="utf-8"))
    entity_id   = extraction["entity_id"]
    new_summary  = extraction.get("summary") or None
    new_analysis = extraction.get("analysis") or ""

    registry_dir   = vault_path / ".watchdog" / "registry"
    entities_path  = registry_dir / "entities.json"
    documents_path = registry_dir / "documents.json"

    if not entities_path.exists():
        sys.exit("Error: entities.json not found — is this a Watchdog vault?")

    entities_reg  = json.loads(entities_path.read_text())
    documents_reg = json.loads(documents_path.read_text()) if documents_path.exists() else {}

    if not apply_one(entity_id, new_summary, new_analysis, vault_path, entities_reg, documents_reg):
        sys.exit(f"Error: entity '{entity_id}' not found in entities.json")

    entities_path.write_text(
        json.dumps(entities_reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    _update_manifest(vault_path, entities_reg)

    print(f"OK  {entity_id}  summary+analysis synthesized")
