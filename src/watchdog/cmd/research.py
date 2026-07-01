"""`watchdog research` — open Claude Code on the web-research skill, then deterministically
download the sources it queued (#186).

The `/watchdog-research` skill curates URLs into a links file; this command launches that
session and, when it ends, downloads the queued sources into `_INCOMING/` through the
deterministic egress gate in `pipeline.research` (validate URL → fetch → sanitize → write +
`.yml` sidecar). All outbound *archival* fetching thus happens here, in the user's terminal —
never granted to the interactive skill, whose only web access is WebSearch/WebFetch for its own
reading. The internal `research-fetch` command runs the same download on demand (recovery)."""

import json
import subprocess
import sys
from pathlib import Path

from watchdog.cmd.base import (
    _BOLD,
    _CYAN,
    _DIM,
    _GREEN,
    _MODEL_IDS,
    _RESET,
    _YELLOW,
    CONFIG_FILE,
    _find_project,
    _resolve_vault,
    load_projects,
)
from watchdog.pipeline import research


def _wayback_creds() -> tuple[str, str] | None:
    """Return the archive.org (access_key, secret_key) when Wayback archiving is enabled and both
    keys are configured; else None (the off-by-default case, #201)."""
    if not CONFIG_FILE.exists():
        return None
    try:
        config = json.loads(CONFIG_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not config.get("wayback_save"):
        return None
    access = (config.get("wayback_access_key") or "").strip()
    secret = (config.get("wayback_secret_key") or "").strip()
    return (access, secret) if access and secret else None


def _queue_path(vault: Path) -> Path:
    return research.queue_path(vault)


def _queue_count(vault: Path) -> int:
    return research.pending_count(vault)


def _run_download(vault: Path, source_file: Path | None = None) -> int:
    """Download every queued source into `_INCOMING/`, continuing past failures. Returns the number
    deposited. With no `source_file`, consumes the durable worklist — downloaded rows drop out;
    rows that failed stay queued for a later retry so a transient failure is never silently lost.
    An explicit `source_file` (recovery) is read but left untouched."""
    text = source_file.read_text(encoding="utf-8") if source_file else research.read_queue_text(vault)
    entries = research.parse_worklist(text) if text else []
    wayback = _wayback_creds()
    results = research.deposit_many(vault, entries, wayback=wayback)
    deposited = [r for r in results if r.path]
    failed = [r for r in results if not r.path]
    if source_file is None:
        failed_urls = {r.url for r in failed}
        research.retain_pending(vault, [e for e in entries if e["url"] in failed_urls])
    print()
    print(f"  {_GREEN}Downloaded{_RESET} {_BOLD}{len(deposited)}{_RESET} of {len(results)} "
          f"into {_CYAN}_INCOMING/{_RESET}")
    if wayback and deposited:
        print(f"  {_DIM}Archived each to the Wayback Machine — snapshot URL in every source's sidecar.{_RESET}")
    for r in deposited:
        print(f"    {_CYAN}{r.path.name}{_RESET}  {_DIM}{r.url}{_RESET}")
    if failed:
        note = ("" if source_file is not None else
                f" {_DIM}(left queued — retry with {_RESET}{_CYAN}watchdog research-fetch{_RESET}{_DIM}){_RESET}")
        print(f"\n  {_YELLOW}Skipped {len(failed)}{_RESET}{note}")
        for r in failed:
            print(f"    {_DIM}{r.url}{_RESET}  {_YELLOW}{r.error}{_RESET}")
    return len(deposited)


def _confirm(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() in ("", "y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def cmd_research(args) -> None:
    """Explain the mode, open Claude Code on /watchdog-research, then download what it queued."""
    vault = Path(".").resolve()
    if not (vault / ".watchdog").is_dir():
        if getattr(args, "name", None):
            _, info = _find_project(args.name)
            vault = Path(info["path"])
        else:
            sys.exit("Error: not inside a Watchdog project. cd into your investigation first, "
                     "or pass the investigation name.")
    model = getattr(args, "model", None) or "sonnet"
    if model not in _MODEL_IDS:
        sys.exit(f"Error: unknown model '{model}' — choose sonnet, opus, or haiku")

    info = next((v for v in load_projects().values()
                 if Path(v["path"]).resolve() == vault.resolve()), None)
    name = info["name"] if info else vault.name

    # Recover an unfetched queue left by a previous (e.g. interrupted) session before starting fresh.
    # Declining leaves it queued (a later download or the pending warnings will surface it) — never
    # silently discarded.
    stale = _queue_count(vault)
    if stale:
        s = "s" if stale != 1 else ""
        them = "them" if stale != 1 else "it"
        print(f"\n  {_YELLOW}{stale} source{s} from a previous research session "
              f"{'are' if stale != 1 else 'is'} queued and not downloaded.{_RESET}")
        if _confirm(f"  Download {them} into _INCOMING/ now? [Y/n] "):
            _run_download(vault)

    print(f"\n  {_BOLD}Web research — {name}{_RESET}\n")
    print(f"  {_DIM}Seeded by your vault, Claude conducts bounded web research and queues the{_RESET}")
    print(f"  {_DIM}sources it finds. When the session ends, watchdog downloads them into{_RESET}")
    print(f"  {_RESET}{_CYAN}_INCOMING/{_RESET}{_DIM} — so findings flow through the normal chew → ingest pipeline.{_RESET}")
    print(f"  {_DIM}Claude never writes vault notes directly. In the session it will:{_RESET}\n")
    print(f"    {_DIM}1.{_RESET} propose a research mission from the vault's open gaps and leads")
    print(f"    {_DIM}2.{_RESET} confirm the question and how wide to cast the net (quick / standard / deep)")
    print(f"    {_DIM}3.{_RESET} research in rounds, checking in with you between each")
    print(f"    {_DIM}4.{_RESET} queue each source it keeps, with a reliability tag and why it matters")
    print(f"    {_DIM}5.{_RESET} write a research memo to {_CYAN}briefings/{_RESET}\n")

    question = (getattr(args, "question", None) or "").strip()
    if not question:
        try:
            question = input(f"  Research question {_DIM}(Enter to let Claude propose one){_RESET}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            print(f"\n  When ready, open Claude Code and run:  {_CYAN}/watchdog-research{_RESET}\n")
            return

    # Launch interactively via subprocess (not execvp) so control returns for the post-flight
    # download. claude takes over the terminal and we resume when it exits — Claude Code does not
    # self-exit when the skill finishes, so flag that the download waits on the user leaving.
    print(f"  {_DIM}When you're done, exit Claude Code (Ctrl-D) — watchdog will then offer to "
          f"download the sources it queued.{_RESET}\n")
    prompt = f"/watchdog-research {question}".strip()
    try:
        subprocess.run(["claude", "--model", _MODEL_IDS.get(model, model), prompt], cwd=str(vault))
    except FileNotFoundError:
        sys.exit("Error: Claude Code not found — install from https://claude.ai/download")

    # Post-flight: download what the session queued.
    found = _queue_count(vault)
    if not found:
        print(f"\n  {_DIM}No sources were queued this session — nothing to download.{_RESET}\n")
        return
    s = "s" if found != 1 else ""
    them = "them" if found != 1 else "it"
    print(f"\n  {_BOLD}{found}{_RESET} source{s} queued for download.")
    if _confirm(f"  Download {them} into _INCOMING/ now? [Y/n] "):
        count = _run_download(vault)
        if count:
            print(f"\n  Next: {_CYAN}watchdog chew{_RESET} then {_CYAN}watchdog ingest{_RESET} "
                  f"to fold {'them' if count != 1 else 'it'} into the vault.\n")
    else:
        print(f"\n  Left queued. Run {_CYAN}watchdog research-fetch{_RESET} to download later.\n")


def cmd_research_seen(args) -> None:
    """Internal: print URLs already captured (one per line), so /watchdog-research can skip
    re-fetching them. Derived from documents.json + in-flight _INCOMING/ sidecars (research.seen_urls)."""
    _, _info, vault = _resolve_vault(getattr(args, "project", None))
    for url in sorted(research.seen_urls(vault)):
        print(url)


def cmd_research_fetch(args) -> None:
    """Internal: download the queued research sources into _INCOMING/ (manual / recovery path)."""
    _, _info, vault = _resolve_vault(getattr(args, "project", None))
    source_file = Path(args.file) if getattr(args, "file", None) else None
    if source_file is not None:
        if not source_file.exists() or not research.parse_worklist(source_file.read_text(encoding="utf-8")):
            sys.exit(f"Error: no queued sources at {source_file}")
    elif not _queue_count(vault):
        sys.exit(f"Error: no queued sources at {_queue_path(vault)}")
    _run_download(vault, source_file)
    print()
