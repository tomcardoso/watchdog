"""`watchdog merge-entities <keep-id> <merge-id>` — deterministic registry surgery
that folds a duplicate entity into another (#219).

Three shipped features could only *detect* the same real-world entity living under
two ids — the dashboard's "Possible duplicates" view, the `/watchdog-health`
near-duplicate check, and D39's Neo4j-export tradeoff note — this command is the fix.
No model calls: it must be run from inside the vault it mutates, the same
"run from inside the vault" convention `watchdog is-duplicate` / `watchdog
post-flight` already use, since there's no useful project-name lookup for a command
that edits the registry in place."""

import json
import sys
from pathlib import Path

from watchdog.cmd.base import _BOLD, _CYAN, _DIM, _GREEN, _YELLOW, _RESET
from watchdog.pipeline import merge_entities as _merge_entities


def _entity_preview(eid: str, entry: dict) -> str:
    n_docs = len(entry.get("appears_in", []))
    n_roles = len(entry.get("roles", []))
    return (
        f"  {_BOLD}{entry['name']}{_RESET}  {_DIM}({entry.get('type', '?')}, {eid}){_RESET}\n"
        f"    {_DIM}{n_docs} document{'s' if n_docs != 1 else ''} · "
        f"{n_roles} relationship{'s' if n_roles != 1 else ''}{_RESET}"
    )


def cmd_merge_entities(args) -> None:
    vault = Path(".").resolve()
    if not (vault / ".watchdog").is_dir():
        sys.exit("Error: must be run from inside a Watchdog vault directory")

    entities_path = vault / ".watchdog" / "Registry" / "entities.json"
    try:
        entities_reg = json.loads(entities_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        sys.exit("Error: entities.json not found or unreadable — is this a Watchdog vault?")

    keep_id, merge_id = args.keep_id, args.merge_id
    if keep_id not in entities_reg:
        sys.exit(f"Error: entity '{keep_id}' not found in entities.json")
    if merge_id not in entities_reg:
        sys.exit(f"Error: entity '{merge_id}' not found in entities.json")
    keep_entry, merge_entry = entities_reg[keep_id], entities_reg[merge_id]

    # Show both entities before doing anything irreversible — the surviving one first,
    # then the one about to be folded away and disappear under its own id.
    print()
    print(f"  {_BOLD}Keep{_RESET}")
    print(_entity_preview(keep_id, keep_entry))
    print(f"  {_YELLOW}Merge away{_RESET}")
    print(_entity_preview(merge_id, merge_entry))
    if keep_entry.get("type") != merge_entry.get("type"):
        print(f"\n  {_YELLOW}Warning:{_RESET} different entity types "
              f"({merge_entry.get('type', '?')} vs {keep_entry.get('type', '?')}) — "
              f"make sure this is really the same entity.")

    if not getattr(args, "force", False):
        try:
            answer = input(f"\n  Merge {_BOLD}{merge_entry['name']}{_RESET} into "
                           f"{_BOLD}{keep_entry['name']}{_RESET}? This cannot be undone. "
                           f"[y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)
        if answer not in ("y", "yes"):
            print(f"  {_DIM}Cancelled — nothing changed.{_RESET}\n")
            return

    try:
        result = _merge_entities.run(vault, keep_id, merge_id)
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
    if result.get("summary_dropped"):
        print(
            f"  {_YELLOW}Summary now reflects only the kept entity — run{_RESET} "
            f"{_CYAN}/watchdog-entity {args.keep_id}{_RESET} "
            f"{_YELLOW}in a Claude Code session to re-synthesize it from all sources.{_RESET}"
        )
    if result["backup_dir"]:
        rel = result["backup_dir"].relative_to(vault)
        print(f"  {_DIM}backup: {_CYAN}{rel}{_RESET}{_DIM} — copy files back to undo{_RESET}")
    print()
