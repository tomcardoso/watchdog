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
    cmd_register,
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
    cmd_finalize,
    cmd_guided,
    cmd_ingest,
    cmd_postflight,
    cmd_preflight,
    cmd_queue_status,
    cmd_requeue,
)
from watchdog.cmd.registry import (
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
    cmd_show_skills,
    cmd_unlock,
)
from watchdog.cmd.auth import cmd_auth
from watchdog.cmd.export import cmd_export
from watchdog.cmd.leads import cmd_leads
from watchdog.cmd.research import cmd_fetch, cmd_research, cmd_research_fetch, cmd_research_seen


def _cmd_rebuild_timeline(args) -> None:
    from pathlib import Path
    from watchdog.pipeline.timeline import cmd_rebuild_timeline
    if args.name:
        _, info = _find_project(args.name)
        vault = Path(info["path"])
    else:
        vault = Path(".").resolve()
        if not (vault / ".watchdog").is_dir():
            sys.exit("Error: not inside a watchdog project. Run `watchdog timeline <name>` or cd into a project first.")
    cmd_rebuild_timeline(vault)


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
        "timeline-collisions", "research-fetch", "research-seen",
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
        elif cmd == "timeline-collisions":
            from watchdog.pipeline.timeline import main_collisions
            main_collisions()
        elif cmd == "research-fetch":
            _p.add_argument("project", nargs="?")
            _p.add_argument("--file")
            cmd_research_fetch(_p.parse_args(sys.argv[2:]))
        elif cmd == "research-seen":
            _p.add_argument("project", nargs="?")
            cmd_research_seen(_p.parse_args(sys.argv[2:]))
        return

    parser = argparse.ArgumentParser(
        prog="watchdog",
        description="Investigative journalism document intelligence tool",
    )
    sub = parser.add_subparsers(dest="command", required=False)

    p_register = sub.add_parser("register", help="Register an existing vault folder with watchdog")
    p_register.add_argument("path", nargs="?", help="Path to the existing vault folder (defaults to current directory)")
    p_register.add_argument("--name", help="Investigation name (omit to be prompted)")
    p_register.set_defaults(func=cmd_register)

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

    p_show_skills = sub.add_parser("show-skills", help="List the record skills, or print one (opens the skills folder on GitHub)")
    p_show_skills.add_argument("name", nargs="?", help="Skill name to print in full (omit to list all)")
    p_show_skills.set_defaults(func=cmd_show_skills)

    p_about = sub.add_parser("about", help="Show version and project links")
    p_about.set_defaults(func=cmd_about)

    p_search = sub.add_parser("search", help="Semantic search across ingested documents")
    p_search.add_argument("project", nargs="?", help="Investigation name or slug (omit when inside the project folder)").completer = _project_completer
    p_search.add_argument("query", nargs="?", help="Search query (supports +/- phrases)")
    p_search.add_argument("--top", dest="top_n", type=int, default=5, metavar="N",
                          help="Number of results to return per section (default: 5)")
    p_search.add_argument("--threshold", type=float, default=None, metavar="S",
                          help="Hide results scoring below S (0.0–1.0)")
    p_search.add_argument("--no-rerank", action="store_true",
                          help="Skip the cross-encoder rerank of corpus results (faster; lower quality)")
    p_search.add_argument("--full", action="store_true",
                          help="Print the complete passage/note text instead of a truncated snippet")
    p_search.add_argument("--json", action="store_true",
                          help="Emit results as JSON (for skills/scripts) instead of the formatted listing")
    p_search.set_defaults(func=cmd_search)

    p_export = sub.add_parser("export", help="Export the knowledge graph as Neo4j-import CSV (or Cypher)")
    p_export.add_argument("project", nargs="?", help="Investigation name or slug (omit when inside the project folder)").completer = _project_completer
    p_export.add_argument("--output", metavar="DIR", help="Output directory (default: <slug>-export/)")
    p_export.add_argument("--format", choices=["csv", "cypher"], default="csv",
                          help="Output format (default: csv)")
    p_export.set_defaults(func=cmd_export)

    p_leads = sub.add_parser("leads", help="Surface investigative leads from the entity graph (deterministic)")
    p_leads.add_argument("project", nargs="?", help="Investigation name or slug (omit when inside the project folder)").completer = _project_completer
    p_leads.set_defaults(func=cmd_leads)

    p_research = sub.add_parser("research", help="Open Claude Code to research open questions on the web")
    p_research.add_argument("name", nargs="?", help="Investigation name or slug (default: current directory)").completer = _project_completer
    p_research.add_argument("--question", "-q", help="Research question to seed (omit to be prompted)")
    p_research.add_argument("--model", help="Model to use (sonnet/opus/haiku, default: sonnet)")
    p_research.set_defaults(func=cmd_research)

    p_fetch = sub.add_parser("fetch", help="Download a batch of URLs (or a links file) into _INCOMING/")
    p_fetch.add_argument("targets", nargs="+", metavar="URL|FILE",
                         help="One or more URLs, or the path to a links file (one URL per line, or the "
                              "tab-separated url⇥title⇥source_type⇥relevance form)")
    p_fetch.add_argument("--project", help="Investigation name or slug (default: current directory)").completer = _project_completer
    p_fetch.set_defaults(func=cmd_fetch)

    p_unlock = sub.add_parser("unlock", help="Release a stale ingest lock")
    p_unlock.add_argument("project", nargs="?", help="Investigation name or slug (default: infer from cwd)").completer = _project_completer
    p_unlock.add_argument("--force", action="store_true", help="Remove lock even if recent")
    p_unlock.set_defaults(func=cmd_unlock)

    p_requeue = sub.add_parser("requeue", help="Move documents from queue/_failed/ back into the queue for re-ingest")
    p_requeue.set_defaults(func=cmd_requeue)

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
    p_log.add_argument("name", nargs="?", help="Investigation name or slug (omit when inside the project directory)").completer = _project_completer
    p_log.add_argument("--lines", type=int, default=None, metavar="N",
                       help="Number of lines to show (default: all)")
    p_log.set_defaults(func=cmd_log)

    p_watch = sub.add_parser("watch", help="Watch _INCOMING/ and chew files automatically")
    p_watch.add_argument("name", nargs="?", help="Investigation name or slug (omit when inside the project directory)").completer = _project_completer
    p_watch.set_defaults(func=cmd_watch)

    p_timeline = sub.add_parser("timeline", help="Rebuild timeline.md from canonical .watchdog/timeline/ files")
    p_timeline.add_argument("name", nargs="?", help="Investigation name or slug (default: current directory)").completer = _project_completer
    p_timeline.set_defaults(func=_cmd_rebuild_timeline)

    p_rename = sub.add_parser("rename", help="Rename an investigation (folder and registry)")
    p_rename.add_argument("project", nargs="?", help="Investigation name or slug (omit when inside the project folder)").completer = _project_completer
    p_rename.add_argument("name", nargs="?", help="New name (omit to be prompted)")
    p_rename.set_defaults(func=cmd_rename)

    p_describe = sub.add_parser("describe", help="Set or update an investigation description")
    p_describe.add_argument("project", nargs="?", help="Investigation name or slug (omit when inside the project folder)").completer = _project_completer
    p_describe.add_argument("text", nargs="?", help="New description text (omit to be prompted)")
    p_describe.set_defaults(func=cmd_describe)

    _model_choices = ["sonnet", "opus", "haiku"]
    _effort_choices = ["low", "medium", "high"]
    _model_help = ("a Claude tier (sonnet/opus/haiku) or a backend:model form "
                   "(claude-api:opus, openai:gpt-5-mini, deepseek:deepseek-chat)")
    p_ingest = sub.add_parser("ingest", help="Extract queued documents (runs the Python pipeline)")
    p_ingest.add_argument("--extractor-model", default=None, dest="extractor_model", metavar="MODEL",
                          help=f"Model for extraction — {_model_help}; overrides watchdog configure (default: sonnet)")
    p_ingest.add_argument("--finalizer-model", default=None, dest="finalizer_model", metavar="MODEL",
                          help=f"Model for synthesis + timeline + briefing — {_model_help}; overrides watchdog configure (default: haiku)")
    p_ingest.add_argument("--classifier-model", default=None, dest="classifier_model", metavar="MODEL",
                          help=f"Model for document classification — {_model_help}; overrides watchdog configure (default: haiku)")
    p_ingest.add_argument("--extractor-effort", choices=_effort_choices, default=None,
                          dest="extractor_effort",
                          help="Reasoning effort for extraction — lower spends fewer tokens; "
                               "overrides watchdog configure (default: high)")
    p_ingest.add_argument("--finalizer-effort", choices=_effort_choices, default=None,
                          dest="finalizer_effort",
                          help="Reasoning effort for synthesis + timeline + briefing — "
                               "overrides watchdog configure (default: high)")
    p_ingest.add_argument("--concurrency", type=int, default=None,
                          help="Documents extracted in parallel — overrides watchdog configure (default: 5)")
    p_ingest.add_argument("--classify-pages", type=int, default=None, dest="classify_pages",
                          help="Pages shown to the document classifier — overrides watchdog configure (default: 5)")
    from watchdog.cmd.ingest import _PICK_SKILL
    p_ingest.add_argument("--skill", nargs="?", const=_PICK_SKILL, default=None, dest="skill",
                          metavar="NAME",
                          help="Pin a record skill for every document, skipping classification. "
                               "Pass a skill name, or use --skill with no value to pick interactively.")
    p_ingest.set_defaults(func=cmd_ingest)

    p_finalize = sub.add_parser("finalize", help="Complete post-ingest (synthesis + timeline + briefing) for an already-extracted batch — e.g. after a rate limit stopped it")
    p_finalize.add_argument("--finalizer-model", default=None, dest="finalizer_model", metavar="MODEL",
                            help=f"Model for synthesis + timeline + briefing — {_model_help}; overrides watchdog configure (default: haiku)")
    p_finalize.add_argument("--finalizer-effort", choices=_effort_choices, default=None,
                            dest="finalizer_effort",
                            help="Reasoning effort for synthesis + timeline + briefing — "
                                 "overrides watchdog configure (default: high)")
    p_finalize.set_defaults(func=cmd_finalize)

    p_context = sub.add_parser("context", help="Open Claude Code to seed investigation context from _CONTEXT/")
    p_context.add_argument("name", nargs="?", help="Investigation name or slug (default: current directory)").completer = _project_completer
    p_context.add_argument("--model", choices=_model_choices, default="sonnet",
                           help="Model to use (default: sonnet)")
    p_context.set_defaults(func=cmd_context)

    p_auth = sub.add_parser("auth", help="Choose auth mode and manage API keys for model backends")
    p_auth.add_argument("action", nargs="?", choices=["status", "use", "set", "get", "remove"],
                        help="status (default) | use <mode> | set/get/remove [provider]")
    p_auth.add_argument("target", nargs="?",
                        help="mode for `use` (subscription/api-key); provider for set/get/remove "
                             "(anthropic [default], openai, deepseek)")
    p_auth.set_defaults(func=cmd_auth)

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
            cmd_guided(args)
        else:
            _print_banner()
        return

    if args.command not in {"setup", "about", "configure"} and not CONFIG_FILE.exists():
        print(f"\n  {_BOLD}Watchdog isn't set up yet.{_RESET}  Run: {_CYAN}watchdog setup{_RESET}\n")
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
