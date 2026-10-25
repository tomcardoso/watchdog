"""`migrations.*` and `jobs.migrate` — "Bring this investigation up to date" (D336): the Overview's
one-time banner, the Maintenance row and the dialog that shows the pending steps and one estimate
before the `migrate` operation starts.

App-only (I10). `status` and `estimate` are reads; dismissing the banner writes the app-owned stamp
(`.watchdog/migrations.json`), as opening Review writes its last-opened time; starting runs the
operation as a job after the refusals it would make itself.
"""

from __future__ import annotations

from watchdog.gui.rpc import RpcError, method
from watchdog.gui.vaultio import require_vault

KIND = "migrate"


@method("migrations.status")
def status(vault: str) -> dict:
    from watchdog.pipeline import history, migrations
    v = require_vault(vault)
    return {**migrations.status(v), "busy": history.run_in_progress(v)}


@method("migrations.dismissBanner")
def dismiss_banner(vault: str) -> dict:
    from watchdog.pipeline import migrations
    v = require_vault(vault)
    migrations.dismiss_banner(v)
    return migrations.status(v)


@method("migrations.estimate")
def estimate(vault: str) -> dict:
    from watchdog.auth import billing_summary, run_providers
    from watchdog.gui.api.contradictions import _auth, _model
    from watchdog.gui.engine_setup import engine_ready
    from watchdog.pipeline import history, ingest_setup, migrations
    v = require_vault(vault)
    p = migrations.plan(v)
    steps, backends = [], []
    low = high = 0.0
    priced = True
    auth = {"mode": None, "ok": True, "reason": None}
    for s in p["steps"]:
        row = {k: s[k] for k in ("id", "title", "description", "calls_model", "items", "calls",
                                 "est_tokens", "detail")}
        row.update({"cost_low": None, "cost_high": None, "model": None})
        if s["calls"] and s["model"]:
            a = _auth(s["backend"])
            if not a["ok"]:
                auth = a
            elif auth["mode"] is None:
                auth = {**auth, "mode": a["mode"]}
            info = _model(s["backend"], s["model"], None, a["mode"])
            cost = ingest_setup.recheck_cost_estimate(v, s["est_tokens"], s["model"], info["backend"])
            row.update({"cost_low": cost["cost_low"], "cost_high": cost["cost_high"],
                        "model": info["name"], "subscription": cost["subscription"]})
            backends.append(s["backend"])
            if cost["cost_low"] is None:
                priced = priced and cost["subscription"]
            else:
                low, high = low + cost["cost_low"], high + cost["cost_high"]
        steps.append(row)
    calls = sum(s["calls"] for s in steps)
    subscription = bool(calls) and all(s.get("subscription") for s in steps if s["calls"])
    return {"steps": steps, "calls": calls, "est_tokens": p["est_tokens"],
            "cost_low": low if calls and priced and not subscription else None,
            "cost_high": high if calls and priced and not subscription else None,
            "subscription": subscription, "auth": auth,
            "billing": billing_summary(v, run_providers(backends)) if backends else [],
            "busy": history.run_in_progress(v), "engine_ready": engine_ready()}


@method("jobs.migrate")
def start(vault: str) -> dict:
    from watchdog.gui import jobs
    from watchdog.gui.api.contradictions import _auth
    from watchdog.pipeline import history, migrations
    v = require_vault(vault)
    jobs.require_engine(KIND)
    if history.run_in_progress(v):
        raise RpcError(migrations.BUSY, code="busy")
    steps = migrations.pending(v)
    if not steps:
        raise RpcError("This investigation is already up to date.", code="nothing_to_do")
    stages, _effort = migrations.stages()
    backends = [stages[m.stage][0] for m in steps if m.calls_model and m.stage]
    for b in backends:
        a = _auth(b)
        if not a["ok"]:
            raise RpcError(a["reason"] or "No way to reach Claude is set up yet.", code="auth_required")
    from watchdog.auth import KeyChoiceError, check_run_keys
    try:
        check_run_keys(v, backends)
    except KeyChoiceError as e:      # the investigation's chosen key isn't here (D290)
        raise RpcError(str(e), code="key_missing") from None
    job = jobs.MANAGER.start(v, KIND, {}, "Bring this investigation up to date", KIND)
    return job.to_dict()
