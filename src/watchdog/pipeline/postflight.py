"""
Watchdog post-flight — validates an extraction JSON and writes it to the vault.

Handles everything after Claude produces the extraction JSON:
  1. Validates the extraction (schema + required fields)
  2. Applies match_id decisions (Claude signals entity merges)
  3. Reads near-dup minhash from the queue file
  4. Calls write_vault.run() directly
  5. Cleans up temp files
  6. Returns {"ok": true} or {"errors": [...]}
"""

import json
import sys
from pathlib import Path

_VALID_CONFIDENCE = {"high", "medium", "low", "disputed"}


def _validate(data: dict) -> list[str]:
    errors: list[str] = []

    doc = data.get("document")
    if not isinstance(doc, dict):
        errors.append("missing or invalid 'document' field")
    else:
        for field in ("sha256", "filename"):
            if not doc.get(field):
                errors.append(f"document.{field} is missing or empty")
        for i, fact in enumerate(doc.get("key_facts", [])):
            if not isinstance(fact, dict):
                errors.append(f"document.key_facts[{i}] is not an object")
            elif fact.get("confidence") and fact["confidence"] not in _VALID_CONFIDENCE:
                errors.append(f"document.key_facts[{i}].confidence '{fact['confidence']}' must be one of: {', '.join(sorted(_VALID_CONFIDENCE))}")

    entities = data.get("entities")
    if not isinstance(entities, list):
        errors.append("missing or invalid 'entities' field")
    else:
        for i, ent in enumerate(entities):
            if not isinstance(ent, dict):
                errors.append(f"entities[{i}] is not an object")
                continue
            for field in ("id", "name", "type"):
                if not ent.get(field):
                    errors.append(f"entities[{i}].{field} is missing or empty")
            for j, ev in enumerate(ent.get("timeline_events", [])):
                if not isinstance(ev, dict):
                    errors.append(f"entities[{i}].timeline_events[{j}] is not an object")
                elif ev.get("confidence") and ev["confidence"] not in _VALID_CONFIDENCE:
                    errors.append(f"entities[{i}].timeline_events[{j}].confidence '{ev['confidence']}' must be one of: {', '.join(sorted(_VALID_CONFIDENCE))}")
            for j, role in enumerate(ent.get("roles", [])):
                if not isinstance(role, dict):
                    errors.append(f"entities[{i}].roles[{j}] must be an object with relationship/target_id/target_type/target_name/page/confidence/date_range keys — not a string")

    if not data.get("morgue_entity_id"):
        errors.append("morgue_entity_id is missing or empty — this is the kebab-case id of the entity this document is primarily about")
    if not data.get("morgue_document_type"):
        errors.append("morgue_document_type is missing or empty — use a slug like annual-report, court-order, bankruptcy-filing")

    return errors


def _apply_match_ids(extraction: dict) -> dict:
    """Rewrite entity IDs based on Claude's match_id merge decisions."""
    for entity in extraction.get("entities", []):
        match_id = entity.pop("match_id", None)
        if match_id:
            entity["id"] = match_id
    return extraction


def run(vault: Path, extraction_path: Path) -> dict:
    if not extraction_path.exists():
        return {"errors": [f"extraction file not found: {extraction_path}"]}

    try:
        extraction = json.loads(extraction_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {"errors": [f"invalid JSON: {e}"]}

    errors = _validate(extraction)
    if errors:
        return {"errors": errors}

    extraction = _apply_match_ids(extraction)

    # Get near-dup minhash from queue file (computed at chew time)
    sha256 = extraction.get("document", {}).get("sha256", "")
    neardup_data: dict = {}
    if sha256:
        queue_file = vault / ".watchdog" / "queue" / f"{sha256}.json"
        if queue_file.exists():
            try:
                q = json.loads(queue_file.read_text(encoding="utf-8"))
                neardup_data = q.get("near_dup", {})
            except Exception:
                pass

    # Write the validated (and match_id-resolved) extraction back so write_vault reads it
    extraction_path.write_text(
        json.dumps(extraction, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    try:
        from watchdog.pipeline.write_vault import run as wv_run
        wv_run(
            extraction_path=extraction_path,
            vault_path=vault,
            neardup_data=neardup_data,
            skip_timeline=True,
        )
    except SystemExit as e:
        return {"errors": [str(e)]}
    except Exception as e:
        return {"errors": [str(e)]}

    # Stage raw timeline NDJSON files (replaces the subagent's manual per-date
    # writes). The vault is already written at this point, so a staging failure
    # is reported as a warning rather than failing the whole extraction —
    # erroring here would trigger a retry and double-write the vault.
    try:
        from watchdog.pipeline.timeline import stage_timeline_events
        stage_timeline_events(vault, extraction)
    except Exception as e:
        print(f"Warning: timeline staging failed: {e}", file=sys.stderr)

    # Clean up temp files
    for path in [
        extraction_path,
        vault / ".watchdog" / "tmp" / f"wdg_nd_{sha256}.json",
    ]:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    return {"ok": True, "sha256": sha256}


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Watchdog post-flight processor")
    parser.add_argument("--extraction", required=True, help="Path to extraction JSON")
    args = parser.parse_args()

    vault = Path(".").resolve()
    if not (vault / ".watchdog").is_dir():
        sys.exit("Error: must be run from inside a Watchdog vault directory")

    extraction_path = Path(args.extraction).resolve()
    if not str(extraction_path).startswith(str(vault) + "/"):
        sys.exit(f"Error: --extraction must be inside the vault directory ({vault})")

    result = run(vault, extraction_path)
    print(json.dumps(result, ensure_ascii=False))
    if "errors" in result:
        sys.exit(1)


if __name__ == "__main__":
    main()
