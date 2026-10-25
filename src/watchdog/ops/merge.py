"""Merge a duplicate entity into another (#219): deterministic registry surgery, no model call.

The same real-world entity can end up under two ids when reconciliation misses a pair. The app
shows both entities and asks before it starts this; the merge itself is the reporter's (I12) and
can be undone (D281)."""

import json
import sys
from pathlib import Path

from watchdog.ops import op, require_vault
from watchdog.pipeline import merge_entities as _merge_entities
from watchdog.pipeline.json_io import _read_json, _read_json_or


@op("merge-entities", engine="index")
def merge_entities(rep, vault: Path, *, keep: str, merge: str) -> dict:
    """Fold entity `merge` into `keep`."""
    result = _merge_one(rep, vault, keep, merge)
    rep.log("Rebuild the search index (Activity → Maintenance) to drop the merged entity's old "
            "search entries.")
    return result


def _merge_one(rep, vault: Path, keep: str, merge: str) -> dict:
    """One merge, logged to the merge log and the vault's history by `merge_entities.run`. Exits
    with the reason when it cannot be done."""
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
    if result.get("summary_dropped"):
        rep.log("The summary now reflects only the kept entity.")
    return {k: (str(v) if isinstance(v, Path) else v) for k, v in result.items()}


@op("merge-entity-pairs", engine="index", exit_code=lambda r: 1 if (r or {}).get("failed") else 0)
def merge_entity_pairs(rep, vault: Path, *, keep: list[str], merge: list[str]) -> dict:
    """Merge several pairs, `merge[i]` into `keep[i]`, one at a time through the same merge as
    `merge-entities` (Review's "Merge ticked", D328): each is logged and can be undone on its own.

    A pair that fails is reported and the rest go on; merges already made stay made. A pair whose
    record an earlier pair in the batch merged away is followed to the record it joined, and one
    whose two sides are already joined that way is skipped. Returns `{merged, failed, skipped}`,
    each a list of `{keep, merge, keep_name, merge_name}`, a failure with its `error`."""
    require_vault(vault)
    if len(keep) != len(merge) or not keep:
        sys.exit("Error: give the same number of records to keep and to merge.")
    joined: dict[str, str] = {}   # merged-away id → the id it joined

    def follow(eid: str) -> str:
        seen = set()
        while eid in joined and eid not in seen:
            seen.add(eid)
            eid = joined[eid]
        return eid

    def names(k: str, m: str) -> dict:
        ents = _read_json_or(vault / ".watchdog" / "registry" / "entities.json", {})
        name = lambda e: (ents.get(e) or {}).get("name") or e   # noqa: E731
        return {"keep": k, "merge": m, "keep_name": name(k), "merge_name": name(m)}

    merged, failed, skipped = [], [], []
    total = len(keep)
    for n, (k0, m0) in enumerate(zip(keep, merge), 1):
        k, m = follow(k0), follow(m0)
        if k == m:
            skipped.append(names(k0, m0))
            rep.log(f"{n} of {total}: already one record; nothing to do.")
            continue
        row = names(k, m)
        rep.log(f"{n} of {total}: merging {row['merge_name']} into {row['keep_name']}.")
        try:
            _merge_one(rep, vault, k, m)
        except SystemExit as e:
            reason = str(e.code if e.code is not None else "").removeprefix("Error: ").strip()
            failed.append({**row, "error": reason or "The merge did not finish."})
            rep.warn(f"Could not merge {row['merge_name']} into {row['keep_name']}: {failed[-1]['error']}")
            continue
        except Exception as e:   # one pair's failure must not undo or stop the others
            failed.append({**row, "error": str(e) or type(e).__name__})
            rep.warn(f"Could not merge {row['merge_name']} into {row['keep_name']}: {failed[-1]['error']}")
            continue
        joined[m] = k
        merged.append(row)
    rep.log(f"Merged {len(merged)} of {total} pair{'s' if total != 1 else ''}"
            + (f"; {len(failed)} could not be merged" if failed else "")
            + (f"; {len(skipped)} already joined" if skipped else "") + ".")
    if merged:
        rep.log("Each merge is in Review → Merges → Recent merges, where it can be undone. Rebuild "
                "the search index (Activity → Maintenance) to drop the merged entities' old "
                "search entries.")
    return {"merged": merged, "failed": failed, "skipped": skipped}
