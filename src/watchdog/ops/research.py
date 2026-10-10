"""Research downloads (D298): fetch the sources a research session queued, or a list of links,
into `incoming/` through the egress gate (§14).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

from watchdog.cmd import research as _rs
from watchdog.cmd.base import _CYAN, _RESET, _ensure_layout, _resolve_vault
from watchdog.ops import current, op, say
from watchdog.pipeline import research


@op("research-fetch")
def research_fetch(rep, vault: Path, *, file: str | None = None) -> dict:
    """Download the sources a research session queued (or those listed in `file`)."""
    from watchdog.ops.ingest import _require_vault
    _require_vault(vault)
    _research_fetch(SimpleNamespace(project=None, file=file))
    return {}


@op("fetch-links")
def fetch_links(rep, vault: Path, *, targets: list[str]) -> dict:
    """Download a list of links, or the links in a file, into `incoming/`."""
    from watchdog.ops.ingest import _require_vault
    _require_vault(vault)
    if not targets:
        sys.exit("Error: no links to download.")
    _fetch(SimpleNamespace(project=None, targets=list(targets)))
    return {}


def _research_fetch(args) -> None:
    """Internal: download the queued research sources into incoming/ (manual / recovery path)."""
    _, _info, vault = _resolve_vault(getattr(args, "project", None))
    _ensure_layout(vault)
    source_file = Path(args.file) if getattr(args, "file", None) else None
    if source_file is not None:
        if not source_file.exists() or not research.parse_worklist(source_file.read_text(encoding="utf-8")):
            sys.exit(f"Error: no queued sources at {source_file}")
    elif not _rs._queue_count(vault):
        sys.exit(f"Error: no queued sources at {_rs._queue_path(vault)}")
    _rs._run_download(vault, source_file)
    say()


def _fetch(args) -> None:
    """`watchdog fetch <file | urls…>` — download a batch of URLs into `incoming/`, independent of
    the agentic research flow (#197). Each URL goes through the same egress gate as research sources
    (validate → fetch → sanitize → `.yml` sidecar), and Wayback archiving applies if configured. The
    input is either a links/TSV file (one URL per line) or URLs given directly on the command line."""
    _, _info, vault = _resolve_vault(getattr(args, "project", None))
    _ensure_layout(vault)
    targets = args.targets

    # A single argument that names a file is a links file; anything else is treated as URLs.
    if len(targets) == 1 and not urlsplit(targets[0]).scheme and Path(targets[0]).is_file():
        entries = research.parse_worklist(Path(targets[0]).read_text(encoding="utf-8"))
        if not entries:
            sys.exit(f"Error: no URLs found in {targets[0]}")
    else:
        entries = [{"url": t.strip()} for t in targets if t.strip()]
        if not entries:
            sys.exit("Error: no URLs to fetch")

    wayback = _rs._wayback_creds()
    results = research.deposit_many(vault, entries, wayback=wayback, retrieved_by="fetch",
                                    on_progress=_rs._print_progress)
    count = _rs._report_deposits(results, wayback=wayback, requeued_failures=False)
    if count:
        if current().app:
            say(f"\n  {'They are' if count != 1 else 'It is'} saved in incoming/, ready to add.\n")
        else:
            say(f"\n  Next: {_CYAN}watchdog chew{_RESET} then {_CYAN}watchdog dig{_RESET} "
                  f"to fold {'them' if count != 1 else 'it'} into the vault.\n")
    else:
        say()
