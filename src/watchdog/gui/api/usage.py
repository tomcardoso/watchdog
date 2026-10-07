"""`usage.*` — per-run token, cost and latency records (`.watchdog/registry/usage/usage-<ts>.json`).

The grouping, totals and notes come from `cmd/usage`'s own helpers, so a run reads the same here
as in `watchdog usage`. Records written by older versions can be missing fields; every call is
normalised before it is summed rather than failing the screen.
"""

from __future__ import annotations

from pathlib import Path

from watchdog.gui import vaultio
from watchdog.gui.rpc import RpcError, method
from watchdog.gui.vaultio import require_vault

_NUMERIC = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


def _ts(path: Path) -> str:
    stem = path.stem
    return stem[len("usage-"):] if stem.startswith("usage-") else stem


def _calls(path: Path) -> list[dict]:
    data = vaultio.read_json(path, {})
    raw = data.get("calls") if isinstance(data, dict) else None
    out = []
    for c in raw or []:
        if not isinstance(c, dict):
            continue
        c = dict(c)
        c["task"] = c.get("task") or "unknown"
        for k in _NUMERIC:
            c[k] = c.get(k) or 0
        c["cost_usd"] = c.get("cost_usd") or 0.0
        c["latency_s"] = c.get("latency_s") or 0.0
        out.append(c)
    return out


def _corpus(vault: Path) -> dict | None:
    from watchdog.cmd.usage import _corpus_pages
    found = _corpus_pages(vault)
    return {"documents": found[1], "pages": found[0]} if found else None


@method("usage.runs")
def runs(vault: str) -> dict:
    from watchdog.cmd.usage import _STAGE, _run_backends, _run_totals, _subscription_note
    from watchdog.pipeline.orchestrate import usage_files

    v = require_vault(vault)
    rows = []
    for f in usage_files(v):
        calls = _calls(f)
        t = _run_totals(calls)
        stages: dict[str, float] = {}
        for c in calls:
            stage = _STAGE.get(c["task"], c["task"])
            stages[stage] = stages.get(stage, 0.0) + c["cost_usd"]
        rows.append({
            "ts": _ts(f), "file": f.name, "calls": len(calls),
            "input_tokens": t["input_tokens"], "output_tokens": t["output_tokens"],
            "cache_read_tokens": t["cache_read_tokens"], "cache_write_tokens": t["cache_write_tokens"],
            "cost_usd": t["cost_usd"], "latency_s": t["latency_s"],
            "backends": _run_backends(calls), "subscription": _subscription_note(calls) is not None,
            "stages": stages,
        })
    rows.sort(key=lambda r: r["ts"], reverse=True)
    return {"runs": rows, "corpus": _corpus(v)}


def _call_row(c: dict) -> dict:
    from watchdog.cmd.usage import _pages_in_detail
    pages = _pages_in_detail(c.get("detail"))
    return {
        "task": c["task"], "filename": c.get("filename"), "detail": c.get("detail"),
        "model": c.get("model"), "backend": c.get("backend"), "auth_mode": c.get("auth_mode"),
        "effort": c.get("effort"), "attempts": c.get("attempts") or 1, "failed": bool(c.get("failed")),
        "input_tokens": c["input_tokens"], "output_tokens": c["output_tokens"],
        "cache_read_tokens": c["cache_read_tokens"], "cache_write_tokens": c["cache_write_tokens"],
        "latency_s": c["latency_s"], "cost_usd": c["cost_usd"],
        "cost_per_page": (c["cost_usd"] / pages) if pages else None,
        "api_ms": c.get("api_ms"), "num_turns": c.get("num_turns"),
        "reasoning_tokens": c.get("reasoning_tokens"),
    }


def _totals(calls: list[dict]) -> dict:
    return {
        "calls": len(calls),
        "input_tokens": sum(c["input_tokens"] for c in calls),
        "output_tokens": sum(c["output_tokens"] for c in calls),
        "cache_read_tokens": sum(c["cache_read_tokens"] for c in calls),
        "cache_write_tokens": sum(c["cache_write_tokens"] for c in calls),
        "cost_usd": sum(c["cost_usd"] for c in calls),
        "latency_s": sum(c["latency_s"] for c in calls),
    }


@method("usage.run")
def run(vault: str, ts: str | None = None) -> dict:
    """One run's breakdown by stage (latest when `ts` is omitted), as `watchdog usage` prints it."""
    from watchdog.cmd.usage import (
        _STAGE, _STAGE_ORDER, _batch_lifecycle_note, _peak_concurrency, _stage_backends,
        _stage_models, _subscription_note, _wall_span,
    )
    from watchdog.pipeline.orchestrate import usage_files

    v = require_vault(vault)
    files = usage_files(v)
    if not files:
        raise RpcError("No processing runs are recorded for this investigation yet.", code="no_runs")
    if ts:
        matches = [f for f in files if ts in f.stem]
        if not matches:
            raise RpcError(f"No run matching “{ts}” was found.", code="not_found")
        if len(matches) > 1:
            raise RpcError(f"“{ts}” matches more than one run: "
                           f"{', '.join(_ts(f) for f in matches)}.", code="ambiguous")
        chosen = matches[0]
    else:
        chosen = files[-1]

    calls = _calls(chosen)
    by_stage: dict[str, list[dict]] = {}
    for c in calls:
        by_stage.setdefault(_STAGE.get(c["task"], c["task"]), []).append(c)
    ordered = [s for s in _STAGE_ORDER if s in by_stage] + [s for s in by_stage if s not in _STAGE_ORDER]

    stages = []
    for stage in ordered:
        sc = by_stage[stage]
        batch_call = next((c for c in sc if c.get("batch_id")), None)
        stages.append({
            "stage": stage,
            "model": _stage_models(sc),
            "backend": _stage_backends(sc),
            "calls": [_call_row(c) for c in sorted(sc, key=lambda c: -c["cost_usd"])],
            "totals": _totals(sc),
            "wall_seconds": _wall_span(sc),
            "peak_concurrency": _peak_concurrency(sc) if len(sc) > 1 else 1,
            "batch_note": _batch_lifecycle_note(batch_call) if batch_call else None,
        })

    totals = {**_totals(calls), "wall_seconds": _wall_span(calls)}
    corpus = _corpus(v)
    pages = corpus["pages"] if corpus else 0
    return {
        "ts": _ts(chosen),
        "stages": stages,
        "totals": totals,
        "subscription_note": _subscription_note(calls),
        "corpus": corpus,
        "cost_per_page": (totals["cost_usd"] / pages) if pages else None,
    }
