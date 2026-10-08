"""`ingest.*` — what a pipeline run would do, before it starts: the public-records warning's
count, which model runs each stage, whether auth is in place, and the cost estimate.

The values come from the CLI's own resolvers (`cmd/ingest._resolve_stage`, `_effort`,
`_auto_approve_verdict`, `ingest_setup.cost_estimate`, …), so the app can never show a plan that
`watchdog add` would not follow. Nothing here takes a lock or writes.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Any

from watchdog import defaults
from watchdog.gui.rpc import RpcError, method
from watchdog.gui.vaultio import require_vault, strip_ansi

_OPTION_KEYS = (
    "extractor_model", "classifier_model", "finalizer_model", "finalizer_reconciliation_model",
    "finalizer_synthesis_model", "finalizer_timeline_model", "finalizer_briefing_model",
    "extractor_effort", "classifier_effort", "finalizer_effort", "concurrency", "classify_pages",
    "skill", "limit", "verify", "force", "wait", "skip_briefing", "chew_workers", "chunk_workers",
)


def options_namespace(options: dict | None) -> argparse.Namespace:
    """The `dig`/`bark`/`add` flags a `RunOptions` object stands for, as the argparse namespace
    the CLI's resolvers read. An unset (or null) option is unset, so the configured default wins."""
    opts = options or {}
    if not isinstance(opts, dict):
        raise RpcError("options must be an object", code="bad_params")
    values: dict[str, Any] = {k: None for k in _OPTION_KEYS}
    for k in _OPTION_KEYS:
        if opts.get(k) is not None and opts.get(k) != "":
            values[k] = opts[k]
    values["force"] = bool(values["force"])
    return argparse.Namespace(**values)


@dataclass
class Plan:
    """Every stage's resolved `[backend:]model` and effort for one prospective run."""
    config: dict
    classify: tuple
    extract: tuple
    post: tuple
    classify_effort: str | None
    extract_effort: str | None
    post_effort: str | None
    overrides: dict = field(default_factory=dict)

    @property
    def backends(self) -> list:
        from watchdog.cmd.ingest import _FINALIZER_STAGES
        return [self.classify[0], self.extract[0], self.post[0],
                *(self.overrides.get(f"{s}_backend") for s in _FINALIZER_STAGES)]


def resolve_plan(args: argparse.Namespace) -> Plan:
    """Resolve stages and efforts exactly as `cmd_ingest` does (`sys.exit` on a bad value becomes
    the app's error message)."""
    from watchdog.cmd.base import load_config
    from watchdog.cmd.ingest import _effort, _resolve_finalizer_overrides, _resolve_stage

    config = load_config()
    extract = _resolve_stage(args.extractor_model, config.get("extractor_model"))
    post = _resolve_stage(args.finalizer_model, config.get("finalizer_model"),
                          default=defaults.FINALIZER_MODEL)
    classify = _resolve_stage(args.classifier_model, config.get("classifier_model"),
                              default=defaults.CLASSIFIER_MODEL)
    return Plan(
        config=config, classify=classify, extract=extract, post=post,
        classify_effort=_effort(args.classifier_effort, config.get("classifier_effort"),
                                default=defaults.CLASSIFIER_EFFORT, backend=classify[0],
                                model=classify[1]),
        extract_effort=_effort(args.extractor_effort, config.get("extractor_effort"),
                               default=defaults.EXTRACTOR_EFFORT, backend=extract[0],
                               model=extract[1]),
        post_effort=_effort(args.finalizer_effort, config.get("finalizer_effort")),
        overrides=_resolve_finalizer_overrides(args, config, post[0], post[1]),
    )


def _queue(vault, args: argparse.Namespace) -> tuple[list, list]:
    """`(queue_files, to_send)` as `cmd_ingest` counts them: the queue after `--limit`, then
    what is neither staged nor committed (everything, under `--force`)."""
    from watchdog.pipeline.ingest_setup import limit_queue, needs_extraction, scan_queue
    try:
        limit = int(args.limit) if args.limit is not None else None
    except (TypeError, ValueError):
        raise RpcError("The limit must be a whole number.", code="bad_params")
    queue_files = limit_queue(vault, scan_queue(vault), limit, force=args.force)
    return queue_files, (queue_files if args.force else needs_extraction(vault, queue_files))


def _auth(plan: Plan) -> dict:
    """Claude auth is only needed when some stage is routed to it (as `cmd_ingest` checks)."""
    from watchdog.cmd.auth import resolve_auth
    from watchdog.model_client import CLAUDE_BACKENDS
    if not any(b is None or b in CLAUDE_BACKENDS for b in plan.backends):
        return {"mode": None, "ok": True, "reason": None}
    a = resolve_auth()
    mode = a.get("mode")
    return {"mode": mode, "ok": mode in ("subscription", "api-key"),
            "reason": a.get("reason") if mode == "none" else None}


def _label(backend: str | None, model: str) -> str:
    return f"{backend}:{model}" if backend else model


def _model_rows(plan: Plan, auth_mode: str | None) -> list[dict]:
    from watchdog.cmd.ingest import _FINALIZER_STAGES

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
    for stage in _FINALIZER_STAGES:
        b = plan.overrides.get(f"{stage}_backend", plan.post[0])
        m = plan.overrides.get(f"{stage}_model", plan.post[1])
        if (b, m) != plan.post:
            rows.append(row(f"finalizer:{stage}", b, m, plan.post_effort))
    return rows


@method("ingest.preflight")
def preflight(vault: str, options: dict | None = None) -> dict:
    from watchdog.cmd import home
    from watchdog.cmd.base import _count_awaiting_bark, _count_incoming, _count_queued
    from watchdog.cmd.ingest import _auto_approve_on, _auto_approve_verdict, _public_records_warning
    from watchdog.pipeline import orchestrate

    v = require_vault(vault)
    args = options_namespace(options)
    plan = resolve_plan(args)
    _queue_files, to_send = _queue(v, args)
    auth = _auth(plan)

    enabled = _auto_approve_on(plan.config)
    approve, blocker = False, None
    if enabled and to_send:
        gate = _auto_approve_verdict(auth_mode=auth["mode"], stages=plan.backends)
        approve, blocker = bool(gate.get("approve")), gate.get("blocker")

    warning = strip_ansi(_public_records_warning(len(to_send))).strip("\n")
    warning = "\n".join(ln[2:] if ln.startswith("  ") else ln for ln in warning.splitlines())
    return {
        "documents_to_send": len(to_send),
        "incoming": _count_incoming(v),
        "queued": _count_queued(v),
        "staged": _count_awaiting_bark(v),
        "failed": home._failed(v),
        "pending_finalization": (orchestrate.pending_finalization(v)
                                 if orchestrate.has_pending_finalization(v) else None),
        "auth": auth,
        "models": _model_rows(plan, auth["mode"]),
        "auto_approve": {"enabled": enabled, "approve": approve, "blocker": blocker},
        "warning_text": warning,
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
    from watchdog.cmd.auth import resolve_auth
    from watchdog.cmd.ingest import (
        _effective_extract_backend, _failed_count, _format_all_models_estimate,
        _format_cost_estimate, _format_finalize_estimate, _quarantine_notice, _resolve_stage,
    )
    from watchdog.cmd.base import load_config
    from watchdog.pipeline import ingest_setup, orchestrate

    if stage not in ("dig", "bark"):
        raise RpcError("stage must be “dig” or “bark”.", code="bad_params")
    v = require_vault(vault)
    args = options_namespace(options)
    config = load_config()

    if stage == "dig":
        backend, _model = _resolve_stage(args.extractor_model, config.get("extractor_model"))
        queue_files, _ = _queue(v, args)
        if not queue_files:
            failed = _failed_count(v)
            text = (strip_ansi(_quarantine_notice(failed)).strip() + " Nothing else is queued."
                    if failed else "The queue is empty — nothing to estimate.")
            return {"text": text, "estimate": None, "all_models": None}
        mode = resolve_auth()["mode"] if backend is None else None
        est = ingest_setup.cost_estimate(v, queue_files, _effective_extract_backend(backend, mode))
        text = strip_ansi(_format_cost_estimate(est)).strip()
        rows_fn = ingest_setup.cost_estimate_all_models
    else:
        backend, _model = _resolve_stage(args.finalizer_model, config.get("finalizer_model"),
                                         default=defaults.FINALIZER_MODEL)
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
