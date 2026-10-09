#!/usr/bin/env python3
"""
Refresh a single entity note from a Claude-synthesized extraction.

Used by /watchdog-entity to rewrite an entity's Summary and Timeline
after re-reading all documents the entity appears in. Unlike
watchdog-write-vault (which accumulates), this command replaces
Summary and Timeline in full — it's a fresh synthesis.

The refreshed events replace the entity note's own Timeline. timeline.md is re-rendered too, but
from the deduplicated per-date timeline files, so the refreshed events don't change it.

Usage:
    watchdog-write-entity --entity-id alice-smith --extraction .watchdog/tmp/entity-refresh-alice-smith.json [--vault .]

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
      "basis": "stated"|"inferred"
    }
  ]
}
"""

import argparse
import json
import os
import sys
from pathlib import Path

from watchdog.vault_paths import is_vault
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


def run(extraction_path: Path, vault_path: Path) -> None:
    extraction = json.loads(extraction_path.read_text(encoding="utf-8"))
    entity_id  = extraction["entity_id"]
    new_summary = _defang_links(extraction.get("summary") or "") or None
    new_events  = _clean_events(extraction.get("timeline_events", []))

    registry_dir   = vault_path / ".watchdog" / "registry"
    entities_path  = registry_dir / "entities.json"
    documents_path = registry_dir / "documents.json"

    if not entities_path.exists():
        sys.exit("Error: entities.json not found — is this a Watchdog vault?")

    # Under the same lock every other registry writer takes, and written atomically: this runs
    # from a Claude Code session that can overlap a `watchdog bark` in the terminal.
    with _registry_lock(registry_dir):
        entities_reg  = json.loads(entities_path.read_text(encoding="utf-8"))
        documents_reg = json.loads(documents_path.read_text(encoding="utf-8")) if documents_path.exists() else {}

        if entity_id not in entities_reg:
            sys.exit(f"Error: entity '{entity_id}' not found in entities.json")

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

    # The refresh JSON was scratch input; clearing it here saves the skill a `rm` call (and its
    # permission prompt).
    tmp_dir = (vault_path / ".watchdog" / "tmp").resolve()
    if extraction_path.resolve().is_relative_to(tmp_dir):
        extraction_path.unlink(missing_ok=True)

    print(f"OK  {entity_id}  timeline_events={len(new_events)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Refresh an entity note from a Claude-synthesized extraction"
    )
    parser.add_argument("--entity-id", required=True, help="Entity ID (kebab-case)")
    parser.add_argument("--extraction", required=True, help="Path to entity refresh JSON")
    parser.add_argument("--vault", default=".", help="Vault root directory (default: .)")
    args = parser.parse_args()

    extraction_path = Path(args.extraction).resolve()
    vault_path = Path(args.vault).resolve()

    # The vault's settings pre-approve this command for /watchdog-entity. From a Claude Code
    # session it may only write to the vault the session runs in, never another one (D257, I6).
    # Checked before anything about the path is reported, so it can't confirm a guessed name.
    if os.environ.get("CLAUDECODE") and vault_path != Path(".").resolve():
        sys.exit("Error: from inside a Claude Code session, write-entity only writes to this "
                 "investigation; --vault must be the current folder.")
    if not vault_path.exists():
        sys.exit(f"Error: vault directory {vault_path} not found")
    if not is_vault(vault_path):
        sys.exit(f"Error: {vault_path} is not a Watchdog vault directory")
    if not str(extraction_path).startswith(str(vault_path) + "/"):
        sys.exit(f"Error: --extraction path must be inside the vault directory ({vault_path})")
    if not extraction_path.exists():
        sys.exit(f"Error: {extraction_path} not found")

    run(extraction_path, vault_path)


if __name__ == "__main__":
    main()
