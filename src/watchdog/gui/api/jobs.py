"""jobs.* and action.run: the app's route to every operation that changes anything (D298).

Each runs one registered operation (`watchdog.ops`) in a worker process: `jobs.start` for the long
ones, followed in the job dock, and `action.run` for the quick ones, run to completion.
"""

from __future__ import annotations

from watchdog import ops
from watchdog.gui import jobs
from watchdog.gui.rpc import SHUTDOWN_HOOKS, RpcError, method
from watchdog.gui.vaultio import require_vault

DEFAULT_ACTION_TIMEOUT = 60


def _op(op) -> str:
    if not isinstance(op, str) or not op:
        raise RpcError("op must name an operation.", code="bad_params")
    return op


def _vault(vault):
    return require_vault(vault) if vault is not None else None


@method("jobs.ops")
def list_ops() -> dict:
    """Every operation the app can run, with its parameters (name → type, required, default)."""
    return ops.schema()


@method("jobs.start")
def start(vault=None, op=None, params=None, label="", kind=None) -> dict:
    job = jobs.MANAGER.start(_vault(vault), _op(op), params or {}, label or op, kind)
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


@method("jobs.undoMerge")
def undo_merge(vault: str, id: str) -> dict:
    """Start the job that undoes one entity merge (D280), refused up front, with the reason, when
    the merge cannot be split correctly."""
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
    name = (entry.get("merged") or {}).get("name") or id
    job = jobs.MANAGER.start(v, "undo-merge", {"id": id}, f"Undo merge: {name}", "undo-merge")
    return job.to_dict()


@method("action.run")
def run_action(vault=None, op=None, params=None, timeout=None) -> dict:
    return jobs.run_action(_vault(vault), _op(op), params or {}, float(timeout or DEFAULT_ACTION_TIMEOUT))


SHUTDOWN_HOOKS.append(jobs.MANAGER.shutdown)
