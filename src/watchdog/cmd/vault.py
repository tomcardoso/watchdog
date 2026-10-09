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

from watchdog.vault_paths import CONTEXT_NAME, INCOMING_NAME, incoming_dir, is_vault
from watchdog import interactive
from watchdog.cmd.base import (
    VAULT_SCHEMA_VERSION,
    _BOLD, _CYAN, _DIM, _GREEN, _RESET, _YELLOW,
    _check_project_health,
    _ensure_layout,
    _check_vault_locks,
    _count_awaiting_bark,
    _count_awaiting_dig,
    _count_incoming,
    _count_queued,
    _warn_pending_research,
    _find_project,
    _registered_project,
    _fmt_date,
    _fmt_size,
    _load_registry,
    _notify,
    _projects_dir,
    _render_template,
    _vault_settings,
    _vault_size,
    load_projects,
    save_projects,
    slugify,
)
from watchdog.pipeline.json_io import _read_json, _read_json_or
from watchdog.vault_paths import processing_log


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
    name: Organizations
    filters:
      and:
        - file.inFolder("entities/organization")
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
    name: Possible duplicate documents
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
        pass  # non-fatal — user can register manually via watchdog open


def _obsidian_vault_ts(vault: Path):
    """The registration timestamp (ms since epoch) for `vault` in obsidian.json, or None."""
    cfg = _obsidian_config_path()
    if not cfg.exists():
        return None
    try:
        data = json.loads(cfg.read_text())
        for v in data.get("vaults", {}).values():
            if v.get("path") == str(vault):
                return v.get("ts")
    except Exception:
        return None
    return None


def _obsidian_launch_epoch():
    """Epoch seconds when the running Obsidian was launched, or None if it is not
    running or the start time can't be determined.

    Obsidian reads obsidian.json only at startup, so a vault registered after
    launch is invisible to the running instance until it is restarted.
    """
    if sys.platform == "win32":
        return None
    try:
        out = subprocess.run(["pgrep", "-x", "Obsidian"], capture_output=True, text=True)
    except Exception:
        return None
    pids = [p for p in out.stdout.split() if p.strip()]
    if not pids:
        return None
    starts = []
    for pid in pids:
        try:
            r = subprocess.run(["ps", "-o", "lstart=", "-p", pid], capture_output=True, text=True)
            s = r.stdout.strip()
            if s:
                starts.append(datetime.strptime(s, "%a %b %d %H:%M:%S %Y").timestamp())
        except Exception:
            continue
    return min(starts) if starts else None


def cmd_register(args) -> None:
    vault = Path(args.path).expanduser().resolve() if args.path else Path.cwd()

    if not vault.exists():
        sys.exit(f"Error: path not found: {vault}")
    if not is_vault(vault):
        sys.exit(f"Error: {vault} does not look like a watchdog vault — no .watchdog folder found.")

    try:
        reg = _load_registry(vault)
    except json.JSONDecodeError as e:
        sys.exit(f"Error: registry file is corrupt — {e}\nRun 'watchdog settings doctor' to diagnose.")
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
        sys.exit(f"Error: a project with slug '{slug}' is already registered. Use 'watchdog projects rename' or choose a different name.")

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

    # Most filesystems (APFS, ext4, ...) cap a single path component at 255 bytes — a slug
    # past that raises an uncaught OSError from mkdir() below. Check bytes, not characters:
    # slugify passes through non-ASCII word characters, which can be several bytes each in UTF-8.
    if len(slug.encode("utf-8")) > 255:
        sys.exit("Error: project name is too long once slugified — shorten it.")

    parent = Path(args.dir).expanduser().resolve() if args.dir else _projects_dir()
    vault = parent / slug

    if vault.exists():
        sys.exit(f"Error: {vault} already exists.")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    today = datetime.now().strftime("%Y-%m-%d")

    for d in [
        INCOMING_NAME,
        CONTEXT_NAME,
        "morgue",
        ".watchdog/registry",
        ".watchdog/queue",
        ".watchdog/staging",
        ".watchdog/timeline",
        ".watchdog/tmp",
        "entities",
        "documents",
        "briefings",
        "wiki",
        "queries",
        ".obsidian/plugins",
        ".obsidian/snippets",
        ".claude",
    ]:
        (vault / d).mkdir(parents=True)

    (vault / ".watchdog" / "registry" / "documents.json").write_text("{}\n")
    (vault / ".watchdog" / "registry" / "entities.json").write_text("{}\n")
    (vault / ".watchdog" / "registry" / "manifest.json").write_text("{}\n")
    (vault / ".watchdog" / "registry" / "registry.json").write_text(
        json.dumps(
            {"schema_version": "1", "created_at": now, "last_updated": now,
             "document_count": 0, "entity_count": 0,
             "entity_note_format": _note_format()},
            indent=2,
        ) + "\n"
    )
    (processing_log(vault)).write_text("")

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
                # One group per canonical entity folder (D105) plus documents. Ingest adds a
                # group for any further folder it creates (orchestrate._update_graph_colours).
                "colorGroups": [
                    {"query": "path:entities/person",       "color": {"a": 1, "rgb": 4886745}},   # #4A90D9 blue
                    {"query": "path:entities/organization", "color": {"a": 1, "rgb": 5999451}},   # #5BA95B green
                    {"query": "path:entities/place",        "color": {"a": 1, "rgb": 15238714}},  # #E8863A orange
                    {"query": "path:entities/public-body",  "color": {"a": 1, "rgb": 9323693}},   # #8E44AD purple
                    {"query": "path:entities/proceeding",   "color": {"a": 1, "rgb": 12597547}},  # #C0392B red
                    {"query": "path:entities/asset",        "color": {"a": 1, "rgb": 1482885}},   # #16A085 teal
                    {"query": "path:documents",             "color": {"a": 1, "rgb": 9145227}},   # #8B8B8B grey
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

    (vault / ".claude" / "settings.json").write_text(json.dumps(_vault_settings(), indent=2) + "\n")

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
    print(f"    1. {_DIM}(optional){_RESET} Drop background material into {_CYAN}{vault}/context/{_RESET} and run {_CYAN}watchdog ask --context{_RESET}")
    print(f"    2. Run {_CYAN}watchdog add <files or folders>{_RESET} to add documents "
          f"{_DIM}(or drop them into incoming/ and run {_RESET}{_CYAN}watchdog add{_RESET}{_DIM}){_RESET}")
    print(f"    3. Run {_CYAN}watchdog open {slug}{_RESET} to open the vault in Obsidian")
    print()


def cmd_obsidian(args) -> None:
    info = _registered_project(args.name, "open")
    vault = Path(info["path"])
    if not vault.exists():
        sys.exit(f"Error: project directory not found: {vault}")
    if not _obsidian_registered(vault):
        print(f"\n  {_YELLOW}Vault not registered in Obsidian yet.{_RESET}")
        print()
        print(f"  Open Obsidian → {_BOLD}Open folder as vault{_RESET} → navigate to:")
        print(f"  {_CYAN}{vault}{_RESET}")
        print()
        print(f"  After opening it once, {_CYAN}watchdog open {info['name']}{_RESET} will work automatically.\n")
        return
    launch = _obsidian_launch_epoch()
    ts = _obsidian_vault_ts(vault)
    if launch is not None and ts is not None and launch < ts / 1000:
        # Obsidian is running but was launched before this vault was registered,
        # so its in-memory vault list doesn't include it — the URI would fail
        # with "Vault not found". Ask the user to restart Obsidian.
        print(f"\n  {_YELLOW}Obsidian is already running but hasn't loaded this vault yet.{_RESET}")
        print()
        print(f"  Obsidian only reads its vault list when it starts, and {_BOLD}{info['name']}{_RESET}")
        print("  was registered afterwards.")
        print()
        print(f"  Quit Obsidian completely, then run {_CYAN}watchdog open {info['name']}{_RESET} again.\n")
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
        sys.exit("Error: watchdog open is not supported on this platform")
    result = subprocess.run(opener, capture_output=True)
    if result.returncode != 0:
        sys.exit("Error: could not open Obsidian — is it installed?")
    print(f"\n  {_GREEN}Opened:{_RESET} {_BOLD}{info['name']}{_RESET} in Obsidian\n")


def cmd_open(args) -> None:
    if not getattr(args, "folder", False):
        return cmd_obsidian(args)
    info = _registered_project(args.name, "open")
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
        sys.exit("Error: could not open file explorer")
    print(f"\n  {_GREEN}Opened:{_RESET} {_CYAN}{vault}{_RESET}\n")


def _relocate_telemetry(old_resolved: Path, new: Path) -> None:
    try:
        from watchdog import telemetry_db
        telemetry_db.relocate_vault(old_resolved, new)
    except Exception as e:
        print(f"  {_YELLOW}Warning:{_RESET} could not update this vault's rows in the telemetry "
              f"store: {e}")


def cmd_rename(args) -> None:
    first    = args.project
    new_name = args.name.strip() if args.name else None

    if first is not None and new_name is None:
        projects = load_projects()
        cwd = Path(".").resolve()
        in_vault = any(Path(v["path"]).resolve() == cwd for v in projects.values())
        slug_try = slugify(first)
        # Inside a vault a lone argument is the new value unless it names a project exactly;
        # prefix-matching here turned `rename Shell` into "rename the shell-company project".
        is_known = slug_try in projects or (
            not in_vault and any(k.startswith(slug_try) for k in projects))
        if is_known:
            slug, info = _find_project(first)
        else:
            cwd   = Path(".").resolve()
            match = next(((s, v) for s, v in projects.items() if Path(v["path"]).resolve() == cwd), None)
            if match is None:
                sys.exit(f"Project not found: {first}\nRun 'watchdog projects list' to see all projects.")
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
        old_resolved = vault.resolve()
        vault.rename(new_vault)
        _relocate_telemetry(old_resolved, new_vault)

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
        cwd = Path(".").resolve()
        in_vault = any(Path(v["path"]).resolve() == cwd for v in projects.values())
        slug_try = slugify(first)
        # Inside a vault a lone argument is the new value unless it names a project exactly;
        # prefix-matching here turned `rename Shell` into "rename the shell-company project".
        is_known = slug_try in projects or (
            not in_vault and any(k.startswith(slug_try) for k in projects))
        if is_known:
            slug, info = _find_project(first)
        else:
            cwd   = Path(".").resolve()
            match = next(((s, v) for s, v in projects.items() if Path(v["path"]).resolve() == cwd), None)
            if match is None:
                sys.exit(f"Project not found: {first}\nRun 'watchdog projects list' to see all projects.")
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

    if getattr(args, "yes", False):
        answer = True
    elif args.purge:
        print(f"  {_YELLOW}Warning: --purge will permanently delete all vault files from disk.{_RESET}")
        print(f"  {_YELLOW}This cannot be undone.{_RESET}")
        print()
        answer = interactive.confirm("  Delete all files and remove from registry?", default=False)
    else:
        answer = interactive.confirm("  Remove from registry?", default=False)

    if not answer:
        print(f"\n  {_DIM}Cancelled.{_RESET}\n")
        return

    projects = load_projects()
    del projects[slug]
    save_projects(projects)

    if args.purge:
        # Before the folder is gone, so symlinks still resolve. A folder already deleted by hand
        # (or on an unmounted drive) still has its telemetry rows purged (D261).
        resolved = vault.resolve()
        # Another registered investigation at the same path owns the folder and its rows now.
        shared = any(Path(info.get("path", "")).expanduser().resolve() == resolved
                     for info in projects.values() if info.get("path"))
        if shared:
            print(f"  {_YELLOW}Kept the files:{_RESET} another investigation in the list uses "
                  f"{vault}.")
        elif vault.exists():
            if not is_vault(vault):
                sys.exit(f"Error: {vault} does not look like a watchdog vault — aborting purge.")
            shutil.rmtree(vault)
        try:
            from watchdog import telemetry_db
            if not shared:
                telemetry_db.purge_vault(resolved)
        except Exception as e:
            print(f"  {_YELLOW}Warning:{_RESET} could not remove this vault's rows from the "
                  f"telemetry store: {e}")

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
    print(f"\n  {_GREEN}{label}:{_RESET} {_BOLD}{info['name']}{_RESET}")
    print()


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
    src_resolved = src.resolve()      # before the move, so a symlinked path still resolves
    if src.exists():
        try:
            shutil.move(str(src), str(dst))
        except shutil.Error as e:
            # Raised by the copytree fallback (cross-filesystem move) when the vault contains a
            # file it can't copy — a FIFO, socket, or device node. `e.args[0]` is a list of
            # (src, dst, error_string) triples, one per file that blocked the move.
            blocked = "\n".join(f"  {s}: {err}" for s, _d, err in e.args[0]) if e.args else str(e)
            sys.exit(f"Error: could not move {src} — some files could not be copied:\n{blocked}")
        moved = True
    elif not dst.exists():
        sys.exit(
            f"Error: {src} not found and {dst} does not exist — nothing to update.\n"
            f"Move the vault manually first, then re-run: watchdog projects move {slug} <new-path>"
        )

    projects = load_projects()
    projects[slug]["path"] = str(dst)
    save_projects(projects)
    _relocate_telemetry(src_resolved, dst)

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
    print(f"\n  {_GREEN}Archived:{_RESET} {_BOLD}{info['name']}{_RESET}  {_DIM}hidden from watchdog projects list{_RESET}\n")


def cmd_unarchive(args) -> None:
    slug, info = _find_project(args.name)
    projects = load_projects()
    projects[slug].pop("archived", None)
    save_projects(projects)
    print(f"\n  {_GREEN}Unarchived:{_RESET} {_BOLD}{info['name']}{_RESET}\n")


def cmd_log(args) -> None:
    info = _registered_project(args.name, "projects log")
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


def _poll_stable_files(candidates: set, pending_sizes: dict) -> tuple:
    """Split newly-seen files into size-stable (safe to chew) vs. still-growing.

    A file mid-copy (Finder, a network share) must not be chewed until its size holds
    steady across two polls — otherwise chew hashes/OCRs truncated bytes (#261).
    """
    ready, new_pending = [], {}
    for f in candidates:
        try:
            size = f.stat().st_size
        except OSError:
            continue
        if pending_sizes.get(f) == size:
            ready.append(f)          # unchanged since the last poll — copy finished
        else:
            new_pending[f] = size    # still growing (or first sighting) — wait
    return ready, new_pending


def cmd_watch(args) -> None:
    info = _registered_project(args.name, "add --watch")
    vault = Path(info["path"])
    if not vault.exists():
        sys.exit(f"Error: project directory not found: {vault}")
    _ensure_layout(vault)

    from watchdog.pipeline.preprocess_batch import run_ingest, find_files
    import time as _time

    incoming = incoming_dir(vault)
    print(f"\n  {_BOLD}{info['name']}{_RESET}  watching {_CYAN}incoming/{_RESET} — press Ctrl+C to stop.\n")

    # Files already waiting are chewed on the first stable poll, like any new arrival — they used
    # to be ignored until some other file happened to arrive.
    known: set = set()
    waiting = len(find_files([incoming]))
    if waiting:
        print(f"  {_DIM}{waiting} file{'s' if waiting != 1 else ''} already in incoming/ — chewing "
              f"them first.{_RESET}\n")
    pending_sizes: dict = {}   # file -> size at the previous poll, until it stops growing (#261)

    try:
        while True:
            _time.sleep(3)
            current: set = set(find_files([incoming]))
            ready, pending_sizes = _poll_stable_files(current - known, pending_sizes)
            if ready:
                n = len(ready)
                label = f"{n} file{'s' if n != 1 else ''}"
                print(f"  {_BOLD}{label}{_RESET} detected — chewing...\n")
                queued_before = _count_queued(vault)
                run_ingest(vault, files=ready)
                new_queued = _count_queued(vault) - queued_before
                if new_queued > 0:
                    _notify(
                        f"Watchdog — {info['name']}",
                        f"Chewed {label}. {new_queued} file{'s' if new_queued != 1 else ''} ready — run watchdog dig.",
                    )
                known = set()
            else:
                known = current - set(pending_sizes)
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
            print(f"\n  No active investigations. {len(archived)} archived — run {_CYAN}watchdog projects list --all{_RESET} to show.\n")
        else:
            print(f"\n  No projects. Create one with: {_CYAN}watchdog new <name>{_RESET}\n")
        return

    rows = []
    for slug, info in sorted(visible.items(), key=lambda x: x[1]["name"]):
        vault = Path(info["path"])
        health   = _check_project_health(info)
        registry_corrupt = False
        try:
            reg = _load_registry(vault)
        except json.JSONDecodeError:
            reg = None
            registry_corrupt = True
        docs     = str(reg["document_count"]) if reg else "—"
        entities = str(reg["entity_count"])   if reg else "—"
        updated  = _fmt_date(reg["last_updated"]) if reg else "—"
        incoming = str(_count_incoming(vault))    if vault.exists() else "—"
        # Split rather than a single "queued" count (#461): a queue file persists until `bark`
        # commits it, so post-dig/bark split, "queued" alone conflates "not yet dug" with "dug,
        # awaiting bark" — two very different states for a vault that may stay dig-only forever
        # (e.g. an extractor-only benchmark arm).
        awaiting_dig  = str(_count_awaiting_dig(vault))  if vault.exists() else "—"
        awaiting_bark = str(_count_awaiting_bark(vault)) if vault.exists() else "—"
        created  = _fmt_date(info.get("created_at", ""))
        size     = _fmt_size(_vault_size(vault)) if vault.exists() else "—"
        is_arch  = bool(info.get("archived"))
        description = info.get("description", "")
        rows.append((info["name"], slug, docs, entities, updated, incoming, awaiting_dig,
                    awaiting_bark, is_arch, description, health, created, size, registry_corrupt))

    name_w = max(len(r[0]) for r in rows) + 2
    slug_w = max(len(r[1]) for r in rows) + 2
    header = (
        f"  {_BOLD}{'Project':<{name_w}}{_RESET}"
        f"  {_DIM}{'Slug':<{slug_w}}"
        f"  {'Docs':>6}"
        f"  {'Entities':>8}"
        f"  {'Awaiting chew':>13}"
        f"  {'Awaiting dig':>12}"
        f"  {'Awaiting bark':>13}"
        f"  {'Created':>10}"
        f"  {'Size':>8}"
        f"  Updated{_RESET}"
    )
    sep_w = len(re.sub(r"\033\[[0-9;]*m", "", header)) - 2
    print(f"\n{header}")
    print(f"  {_DIM}{'─' * sep_w}{_RESET}")
    for (name, slug, docs, entities, updated, incoming, awaiting_dig, awaiting_bark,
         is_arch, description, health, created, size, registry_corrupt) in rows:
        inc = "—" if incoming == "0" else incoming
        dig = "—" if awaiting_dig  == "0" else awaiting_dig
        bark = "—" if awaiting_bark == "0" else awaiting_bark
        if is_arch:
            inc_str = f"{_DIM}{inc:>13}{_RESET}"
            dig_str = f"{_DIM}{dig:>12}{_RESET}"
            bark_str = f"{_DIM}{bark:>13}{_RESET}"
            name_str = f"  {_DIM}{name:<{name_w}}{_RESET}"
        else:
            inc_str = f"{_YELLOW}{inc:>13}{_RESET}" if inc != "—" else f"{_DIM}{inc:>13}{_RESET}"
            dig_str = f"{_YELLOW}{dig:>12}{_RESET}" if dig != "—" else f"{_DIM}{dig:>12}{_RESET}"
            bark_str = f"{_YELLOW}{bark:>13}{_RESET}" if bark != "—" else f"{_DIM}{bark:>13}{_RESET}"
            name_str = f"  {_BOLD}{name:<{name_w}}{_RESET}"
        print(
            f"{name_str}"
            f"  {_DIM}{slug:<{slug_w}}"
            f"  {docs:>6}"
            f"  {entities:>8}{_RESET}"
            f"  {inc_str}"
            f"  {dig_str}"
            f"  {bark_str}"
            f"  {_DIM}{created:>10}"
            f"  {size:>8}"
            f"  {updated}{_RESET}"
        )
        if description:
            print(f"    {_DIM}{description}{_RESET}")
        if health:
            print(f"    {_YELLOW}⚠ {health}{_RESET}  {_DIM}run {_RESET}{_CYAN}watchdog projects move {slug} <path>{_RESET}{_DIM} to relink or {_RESET}{_CYAN}watchdog projects delete {slug}{_RESET}{_DIM} to remove{_RESET}")
        elif registry_corrupt:
            print(f"    {_YELLOW}⚠ registry file is corrupt{_RESET}  {_DIM}run {_RESET}{_CYAN}watchdog settings doctor{_RESET}{_DIM} to diagnose{_RESET}")
    if archived and not show_all:
        n = len(archived)
        print(f"  {_DIM}+ {n} archived — run {_RESET}{_CYAN}watchdog projects list --all{_RESET}{_DIM} to show{_RESET}")
    print()


def cmd_status(args) -> None:
    from collections import Counter
    if not args.name:
        cwd = Path(".").resolve()
        if is_vault(cwd):
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

    try:
        reg = _load_registry(vault)
    except json.JSONDecodeError as e:
        sys.exit(f"Error: registry file is corrupt — {e}\nRun 'watchdog settings doctor' to diagnose.")
    if not reg:
        print(f"\n  {_BOLD}{info['name']}{_RESET}")
        print(f"  {_CYAN}{info['path']}{_RESET}")
        if info.get("description"):
            print(f"  {_DIM}{info['description']}{_RESET}")
        print(f"  {_DIM}Created {_fmt_date(info.get('created_at', ''))}{_RESET}")
        print(f"\n  {_DIM}No registry found — run {_RESET}{_CYAN}watchdog chew{_RESET}{_DIM} then {_RESET}{_CYAN}watchdog dig{_RESET}{_DIM} to begin.{_RESET}\n")
        return

    docs_file = vault / ".watchdog" / "registry" / "documents.json"
    ents_file = vault / ".watchdog" / "registry" / "entities.json"
    try:
        docs_data = _read_json(docs_file) if docs_file.exists() else {}
        ents_data = _read_json(ents_file) if ents_file.exists() else {}
    except json.JSONDecodeError as e:
        sys.exit(f"Error: registry file is corrupt — {e}\nRun 'watchdog settings doctor' to diagnose.")

    total_pages = sum(d.get("page_count", 0) for d in docs_data.values())
    doc_types   = Counter(d["document_type"] for d in docs_data.values() if d.get("document_type"))
    ent_types   = Counter(e["type"]          for e in ents_data.values() if e.get("type"))
    incoming_n = _count_incoming(vault)
    awaiting_dig_n  = _count_awaiting_dig(vault)
    awaiting_bark_n = _count_awaiting_bark(vault)

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
        print(f"  {_YELLOW}{incoming_n} file{'s' if incoming_n != 1 else ''}{_RESET} in {_CYAN}incoming/{_RESET} {_DIM}— run{_RESET} {_CYAN}watchdog chew{_RESET}")
    if awaiting_dig_n:
        print(f"  {_YELLOW}{awaiting_dig_n} file{'s' if awaiting_dig_n != 1 else ''}{_RESET} chewed and awaiting {_CYAN}watchdog dig{_RESET}")
    if awaiting_bark_n:
        print(f"  {_YELLOW}{awaiting_bark_n} file{'s' if awaiting_bark_n != 1 else ''}{_RESET} dug and awaiting {_CYAN}watchdog bark{_RESET}")
    _warn_pending_research(vault)

    if orchestrate.has_pending_finalization(vault):
        p = orchestrate.pending_finalization(vault)
        bits = []
        if p["docs"]:
            bits.append(f"{p['docs']} document{'s' if p['docs'] != 1 else ''}")
        if p["entities"]:
            bits.append(f"{p['entities']} entit{'ies' if p['entities'] != 1 else 'y'} to synthesize")
        detail = f" {_DIM}({', '.join(bits)}){_RESET}" if bits else ""
        print(f"  {_YELLOW}Batch waiting to be finished{_RESET}{detail} {_DIM}— run{_RESET} {_CYAN}watchdog bark{_RESET}")

    from watchdog.pipeline import batch_extract
    pending_batch = batch_extract.read_state(vault)
    if pending_batch:
        n = len(pending_batch.get("shas", []))
        print(f"  {_YELLOW}Batch extraction pending{_RESET} {_DIM}({n} document{'s' if n != 1 else ''}, "
              f"{pending_batch.get('batch_id', '?')}) — run{_RESET} {_CYAN}watchdog dig{_RESET}"
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


def doctor_findings(all_projects: dict) -> tuple[list, list, list]:
    """The three kinds of problem `watchdog doctor` reports, as `(struct_issues, schema_issues,
    corrupt_registries)` lists of `(slug, info[, detail])` tuples, in project-name order. Shared
    with the desktop app, which presents the same findings without the terminal layout."""
    struct_issues  = []
    schema_issues  = []
    corrupt_registries = []
    for slug, info in sorted(all_projects.items(), key=lambda x: x[1]["name"]):
        problem = _check_project_health(info)
        if problem:
            struct_issues.append((slug, info, problem))
        else:
            try:
                reg = _load_registry(Path(info["path"]))
            except json.JSONDecodeError:
                corrupt_registries.append((slug, info))
                continue
            if reg and reg.get("schema_version") != VAULT_SCHEMA_VERSION:
                schema_issues.append((slug, info, reg.get("schema_version", "unversioned")))
    return struct_issues, schema_issues, corrupt_registries


def cmd_doctor(args) -> None:
    all_projects = load_projects()
    if not all_projects:
        print("\n  No registered investigations.\n")
        return

    struct_issues, schema_issues, corrupt_registries = doctor_findings(all_projects)

    total   = len(all_projects)
    n_issues = len(struct_issues) + len(schema_issues) + len(corrupt_registries)
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
        print(f"     {_DIM}→ {_RESET}{_CYAN}watchdog projects move {slug} <new-path>{_RESET}{_DIM} to relink{_RESET}")
        print(f"     {_DIM}→ {_RESET}{_CYAN}watchdog projects delete {slug}{_RESET}{_DIM} to remove from registry{_RESET}")
        print()

    for slug, info, found_ver in schema_issues:
        arch_note = f"  {_DIM}(archived){_RESET}" if info.get("archived") else ""
        print(f"  {_YELLOW}⚠  {_BOLD}{info['name']}{_RESET}  {_DIM}{slug}{_RESET}{arch_note}")
        print(f"     {_DIM}Schema v{found_ver} — current is v{VAULT_SCHEMA_VERSION}{_RESET}")
        print(f"     {_DIM}This vault may need migration before it is fully compatible.{_RESET}")
        print()

    for slug, info in corrupt_registries:
        arch_note = f"  {_DIM}(archived){_RESET}" if info.get("archived") else ""
        reg_path = Path(info["path"]) / ".watchdog" / "registry" / "registry.json"
        print(f"  {_YELLOW}⚠  {_BOLD}{info['name']}{_RESET}  {_DIM}{slug}{_RESET}{arch_note}")
        print(f"     {_DIM}Registry file is corrupt: {reg_path}{_RESET}")
        print(f"     {_DIM}It's a regenerated summary cache — delete it and it will rebuild on the next {_RESET}{_CYAN}watchdog dig{_RESET}{_DIM} or {_RESET}{_CYAN}watchdog bark{_RESET}{_DIM}.{_RESET}")
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


# Facts listed beside one search hit (`--json`), so a session can cite what it found (D283).
_SEARCH_FACTS_PER_HIT = 12


def _page_facts(vault: Path):
    """A function (sha, page) -> the facts of that page of that document, each with its citation
    link, for `--json` search hits. Facts whose matched passage is on the page count too."""
    from watchdog.pipeline import citations, entity_facts
    index = entity_facts.FactIndex(vault)

    def facts(sha: str | None, page) -> list[dict]:
        if not sha or not isinstance(page, int):
            return []
        out = []
        for f in index.document_facts(sha):
            if page in (f.get("page"), f.get("passage_page")) and f.get("note"):
                out.append({"id": f["id"], "cite": citations.link(f), "fact": f.get("fact"),
                            "page": f.get("page"), "mark": (f.get("mark") or {}).get("status")})
        return out[:_SEARCH_FACTS_PER_HIT]
    return facts, index


def _build_search_json(query: str, passages: list[dict], notes: list[dict],
                       exact: list[dict] | None = None, vault: Path | None = None) -> dict:
    """Shape `watchdog search` results for `--json` consumers (the watchdog-query semantic
    lane, scripts). Each passage carries the citable span (`text`) + its page; `score` is the
    cosine similarity (ordering already reflects fusion + rerank). ``exact`` is the full-text
    (FTS5) lane (#109) — every hit for the exact term/phrase, no relevance score.

    This JSON shape is a stable contract for machine consumers (the `watchdog-query` skill,
    src/watchdog/skills/watchdog-query.md, and any other script parsing `--json` output) — unlike
    the human-readable text output, which may change freely, a field rename or removal here is a
    breaking change and should not be made casually (#499).

    With `vault`, each passage and each exact match in a document's text also carries `facts`:
    the facts recorded on that page, each with its D271 `id`, the `cite` link to paste into a
    page (`[[documents/<slug>#^f-<hash>|p. 4]]`), its text, page and the reporter's mark (D283)."""
    facts_on, index = _page_facts(vault) if vault is not None else (None, None)
    sha_by_name: dict = {}
    if index is not None:
        for sha, d in index.documents.items():
            if isinstance(d, dict):
                sha_by_name.setdefault(d.get("filename"), []).append(sha)

    def with_facts(item: dict, sha: str | None) -> dict:
        if facts_on is not None:
            item["facts"] = facts_on(sha, item.get("page"))
        return item

    def by_name(name) -> str | None:
        shas = sha_by_name.get(name) or []
        return shas[0] if len(shas) == 1 else None

    return {
        "query": query,
        "passages": [
            with_facts({"filename": r.get("filename"), "page": r.get("page"),
                        "text": r.get("text"), "score": round(float(r["score"]), 4)},
                       by_name(r.get("filename")))
            for r in passages
        ],
        "notes": [
            {"note_path": r.get("note_path"), "preview": r.get("preview"),
             "score": round(float(r["score"]), 4)}
            for r in notes
        ],
        "exact": [
            with_facts({"kind": r.get("kind"), "title": r.get("title"), "path": r.get("path"),
                        "page": r.get("page"), "text": r.get("text")},
                       r.get("key") if r.get("kind") == "corpus" else None)
            if r.get("kind") == "corpus" else
            {"kind": r.get("kind"), "title": r.get("title"), "path": r.get("path"),
             "page": r.get("page"), "text": r.get("text")}
            for r in (exact or [])
        ],
    }


def _note_format() -> int:
    from watchdog.pipeline.entity_notes import NOTE_FORMAT
    return NOTE_FORMAT


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


def batch_report(vault: Path, terms: list[str], limit: int) -> tuple[list[dict], int]:
    """One entry per term — `{term, entities, hits, error}` — from the vault's manifest (name and
    alias matches) and its full-text index, plus how many exact-match lookups failed. A failed
    lookup carries its `error` and must never be read as "no hits". Shared with the desktop app."""
    from watchdog.pipeline import fulltext
    manifest_path = vault / ".watchdog" / "registry" / "manifest.json"
    manifest = {}
    if manifest_path.exists():
        manifest = _read_json_or(manifest_path, {}, catch=(json.JSONDecodeError,))

    report = []
    failures = 0
    for term in terms:
        entities = _manifest_matches(manifest, term)
        try:
            hits, error = fulltext.search(vault, term, limit=limit), None
        except Exception as e:
            hits, error = [], str(e)
            failures += 1
        report.append({"term": term, "entities": entities, "hits": hits, "error": error})
    return report, failures


def cmd_search_batch(args, vault: Path, batch_file: str) -> None:
    """`watchdog search --batch <file>`: one report per term (#110) — every N names in a
    leaked roster, sanctions list, or donor list against manifest entities (structured
    name/alias matches) and the full-text index (#109, every literal occurrence in the
    corpus and every note). Deliberately skips the semantic/embedding lane: a batch is
    routinely hundreds of terms, and embedding + rerank per term doesn't scale the way an
    in-process SQLite query does — manifest + FTS is the fast, exhaustive combination the
    issue calls a "clear no-hits for misses" for.
    """
    terms = _read_batch_terms(Path(batch_file))
    if not terms:
        sys.exit(f"Error: no terms found in {batch_file}")

    as_json = getattr(args, "json", False)
    report, failures = batch_report(vault, terms, args.top_n)
    if failures:
        # A failed lookup must never read as "no hits" — for a sanctions or donor list that is a
        # false negative presented as a result.
        print(f"  {_YELLOW}Warning: exact-match search failed for {failures} of {len(terms)} "
              f"term(s) — those terms were NOT checked against the document text "
              f"(try `watchdog reindex`).{_RESET}", file=sys.stderr)

    if as_json:
        print(json.dumps({
            "terms": [
                {"term": r["term"],
                 "entities": r["entities"],
                 "hits": [{"kind": h["kind"], "title": h["title"], "path": h["path"], "page": h["page"]}
                          for h in r["hits"]],
                 **({"error": r["error"]} if r["error"] else {})}
                for r in report
            ]
        }, ensure_ascii=False))
        return

    print()
    for r in report:
        print(f"  {_BOLD}{r['term']}{_RESET}")
        if r["error"] and not r["entities"]:
            print(f"    {_YELLOW}not checked — exact-match search failed: {r['error']}{_RESET}")
            print()
            continue
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


def everywhere_report(all_projects: dict, terms: list[str], limit: int) -> tuple[list[dict], list[tuple[str, str]]]:
    """Every active (non-archived) investigation's manifest and full-text matches for `terms`:
    `(results, skipped)`, where each result is `{slug, name, path, entities, hits, error}` in
    project-name order and `skipped` lists `(slug, problem)` for investigations whose folder is
    missing or isn't a vault. Shared with the desktop app."""
    from watchdog.pipeline import fulltext

    active = {k: v for k, v in all_projects.items() if not v.get("archived")}
    results = []
    skipped = []
    for slug, info in sorted(active.items(), key=lambda x: x[1]["name"]):
        problem = _check_project_health(info)
        if problem:
            skipped.append((slug, problem))
            continue
        vault = Path(info["path"])

        manifest_path = vault / ".watchdog" / "registry" / "manifest.json"
        manifest = {}
        if manifest_path.exists():
            manifest = _read_json_or(manifest_path, {}, catch=(json.JSONDecodeError,))

        entities_by_id = {}
        hits = []
        error = None
        for term in terms:
            for e in _manifest_matches(manifest, term):
                entities_by_id[e["id"]] = e
            try:
                hits.extend(fulltext.search(vault, term, limit=limit))
            except Exception as e:
                error = str(e)

        results.append({"slug": slug, "name": info["name"], "path": info["path"],
                        "entities": list(entities_by_id.values()), "hits": hits, "error": error})
    return results, skipped


def cmd_search_everywhere(args) -> None:
    """`watchdog search --everywhere <query>` (and `--everywhere --batch <file>`, #272): the
    cheap first slice of #67 (global entity registry) — "have I seen this name in *any* of
    my vaults?" answered today by iterating every registered, non-archived investigation's
    existing manifest + full-text (FTS5) indexes and grouping hits by investigation. Follows
    D57's batch-mode precedent by skipping the semantic/rerank lane: N vaults x embedding +
    rerank doesn't scale the way in-process SQLite queries do. Vaults with a broken/missing
    path are skipped, same tolerance as `watchdog doctor`.
    """
    batch_file = getattr(args, "batch", None)
    if batch_file:
        if args.project or args.query:
            sys.exit("Error: --everywhere searches every investigation; drop the project name argument.")
        terms = _read_batch_terms(Path(batch_file))
        if not terms:
            sys.exit(f"Error: no terms found in {batch_file}")
    else:
        if args.project and args.query:
            sys.exit("Error: --everywhere searches every investigation — quote a multi-word "
                      "query instead of passing a project name.")
        query = args.query or args.project
        if not query:
            sys.exit("Error: please provide a search query.")
        terms = [query]

    all_projects = load_projects()
    if not all_projects:
        print("\n  No registered investigations.\n")
        return

    as_json = getattr(args, "json", False)
    results, skipped = everywhere_report(all_projects, terms, args.top_n)
    n_skipped = len(skipped)

    if as_json:
        print(json.dumps({
            "terms": terms,
            "investigations": [
                {"slug": r["slug"], "name": r["name"], "entities": r["entities"],
                 "hits": [{"kind": h["kind"], "title": h["title"], "path": h["path"], "page": h["page"]}
                          for h in r["hits"]],
                 **({"error": r["error"]} if r["error"] else {})}
                for r in results
            ],
        }, ensure_ascii=False))
        return

    print()
    hit_results = [r for r in results if r["entities"] or r["hits"]]
    if not hit_results:
        noun = "investigation" if len(results) == 1 else "investigations"
        print(f"  {_DIM}No matches across {len(results)} {noun}.{_RESET}\n")
    else:
        for r in hit_results:
            n_ent, n_hit = len(r["entities"]), len(r["hits"])
            parts = []
            if n_ent:
                parts.append(f"{n_ent} {'entity' if n_ent == 1 else 'entities'}")
            if n_hit:
                hit_part = f"{n_hit} exact match{'es' if n_hit != 1 else ''}"
                if n_hit == 1:
                    h = r["hits"][0]
                    if h["kind"] == "corpus":
                        loc = f"p. {h.get('page')}, {h.get('title') or h.get('path')}"
                    else:
                        loc = f"{_EXACT_KIND_LABELS.get(h['kind'], h['kind'])}: {h.get('title') or h.get('path')}"
                    hit_part += f" ({loc})"
                parts.append(hit_part)
            print(f"  {_BOLD}{r['name']}{_RESET}  {_DIM}{r['slug']}{_RESET}")
            print(f"    {_DIM}{' · '.join(parts)}{_RESET}")
        print()

    unchecked = [r for r in results if r["error"]]
    if unchecked:
        names = ", ".join(r["name"] for r in unchecked)
        print(f"  {_YELLOW}Exact-match search failed in {len(unchecked)} investigation"
              f"{'s' if len(unchecked) != 1 else ''} ({names}) — the document text there was NOT "
              f"checked; run `watchdog reindex` in each.{_RESET}\n")
    if n_skipped:
        noun = "investigation" if n_skipped == 1 else "investigations"
        print(f"  {_DIM}Skipped {n_skipped} {noun} with a broken vault path.{_RESET}\n")


def _confine_to_session_vault(args) -> None:
    """Inside a Claude Code session, keep `watchdog search` to the vault the session runs in (D257).

    The vault's settings pre-approve `watchdog search *` so /watchdog-query can run it without a
    prompt, and the documents a session reads are adversarial by assumption (I6). Without this,
    a prompt-injected document could have the session run `search --batch ~/.watchdog/
    credentials.json` (every line is echoed back as a term), search another investigation, or
    search all of them with `--everywhere`, all with no prompt. Claude Code marks its shell with
    `CLAUDECODE=1`; dropping the marker means running a different command line, which the allow
    rule no longer matches, so it prompts. A person at their own terminal is unaffected.

    Other investigations are never looked up here, and every refusal reads the same whatever
    was named, so the error can't be used to learn which investigations exist."""
    if not os.environ.get("CLAUDECODE"):
        return
    refuse = ("Error: from inside a Claude Code session, watchdog search only works from this "
              "investigation's own folder, on this investigation{}. Run it in your own terminal "
              "instead.")
    here = Path(".").resolve()
    own = {slug for slug, v in load_projects().items() if Path(v["path"]).resolve() == here}
    if not own:
        sys.exit(refuse.format(""))
    if getattr(args, "everywhere", False):
        sys.exit(refuse.format(" (not --everywhere)"))
    batch = getattr(args, "batch", None)
    if batch and here not in Path(batch).expanduser().resolve().parents:
        sys.exit(refuse.format(", with a --batch file inside it"))
    if args.project and (args.query or batch) and args.project not in own \
            and slugify(args.project) not in own:
        sys.exit(refuse.format(""))


def cmd_search(args) -> None:
    _confine_to_session_vault(args)
    if getattr(args, "everywhere", False):
        cmd_search_everywhere(args)
        return

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
        # One positional: inside a vault it is the query — `watchdog search shell` once failed
        # with "please provide a search query" because "shell" prefix-matched another project's
        # slug. Outside a vault it can only be a project name, which still needs a query.
        projects = load_projects()
        cwd = Path(".").resolve()
        match = next(((s, v) for s, v in projects.items() if Path(v["path"]).resolve() == cwd), None)
        if match is None:
            slug_try = slugify(project_arg)
            if slug_try in projects or any(k.startswith(slug_try) for k in projects):
                sys.exit("Error: please provide a search query.")
            sys.exit(f"Project not found: {project_arg}\nRun 'watchdog projects list' to see all projects.")
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
        print(f"  {_YELLOW}Warning: exact-match search unavailable: {e}{_RESET}", file=sys.stderr)

    if as_json:
        print(json.dumps(_build_search_json(args.query, passages, notes, exact, vault=vault),
                         ensure_ascii=False))
        return

    print()
    if not passages and not notes and not exact:
        hint = f" above {args.threshold:.2f}" if args.threshold is not None else ""
        print(f"  {_DIM}No results{hint}.{_RESET}\n")
        return

    full  = getattr(args, "full", False)
    terms = _search_query_terms(args.query)
    from watchdog.links import note_link
    docs_reg = _read_json_or(vault / ".watchdog" / "registry" / "documents.json", {})
    note_by_sha = {sha: d.get("document_note") for sha, d in docs_reg.items()}
    # Passages carry only a filename; link one only when no other document shares the name.
    by_name: dict[str, list[str | None]] = {}
    for d in docs_reg.values():
        by_name.setdefault(d.get("filename"), []).append(d.get("document_note"))
    note_by_name = {n: notes[0] for n, notes in by_name.items() if len(notes) == 1}

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
                title = note_link(vault, note_by_sha.get(r.get("key")), r.get("title") or "?")
                print(f"  {_BOLD}{title}{_RESET}  {_DIM}{where}{_RESET}")
            else:
                label = _EXACT_KIND_LABELS.get(r["kind"], r["kind"])
                print(f"  {_BOLD}{note_link(vault, r.get('path'))}{_RESET}  {_DIM}{label}{_RESET}")
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
            title = note_link(vault, note_by_name.get(r.get("filename")), r.get("filename", "?"))
            print(f"  {_BOLD}{title}{_RESET}  {_DIM}p.{r.get('page')}  score {score}{_RESET}")
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
            print(f"  {_BOLD}{note_link(vault, r['note_path'])}{_RESET}  {_DIM}score {score}{_RESET}")
            print(f"  {_DIM}{preview}{_RESET}")
            print()
