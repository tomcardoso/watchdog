"""`watchdog verify-fact` (D271) and `watchdog locate-passages` (D270): the reporter's
verification ledger and the source passage behind each fact, both model-free.

Run from inside the vault, like `watchdog resolve`:

  * `watchdog verify-fact <fact-id> --status verified|disputed|cant-verify [--note TEXT]`
  * `watchdog verify-fact <fact-id> --status clear` removes the mark (its history is kept)
  * `watchdog verify-fact --list` shows progress and every marked fact
  * `watchdog locate-passages [--force]` finds passages for documents committed before passages
    existed, from the morgue's page text

The app writes marks in-process through `verification.mark`, the function this command calls (I10).
"""

import sys
from pathlib import Path

from watchdog.cmd.base import _BOLD, _CYAN, _DIM, _GREEN, _RESET, _YELLOW
from watchdog.vault_paths import is_vault


def _vault() -> Path:
    vault = Path(".").resolve()
    if not is_vault(vault):
        sys.exit("Error: must be run from inside a Watchdog vault directory")
    return vault


def _print_list(vault: Path) -> None:
    from watchdog.pipeline import verification

    facts = verification.all_facts(vault)
    s = verification.summary(vault, facts)
    rows = verification.entries(vault, facts)
    print()
    print(f"  {_BOLD}{s['verified']}{_RESET} of {_BOLD}{s['facts']}{_RESET} facts verified  "
          f"{_DIM}{s['disputed']} disputed · {s['unverifiable']} can't verify · "
          f"{s['unmarked']} not yet checked{_RESET}")
    if not rows:
        print(f"\n  {_DIM}No facts have been marked yet.{_RESET}\n")
        return
    print()
    for r in rows:
        label = verification.LABELS[r["status"]]
        where = f"{r['filename']}" + (f" p. {r['page']}" if r["page"] else "")
        stale = f"  {_YELLOW}no longer matches a current fact{_RESET}" if r["orphaned"] else ""
        print(f"    {_BOLD}{label}{_RESET}  {_DIM}{where} · {r['by']} · {(r['at'] or '')[:16]}{_RESET}{stale}")
        print(f"      {r['fact']}")
        if r["note"]:
            print(f"      {_DIM}{r['note']}{_RESET}")
        print(f"      {_CYAN}{r['id']}{_RESET}")
    print()


def cmd_verify_fact(args) -> None:
    from watchdog.pipeline import verification

    vault = _vault()
    if args.list:
        _print_list(vault)
        return
    if not args.id:
        sys.exit("Error: give a fact id and --status, or use --list")
    if args.status is None:
        sys.exit("Error: --status is required (verified, disputed, cant-verify or clear)")
    try:
        status = verification.parse_status(args.status)
        entry = verification.mark(vault, args.id, status, note=args.note, by=args.by)
    except (LookupError, ValueError) as e:
        sys.exit(f"Error: {e.args[0] if e.args else e}")
    print()
    if status is None:
        print(f"  {_GREEN}Cleared{_RESET} the mark on {_BOLD}{args.id}{_RESET}")
    else:
        print(f"  {_GREEN}Marked{_RESET} {_BOLD}{verification.LABELS[status]}{_RESET} "
              f"{_DIM}by {entry['by']} · {entry['at']}{_RESET}")
        print(f"  {entry.get('fact') or ''}")
    print(f"  {_DIM}Listed in{_RESET} {_CYAN}{verification.NOTE_FILE}{_RESET}")
    print()


def cmd_locate_passages(args) -> None:
    from watchdog.pipeline import passages

    vault = _vault()
    result = passages.backfill(vault, force=args.force)
    c = result["counts"]
    print()
    print(f"  {_GREEN}Passages{_RESET} for {_BOLD}{result['updated']}{_RESET} "
          f"{_DIM}of {result['documents']} documents updated{_RESET}")
    print(f"  {_DIM}{c['quote']} from quotes · {c['matched']} matched on the page · "
          f"{c['unlocated']} with no matching passage{_RESET}")
    for s in result["skipped"]:
        print(f"  {_YELLOW}Skipped{_RESET} {s['filename']}  {_DIM}{s['reason']}{_RESET}")
    print()
