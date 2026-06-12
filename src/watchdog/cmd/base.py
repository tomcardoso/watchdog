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
    "process":    "chew",
    "preprocess": "chew",
    "prep":       "chew",
    "remove":     "delete",
    "rm":         "delete",
    "mv":         "move",
    "rn":         "rename",
}

_PIPELINE_COMMANDS = {
    "near-dup":    ("watchdog.pipeline.near_dup",     "watchdog-near-dup"),
    "write-vault": ("watchdog.pipeline.write_vault",  "watchdog-write-vault"),
    "write-entity":("watchdog.pipeline.write_entity", "watchdog-write-entity"),
}

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates" / "vault"

_VAULT_PERMISSIONS = [
    # watchdog pipeline commands (subagent-facing)
    "Bash(watchdog pre-flight *)",
    "Bash(watchdog post-flight *)",
    "Bash(watchdog entity-index)",
    "Bash(watchdog queue-status)",
    "Bash(watchdog is-duplicate *)",
    "Bash(watchdog write-entity --entity-id *)",
    "Bash(watchdog unlock)",
    # shell utilities
    "Bash(find .watchdog/queue/ *)",
    # internal vault state
    "Write(/.watchdog/tmp/**)",
    "Edit(/.watchdog/tmp/**)",
    "Edit(/.watchdog/Registry/**)",
    "Write(/.watchdog/Registry/**)",
    # post-ingest output files
    "Write(/briefings/**)",
    "Write(/hot.md)",
    "Edit(/hot.md)",
    "Write(/log.md)",
    "Edit(/log.md)",
]

_CMD_HELP: dict[str, dict] = {
    "new": {
        "desc": "Create a new investigation vault",
        "args": [("name", "Investigation name (e.g. 'Shell Company Investigation')")],
        "opts": [("--dir DIR", "Parent directory (default: projects_dir from config)")],
    },
    "ingest": {
        "desc": "Set up extraction session and open in Claude Code",
        "opts": [
            ("--orchestrator-model M", "Model for the orchestrator session (sonnet/opus/haiku, default: sonnet)"),
            ("--extractor-model M",    "Model for extraction subagents (sonnet/haiku, default: sonnet)"),
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
        "args": [("project", "Investigation name or slug"), ("name", "New name")],
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
            ("query",   "Search query"),
        ],
        "opts": [("--top N", "Number of results to return (default: 5)")],
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
        if any(p in ("_FAILED", "_failed") for p in rel_parts):
            dirs.clear()
            continue
        count += sum(1 for f in files if not f.startswith(".") and not f.endswith(".yml"))
    return count


def _count_queued(vault: Path) -> int:
    queue = vault / ".watchdog" / "queue"
    if not queue.exists():
        return 0
    return sum(1 for f in queue.iterdir() if f.suffix == ".json")


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
    print()


def _print_banner() -> None:
    print(f"\n  🔍🐕  {_BOLD}Watchdog{_RESET} — investigative document intelligence")
    print()
    print(f"  {_DIM}Usage:  watchdog <command> [options]{_RESET}")
    print()
    groups = [
        ("Investigation", [
            ("new",        "Create a new investigation vault"),
            ("obsidian",   "Open in Obsidian"),
            ("open",       "Open vault folder in Finder / file explorer"),
            ("archive",    "Archive a completed investigation"),
            ("unarchive",  "Restore an archived investigation"),
            ("rename",     "Rename an investigation"),
            ("move",       "Move vault to a new path"),
            ("delete",     "Remove an investigation from registry"),
        ]),
        ("Processing", [
            ("chew",       "Process documents in _INCOMING/"),
            ("ingest",     "Set up extraction session and open in Claude Code"),
            ("context",    "Seed investigation context from _CONTEXT/"),
            ("watch",      "Watch _INCOMING/ and chew files automatically"),
            ("log",        "Show ingest history"),
        ]),
        ("Info", [
            ("list",       "List all investigations"),
            ("status",     "Show detailed status"),
            ("search",     "Semantic search across ingested documents"),
        ]),
        ("Settings", [
            ("setup",      "Set up Watchdog after installation"),
            ("configure",  "View or change configuration"),
            ("about",      "Show version and project links"),
        ]),
    ]
    for group_name, cmds in groups:
        print(f"  {_BOLD}{group_name}{_RESET}")
        for cmd, desc in cmds:
            print(f"    {_CYAN}{cmd:<12}{_RESET} {desc}")
        print()
