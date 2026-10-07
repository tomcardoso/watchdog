"""`research.status` — the web-research download queue (`.watchdog/research/queue.tsv`) and whether
Wayback archiving is set up. Downloading itself runs as a job (`research-fetch`, `fetch`)."""

from __future__ import annotations

from watchdog.gui.rpc import method
from watchdog.gui.vaultio import require_vault


@method("research.status")
def status(vault: str) -> dict:
    from watchdog.cmd.research import _wayback_creds
    from watchdog.pipeline import research

    v = require_vault(vault)
    entries = research.parse_worklist(research.read_queue_text(v) or "")
    queued = [{"url": e["url"], "title": e.get("title") or None,
               "source_type": e.get("source_type") or None,
               "relevance": e.get("relevance") or None} for e in entries]
    return {"queued": queued, "wayback_configured": _wayback_creds() is not None}
