"""`projects.*` — the registered investigations and their health, read from `projects.json` and
each vault's own registries. Mutations (new, rename, archive, …) run the real CLI through
`action.run`, so nothing here writes."""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

from watchdog.gui import vaultio
from watchdog.gui.rpc import RpcError, method

_HEALTH = {"folder not found": "missing", "not a watchdog vault": "not_a_vault"}
_LOG_DEFAULT_LINES = 500
_LOG_MAX_LINES = 20000


def _find(slug: str) -> tuple[str, dict]:
    """The registry entry for `slug` — exact, else a unique prefix (as the CLI resolves names)."""
    projects = vaultio.registered_projects()
    if isinstance(slug, str) and slug:
        if slug in projects:
            return slug, projects[slug]
        matches = [k for k in projects if k.startswith(slug)]
        if len(matches) == 1:
            return matches[0], projects[matches[0]]
        if len(matches) > 1:
            raise RpcError(f"“{slug}” matches more than one investigation.", code="ambiguous")
    raise RpcError("That investigation isn't registered.", code="not_found")


def project_dict(slug: str, info: dict) -> dict:
    """The app's `Project`: registry fields, a health verdict and cheap counts."""
    from watchdog.cmd.base import (
        _check_project_health, _count_awaiting_bark, _count_awaiting_dig, _count_incoming,
        _load_registry,
    )
    from watchdog.cmd.home import _failed

    vault = Path(info.get("path") or "")
    health = _check_project_health({"path": str(vault)})
    health = _HEALTH.get(health, health) if health else None
    reg = None
    if health is None:
        try:
            reg = _load_registry(vault)
        except (json.JSONDecodeError, OSError):
            health = "registry_corrupt"
    live = health in (None, "registry_corrupt")
    reg = reg if isinstance(reg, dict) else {}
    return {
        "slug": slug,
        "name": info.get("name") or slug,
        "description": info.get("description") or None,
        "path": str(vault),
        "archived": bool(info.get("archived")),
        "created": info.get("created_at") or None,
        "health": health,
        # Whether the user has allowed Watchdog to work in this folder (watchdog/access.py);
        # always true when the app isn't enforcing access.
        "access": _access_granted(vault),
        "stats": {
            "documents": int(reg.get("document_count") or 0),
            "entities": int(reg.get("entity_count") or 0),
            "last_ingest": reg.get("last_updated") or None,
            "incoming": _count_incoming(vault) if live else 0,
            "awaiting": (_count_awaiting_dig(vault) + _count_awaiting_bark(vault)) if live else 0,
            "failed": _failed(vault) if live else 0,
        },
    }


@method("projects.list")
def list_projects(all: bool = False) -> list[dict]:
    projects = vaultio.registered_projects()
    rows = [project_dict(slug, info) for slug, info in projects.items()
            if isinstance(info, dict) and (all or not info.get("archived"))]
    rows.sort(key=lambda p: p["name"].lower())
    return rows


@method("projects.get")
def get(slug: str) -> dict:
    slug, info = _find(slug)
    return project_dict(slug, info)


@method("projects.forPath")
def for_path(path: str) -> dict | None:
    if not isinstance(path, str) or not path.strip():
        return None
    target = os.path.realpath(Path(path).expanduser())
    for slug, info in vaultio.registered_projects().items():
        try:
            if os.path.realpath(info["path"]) == target:
                return project_dict(slug, info)
        except (KeyError, TypeError, OSError):
            continue
    return None


@method("projects.status")
def status(slug: str) -> dict:
    from watchdog.cmd.base import _vault_size
    from watchdog.pipeline import orchestrate

    slug, info = _find(slug)
    project = project_dict(slug, info)
    vault = Path(project["path"])
    by_type: Counter = Counter()
    documents_by_type: Counter = Counter()
    locks = {"chew": False, "ingest": False}
    pending = None
    size = 0
    if project["health"] in (None, "registry_corrupt"):
        for ent in vaultio.load_entities(vault).values():
            if isinstance(ent, dict) and ent.get("type"):
                by_type[vaultio.entity_type(ent["type"])] += 1
        for doc in vaultio.load_documents(vault).values():
            if isinstance(doc, dict) and doc.get("document_type"):
                documents_by_type[doc["document_type"]] += 1
        locks = {"chew": (vault / ".watchdog" / ".chew-lock").exists(),
                 "ingest": (vault / ".watchdog" / "registry" / ".ingest-lock").exists()}
        if orchestrate.has_pending_finalization(vault):
            pending = orchestrate.pending_finalization(vault)
        size = _vault_size(vault)
    return {
        "project": project,
        "by_type": dict(by_type.most_common()),
        "documents_by_type": dict(documents_by_type.most_common()),
        "locks": locks,
        "pending_finalization": pending,
        "size_bytes": size,
    }


@method("projects.log")
def log(slug: str, lines: int | None = None) -> dict:
    slug, info = _find(slug)
    path = Path(info.get("path") or "") / ".watchdog" / "registry" / "ingest.log"
    text = vaultio.read_text(path)
    out = [ln.rstrip("\r") for ln in text.splitlines()]
    n = _LOG_DEFAULT_LINES if lines is None else max(0, min(int(lines), _LOG_MAX_LINES))
    return {"lines": out[-n:] if n else []}


@method("projects.doctor")
def doctor() -> dict:
    """The findings `watchdog projects doctor` prints, as data."""
    from watchdog.cmd.base import VAULT_SCHEMA_VERSION
    from watchdog.cmd.vault import doctor_findings

    struct_issues, schema_issues, corrupt = doctor_findings(vaultio.registered_projects())
    issues = []
    for slug, info, problem in struct_issues:
        issues.append({
            "kind": "missing", "slug": slug, "name": info["name"], "path": info["path"],
            "problem": f"{problem.capitalize()}: {info['path']}",
            "suggestion": (f"watchdog projects move {slug} <new-path> to relink, or "
                           f"watchdog projects delete {slug} to remove it from the registry"),
        })
    for slug, info, found in schema_issues:
        issues.append({
            "kind": "schema", "slug": slug, "name": info["name"], "path": info["path"],
            "problem": f"Schema v{found} — current is v{VAULT_SCHEMA_VERSION}",
            "suggestion": "This vault may need migration before it is fully compatible.",
        })
    for slug, info in corrupt:
        reg_path = Path(info["path"]) / ".watchdog" / "registry" / "registry.json"
        issues.append({
            "kind": "corrupt_registry", "slug": slug, "name": info["name"], "path": info["path"],
            "problem": f"Registry file is corrupt: {reg_path}",
            "suggestion": ("It's a regenerated summary cache — delete it and it will rebuild on "
                           "the next processing or post-processing run."),
        })
    return {"issues": issues}


def _access_granted(vault) -> bool:
    from watchdog import access
    return not access.enforced() or access.is_granted(vault)
