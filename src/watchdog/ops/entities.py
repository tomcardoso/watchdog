"""Entity operations beside the merge itself (D298): undo a merge, record a contradiction the
reporter found, and re-check stored facts for contradictions with the finalizer model (D287).
"""

from __future__ import annotations

import sys
from pathlib import Path

from watchdog.ops import op
from watchdog.ops.ingest import _require_vault


@op("undo-merge", engine="index")
def undo_merge(rep, vault: Path, *, id: str) -> dict:
    """Split one merged entity back out (D281). Refused, with the reason, when the merge can no
    longer be split correctly."""
    from watchdog.pipeline import merge_undo
    _require_vault(vault)
    try:
        out = merge_undo.undo(vault, id)
    except merge_undo.UndoRefused as e:
        sys.exit(f"Error: {e}")
    rep.log(f"Split {out['split_name']} back out of {out['keep_name']}: {out['documents']} "
            f"document{'s' if out['documents'] != 1 else ''}, {out['facts']} "
            f"fact{'s' if out['facts'] != 1 else ''}. Contradictions recorded on the joined record, "
            f"your notes and the summary stay on {out['keep_name']}.")
    return {k: v for k, v in out.items() if isinstance(v, (str, int, float, bool, type(None)))}


@op("add-contradiction", kind="action")
def add_contradiction(rep, vault: Path, *, entity: str, label: str, a: str, a_doc: str, b: str,
                      b_doc: str, a_page: int | None = None, b_page: int | None = None) -> dict:
    """Record a contradiction the reporter verified, as a callout on the entity, in the format
    processing writes, so Review handles it like any other."""
    from watchdog.pipeline import contradiction
    _require_vault(vault)
    try:
        result = contradiction.run(vault, entity, label, a, a_doc, a_page, b, b_doc, b_page)
    except OSError as e:
        sys.exit(f"Error: the investigation's records could not be written ({e}). If documents are "
                 "being added, wait for that to finish and try again.")
    except ValueError as e:
        sys.exit(f"Error: {e}")
    if result["added"]:
        rep.log(f"Recorded the contradiction on {result['entity_name']}.")
    else:
        rep.log(f"This contradiction is already recorded on {result['entity_name']}; nothing changed.")
    return {"added": bool(result["added"]), "entity_name": result["entity_name"], "rid": result["rid"]}


@op("recheck-contradictions", engine="index", secrets=True, exit_code=lambda r: 1 if (r or {}).get("error") else 0)
def recheck_contradictions(rep, vault: Path, *, ids: list[str] = (), all: bool = False) -> dict:
    """Compare the stored facts of `ids` (or of every recurring entity, with `all`) for
    contradictions with the finalizer model, and file what it finds for Review (D287)."""
    from watchdog.pipeline import recheck
    _require_vault(vault)
    if not all and not ids:
        sys.exit("Error: name the entities to re-check, or ask for the whole investigation.")
    try:
        out = recheck.run(vault, None if all else list(ids), say=rep.log, warn=rep.warn)
    except recheck.Busy as e:
        sys.exit(f"Error: {e}")
    rep.log(recheck.summary(out))
    return out
