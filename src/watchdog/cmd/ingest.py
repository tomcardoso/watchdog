"""The terminal's pipeline commands (`add`, `chew`, `dig`, `bark`, `requeue`, `context`).

The pipeline itself lives in `watchdog.ops.ingest` (D298); these wrappers resolve the vault from
the working folder and run it with the terminal reporter, which asks the terminal's questions and
prints terminal wording. They go with the command line (issue #729, stage 3).
"""

import sys
from pathlib import Path

from watchdog import interactive
from watchdog.cmd.base import (
    _BOLD, _CYAN, _DIM, _RESET, _YELLOW,
    _ensure_layout, _find_project, _launch_claude, _MODEL_IDS, _render_template, load_projects,
)
from watchdog.ops import ingest as _ops
from watchdog.ops.ingest import _PICK_SKILL, exit_code_for  # noqa: F401  (cli imports these)
from watchdog.vault_paths import context_dir, is_vault


def _here() -> Path:
    return Path(".").resolve()


def cmd_chew(args) -> dict | None:
    vault = _here()
    if not is_vault(vault):
        sys.exit("Error: not inside a Watchdog project folder. cd into your investigation first.")
    _ensure_layout(vault)
    return _ops._chew(args, vault)


def cmd_ingest(args, *, confirm: bool = True, skip_preview: bool = False,
               non_interactive: bool = False) -> dict | None:
    return _ops._ingest(args, _here(), confirm=confirm, skip_preview=skip_preview,
                        non_interactive=non_interactive)


def cmd_extract(args, *, non_interactive: bool = False) -> dict | None:
    """`watchdog dig`; `non_interactive` is for the benchmark runner."""
    return _ops._extract(args, _here(), non_interactive=non_interactive)


def cmd_finalize(args) -> dict | None:
    vault = _here()
    if not is_vault(vault):
        sys.exit("Error: must be run from inside a Watchdog vault directory")
    _ensure_layout(vault)
    return _ops._finalize(args, vault)


def cmd_add(args) -> dict | None:
    if getattr(args, "watch", False):
        # `add --watch` is normally rewritten to `watch` before parsing (groups.rewrite); an
        # abbreviation like `--watc` reaches here instead, and must still mean watch.
        from argparse import Namespace
        from watchdog.cmd.vault import cmd_watch
        paths = getattr(args, "paths", None) or []
        if len(paths) > 1:
            sys.exit("Error: watchdog add --watch [name] takes only an investigation name.")
        return cmd_watch(Namespace(name=paths[0] if paths else None))
    vault = _here()
    if not is_vault(vault):
        sys.exit("Error: not inside a Watchdog project folder. cd into your investigation first.")
    _ensure_layout(vault)
    return _ops._add(args, vault)


def cmd_requeue(args) -> None:
    """Move documents from queue/_failed/ back into the active queue for re-ingest."""
    if getattr(args, "project", None):
        _, info = _find_project(args.project)
        vault = Path(info["path"])
    else:
        vault = _here()
        if not is_vault(vault):
            sys.exit("Error: must be run from inside a Watchdog vault directory, or pass the "
                     "investigation name")
    print(_ops.requeue_message(_ops._requeue_failed(vault)))



def cmd_context(args) -> None:
    vault = Path(".").resolve()
    if not is_vault(vault):
        if getattr(args, "name", None):
            _, info = _find_project(args.name)
            vault = Path(info["path"])
        else:
            sys.exit("Error: not inside a Watchdog project. cd into your investigation first, or pass the investigation name.")
    _ensure_layout(vault)
    model = getattr(args, "model", None) or "sonnet"
    if model not in _MODEL_IDS:
        sys.exit(f"Error: unknown model '{model}' — choose sonnet, opus, or haiku")

    projects = load_projects()
    info = next((v for v in projects.values() if Path(v["path"]).resolve() == vault.resolve()), None)
    name = info["name"] if info else vault.name

    ctx_dir = context_dir(vault)
    context_files = sorted(ctx_dir.iterdir()) if ctx_dir.is_dir() else []
    context_exists = (vault / "context.md").exists()

    print(f"\n  {_BOLD}{name}{_RESET}")
    if context_files:
        n = len(context_files)
        print(f"  {_DIM}{n} file{'s' if n != 1 else ''} in{_RESET} {_CYAN}context/{_RESET}")
    else:
        print(f"  {_YELLOW}context/ is empty{_RESET}{_DIM} — Claude will interview you instead{_RESET}")
    if context_exists:
        print(f"  {_DIM}existing context.md will be updated{_RESET}")

    if interactive.confirm("\n  Open in Claude Code to seed context?", default=True):
        context_path = vault / "context.md"
        if not context_path.exists():
            description = info["description"] if info and info.get("description") else "<!-- One paragraph, written as open questions: what do you want to understand or explore? -->"
            context_path.write_text(_render_template("context.md", name=name, description=description))
        _launch_claude(vault, "/watchdog-context", model=model)
    else:
        print(f"\n  When ready, open Claude Code and run:  {_CYAN}/watchdog-context{_RESET}\n")

