"""Research downloads (D298): fetch the sources a research session queued, or a list of links,
into `incoming/` through the egress gate (§14): validate the URL, fetch, sanitize, then write the
document and its `.yml` sidecar. Archiving to the Wayback Machine applies when it is set up.
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlsplit

from watchdog import config
from watchdog.ops import op, require_vault
from watchdog.pipeline import capture, research


def wayback_creds() -> tuple[str, str] | None:
    """The archive.org (access key, secret key) when Wayback archiving is on and both keys are
    set; else None (the default, #201)."""
    from watchdog import auth
    if not config.read().get("wayback_save"):
        return None
    state = auth.load_state()
    access = auth.setting_secret("wayback_access_key", state)
    secret = auth.setting_secret("wayback_secret_key", state)
    return (access, secret) if access and secret else None


@op("research-fetch", secrets=True)
def research_fetch(rep, vault: Path, *, file: str | None = None) -> dict:
    """Download the sources a research session queued (or those listed in `file`). Rows that
    fail stay queued, so a transient failure is never lost; an explicit `file` is left as it is."""
    require_vault(vault)
    source_file = Path(file) if file else None
    if source_file is not None:
        text = source_file.read_text(encoding="utf-8") if source_file.exists() else ""
    else:
        text = research.read_queue_text(vault)
    entries = research.parse_worklist(text) if text else []
    if not entries:
        sys.exit("Error: there are no queued sources to download.")
    wayback = wayback_creds()
    results = research.deposit_many(vault, entries, wayback=wayback,
                                    on_progress=lambda i, n, url: _progress(rep, i, n, url))
    if source_file is None:
        failed_urls = {r.url for r in results if not r.path}
        research.retain_pending(vault, [e for e in entries if e["url"] in failed_urls])
    n = _report(rep, results, wayback=wayback, failures_kept=source_file is None)
    return {"downloaded": n, "failed": len(results) - n}


@op("fetch-links", secrets=True)
def fetch_links(rep, vault: Path, *, targets: list[str]) -> dict:
    """Download a list of links, or the links in a file, into `incoming/` (#197)."""
    require_vault(vault)
    # A single target that names a file is a links file; anything else is a list of URLs.
    if len(targets) == 1 and not urlsplit(targets[0]).scheme and Path(targets[0]).is_file():
        entries = research.parse_worklist(Path(targets[0]).read_text(encoding="utf-8"))
    else:
        entries = [{"url": t.strip()} for t in targets if t.strip()]
    if not entries:
        sys.exit("Error: there are no links to download.")
    wayback = wayback_creds()
    results = research.deposit_many(vault, entries, wayback=wayback, retrieved_by="fetch",
                                    on_progress=lambda i, n, url: _progress(rep, i, n, url))
    n = _report(rep, results, wayback=wayback, failures_kept=False)
    if n:
        rep.log(f"{'They are' if n != 1 else 'It is'} saved in incoming/, ready to add.")
    return {"downloaded": n, "failed": len(results) - n}


def _progress(rep, i: int, total: int, url: str) -> None:
    """A line per source as it starts, so a batch isn't silent until it is done."""
    rep.log(f"[{i}/{total}] {url}")
    rep.progress("download", done=i, total=total, url=url)


def _report(rep, results: list, *, wayback, failures_kept: bool) -> int:
    deposited = [r for r in results if r.path]
    failed = [r for r in results if not r.path]
    rep.log(f"Downloaded {len(deposited)} of {len(results)} into incoming/.")
    if wayback and deposited:
        rep.log("Archived each to the Wayback Machine; the snapshot's address is in every "
                "source's sidecar.")
    for r in deposited:
        rep.log(f"  {r.path.name}  {r.url}")
    if any(r.path.suffix in (".html", ".xhtml") for r in deposited) and not capture.render_available():
        rep.log("These pages were saved as text. Full page snapshots (images, styles, "
                "client-rendered pages) need the capture browser, which is not installed.")
    if failed:
        rep.log(f"Skipped {len(failed)}" + (" (they stay queued; retry from Web research)"
                                             if failures_kept else "") + ":")
        for r in failed:
            rep.log(f"  {r.url}  {r.error}")
    return len(deposited)
