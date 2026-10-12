"""`ingest.*` — what a pipeline run would do, before it starts: the public-records warning's
count, which model runs each stage, whether auth is in place, and the cost estimate.

The values come from the run's own resolvers (`ops.ingest.resolve_plan`, `_auto_approve_verdict`,
`ingest_setup.cost_estimate`, …), so the app can never show a plan that a run would not follow.
Nothing here takes a lock or writes.
"""

from __future__ import annotations

from dataclasses import fields

from watchdog.gui.rpc import RpcError, method
from watchdog.gui.vaultio import require_vault, strip_ansi
from watchdog.ops.ingest import FINALIZER_STAGES, Plan, RunOptions, resolve_plan

# The `RunOptions` fields a form may set (`RunOptions` in gui/src/shared/api.ts).
_OPTION_KEYS = (
    "extractor_model", "classifier_model", "finalizer_model", "finalizer_reconciliation_model",
    "finalizer_synthesis_model", "finalizer_timeline_model", "finalizer_briefing_model",
    "extractor_effort", "classifier_effort", "finalizer_effort", "concurrency", "classify_pages",
    "skill", "limit", "verify", "force", "wait", "skip_briefing",
)


def run_options(options: dict | None) -> RunOptions:
    """The `RunOptions` a form's options object stands for. An unset (or null) option is unset,
    so the setting wins."""
    opts = options or {}
    if not isinstance(opts, dict):
        raise RpcError("options must be an object", code="bad_params")
    known = {f.name for f in fields(RunOptions)}
    values = {k: opts[k] for k in _OPTION_KEYS
              if k in known and opts.get(k) is not None and opts.get(k) != ""}
    if "limit" in values:
        try:
            values["limit"] = int(values["limit"])
        except (TypeError, ValueError):
            raise RpcError("The limit must be a whole number.", code="bad_params")
    values["force"] = bool(values.get("force"))
    return RunOptions(**values)


def _queue(vault, opts: RunOptions) -> tuple[list, list]:
    """`(queue_files, to_send)` as a run counts them: the queue after the limit, then what is
    neither staged nor committed (everything, when re-extracting)."""
    from watchdog.pipeline.ingest_setup import limit_queue, needs_extraction, scan_queue
    queue_files = limit_queue(vault, scan_queue(vault), opts.limit, force=opts.force)
    return queue_files, (queue_files if opts.force else needs_extraction(vault, queue_files))


def _auth(plan: Plan) -> dict:
    """Claude auth is only needed when some stage is routed to it (as a run checks)."""
    from watchdog.auth import resolve_auth
    from watchdog.model_client import CLAUDE_BACKENDS
    if not any(b is None or b in CLAUDE_BACKENDS for b in plan.backends):
        return {"mode": None, "ok": True, "reason": None}
    a = resolve_auth()
    mode = a.get("mode")
    return {"mode": mode, "ok": mode in ("subscription", "api-key"),
            "reason": a.get("reason") if mode == "none" else None}


def _billing(vault, backends) -> list[dict]:
    """Which labelled key will pay for each provider the run uses, and whether one the
    investigation chose is missing from this computer (#690, D290). Never the key itself."""
    from watchdog.auth import billing_summary, run_providers
    return billing_summary(vault, run_providers(backends))


def _label(backend: str | None, model: str) -> str:
    return f"{backend}:{model}" if backend else model


def _model_rows(plan: Plan, auth_mode: str | None) -> list[dict]:

    def effective(backend):
        # A bare Claude tier is routed by auth mode — say which backend that really is.
        if backend:
            return backend
        return {"subscription": "claude-agent-sdk", "api-key": "claude-api"}.get(auth_mode or "")

    def row(stage, backend, model, effort):
        return {"stage": stage, "backend": effective(backend), "model": model, "effort": effort,
                "label": _label(backend, model)}

    rows = [row("classifier", *plan.classify, plan.classify_effort),
            row("extractor", *plan.extract, plan.extract_effort),
            row("finalizer", *plan.post, plan.post_effort)]
    for stage in FINALIZER_STAGES:
        b = plan.overrides.get(f"{stage}_backend", plan.post[0])
        m = plan.overrides.get(f"{stage}_model", plan.post[1])
        if (b, m) != plan.post:
            rows.append(row(f"finalizer:{stage}", b, m, plan.post_effort))
    return rows


@method("ingest.preflight")
def preflight(vault: str, options: dict | None = None) -> dict:
    from watchdog.vault import count_awaiting_post, count_failed, count_incoming, count_queued
    from watchdog.ops.ingest import (_auto_approve_on, _auto_approve_verdict, _public_records_warning,
                                     local_only_note)
    from watchdog.pipeline import orchestrate

    from watchdog.ops import sample
    v = require_vault(vault)
    if sample.is_sample_folder(v):
        return _sample_preflight(v, run_options(options))
    opts = run_options(options)
    plan = resolve_plan(opts)
    _queue_files, to_send = _queue(v, opts)
    auth = _auth(plan)

    enabled = _auto_approve_on(plan.config)
    approve, blocker = False, None
    if enabled and to_send:
        gate = _auto_approve_verdict(auth_mode=auth["mode"], stages=plan.backends)
        approve, blocker = bool(gate.get("approve")), gate.get("blocker")

    # Every stage on a model server on this computer: no acknowledgement, one plain line (D306).
    # The run decides again for itself at the gate; this only tells the app what it will do.
    local_only = local_only_note(plan.backends)
    warning = strip_ansi(_public_records_warning(len(to_send), plan.backends)).strip("\n")
    warning = "\n".join(ln[2:] if ln.startswith("  ") else ln for ln in warning.splitlines())
    return {
        "documents_to_send": len(to_send),
        "incoming": count_incoming(v),
        "queued": count_queued(v),
        "staged": count_awaiting_post(v),
        "failed": count_failed(v),
        "pending_finalization": (orchestrate.pending_finalization(v)
                                 if orchestrate.has_pending_finalization(v) else None),
        "auth": auth,
        "billing": _billing(v, plan.backends),
        "models": _model_rows(plan, auth["mode"]),
        "auto_approve": {"enabled": enabled, "approve": approve, "blocker": blocker},
        "warning_text": warning,
        "ack_required": local_only is None,
        "ack_note": local_only,
        "sample": None,
    }


def _sample_preflight(v, opts: RunOptions) -> dict:
    """The preflight in the sample investigation (D307): the run answers from prepared answers,
    so no auth, key, model or acknowledgement, and it names any queued file that would be refused."""
    from watchdog.ops import sample
    from watchdog.ops.ingest import _sample_opts
    from watchdog.pipeline import orchestrate
    from watchdog.vault import count_awaiting_post, count_failed, count_incoming, count_queued
    _queue_files, to_send = _queue(v, _sample_opts(opts))
    return {
        "documents_to_send": len(to_send),
        "incoming": count_incoming(v), "queued": count_queued(v), "staged": count_awaiting_post(v),
        "failed": count_failed(v),
        "pending_finalization": (orchestrate.pending_finalization(v)
                                 if orchestrate.has_pending_finalization(v) else None),
        "auth": {"mode": None, "ok": True, "reason": None},
        "billing": [],
        "models": [],
        "auto_approve": {"enabled": False, "approve": False, "blocker": None},
        "warning_text": "",
        "ack_required": False,
        "ack_note": sample.NOTE,
        "sample": {"unprepared": sample.unprepared(v, to_send), "refusal": sample.REFUSAL},
    }


def _all_models_rows(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        rate = r.get("price_multiplier", 1.0)
        note = None
        if rate > 1:
            note = "peak rate — this model is cheaper outside peak hours"
        elif rate < 1:
            note = "off-peak rate — this model costs more during peak hours"
        out.append({"label": r["name"], "provider": r["provider"], "cost_usd": r["cost"], "note": note})
    return out


@method("ingest.estimate")
def estimate(vault: str, stage: str, all_models: bool = False, options: dict | None = None) -> dict:
    from watchdog.auth import resolve_auth
    from watchdog.ops.ingest import (
        _effective_extract_backend, _format_all_models_estimate, _format_cost_estimate,
        _format_finalize_estimate, _quarantine_notice,
    )
    from watchdog.pipeline import ingest_setup, orchestrate
    from watchdog.vault import count_failed

    from watchdog.ops import sample
    if stage not in ("dig", "bark"):
        raise RpcError("stage must be “dig” or “bark”.", code="bad_params")
    v = require_vault(vault)
    if sample.is_sample_folder(v):
        # Prepared answers (D307): nothing to price, whatever model the options name.
        return {"text": "Estimated cost: $0.00. " + sample.NOTE, "estimate": None, "all_models": None}
    opts = run_options(options)
    plan = resolve_plan(opts)

    if stage == "dig":
        backend = plan.extract[0]
        queue_files, _ = _queue(v, opts)
        if not queue_files:
            failed = count_failed(v)
            text = (strip_ansi(_quarantine_notice(failed)).strip() + " Nothing else is queued."
                    if failed else "The queue is empty — nothing to estimate.")
            return {"text": text, "estimate": None, "all_models": None}
        mode = resolve_auth()["mode"] if backend is None else None
        est = ingest_setup.cost_estimate(v, queue_files, _effective_extract_backend(backend, mode))
        text = strip_ansi(_format_cost_estimate(est)).strip()
        rows_fn = ingest_setup.cost_estimate_all_models
    else:
        backend = plan.post[0]
        if not orchestrate.has_pending_finalization(v):
            return {"text": "Nothing to finish — run processing first.", "estimate": None,
                    "all_models": None}
        mode = resolve_auth()["mode"] if backend is None else None
        est = ingest_setup.finalize_cost_estimate(v, _effective_extract_backend(backend, mode))
        text = strip_ansi(_format_finalize_estimate(est)).strip()
        rows_fn = ingest_setup.finalize_cost_estimate_all_models

    rows = None
    if all_models:
        rows = rows_fn(v, est["est_tokens"])
        text += "\n\n" + strip_ansi(_format_all_models_estimate(rows)).strip()
    return {"text": text, "estimate": est,
            "all_models": _all_models_rows(rows) if rows is not None else None}
