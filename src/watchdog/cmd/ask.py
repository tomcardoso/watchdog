"""`watchdog ask ["question"]` — open an interactive Claude Code session on the vault (D253).

With a question, the session's first prompt is `/watchdog-query <question>`: Claude answers from
the vault with citations, files a substantive answer to `queries/` as the skill does, and the
session stays open for follow-ups. Without one, it opens ready for questions. The session runs on
whatever Claude Code is signed in with, not the ingest provider."""

import sys
from pathlib import Path

from watchdog.cmd.base import _BOLD, _CYAN, _DIM, _RESET, _YELLOW, _find_project, _launch_claude, load_projects
from watchdog.vault_paths import is_vault


def _vault(name: str | None) -> tuple[Path, str]:
    if name:
        _, info = _find_project(name)
        return Path(info["path"]), info.get("name") or name
    vault = Path(".").resolve()
    if not is_vault(vault):
        sys.exit("Error: not inside a Watchdog project. cd into your investigation first, "
                 "or pass --project NAME.")
    info = next((v for v in load_projects().values() if Path(v["path"]).resolve() == vault), None)
    return vault, (info or {}).get("name") or vault.name


def prompt_for(question: str | None, has_skill: bool = True) -> str | None:
    """The session's first prompt: the query skill on `question`, the bare question when the vault
    has no `/watchdog-query` (an unknown slash command would lose it), or nothing."""
    question = " ".join((question or "").split())
    if not question:
        return None
    if has_skill:
        return f"/watchdog-query {question}"
    # The bare question is passed to `claude` as a positional argument; one starting with "-"
    # would be read as a flag (`-p` is print mode), so it gets a harmless prefix.
    return f"Question: {question}" if question.startswith("-") else question


def cmd_ask(args) -> None:
    vault, name = _vault(getattr(args, "project", None))
    if not vault.is_dir():
        sys.exit(f"Error: project directory not found: {vault}")
    question = " ".join(getattr(args, "question", None) or [])
    print(f"\n  {_BOLD}Opening Claude Code in {name}…{_RESET}")
    has_skill = (vault / ".claude" / "commands" / "watchdog-query.md").exists()
    if not has_skill:
        print(f"  {_YELLOW}This vault has no /watchdog-query command.{_RESET} "
              f"{_DIM}Run{_RESET} {_CYAN}watchdog refresh-skills{_RESET} {_DIM}to install it.{_RESET}")
    if question and has_skill:
        print(f"  {_DIM}Claude answers from the vault with citations, then stays open for "
              f"follow-ups.{_RESET}")
    elif question:
        print(f"  {_DIM}Claude answers your question, then stays open for follow-ups.{_RESET}")
    else:
        print(f"  {_DIM}Ask anything about the documents — for a cited answer saved to queries/, "
              f"start with{_RESET} {_CYAN}/watchdog-query{_RESET}{_DIM}.{_RESET}")
    print(f"  {_DIM}Exit with Ctrl-D.{_RESET}\n")
    _launch_claude(vault, prompt_for(question, has_skill), model=getattr(args, "model", None))
