#!/usr/bin/env python3
"""
Refresh a single entity note from a Claude-synthesized extraction.

Used by /watchdog-entity to rewrite an entity's Summary and Timeline
after re-reading all documents the entity appears in. Unlike
watchdog-write-vault (which accumulates), this command replaces
Summary and Timeline in full — it's a fresh synthesis.

Usage:
    watchdog-write-entity --entity-id alice-smith --extraction /tmp/entity-refresh-alice-smith.json [--vault .]

Extraction JSON schema:
{
  "entity_id": str,
  "summary": str,
  "timeline_events": [
    {
      "date": str,         // YYYY-MM-DD, YYYY-MM, or YYYY
      "event": str,
      "source_sha256": str,
      "page": int|null,
      "confidence": str
    }
  ]
}
"""

import argparse
import json
import sys
from pathlib import Path

from watchdog.pipeline.write_vault import (
    _extract_notes_section,
    _extract_analysis,
    _rebuild_global_timeline,
    build_entity_note,
    _today,
    _now_iso,
)


def run(extraction_path: Path, vault_path: Path) -> None:
    extraction = json.loads(extraction_path.read_text(encoding="utf-8"))
    entity_id  = extraction["entity_id"]
    new_summary = extraction.get("summary") or None
    new_events  = extraction.get("timeline_events", [])

    registry_dir   = vault_path / ".watchdog" / "Registry"
    entities_path  = registry_dir / "entities.json"
    documents_path = registry_dir / "documents.json"

    if not entities_path.exists():
        sys.exit("Error: entities.json not found — is this a Watchdog vault?")

    entities_reg  = json.loads(entities_path.read_text())
    documents_reg = json.loads(documents_path.read_text()) if documents_path.exists() else {}

    if entity_id not in entities_reg:
        sys.exit(f"Error: entity '{entity_id}' not found in entities.json")

    entry = entities_reg[entity_id]
    note_path = vault_path / f"{entry['note_path']}.md"

    # Replace timeline events entirely (full refresh from all documents)
    entry["timeline_events"] = new_events
    entry["date_last_updated"] = _today()

    # Preserve existing analysis — entity refresh doesn't touch it
    existing_analysis = _extract_analysis(note_path)
    notes_section = _extract_notes_section(note_path)

    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(
        build_entity_note(entry, notes_section, documents_reg, new_summary, existing_analysis),
        encoding="utf-8",
    )

    entities_path.write_text(
        json.dumps(entities_reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    _rebuild_global_timeline(vault_path, entities_reg, documents_reg)

    print(f"OK  {entity_id}  timeline_events={len(new_events)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Refresh an entity note from a Claude-synthesized extraction"
    )
    parser.add_argument("--entity-id", required=True, help="Entity ID (kebab-case)")
    parser.add_argument("--extraction", required=True, help="Path to entity refresh JSON")
    parser.add_argument("--vault", default=".", help="Vault root directory (default: .)")
    args = parser.parse_args()

    extraction_path = Path(args.extraction)
    vault_path = Path(args.vault).resolve()

    if not extraction_path.exists():
        sys.exit(f"Error: {extraction_path} not found")
    if not vault_path.exists():
        sys.exit(f"Error: vault directory {vault_path} not found")

    run(extraction_path, vault_path)


if __name__ == "__main__":
    main()
