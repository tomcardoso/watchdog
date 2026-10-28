"""Investigation management operations (D298): create, register, rename, describe, move,
archive and remove an investigation.

They change the list of investigations (`watchdog.projects`) and the investigation's folder; the
app runs each in the worker like any other operation.
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from watchdog import obsidian, projects
from watchdog.ops import op
from watchdog.pipeline.write_vault import slugify
from watchdog.vault import (
    DASHBOARD_BASE, WATCHLIST_TEMPLATE, claude_settings, install_commands,
    pkg_version, render_template,
)
from watchdog.vault_paths import CONTEXT_NAME, INCOMING_NAME, is_vault, processing_log


def _entry(slug: str) -> dict:
    info = projects.load().get(slug) or {}
    return {"slug": slug, "name": info.get("name"), "path": info.get("path")}


@op("projects-new", vault=False, kind="action")
def new(rep, *, name: str, description: str = "", dir: str | None = None) -> dict:
    """Create an investigation folder and list it."""
    vault = create(name, description=description, parent=Path(dir).expanduser().resolve() if dir else None)
    rep.log(f"Created {vault}")
    return _entry(slugify(name.strip()))


def create(name: str, *, description: str = "", parent: Path | None = None) -> Path:
    """Create a new investigation's folder under `parent` (default: the `projects_dir` setting)
    and add it to the list. Returns the folder."""
    name, description = name.strip(), (description or "").strip()
    slug = slugify(name)
    if not slug:
        sys.exit("Error: the name is not usable; use letters or numbers.")
    # Most filesystems cap a single path component at 255 bytes. Check bytes, not characters:
    # slugify keeps non-ASCII word characters, which can be several bytes each in UTF-8.
    if len(slug.encode("utf-8")) > 255:
        sys.exit("Error: the name is too long; shorten it.")
    vault = (parent or projects.projects_dir()) / slug
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

    registry = vault / ".watchdog" / "registry"
    for f in ("documents.json", "entities.json", "manifest.json"):
        (registry / f).write_text("{}\n")
    (registry / "registry.json").write_text(json.dumps(
        {"schema_version": projects.VAULT_SCHEMA_VERSION, "created_at": now, "last_updated": now,
         "document_count": 0, "entity_count": 0},
        indent=2) + "\n")
    processing_log(vault).write_text("")
    (vault / ".obsidian" / "app.json").write_text(json.dumps(
        {"userIgnoreFilters": [".watchdog"], "showInlineTitle": False}, indent=2) + "\n")
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

    (vault / "index.md").write_text(render_template("index.md", name=name, today=today))
    (vault / "dashboard.base").write_text(DASHBOARD_BASE)
    # Claude's instructions live in .claude/ to keep the vault root clean (Claude Code loads
    # ./.claude/CLAUDE.md the same as ./CLAUDE.md). README.md is for anyone who opens the folder.
    (vault / ".claude" / "CLAUDE.md").write_text(render_template("CLAUDE.md", name=name))
    (vault / "README.md").write_text(render_template("README.md", name=name, version=pkg_version()))
    (vault / "watchlist.md").write_text(WATCHLIST_TEMPLATE)
    install_commands(vault / ".claude" / "commands")
    (vault / ".claude" / "settings.json").write_text(json.dumps(claude_settings(), indent=2) + "\n")
    obsidian.register(vault)

    # Everything this investigation will hold is processed by this version of the pipeline, so it
    # starts with every migration applied and never sees "Bring this investigation up to date".
    from watchdog.pipeline import migrations
    migrations.stamp_current(vault)

    listed = projects.load()
    entry = {"name": name, "path": str(vault), "created_at": now}
    if description:
        entry["description"] = description
    listed[slug] = entry
    projects.save(listed)
    return vault


@op("projects-register", vault=False, kind="action")
def register(rep, *, path: str, name: str) -> dict:
    """List an existing investigation folder."""
    vault = Path(path).expanduser().resolve()
    if not vault.exists():
        sys.exit(f"Error: {vault} was not found.")
    if not is_vault(vault):
        sys.exit(f"Error: {vault} is not a Watchdog investigation (it has no .watchdog folder).")
    try:
        reg = projects.load_registry(vault)
    except json.JSONDecodeError as e:
        sys.exit(f"Error: the investigation's registry summary is damaged ({e}).")
    name = name.strip()
    slug = slugify(name)
    if not slug:
        sys.exit("Error: the name is not usable; use letters or numbers.")
    listed = projects.load()
    if slug in listed:
        sys.exit(f"Error: an investigation named “{name}” is already listed. Choose a different name.")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    listed[slug] = {"name": name, "path": str(vault), "created_at": (reg or {}).get("created_at", now)}
    projects.save(listed)
    obsidian.register(vault)
    rep.log(f"Added {name}: {vault}")
    return _entry(slug)


@op("projects-rename", vault=False, kind="action")
def rename(rep, *, slug: str, name: str) -> dict:
    """Rename an investigation: its folder and its entry in the list."""
    slug, info = projects.find(slug)
    new_name = name.strip()
    new_slug = slugify(new_name)
    if not new_slug:
        sys.exit("Error: the new name is not usable; use letters or numbers.")
    vault = Path(info["path"])
    projects.check_locks(vault)
    listed = projects.load()
    if new_slug in listed and new_slug != slug:
        sys.exit(f"Error: an investigation named “{new_name}” already exists.")
    new_vault = vault.parent / new_slug
    if slug != new_slug:
        if new_vault.exists():
            sys.exit(f"Error: {new_vault} already exists.")
        old_resolved = vault.resolve()
        vault.rename(new_vault)
        _relocate_telemetry(rep, old_resolved, new_vault)
        obsidian.relocate(info["path"], new_vault)
    listed[new_slug] = {**info, "name": new_name, "path": str(new_vault)}
    if new_slug != slug:
        del listed[slug]
    projects.save(listed)
    rep.log(f"Renamed {info['name']} to {new_name}: {new_vault}")
    return _entry(new_slug)


@op("projects-describe", vault=False, kind="action")
def describe(rep, *, slug: str, description: str = "") -> dict:
    """Set or clear an investigation's one-line description."""
    slug, info = projects.find(slug)
    listed = projects.load()
    text = description.strip()
    if text:
        listed[slug]["description"] = text
    else:
        listed[slug].pop("description", None)
    projects.save(listed)
    rep.log(f"{'Updated' if text else 'Cleared'} the description of {info['name']}.")
    return _entry(slug)


@op("projects-move", vault=False, kind="action")
def move(rep, *, slug: str, path: str) -> dict:
    """Move an investigation's folder, or point the list at where it was moved to."""
    slug, info = projects.find(slug)
    src = Path(info["path"])
    projects.check_locks(src)
    dst = Path(path).expanduser().resolve()
    if src == dst:
        sys.exit("Error: the investigation is already in that folder.")
    if dst.is_dir() and not (dst / ".watchdog").exists():
        dst = dst / src.name
    moved = False
    src_resolved = src.resolve()      # before the move, so a symlinked path still resolves
    if src.exists():
        try:
            shutil.move(str(src), str(dst))
        except shutil.Error as e:
            # The copy fallback (a move across drives) refuses a file it can't copy: a FIFO,
            # socket or device node. `e.args[0]` lists (src, dst, error) for each.
            blocked = "\n".join(f"  {s}: {err}" for s, _d, err in e.args[0]) if e.args else str(e)
            sys.exit(f"Error: {src} could not be moved; some files could not be copied:\n{blocked}")
        moved = True
    elif not dst.exists():
        sys.exit(f"Error: neither {src} nor {dst} exists. Move the folder back, or choose its new "
                 "location.")
    listed = projects.load()
    listed[slug]["path"] = str(dst)
    projects.save(listed)
    _relocate_telemetry(rep, src_resolved, dst)
    obsidian.relocate(src, dst)
    rep.log(f"{'Moved' if moved else 'Updated'} {info['name']}: {dst}")
    return _entry(slug)


@op("projects-delete", vault=False, kind="action")
def delete(rep, *, slug: str, purge: bool = False) -> dict:
    """Remove an investigation from the list; with `purge`, delete its folder too. The app asks
    for the confirmation (the name typed out) before it starts this."""
    slug, info = projects.find(slug)
    vault = Path(info["path"])
    projects.check_locks(vault)
    listed = projects.load()
    del listed[slug]
    projects.save(listed)
    if purge:
        # Before the folder is gone, so symlinks still resolve. A folder already deleted by hand
        # (or on an unmounted drive) still has its telemetry rows purged (D261).
        resolved = vault.resolve()
        # Another listed investigation at the same path owns the folder and its rows now.
        shared = any(Path(i.get("path", "")).expanduser().resolve() == resolved
                     for i in listed.values() if i.get("path"))
        if shared:
            rep.log(f"Kept the files: another investigation in the list uses {vault}.")
        elif vault.exists():
            if not is_vault(vault):
                sys.exit(f"Error: {vault} is not a Watchdog investigation; nothing was deleted.")
            shutil.rmtree(vault)
        try:
            from watchdog import telemetry_db
            if not shared:
                telemetry_db.purge_vault(resolved)
        except Exception as e:  # noqa: BLE001
            rep.warn(f"Warning: this investigation's rows could not be removed from the usage "
                     f"records: {e}")
    obsidian.unregister(vault)
    rep.log(f"{'Deleted' if purge else 'Removed'} {info['name']}.")
    return {"slug": slug, "removed": True}


@op("projects-archive", vault=False, kind="action")
def archive(rep, *, slug: str, archived: bool = True) -> dict:
    """Hide an investigation from the list (`archived`), or bring it back."""
    found, info = projects.find(slug)
    listed = projects.load()
    if archived:
        listed[found]["archived"] = True
    else:
        listed[found].pop("archived", None)
    projects.save(listed)
    rep.log(f"{'Archived' if archived else 'Restored'} {info['name']}.")
    return {"slug": found, "archived": archived}


def _relocate_telemetry(rep, old_resolved: Path, new: Path) -> None:
    try:
        from watchdog import telemetry_db
        telemetry_db.relocate_vault(old_resolved, new)
    except Exception as e:  # noqa: BLE001
        rep.warn(f"Warning: this investigation's usage records could not be updated: {e}")
