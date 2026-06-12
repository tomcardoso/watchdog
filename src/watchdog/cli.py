"""watchdog — investigative journalism document intelligence CLI"""

import argparse
import subprocess  # noqa — kept for test monkeypatching via watchdog.cli.subprocess
import sys         # noqa — kept for test monkeypatching via watchdog.cli.sys

from watchdog.cmd.base import (
    CONFIG_FILE,
    WATCHDOG_HOME,
    PROJECTS_FILE,
    _ALIASES,
    _BOLD,
    _CMD_HELP,
    _CYAN,
    _DIM,
    _GREEN,
    _PIPELINE_COMMANDS,
    _RESET,
    _YELLOW,
    _check_vault_locks,
    _count_incoming,
    _count_queued,
    _find_project,
    _fmt_date,
    _launch_claude,
    _load_registry,
    _notify,
    _perf_cpu_count,
    _print_banner,
    _print_cmd_help,
    _project_completer,
    _projects_dir,
    _render_template,
    _VAULT_PERMISSIONS,
    load_projects,
    save_projects,
    slugify,
)
from watchdog.cmd.vault import (
    _obsidian_config_path,
    _obsidian_registered,
    _register_obsidian_vault,
    cmd_archive,
    cmd_delete,
    cmd_describe,
    cmd_doctor,
    cmd_list,
    cmd_log,
    cmd_move,
    cmd_new,
    cmd_obsidian,
    cmd_open,
    cmd_rename,
    cmd_search,
    cmd_status,
    cmd_unarchive,
    cmd_watch,
)
from watchdog.cmd.ingest import (
    _run_preprocess,
    cmd_chew,
    cmd_context,
    cmd_ingest,
    cmd_postflight,
    cmd_preflight,
    cmd_queue_status,
)
from watchdog.cmd.registry import (
    _VALID_CONFIDENCE,
    cmd_entity_index,
    cmd_is_duplicate,
    cmd_validate_extraction,
)
from watchdog.cmd.setup import (
    _CONFIGURE_KEYS,
    _OCR_ENGINE_PACKAGES,
    _TESSERACT_HEADERS_HINT,
    _ensure_ocr_engine,
    cmd_about,
    cmd_configure,
    cmd_refresh_skills,
    cmd_setup,
    cmd_unlock,
)


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] in ("-v", "--version"):
        cmd_about(None)
        return

    if len(sys.argv) >= 2 and sys.argv[1] in ("-h", "--help"):
        _print_banner()
        return

    if len(sys.argv) >= 3 and sys.argv[2] in ("-h", "--help"):
        cmd = _ALIASES.get(sys.argv[1], sys.argv[1])
        if cmd in _CMD_HELP:
            _print_cmd_help(cmd)
            return

    if len(sys.argv) >= 2 and sys.argv[1] in _ALIASES:
        sys.argv[1] = _ALIASES[sys.argv[1]]

    if len(sys.argv) >= 2 and sys.argv[1] in _PIPELINE_COMMANDS:
        import importlib
        module_path, prog_name = _PIPELINE_COMMANDS[sys.argv[1]]
        sys.argv = [prog_name] + sys.argv[2:]
        importlib.import_module(module_path).main()
        return

    # Internal pipeline commands — dispatched before argparse so they never
    # appear in tab completion
    _INTERNAL_CMDS = {
        "entity-index", "queue-status", "validate-extraction",
        "is-duplicate",  "pre-flight",  "post-flight",
    }
    if len(sys.argv) >= 2 and sys.argv[1] in _INTERNAL_CMDS:
        cmd = sys.argv[1]
        _p = argparse.ArgumentParser(prog=f"watchdog {cmd}")
        if cmd == "entity-index":
            _p.add_argument("project", nargs="?")
            cmd_entity_index(_p.parse_args(sys.argv[2:]))
        elif cmd == "queue-status":
            _p.add_argument("project", nargs="?")
            cmd_queue_status(_p.parse_args(sys.argv[2:]))
        elif cmd == "validate-extraction":
            _p.add_argument("file")
            cmd_validate_extraction(_p.parse_args(sys.argv[2:]))
        elif cmd == "is-duplicate":
            _p.add_argument("sha256")
            _p.add_argument("project", nargs="?")
            cmd_is_duplicate(_p.parse_args(sys.argv[2:]))
        elif cmd == "pre-flight":
            _p.add_argument("sha256")
            cmd_preflight(_p.parse_args(sys.argv[2:]))
        elif cmd == "post-flight":
            _p.add_argument("--extraction", required=True)
            cmd_postflight(_p.parse_args(sys.argv[2:]))
        return

    parser = argparse.ArgumentParser(
        prog="watchdog",
        description="Investigative journalism document intelligence tool",
    )
    sub = parser.add_subparsers(dest="command", required=False)

    p_new = sub.add_parser("new", help="Create a new investigation vault")
    p_new.add_argument("name", nargs="?", help="Investigation name (e.g. 'Shell Company Investigation')")
    p_new.add_argument("--name", dest="name_flag", help="Investigation name (alternative to positional)")
    p_new.add_argument("--description", help="One-line description of the investigation")
    p_new.add_argument("--dir", help=f"Parent directory (default: projects_dir from config)")
    p_new.set_defaults(func=cmd_new)

    p_list = sub.add_parser("list", help="List all registered investigations")
    p_list.add_argument("--all", action="store_true", help="Include archived investigations")
    p_list.set_defaults(func=cmd_list)

    p_status = sub.add_parser("status", help="Show detailed status for an investigation")
    p_status.add_argument("name", nargs="?", help="Investigation name or slug (omit to list all)").completer = _project_completer
    p_status.set_defaults(func=cmd_status)

    p_doctor = sub.add_parser("doctor", help="Check all registered investigations for missing or broken vaults")
    p_doctor.set_defaults(func=cmd_doctor)

    p_setup = sub.add_parser("setup", help="Set up Watchdog after installation")
    p_setup.add_argument("--force", action="store_true", help="Re-run setup even if already complete")
    p_setup.set_defaults(func=cmd_setup)

    p_refresh = sub.add_parser("refresh-skills", help="Update skill files in a vault after a watchdog upgrade")
    p_refresh.add_argument("name", nargs="?", help="Investigation name or slug (default: current directory)").completer = _project_completer
    p_refresh.set_defaults(func=cmd_refresh_skills)

    p_about = sub.add_parser("about", help="Show version and project links")
    p_about.set_defaults(func=cmd_about)

    p_search = sub.add_parser("search", help="Semantic search across ingested documents")
    p_search.add_argument("project", nargs="?", help="Investigation name or slug (omit when inside the project folder)").completer = _project_completer
    p_search.add_argument("query", nargs="?", help="Search query")
    p_search.add_argument("--top", dest="top_n", type=int, default=5, metavar="N",
                          help="Number of results to return (default: 5)")
    p_search.set_defaults(func=cmd_search)

    p_unlock = sub.add_parser("unlock", help="Release a stale ingest lock")
    p_unlock.add_argument("project", nargs="?", help="Investigation name or slug (default: infer from cwd)").completer = _project_completer
    p_unlock.add_argument("--force", action="store_true", help="Remove lock even if recent")
    p_unlock.set_defaults(func=cmd_unlock)

    p_configure = sub.add_parser("configure", help="View or change configuration")
    p_configure.add_argument("key",   nargs="?", help=f"Config key ({', '.join(_CONFIGURE_KEYS)})")
    p_configure.add_argument("value", nargs="?", help="Value to set")
    p_configure.set_defaults(func=cmd_configure)

    p_chew = sub.add_parser("chew", help="Process documents in _INCOMING/ and prepare them for ingestion")
    p_chew.add_argument("file", nargs="?", default=None,
                        help="Specific file to chew (omit to chew all of _INCOMING/)")
    p_chew.add_argument("--chew-workers", type=int, default=None, metavar="N",
                        dest="chew_workers",
                        help="Parallel file workers (see chew_workers in watchdog configure)")
    p_chew.add_argument("--chunk-workers", type=int, default=None, metavar="N",
                        dest="chunk_workers",
                        help="Parallel chunk workers per file (see chunk_workers in watchdog configure)")
    p_chew.set_defaults(func=cmd_chew)

    p_obsidian = sub.add_parser("obsidian", help="Open an investigation vault in Obsidian")
    p_obsidian.add_argument("name", nargs="?", help="Investigation name or slug (default: current directory)").completer = _project_completer
    p_obsidian.set_defaults(func=cmd_obsidian)

    p_open = sub.add_parser("open", help="Open vault folder in Finder / file explorer")
    p_open.add_argument("name", nargs="?", help="Investigation name or slug (default: current directory)").completer = _project_completer
    p_open.set_defaults(func=cmd_open)

    p_delete = sub.add_parser("delete", help="Remove an investigation from registry")
    p_delete.add_argument("name", help="Investigation name or slug").completer = _project_completer
    p_delete.add_argument("--purge", action="store_true",
                          help="Also permanently delete all vault files from disk")
    p_delete.set_defaults(func=cmd_delete)

    p_move = sub.add_parser("move", help="Update vault path in registry")
    p_move.add_argument("name", help="Investigation name or slug").completer = _project_completer
    p_move.add_argument("path", help="New path for the vault")
    p_move.set_defaults(func=cmd_move)

    p_archive = sub.add_parser("archive", help="Archive a completed investigation")
    p_archive.add_argument("name", help="Investigation name or slug").completer = _project_completer
    p_archive.set_defaults(func=cmd_archive)

    p_unarchive = sub.add_parser("unarchive", help="Restore an archived investigation")
    p_unarchive.add_argument("name", help="Investigation name or slug").completer = _project_completer
    p_unarchive.set_defaults(func=cmd_unarchive)

    p_log = sub.add_parser("log", help="Show ingest history for an investigation")
    p_log.add_argument("name", help="Investigation name or slug").completer = _project_completer
    p_log.add_argument("--lines", type=int, default=None, metavar="N",
                       help="Number of lines to show (default: all)")
    p_log.set_defaults(func=cmd_log)

    p_watch = sub.add_parser("watch", help="Watch _INCOMING/ and chew files automatically")
    p_watch.add_argument("name", help="Investigation name or slug").completer = _project_completer
    p_watch.set_defaults(func=cmd_watch)

    p_rename = sub.add_parser("rename", help="Rename an investigation (folder and registry)")
    p_rename.add_argument("project", nargs="?", help="Investigation name or slug (omit when inside the project folder)").completer = _project_completer
    p_rename.add_argument("name", nargs="?", help="New name (omit to be prompted)")
    p_rename.set_defaults(func=cmd_rename)

    p_describe = sub.add_parser("describe", help="Set or update an investigation description")
    p_describe.add_argument("project", nargs="?", help="Investigation name or slug (omit when inside the project folder)").completer = _project_completer
    p_describe.add_argument("text", nargs="?", help="New description text (omit to be prompted)")
    p_describe.set_defaults(func=cmd_describe)

    _model_choices = ["sonnet", "opus", "haiku"]
    p_ingest = sub.add_parser("ingest", help="Set up extraction session and open in Claude Code")
    p_ingest.add_argument("--orchestrator-model", choices=_model_choices, default="sonnet",
                          dest="orchestrator_model",
                          help="Model for the orchestrator session (default: sonnet)")
    p_ingest.add_argument("--extractor-model", choices=_model_choices, default="sonnet",
                          dest="extractor_model",
                          help="Model for extraction subagents (default: sonnet)")
    p_ingest.set_defaults(func=cmd_ingest)

    p_context = sub.add_parser("context", help="Open Claude Code to seed investigation context from _CONTEXT/")
    p_context.add_argument("name", nargs="?", help="Investigation name or slug (default: current directory)").completer = _project_completer
    p_context.add_argument("--model", choices=_model_choices, default="sonnet",
                           help="Model to use (default: sonnet)")
    p_context.set_defaults(func=cmd_context)

    try:
        import argcomplete
        sub.choices = dict(sorted(sub.choices.items()))
        argcomplete.autocomplete(parser)
    except ImportError:
        pass

    args = parser.parse_args()

    if args.command is None:
        if not CONFIG_FILE.exists():
            print(f"\n  {_BOLD}Watchdog isn't set up yet.{_RESET}\n")
            try:
                answer = input("  Run setup now? [Y/n] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if answer in ("", "y", "yes"):
                from watchdog.setup_cmd import run as run_setup
                run_setup()
            return
        from pathlib import Path
        wddir = Path(".watchdog")
        if wddir.is_dir() and (wddir / "queue").is_dir():
            _run_preprocess(Path(".").resolve(), confirm=True)
        else:
            _print_banner()
        return

    if args.command not in {"setup", "about", "configure"} and not CONFIG_FILE.exists():
        print(f"\n  {_BOLD}Watchdog isn't set up yet.{_RESET}  Run: {_CYAN}watchdog setup{_RESET}\n")
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
