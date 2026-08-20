"""Cost-preview reference data for run_benchmark.py (issue #478).

Every benchmark arm targets a freshly seeded vault — `BENCHMARKING.md`'s "every condition is its
own fresh vault, never reuse a vault" rule — so `ingest_setup.cost_estimate`/
`finalize_cost_estimate`'s own $/token ratio, read from the *target* vault's usage history, is
always empty at preview time. This is not a first-run edge case that resolves itself: it is the
permanent state of every arm.

This module looks instead at usage files already archived from previous benchmark runs of the
same model/effort/backend combination (`benchmarks/<run-id>/artifacts/<vault-name>/usage/`,
written by `bench_report.write_run`), and falls back to a small documented ratio table only when
no such history exists anywhere yet.
"""
from __future__ import annotations

import json
from pathlib import Path

from watchdog.pipeline import orchestrate

# Rough output:input token ratio by reasoning-effort tier, used only when `reference_usage_files`
# finds no archived run at all for the model/effort/backend being estimated (issue #478's
# suggested item 2). Derived from a single data point — the gpt-5.6-luna low/med/high re-run in
# #509 (PR #527, see the issue's comment): output verbosity swings ~4x across effort tiers on
# that one model alone, so this is an order-of-magnitude placeholder, not a calibrated figure —
# nothing here says another model swings the same way.
DEFAULT_OUTPUT_RATIO_BY_EFFORT: dict[str | None, float] = {
    "low": 0.17,
    "medium": 0.24,
    None: 0.24,
    "high": 0.68,
    "xhigh": 0.68,
    "max": 0.68,
}


def _matches(data: dict, model: str, effort: str | None, backend: str | None,
            finalize_only: bool) -> bool:
    calls = data.get("calls") or []
    if not calls:
        return False
    if any(c.get("model") != model or c.get("effort") != effort or c.get("backend") != backend
          for c in calls):
        return False
    if finalize_only and any(c.get("task") not in orchestrate.FINALIZE_TASKS for c in calls):
        return False
    return True


def reference_usage_files(benchmarks_root: Path, model: str, effort: str | None,
                          backend: str | None, max_runs: int = 3,
                          finalize_only: bool = False) -> list[Path]:
    """Usage files from any past benchmark run whose calls all match this exact
    model/effort/backend combination (and, for the finalizer, all fall in `FINALIZE_TASKS`) —
    the closest available substitute for a fresh arm vault's own (nonexistent) history. Matching
    on backend too, not just model+effort: the same model can be metered very differently across
    backends (`claude-batch`'s 50% discount vs `claude-api`), so borrowing a ratio across backends
    would misprice by the backend gap, not just approximate the model's own variance.

    Oldest-first, capped to the most recent `max_runs` — matching `orchestrate.usage_files`'s own
    ordering contract, since callers pass this straight through to `cost_estimate`/
    `finalize_cost_estimate` in place of that function's own vault scan.
    """
    candidates = []
    # Both layouts: runs now land in `runs/<id>/` (#550), but runs archived before that move sit
    # directly under the benchmarks root and are still perfectly good references. The two patterns
    # sit at different depths and so never match the same directory.
    root = Path(benchmarks_root)
    usage_dirs = sorted([*root.glob("runs/*/artifacts/*/usage"),
                         *root.glob("*/artifacts/*/usage")])
    for usage_dir in usage_dirs:
        for uf in sorted(usage_dir.glob("usage-*.json")):
            try:
                data = json.loads(uf.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if _matches(data, model, effort, backend, finalize_only):
                candidates.append(uf)
    candidates.sort(key=lambda p: p.name)
    return candidates[-max_runs:]


def fallback_estimate(est_tokens: int, model: str, effort: str | None) -> dict | None:
    """Catalog-list-price point estimate for a model/effort with no archived usage anywhere yet
    (issue #478's suggested item 2) — priced directly against `model_catalog.yaml`'s own
    published rate rather than any run-derived $/token ratio, scaled by
    `DEFAULT_OUTPUT_RATIO_BY_EFFORT`'s rough per-effort ratio. Returns `None` for a model not in
    the catalog, so a caller never fabricates a number from nothing.
    """
    from watchdog.model_catalog import all_models, price_multiplier
    row = next((m for m in all_models() if m["id"] == model), None)
    if row is None:
        return None
    ratio = DEFAULT_OUTPUT_RATIO_BY_EFFORT.get(effort, DEFAULT_OUTPUT_RATIO_BY_EFFORT[None])
    # Priced at the rate in force now, for a model whose rates vary by the clock (D217) — a
    # pre-flight figure for an arm about to run. An arm that runs long enough to cross a peak
    # boundary is billed on both sides of it; only the archived per-call costs show that.
    cost = ((est_tokens * row["input"] + est_tokens * ratio * row["output"])
            * price_multiplier(model))
    return {"cost_low": cost, "cost_high": cost, "runs_used": 0, "projected": True}
