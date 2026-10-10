"""Investigation management operations (D298): create, register, rename, describe, move and
remove an investigation, and watch its `incoming/` folder.

They change the projects list (`~/.watchdog/projects.json`) and the investigation's folder; the
app runs each in the worker like any other operation. The terminal's `watchdog new`, `projects
rename` and the rest call the same functions until the command line is removed.
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from watchdog.cmd import vault as _v
from watchdog.cmd.base import (
    _BOLD, _CYAN, _DIM, _GREEN, _RESET, _YELLOW,
    _check_vault_locks, _count_queued, _ensure_layout, _find_project, _load_registry, _notify,
    _projects_dir, _registered_project, _render_template, _vault_settings, load_projects,
    save_projects, slugify,
)
from watchdog.ops import current, hint, op, say
from watchdog.vault_paths import CONTEXT_NAME, INCOMING_NAME, incoming_dir, is_vault, processing_log


def _entry(slug: str) -> dict:
    info = load_projects().get(slug) or {}
    return {"slug": slug, "name": info.get("name"), "path": info.get("path")}


@op("projects-new", vault=False, kind="action")
def new(rep, *, name: str, description: str = "", dir: str | None = None) -> dict:
    """Create an investigation folder and list it."""
    _new(SimpleNamespace(name=name.strip(), name_flag=None, description=description.strip(), dir=dir))
    return _entry(slugify(name.strip()))


@op("projects-register", vault=False, kind="action")
def register(rep, *, path: str, name: str) -> dict:
    """List an existing investigation folder."""
    _register(SimpleNamespace(path=path, name=name))
    return _entry(slugify(name.strip()))


@op("projects-rename", vault=False, kind="action")
def rename(rep, *, slug: str, name: str) -> dict:
    """Rename an investigation: its folder and its entry in the list."""
    _rename(SimpleNamespace(project=slug, name=name))
    return _entry(slugify(name.strip()))


@op("projects-describe", vault=False, kind="action")
def describe(rep, *, slug: str, description: str = "") -> dict:
    """Set or clear an investigation's one-line description."""
    _describe(SimpleNamespace(project=slug, text=description))
    return _entry(slug)


@op("projects-move", vault=False, kind="action")
def move(rep, *, slug: str, path: str) -> dict:
    """Move an investigation's folder, or point the list at where it was moved to."""
    _move(SimpleNamespace(name=slug, path=path))
    return _entry(slug)


@op("projects-delete", vault=False, kind="action")
def delete(rep, *, slug: str, purge: bool = False) -> dict:
    """Remove an investigation from the list; with `purge`, delete its folder too. The app asks
    for the confirmation (the name typed out) before it starts this."""
    _delete(SimpleNamespace(name=slug, yes=True, purge=purge))
    return {"slug": slug, "removed": slug not in load_projects()}


@op("watch", engine="add")
def watch(rep, vault: Path) -> None:
    """Pre-process files as they arrive in `incoming/`, until stopped."""
    _watch(SimpleNamespace(name=None))


def _register(args) -> None:
    vault = Path(args.path).expanduser().resolve() if args.path else Path.cwd()

    if not vault.exists():
        sys.exit(f"Error: path not found: {vault}")
    if not is_vault(vault):
        sys.exit(f"Error: {vault} does not look like a watchdog vault — no .watchdog folder found.")

    try:
        reg = _load_registry(vault)
    except json.JSONDecodeError as e:
        sys.exit(f"Error: registry file is corrupt — {e}"
                 + hint("\nRun 'watchdog settings doctor' to diagnose.", ""))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Infer name: use folder name as default, let user override
    default_name = vault.name.replace("-", " ").replace("_", " ").title()
    if reg and reg.get("name"):
        default_name = reg["name"]

    if args.name:
        name = args.name.strip()
    else:
        say(f"\n  {_DIM}Vault:{_RESET} {_CYAN}{vault}{_RESET}")
        say(f"  {_DIM}Suggested name:{_RESET} {default_name}")
        try:
            entered = input("\n  Investigation name (Enter to accept suggestion): ").strip()
            name = entered if entered else default_name
        except (EOFError, KeyboardInterrupt):
            say()
            sys.exit(1)

    slug = slugify(name)
    if not slug:
        sys.exit("Error: name is invalid.")

    projects = load_projects()
    if slug in projects:
        sys.exit(f"Error: a project with slug '{slug}' is already registered. "
                 + hint("Use 'watchdog projects rename' or choose a different name.", "Choose a different name."))

    created_at = reg.get("created_at", now) if reg else now
    projects[slug] = {"name": name, "path": str(vault), "created_at": created_at}
    save_projects(projects)
    _v._register_obsidian_vault(vault)

    say(f"\n  {_GREEN}Registered:{_RESET} {_BOLD}{name}{_RESET}  {_DIM}[{slug}]{_RESET}")
    say(f"  {_CYAN}{vault}{_RESET}\n")


def _new(args) -> None:
    name = args.name or getattr(args, "name_flag", None)
    description = getattr(args, "description", None) or ""

    if not name:
        say()
        try:
            name = input("  Investigation name: ").strip()
            if not description:
                description = input("  Brief description (optional): ").strip()
        except (EOFError, KeyboardInterrupt):
            say()
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
             "entity_note_format": _v._note_format()},
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
    (vault / "dashboard.base").write_text(_v._DASHBOARD_BASE)
    # CLAUDE.md (Claude's internal instructions) lives in .claude/ to keep the vault root
    # clean — Claude Code loads ./.claude/CLAUDE.md the same as ./CLAUDE.md. README.md is the
    # human-facing entry point for anyone who opens the folder.
    (vault / ".claude" / "CLAUDE.md").write_text(_render_template("CLAUDE.md", name=name))
    (vault / "README.md").write_text(_render_template("README.md", name=name, version=_v._pkg_version()))
    (vault / "watchlist.md").write_text(_v._WATCHLIST_TEMPLATE)

    from watchdog.setup_cmd import install_skills
    install_skills(vault / ".claude" / "commands")
    _v._register_obsidian_vault(vault)

    (vault / ".claude" / "settings.json").write_text(json.dumps(_vault_settings(), indent=2) + "\n")

    projects = load_projects()
    entry = {"name": name, "path": str(vault), "created_at": now}
    if description:
        entry["description"] = description
    projects[slug] = entry
    save_projects(projects)

    say(f"\n  {_GREEN}Created:{_RESET} {_BOLD}{vault}{_RESET}")
    say()
    say(r"                  _,)")
    say(r"          _..._.-;-'  ")
    say(r"       .-'     `(     ")
    say(r"      /      ;   \    ")
    say(r"     ;.' ;`  ,;  ;   ")
    say(r"    .'' ``. (  \ ;   ")
    say(r"   / f_ _L \ ;  )\   ")
    say(r"   \/|` '|\/;; <;/   ")
    say(r"  ((; \_/  (()        ")
    say(r'       "              ')
    say()
    if current().app:
        say(f"  {_DIM}Created {vault}{_RESET}\n")
    else:
        _v._print_new_vault_steps(vault, slug)


def _rename(args) -> None:
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
                sys.exit(f"Project not found: {first}" + hint("\nRun 'watchdog projects list' to see all projects.", ""))
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
            say(f"\n  {_DIM}Current:{_RESET} {current}")
        try:
            new_name = input("\n  New name: ").strip()
        except (EOFError, KeyboardInterrupt):
            say()
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
        _v._relocate_telemetry(old_resolved, new_vault)

        # Update Obsidian registry (path changed)
        cfg = _v._obsidian_config_path()
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

    say(f"\n  {_GREEN}Renamed:{_RESET}  {_DIM}{info['name']}{_RESET} → {_BOLD}{new_name}{_RESET}  {_DIM}[{new_slug}]{_RESET}")
    say(f"  {_CYAN}{new_vault}{_RESET}\n")


def _describe(args) -> None:
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
                sys.exit(f"Project not found: {first}" + hint("\nRun 'watchdog projects list' to see all projects.", ""))
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
            say(f"\n  {_DIM}Current:{_RESET} {current}")
        try:
            new_desc = input("\n  New description: ").strip()
        except (EOFError, KeyboardInterrupt):
            say()
            sys.exit(1)
        new_desc = new_desc or ""

    projects = load_projects()
    if new_desc:
        projects[slug]["description"] = new_desc
        save_projects(projects)
        say(f"\n  {_GREEN}Updated:{_RESET}  {_BOLD}{info['name']}{_RESET}")
        say(f"  {_DIM}{new_desc}{_RESET}\n")
    else:
        projects[slug].pop("description", None)
        save_projects(projects)
        say(f"\n  {_GREEN}Cleared:{_RESET}  {_BOLD}{info['name']}{_RESET}\n")


def _delete(args) -> None:
    slug, info = _find_project(args.name)
    vault = Path(info["path"])
    _check_vault_locks(vault, slug)

    say(f"\n  {_BOLD}{info['name']}{_RESET}  {_DIM}{slug}{_RESET}")
    say(f"  {_CYAN}{vault}{_RESET}")
    say()

    if getattr(args, "yes", False):
        answer = True
    elif args.purge:
        say(f"  {_YELLOW}Warning: --purge will permanently delete all vault files from disk.{_RESET}")
        say(f"  {_YELLOW}This cannot be undone.{_RESET}")
        say()
        answer = current().confirm("  Delete all files and remove from registry?", default=False)
    else:
        answer = current().confirm("  Remove from registry?", default=False)

    if not answer:
        say(f"\n  {_DIM}Cancelled.{_RESET}\n")
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
            say(f"  {_YELLOW}Kept the files:{_RESET} another investigation in the list uses "
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
            say(f"  {_YELLOW}Warning:{_RESET} could not remove this vault's rows from the "
                  f"telemetry store: {e}")

    # Remove from Obsidian registry
    cfg = _v._obsidian_config_path()
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
    say(f"\n  {_GREEN}{label}:{_RESET} {_BOLD}{info['name']}{_RESET}")
    say()


def _move(args) -> None:
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
            + hint(f"Move the vault manually first, then re-run: watchdog projects move {slug} <new-path>",
                    "Move the folder back, or choose its new location.")
        )

    projects = load_projects()
    projects[slug]["path"] = str(dst)
    save_projects(projects)
    _v._relocate_telemetry(src_resolved, dst)

    # Update Obsidian registry
    cfg = _v._obsidian_config_path()
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
    say(f"\n  {_GREEN}{verb}:{_RESET} {_BOLD}{info['name']}{_RESET}")
    say(f"  {_CYAN}{dst}{_RESET}\n")


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


def _watch(args) -> None:
    info = _registered_project(args.name, "add --watch")
    vault = Path(info["path"])
    if not vault.exists():
        sys.exit(f"Error: project directory not found: {vault}")
    _ensure_layout(vault)

    from watchdog.pipeline.preprocess_batch import run_ingest, find_files
    import time as _time

    incoming = incoming_dir(vault)
    say(f"\n  {_BOLD}{info['name']}{_RESET}  watching {_CYAN}incoming/{_RESET}"
          + hint(" — press Ctrl+C to stop.", " — use Stop in Activity to stop.") + "\n")

    # Files already waiting are chewed on the first stable poll, like any new arrival — they used
    # to be ignored until some other file happened to arrive.
    known: set = set()
    waiting = len(find_files([incoming]))
    if waiting:
        say(f"  {_DIM}{waiting} file{'s' if waiting != 1 else ''} already in incoming/ — chewing "
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
                say(f"  {_BOLD}{label}{_RESET} detected — chewing...\n")
                queued_before = _count_queued(vault)
                run_ingest(vault, files=ready)
                new_queued = _count_queued(vault) - queued_before
                if new_queued > 0:
                    _notify(
                        f"Watchdog — {info['name']}",
                        f"Chewed {label}. {new_queued} file{'s' if new_queued != 1 else ''} ready" + hint(" — run watchdog dig.", "."),
                    )
                known = set()
            else:
                known = current - set(pending_sizes)
    except KeyboardInterrupt:
        say(f"\n  {_DIM}Stopped watching.{_RESET}\n")
