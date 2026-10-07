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


@method("action.run")
def run_action(vault=None, args=None, timeout=None) -> dict:
    return jobs.run_action(_vault(vault), _args(args or []), float(timeout or DEFAULT_ACTION_TIMEOUT))


SHUTDOWN_HOOKS.append(jobs.MANAGER.shutdown)
