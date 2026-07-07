"""`watchdog contradiction-add <entity-id>` — promote a verified surface-found contradiction
candidate into an entity note, deterministically (#312).

`/watchdog-surface` reports cross-document contradictions as labelled candidates rather than
writing callouts into pipeline-owned notes (D81). Once the journalist verifies a candidate
against the sources, this is the sanctioned way to get it into the note: it writes the callout
through the pipeline's own note builder, in the exact format extraction emits, and the
resolutions layer (`watchdog resolve` / `unresolve`) then works on it like any pipeline-emitted
callout. Run from inside the vault, the same convention `watchdog merge-entities` uses."""

import sys
from pathlib import Path

from watchdog.cmd.base import _BOLD, _CYAN, _DIM, _GREEN, _RESET
from watchdog.pipeline import contradiction as _contradiction


def cmd_contradiction_add(args) -> None:
    vault = Path(".").resolve()
    if not (vault / ".watchdog").is_dir():
        sys.exit("Error: must be run from inside a Watchdog vault directory")

    try:
        result = _contradiction.run(
            vault, args.entity_id, args.label,
            args.a, args.a_doc, args.a_page,
            args.b, args.b_doc, args.b_page,
        )
    except ValueError as e:
        sys.exit(f"Error: {e}")

    print()
    if not result["added"]:
        print(f"  {_DIM}Already present on {_RESET}{_BOLD}{result['entity_name']}{_RESET}"
              f"{_DIM} — nothing changed.{_RESET}")
        print(f"  {_DIM}resolution id: {_RESET}{_CYAN}{result['rid']}{_RESET}")
        print()
        return

    print(f"  {_GREEN}Added contradiction{_RESET} to {_BOLD}{result['entity_name']}{_RESET} "
          f"{_DIM}({args.entity_id}){_RESET}")
    print(f"  {_CYAN}{result['note_path']}{_RESET}")
    print(f"  {_DIM}acknowledge with{_RESET} {_CYAN}watchdog resolve {result['rid']}{_RESET}")
    print()
