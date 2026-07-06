"""Vault lifecycle commands: create, list, status, obsidian, archive, etc."""

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from watchdog.cmd.base import (
    CONFIG_FILE,
    VAULT_SCHEMA_VERSION,
    _BOLD, _CYAN, _DIM, _GREEN, _RESET, _YELLOW,
    _check_project_health,
    _check_vault_locks,
    _count_incoming,
    _count_queued,
    _warn_pending_research,
    _find_project,
    _fmt_date,
    _fmt_size,
    _load_registry,
    _notify,
    _projects_dir,
    _render_template,
    _VAULT_PERMISSIONS,
    _vault_size,
    load_projects,
    save_projects,
    slugify,
)


_WATCHLIST_TEMPLATE = """\
# Watch list
#
# One term per line. When a newly-ingested document mentions a term listed here,
# Watchdog writes an alert to briefings/alerts-<date>.md and flags it in the terminal.
#
#   - Lines starting with # are ignored (like this one).
#   - Matching is case-insensitive and matches whole words ("Ana" won't match "banana").
#   - Blank lines are ignored.
#   - Advanced: wrap a line in slashes for a regular expression, e.g. /Smith,?\\s+John/
#
# Add your terms below:

"""


# Native Obsidian Bases dashboard (core feature, Obsidian 1.9+ — no plugin needed).
# Written verbatim at scaffold time; queries the entity/document note frontmatter the
# pipeline already writes. Bases has no documented `sort:` key, so recurrence (an
# entity's appears_in count) is surfaced as a column the user sorts on by clicking the
# header. See DECISIONS D42.
_DASHBOARD_BASE = """\
filters:
  and:
    - file.ext == "md"
formulas:
  documents: appears_in.length
properties:
  name:
    displayName: Name
  type:
    displayName: Type
  aliases:
    displayName: Also known as
  document_type:
    displayName: Document type
  date_of_document:
    displayName: Dated
  date_ingested:
    displayName: Ingested
  date_last_updated:
    displayName: Updated
  page_count:
    displayName: Pages
  near_duplicate_of:
    displayName: Near-duplicate of
  formula.documents:
    displayName: Documents
views:
  - type: table
    name: Most-mentioned entities
    filters:
      and:
        - file.inFolder("entities")
    order:
      - file.name
      - name
      - type
      - formula.documents
      - date_last_updated
    limit: 20
  - type: table
    name: Recent documents
    filters:
      and:
        - file.inFolder("documents")
    order:
      - file.name
      - document_type
      - date_of_document
      - date_ingested
      - page_count
    limit: 15
  - type: table
    name: People
    filters:
      and:
        - file.inFolder("entities/person")
    order:
      - file.name
      - name
      - aliases
      - formula.documents
  - type: table
    name: Companies
    filters:
      and:
        - file.inFolder("entities/company")
    order:
      - file.name
      - name
      - aliases
      - formula.documents
  - type: table
    name: Single-source entities (review)
    filters:
      and:
        - file.inFolder("entities")
        - appears_in.length == 1
    order:
      - file.name
      - name
      - type
  - type: table
    name: Possible duplicates
    filters:
      and:
        - file.inFolder("documents")
        - near_duplicate_of
    order:
      - file.name
      - near_duplicate_of
"""


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


def cmd_register(args) -> None:
    vault = Path(args.path).expanduser().resolve() if args.path else Path.cwd()

    if not vault.exists():
        sys.exit(f"Error: path not found: {vault}")
    if not (vault / ".watchdog").exists():
        sys.exit(f"Error: {vault} does not look like a watchdog vault — no .watchdog folder found.")

    reg = _load_registry(vault)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Infer name: use folder name as default, let user override
    default_name = vault.name.replace("-", " ").replace("_", " ").title()
    if reg and reg.get("name"):
        default_name = reg["name"]

    if args.name:
        name = args.name.strip()
    else:
        print(f"\n  {_DIM}Vault:{_RESET} {_CYAN}{vault}{_RESET}")
        print(f"  {_DIM}Suggested name:{_RESET} {default_name}")
        try:
            entered = input("\n  Investigation name (Enter to accept suggestion): ").strip()
            name = entered if entered else default_name
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(1)

    slug = slugify(name)
    if not slug:
        sys.exit("Error: name is invalid.")

    projects = load_projects()
    if slug in projects:
        sys.exit(f"Error: a project with slug '{slug}' is already registered. Use 'watchdog rename' or choose a different name.")

    created_at = reg.get("created_at", now) if reg else now
    projects[slug] = {"name": name, "path": str(vault), "created_at": created_at}
    save_projects(projects)
    _register_obsidian_vault(vault)

    print(f"\n  {_GREEN}Registered:{_RESET} {_BOLD}{name}{_RESET}  {_DIM}[{slug}]{_RESET}")
    print(f"  {_CYAN}{vault}{_RESET}\n")


def _pkg_version() -> str:
    """Installed watchdog-intel version, for stamping into a new vault's README."""
    try:
        from importlib.metadata import version, PackageNotFoundError
        try:
            return version("watchdog-intel")
        except PackageNotFoundError:
            return "dev"
    except Exception:
        return "dev"


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
        ".watchdog/timeline",
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

    (vault / "index.md").write_text(_render_template("index.md", name=name, today=today))
    (vault / "dashboard.base").write_text(_DASHBOARD_BASE)
    # CLAUDE.md (Claude's internal instructions) lives in .claude/ to keep the vault root
    # clean — Claude Code loads ./.claude/CLAUDE.md the same as ./CLAUDE.md. README.md is the
    # human-facing entry point for anyone who opens the folder.
    (vault / ".claude" / "CLAUDE.md").write_text(_render_template("CLAUDE.md", name=name))
    (vault / "README.md").write_text(_render_template("README.md", name=name, version=_pkg_version()))
    (vault / "watchlist.md").write_text(_WATCHLIST_TEMPLATE)

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
                    # Load hot.md into context at the start of a session — and again
                    # after compaction, which drops hook-injected context. The `compact`
                    # matcher fires once compaction completes, so this one hook covers a
                    # fresh start, a resume, and a post-compaction reload. SessionStart
                    # stdout is added to Claude's context, so `cat` is all it takes.
                    "SessionStart": [
                        {
                            "matcher": "startup|resume|compact",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "[ -f hot.md ] && cat hot.md || true",
                                }
                            ],
                        }
                    ],
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
                                        "print('WATCHDOG: ' + str(len(p)) + ' file(s) ready for extraction — run watchdog ingest in your terminal') if p else None"
                                        "\""
                                    ),
                                }
                            ],
                        }
                    ],
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
    print(r"                  _,)")
    print(r"          _..._.-;-'  ")
    print(r"       .-'     `(     ")
    print(r"      /      ;   \    ")
    print(r"     ;.' ;`  ,;  ;   ")
    print(r"    .'' ``. (  \ ;   ")
    print(r"   / f_ _L \ ;  )\   ")
    print(r"   \/|` '|\/;; <;/   ")
    print(r"  ((; \_/  (()        ")
    print(r'       "              ')
    print()
    print(f"  {_DIM}To navigate into your new vault, copy and paste this command:{_RESET}")
    print(f"  {_CYAN}cd {vault}{_RESET}")
    print()
    print(f"  {_BOLD}Next steps{_RESET}")
    print(f"    1. {_DIM}(optional){_RESET} Drop background material into {_CYAN}{vault}/_CONTEXT/{_RESET} and run {_CYAN}watchdog context{_RESET}")
    print(f"    2. Drop documents into {_CYAN}{vault}/_INCOMING/{_RESET}")
    print(f"    3. Run {_CYAN}watchdog chew{_RESET} to process documents")
    print(f"    4. Run {_CYAN}watchdog ingest{_RESET} to extract the queued documents")
    print(f"    5. Run {_CYAN}watchdog obsidian {slug}{_RESET} to open the vault in Obsidian")
    print()


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


def cmd_open(args) -> None:
    if not args.name:
        cwd = Path(".").resolve()
        if (cwd / ".watchdog").is_dir():
            projects = load_projects()
            info = next((v for v in projects.values() if Path(v["path"]).resolve() == cwd), None)
            if info is None:
                sys.exit("Error: current directory is a vault but not registered. Run `watchdog new` first.")
        else:
            sys.exit("Error: not inside a watchdog project. Run `watchdog open <name>` or cd into a project first.")
    else:
        _, info = _find_project(args.name)
    vault = Path(info["path"])
    if not vault.exists():
        sys.exit(f"Error: project directory not found: {vault}")
    if sys.platform == "darwin":
        opener = ["open", str(vault)]
    elif sys.platform.startswith("linux"):
        opener = ["xdg-open", str(vault)]
    elif sys.platform == "win32":
        import os as _os
        try:
            _os.startfile(str(vault))
        except Exception:
            sys.exit("Error: could not open file explorer")
        print(f"\n  {_GREEN}Opened:{_RESET} {_CYAN}{vault}{_RESET}\n")
        return
    else:
        sys.exit("Error: watchdog open is not supported on this platform")
    result = subprocess.run(opener, capture_output=True)
    if result.returncode != 0:
        sys.exit(f"Error: could not open file explorer")
    print(f"\n  {_GREEN}Opened:{_RESET} {_CYAN}{vault}{_RESET}\n")


def cmd_rename(args) -> None:
    first    = args.project
    new_name = args.name.strip() if args.name else None

    if first is not None and new_name is None:
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
            new_name = first.strip()
    elif first is not None:
        slug, info = _find_project(first)
    else:
        cwd      = Path(".").resolve()
        projects = load_projects()
        match    = next(((s, v) for s, v in projects.items() if Path(v["path"]).resolve() == cwd), None)
        if match is None:
            sys.exit("Error: not inside a vault directory. Pass the project name explicitly.")
        slug, info = match

    if new_name is None:
        current = info.get("name", "")
        if current:
            print(f"\n  {_DIM}Current:{_RESET} {current}")
        try:
            new_name = input("\n  New name: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(1)
        if not new_name:
            sys.exit("Error: name cannot be empty.")

    vault    = Path(info["path"])
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
    new_desc = args.text.strip() if args.text is not None else None

    if first is not None and new_desc is None:
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
        new_desc = new_desc or ""

    projects = load_projects()
    if new_desc:
        projects[slug]["description"] = new_desc
        save_projects(projects)
        print(f"\n  {_GREEN}Updated:{_RESET}  {_BOLD}{info['name']}{_RESET}")
        print(f"  {_DIM}{new_desc}{_RESET}\n")
    else:
        projects[slug].pop("description", None)
        save_projects(projects)
        print(f"\n  {_GREEN}Cleared:{_RESET}  {_BOLD}{info['name']}{_RESET}\n")


def cmd_delete(args) -> None:
    slug, info = _find_project(args.name)
    vault = Path(info["path"])
    _check_vault_locks(vault, slug)

    print(f"\n  {_BOLD}{info['name']}{_RESET}  {_DIM}{slug}{_RESET}")
    print(f"  {_CYAN}{vault}{_RESET}")
    print()

    if args.purge:
        print(f"  {_YELLOW}Warning: --purge will permanently delete all vault files from disk.{_RESET}")
        print(f"  {_YELLOW}This cannot be undone.{_RESET}")
        print()
        try:
            answer = input(f"  Delete all files and remove from registry? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return
    else:
        try:
            answer = input(f"  Remove from registry? [y/N] ").strip().lower()
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
        if not (vault / ".watchdog").exists():
            sys.exit(f"Error: {vault} does not look like a watchdog vault — aborting purge.")
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
    if not args.name:
        cwd = Path(".").resolve()
        if (cwd / ".watchdog").is_dir():
            projects = load_projects()
            info = next((v for v in projects.values() if Path(v["path"]).resolve() == cwd), None)
            if info is None:
                sys.exit("Error: current directory is a vault but not registered. Run `watchdog register` first.")
        else:
            sys.exit("Error: not inside a watchdog project. Run `watchdog log <name>` or cd into a project first.")
    else:
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
    if not args.name:
        cwd = Path(".").resolve()
        if (cwd / ".watchdog").is_dir():
            projects = load_projects()
            info = next((v for v in projects.values() if Path(v["path"]).resolve() == cwd), None)
            if info is None:
                sys.exit("Error: current directory is a vault but not registered. Run `watchdog register` first.")
        else:
            sys.exit("Error: not inside a watchdog project. Run `watchdog watch <name>` or cd into a project first.")
    else:
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
                        f"Chewed {label}. {new_queued} file{'s' if new_queued != 1 else ''} ready — run watchdog ingest.",
                    )
                known = set()
            else:
                known = current
    except KeyboardInterrupt:
        print(f"\n  {_DIM}Stopped watching.{_RESET}\n")


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
        health   = _check_project_health(info)
        reg      = _load_registry(vault)
        docs     = str(reg["document_count"]) if reg else "—"
        entities = str(reg["entity_count"])   if reg else "—"
        updated  = _fmt_date(reg["last_updated"]) if reg else "—"
        incoming = str(_count_incoming(vault)) if vault.exists() else "—"
        queued   = str(_count_queued(vault))   if vault.exists() else "—"
        created  = _fmt_date(info.get("created_at", ""))
        size     = _fmt_size(_vault_size(vault)) if vault.exists() else "—"
        is_arch  = bool(info.get("archived"))
        description = info.get("description", "")
        rows.append((info["name"], slug, docs, entities, updated, incoming, queued, is_arch, description, health, created, size))

    name_w = max(len(r[0]) for r in rows) + 2
    slug_w = max(len(r[1]) for r in rows) + 2
    sep_w = name_w + slug_w + 6 + 8 + 7 + 9 + 10 + 8 + 10 + 9 * 2 - 2
    header = (
        f"  {_BOLD}{'Project':<{name_w}}{_RESET}"
        f"  {_DIM}{'Slug':<{slug_w}}"
        f"  {'Docs':>6}"
        f"  {'Entities':>8}"
        f"  {'To chew':>7}"
        f"  {'To ingest':>9}"
        f"  {'Created':>10}"
        f"  {'Size':>8}"
        f"  Updated{_RESET}"
    )
    print(f"\n{header}")
    print(f"  {_DIM}{'─' * sep_w}{_RESET}")
    for name, slug, docs, entities, updated, incoming, queued, is_arch, description, health, created, size in rows:
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
            f"  {_DIM}{created:>10}"
            f"  {size:>8}"
            f"  {updated}{_RESET}"
        )
        if description:
            print(f"    {_DIM}{description}{_RESET}")
        if health:
            print(f"    {_YELLOW}⚠ {health}{_RESET}  {_DIM}run {_RESET}{_CYAN}watchdog move {slug} <path>{_RESET}{_DIM} to relink or {_RESET}{_CYAN}watchdog delete {slug}{_RESET}{_DIM} to remove{_RESET}")
    if archived and not show_all:
        n = len(archived)
        print(f"  {_DIM}+ {n} archived — run {_RESET}{_CYAN}watchdog list --all{_RESET}{_DIM} to show{_RESET}")
    print()


def cmd_status(args) -> None:
    from collections import Counter
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
        print(f"\n  {_DIM}No registry found — run {_RESET}{_CYAN}watchdog chew{_RESET}{_DIM} then {_RESET}{_CYAN}watchdog ingest{_RESET}{_DIM} to begin.{_RESET}\n")
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

    size_str    = _fmt_size(_vault_size(vault))
    schema_ver  = reg.get("schema_version", "unversioned")
    schema_note = "" if schema_ver == VAULT_SCHEMA_VERSION else f"  {_YELLOW}schema v{schema_ver} (current: v{VAULT_SCHEMA_VERSION}){_RESET}"
    print(f"  {_DIM}Created {_fmt_date(info.get('created_at', ''))}  ·  {size_str}  ·  schema v{schema_ver}{_RESET}{schema_note}")
    print()

    pages_note = f" {_DIM}({total_pages} pages){_RESET}" if total_pages else ""
    print(f"  {_BOLD}{reg['document_count']}{_RESET} documents{pages_note} · {_BOLD}{reg['entity_count']}{_RESET} entities · {_DIM}last updated {_fmt_date(reg['last_updated'])}{_RESET}")

    from watchdog.pipeline import orchestrate
    last_usage = orchestrate.latest_usage(vault)
    if last_usage:
        cost = f" · ~${last_usage['cost_usd']:.4f}" if last_usage.get("cost_usd") else ""
        print(f"  {_DIM}Last ingest: {last_usage['input_tokens']:,} in / "
              f"{last_usage['output_tokens']:,} out tokens{cost}{_RESET}")

    if incoming_n:
        print(f"  {_YELLOW}{incoming_n} file{'s' if incoming_n != 1 else ''}{_RESET} in {_CYAN}_INCOMING/{_RESET} {_DIM}— run{_RESET} {_CYAN}watchdog chew{_RESET}")
    if queued_n:
        print(f"  {_YELLOW}{queued_n} file{'s' if queued_n != 1 else ''}{_RESET} chewed and waiting for {_CYAN}watchdog ingest{_RESET}")
    _warn_pending_research(vault)

    if orchestrate.has_pending_finalization(vault):
        p = orchestrate.pending_finalization(vault)
        bits = []
        if p["docs"]:
            bits.append(f"{p['docs']} document{'s' if p['docs'] != 1 else ''}")
        if p["entities"]:
            bits.append(f"{p['entities']} entit{'ies' if p['entities'] != 1 else 'y'} to synthesize")
        detail = f" {_DIM}({', '.join(bits)}){_RESET}" if bits else ""
        print(f"  {_YELLOW}Batch pending finalization{_RESET}{detail} {_DIM}— run{_RESET} {_CYAN}watchdog finalize{_RESET}")

    from watchdog.pipeline import batch_extract
    pending_batch = batch_extract.read_state(vault)
    if pending_batch:
        n = len(pending_batch.get("shas", []))
        print(f"  {_YELLOW}Batch extraction pending{_RESET} {_DIM}({n} document{'s' if n != 1 else ''}, "
              f"{pending_batch.get('batch_id', '?')}) — run{_RESET} {_CYAN}watchdog ingest{_RESET}"
              f"{_DIM} to check on it{_RESET}")

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


def cmd_doctor(args) -> None:
    all_projects = load_projects()
    if not all_projects:
        print(f"\n  No registered investigations.\n")
        return

    struct_issues  = []
    schema_issues  = []
    for slug, info in sorted(all_projects.items(), key=lambda x: x[1]["name"]):
        problem = _check_project_health(info)
        if problem:
            struct_issues.append((slug, info, problem))
        else:
            reg = _load_registry(Path(info["path"]))
            if reg and reg.get("schema_version") != VAULT_SCHEMA_VERSION:
                schema_issues.append((slug, info, reg.get("schema_version", "unversioned")))

    total   = len(all_projects)
    n_issues = len(struct_issues) + len(schema_issues)
    healthy  = total - n_issues
    noun     = "investigation" if total == 1 else "investigations"
    print(f"\n  Checking {total} registered {noun}...")
    print()

    if not n_issues:
        print(f"  {_GREEN}✓ All {total} {noun} healthy{_RESET}")
        print()
        return

    h_noun = "investigation" if healthy == 1 else "investigations"
    print(f"  {_GREEN}✓ {healthy} {h_noun} healthy{_RESET}")
    print()

    for slug, info, problem in struct_issues:
        arch_note = f"  {_DIM}(archived){_RESET}" if info.get("archived") else ""
        print(f"  {_YELLOW}⚠  {_BOLD}{info['name']}{_RESET}  {_DIM}{slug}{_RESET}{arch_note}")
        print(f"     {_DIM}{problem.capitalize()}: {info['path']}{_RESET}")
        print(f"     {_DIM}→ {_RESET}{_CYAN}watchdog move {slug} <new-path>{_RESET}{_DIM} to relink{_RESET}")
        print(f"     {_DIM}→ {_RESET}{_CYAN}watchdog delete {slug}{_RESET}{_DIM} to remove from registry{_RESET}")
        print()

    for slug, info, found_ver in schema_issues:
        arch_note = f"  {_DIM}(archived){_RESET}" if info.get("archived") else ""
        print(f"  {_YELLOW}⚠  {_BOLD}{info['name']}{_RESET}  {_DIM}{slug}{_RESET}{arch_note}")
        print(f"     {_DIM}Schema v{found_ver} — current is v{VAULT_SCHEMA_VERSION}{_RESET}")
        print(f"     {_DIM}This vault may need migration before it is fully compatible.{_RESET}")
        print()


def _search_query_terms(query: str) -> list[str]:
    """Positive-phrase word tokens from a search query, for highlighting matches in
    printed snippets (excludes -phrase exclusions and single-letter tokens)."""
    from watchdog.pipeline.embed import _parse_query, _TOKEN_RE
    pos, _neg = _parse_query(query)
    terms = {m.group(0).lower() for phrase in pos for m in _TOKEN_RE.finditer(phrase)}
    return sorted((t for t in terms if len(t) > 1), key=len, reverse=True)


def _highlight_snippet(text: str, terms: list[str]) -> str:
    """Bold case-insensitive whole-word matches of `terms` within `text` (the rest keeps
    whatever colour the caller already opened, e.g. dim)."""
    if not terms:
        return text
    pattern = re.compile(r"\b(" + "|".join(re.escape(t) for t in terms) + r")\b", re.IGNORECASE)
    return pattern.sub(lambda m: f"{_RESET}{_BOLD}{m.group(0)}{_RESET}{_DIM}", text)


def _windowed_snippet(text: str, terms: list[str], width: int) -> str:
    """Truncate `text` to `width` chars, centred on the first matched term instead of
    always keeping the start — a hit late in a passage would otherwise be cut off."""
    if len(text) <= width:
        return text
    idx = None
    if terms:
        pattern = re.compile("|".join(re.escape(t) for t in terms), re.IGNORECASE)
        match = pattern.search(text)
        if match:
            idx = match.start()
    if idx is None:
        return text[:width] + "…"
    start = max(0, min(idx - width // 2, len(text) - width))
    end   = start + width
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end]}{suffix}"


_EXACT_KIND_LABELS = {
    "corpus": "Source document", "entity": "Entity note", "document": "Document note",
    "timeline": "Timeline", "briefing": "Briefing", "hot": "Hot cache",
    "log": "Run log", "context": "Context",
}


def _build_search_json(query: str, passages: list[dict], notes: list[dict],
                        exact: list[dict] | None = None) -> dict:
    """Shape `watchdog search` results for `--json` consumers (the watchdog-query semantic
    lane, scripts). Each passage carries the citable span (`text`) + its page; `score` is the
    cosine similarity (ordering already reflects fusion + rerank). ``exact`` is the full-text
    (FTS5) lane (#109) — every hit for the exact term/phrase, no relevance score."""
    return {
        "query": query,
        "passages": [
            {"filename": r.get("filename"), "page": r.get("page"),
             "text": r.get("text"), "score": round(float(r["score"]), 4)}
            for r in passages
        ],
        "notes": [
            {"note_path": r.get("note_path"), "preview": r.get("preview"),
             "score": round(float(r["score"]), 4)}
            for r in notes
        ],
        "exact": [
            {"kind": r.get("kind"), "title": r.get("title"), "path": r.get("path"),
             "page": r.get("page"), "text": r.get("text")}
            for r in (exact or [])
        ],
    }


def _read_batch_terms(path: Path) -> list[str]:
    if not path.exists():
        sys.exit(f"Error: batch file not found: {path}")
    terms = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            terms.append(line)
    return terms


def _manifest_matches(manifest: dict, term: str) -> list[dict]:
    """Entities whose name or any alias contains `term` (case-insensitive substring)."""
    t = term.lower()
    hits = []
    for eid, entry in manifest.items():
        names = [entry.get("name", ""), *entry.get("aliases", [])]
        if any(t in n.lower() for n in names if n):
            hits.append({"id": eid, "name": entry.get("name"), "type": entry.get("type"),
                        "note_path": entry.get("note_path")})
    return hits


def cmd_search_batch(args, vault: Path, batch_file: str) -> None:
    """`watchdog search --batch <file>`: one report per term (#110) — every N names in a
    leaked roster, sanctions list, or donor list against manifest entities (structured
    name/alias matches) and the full-text index (#109, every literal occurrence in the
    corpus and every note). Deliberately skips the semantic/embedding lane: a batch is
    routinely hundreds of terms, and embedding + rerank per term doesn't scale the way an
    in-process SQLite query does — manifest + FTS is the fast, exhaustive combination the
    issue calls a "clear no-hits for misses" for.
    """
    from watchdog.pipeline import fulltext
    terms = _read_batch_terms(Path(batch_file))
    if not terms:
        sys.exit(f"Error: no terms found in {batch_file}")

    manifest_path = vault / ".watchdog" / "Registry" / "manifest.json"
    manifest = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    as_json = getattr(args, "json", False)
    limit = args.top_n
    report = []
    for term in terms:
        entities = _manifest_matches(manifest, term)
        try:
            hits = fulltext.search(vault, term, limit=limit)
        except Exception:
            hits = []
        report.append({"term": term, "entities": entities, "hits": hits})

    if as_json:
        print(json.dumps({
            "terms": [
                {"term": r["term"],
                 "entities": r["entities"],
                 "hits": [{"kind": h["kind"], "title": h["title"], "path": h["path"], "page": h["page"]}
                          for h in r["hits"]]}
                for r in report
            ]
        }, ensure_ascii=False))
        return

    print()
    for r in report:
        print(f"  {_BOLD}{r['term']}{_RESET}")
        if not r["entities"] and not r["hits"]:
            print(f"    {_DIM}no hits{_RESET}")
            print()
            continue
        for e in r["entities"]:
            print(f"    {_DIM}entity{_RESET}  {e['note_path']}  {_DIM}({e['type']}){_RESET}")
        for h in r["hits"]:
            label = _EXACT_KIND_LABELS.get(h["kind"], h["kind"]).lower()
            loc = f"p.{h['page']}" if h.get("page") else (h.get("path") or "")
            print(f"    {_DIM}{label}{_RESET}  {h.get('title') or h.get('path')}  {_DIM}{loc}{_RESET}")
        print()


def cmd_search(args) -> None:
    batch_file = getattr(args, "batch", None)
    if batch_file:
        project_arg = args.project
        if args.query:
            sys.exit("Error: --batch reads terms from a file; drop the search query argument.")
        if project_arg:
            _, info = _find_project(project_arg)
        else:
            projects = load_projects()
            cwd = Path(".").resolve()
            match = next(((s, v) for s, v in projects.items() if Path(v["path"]).resolve() == cwd), None)
            if match is None:
                sys.exit("Error: not inside a Watchdog project — pass a project name.")
            _, info = match
        cmd_search_batch(args, Path(info["path"]), batch_file)
        return

    project_arg = args.project
    query_arg   = args.query

    if project_arg and query_arg:
        _, info = _find_project(project_arg)
        args.query = query_arg
    elif project_arg and not query_arg:
        projects  = load_projects()
        slug_try  = slugify(project_arg)
        is_known  = slug_try in projects or any(k.startswith(slug_try) for k in projects)
        if is_known:
            sys.exit("Error: please provide a search query.")
        cwd   = Path(".").resolve()
        match = next(((s, v) for s, v in projects.items() if Path(v["path"]).resolve() == cwd), None)
        if match is None:
            sys.exit(f"Project not found: {project_arg}\nRun 'watchdog list' to see all projects.")
        _, info = match
        args.query = project_arg
    else:
        sys.exit("Error: please provide a search query.")

    vault = Path(info["path"])

    as_json = getattr(args, "json", False)

    from watchdog.pipeline.embed import search, index_stats
    stats = index_stats(vault)
    if stats["total"] == 0:
        if as_json:
            print(json.dumps(_build_search_json(args.query, [], [])))
        else:
            print(f"\n  {_DIM}No embeddings found. Index is built automatically during ingest.{_RESET}\n")
        return

    # Without an explicit --threshold, don't filter (cosine ≥ -1 always holds), so a plain
    # search always returns its top-N; --threshold opts into hiding weak matches.
    min_score = args.threshold if args.threshold is not None else -1.0
    rerank = not getattr(args, "no_rerank", False)
    passages = search(vault, args.query, top_n=args.top_n, min_score=min_score, scope="corpus", rerank=rerank)
    notes    = search(vault, args.query, top_n=args.top_n, min_score=min_score, scope="notes")

    from watchdog.pipeline import fulltext
    try:
        exact = fulltext.search(vault, args.query, limit=args.top_n)
    except Exception as e:
        exact = []
        if not as_json:
            print(f"  {_YELLOW}Warning: exact-match search unavailable: {e}{_RESET}", file=sys.stderr)

    if as_json:
        print(json.dumps(_build_search_json(args.query, passages, notes, exact), ensure_ascii=False))
        return

    print()
    if not passages and not notes and not exact:
        hint = f" above {args.threshold:.2f}" if args.threshold is not None else ""
        print(f"  {_DIM}No results{hint}.{_RESET}\n")
        return

    full  = getattr(args, "full", False)
    terms = _search_query_terms(args.query)

    if exact:
        print(f"  {_BOLD}Exact matches{_RESET}\n")
        for r in exact:
            snippet = (r.get("text") or "").replace("\n", " ").strip()
            if not full:
                snippet = _windowed_snippet(snippet, terms, 240)
            snippet = _highlight_snippet(snippet, terms)
            if r["kind"] == "corpus":
                where = f"p.{r.get('page')}"
                if r.get("path"):
                    where += f"  {_DIM}{r['path']}#page={r.get('page')}{_RESET}"
                print(f"  {_BOLD}{r.get('title') or '?'}{_RESET}  {_DIM}{where}{_RESET}")
            else:
                label = _EXACT_KIND_LABELS.get(r["kind"], r["kind"])
                print(f"  {_BOLD}{r.get('path')}{_RESET}  {_DIM}{label}{_RESET}")
            print(f"  {_DIM}{snippet}{_RESET}")
            print()

    if passages:
        print(f"  {_BOLD}Source passages{_RESET}\n")
        for r in passages:
            score   = f"{r['score']:.2f}"
            snippet = r.get("text", "").replace("\n", " ").strip()
            if not full:
                snippet = _windowed_snippet(snippet, terms, 240)
            snippet = _highlight_snippet(snippet, terms)
            print(f"  {_BOLD}{r.get('filename', '?')}{_RESET}  {_DIM}p.{r.get('page')}  score {score}{_RESET}")
            print(f"  {_DIM}{snippet}{_RESET}")
            print()

    if notes:
        print(f"  {_BOLD}Notes{_RESET}\n")
        for r in notes:
            score   = f"{r['score']:.2f}"
            preview = r.get("preview", "").replace("\n", " ").strip()
            if not full:
                preview = _windowed_snippet(preview, terms, 200)
            preview = _highlight_snippet(preview, terms)
            print(f"  {_BOLD}{r['note_path']}{_RESET}  {_DIM}score {score}{_RESET}")
            print(f"  {_DIM}{preview}{_RESET}")
            print()
