"""Bare `watchdog` inside a vault: one screen of what's going on and what to do next (D251)."""

import re
import sys
from pathlib import Path

from watchdog import interactive
from watchdog.cmd.base import (
    _BOLD, _CYAN, _DIM, _RESET, _YELLOW,
    _count_awaiting_bark, _count_awaiting_dig, _count_incoming, load_projects,
)
from watchdog.links import note_link

# Briefing files that aren't ingest briefings.
_NOT_INGEST_BRIEFINGS = ("leads-", "alerts-", "research-")
_BRIEFING_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{2}-\d{2}(-\d+)?\.md$")
_WID = re.compile(r"<!--wid:(alert:[^>]+?)-->")


def _latest_briefing(vault: Path) -> Path | None:
    d = vault / "briefings"
    if not d.is_dir():
        return None
    found = [p for p in d.glob("*.md") if _BRIEFING_NAME.match(p.name)]
    return max(found, key=lambda p: p.name) if found else None


def _headline(vault: Path) -> str | None:
    """The first line of `hot.md`'s investigation status, which the latest briefing wrote."""
    hot = vault / "hot.md"
    if not hot.exists():
        return None
    text = hot.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"^## Investigation status\s*\n+(.+)$", text, flags=re.M)
    return m.group(1).strip() if m and m.group(1).strip() else None


def _open_alerts(vault: Path, resolved: frozenset[str]) -> int:
    ids: set[str] = set()
    d = vault / "briefings"
    if d.is_dir():
        for p in d.glob("alerts-*.md"):
            ids.update(_WID.findall(p.read_text(encoding="utf-8", errors="replace")))
    return len(ids - resolved)


def _failed(vault: Path) -> int:
    d = vault / ".watchdog" / "queue" / "_failed"
    return sum(1 for f in d.glob("*.json")) if d.is_dir() else 0


def summary(vault: Path) -> dict:
    """Everything the home screen shows, read from local files only."""
    from watchdog.cmd.review import count_open_duplicates
    from watchdog.pipeline import leads, orchestrate, research, resolutions
    from watchdog.pipeline.preprocess_batch import find_files

    resolved = resolutions.resolved_ids(vault)
    found = leads.scan(vault)
    context_dir = vault / "_CONTEXT"
    return {
        "vault": vault,
        "briefing": _latest_briefing(vault),
        "headline": _headline(vault),
        "contradictions": sum(c["count"] for c in found["contradictions"]),
        "leads": len(found["unprofiled"]) + len(found["isolated"]) + len(found["inferred"]),
        "near_duplicates": count_open_duplicates(vault),
        "alerts": _open_alerts(vault, resolved),
        "incoming": _count_incoming(vault),
        "awaiting_dig": _count_awaiting_dig(vault),
        "awaiting_bark": _count_awaiting_bark(vault),
        "pending_finalize": orchestrate.has_pending_finalization(vault),
        "failed": _failed(vault),
        "research_urls": research.pending_count(vault),
        "context_unseeded": bool(context_dir.is_dir() and find_files([context_dir])
                                 and not (vault / "context.md").exists()),
    }


def has_work(s: dict) -> bool:
    """Whether `watchdog add` has anything to do."""
    return bool(s["incoming"] or s["awaiting_dig"] or s["awaiting_bark"] or s["pending_finalize"])


def _row(n: int, label: str, command: str) -> str:
    """One count row. `label` is "singular|plural" when the two differ."""
    one, _, many = label.partition("|")
    label = one if n == 1 or not many else many
    left = f"{n:>4}  {label}"
    return f"  {_BOLD}{n:>4}{_RESET}  {label}{' ' * max(2, 38 - len(left))}{_CYAN}{command}{_RESET}"


def render(name: str, s: dict) -> str:
    lines = [f"\n  {_BOLD}{name}{_RESET}"]
    if s["briefing"]:
        lines.append(f"\n  {_BOLD}Latest briefing{_RESET}  {_DIM}{s['briefing'].stem}{_RESET}")
        if s["headline"]:
            lines.append(f"    {s['headline']}")
        rel = f"briefings/{s['briefing'].name}"
        lines.append(f"    {_CYAN}{note_link(s['vault'], rel)}{_RESET}")
    else:
        lines.append(f"\n  {_DIM}No briefing yet — add documents to get the first one.{_RESET}")

    waiting = [(s["contradictions"], "contradiction|contradictions",
                "watchdog review contradictions"),
               (s["leads"], "open lead|open leads", "watchdog review leads"),
               (s["near_duplicates"], "possible duplicate document|possible duplicate documents",
                "watchdog review duplicates"),
               (s["alerts"], "watch-list hit|watch-list hits", "watchdog review alerts")]
    waiting = [w for w in waiting if w[0]]
    if waiting:
        lines.append(f"\n  {_BOLD}Waiting on you{_RESET}")
        lines.extend(_row(*w) for w in waiting)

    progress = [(s["incoming"], "file in _INCOMING/|files in _INCOMING/", "watchdog add"),
                (s["awaiting_dig"] + s["awaiting_bark"],
                 "document not yet finished|documents not yet finished", "watchdog add"),
                (s["failed"], "document that failed|documents that failed", "watchdog add --retry"),
                (s["research_urls"], "research link not downloaded|research links not downloaded",
                 "watchdog research-fetch")]
    progress = [p for p in progress if p[0]]
    if s["pending_finalize"] and not (s["awaiting_dig"] or s["awaiting_bark"]):
        progress.append((1, "batch waiting to be finished", "watchdog add"))
    if s["context_unseeded"]:
        progress.append((1, "background folder not yet read", "watchdog context"))
    if progress:
        lines.append(f"\n  {_BOLD}In progress{_RESET}")
        lines.extend(_row(*p) for p in progress)

    lines.append(f"\n  {_BOLD}Explore{_RESET}")
    lines.append(f"    {_CYAN}watchdog ask \"…\"{_RESET}{_DIM}  ·  {_RESET}"
                 f"{_CYAN}watchdog search \"…\"{_RESET}{_DIM}  ·  {_RESET}{_CYAN}watchdog obsidian{_RESET}\n")
    return "\n".join(lines)


def cmd_home(args) -> dict | None:
    vault = Path(".").resolve()
    info = next((v for v in load_projects().values() if Path(v["path"]).resolve() == vault), None)
    name = (info or {}).get("name") or vault.name
    s = summary(vault)
    print(render(name, s))
    if has_work(s) and sys.stdin.isatty() and interactive.confirm(
            f"  {_YELLOW}Documents are waiting.{_RESET} Add them now?", default=True):
        from watchdog.cmd.ingest import cmd_add
        return cmd_add(args)
    return None
