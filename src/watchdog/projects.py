"""The list of investigations this computer knows (`~/.watchdog/projects.json`) and the checks the
app makes on each one.

Each entry is keyed by the investigation's slug: `{"name", "path", "created_at", "description"?,
"archived"?}`. The operations that change the list live in `watchdog.ops.projects`.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from watchdog import config
from watchdog.vault_paths import is_vault, preprocessing_lock, processing_lock

# The schema a new investigation's registry is stamped with.
VAULT_SCHEMA_VERSION = "1"


def path() -> Path:
    return config.home() / "projects.json"


def load() -> dict:
    p = path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        sys.exit(f"Error: the list of investigations is damaged ({e}). Fix or remove {p}.")


def save(projects: dict) -> None:
    config.home().mkdir(parents=True, exist_ok=True)
    path().write_text(json.dumps(projects, indent=2) + "\n", encoding="utf-8")


def projects_dir() -> Path:
    """Where new investigations are created: the `projects_dir` setting, else ~/Investigations."""
    return Path(config.load().get("projects_dir") or Path.home() / "Investigations").expanduser()


def find(slug: str) -> tuple[str, dict]:
    """`(slug, entry)` for the investigation listed under exactly `slug`. Never a prefix or a
    name: the operations that call this rename, move and delete folders, and a near miss must
    not land on another investigation (a stale "port" deleting "port-calder")."""
    projects = load()
    if not isinstance(slug, str) or slug not in projects:
        sys.exit(f"Error: no investigation is listed as “{slug}”. Refresh the list and try again.")
    return slug, projects[slug]


def name_for(vault: Path) -> str:
    """The investigation's name as listed, else its folder's name."""
    resolved = Path(vault).resolve()
    for info in load().values():
        if info.get("path") and Path(info["path"]).expanduser().resolve() == resolved:
            return info.get("name") or vault.name
    return Path(vault).name


def check_health(info: dict) -> str | None:
    """Why a listed investigation can't be opened, or None."""
    vault = Path(info["path"])
    if not vault.exists():
        return "folder not found"
    if not is_vault(vault):
        return "not a watchdog vault"
    return None


def load_registry(vault: Path) -> dict | None:
    """The investigation's `registry.json`, or None before anything was added. A damaged file
    raises `json.JSONDecodeError` for the caller to report."""
    reg = vault / ".watchdog" / "registry" / "registry.json"
    if not reg.exists():
        return None
    return json.loads(reg.read_text())


def vault_size(vault: Path) -> int:
    """Total bytes under `vault`. `os.scandir` reuses the listing's own metadata where the
    platform provides it, instead of a separate stat per file."""
    total = 0
    stack = [str(vault)]
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return total


def check_locks(vault: Path) -> None:
    """Refuse to rename, move or delete an investigation while a run holds it. A lock whose run
    died, or one past the age window when its run can't be checked, holds nothing (D293)."""
    from watchdog.pipeline import locks
    for lock, stage in ((preprocessing_lock(vault), "pre-processing"),
                        (processing_lock(vault), "adding documents")):
        holder = locks.held(lock)
        if holder is None:
            continue
        elsewhere = " on another computer" if holder["where"] == "elsewhere" else ""
        sys.exit(f"Error: {stage} is in progress{elsewhere}. Try again when it has finished.")


def doctor_findings(all_projects: dict) -> tuple[list, list]:
    """The problems the app's health check reports, as `(missing, corrupt)`: `(slug, info,
    problem)` for an investigation whose folder is gone or isn't one, and `(slug, info)` for one
    whose registry summary is damaged. In investigation-name order."""
    missing, corrupt = [], []
    for slug, info in sorted(all_projects.items(), key=lambda x: x[1]["name"]):
        problem = check_health(info)
        if problem:
            missing.append((slug, info, problem))
            continue
        try:
            load_registry(Path(info["path"]))
        except json.JSONDecodeError:
            corrupt.append((slug, info))
    return missing, corrupt
