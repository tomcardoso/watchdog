"""watchdog — investigative journalism document intelligence CLI"""

import argparse
import subprocess  # noqa — kept for test monkeypatching via watchdog.cli.subprocess
import sys         # noqa — kept for test monkeypatching via watchdog.cli.sys

from watchdog import interactive
from watchdog.cmd.base import (
    CONFIG_FILE,
    WATCHDOG_HOME,
    PROJECTS_FILE,
    _ALIASES,
    _DEPRECATED_ALIASES,
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
    _obsidian_launch_epoch,
    _obsidian_registered,
    _obsidian_vault_ts,
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
    cmd_extract,
    cmd_finalize,
    cmd_guided,
    cmd_ingest,
    cmd_queue_status,
    cmd_requeue,
    exit_code_for,
)
from watchdog.cmd.registry import (
    cmd_entity_index,
    cmd_is_duplicate,
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
from watchdog.cmd.merge_entities import cmd_merge_entities
from watchdog.cmd.contradiction import cmd_contradiction_add
from watchdog.cmd.leads import cmd_leads
from watchdog.cmd.resolve import cmd_resolve, cmd_unresolve
from watchdog.cmd.reindex import cmd_reindex
from watchdog.cmd.research import cmd_fetch, cmd_research, cmd_research_fetch, cmd_research_seen
from watchdog.cmd.usage import cmd_usage
from watchdog.cmd.watchlist import cmd_watchlist, cmd_watchlist_add


def _cmd_rebuild_timeline(args) -> None:
    from pathlib import Path
    from watchdog.pipeline.timeline import cmd_rebuild_timeline, main_rebuild
    if args.name:
        _, info = _find_project(args.name)
        vault = Path(info["path"])
        cmd_rebuild_timeline(vault)
    else:
        main_rebuild()


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] in ("-v", "--version"):
        cmd_about(None)
        return

    if len(sys.argv) >= 2 and sys.argv[1] in ("-h", "--help"):
        _print_banner()
        return

    if len(sys.argv) >= 3 and sys.argv[2] in ("-h", "--help"):
        cmd = _ALIASES.get(sys.argv[1], _DEPRECATED_ALIASES.get(sys.argv[1], sys.argv[1]))
        if cmd in _CMD_HELP:
            _print_cmd_help(cmd)
            return

    if len(sys.argv) >= 2 and sys.argv[1] in _ALIASES:
        sys.argv[1] = _ALIASES[sys.argv[1]]

    # `extract`/`finalize` → `dig`/`bark` (#441, D138): kept working during a deprecation
    # window rather than removed outright, but unlike a plain `_ALIASES` entry, this warns —
    # the point is for people to actually move onto the new name.
    if len(sys.argv) >= 2 and sys.argv[1] in _DEPRECATED_ALIASES:
        old, new = sys.argv[1], _DEPRECATED_ALIASES[sys.argv[1]]
        print(f"\n  {_YELLOW}Warning:{_RESET} {_CYAN}watchdog {old}{_RESET}{_DIM} is deprecated — "
              f"use {_RESET}{_CYAN}watchdog {new}{_RESET}{_DIM} instead.{_RESET}")
        sys.argv[1] = new

    if len(sys.argv) >= 2 and sys.argv[1] in _PIPELINE_COMMANDS:
        import importlib
        module_path, prog_name = _PIPELINE_COMMANDS[sys.argv[1]]
        sys.argv = [prog_name] + sys.argv[2:]
        importlib.import_module(module_path).main()
        return

    # Internal pipeline commands — dispatched before argparse so they never
    # appear in tab completion
    _INTERNAL_CMDS = {
        "entity-index", "queue-status",
        "is-duplicate",
        "timeline-collisions", "research-fetch", "research-seen", "watchlist-add",
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
        elif cmd == "is-duplicate":
            _p.add_argument("sha256")
            _p.add_argument("project", nargs="?")
            cmd_is_duplicate(_p.parse_args(sys.argv[2:]))
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
        elif cmd == "watchlist-add":
            _p.add_argument("terms", nargs="+")
            cmd_watchlist_add(_p.parse_args(sys.argv[2:]))
        return

    parser = argparse.ArgumentParser(
        prog="watchdog",
        description="Investigative journalism document intelligence tool",
    )
    # Bare `watchdog` (no subcommand) inside a vault walks into `cmd_guided`, which falls
    # through to `cmd_ingest` via `_offer_ingest` — this top-level flag rides along on that
    # same `args` Namespace and reaches `cmd_ingest`'s `getattr(args, "skip_briefing", False)`
    # unchanged (#410). Subcommand-scoped `--skip-briefing` (on `ingest`/`bark`) is separate,
    # added on their own subparsers below.
    parser.add_argument("--skip-briefing", action="store_true", default=False, dest="skip_briefing",
                        help="When the guided walk reaches ingest, run entity reconciliation, "
                             "synthesis, and the timeline rebuild, but skip the briefing model call.")
    sub = parser.add_subparsers(dest="command", required=False)

    p_register = sub.add_parser("register", help="Register an existing vault folder with watchdog")
    p_register.add_argument("path", nargs="?", help="Path to the existing vault folder (defaults to current directory)")
    p_register.add_argument("--name", help="Investigation name (omit to be prompted)")
    p_register.set_defaults(func=cmd_register)

    p_new = sub.add_parser("new", help="Create a new investigation vault")
    p_new.add_argument("name", nargs="?", help="Investigation name (e.g. 'Shell Company Investigation')")
    p_new.add_argument("--name", dest="name_flag", help="Investigation name (alternative to positional)")
    p_new.add_argument("--description", help="One-line description of the investigation")
    p_new.add_argument("--dir", help="Parent directory (default: projects_dir from config)")
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

    p_search = sub.add_parser("search", help="Search ingested documents (semantic + exact-match)")
    p_search.add_argument("project", nargs="?", help="Investigation name or slug (omit when inside the project folder)").completer = _project_completer
    p_search.add_argument("query", nargs="?", help="Search query (supports +/- phrases and \"quoted phrases\")")
    p_search.add_argument("--top", dest="top_n", type=int, default=5, metavar="N",
                          help="Number of results to return per section (default: 5)")
    p_search.add_argument("--threshold", type=float, default=None, metavar="S",
                          help="Hide results scoring below S (0.0–1.0)")
    p_search.add_argument("--no-rerank", action="store_true",
                          help="Skip the cross-encoder rerank of corpus results (faster; lower quality)")
    p_search.add_argument("--full", action="store_true",
                          help="Print the complete passage/note text instead of a truncated snippet")
    p_search.add_argument("--batch", metavar="FILE",
                          help="Read search terms from FILE (one per line) and report hits per term, "
                               "instead of ranking a single query")
    p_search.add_argument("--everywhere", action="store_true",
                          help="Search every registered, non-archived investigation instead of one project "
                               "(manifest + exact-match lanes only, grouped by investigation; combine with "
                               "--batch to check a list of terms across all vaults)")
    p_search.add_argument("--json", action="store_true",
                          help="Emit results as JSON (for skills/scripts) instead of the formatted listing")
    p_search.set_defaults(func=cmd_search)

    p_export = sub.add_parser("export", help="Export the knowledge graph as Neo4j-import CSV (or Cypher)")
    p_export.add_argument("project", nargs="?", help="Investigation name or slug (omit when inside the project folder)").completer = _project_completer
    p_export.add_argument("--output", metavar="DIR", help="Output directory (default: <slug>-export/)")
    p_export.add_argument("--format", choices=["csv", "cypher"], default="csv",
                          help="Output format (default: csv)")
    p_export.set_defaults(func=cmd_export)

    p_usage = sub.add_parser("usage", help="Per-call token/cost/latency breakdown for ingest runs (deterministic, no model)")
    p_usage.add_argument("project", nargs="?", help="Investigation name or slug (omit when inside the project folder)").completer = _project_completer
    p_usage.add_argument("--all", action="store_true", help="Compare every run recorded in the vault")
    p_usage.add_argument("--run", metavar="TIMESTAMP", help="Analyze one specific past run instead of the latest")
    p_usage.set_defaults(func=cmd_usage)

    p_merge_entities = sub.add_parser("merge-entities", help="Merge a duplicate entity into another")
    p_merge_entities.add_argument("keep_id", help="Entity id to keep (the survivor)")
    p_merge_entities.add_argument("merge_id", help="Entity id to merge away (folded into keep_id)")
    p_merge_entities.add_argument("--force", action="store_true",
                                  help="Skip the confirmation prompt")
    p_merge_entities.set_defaults(func=cmd_merge_entities)

    p_contradiction = sub.add_parser("contradiction-add", help=argparse.SUPPRESS)
    p_contradiction.add_argument("entity_id", help="Entity id the contradiction belongs to")
    p_contradiction.add_argument("--label", required=True, metavar="TEXT",
                                 help="Short label for the disputed fact")
    p_contradiction.add_argument("--a", required=True, metavar="VALUE",
                                 help="First (existing) value")
    p_contradiction.add_argument("--a-doc", required=True, dest="a_doc", metavar="SLUG",
                                 help="Document slug the first value comes from")
    p_contradiction.add_argument("--a-page", dest="a_page", type=int, metavar="N",
                                 help="Page number for the first value (optional)")
    p_contradiction.add_argument("--b", required=True, metavar="VALUE",
                                 help="Second (conflicting) value")
    p_contradiction.add_argument("--b-doc", required=True, dest="b_doc", metavar="SLUG",
                                 help="Document slug the second value comes from")
    p_contradiction.add_argument("--b-page", dest="b_page", type=int, metavar="N",
                                 help="Page number for the second value (optional)")
    p_contradiction.set_defaults(func=cmd_contradiction_add)

    p_leads = sub.add_parser("leads", help="Surface investigative leads from the entity graph (deterministic)")
    p_leads.add_argument("project", nargs="?", help="Investigation name or slug (omit when inside the project folder)").completer = _project_completer
    p_leads.set_defaults(func=cmd_leads)

    p_resolve = sub.add_parser("resolve", help="Acknowledge leads/alerts/contradictions so they stop re-surfacing")
    p_resolve.add_argument("ids", nargs="*", metavar="ID", help="Resolution ids printed next to each report item")
    p_resolve.add_argument("--sync", action="store_true", help="Import `- [x]` checkboxes ticked in briefings/")
    p_resolve.add_argument("--list", action="store_true", help="List what is currently acknowledged")
    p_resolve.set_defaults(func=cmd_resolve)

    p_unresolve = sub.add_parser("unresolve", help="Bring resolved leads/alerts/contradictions back into the active list")
    p_unresolve.add_argument("ids", nargs="+", metavar="ID", help="Resolution ids to reopen")
    p_unresolve.set_defaults(func=cmd_unresolve)

    p_reindex = sub.add_parser("reindex", help="Rebuild the search index from disk — no OCR re-run, no model calls")
    p_reindex.add_argument("project", nargs="?", help="Investigation name or slug (omit when inside the project folder)").completer = _project_completer
    p_reindex.set_defaults(func=cmd_reindex)

    p_research = sub.add_parser("research", help="Open Claude Code to research open questions on the web")
    p_research.add_argument("name", nargs="?", help="Investigation name or slug (default: current directory)").completer = _project_completer
    p_research.add_argument("--question", "-q", help="Research question to seed (omit to be prompted)")
    p_research.add_argument("--model", help="Model to use (sonnet/opus/haiku, default: sonnet)")
    p_research.set_defaults(func=cmd_research)

    p_watchlist = sub.add_parser("watchlist", help="Sweep the whole vault against watchlist.md (deterministic, no model)")
    p_watchlist.add_argument("project", nargs="?", help="Investigation name or slug (omit when inside the project folder)").completer = _project_completer
    p_watchlist.set_defaults(func=cmd_watchlist)

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
    _effort_choices = ["low", "medium", "high", "xhigh", "max"]
    _model_help = ("a Claude tier (sonnet/opus/haiku) or a backend:model form "
                   "(claude-api:opus, openai:gpt-5-mini, deepseek:deepseek-v4-flash, "
                   "gemini:gemini-3.1-flash-lite, local:llama-3.3-70b, "
                   "openrouter:anthropic/claude-3.5-sonnet)")
    # Per-stage finalizer model overrides (issue #433): each routes just that post-ingest stage
    # to a different model than --finalizer-model, falling back to it when unset. Shared between
    # `ingest` and `finalize`, which both run post-ingest.
    _finalizer_stage_help = {
        "reconciliation": "merging duplicate entities and flagging contradictions between documents",
        "synthesis": "synthesizing prose for multi-mention entities",
        "timeline": "deduplicating and reconciling timeline collisions",
        "briefing": "writing the briefing",
    }

    def _add_finalizer_stage_flags(p) -> None:
        for stage, what in _finalizer_stage_help.items():
            p.add_argument(f"--finalizer-{stage}-model", default=None,
                           dest=f"finalizer_{stage}_model", metavar="MODEL",
                           help=f"Model for {what} only, overriding --finalizer-model for just "
                                f"this stage — {_model_help}; falls back to --finalizer-model "
                                f"(or finalizer_model) when unset")

    def _add_verify_flag(p) -> None:
        # Three states, not two: --verify and --no-verify are explicit answers that beat the
        # `verify_extraction` config key, and the default None means "whatever configure says".
        # Same shape the model/effort flags use, expressed with a paired store_true/store_false
        # since there is nothing to type after it.
        g = p.add_mutually_exclusive_group()
        g.add_argument("--verify", action="store_true", default=None, dest="verify",
                       help="After extracting each document, re-read it with a cheap second "
                            "call that lists material facts the extraction missed, and add "
                            "them. Costs roughly 15%% more per run; not with a batch extractor "
                            "model. Overrides watchdog configure (default: off).")
        g.add_argument("--no-verify", action="store_false", default=None, dest="verify",
                       help="Skip the second-read verification pass even when watchdog "
                            "configure turns it on.")

    p_ingest = sub.add_parser("ingest", help="Extract queued documents (runs the Python pipeline)")
    p_ingest.add_argument("--extractor-model", default=None, dest="extractor_model", metavar="MODEL",
                          help=f"Model for extraction — {_model_help}; overrides watchdog configure (default: sonnet)")
    p_ingest.add_argument("--finalizer-model", default=None, dest="finalizer_model", metavar="MODEL",
                          help=f"Model for the post-ingest step — reconciling duplicate entities, "
                               f"flagging contradictions, synthesis, timeline, briefing — {_model_help}; "
                               f"overrides watchdog configure (default: haiku)")
    p_ingest.add_argument("--classifier-model", default=None, dest="classifier_model", metavar="MODEL",
                          help=f"Model for document classification — {_model_help}; overrides watchdog configure (default: haiku)")
    p_ingest.add_argument("--extractor-effort", choices=_effort_choices, default=None,
                          dest="extractor_effort",
                          help="Reasoning effort for extraction — lower spends fewer tokens; "
                               "xhigh/max need a supporting model, OpenAI or Claude — "
                               "overrides watchdog configure (default: medium)")
    p_ingest.add_argument("--finalizer-effort", choices=_effort_choices, default=None,
                          dest="finalizer_effort",
                          help="Reasoning effort for the post-ingest step — entity reconciliation, "
                               "contradiction flagging, synthesis, timeline, briefing — "
                               "xhigh/max need a supporting model, OpenAI or Claude — "
                               "overrides watchdog configure (default: high)")
    _add_finalizer_stage_flags(p_ingest)
    _add_verify_flag(p_ingest)
    p_ingest.add_argument("--concurrency", type=int, default=None,
                          help="Documents extracted in parallel — overrides watchdog configure (default: 5)")
    p_ingest.add_argument("--classify-pages", type=int, default=None, dest="classify_pages",
                          help="Pages shown to the document classifier — overrides watchdog configure (default: 5)")
    from watchdog.cmd.ingest import _PICK_SKILL
    p_ingest.add_argument("--skill", nargs="?", const=_PICK_SKILL, default=None, dest="skill",
                          metavar="NAME",
                          help="Pin a record skill for every document, skipping classification. "
                               "Pass a skill name, or use --skill with no value to pick interactively.")
    p_ingest.add_argument("--wait", action="store_true", default=False,
                          help="On a rate limit, sleep until it resets and resume automatically "
                               "instead of stopping for you to re-run ingest. Not with a "
                               "batch-mode extractor model (claude-batch/openai-batch).")
    p_ingest.add_argument("--estimate", action="store_true",
                          help="Print a token/cost estimate for the queue and exit — no lock, no confirm, no extraction")
    p_ingest.add_argument("--estimate-all", action="store_true", dest="estimate_all",
                          help="Like --estimate, but also project the cost across every model in "
                               "the catalog, cheapest first — for comparing providers before "
                               "choosing one")
    p_ingest.add_argument("--skip-briefing", action="store_true", default=False, dest="skip_briefing",
                          help="Run entity reconciliation, synthesis, and the timeline rebuild, "
                               "but skip the briefing model call — useful for bulk backfills or "
                               "re-ingests where the briefing isn't worth the cost every time.")
    p_ingest.add_argument("--force", nargs="*", default=None, dest="force", metavar="DOC",
                          help="Re-extract even when a cached extraction already exists — costs "
                               "full extraction spend on every document. Warns before overwriting "
                               "any note already committed to the vault (default: cancel). Pass "
                               "one or more committed documents (sha256, an unambiguous sha256 "
                               "prefix, or filename) to re-queue and re-extract them too, e.g. "
                               "--force report.pdf 9f2c1a.")
    p_ingest.add_argument("--skip-warning", action="store_true", default=False, dest="skip_warning",
                          help="Skip the 'Public records only' acknowledgement pause — for "
                               "repeated or scripted runs on a corpus already vetted as public. "
                               "Still prints a one-line notice that documents were sent to the model.")
    p_ingest.set_defaults(func=cmd_ingest)

    p_extract = sub.add_parser("dig", help="Classify and extract queued documents; stop before finalize (run watchdog bark next)")
    p_extract.add_argument("--extractor-model", default=None, dest="extractor_model", metavar="MODEL",
                           help=f"Model for extraction — {_model_help}; overrides watchdog configure (default: sonnet)")
    p_extract.add_argument("--classifier-model", default=None, dest="classifier_model", metavar="MODEL",
                           help=f"Model for document classification — {_model_help}; overrides watchdog configure (default: haiku)")
    p_extract.add_argument("--extractor-effort", choices=_effort_choices, default=None,
                           dest="extractor_effort",
                           help="Reasoning effort for extraction — lower spends fewer tokens; "
                                "xhigh/max need a supporting model, OpenAI or Claude — "
                                "overrides watchdog configure (default: medium)")
    _add_verify_flag(p_extract)
    p_extract.add_argument("--concurrency", type=int, default=None,
                           help="Documents extracted in parallel — overrides watchdog configure (default: 5)")
    p_extract.add_argument("--classify-pages", type=int, default=None, dest="classify_pages",
                           help="Pages shown to the document classifier — overrides watchdog configure (default: 5)")
    p_extract.add_argument("--skill", nargs="?", const=_PICK_SKILL, default=None, dest="skill",
                           metavar="NAME",
                           help="Pin a record skill for every document, skipping classification. "
                                "Pass a skill name, or use --skill with no value to pick interactively.")
    p_extract.add_argument("--wait", action="store_true", default=False,
                           help="On a rate limit, sleep until it resets and resume automatically "
                                "instead of stopping for you to re-run dig. Not with a "
                                "batch-mode extractor model (claude-batch/openai-batch).")
    p_extract.add_argument("--estimate", action="store_true",
                           help="Print a token/cost estimate for the queue and exit — no lock, no confirm, no extraction")
    p_extract.add_argument("--estimate-all", action="store_true", dest="estimate_all",
                           help="Like --estimate, but also project the cost across every model in "
                                "the catalog, cheapest first — for comparing providers before "
                                "choosing one")
    p_extract.add_argument("--force", action="store_true", default=False, dest="force",
                           help="Re-extract even when a cached extraction already exists — costs "
                                "full extraction spend on every document. Nothing is committed to "
                                "the vault by `dig`, so this needs no overwrite warning.")
    p_extract.add_argument("--skip-warning", action="store_true", default=False, dest="skip_warning",
                           help="Skip the 'Public records only' acknowledgement pause — for "
                                "repeated or scripted runs on a corpus already vetted as public. "
                                "Still prints a one-line notice that documents were sent to the model.")
    p_extract.set_defaults(func=cmd_extract)

    p_finalize = sub.add_parser("bark", help="Complete post-ingest (entity reconciliation, synthesis, timeline, briefing) for an already-extracted batch — e.g. after a rate limit stopped it")
    p_finalize.add_argument("--finalizer-model", default=None, dest="finalizer_model", metavar="MODEL",
                            help=f"Model for the post-ingest step — reconciling duplicate entities, "
                                 f"flagging contradictions, synthesis, timeline, briefing — {_model_help}; "
                                 f"overrides watchdog configure (default: haiku)")
    p_finalize.add_argument("--finalizer-effort", choices=_effort_choices, default=None,
                            dest="finalizer_effort",
                            help="Reasoning effort for the post-ingest step — entity reconciliation, "
                                 "contradiction flagging, synthesis, timeline, briefing — "
                                 "xhigh/max need a supporting model, OpenAI or Claude — "
                                 "overrides watchdog configure (default: high)")
    _add_finalizer_stage_flags(p_finalize)
    p_finalize.add_argument("--estimate", action="store_true",
                            help="Print a token/cost estimate for the pending batch and exit — "
                                 "no lock, no finalize")
    p_finalize.add_argument("--estimate-all", action="store_true", dest="estimate_all",
                            help="Like --estimate, but also project the cost across every model in "
                                 "the catalog, cheapest first — for comparing providers before "
                                 "choosing one")
    p_finalize.add_argument("--skip-briefing", action="store_true", default=False, dest="skip_briefing",
                            help="Run entity reconciliation, synthesis, and the timeline rebuild, "
                                 "but skip the briefing model call — useful for bulk backfills or "
                                 "re-ingests where the briefing isn't worth the cost every time.")
    p_finalize.set_defaults(func=cmd_finalize)

    p_context = sub.add_parser("context", help="Open Claude Code to seed investigation context from _CONTEXT/")
    p_context.add_argument("name", nargs="?", help="Investigation name or slug (default: current directory)").completer = _project_completer
    p_context.add_argument("--model", choices=_model_choices, default="sonnet",
                           help="Model to use (default: sonnet)")
    p_context.set_defaults(func=cmd_context)

    p_auth = sub.add_parser("auth", help="Show and interactively change how Watchdog authenticates to model providers")
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
            if interactive.confirm("  Run setup now?", default=True):
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

    # `ingest` combined extract+finalize into one shot; retired in favour of two clearer paths
    # — the guided `watchdog` walk, or manual `watchdog dig` + `watchdog bark` (#441, D138).
    # No renamed successor to remap onto, so it keeps its own subparser and just warns here.
    if args.command == "ingest":
        print(f"\n  {_YELLOW}Warning:{_RESET} {_CYAN}watchdog ingest{_RESET}{_DIM} is deprecated — "
              f"use {_RESET}{_CYAN}watchdog{_RESET}{_DIM} for the guided walk, or {_RESET}"
              f"{_CYAN}watchdog dig{_RESET}{_DIM} then {_RESET}{_CYAN}watchdog bark{_RESET}"
              f"{_DIM} for manual control.{_RESET}")

    code = exit_code_for(args.func(args))
    if code:
        sys.exit(code)


if __name__ == "__main__":
    main()
