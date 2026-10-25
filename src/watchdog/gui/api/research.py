"""`research.status` — the web-research download queue (`.watchdog/research/queue.tsv`) and whether
Wayback archiving is set up. Downloading itself runs as a job (`research-fetch`, `fetch`)."""

from __future__ import annotations

from watchdog.gui.rpc import method
from watchdog.gui.vaultio import require_vault


@method("research.status")
def status(vault: str) -> dict:
    from watchdog.ops.research import wayback_creds as _wayback_creds
    from watchdog.pipeline import research

    v = require_vault(vault)
    entries = research.parse_worklist(research.read_queue_text(v) or "")
    failures = research.read_failures(v)
    queued = [{"url": e["url"], "title": e.get("title") or None,
               "source_type": e.get("source_type") or None,
               "relevance": e.get("relevance") or None,
               # Set once a download of it failed (D346): why, and how many times it was tried.
               "failed": ({"error": f.get("error") or "", "attempts": f.get("attempts") or 1}
                          if isinstance(f := failures.get(e["url"]), dict) else None)}
              for e in entries]
    return {"queued": queued, "wayback_configured": _wayback_creds() is not None}


@method("research.dismiss")
def dismiss(vault: str, urls: list[str]) -> dict:
    """Take sources off the download queue: ones the reporter gave up on after they failed."""
    from watchdog.pipeline import research
    if not isinstance(urls, list) or not all(isinstance(u, str) for u in urls):
        from watchdog.gui.rpc import RpcError
        raise RpcError("Name the sources to remove.", code="bad_params")
    return {"removed": research.dismiss(require_vault(vault), urls)}
