"""`watchdog resolve` / `watchdog unresolve` — acknowledge (or un-acknowledge) leads,
watch-word alerts, and contradiction callouts so the deterministic report generators stop
re-surfacing them (#266).

Run from inside the vault, the same convention `watchdog merge-entities` uses — this edits the
registry-adjacent `resolutions.json` in place, so there's no useful project-name lookup. Three
ways to acknowledge, all landing in the same store:

  * `watchdog resolve <id> …`  — mark one or more resolution ids (printed next to each report item)
  * `watchdog resolve --sync`  — import `- [x]` checkboxes ticked in the briefing files
  * `watchdog resolve --list`  — show what's currently acknowledged

`watchdog unresolve <id> …` removes ids, bringing the items back into the active list."""

import sys
from pathlib import Path

from watchdog.cmd.base import _BOLD, _CYAN, _DIM, _GREEN, _RESET, _YELLOW
from watchdog.pipeline import resolutions


def _vault() -> Path:
    vault = Path(".").resolve()
    if not (vault / ".watchdog").is_dir():
        sys.exit("Error: must be run from inside a Watchdog vault directory")
    return vault


def _print_list(vault: Path) -> None:
    resolved = resolutions.load(vault).get("resolved", {})
    print()
    print(f"  {_BOLD}Resolved items{_RESET} {_DIM}({len(resolved)}){_RESET}")
    print()
    if not resolved:
        print(f"  {_DIM}Nothing acknowledged yet.{_RESET}\n")
        return
    for rid, meta in sorted(resolved.items()):
        at = meta.get("at", "")
        print(f"    {_CYAN}{rid}{_RESET}  {_DIM}{at}{_RESET}")
    print()


def cmd_resolve(args) -> None:
    vault = _vault()

    if args.list:
        _print_list(vault)
        return

    if args.sync:
        added, removed = resolutions.sync_from_briefings(vault)
        print()
        if not added and not removed:
            print(f"  {_DIM}No checkbox changes to sync from briefings/.{_RESET}\n")
            return
        if added:
            print(f"  {_GREEN}Resolved{_RESET} {_BOLD}{len(added)}{_RESET} "
                  f"{_DIM}item{'s' if len(added) != 1 else ''} from ticked checkboxes{_RESET}")
        if removed:
            print(f"  {_YELLOW}Reopened{_RESET} {_BOLD}{len(removed)}{_RESET} "
                  f"{_DIM}item{'s' if len(removed) != 1 else ''} from cleared checkboxes{_RESET}")
        print()
        return

    if not args.ids:
        sys.exit("Error: give one or more resolution ids, or use --sync / --list")

    added = resolutions.resolve(vault, args.ids, label="manual")
    skipped = len(args.ids) - len(added)
    print()
    print(f"  {_GREEN}Resolved{_RESET} {_BOLD}{len(added)}{_RESET} "
          f"{_DIM}item{'s' if len(added) != 1 else ''}"
          f"{f' · {skipped} already resolved' if skipped else ''}{_RESET}")
    print()


def cmd_unresolve(args) -> None:
    vault = _vault()
    removed = resolutions.unresolve(vault, args.ids)
    missing = len(args.ids) - len(removed)
    print()
    print(f"  {_GREEN}Reopened{_RESET} {_BOLD}{len(removed)}{_RESET} "
          f"{_DIM}item{'s' if len(removed) != 1 else ''}"
          f"{f' · {missing} not resolved' if missing else ''}{_RESET}")
    print()
