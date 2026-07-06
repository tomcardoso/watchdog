#!/usr/bin/env python3
"""
Write a finalizer's synthesized prose into an entity note.

Used by post-ingest after it reconciles an entity's per-document fragments. The
shared ``apply_one`` writer replaces *only* the prose sections — ## Summary and
## Analysis — leaving the append-only ## Contradictions log, the
deterministically-merged ## Timeline, ## Relationships, and the journalist
## Notes untouched. It backs the bulk synthesis path (``synthesis_bundle.py``'s
``apply_bundle``, called from `orchestrate.py`); this module's own ``main()``
below is a standalone single-entity entry point (not wired into the `watchdog`
CLI), runnable as ``python -m watchdog.pipeline.finalize_entity``.

Unlike watchdog-write-entity (the /watchdog-entity full refresh, which also
re-synthesizes the Timeline), this is a narrow prose-only write — it never reads
or rewrites structured sections.

Usage:
    python -m watchdog.pipeline.finalize_entity --entity-id alice-smith --extraction .watchdog/tmp/wdg_synth-alice-smith.json [--vault .]

Extraction JSON schema:
{
  "entity_id": str,
  "summary": str,
  "analysis": str|null
}
"""

import argparse
import json
import sys
from pathlib import Path

from watchdog.pipeline.write_vault import (
    _extract_notes_section,
    _extract_contradictions,
    _update_manifest,
    build_entity_note,
    _today,
)


def apply_one(
    entity_id: str,
    new_summary: str | None,
    new_analysis: str,
    vault_path: Path,
    entities_reg: dict,
    documents_reg: dict,
) -> bool:
    """Write one entity's synthesized prose into its note.

    Replaces only ## Summary and ## Analysis; preserves Contradictions, Timeline,
    Relationships, and journalist Notes. Mutates the entry's date_last_updated in
    ``entities_reg`` but does not persist entities.json — the caller writes the
    registry once after applying every entity. Returns False if the entity is
    unknown (caller decides whether that is an error or a skip).

    Shared by this module's standalone single-entity entry point and the bulk
    synthesis_bundle.apply_bundle path.
    """
    if entity_id not in entities_reg:
        return False

    entry = entities_reg[entity_id]
    note_path = vault_path / f"{entry['note_path']}.md"

    # Timeline and Relationships come from the registry entry unchanged; contradictions
    # and journalist notes are preserved from the existing note. Only the prose is new.
    notes_section = _extract_notes_section(note_path)
    contradictions = _extract_contradictions(note_path)
    entry["date_last_updated"] = _today()

    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_content = build_entity_note(
        entry, notes_section, documents_reg, new_summary, new_analysis, contradictions
    )
    note_path.write_text(note_content, encoding="utf-8")
    try:
        from watchdog.pipeline.embed import add_note
        add_note(vault_path, entry["note_path"], note_content)
    except Exception as e:
        print(f"  Warning: embed index update failed for {entry['note_path']}: {e}", file=sys.stderr)
    try:
        from watchdog.pipeline.fulltext import add_note as fts_add_note
        fts_add_note(vault_path, entry["note_path"], "entity", entry["name"], note_content)
    except Exception as e:
        print(f"  Warning: full-text index update failed for {entry['note_path']}: {e}", file=sys.stderr)
    return True


def run(extraction_path: Path, vault_path: Path) -> None:
    extraction  = json.loads(extraction_path.read_text(encoding="utf-8"))
    entity_id   = extraction["entity_id"]
    new_summary  = extraction.get("summary") or None
    new_analysis = extraction.get("analysis") or ""

    registry_dir   = vault_path / ".watchdog" / "Registry"
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Write a finalizer's synthesized Summary + Analysis into an entity note"
    )
    parser.add_argument("--entity-id", required=True, help="Entity ID (kebab-case)")
    parser.add_argument("--extraction", required=True, help="Path to synthesis JSON")
    parser.add_argument("--vault", default=".", help="Vault root directory (default: .)")
    args = parser.parse_args()

    extraction_path = Path(args.extraction).resolve()
    vault_path = Path(args.vault).resolve()

    if not vault_path.exists():
        sys.exit(f"Error: vault directory {vault_path} not found")
    if not (vault_path / ".watchdog").is_dir():
        sys.exit(f"Error: {vault_path} is not a Watchdog vault directory")
    if not str(extraction_path).startswith(str(vault_path) + "/"):
        sys.exit(f"Error: --extraction path must be inside the vault directory ({vault_path})")
    if not extraction_path.exists():
        sys.exit(f"Error: {extraction_path} not found")

    run(extraction_path, vault_path)


if __name__ == "__main__":
    main()
