"""jobs.* and action.run: the app's route to every `watchdog` command that changes anything."""

from __future__ import annotations

from watchdog.gui import jobs
from watchdog.gui.rpc import SHUTDOWN_HOOKS, RpcError, method
from watchdog.gui.vaultio import require_vault

DEFAULT_ACTION_TIMEOUT = 60


def _args(args) -> list[str]:
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise RpcError("args must be a list of strings.", code="bad_params")
    return args


def _vault(vault):
    return require_vault(vault) if vault is not None else None


@method("jobs.start")
def start(vault=None, args=None, label="", kind=None) -> dict:
    job = jobs.MANAGER.start(_vault(vault), _args(args or []), label or " ".join(args or []), kind)
    return job.to_dict()


@method("jobs.cancel")
def cancel(id: str) -> dict:
    jobs.MANAGER.cancel(id)
    return {"ok": True}


@method("jobs.list")
def list_jobs() -> list:
    ordered = sorted(jobs.MANAGER.jobs.values(), key=lambda j: j.started, reverse=True)
    return [j.to_dict() for j in ordered]


@method("jobs.get")
def get(id: str) -> dict:
    job = jobs.MANAGER.get(id)
    with job.lock:
        log = list(job.log)
    return {**job.to_dict(), "log": log}


@method("jobs.flags")
def flags(command: str, options: dict | None = None) -> dict:
    if command not in jobs.COMMANDS:
        raise RpcError(f"jobs.flags supports {', '.join(jobs.COMMANDS)}.", code="bad_params")
    return {"args": jobs.flags_for(command, options)}


@method("jobs.rebuildNotes")
def rebuild_notes(vault: str) -> dict:
    """Start the Maintenance job that rebuilds every entity and document note from stored data
    (D280): `entity_notes.main`, the library function, run as a job like any other vault write."""
    import sys
    v = require_vault(vault)
    argv = [sys.executable, "-m", "watchdog.pipeline.entity_notes"]
    job = jobs.MANAGER.start(v, ["rebuild-notes"], "Rebuild notes", "rebuild-notes", argv=argv)
    return job.to_dict()


@method("jobs.undoMerge")
def undo_merge(vault: str, id: str) -> dict:
    """Start the job that undoes one entity merge (D280): `merge_undo.main`, the library function,
    run as a job. Refused up front, with the reason, when the merge cannot be split correctly."""
    import sys
    from watchdog.pipeline import merge_log, merge_undo
    v = require_vault(vault)
    if not isinstance(id, str) or not id.startswith("merge:"):
        raise RpcError("Not a merge id.", code="bad_params")
    try:
        entry = merge_undo._find(merge_log.load(v), id)
    except merge_undo.UndoRefused as e:
        raise RpcError(str(e), code="not_found") from e
    reason = merge_undo.check(v, entry)
    if reason:
        raise RpcError(reason, code="cannot_undo")
    argv = [sys.executable, "-m", "watchdog.pipeline.merge_undo", id]
    name = (entry.get("merged") or {}).get("name") or id
    job = jobs.MANAGER.start(v, ["undo-merge", id], f"Undo merge: {name}", "undo-merge", argv=argv)
    return job.to_dict()


@method("action.run")
def run_action(vault=None, args=None, timeout=None) -> dict:
    return jobs.run_action(_vault(vault), _args(args or []), float(timeout or DEFAULT_ACTION_TIMEOUT))


SHUTDOWN_HOOKS.append(jobs.MANAGER.shutdown)
