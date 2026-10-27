"""Setup operations (D298): download an on-demand model or add-on ahead of time (D273, D357)."""

from __future__ import annotations

import sys

from watchdog.ops import op


@op("download-model", vault=False)
def download_model(rep, *, model: str) -> dict:
    """Download one on-demand piece now rather than when it is first needed: the transcription model
    (progress is the `model` event, as in pre-processing) or the capture browser (D357)."""
    from watchdog.gui import engine_setup
    if model not in dict(engine_setup.ON_DEMAND):
        sys.exit(f"Error: “{model}” isn't a model that downloads on demand.")
    results = engine_setup.download_models([model])
    if results.get(model) != "ok":
        why = engine_setup.ERRORS.get(model)
        sys.exit("Error: the download did not finish. Check the connection and try again."
                 + (f"\n{why}" if why else ""))
    return {"model": model}
