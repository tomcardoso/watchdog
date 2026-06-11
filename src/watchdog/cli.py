"""watchdog — investigative journalism document intelligence CLI"""

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

WATCHDOG_HOME = Path.home() / ".watchdog"
PROJECTS_FILE = WATCHDOG_HOME / "projects.json"
CONFIG_FILE   = WATCHDOG_HOME / "config.json"


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

_ALIASES = {
    "init":       "new",
    "create":     "new",
    "ls":         "list",
    "info":       "status",
    "inspect":    "status",
    "cd":         "open",
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

_TEMPLATES_DIR = Path(__file__).parent / "templates" / "vault"


def _render_template(filename: str, **vars: str) -> str:
    text = (_TEMPLATES_DIR / filename).read_text()
    for key, value in vars.items():
        text = text.replace("{" + key + "}", value)
    return text

_VAULT_PERMISSIONS = [
    "Bash(watchdog entity-index)",
    "Bash(watchdog queue-status)",
    "Bash(watchdog is-duplicate *)",
    "Bash(watchdog near-dup *)",
    "Bash(watchdog validate-extraction *)",
    "Bash(watchdog write-vault *)",
    "Bash(find .watchdog/queue/ *)",
    "Bash(rm .watchdog/tmp/*)",
    "Bash(rm .watchdog/queue/*.json)",
    "Bash(rm .watchdog/Registry/.ingest-lock)",
    "Bash(date *)",
    "Write(/.watchdog/tmp/**)",
    "Write(/.watchdog/Registry/**)",
]

_BOLD   = "\033[1m"
_DIM    = "\033[2m"
_CYAN   = "\033[0;36m"
_YELLOW = "\033[0;33m"
_GREEN  = "\033[0;32m"
_RESET  = "\033[0m"


_CMD_HELP: dict[str, dict] = {
    "new": {
        "desc": "Create a new investigation vault",
        "args": [("name", "Investigation name (e.g. 'Shell Company Investigation')")],
        "opts": [("--dir DIR", "Parent directory (default: projects_dir from config)")],
    },
    "open": {
        "desc": "Chew pending documents and open in Claude Code",
        "args": [("name", "Investigation name or slug (omit when inside the project folder)", True)],
    },
    "obsidian": {
        "desc": "Open an investigation vault in Obsidian",
        "args": [("name", "Investigation name or slug")],
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


def _projects_dir() -> Path:
    if CONFIG_FILE.exists():
        config = json.loads(CONFIG_FILE.read_text())
        return Path(config["projects_dir"]).expanduser()
    return Path.home() / "Investigations"


from watchdog.pipeline.write_vault import slugify  # noqa: E402 — after constants


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


def cmd_new(args) -> None:
    name = args.name or getattr(args, "name_flag", None)
    description = getattr(args, "description", None) or ""

    if not name:
        print()
        try:
            name = input("  Investigation name: ").strip()
            if not description:
                description = input("  Brief description (optional): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(1)

    slug = slugify(name)

    if not slug:
        sys.exit("Error: project name is invalid.")

    parent = Path(args.dir).expanduser().resolve() if args.dir else _projects_dir()
    vault = parent / slug

    if vault.exists():
        sys.exit(f"Error: {vault} already exists.")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    today = datetime.now().strftime("%Y-%m-%d")

    for d in [
        "_INCOMING",
        "_CONTEXT",
        "morgue",
        ".watchdog/Registry",
        ".watchdog/queue",
        ".watchdog/staging",
        ".watchdog/tmp",
        "entities/person",
        "entities/company",
        "entities/address",
        "documents",
        "briefings",
        "wiki",
        "queries",
        ".obsidian/plugins",
        ".obsidian/snippets",
        ".claude",
    ]:
        (vault / d).mkdir(parents=True)

    (vault / ".watchdog" / "Registry" / "documents.json").write_text("{}\n")
    (vault / ".watchdog" / "Registry" / "entities.json").write_text("{}\n")
    (vault / ".watchdog" / "Registry" / "manifest.json").write_text("{}\n")
    (vault / ".watchdog" / "Registry" / "registry.json").write_text(
        json.dumps(
            {"schema_version": "1", "created_at": now, "last_updated": now,
             "document_count": 0, "entity_count": 0},
            indent=2,
        ) + "\n"
    )
    (vault / ".watchdog" / "Registry" / "ingest.log").write_text("")

    (vault / ".obsidian" / "app.json").write_text(
        json.dumps(
            {"userIgnoreFilters": [".watchdog"], "showInlineTitle": False},
            indent=2,
        ) + "\n"
    )

    # rgb values are 24-bit packed integers: (R << 16) | (G << 8) | B
    (vault / ".obsidian" / "graph.json").write_text(
        json.dumps(
            {
                "colorGroups": [
                    {"query": "path:entities/person",  "color": {"a": 1, "rgb": 4886745}},   # #4A90D9 blue
                    {"query": "path:entities/company", "color": {"a": 1, "rgb": 5999451}},   # #5BA95B green
                    {"query": "path:entities/address", "color": {"a": 1, "rgb": 15238714}},  # #E8863A orange
                    {"query": "path:documents",        "color": {"a": 1, "rgb": 9145227}},   # #8B8B8B grey
                ]
            },
            indent=2,
        ) + "\n"
    )

    (vault / "hot.md").write_text(_render_template("hot.md"))
    (vault / "log.md").write_text(_render_template("log.md"))
    desc_placeholder = description if description else "<!-- One paragraph. What is the story? What pattern, question, or wrongdoing are you pursuing? -->"
    (vault / "context.md").write_text(_render_template("context.md", name=name, description=desc_placeholder))
    (vault / "index.md").write_text(_render_template("index.md", name=name, today=today))
    (vault / "CLAUDE.md").write_text(_render_template("CLAUDE.md", name=name))

    from watchdog.setup_cmd import install_skills
    install_skills(vault / ".claude" / "commands")
    _register_obsidian_vault(vault)

    (vault / ".claude" / "settings.json").write_text(
        json.dumps(
            {
                "permissions": {
                    "allow": _VAULT_PERMISSIONS,
                },
                "hooks": {
                    "UserPromptSubmit": [
                        {
                            "matcher": "",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": (
                                        "python3 -c \""
                                        "from pathlib import Path; "
                                        "p = list(Path('.watchdog/queue').glob('*.json')) "
                                        "if Path('.watchdog/queue').exists() else []; "
                                        "print('WATCHDOG: ' + str(len(p)) + ' file(s) ready for extraction — run /watchdog-ingest') if p else None"
                                        "\""
                                    ),
                                }
                            ],
                        }
                    ]
                },
            },
            indent=2,
        ) + "\n"
    )

    projects = load_projects()
    entry = {"name": name, "path": str(vault), "created_at": now}
    if description:
        entry["description"] = description
    projects[slug] = entry
    save_projects(projects)

    print(f"\n  {_GREEN}Created:{_RESET} {_BOLD}{vault}{_RESET}")
    print()
    print(f"  {_BOLD}Next steps{_RESET}")
    print(f"    1. Drop documents into {_CYAN}{vault}/_INCOMING/{_RESET}")
    print(f"    2. Run {_CYAN}watchdog open {slug}{_RESET} to chew and open in Claude Code")
    print(f"    3. Run {_CYAN}/watchdog-ingest{_RESET} when prompted inside Claude Code")
    print(f"    4. Run {_CYAN}watchdog obsidian {slug}{_RESET} to open the vault in Obsidian")
    print()


def _run_preprocess(
    vault: Path,
    workers: int | None = None,
    chunk_workers: int | None = None,
    confirm: bool = False,
) -> None:
    from watchdog.pipeline.preprocess_batch import run_ingest, find_files
    incoming = vault / "_INCOMING"
    queue    = vault / ".watchdog" / "queue"
    if not incoming.is_dir():
        sys.exit(f"Error: _INCOMING/ not found in {vault}")
    if confirm:
        files = find_files([incoming])
        if not files:
            queued = len(list(queue.glob("*.json"))) if queue.exists() else 0
            if queued:
                print(f"\n  {_DIM}_INCOMING/ is empty — {queued} file{'s' if queued != 1 else ''} ready for {_RESET}{_CYAN}/watchdog-ingest{_RESET}{_DIM}.{_RESET}\n")
            else:
                print(f"\n  {_DIM}_INCOMING/ is empty — nothing to chew.{_RESET}\n")
            return
        n = len(files)
        label = f"{n} file{'s' if n != 1 else ''}"
        try:
            answer = input(f"\n  Found {_BOLD}{label}{_RESET} in _INCOMING/. Chew now? [Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if answer not in ("", "y", "yes"):
            return
    run_ingest(vault, workers=workers, chunk_workers=chunk_workers)


def cmd_chew(args) -> None:
    vault = Path(".").resolve()
    if not (vault / ".watchdog").is_dir():
        sys.exit("Error: not inside a Watchdog project folder. cd into your investigation first.")

    queued_before = _count_queued(vault)
    file_arg = getattr(args, "file", None)
    chew_workers  = getattr(args, "chew_workers", None)
    chunk_workers = getattr(args, "chunk_workers", None)
    if file_arg:
        from watchdog.pipeline.preprocess_batch import run_ingest
        f = Path(file_arg).resolve()
        if not f.exists():
            sys.exit(f"Error: file not found: {f}")
        run_ingest(vault, workers=chew_workers, chunk_workers=chunk_workers, files=[f])
    else:
        _run_preprocess(vault, workers=chew_workers, chunk_workers=chunk_workers)

    new_queued = _count_queued(vault) - queued_before
    if new_queued > 0:
        _notify("Watchdog", f"{new_queued} file{'s' if new_queued != 1 else ''} chewed — ready for /watchdog-ingest.")


def _launch_claude(vault: Path) -> None:
    try:
        os.execvp("claude", ["claude", str(vault)])
    except FileNotFoundError:
        sys.exit("Error: Claude Code not found — install from https://claude.ai/download")


def cmd_open(args) -> None:
    name = getattr(args, "name", None)
    if name is None:
        vault = Path(".").resolve()
        if not (vault / ".watchdog").is_dir():
            sys.exit("Error: not inside a Watchdog project folder. cd into your investigation first.")
        projects = load_projects()
        info = next((v for v in projects.values() if Path(v["path"]).resolve() == vault),
                    {"name": vault.name, "path": str(vault)})
    else:
        _, info = _find_project(name)
        vault = Path(info["path"])
    if not vault.exists():
        sys.exit(f"Error: project directory not found: {vault}")
    print(f"\n  {_BOLD}{info['name']}{_RESET}  {_CYAN}{vault}{_RESET}\n")
    os.chdir(vault)

    # Step 1: chew if there are files in _INCOMING/
    _run_preprocess(vault, confirm=True)

    # Step 2: offer to open Claude Code if there are files queued for ingest
    queue = vault / ".watchdog" / "queue"
    queued = len(list(queue.glob("*.json"))) if queue.exists() else 0
    if queued:
        try:
            answer = input(f"  {_BOLD}{queued}{_RESET} file{'s' if queued != 1 else ''} ready for {_CYAN}/watchdog-ingest{_RESET}. Open in Claude Code? [Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if answer in ("", "y", "yes"):
            _launch_claude(vault)
    else:
        _launch_claude(vault)



def _obsidian_config_path() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "obsidian" / "obsidian.json"
    elif sys.platform == "win32":
        return Path(os.environ["APPDATA"]) / "obsidian" / "obsidian.json"
    else:
        cfg_home = Path(os.environ.get("XDG_CONFIG_HOME", "")) or Path.home() / ".config"
        return cfg_home / "obsidian" / "obsidian.json"


def _obsidian_registered(vault: Path) -> bool:
    cfg = _obsidian_config_path()
    if not cfg.exists():
        return False
    try:
        data = json.loads(cfg.read_text())
        return any(v.get("path") == str(vault) for v in data.get("vaults", {}).values())
    except Exception:
        return False


def _register_obsidian_vault(vault: Path) -> None:
    cfg = _obsidian_config_path()
    try:
        data = json.loads(cfg.read_text()) if cfg.exists() else {"vaults": {}}
        data.setdefault("vaults", {})[secrets.token_hex(8)] = {
            "path": str(vault),
            "ts": int(datetime.now(timezone.utc).timestamp() * 1000),
        }
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps(data))
    except Exception:
        pass  # non-fatal — user can register manually via watchdog obsidian


def cmd_obsidian(args) -> None:
    if not args.name:
        cwd = Path(".").resolve()
        if (cwd / ".watchdog").is_dir():
            projects = load_projects()
            info = next((v for v in projects.values() if Path(v["path"]).resolve() == cwd), None)
            if info is None:
                sys.exit("Error: current directory is a vault but not registered. Run `watchdog new` first.")
        else:
            sys.exit("Error: not inside a watchdog project. Run `watchdog obsidian <name>` or cd into a project first.")
    else:
        _, info = _find_project(args.name)
    vault = Path(info["path"])
    if not vault.exists():
        sys.exit(f"Error: project directory not found: {vault}")
    if not _obsidian_registered(vault):
        print(f"\n  {_YELLOW}Vault not registered in Obsidian yet.{_RESET}")
        print()
        print(f"  Open Obsidian → {_BOLD}Open folder as vault{_RESET} → navigate to:")
        print(f"  {_CYAN}{vault}{_RESET}")
        print()
        print(f"  After opening it once, {_CYAN}watchdog obsidian {info['name']}{_RESET} will work automatically.\n")
        return
    from urllib.parse import quote
    url = f"obsidian://open?path={quote(str(vault))}"
    if sys.platform == "darwin":
        opener = ["open", url]
    elif sys.platform.startswith("linux"):
        opener = ["xdg-open", url]
    elif sys.platform == "win32":
        import os as _os
        try:
            _os.startfile(url)
        except Exception:
            sys.exit("Error: could not open Obsidian — is it installed?")
        print(f"\n  {_GREEN}Opened:{_RESET} {_BOLD}{info['name']}{_RESET} in Obsidian\n")
        return
    else:
        sys.exit("Error: watchdog obsidian is not supported on this platform")
    result = subprocess.run(opener, capture_output=True)
    if result.returncode != 0:
        sys.exit("Error: could not open Obsidian — is it installed?")
    print(f"\n  {_GREEN}Opened:{_RESET} {_BOLD}{info['name']}{_RESET} in Obsidian\n")


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


def _check_vault_locks(vault: Path, slug: str) -> None:
    chew_lock   = vault / ".watchdog" / ".chew-lock"
    ingest_lock = vault / ".watchdog" / "Registry" / ".ingest-lock"
    if chew_lock.exists():
        sys.exit(f"Error: chew is in progress. Wait for it to finish or run: watchdog unlock {slug}")
    if ingest_lock.exists():
        sys.exit(f"Error: ingest is in progress. Wait for it to finish or run: watchdog unlock {slug}")


def cmd_rename(args) -> None:
    slug, info = _find_project(args.project)
    vault = Path(info["path"])
    new_name = args.name.strip()
    new_slug = slugify(new_name)

    if not new_slug:
        sys.exit("Error: new name is invalid.")

    _check_vault_locks(vault, slug)

    projects = load_projects()
    if new_slug in projects and new_slug != slug:
        sys.exit(f"Error: a project named '{new_name}' already exists.")

    new_vault = vault.parent / new_slug

    if slug != new_slug:
        if new_vault.exists():
            sys.exit(f"Error: {new_vault} already exists.")
        vault.rename(new_vault)

        # Update Obsidian registry (path changed)
        cfg = _obsidian_config_path()
        if cfg.exists():
            try:
                data = json.loads(cfg.read_text())
                for v in data.get("vaults", {}).values():
                    if v.get("path") == info["path"]:
                        v["path"] = str(new_vault)
                cfg.write_text(json.dumps(data))
            except Exception:
                pass

    projects[new_slug] = {**info, "name": new_name, "path": str(new_vault)}
    if new_slug != slug:
        del projects[slug]
    save_projects(projects)

    print(f"\n  {_GREEN}Renamed:{_RESET}  {_DIM}{info['name']}{_RESET} → {_BOLD}{new_name}{_RESET}  {_DIM}[{new_slug}]{_RESET}")
    print(f"  {_CYAN}{new_vault}{_RESET}\n")


def cmd_describe(args) -> None:
    first    = args.project
    new_desc = args.text.strip() if args.text else None  # explicit text always wins

    if first is not None and new_desc is None:
        # One positional, no text: try it as a project name; if not found, treat as description
        projects = load_projects()
        slug_try = slugify(first)
        is_known = slug_try in projects or any(k.startswith(slug_try) for k in projects)
        if is_known:
            slug, info = _find_project(first)
        else:
            cwd   = Path(".").resolve()
            match = next(((s, v) for s, v in projects.items() if Path(v["path"]).resolve() == cwd), None)
            if match is None:
                sys.exit(f"Project not found: {first}\nRun 'watchdog list' to see all projects.")
            slug, info = match
            new_desc = first.strip()
    elif first is not None:
        slug, info = _find_project(first)
    else:
        cwd      = Path(".").resolve()
        projects = load_projects()
        match    = next(((s, v) for s, v in projects.items() if Path(v["path"]).resolve() == cwd), None)
        if match is None:
            sys.exit("Error: not inside a vault directory. Pass the project name explicitly.")
        slug, info = match

    if new_desc is None:
        current = info.get("description", "")
        if current:
            print(f"\n  {_DIM}Current:{_RESET} {current}")
        try:
            new_desc = input("\n  New description: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(1)
        if not new_desc:
            sys.exit("Error: description cannot be empty.")

    projects = load_projects()
    projects[slug]["description"] = new_desc
    save_projects(projects)

    print(f"\n  {_GREEN}Updated:{_RESET}  {_BOLD}{info['name']}{_RESET}")
    print(f"  {_DIM}{new_desc}{_RESET}\n")


def cmd_delete(args) -> None:
    slug, info = _find_project(args.name)
    vault = Path(info["path"])
    _check_vault_locks(vault, slug)

    print(f"\n  {_BOLD}{info['name']}{_RESET}  {_DIM}{slug}{_RESET}")
    print(f"  {_CYAN}{vault}{_RESET}")
    if args.purge:
        print()
        print(f"  {_YELLOW}--purge will permanently delete all vault files from disk.{_RESET}")

    print()
    try:
        action = "Delete vault and remove" if args.purge else "Remove"
        answer = input(f"  {action} from registry? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return

    if answer not in ("y", "yes"):
        print(f"\n  {_DIM}Cancelled.{_RESET}\n")
        return

    projects = load_projects()
    del projects[slug]
    save_projects(projects)

    if args.purge and vault.exists():
        shutil.rmtree(vault)

    # Remove from Obsidian registry
    cfg = _obsidian_config_path()
    if cfg.exists():
        try:
            data = json.loads(cfg.read_text())
            vaults = data.get("vaults", {})
            to_remove = [k for k, v in vaults.items() if v.get("path") == str(vault)]
            for k in to_remove:
                del vaults[k]
            cfg.write_text(json.dumps(data))
        except Exception:
            pass

    label = "Deleted" if args.purge else "Removed"
    print(f"\n  {_GREEN}{label}:{_RESET} {_BOLD}{info['name']}{_RESET}\n")


def cmd_move(args) -> None:
    slug, info = _find_project(args.name)
    src = Path(info["path"])
    _check_vault_locks(src, slug)
    dst = Path(args.path).expanduser().resolve()

    if src == dst:
        sys.exit("Error: source and destination are the same.")

    # If dst is an existing directory that is not the vault itself, put vault inside it
    if dst.is_dir() and not (dst / ".watchdog").exists():
        dst = dst / src.name

    moved = False
    if src.exists():
        shutil.move(str(src), str(dst))
        moved = True
    elif not dst.exists():
        sys.exit(
            f"Error: {src} not found and {dst} does not exist — nothing to update.\n"
            f"Move the vault manually first, then re-run: watchdog move {slug} <new-path>"
        )

    projects = load_projects()
    projects[slug]["path"] = str(dst)
    save_projects(projects)

    # Update Obsidian registry
    cfg = _obsidian_config_path()
    if cfg.exists():
        try:
            data = json.loads(cfg.read_text())
            for v in data.get("vaults", {}).values():
                if v.get("path") == str(src):
                    v["path"] = str(dst)
            cfg.write_text(json.dumps(data))
        except Exception:
            pass

    verb = "Moved" if moved else "Updated"
    print(f"\n  {_GREEN}{verb}:{_RESET} {_BOLD}{info['name']}{_RESET}")
    print(f"  {_CYAN}{dst}{_RESET}\n")


def cmd_archive(args) -> None:
    slug, info = _find_project(args.name)
    projects = load_projects()
    projects[slug]["archived"] = True
    save_projects(projects)
    print(f"\n  {_GREEN}Archived:{_RESET} {_BOLD}{info['name']}{_RESET}  {_DIM}hidden from watchdog list{_RESET}\n")


def cmd_unarchive(args) -> None:
    slug, info = _find_project(args.name)
    projects = load_projects()
    projects[slug].pop("archived", None)
    save_projects(projects)
    print(f"\n  {_GREEN}Unarchived:{_RESET} {_BOLD}{info['name']}{_RESET}\n")


def cmd_log(args) -> None:
    _, info = _find_project(args.name)
    vault = Path(info["path"])
    log_path = vault / "log.md"

    if not log_path.exists():
        print(f"\n  {_DIM}No ingest log found — nothing has been ingested yet.{_RESET}\n")
        return

    content = log_path.read_text().strip()
    if not content:
        print(f"\n  {_DIM}Ingest log is empty.{_RESET}\n")
        return

    lines = content.splitlines()
    n = getattr(args, "lines", None)
    if n:
        lines = lines[-n:]

    print(f"\n  {_BOLD}{info['name']}{_RESET}  {_DIM}ingest log{_RESET}\n")
    for line in lines:
        stripped = line.rstrip()
        if not stripped:
            print()
        elif stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            text = stripped.lstrip("#").strip()
            indent = "  " + "  " * (level - 1)
            print(f"{indent}{_BOLD}{text}{_RESET}")
        else:
            print(f"  {_DIM}{stripped}{_RESET}")
    print()


def cmd_watch(args) -> None:
    _, info = _find_project(args.name)
    vault = Path(info["path"])
    if not vault.exists():
        sys.exit(f"Error: project directory not found: {vault}")

    from watchdog.pipeline.preprocess_batch import run_ingest, find_files
    import time as _time

    incoming = vault / "_INCOMING"
    print(f"\n  {_BOLD}{info['name']}{_RESET}  watching {_CYAN}_INCOMING/{_RESET} — press Ctrl+C to stop.\n")

    known: set = set(find_files([incoming]))

    try:
        while True:
            _time.sleep(3)
            current: set = set(find_files([incoming]))
            new_files = current - known
            if new_files:
                n = len(new_files)
                label = f"{n} file{'s' if n != 1 else ''}"
                print(f"  {_BOLD}{label}{_RESET} detected — chewing...\n")
                queued_before = _count_queued(vault)
                run_ingest(vault)
                new_queued = _count_queued(vault) - queued_before
                if new_queued > 0:
                    _notify(
                        f"Watchdog — {info['name']}",
                        f"Chewed {label}. {new_queued} file{'s' if new_queued != 1 else ''} ready for /watchdog-ingest.",
                    )
                known = set()
            else:
                known = current
    except KeyboardInterrupt:
        print(f"\n  {_DIM}Stopped watching.{_RESET}\n")


def cmd_about(_args) -> None:
    from watchdog import __version__
    print()
    print(f"  🔍🐕  {_BOLD}Watchdog{_RESET}  {_DIM}v{__version__}{_RESET}")
    print(f"  {_DIM}Investigative journalism document intelligence{_RESET}")
    print()
    print(f"  🐙  {_DIM}GitHub   {_RESET}{_CYAN}https://github.com/tomcardoso/watchdog{_RESET}")
    print(f"  🐛  {_DIM}Issues   {_RESET}{_CYAN}https://github.com/tomcardoso/watchdog/issues{_RESET}")
    print(f"  📖  {_DIM}Install  {_RESET}{_CYAN}https://github.com/tomcardoso/watchdog/blob/main/INSTALL.md{_RESET}")
    print()


def cmd_setup(args) -> None:
    from watchdog.setup_cmd import run as run_setup
    run_setup(force=getattr(args, "force", False))


def cmd_refresh_skills(args) -> None:
    if args.name:
        _, info = _find_project(args.name)
        vault = Path(info["path"])
    else:
        vault = Path(".").resolve()
        if not (vault / ".watchdog").is_dir():
            sys.exit("Error: not inside a watchdog project. cd into a vault or pass a project name.")
    from watchdog.setup_cmd import install_skills
    commands_dir = vault / ".claude" / "commands"
    install_skills(commands_dir)

    settings_path = vault / ".claude" / "settings.json"
    added = []
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
            existing = set(settings.get("permissions", {}).get("allow", []))
            missing  = [p for p in _VAULT_PERMISSIONS if p not in existing]
            if missing:
                settings.setdefault("permissions", {}).setdefault("allow", []).extend(missing)
                settings_path.write_text(json.dumps(settings, indent=2) + "\n")
                added = missing
        except (json.JSONDecodeError, KeyError):
            pass

    print(f"\n  {_GREEN}Skills refreshed{_RESET}  {_DIM}{commands_dir}{_RESET}")
    if added:
        print(f"  {_GREEN}Permissions updated{_RESET}  {_DIM}added {len(added)} missing rule{'s' if len(added) != 1 else ''}{_RESET}")
    print()


def cmd_list(args) -> None:
    all_projects = load_projects()
    show_all = getattr(args, "all", False)
    active   = {k: v for k, v in all_projects.items() if not v.get("archived")}
    archived = {k: v for k, v in all_projects.items() if v.get("archived")}

    visible = dict(active)
    if show_all:
        visible.update(archived)

    if not visible:
        if archived and not show_all:
            print(f"\n  No active investigations. {len(archived)} archived — run {_CYAN}watchdog list --all{_RESET} to show.\n")
        else:
            print(f"\n  No projects. Create one with: {_CYAN}watchdog new <name>{_RESET}\n")
        return

    rows = []
    for slug, info in sorted(visible.items(), key=lambda x: x[1]["name"]):
        vault = Path(info["path"])
        reg      = _load_registry(vault)
        docs     = str(reg["document_count"]) if reg else "—"
        entities = str(reg["entity_count"])   if reg else "—"
        updated  = _fmt_date(reg["last_updated"]) if reg else "—"
        incoming = str(_count_incoming(vault)) if vault.exists() else "—"
        queued   = str(_count_queued(vault))   if vault.exists() else "—"
        is_arch  = bool(info.get("archived"))
        description = info.get("description", "")
        rows.append((info["name"], slug, docs, entities, updated, incoming, queued, is_arch, description))

    name_w = max(len(r[0]) for r in rows) + 2
    slug_w = max(len(r[1]) for r in rows) + 2
    sep_w = name_w + slug_w + 6 + 8 + 7 + 9 + 10 + 7 * 2 - 2
    header = (
        f"  {_BOLD}{'Project':<{name_w}}{_RESET}"
        f"  {_DIM}{'Slug':<{slug_w}}"
        f"  {'Docs':>6}"
        f"  {'Entities':>8}"
        f"  {'To chew':>7}"
        f"  {'To ingest':>9}"
        f"  Updated{_RESET}"
    )
    print(f"\n{header}")
    print(f"  {_DIM}{'─' * sep_w}{_RESET}")
    for name, slug, docs, entities, updated, incoming, queued, is_arch, description in rows:
        inc = "—" if incoming == "0" else incoming
        que = "—" if queued   == "0" else queued
        if is_arch:
            inc_str = f"{_DIM}{inc:>7}{_RESET}"
            que_str = f"{_DIM}{que:>9}{_RESET}"
            name_str = f"  {_DIM}{name:<{name_w}}{_RESET}"
        else:
            inc_str = f"{_YELLOW}{inc:>7}{_RESET}" if inc != "—" else f"{_DIM}{inc:>7}{_RESET}"
            que_str = f"{_YELLOW}{que:>9}{_RESET}" if que != "—" else f"{_DIM}{que:>9}{_RESET}"
            name_str = f"  {_BOLD}{name:<{name_w}}{_RESET}"
        print(
            f"{name_str}"
            f"  {_DIM}{slug:<{slug_w}}"
            f"  {docs:>6}"
            f"  {entities:>8}{_RESET}"
            f"  {inc_str}"
            f"  {que_str}"
            f"  {_DIM}{updated}{_RESET}"
        )
        if description:
            print(f"    {_DIM}{description}{_RESET}")
    if archived and not show_all:
        n = len(archived)
        print(f"  {_DIM}+ {n} archived — run {_RESET}{_CYAN}watchdog list --all{_RESET}{_DIM} to show{_RESET}")
    print()


def cmd_status(args) -> None:
    if not args.name:
        cwd = Path(".").resolve()
        if (cwd / ".watchdog").is_dir():
            projects = load_projects()
            info = next((v for v in projects.values() if Path(v["path"]).resolve() == cwd), None)
            if info is None:
                cmd_list(args)
                return
        else:
            cmd_list(args)
            return
    else:
        _, info = _find_project(args.name)
    vault = Path(info["path"])

    if not vault.exists():
        sys.exit(f"Error: project directory not found: {vault}")

    reg = _load_registry(vault)
    if not reg:
        print(f"\n  {_BOLD}{info['name']}{_RESET}")
        print(f"  {_CYAN}{info['path']}{_RESET}")
        if info.get("description"):
            print(f"  {_DIM}{info['description']}{_RESET}")
        print(f"  {_DIM}Created {_fmt_date(info.get('created_at', ''))}{_RESET}")
        print(f"\n  {_DIM}No registry found — open this vault in Claude Code to begin ingesting.{_RESET}\n")
        return

    docs_file = vault / ".watchdog" / "Registry" / "documents.json"
    ents_file = vault / ".watchdog" / "Registry" / "entities.json"
    try:
        docs_data = json.loads(docs_file.read_text()) if docs_file.exists() else {}
        ents_data = json.loads(ents_file.read_text()) if ents_file.exists() else {}
    except json.JSONDecodeError as e:
        sys.exit(f"Error: registry file is corrupt — {e}\nRun '/watchdog-health' to diagnose.")

    total_pages = sum(d.get("page_count", 0) for d in docs_data.values())
    doc_types   = Counter(d["document_type"] for d in docs_data.values() if d.get("document_type"))
    ent_types   = Counter(e["type"]          for e in ents_data.values() if e.get("type"))
    incoming_n = _count_incoming(vault)
    queued_n   = _count_queued(vault)

    print(f"\n  {_BOLD}{info['name']}{_RESET}  {_DIM}{slugify(info['name'])}{_RESET}")
    print(f"  {_CYAN}{info['path']}{_RESET}")
    if info.get("description"):
        print(f"  {_DIM}{info['description']}{_RESET}")
    print(f"  {_DIM}Created {_fmt_date(info.get('created_at', ''))}{_RESET}")
    print()

    pages_note = f" {_DIM}({total_pages} pages){_RESET}" if total_pages else ""
    print(f"  {_BOLD}{reg['document_count']}{_RESET} documents{pages_note} · {_BOLD}{reg['entity_count']}{_RESET} entities · {_DIM}last updated {_fmt_date(reg['last_updated'])}{_RESET}")

    if incoming_n:
        print(f"  {_YELLOW}{incoming_n} file{'s' if incoming_n != 1 else ''}{_RESET} in {_CYAN}_INCOMING/{_RESET} {_DIM}— run{_RESET} {_CYAN}watchdog chew{_RESET}")
    if queued_n:
        print(f"  {_YELLOW}{queued_n} file{'s' if queued_n != 1 else ''}{_RESET} chewed and waiting for {_CYAN}/watchdog-ingest{_RESET}")

    if doc_types:
        print()
        print(f"  {_BOLD}Documents by type{_RESET}")
        for dtype, count in sorted(doc_types.items(), key=lambda x: -x[1]):
            print(f"  {_DIM}  {dtype:<40}{_RESET} {count:>4}")

    if ent_types:
        print()
        print(f"  {_BOLD}Entities by type{_RESET}")
        for etype, count in sorted(ent_types.items(), key=lambda x: -x[1]):
            print(f"  {_DIM}  {etype:<40}{_RESET} {count:>4}")

    print()


_CONFIGURE_KEYS = {
    # ── Project ───────────────────────────────────────────────────────────────
    "projects_dir": {
        "short": "Path where new investigation vaults are created",
        "help": (
            "The directory where `watchdog new` creates investigation vaults.\n"
            "  Set during setup; change here to move future vaults to a different location.\n"
            "  Existing vaults are not moved."
        ),
        "type": "path",
    },
    # ── OCR ───────────────────────────────────────────────────────────────────
    "ocr_engine": {
        "short": "OCR engine for scanned documents (default: auto)",
        "help": (
            "OCR engine used when processing scanned documents.\n"
            "  auto:         Apple Vision on macOS (if ocrmac installed), Tesseract elsewhere.\n"
            "  apple_vision: Apple Vision only — macOS with ocrmac required.\n"
            "  tesseract:    Tesseract — requires system install (brew or apt install tesseract-ocr).\n"
            "  easyocr:      EasyOCR — pure pip install, no system deps, less accurate on forms.\n"
            "  rapidocr:     RapidOCR — lightweight, no C deps, fast.\n"
            "  Valid values: auto, apple_vision, tesseract, easyocr, rapidocr."
        ),
        "type": "enum",
        "default": "auto",
        "choices": ["auto", "apple_vision", "tesseract", "easyocr", "rapidocr"],
    },
    "ocr_languages": {
        "short": "Apple Vision OCR languages (comma-separated BCP 47 codes, e.g. en-US,fr-FR)",
        "help": (
            "Languages Apple Vision should try when reading scanned documents.\n"
            "  Leave unset to auto-detect from the image (macOS 13+).\n"
            "  Set explicitly if auto-detection produces poor results or you are on macOS 12.\n"
            "  Codes: https://developer.apple.com/documentation/vision/vnrecognizetextrequest"
        ),
        "type": "lang_list",
    },
    "garbled_threshold": {
        "short": "OCR trigger threshold — alphanumeric ratio below which a PDF text layer is garbled (default: 0.75)",
        "help": (
            "When reading a PDF, Watchdog samples the text layer and measures what fraction of\n"
            "  characters are alphanumeric or whitespace. If the ratio falls below this threshold,\n"
            "  the text layer is considered garbled and OCR is applied automatically.\n"
            "  Lower = more aggressive OCR. Higher may miss subtly garbled pages.\n"
            "  Valid range: 0.0–1.0. Default: 0.75."
        ),
        "type": "float",
        "default": 0.75,
        "min": 0.0,
        "max": 1.0,
    },
    # ── Processing ────────────────────────────────────────────────────────────
    "chew_workers": {
        "short": "Parallel files during chewing ('auto' for adaptive, or a fixed number)",
        "help": (
            "Number of files chewed simultaneously by `watchdog chew`.\n"
            "  'auto' (default): Watchdog scans the batch before starting and sets this based on\n"
            "  median document length — more workers for short-doc batches, fewer for large PDFs.\n"
            "  Set to a whole number to pin the value regardless of batch content.\n"
            "  Set to 1 to process files one at a time."
        ),
        "type": "int_or_auto",
        "default": "auto",
        "min": 1,
    },
    "chunk_size": {
        "short": "Pages per chunk when splitting large PDFs for parallel processing (default: 40)",
        "help": (
            "PDFs with more pages than this value are split into chunks and processed in parallel.\n"
            "  Smaller chunks reduce peak memory per worker but add per-chunk overhead.\n"
            "  Larger chunks are more efficient on fast machines with ample RAM.\n"
            "  Default: 40."
        ),
        "type": "int",
        "default": 40,
        "min": 1,
    },
    "chunk_workers": {
        "short": "Parallel subprocesses for large-PDF chunks ('auto' for adaptive, or a fixed number)",
        "help": (
            "Number of parallel subprocesses used when splitting large PDFs (>chunk_size pages).\n"
            "  'auto' (default): set adaptively based on median document length in the batch.\n"
            "  Works in tandem with chew_workers: total subprocess load for large-PDF batches\n"
            "  is approximately chew_workers × chunk_workers.\n"
            "  Set to 1 to disable within-file parallelism."
        ),
        "type": "int_or_auto",
        "default": "auto",
        "min": 1,
    },
    "chunk_timeout": {
        "short": "Seconds before a chunk subprocess is killed (default: 300)",
        "help": (
            "Each chunk subprocess is given this many seconds to complete before being killed.\n"
            "  Increase for very large or complex PDFs on slow machines.\n"
            "  Default: 300 (5 minutes)."
        ),
        "type": "int",
        "default": 300,
        "min": 1,
    },
    # ── Extraction ────────────────────────────────────────────────────────────
    "table_structure": {
        "short": "Run table detection model on PDFs (default: true)",
        "help": (
            "When enabled, Docling runs a dedicated ML model to detect and reconstruct tables.\n"
            "  Disable to speed up ingestion of text-only documents (court decisions, contracts).\n"
            "  Does not affect text extraction — only the table structure model.\n"
            "  Default: true."
        ),
        "type": "bool",
        "default": True,
    },
    "embed_images": {
        "short": "Embed images as base64 in markdown output so Claude can see figures (default: false)",
        "help": (
            "When enabled, images and figures in documents are embedded as base64 data\n"
            "  in the markdown output, allowing Claude to read charts, graphs, and other\n"
            "  visual content directly. Significantly increases token usage and processing\n"
            "  time per document. Only useful when documents contain charts, image-based\n"
            "  tables, or diagrams that carry investigative value.\n"
            "  Default: false."
        ),
        "type": "bool",
        "default": False,
    },
    # ── Deduplication ─────────────────────────────────────────────────────────
    "dup_threshold": {
        "short": "Near-duplicate Jaccard similarity threshold — score at which documents are flagged (default: 0.85)",
        "help": (
            "Watchdog fingerprints each document and compares it to all previously ingested documents\n"
            "  using Jaccard similarity on word n-grams. If the score meets or exceeds this threshold,\n"
            "  the document is flagged as a near-duplicate.\n"
            "  Higher = stricter matching (fewer false positives, may miss near-duplicates).\n"
            "  Lower = looser matching (more matches, more false positives).\n"
            "  Valid range: 0.0–1.0. Default: 0.85."
        ),
        "type": "float",
        "default": 0.85,
        "min": 0.0,
        "max": 1.0,
    },
    "shingle_size": {
        "short": "Word n-gram size for near-duplicate fingerprinting (default: 3)",
        "help": (
            "Documents are fingerprinted using overlapping sequences of n consecutive words.\n"
            "  Larger n is more precise but slower and uses more registry storage per document.\n"
            "  Smaller n is faster but produces more false positives.\n"
            "  Changing this invalidates existing shingle data — re-ingest to rebuild fingerprints.\n"
            "  Default: 3 (word trigrams)."
        ),
        "type": "int",
        "default": 3,
        "min": 1,
    },
}


_OCR_ENGINE_PACKAGES = {
    # engine → (import_name, pip_package) or None if bundled with docling
    "apple_vision": ("ocrmac",               "ocrmac"),
    "tesseract":    ("tesserocr",            "tesserocr"),
    "rapidocr":     ("rapidocr_onnxruntime", "rapidocr-onnxruntime"),
    "easyocr":      None,
    "auto":         None,
}

_TESSERACT_HEADERS_HINT = (
    "Tesseract system headers are required to build tesserocr:\n"
    "  Ubuntu/Debian:  sudo apt install tesseract-ocr libtesseract-dev\n"
    "  Fedora:         sudo dnf install tesseract tesseract-devel\n"
    "  macOS:          brew install tesseract\n"
    "Then re-run: watchdog configure ocr_engine tesseract"
)


def _ensure_ocr_engine(engine: str) -> None:
    """Install the Python binding for the requested OCR engine if not already present."""
    if engine == "apple_vision" and sys.platform != "darwin":
        sys.exit("Error: apple_vision OCR is only available on macOS.")

    spec = _OCR_ENGINE_PACKAGES.get(engine)
    if spec is None:
        return

    import_name, pip_name = spec
    try:
        __import__(import_name)
        return  # already installed
    except ImportError:
        pass

    print(f"\n  {_DIM}Installing {pip_name}...{_RESET}")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", pip_name],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        msg = f"\n  {_YELLOW}Warning:{_RESET} could not install {pip_name}.\n"
        if "tesserocr" in pip_name and ("gcc" in stderr or "compile" in stderr.lower() or "build" in stderr.lower()):
            msg += f"\n  {_DIM}{_TESSERACT_HEADERS_HINT}{_RESET}\n"
        else:
            msg += f"\n  {_DIM}{stderr[:300]}{_RESET}\n"
        print(msg)
    else:
        print(f"  {_GREEN}Installed:{_RESET} {_BOLD}{pip_name}{_RESET}\n")


def cmd_search(args) -> None:
    project_arg = args.project
    query_arg   = args.query

    if project_arg and query_arg:
        _, info = _find_project(project_arg)
        args.query = query_arg
    elif project_arg and not query_arg:
        # One positional: try to resolve as a project name first
        projects  = load_projects()
        slug_try  = slugify(project_arg)
        is_known  = slug_try in projects or any(k.startswith(slug_try) for k in projects)
        if is_known:
            sys.exit("Error: please provide a search query.")
        # Not a project — treat as the query and infer project from cwd
        cwd   = Path(".").resolve()
        match = next(((s, v) for s, v in projects.items() if Path(v["path"]).resolve() == cwd), None)
        if match is None:
            sys.exit(f"Project not found: {project_arg}\nRun 'watchdog list' to see all projects.")
        _, info = match
        args.query = project_arg
    else:
        sys.exit("Error: please provide a search query.")

    vault = Path(info["path"])

    from watchdog.pipeline.embed import search, index_stats
    stats = index_stats(vault)
    if stats["total"] == 0:
        print(f"\n  {_DIM}No embeddings found. Index is built automatically during ingest.{_RESET}\n")
        return

    results = search(vault, args.query, top_n=args.top_n)
    print()
    if not results:
        print(f"  {_DIM}No results.{_RESET}\n")
        return
    for r in results:
        score = f"{r['score']:.2f}"
        if r.get("type") == "note":
            print(f"  {_BOLD}{r['note_path']}{_RESET}  {_DIM}score {score}{_RESET}")
        else:
            print(f"  {_BOLD}{r.get('filename', '?')}{_RESET}  {_DIM}p.{r.get('page')}  score {score}{_RESET}")
        preview = r["preview"].replace("\n", " ").strip()
        print(f"  {_DIM}{preview[:200]}{_RESET}")
        print()


_VALID_CONFIDENCE = {"high", "medium", "low", "disputed"}


def cmd_validate_extraction(args) -> None:
    path = Path(args.file)
    if not path.exists():
        sys.exit(f"Error: file not found: {path}")

    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        sys.exit(f"Error: invalid JSON — {e}")

    errors = []

    doc = data.get("document")
    if not isinstance(doc, dict):
        errors.append("missing or invalid 'document' field")
    else:
        for field in ("sha256", "filename"):
            if not doc.get(field):
                errors.append(f"document.{field} is missing or empty")
        for fact in doc.get("key_facts", []):
            if not isinstance(fact, dict):
                errors.append("key_facts contains a non-object entry")
            elif fact.get("confidence") and fact["confidence"] not in _VALID_CONFIDENCE:
                errors.append(f"key_facts confidence '{fact['confidence']}' is not valid")

    entities = data.get("entities")
    if not isinstance(entities, list):
        errors.append("missing or invalid 'entities' field")
    else:
        for i, ent in enumerate(entities):
            if not isinstance(ent, dict):
                errors.append(f"entities[{i}] is not an object")
                continue
            for field in ("id", "name", "type"):
                if not ent.get(field):
                    errors.append(f"entities[{i}].{field} is missing or empty")
            for j, ev in enumerate(ent.get("timeline_events", [])):
                if not isinstance(ev, dict):
                    errors.append(f"entities[{i}].timeline_events[{j}] is not an object")
                elif ev.get("confidence") and ev["confidence"] not in _VALID_CONFIDENCE:
                    errors.append(f"entities[{i}].timeline_events[{j}] confidence '{ev['confidence']}' is not valid")
            for j, role in enumerate(ent.get("roles", [])):
                if not isinstance(role, dict):
                    errors.append(f"entities[{i}].roles[{j}] is not an object")

    if not data.get("morgue_entity_id"):
        errors.append("morgue_entity_id is missing or empty")
    if not data.get("morgue_document_type"):
        errors.append("morgue_document_type is missing or empty")

    if errors:
        for e in errors:
            print(f"error: {e}")
        sys.exit(1)

    print("ok")


def cmd_is_duplicate(args) -> None:
    cwd = Path(".").resolve()
    if (cwd / ".watchdog").is_dir():
        vault = cwd
    else:
        _, info = _find_project(args.project)
        vault = Path(info["path"])

    docs_file = vault / ".watchdog" / "Registry" / "documents.json"
    try:
        docs = json.loads(docs_file.read_text()) if docs_file.exists() else {}
    except json.JSONDecodeError:
        docs = {}

    if args.sha256 in docs:
        print("dup")
        sys.exit(1)
    print("ok")


def cmd_queue_status(args) -> None:
    cwd = Path(".").resolve()
    if (cwd / ".watchdog").is_dir():
        vault = cwd
    else:
        _, info = _find_project(args.project)
        vault = Path(info["path"])

    queue_dir = vault / ".watchdog" / "queue"
    if not queue_dir.exists():
        print('{"total": 0, "files": []}')
        return

    files = sorted(queue_dir.glob("*.json"))
    entries = []
    for f in files:
        source_type = None
        try:
            data = json.loads(f.read_text())
            source_type = data.get("metadata", {}).get("source_type")
        except Exception:
            pass
        entries.append({"path": str(f), "source_type": source_type})

    print(json.dumps({"total": len(entries), "files": entries}, ensure_ascii=False))


def cmd_entity_index(args) -> None:
    cwd = Path(".").resolve()
    if (cwd / ".watchdog").is_dir():
        vault = cwd
    else:
        _, info = _find_project(args.project)
        vault = Path(info["path"])

    manifest_file = vault / ".watchdog" / "Registry" / "manifest.json"
    if not manifest_file.exists():
        print("[]")
        return

    try:
        manifest = json.loads(manifest_file.read_text())
    except json.JSONDecodeError as e:
        sys.exit(f"Error: manifest.json is corrupt — {e}")

    compact = [
        {"id": e["id"], "name": e["name"], "type": e["type"], "aliases": e.get("aliases", [])}
        for e in manifest.values()
        if e.get("id") and e.get("name")
    ]
    print(json.dumps(compact, ensure_ascii=False))


def cmd_unlock(args) -> None:
    _, info = _find_project(args.project)
    vault = Path(info["path"])

    locks = [
        (vault / ".watchdog" / ".chew-lock",                 ".chew-lock",   "chew"),
        (vault / ".watchdog" / "Registry" / ".ingest-lock",  ".ingest-lock", "ingest"),
    ]

    print()
    found_any = False
    for lock_path, lock_name, op_name in locks:
        if not lock_path.exists():
            continue
        found_any = True

        started_at = None
        for line in lock_path.read_text().splitlines():
            if line.startswith("started_at:"):
                started_at = line.split(":", 1)[1].strip()
                break

        age_str = "unknown age"
        is_stale = True
        if started_at:
            try:
                t = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                age_secs = (datetime.now(timezone.utc) - t).total_seconds()
                age_str = f"{int(age_secs // 60)}m ago"
                is_stale = age_secs >= 1800
            except ValueError:
                pass

        if is_stale or args.force:
            lock_path.unlink()
            print(f"  {_GREEN}Removed:{_RESET} {_BOLD}{lock_name}{_RESET}  {_DIM}({age_str}){_RESET}")
        else:
            print(f"  {_YELLOW}Lock is recent{_RESET} ({age_str}) — {op_name} may still be running.")
            print(f"  Use {_CYAN}watchdog unlock {args.project} --force{_RESET} to remove it anyway.")

    if not found_any:
        print(f"  {_DIM}No locks found — nothing to do.{_RESET}")

    tmp_dir = vault / ".watchdog" / "tmp"
    if tmp_dir.exists():
        leftover = list(tmp_dir.glob("wdg_*"))
        for f in leftover:
            f.unlink(missing_ok=True)
        if leftover:
            print(f"  {_GREEN}Cleaned:{_RESET}  {_DIM}{len(leftover)} leftover temp file{'s' if len(leftover) != 1 else ''} from .watchdog/tmp/{_RESET}")

    print()


def cmd_configure(args) -> None:
    config = {}
    if CONFIG_FILE.exists():
        try:
            config = json.loads(CONFIG_FILE.read_text())
        except json.JSONDecodeError:
            sys.exit("Error: config file is corrupt. Try running 'watchdog setup --force'.")

    key   = getattr(args, "key",   None)
    value = getattr(args, "value", None)

    def _display_value(k, v):
        meta = _CONFIGURE_KEYS.get(k, {})
        if v is None:
            if k == "ocr_languages":
                return f"{_DIM}auto-detect (default){_RESET}"
            d = meta.get("default")
            if d is not None:
                v = d  # fall through to normal rendering with the default value
            else:
                return f"{_DIM}(not set){_RESET}"
        if k == "ocr_languages":
            return f"{_CYAN}{', '.join(v)}{_RESET}" if v else f"{_DIM}auto-detect (default){_RESET}"
        if isinstance(v, bool):
            return f"{_CYAN}{'true' if v else 'false'}{_RESET}"
        return f"{_CYAN}{v}{_RESET}"

    if key is None:
        print()
        print(f"  {_BOLD}Configuration{_RESET}  {_DIM}{CONFIG_FILE}{_RESET}")
        print()
        for k, meta in _CONFIGURE_KEYS.items():
            print(f"  {_DIM}{k:<20}{_RESET} {_display_value(k, config.get(k))}")
            print(f"  {' ' * 20} {_DIM}{meta['short']}{_RESET}")
            print()
        return

    if key not in _CONFIGURE_KEYS:
        sys.exit(f"Error: unknown key '{key}'. Known keys: {', '.join(_CONFIGURE_KEYS)}")

    meta = _CONFIGURE_KEYS[key]

    if value is None:
        if sys.stdin.isatty():
            print(f"\n  {_BOLD}{key}{_RESET}\n")
            for line in meta["help"].split("\n"):
                print(f"  {_DIM}{line.strip()}{_RESET}")
            print()
            print(f"  Current value:  {_display_value(key, config.get(key))}")
            if key in ("chunk_workers", "chew_workers"):
                print(f"  Machine cores:  {os.cpu_count() or 1}")
            print()
            answer = input("  Change this value? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print()
                return
            print()
            value = input("  New value: ").strip()
            if not value:
                print(f"\n  {_DIM}No change.{_RESET}\n")
                return
        else:
            print(f"\n  {_BOLD}{key}{_RESET} = {_display_value(key, config.get(key))}\n")
            return

    if key == "ocr_languages":
        langs = [lang.strip() for lang in value.split(",") if lang.strip()]
        config[key] = langs
        display = ", ".join(langs) if langs else "auto-detect"
    elif key == "projects_dir":
        path = Path(value).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        config[key] = str(path)
        display = str(path)
    elif meta["type"] == "float":
        try:
            v = float(value)
        except ValueError:
            sys.exit(f"Error: '{key}' must be a number (e.g. 0.85)")
        lo, hi = meta.get("min"), meta.get("max")
        if lo is not None and v < lo:
            sys.exit(f"Error: '{key}' must be >= {lo}")
        if hi is not None and v > hi:
            sys.exit(f"Error: '{key}' must be <= {hi}")
        config[key] = v
        display = str(v)
    elif meta["type"] == "int_or_auto":
        if value.lower() == "auto":
            config[key] = "auto"
            display = "auto"
        else:
            try:
                v = int(value)
            except ValueError:
                sys.exit(f"Error: '{key}' must be 'auto' or a whole number")
            lo = meta.get("min")
            if lo is not None and v < lo:
                sys.exit(f"Error: '{key}' must be >= {lo}")
            config[key] = v
            display = str(v)
    elif meta["type"] == "int":
        try:
            v = int(value)
        except ValueError:
            sys.exit(f"Error: '{key}' must be a whole number")
        lo = meta.get("min")
        if lo is not None and v < lo:
            sys.exit(f"Error: '{key}' must be >= {lo}")
        config[key] = v
        display = str(v)
    elif meta["type"] == "bool":
        if value.lower() in ("true", "yes", "1", "on"):
            v = True
        elif value.lower() in ("false", "no", "0", "off"):
            v = False
        else:
            sys.exit(f"Error: '{key}' must be true or false")
        config[key] = v
        display = "true" if v else "false"
    elif meta["type"] == "enum":
        choices = meta.get("choices", [])
        if value not in choices:
            sys.exit(f"Error: '{key}' must be one of: {', '.join(choices)}")
        config[key] = value
        display = value
        if key == "ocr_engine":
            _ensure_ocr_engine(value)
    else:
        config[key] = value
        display = value

    WATCHDOG_HOME.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n")
    print(f"\n  {_GREEN}Set:{_RESET} {_BOLD}{key}{_RESET} = {_CYAN}{display}{_RESET}\n")


def _print_banner() -> None:
    print(f"\n  🔍🐕  {_BOLD}Watchdog{_RESET} — investigative document intelligence")
    print()
    print(f"  {_DIM}Usage:  watchdog <command> [options]{_RESET}")
    print()
    groups = [
        ("Investigation", [
            ("new",        "Create a new investigation vault"),
            ("open",       "Chew pending documents and open in Claude Code"),
            ("obsidian",   "Open in Obsidian"),
            ("archive",    "Archive a completed investigation"),
            ("unarchive",  "Restore an archived investigation"),
            ("rename",     "Rename an investigation"),
            ("move",       "Update vault path in registry"),
            ("delete",     "Remove an investigation"),
        ]),
        ("Pipeline", [
            ("chew",       "Process documents in _INCOMING/"),
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

    p_open = sub.add_parser("open", help="Chew pending documents and open in Claude Code")
    p_open.add_argument("name", nargs="?", help="Investigation name or slug (omit when inside the project folder)").completer = _project_completer
    p_open.set_defaults(func=cmd_open)

    p_list = sub.add_parser("list", help="List all registered investigations")
    p_list.add_argument("--all", action="store_true", help="Include archived investigations")
    p_list.set_defaults(func=cmd_list)

    p_status = sub.add_parser("status", help="Show detailed status for an investigation")
    p_status.add_argument("name", nargs="?", help="Investigation name or slug (omit to list all)").completer = _project_completer
    p_status.set_defaults(func=cmd_status)

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
    p_unlock.add_argument("project", help="Investigation name or slug").completer = _project_completer
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
    p_rename.add_argument("project", help="Investigation name or slug").completer = _project_completer
    p_rename.add_argument("name", help="New name")
    p_rename.set_defaults(func=cmd_rename)

    p_describe = sub.add_parser("describe", help="Set or update an investigation description")
    p_describe.add_argument("project", nargs="?", help="Investigation name or slug (omit when inside the project folder)").completer = _project_completer
    p_describe.add_argument("text", nargs="?", help="New description text (omit to be prompted)")
    p_describe.set_defaults(func=cmd_describe)

    p_entity_index = sub.add_parser("entity-index", help="Output compact entity index for ingest subagents")
    p_entity_index.add_argument("project", nargs="?", help="Investigation name or slug (omit when inside project folder)").completer = _project_completer
    p_entity_index.set_defaults(func=cmd_entity_index)

    p_queue_status = sub.add_parser("queue-status", help="List queued files with source type annotations")
    p_queue_status.add_argument("project", nargs="?", help="Investigation name or slug (omit when inside project folder)").completer = _project_completer
    p_queue_status.set_defaults(func=cmd_queue_status)

    p_validate = sub.add_parser("validate-extraction", help="Validate an extraction JSON file before writing to vault")
    p_validate.add_argument("file", help="Path to extraction JSON file")
    p_validate.set_defaults(func=cmd_validate_extraction)

    p_is_dup = sub.add_parser("is-duplicate", help="Check whether a SHA-256 has already been extracted")
    p_is_dup.add_argument("sha256", help="SHA-256 hash to check")
    p_is_dup.add_argument("project", nargs="?", help="Investigation name or slug (omit when inside project folder)").completer = _project_completer
    p_is_dup.set_defaults(func=cmd_is_duplicate)

    try:
        import argcomplete
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
        wddir = Path(".watchdog")
        if wddir.is_dir() and (wddir / "queue").is_dir():
            _run_preprocess(Path(".").resolve(), confirm=True)
        else:
            _print_banner()
        return

    if args.command not in ("setup", "about", "configure", "search", "unlock", "queue-status", "entity-index", "is-duplicate", "validate-extraction", "refresh-skills") and not CONFIG_FILE.exists():
        print(f"\n  {_BOLD}Watchdog isn't set up yet.{_RESET}  Run: {_CYAN}watchdog setup{_RESET}\n")
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
