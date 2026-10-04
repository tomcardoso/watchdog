"""The grouped command surface (D254): the ten commands a reporter uses, routed onto the parsers
that implement them. `new` and `setup` stay top-level because they are the first two commands
anyone runs.

`watchdog projects rename …`, `watchdog settings auth …` and the verbs under `review`,
`research`, `add` and `ask` are rewritten to the original command (`rename`, `auth`, …) before
argparse runs, so each implementation, its flags and its tests stay where they are. The original
names keep working: they are what the vault's skills and permission allowlist call, so they are
the stable interface for scripts, and a person typing one at a terminal gets a one-line pointer
to its new home.
"""

import sys

from watchdog.cmd.base import _BOLD, _CYAN, _DIM, _RESET

# group → {verb: original command}. A group's first entry is what the bare group runs.
GROUPS: dict[str, dict[str, str]] = {
    "projects": {
        "list": "list", "register": "register", "status": "status",
        "rename": "rename", "describe": "describe", "move": "move", "archive": "archive",
        "unarchive": "unarchive", "delete": "delete", "log": "log",
    },
    "settings": {
        "configure": "configure", "auth": "auth", "doctor": "doctor",
        "skills": "show-skills", "refresh-skills": "refresh-skills", "about": "about",
    },
}

# Verbs added to commands that also take their own arguments: `watchdog review resolve <id>`.
VERBS: dict[str, dict[str, str]] = {
    "review": {
        "resolve": "resolve", "unresolve": "unresolve", "watchlist": "watchlist",
        "merge-entities": "merge-entities", "add-contradiction": "contradiction-add",
    },
    "research": {"fetch": "fetch"},
}

# Flags that turn a command into another: `watchdog add --watch` runs `watchdog watch`.
FLAGS: dict[tuple[str, str], str] = {
    ("add", "--watch"): "watch",
    ("ask", "--context"): "context",
}

_GROUP_HELP = {
    "projects": ("Create, list and manage investigations", {
        "list": "List investigations (--all includes archived ones)",
        "register": "Register an existing vault folder",
        "status": "Show detailed status for one investigation, or all",
        "rename": "Rename an investigation",
        "describe": "Set an investigation's one-line description",
        "move": "Move an investigation's folder",
        "archive": "Archive a finished investigation",
        "unarchive": "Restore an archived investigation",
        "delete": "Remove an investigation from the list (files stay on disk)",
        "log": "Show an investigation's ingest log",
    }),
    "settings": ("Models, keys, setup, health checks and skills", {
        "configure": "View or change a setting (also: watchdog settings <key> <value>)",
        "auth": "Show and change how Watchdog signs in to model providers",
        "doctor": "Check for missing or broken vaults",
        "skills": "List the record skills",
        "refresh-skills": "Update a vault's skill files after an upgrade",
        "about": "Show version and project links",
    }),
}

# Where each original name now lives, for the pointer. Commands not listed here (the pipeline
# stages, maintenance commands, `search`, `review`, `add`, …) are unchanged.
MOVED: dict[str, str] = {
    **{orig: f"projects {verb}" for verb, orig in GROUPS["projects"].items()},
    **{orig: f"settings {verb}" for verb, orig in GROUPS["settings"].items()},
    **{orig: f"{cmd} {verb}" for cmd, verbs in VERBS.items() for verb, orig in verbs.items()},
    **{orig: f"{cmd} {flag}" for (cmd, flag), orig in FLAGS.items()},
    "configure": "settings",
    "leads": "review leads",
    "obsidian": "open",
}

MAINTENANCE = [
    ("chew", "Read documents in _INCOMING/ (step 1 of add)"),
    ("dig", "Extract chewed documents (step 2 of add)"),
    ("bark", "Finish a batch: reconciliation, synthesis, briefing (step 3 of add)"),
    ("requeue", "Put failed documents back in the queue without running them"),
    ("timeline", "Rebuild timeline.md"),
    ("reindex", "Rebuild the search index"),
    ("usage", "Token, cost and timing breakdown for past runs"),
    ("export", "Export the entity graph as CSV"),
    ("unlock", "Clear a lock left by a crashed run"),
]


def rewrite(argv: list[str]) -> list[str]:
    """`argv` (without the program name) with any grouped form turned into the original
    command. Anything else is returned unchanged."""
    if not argv:
        return argv
    head, rest = argv[0], argv[1:]
    if head in GROUPS:
        verbs = GROUPS[head]
        if rest and rest[0] in verbs:
            return [verbs[rest[0]], *rest[1:]]
        if rest and rest[0] in ("-h", "--help"):
            return argv
        if head == "settings":
            return ["configure", *rest]          # `settings`, `settings <key> [value]`
        if not rest:
            return [next(iter(verbs.values()))]
        return argv                               # unknown verb: let the group parser explain
    if head in VERBS and rest and rest[0] in VERBS[head]:
        return [VERBS[head][rest[0]], *rest[1:]]
    for flag in rest:
        target = FLAGS.get((head, flag))
        if target:
            return [target, *[a for a in rest if a != flag]]
    return argv


def pointer(typed: str) -> str | None:
    """The one-line note shown when someone types an original name at a terminal."""
    new = MOVED.get(typed)
    if not new or not sys.stdout.isatty():
        return None
    return (f"  {_DIM}`watchdog {typed}` is now{_RESET} {_CYAN}watchdog {new}{_RESET}"
            f"{_DIM} — the old name still works.{_RESET}")


def print_group_help(group: str) -> None:
    desc, verbs = _GROUP_HELP[group]
    print(f"\n  {_BOLD}watchdog {group}{_RESET} {_DIM}— {desc}{_RESET}\n")
    print(f"  {_DIM}Usage:  watchdog {group} <command> [options]{_RESET}\n")
    for verb, text in verbs.items():
        print(f"    {_CYAN}{verb:<16}{_RESET} {text}")
    first = next(iter(verbs))
    bare = "configure" if group == "settings" else first
    print(f"\n  {_DIM}`watchdog {group}` on its own runs `{bare}`. "
          f"Add --help after a command for its options.{_RESET}\n")


def print_maintenance() -> None:
    print(f"\n  {_BOLD}Maintenance commands{_RESET}\n")
    print(f"  {_DIM}For manual control, benchmarking and repairs. `watchdog add` runs the first "
          f"three for you.{_RESET}\n")
    for cmd, text in MAINTENANCE:
        print(f"    {_CYAN}{cmd:<16}{_RESET} {text}")
    print()


def cmd_group(args) -> None:
    """`watchdog projects <unknown>` / `watchdog settings --help`: show the group's commands."""
    print_group_help(args.command)
    if getattr(args, "verb", None):
        sys.exit(f"Error: unknown command '{args.verb}' for watchdog {args.command}")
