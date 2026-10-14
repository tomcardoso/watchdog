"""Setup operations (D298): download an on-demand local model ahead of time (D273)."""

from __future__ import annotations

import sys

from watchdog.ops import op


@op("download-model", vault=False)
def download_model(rep, *, model: str) -> dict:
    """Download one on-demand model (only the transcription model today) now rather than when the
    first recording needs it. Its progress is the `model` event, as in pre-processing."""
    from watchdog.gui import engine_setup
    if model not in dict(engine_setup.ON_DEMAND):
        sys.exit(f"Error: “{model}” isn't a model that downloads on demand.")
    results = engine_setup.download_models([model])
    if results.get(model) != "ok":
        sys.exit("Error: the download did not finish. Check the connection and try again.")
    return {"model": model}
