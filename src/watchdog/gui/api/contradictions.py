"""`contradictions.estimate` and `jobs.recheckContradictions` — the app's "Re-check contradictions"
on an entity's page and in Maintenance (D287).

App-only (I10, D286): no terminal command. The estimate is a read: the calls the re-check would
send (`recheck.plan`), priced on the configured finalizer model (`ingest_setup.recheck_cost_estimate`),
with whether auth is in place and whether a run holds the vault. Starting it runs
`python -m watchdog.pipeline.recheck` as a job, after the same refusals the job would make.
"""

from __future__ import annotations

from watchdog.gui.rpc import RpcError, method
from watchdog.gui.vaultio import require_vault

KIND = "recheck-contradictions"


def _ids(ids, all_) -> list[str] | None:
    if all_:
        return None
    if isinstance(ids, str):
        ids = [ids]
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and i.strip() for i in ids):
        raise RpcError("Name the entities to re-check, or ask for the whole investigation.",
                       code="bad_params")
    return [i.strip() for i in ids]


def _auth(backend: str | None) -> dict:
    """Claude auth matters only when the model is a Claude one (as `watchdog bark` checks)."""
    from watchdog.cmd.auth import resolve_auth
    from watchdog.model_client import CLAUDE_BACKENDS
    if backend is not None and backend not in CLAUDE_BACKENDS:
        return {"mode": None, "ok": True, "reason": None}
    a = resolve_auth()
    mode = a.get("mode")
    return {"mode": mode, "ok": mode in ("subscription", "api-key"),
            "reason": (a.get("reason") or "No way to reach Claude is set up yet.") if mode == "none" else None}


def _model(backend: str | None, model: str, effort: str | None, auth_mode: str | None) -> dict:
    from watchdog import model_catalog, model_client
    effective = backend or {"subscription": "claude-agent-sdk", "api-key": "claude-api"}.get(auth_mode or "")
    return {"model": model, "backend": effective, "effort": effort,
            "label": f"{backend}:{model}" if backend else model,
            "name": model_catalog.display_name(model_client.resolve_model_id(model))}


@method("contradictions.estimate")
def estimate(vault: str, ids: list[str] | None = None, all: bool = False) -> dict:
    from watchdog.cmd.auth import billing_summary, run_providers
    from watchdog.gui.engine_setup import engine_ready
    from watchdog.pipeline import history, ingest_setup, recheck

    v = require_vault(vault)
    wanted = _ids(ids, all)
    backend, model, effort = recheck.finalizer_stage()
    auth = _auth(backend)
    info = _model(backend, model, effort, auth["mode"])
    p = recheck.plan(v, wanted, model, backend)
    cost = ingest_setup.recheck_cost_estimate(v, p["est_tokens"], model, info["backend"])
    return {"scope": "all" if wanted is None else "entities",
            "entities": p["entities"], "skipped": p["skipped"], "calls": len(p["calls"]),
            "facts": p["facts"], **cost, "model": info, "auth": auth,
            "billing": billing_summary(v, run_providers([backend])),
            "busy": history.run_in_progress(v), "engine_ready": engine_ready(),
            "max_entity_calls": recheck.MAX_ENTITY_CALLS}


@method("jobs.recheckContradictions")
def start(vault: str, ids: list[str] | None = None, all: bool = False) -> dict:
    import sys
    from watchdog.gui import jobs
    from watchdog.pipeline import history, recheck

    v = require_vault(vault)
    wanted = _ids(ids, all)
    jobs.require_engine([KIND])
    if history.run_in_progress(v):
        raise RpcError(recheck.BUSY, code="busy")
    backend, model, _effort = recheck.finalizer_stage()
    auth = _auth(backend)
    if not auth["ok"]:
        raise RpcError(auth["reason"] or "No way to reach Claude is set up yet.", code="auth_required")
    from watchdog.cmd.auth import KeyChoiceError, check_run_keys
    try:
        check_run_keys(v, [backend])
    except KeyChoiceError as e:      # the investigation's chosen key isn't here (D290)
        raise RpcError(str(e), code="key_missing") from None
    argv = [sys.executable, "-m", "watchdog.pipeline.recheck"]
    if wanted is None:
        argv.append("--all")
        label = "Re-check contradictions: whole investigation"
    else:
        from watchdog.pipeline.json_io import _read_json_or
        reg = _read_json_or(v / ".watchdog" / "registry" / "entities.json", {})
        for eid in wanted:
            argv += ["--entity", eid]
        names = [(reg.get(i) or {}).get("name") or i for i in wanted]
        label = "Re-check contradictions: " + ", ".join(names[:2]) + (
            f" and {len(names) - 2} more" if len(names) > 2 else "")
    job = jobs.MANAGER.start(v, [KIND, *(wanted or ["--all"])], label, KIND, argv=argv)
    return job.to_dict()
