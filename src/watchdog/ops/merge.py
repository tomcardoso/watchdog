"""Merge a duplicate entity into another (#219): deterministic registry surgery, no model call.

The same real-world entity can end up under two ids when reconciliation misses a pair. The app
shows both entities and asks before it starts this; the merge itself is the reporter's (I12) and
can be undone (D281)."""

import json
import sys
from pathlib import Path

from watchdog.ops import op, require_vault
from watchdog.pipeline import merge_entities as _merge_entities
from watchdog.pipeline.json_io import _read_json


@op("merge-entities", engine="index")
def merge_entities(rep, vault: Path, *, keep: str, merge: str) -> dict:
    """Fold entity `merge` into `keep`."""
    require_vault(vault)
    try:
        entities = _read_json(vault / ".watchdog" / "registry" / "entities.json")
    except (OSError, json.JSONDecodeError):
        sys.exit("Error: the entity registry is missing or unreadable.")
    for eid in (keep, merge):
        if eid not in entities:
            sys.exit(f"Error: there is no entity “{eid}” in this investigation.")
    try:
        result = _merge_entities.run(vault, keep, merge)
    except OSError as e:
        # The registry lock (D258): on Windows `msvcrt` gives up after ~10s while another run
        # (usually post-processing committing a batch) holds it.
        sys.exit(f"Error: the registry could not be written ({e}). If documents are being added "
                 "to this investigation, wait for that to finish and try again.")
    except ValueError as e:
        sys.exit(f"Error: {e}")

    rep.log(f"Merged {result['merge_name']} into {result['keep_name']}: {result['aliases']} "
            f"aliases, {result['appears_in']} documents, {result['roles']} relationships, "
            f"{result['timeline_events']} timeline events.")
    if result["remapped_roles"]:
        n = result["remapped_roles"]
        rep.log(f"{n} relationship{'s' if n != 1 else ''} elsewhere in the investigation now "
                f"point at {result['keep_name']}.")
    rep.log("Rebuild the search index (Activity → Maintenance) to drop the merged entity's old "
            "search entries.")
    if result.get("summary_dropped"):
        rep.log("The summary now reflects only the kept entity.")
    return {k: (str(v) if isinstance(v, Path) else v) for k, v in result.items()}
