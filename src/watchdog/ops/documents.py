"""Document operations (D298): set a document's details, the fields of its `.yml` sidecar, from
the app (D381).
"""

from __future__ import annotations

import sys
from pathlib import Path

from watchdog.ops import op, require_vault


@op("set-details", kind="action")
def set_details(rep, vault: Path, *, values: dict[str, str], sha: str | None = None,
                path: str | None = None) -> dict:
    """Set the details named in `values` ('' removes one) on a document Watchdog has read, by
    `sha`, or on a file waiting to be added, by its `path` in the investigation. Never calls a
    model: a new record skill on a document already extracted is acted on by Retry extraction,
    which the app offers with its estimate after this."""
    from watchdog.pipeline import details
    require_vault(vault)
    if bool(sha) == bool(path):
        sys.exit("Error: name either the document or the waiting file.")
    try:
        out = (details.set_document(vault, sha, values) if sha
               else details.set_waiting(vault, path, values))
    except details.DetailsError as e:
        sys.exit(f"Error: {e}")
    except OSError as e:
        sys.exit(f"Error: the details could not be written ({e}). If documents are being added "
                 "to this investigation, wait for that to finish and try again.")
    n = len(out["changed"])
    rep.log(f"Details of {out['filename']}: " + (f"{n} field{'s' if n != 1 else ''} changed."
                                                  if n else "nothing changed."))
    return out
