"""What an investigation's Overview shows: the latest briefing and its status line, what is
waiting on the reporter, and what is still in progress. Read from local files only."""

from __future__ import annotations

import json
import re
from pathlib import Path

from watchdog.vault import count_awaiting_post, count_awaiting_processing, count_failed, count_incoming
from watchdog.vault_paths import context_dir

_BRIEFING_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{2}-\d{2}(-\d+)?\.md$")
_WID = re.compile(r"<!--wid:(alert:[^>]+?)-->")


def _briefing_order(p: Path) -> tuple[str, int]:
    """Sort key for a run briefing: its minute, then the `-2`, `-3` a second briefing in the same
    minute takes (which a plain name sort puts before the first)."""
    m = _BRIEFING_NAME.match(p.name)
    return (p.name[:16], int(m.group(1)[1:]) if m and m.group(1) else 1)


def run_briefings(vault: Path) -> list[Path]:
    """The run briefings in `briefings/`, newest first."""
    d = vault / "briefings"
    if not d.is_dir():
        return []
    return sorted((p for p in d.glob("*.md") if _BRIEFING_NAME.match(p.name)),
                  key=_briefing_order, reverse=True)


def briefing_status(vault: Path, briefing: Path) -> str | None:
    """The one-line investigation status the briefing's run wrote, from its JSON sidecar
    (`.watchdog/briefings/<name>.json`, D285). Markdown, with any fact citations as links."""
    side = vault / ".watchdog" / "briefings" / f"{briefing.stem}.json"
    try:
        data = json.loads(side.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    status = data.get("status") if isinstance(data, dict) else None
    return status.strip() if isinstance(status, str) and status.strip() else None


def _open_alerts(vault: Path, resolved: frozenset[str]) -> int:
    ids: set[str] = set()
    d = vault / "briefings"
    if d.is_dir():
        for p in d.glob("alerts-*.md"):
            ids.update(_WID.findall(p.read_text(encoding="utf-8", errors="replace")))
    return len(ids - resolved)


def summary(vault: Path) -> dict:
    from watchdog.pipeline import leads, orchestrate, research, resolutions
    from watchdog.pipeline import requests as _requests
    from watchdog.pipeline.preprocess_batch import find_files
    from watchdog.review import count_open_duplicates, count_open_same_entities

    resolved = resolutions.resolved_ids(vault)
    found = leads.scan(vault)
    ctx_dir = context_dir(vault)
    briefings = run_briefings(vault)
    latest = briefings[0] if briefings else None
    return {
        "vault": vault,
        "briefing": latest,
        "headline": briefing_status(vault, latest) if latest is not None else None,
        "contradictions": sum(c["count"] for c in found["contradictions"]),
        "leads": len(found["unprofiled"]) + len(found["isolated"]) + len(found["inferred"]),
        "near_duplicates": count_open_duplicates(vault),
        "possible_same": count_open_same_entities(vault, resolved),
        "alerts": _open_alerts(vault, resolved),
        "requests": len(_requests.open_requests(vault)),
        "incoming": count_incoming(vault),
        "awaiting_dig": count_awaiting_processing(vault),
        "awaiting_bark": count_awaiting_post(vault),
        "pending_finalize": orchestrate.has_pending_finalization(vault),
        "failed": count_failed(vault),
        "research_urls": research.pending_count(vault),
        "context_unseeded": bool(ctx_dir.is_dir() and find_files([ctx_dir])
                                 and not (vault / "context.md").exists()),
    }


def has_work(s: dict) -> bool:
    """Whether adding documents has anything to do."""
    return bool(s["incoming"] or s["awaiting_dig"] or s["awaiting_bark"] or s["pending_finalize"])
