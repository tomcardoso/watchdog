"""`watchdog merge-entities <keep-id> <merge-id>` — deterministic registry surgery
that folds a duplicate entity into another (#219).

Three shipped features could only *detect* the same real-world entity living under
two ids — the dashboard's "Possible duplicates" view, the `/watchdog-health`
near-duplicate check, and D39's Neo4j-export tradeoff note — this command is the fix.
No model calls: it must be run from inside the vault it mutates, the same
"run from inside the vault" convention `watchdog is-duplicate` / `watchdog
post-flight` already use, since there's no useful project-name lookup for a command
that edits the registry in place."""

import sys
from pathlib import Path

from watchdog.cmd.base import _BOLD, _CYAN, _DIM, _GREEN, _YELLOW, _RESET
from watchdog.pipeline import merge_entities as _merge_entities


def cmd_merge_entities(args) -> None:
    vault = Path(".").resolve()
    if not (vault / ".watchdog").is_dir():
        sys.exit("Error: must be run from inside a Watchdog vault directory")

    try:
        result = _merge_entities.run(vault, args.keep_id, args.merge_id)
    except ValueError as e:
        sys.exit(f"Error: {e}")

    print()
    print(
        f"  {_GREEN}Merged:{_RESET}  {_BOLD}{result['merge_name']}{_RESET}  "
        f"{_DIM}({args.merge_id}){_RESET}  →  {_BOLD}{result['keep_name']}{_RESET}  "
        f"{_DIM}({args.keep_id}){_RESET}"
    )
    print(
        f"  {_DIM}{result['aliases']} aliases · {result['appears_in']} documents · "
        f"{result['roles']} relationships · {result['timeline_events']} timeline events{_RESET}"
    )
    if result["remapped_roles"]:
        n = result["remapped_roles"]
        print(
            f"  {_DIM}{n} relationship{'s' if n != 1 else ''} elsewhere in the vault "
            f"remapped to {args.keep_id}{_RESET}"
        )
    print(f"  {_CYAN}{result['keep_note_path']}.md{_RESET}")
    print(
        f"  {_YELLOW}Run{_RESET} {_CYAN}watchdog reindex{_RESET} "
        f"{_YELLOW}to drop the merged entity's stale search-index entries.{_RESET}"
    )
    print()
