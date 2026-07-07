"""Shared constants, path globals, and utility helpers used across cmd modules."""

import json
import os
import subprocess
import sys
from collections import Counter  # noqa: F401 — re-exported for cmd modules
from datetime import datetime, timezone
from pathlib import Path

from watchdog.pipeline.write_vault import slugify  # noqa: F401 — re-exported

WATCHDOG_HOME = Path.home() / ".watchdog"
PROJECTS_FILE = WATCHDOG_HOME / "projects.json"
CONFIG_FILE   = WATCHDOG_HOME / "config.json"

_BOLD   = "\033[1m"
_DIM    = "\033[2m"
_CYAN   = "\033[0;36m"
_YELLOW = "\033[0;33m"
_GREEN  = "\033[0;32m"
_RESET  = "\033[0m"

VAULT_SCHEMA_VERSION = "1"

_MODEL_IDS = {
    "sonnet": "claude-sonnet-4-6",
    "opus":   "claude-opus-4-8",
    "haiku":  "claude-haiku-4-5-20251001",
}

_ALIASES = {
    "init":       "new",
    "create":     "new",
    "ls":         "list",
    "info":       "status",
    "inspect":    "status",
    "version":    "about",
    "config":     "configure",
    "setting":    "configure",
    "settings":   "configure",
    "find":       "search",
    "health":     "doctor",
    "check":      "doctor",
    "telemetry":  "usage",
    "process":    "chew",
    "preprocess": "chew",
    "prep":       "chew",
    "remove":     "delete",
    "rm":         "delete",
    "mv":         "move",
    "rn":         "rename",
}

# The orchestrator (pipeline/orchestrate.py) drives extraction in Python and calls
# preflight/postflight/synthesis_bundle/section/merge/abort as functions, so those no
# longer need CLI registrations. What remains are commands the in-Claude-Code skills
# (e.g. /watchdog-entity) still shell out to.
_PIPELINE_COMMANDS = {
    "near-dup":      ("watchdog.pipeline.near_dup",       "watchdog-near-dup"),
    "write-vault":   ("watchdog.pipeline.write_vault",    "watchdog-write-vault"),
    "write-entity":  ("watchdog.pipeline.write_entity",   "watchdog-write-entity"),
}

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates" / "vault"

_VAULT_PERMISSIONS = [
    # watchdog commands the in-Claude-Code skills run (extraction is now a Python
    # command — `watchdog ingest` — and needs no in-vault Bash permissions).
    "Bash(watchdog entity-index)",
    "Bash(watchdog queue-status)",
    "Bash(watchdog is-duplicate *)",
    "Bash(watchdog write-entity --entity-id *)",
    "Bash(watchdog unlock*)",
    "Bash(watchdog timeline)",
    # /watchdog-context proposes watchlist seed terms (#229); the deterministic append+dedup
    # lives in this command, not the skill hand-editing watchlist.md.
    "Bash(watchdog watchlist-add *)",
    # /watchdog-surface promotes a journalist-confirmed contradiction candidate into the note
    # via this internal command (#312, D82/D83) — pre-approved so the confirmed promotion runs
    # without a second permission prompt; the journalist's explicit confirmation is the gate.
    "Bash(watchdog contradiction-add *)",
    # WebSearch / WebFetch are deliberately NOT here — they make outbound requests, so they are
    # pre-approved only by the watchdog-research skill's own `allowed-tools` frontmatter, scoped to
    # when /watchdog-research is active. Archival downloads run as a deterministic post-flight of
    # `watchdog research` (in the terminal, ungated), never from the skill (#186, D45).
    # internal vault state
    "Write(.watchdog/tmp/**)",
    "Edit(.watchdog/tmp/**)",
    # durable web-research worklist (#196) — the skill writes queued URLs here (not tmp/, which
    # setup sweeps), so a crashed session's queue survives.
    "Write(.watchdog/research/**)",
    "Edit(.watchdog/research/**)",
    "Write(.watchdog/Registry/**)",
    "Edit(.watchdog/Registry/**)",
    "Write(.watchdog/timeline/**)",
    "Edit(.watchdog/timeline/**)",
    # session-authored pages (compounding queries → wiki threads)
    "Write(queries/**)",
    "Edit(queries/**)",
    "Write(wiki/**)",
    "Edit(wiki/**)",
    # post-ingest output files
    "Write(briefings/**)",
    "Write(entities/**)",
    "Edit(entities/**)",
    "Write(documents/**)",
    "Edit(documents/**)",
    "Write(morgue/**)",
    "Write(hot.md)",
    "Edit(hot.md)",
    "Write(log.md)",
    "Edit(log.md)",
    "Write(context.md)",
    "Edit(context.md)",
    "Edit(.obsidian/graph.json)",
]

_CMD_HELP: dict[str, dict] = {
    "register": {
        "desc": "Register an existing vault folder with watchdog",
        "args": [("path", "Path to the existing vault folder")],
        "opts": [("--name NAME", "Investigation name (omit to be prompted)")],
    },
    "new": {
        "desc": "Create a new investigation vault",
        "args": [("name", "Investigation name (e.g. 'Shell Company Investigation')")],
        "opts": [("--dir DIR", "Parent directory (default: projects_dir from config)")],
    },
    "ingest": {
        "desc": "Extract queued documents (runs the Python pipeline)",
        "opts": [
            ("--extractor-model M",  "Override the extraction model for this run — a tier (sonnet/opus/haiku) or backend:model (e.g. deepseek:deepseek-v4-flash); default from watchdog configure"),
            ("--finalizer-model M",  "Override the synthesis + timeline + briefing model for this run — tier or backend:model; default from watchdog configure"),
            ("--classifier-model M", "Override the document-classification model for this run — tier or backend:model; default from watchdog configure"),
            ("--extractor-effort E", "Reasoning effort for extraction (low/medium/high) — lower spends fewer tokens; default from watchdog configure"),
            ("--finalizer-effort E", "Reasoning effort for synthesis + timeline + briefing (low/medium/high); default from watchdog configure"),
            ("--concurrency N",      "Documents extracted in parallel for this run (default from watchdog configure: 5)"),
            ("--classify-pages N",   "Pages shown to the document classifier for this run (default from watchdog configure: 5)"),
            ("--skill [NAME|PATH]",  "Pin a record skill (name or file path) for every document, skipping classification (no value = pick from the list)"),
            ("--wait",               "On a rate limit, sleep until it resets and resume automatically instead of stopping for you to re-run ingest. Not with a claude-batch extractor model"),
        ],
    },
    "context": {
        "desc": "Open Claude Code to seed investigation context from _CONTEXT/",
        "args": [("name", "Investigation name or slug (default: current directory)", True)],
        "opts": [("--model M", "Model to use (sonnet/opus/haiku, default: sonnet)")],
    },
    "obsidian": {
        "desc": "Open an investigation vault in Obsidian",
        "args": [("name", "Investigation name or slug")],
    },
    "open": {
        "desc": "Open vault folder in Finder / file explorer",
        "args": [("name", "Investigation name or slug (default: current directory)")],
    },
    "archive": {
        "desc": "Archive a completed investigation (hidden from watchdog list)",
        "args": [("name", "Investigation name or slug")],
    },
    "unarchive": {
        "desc": "Restore an archived investigation",
        "args": [("name", "Investigation name or slug")],
    },
    "rename": {
        "desc": "Rename an investigation (folder and registry)",
        "args": [
            ("project", "Investigation name or slug (omit when inside the project folder)", True),
            ("name",    "New name (omit to be prompted)", True),
        ],
    },
    "describe": {
        "desc": "Set or update an investigation description",
        "args": [
            ("project", "Investigation name or slug (omit when inside the project folder)", True),
            ("text",    "New description text (omit to be prompted)", True),
        ],
    },
    "move": {
        "desc": "Update vault path in registry",
        "args": [("name", "Investigation name or slug"), ("path", "New path for the vault")],
    },
    "delete": {
        "desc": "Remove an investigation from registry",
        "args": [("name", "Investigation name or slug")],
        "opts": [("--purge", "Also permanently delete all vault files from disk")],
    },
    "chew": {
        "desc": "Process documents in _INCOMING/ and prepare them for ingestion",
        "args": [("file", "Specific file to chew (omit to chew all of _INCOMING/)", True)],
        "opts": [
            ("--chew-workers N",  "Parallel file workers (overrides chew_workers in watchdog configure)"),
            ("--chunk-workers N", "Parallel chunk workers per file, for large PDFs (overrides chunk_workers)"),
        ],
    },
    "watch": {
        "desc": "Watch _INCOMING/ and chew files automatically as they arrive",
        "args": [("name", "Investigation name or slug")],
    },
    "log": {
        "desc": "Show ingest history for an investigation",
        "args": [("name", "Investigation name or slug")],
        "opts": [("--lines N", "Number of lines to show (default: all)")],
    },
    "list": {
        "desc": "List all registered investigations",
        "opts": [("--all", "Include archived investigations")],
    },
    "status": {
        "desc": "Show detailed status for an investigation",
        "args": [("name", "Investigation name or slug (omit to show all)")],
    },
    "search": {
        "desc": "Semantic search across ingested documents",
        "args": [
            ("project", "Investigation name or slug (omit when inside the project folder)", True),
            ("query",   "Search query (supports +/- phrases — see Notes)"),
        ],
        "opts": [
            ("--top N", "Results to return per section (default: 5)"),
            ("--threshold S", "Hide results scoring below S (0.0–1.0); ~0.5 keeps strong matches"),
            ("--full", "Print the complete passage/note instead of a truncated snippet"),
        ],
        "notes": [
            "Searches by meaning, not keywords: \"conflict of interest\" surfaces passages about",
            "recusals or related-party dealings even when that phrase never appears. Returns the",
            "matching source passage with its page — not a generated answer.",
            "",
            "Steer with +/-: lead a phrase with - to push away from it, + to pull toward another",
            "idea. The whole phrase up to the next +/- is one term (no quotes needed); a hyphenated",
            "word like no-bid stays intact.",
            "    watchdog search \"shell company -real estate\"",
            "    watchdog search \"consulting fee +offshore -salary\"",
            "",
            "Scores run 0–1 and are relative: a strong conceptual match sits around 0.5–0.65,",
            "below ~0.4 is usually noise. There's no universal cutoff — tune --threshold to your",
            "corpus. (A +/- query shifts the scale lower, so judge those by ranking, not score.)",
        ],
    },
    "leads": {
        "desc": "Surface investigative leads from the entity graph (deterministic, no model)",
        "args": [("project", "Investigation name or slug (omit when inside the project folder)", True)],
        "notes": [
            "Reads the entity registry and reports, with no model call: entities named as a",
            "relationship target but never profiled, entities recurring across documents with no",
            "relationships, and entities carrying unresolved contradiction flags.",
            "",
            "The same sweep runs at the end of every `watchdog ingest`, writing the full report",
            "to briefings/leads-<date>.md; this command re-runs it on demand between ingests.",
        ],
    },
    "merge-entities": {
        "desc": "Merge a duplicate entity into another, deterministically",
        "args": [
            ("keep-id",  "Entity id to keep (the survivor)"),
            ("merge-id", "Entity id to merge away (folded into keep-id)"),
        ],
        "opts": [("--force", "Skip the confirmation prompt")],
        "notes": [
            "Must be run from inside the vault. Unions aliases, appears_in, roles, and timeline",
            "events onto keep-id; remaps every role.target_id across the whole registry that",
            "pointed at merge-id (not just the two entities involved); concatenates the losing",
            "note's Analysis into the survivor's with provenance intact; and redirects the losing",
            "note to a stub linking to the survivor. No model calls.",
            "",
            "Prints both entities (name, type, document/relationship counts) and asks for",
            "confirmation before doing anything — this is irreversible. Answering anything other",
            "than y/yes cancels with no changes made; pass --force to skip the prompt.",
            "",
            "This is the fix for what the dashboard's \"Possible duplicates\" view and",
            "`/watchdog-health`'s near-duplicate check can only ever flag. Run `watchdog reindex`",
            "afterward to drop the merged entity's stale search-index entries.",
        ],
    },
    "contradiction-add": {
        "desc": "Promote a verified surface-found contradiction into an entity note",
        "args": [("entity-id", "Entity id the contradiction belongs to")],
        "opts": [
            ("--label TEXT",  "Short label for the disputed fact"),
            ("--a VALUE",     "First (existing) value"),
            ("--a-doc SLUG",  "Document slug the first value comes from"),
            ("--a-page N",    "Page number for the first value (optional)"),
            ("--b VALUE",     "Second (conflicting) value"),
            ("--b-doc SLUG",  "Document slug the second value comes from"),
            ("--b-page N",    "Page number for the second value (optional)"),
        ],
        "notes": [
            "Must be run from inside the vault. `/watchdog-surface` reports cross-document",
            "contradictions as labelled candidates rather than writing callouts into entity",
            "notes, which are pipeline-owned (D81). Once you have verified a candidate against",
            "the sources, this writes it into the entity's ## Contradictions section through the",
            "pipeline's own note builder, in the exact format extraction emits — so the callout",
            "is tracked by the resolutions layer and `watchdog resolve` / `unresolve` work on it",
            "like any pipeline-emitted one. No model calls.",
            "",
            "Validates that the entity id and both document slugs exist before writing; a callout",
            "already present is a no-op. `/watchdog-surface` can run this after explicit",
            "journalist confirmation when promoting a candidate.",
        ],
    },
    "research": {
        "desc": "Open Claude Code to research the vault's open questions on the web",
        "args": [("name", "Investigation name or slug (default: current directory)", True)],
        "opts": [
            ("--question Q, -q Q", "Research question to seed (omit to be prompted)"),
            ("--model M",          "Model to use (sonnet/opus/haiku, default: sonnet)"),
        ],
        "notes": [
            "Seeded by the vault's entities, leads, and gaps, Claude conducts bounded web research",
            "and queues the sources it finds; when the session ends, watchdog downloads them into",
            "_INCOMING/ — so findings flow through the normal chew → ingest pipeline. Claude never",
            "writes vault notes directly. After the download, run `watchdog chew` then",
            "`watchdog ingest` to fold the sources into the vault.",
        ],
    },
    "watchlist": {
        "desc": "Sweep the whole vault against watchlist.md (deterministic, no model)",
        "args": [("project", "Investigation name or slug (omit when inside the project folder)", True)],
        "notes": [
            "Reads every document already in documents.json — not just the current run's — and",
            "scans each one's morgue text against watchlist.md, exactly like the per-ingest scan",
            "(D35). For when a term is added to the watchlist after documents were already",
            "ingested and you want the whole vault swept, not just what's ingested from now on.",
            "",
            "Writes to the same briefings/alerts-<date>.md as the per-run scan (appending if the",
            "file already exists). Since it has no memory of prior scans, a full sweep re-reports",
            "every past hit each time it runs — expected, not a bug.",
        ],
    },
    "fetch": {
        "desc": "Download a batch of URLs (or a links file) into _INCOMING/",
        "args": [("URL|FILE", "One or more URLs, or the path to a links file (one URL per line, or the "
                  "tab-separated url⇥title⇥source_type⇥relevance form)")],
        "opts": [("--project P", "Investigation name or slug (default: current directory)")],
        "notes": [
            "For when you already have a list of links — from a spreadsheet, a colleague, your own",
            "browsing — and just want them pulled into the pipeline, no research session needed. Each",
            "URL runs through the same egress hygiene as research sources (public host only, size cap,",
            "scripts stripped) and lands as a document + provenance sidecar. Then run `watchdog chew`",
            "and `watchdog ingest`. Archives to the Wayback Machine too when wayback_save is on.",
        ],
    },
    "usage": {
        "desc": "Per-call token/cost/latency breakdown for ingest runs (deterministic, no model)",
        "args": [("project", "Investigation name or slug (omit when inside the project folder)", True)],
        "opts": [
            ("--all",         "Compare every run recorded in the vault instead of showing just the latest"),
            ("--run TIMESTAMP", "Analyze one specific past run instead of the latest"),
        ],
        "notes": [
            "Reads `.watchdog/Registry/usage/usage-<ts>.json`, written after every `watchdog",
            "ingest`/`watchdog finalize` run, and groups calls by stage (classifier/extractor/",
            "finalizer, matching the CLI's own --classifier-model/--extractor-model/",
            "--finalizer-model flags). Extractor rows show the filename and page range (or",
            "section) each call covered.",
            "",
            "Cost is read directly from each record — model_client computes cost_usd",
            "authoritatively at call time, so there is no local pricing table to keep in sync.",
            "Also reports each call's wall-clock latency, and cost per page across the vault's",
            "whole document registry (not just the run being analyzed).",
        ],
    },
    "export": {
        "desc": "Export the entity/relationship graph for Neo4j, Gephi, or NetworkX",
        "args": [("project", "Investigation name or slug (omit when inside the project folder)", True)],
        "opts": [
            ("--output DIR",     "Output directory (default: <slug>-export/)"),
            ("--format FMT",     "csv (default) or cypher"),
        ],
        "notes": [
            "Reads the entity registry and emits a graph — no model calls, fully deterministic.",
            "csv writes nodes.csv + relationships.csv for `neo4j-admin database import` (also",
            "loadable in Gephi); cypher writes a single graph.cypher of MERGE statements.",
            "",
            "Only stated-direction relationships are emitted (the auto-generated reverse edges are",
            "skipped), and edges to entities that were never profiled are dropped so the import",
            "stays valid. Graph quality is bounded by ingest-time entity deduplication.",
        ],
    },
    "timeline": {
        "desc": "Rebuild timeline.md from canonical .watchdog/timeline/ files",
        "args": [("name", "Investigation name or slug (default: current directory)", True)],
    },
    "unlock": {
        "desc": "Release a stale chew or ingest lock",
        "args": [("project", "Investigation name or slug")],
        "opts": [("--force", "Remove lock even if recent")],
    },
    "setup": {
        "desc": "Set up Watchdog after installation",
        "opts": [("--force", "Re-run setup even if already complete")],
    },
    "configure": {
        "desc": "View or change configuration",
        "args": [("key", "Configuration key (optional)"), ("value", "Value to set (optional)")],
    },
    "doctor": {
        "desc": "Check all registered investigations for missing or broken vaults",
    },
    "about": {
        "desc": "Show version and project links",
    },
}


def _perf_cpu_count() -> int:
    """Performance core count on Apple Silicon; total core count everywhere else."""
    try:
        r = subprocess.run(
            ["sysctl", "-n", "hw.perflevel0.logicalcpu"],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode == 0:
            n = int(r.stdout.strip())
            if n > 0:
                return n
    except Exception:
        pass
    return os.cpu_count() or 4


def _render_template(filename: str, **vars: str) -> str:
    text = (_TEMPLATES_DIR / filename).read_text()
    for key, value in vars.items():
        text = text.replace("{" + key + "}", value)
    return text


def load_projects() -> dict:
    if not PROJECTS_FILE.exists():
        return {}
    with open(PROJECTS_FILE) as f:
        return json.load(f)


def _project_completer(prefix, parsed_args, **kwargs):
    return {slug: info["name"] for slug, info in load_projects().items()}


def save_projects(projects: dict) -> None:
    WATCHDOG_HOME.mkdir(parents=True, exist_ok=True)
    with open(PROJECTS_FILE, "w") as f:
        json.dump(projects, f, indent=2)
        f.write("\n")


def _projects_dir() -> Path:
    if CONFIG_FILE.exists():
        config = json.loads(CONFIG_FILE.read_text())
        return Path(config["projects_dir"]).expanduser()
    return Path.home() / "Investigations"


def _fmt_date(iso: str) -> str:
    try:
        return iso[:10]
    except Exception:
        return "—"


def _vault_size(vault: Path) -> int:
    total = 0
    for root, _, files in os.walk(vault):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return total


def _fmt_size(n: int) -> str:
    for unit, threshold in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= threshold:
            return f"{n / threshold:.1f}{unit}"
    return f"{n}B"


def _check_project_health(info: dict) -> str | None:
    vault = Path(info["path"])
    if not vault.exists():
        return "folder not found"
    if not (vault / ".watchdog").exists():
        return "not a watchdog vault"
    return None


def _load_registry(vault: Path) -> dict | None:
    reg = vault / ".watchdog" / "Registry" / "registry.json"
    if not reg.exists():
        return None
    try:
        return json.loads(reg.read_text())
    except Exception:
        return None


def _count_incoming(vault: Path) -> int:
    incoming = vault / "_INCOMING"
    if not incoming.exists():
        return 0
    count = 0
    for root, dirs, files in os.walk(incoming):
        rel_parts = Path(root).relative_to(incoming).parts
        if any(p in ("_FAILED", "_failed", "_SKIPPED", "_skipped") for p in rel_parts):
            dirs.clear()
            continue
        count += sum(1 for f in files if not f.startswith(".") and not f.endswith(".yml"))
    return count


def _count_queued(vault: Path) -> int:
    queue = vault / ".watchdog" / "queue"
    if not queue.exists():
        return 0
    return sum(1 for f in queue.iterdir() if f.suffix == ".json")


def _warn_pending_research(vault: Path) -> None:
    """Warn when web-research URLs are queued but not downloaded (#196). A crashed research session
    leaves them in the durable worklist; bare `watchdog`, `chew`, and `status` surface them so they
    aren't silently lost. No-op when none are pending."""
    from watchdog.pipeline import research
    n = research.pending_count(vault)
    if n:
        print(f"  {_YELLOW}{n} research URL{'s' if n != 1 else ''}{_RESET} queued but not downloaded "
              f"{_DIM}— run{_RESET} {_CYAN}watchdog research-fetch{_RESET}")


def _resolve_vault(project: str | None) -> tuple[str, dict, Path]:
    """(slug, info, vault_path) from a name arg, or the current directory.

    Used by read-only, whole-vault commands (export, leads): a name resolves through the
    registry; with no name the cwd must itself be a vault. A vault opened by path that isn't
    registered still works — it falls back to a synthetic info dict keyed on the folder name."""
    if project:
        slug, info = _find_project(project)
        return slug, info, Path(info["path"])

    cwd = Path(".").resolve()
    if not (cwd / ".watchdog").is_dir():
        sys.exit("Error: not inside a Watchdog vault — provide an investigation name.")
    for slug, info in load_projects().items():
        if Path(info["path"]).resolve() == cwd:
            return slug, info, cwd
    return slugify(cwd.name), {"name": cwd.name, "path": str(cwd)}, cwd


def _find_project(name: str) -> tuple[str, dict]:
    projects = load_projects()
    slug = slugify(name)
    if slug not in projects:
        matches = [k for k in projects if k.startswith(slug)]
        if len(matches) == 1:
            slug = matches[0]
        elif len(matches) > 1:
            sys.exit(f"Ambiguous name — matches: {', '.join(sorted(matches))}")
        else:
            sys.exit(f"Project not found: {name}\nRun 'watchdog list' to see all projects.")
    return slug, projects[slug]


def _notify(title: str, body: str) -> None:
    if sys.platform != "darwin":
        return
    try:
        subprocess.run(
            ["osascript", "-e", f'display notification "{body}" with title "{title}"'],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass


def _launch_claude(vault: Path, prompt: str | None = None, model: str | None = None) -> None:
    try:
        os.chdir(vault)
        cmd = ["claude"]
        if model:
            cmd += ["--model", _MODEL_IDS.get(model, model)]
        if prompt:
            cmd.append(prompt)
        os.execvp("claude", cmd)
    except FileNotFoundError:
        sys.exit("Error: Claude Code not found — install from https://claude.ai/download")


def _check_vault_locks(vault: Path, slug: str) -> None:
    chew_lock   = vault / ".watchdog" / ".chew-lock"
    ingest_lock = vault / ".watchdog" / "Registry" / ".ingest-lock"
    if chew_lock.exists():
        sys.exit(f"Error: chew is in progress. Wait for it to finish or run: watchdog unlock {slug}")
    if ingest_lock.exists():
        sys.exit(f"Error: ingest is in progress. Wait for it to finish or run: watchdog unlock {slug}")


def _print_cmd_help(cmd: str) -> None:
    info = _CMD_HELP.get(cmd, {})
    arg_defs = info.get("args", [])
    opts = info.get("opts", [])
    usage_parts = ["watchdog", cmd]
    for a in arg_defs:
        name, optional = a[0], (len(a) > 2 and a[2])
        usage_parts.append(f"[{name}]" if optional else f"<{name}>")
    if opts:
        usage_parts.append("[options]")
    print(f"\n  {info.get('desc', '')}")
    print()
    print(f"  {_DIM}Usage:  {' '.join(usage_parts)}{_RESET}")
    if arg_defs:
        print()
        print(f"  {_BOLD}Arguments{_RESET}")
        for a in arg_defs:
            name, desc = a[0], a[1]
            optional = len(a) > 2 and a[2]
            note = "  (optional)" if optional else ""
            print(f"    {_CYAN}{name:<18}{_RESET} {desc}{note}")
    print()
    print(f"  {_BOLD}Options{_RESET}")
    for flag, desc in opts:
        print(f"    {_CYAN}{flag:<18}{_RESET} {desc}")
    print(f"    {_CYAN}{'--help':<18}{_RESET} Show this message and exit")
    notes = info.get("notes", [])
    if notes:
        print()
        print(f"  {_BOLD}Notes{_RESET}")
        for line in notes:
            print(f"    {_DIM}{line}{_RESET}")
    print()


def _print_banner() -> None:
    print(f"\n  🔍🐕  {_BOLD}Watchdog{_RESET} — investigative document intelligence")
    print()
    print(f"  {_DIM}Usage:  watchdog <command> [options]{_RESET}")
    print()
    groups = [
        ("Manage investigations", [
            ("new",        "Create a new investigation vault"),
            ("register",   "Register an existing vault folder"),
            ("obsidian",   "Open in Obsidian"),
            ("open",       "Open vault folder in Finder / file explorer"),
            ("archive",    "Archive a completed investigation"),
            ("unarchive",  "Restore an archived investigation"),
            ("rename",     "Rename an investigation"),
            ("move",       "Move vault to a new path"),
            ("delete",     "Remove an investigation from registry"),
        ]),
        ("Document processing", [
            ("fetch",            "Download a batch of URLs into _INCOMING/"),
            ("chew",             "Process documents in _INCOMING/"),
            ("ingest",           "Extract queued documents into the vault"),
            ("context",          "Seed investigation context from _CONTEXT/"),
            ("watch",            "Watch _INCOMING/ and chew files automatically"),
            ("log",              "Show ingest history"),
            ("timeline",         "Rebuild timeline.md from canonical timeline files"),
        ]),
        ("Investigate", [
            ("search",     "Semantic search across ingested documents"),
            ("leads",      "Surface investigative leads from the entity graph"),
            ("merge-entities", "Merge a duplicate entity into another"),
            ("watchlist",  "Sweep the whole vault against watchlist.md"),
            ("research",   "Research open questions on the web (downloads into _INCOMING/)"),
        ]),
        ("Info", [
            ("list",       "List all investigations"),
            ("status",     "Show detailed status"),
            ("export",     "Export the knowledge graph (Neo4j / Gephi / Cypher)"),
            ("usage",      "Per-call token/cost/latency breakdown for ingest runs"),
            ("doctor",     "Check for missing or broken vaults"),
        ]),
        ("Settings", [
            ("setup",      "Set up Watchdog after installation"),
            ("configure",  "View or change configuration"),
            ("auth",       "Manage API keys for model backends"),
            ("about",      "Show version and project links"),
        ]),
    ]
    for group_name, cmds in groups:
        print(f"  {_BOLD}{group_name}{_RESET}")
        for cmd, desc in cmds:
            print(f"    {_CYAN}{cmd:<15}{_RESET} {desc}")
        print()
