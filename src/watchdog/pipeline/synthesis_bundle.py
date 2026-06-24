#!/usr/bin/env python3
"""
Build the entity-synthesis bundle and bulk-apply synthesized prose.

Phase-1 cost reduction (issue #87/#80 follow-up): instead of launching one
subagent per multi-mention entity — each re-reading its fragment file and note,
paying startup + preamble cache-write overhead — Python gathers every
``count >= 2`` entity's fragments and current prose into a single bundle for one
post-ingest model call, then bulk-applies the returned prose deterministically.

Commands:
    watchdog build-synthesis-bundle [--vault .] [--out PATH]
        Reads .watchdog/tmp/entity-fragments/_queue.json and writes one bundle
        JSON describing the entities to synthesize. Prints the entity count.

    watchdog apply-syntheses --bundle PATH [--vault .]
        Reads the model's synthesis result JSON and writes ## Summary / ## Analysis
        for each entity, leaving all other sections untouched. Unknown entity ids
        and empty summaries are skipped, never written.
"""

import argparse
import json
import sys
from pathlib import Path

from watchdog.pipeline.finalize_entity import apply_one
from watchdog.pipeline.write_vault import (
    _extract_summary,
    _extract_analysis,
    _update_manifest,
)


def _fragments_dir(vault_path: Path) -> Path:
    return vault_path / ".watchdog" / "tmp" / "entity-fragments"


def build_bundle(vault_path: Path, min_docs: int = 2) -> dict:
    """Gather every entity that recurs across the project into one synthesis bundle (#140).

    The gate is **project-wide recurrence**: an entity earns a synthesized summary once it
    appears in ``min_docs`` (default 2) distinct documents across the whole investigation — read
    from its registry ``appears_in``, not from this batch's mention count. Only entities *touched
    this run* (present in the fragment queue) are candidates — an untouched entity has nothing new
    to reconcile. Single-document entities are left as deterministic stubs (facts + relationships,
    no summary section); a recurring entity that only got a fresh stub this run is promoted here as
    its ``appears_in`` crosses the threshold, even when the two documents arrived in different
    batches years apart.
    """
    frag_dir = _fragments_dir(vault_path)
    queue_path = frag_dir / "_queue.json"
    if not queue_path.exists():
        return {"entities": []}

    entities_path = vault_path / ".watchdog" / "Registry" / "entities.json"
    entities_reg = json.loads(entities_path.read_text(encoding="utf-8")) if entities_path.exists() else {}

    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    entities = []
    for eid, rec in queue.items():
        if len(entities_reg.get(eid, {}).get("appears_in", [])) < min_docs:
            continue
        frag_path = frag_dir / f"{eid}.md"
        if not frag_path.exists():
            continue
        note_path = vault_path / f"{rec['note_path']}.md"
        entities.append({
            "entity_id":        eid,
            "name":             rec["name"],
            "note_path":        rec["note_path"],
            "current_summary":  _extract_summary(note_path) or "",
            "current_analysis": _extract_analysis(note_path),
            "fragments":        frag_path.read_text(encoding="utf-8"),
        })

    entities.sort(key=lambda e: e["name"].lower())
    return {"entities": entities}


def apply_bundle(result_path: Path, vault_path: Path) -> dict:
    """Bulk-write synthesized prose from the model's result JSON.

    Validates conservatively: unknown entity ids and entries with an empty
    summary are skipped (the note keeps its carried-forward prose, which is still
    correct). Writes entities.json + manifest once after all entities are applied.
    """
    result = json.loads(result_path.read_text(encoding="utf-8"))
    syntheses = result.get("entity_syntheses", [])

    registry_dir   = vault_path / ".watchdog" / "Registry"
    entities_path  = registry_dir / "entities.json"
    documents_path = registry_dir / "documents.json"
    if not entities_path.exists():
        sys.exit("Error: entities.json not found — is this a Watchdog vault?")

    entities_reg  = json.loads(entities_path.read_text())
    documents_reg = json.loads(documents_path.read_text()) if documents_path.exists() else {}

    applied, skipped = [], []
    for item in syntheses:
        eid = item.get("entity_id")
        summary = (item.get("summary") or "").strip()
        if not eid or not summary:
            skipped.append(eid or "<missing id>")
            continue
        analysis = (item.get("analysis") or "").strip()
        if apply_one(eid, summary, analysis, vault_path, entities_reg, documents_reg):
            applied.append(eid)
        else:
            skipped.append(eid)

    if applied:
        entities_path.write_text(
            json.dumps(entities_reg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        _update_manifest(vault_path, entities_reg)

    return {"applied": applied, "skipped": skipped}


def _main_build() -> None:
    parser = argparse.ArgumentParser(
        description="Build the entity-synthesis bundle for the post-ingest pass"
    )
    parser.add_argument("--vault", default=".", help="Vault root directory (default: .)")
    parser.add_argument(
        "--out",
        default=".watchdog/tmp/synthesis-bundle.json",
        help="Bundle output path, relative to the vault (default: .watchdog/tmp/synthesis-bundle.json)",
    )
    args = parser.parse_args()

    vault_path = Path(args.vault).resolve()
    if not (vault_path / ".watchdog").is_dir():
        sys.exit(f"Error: {vault_path} is not a Watchdog vault directory")

    bundle = build_bundle(vault_path)
    out_path = vault_path / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK  {len(bundle['entities'])} entities for synthesis  ({args.out})")


def _main_apply() -> None:
    parser = argparse.ArgumentParser(
        description="Bulk-apply synthesized Summary + Analysis from a post-ingest result"
    )
    parser.add_argument("--bundle", required=True, help="Path to the synthesis result JSON")
    parser.add_argument("--vault", default=".", help="Vault root directory (default: .)")
    args = parser.parse_args()

    vault_path = Path(args.vault).resolve()
    if not (vault_path / ".watchdog").is_dir():
        sys.exit(f"Error: {vault_path} is not a Watchdog vault directory")
    result_path = Path(args.bundle).resolve()
    if not str(result_path).startswith(str(vault_path) + "/"):
        sys.exit(f"Error: --bundle path must be inside the vault directory ({vault_path})")
    if not result_path.exists():
        sys.exit(f"Error: {result_path} not found")

    outcome = apply_bundle(result_path, vault_path)
    print(f"OK  {len(outcome['applied'])} synthesized, {len(outcome['skipped'])} skipped")


def main() -> None:
    if Path(sys.argv[0]).name.endswith("apply-syntheses"):
        _main_apply()
    else:
        _main_build()


if __name__ == "__main__":
    main()
