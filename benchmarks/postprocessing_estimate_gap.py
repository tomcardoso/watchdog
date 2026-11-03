"""How far the Add documents estimate's handling of post-processing is from what it billed (#820).

    python benchmarks/postprocessing_estimate_gap.py [--json OUT]

`ingest_setup.cost_estimate` prices a queue as its calibrated `est_tokens` times the $/input-token
of the vault's last three usage files. A normal run writes extraction and post-processing to one
usage file, so post-processing reaches the estimate only through that blended rate; on a first
run there is no history and no dollar figure at all. This replays that arithmetic for every run
recorded on this machine and compares it with what the run's post-processing calls
(`orchestrate.FINALIZE_TASKS`) actually cost.

Two sources, read only, never a model call:
- each registered investigation's `.watchdog/registry/usage/usage-*.json` (`~/.watchdog/projects.json`
  plus every folder under the configured `projects_dir`);
- the global telemetry store (`~/.watchdog/telemetry.db`), one run per `run_id`, for investigations
  whose usage files are gone. An investigation with usage files on disk is read from those only.
Benchmark arms are left out: they never post-process.

It prints aggregates only, never an investigation's name, path or a document's name, so its output
can go in an issue. A subscription run's `cost_usd` is notional (the estimate shows no dollars on a
subscription), so those runs are reported apart.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from watchdog import config  # noqa: E402
from watchdog.pipeline.ingest_setup import _real_input_tokens  # noqa: E402
from watchdog.pipeline.orchestrate import FINALIZE_TASKS  # noqa: E402

EXTRACT_TASKS = {"extract", "extract-section"}
MAX_RUNS = 3   # cost_estimate's default history


def _vaults() -> list[Path]:
    """Every investigation folder this machine knows of, existing or not."""
    out: list[Path] = []
    projects = config.home() / "projects.json"
    if projects.exists():
        data = json.loads(projects.read_text(encoding="utf-8"))
        for value in data.values():
            entries = value.values() if isinstance(value, dict) else value
            out += [Path(e).expanduser() for e in entries if isinstance(e, str)]
    cfg = config.home() / "config.json"
    if cfg.exists():
        pdir = json.loads(cfg.read_text(encoding="utf-8")).get("projects_dir")
        if pdir and Path(pdir).expanduser().is_dir():
            out += [p for p in Path(pdir).expanduser().iterdir() if p.is_dir()]
    seen, unique = set(), []
    for v in out:
        key = str(v.resolve()) if v.exists() else str(v)
        if key not in seen:
            seen.add(key)
            unique.append(v)
    return unique


def _run(calls: list[dict], est_input_tokens: int | None) -> dict:
    """One run's figures, with totals summed the way `_usage_totals` does for `cost_estimate`."""
    tasks = {c.get("task") for c in calls}
    totals = {k: sum(c.get(k) or 0 for c in calls)
              for k in ("input_tokens", "cache_read_tokens", "cache_write_tokens")}
    by_task: dict[str, float] = {}
    for c in calls:
        if c.get("task") in FINALIZE_TASKS:
            by_task[c["task"]] = by_task.get(c["task"], 0.0) + (c.get("cost_usd") or 0.0)
    if est_input_tokens is None:
        # Each extract or extract-section record carries the chars/4 estimate of the text it sent
        # (a whole document or one section), so their sum is the run's document estimate.
        est_input_tokens = sum(c.get("est_input_tokens") or 0 for c in calls) or None
    return {
        "kind": ("extraction" if tasks & EXTRACT_TASKS
                 else "post-processing only" if tasks <= FINALIZE_TASKS else "other"),
        "subscription": any(c.get("auth_mode") == "subscription" or c.get("backend") == "claude-agent-sdk"
                            for c in calls),
        "documents": len({c.get("filename") for c in calls if c.get("task") in EXTRACT_TASKS}),
        "est_input_tokens": est_input_tokens,
        "real_input_tokens": _real_input_tokens(totals),
        "extract_input_tokens": sum(_real_input_tokens(c) for c in calls if c.get("task") in EXTRACT_TASKS),
        "cost": sum(c.get("cost_usd") or 0.0 for c in calls),
        "post_cost": sum(by_task.values()),
        "post_by_task": by_task,
    }


def _from_usage_files(vault: Path) -> list[dict]:
    usage = vault / ".watchdog" / "registry" / "usage"
    runs = []
    for uf in sorted(usage.glob("usage-*.json"), key=lambda p: p.name):
        try:
            data = json.loads(uf.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        calls = data.get("calls") or []
        if calls:
            runs.append(_run(calls, (data.get("totals") or {}).get("est_input_tokens")))
    return runs


def _from_telemetry(skip: set[str]) -> list[list[dict]]:
    db_path = config.home() / "telemetry.db"
    if not db_path.exists():
        return []
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    rows = db.execute("SELECT * FROM calls WHERE benchmark_arm_id IS NULL ORDER BY end_ts").fetchall()
    by_vault: dict[str, dict[str, list[dict]]] = {}
    for r in rows:
        if r["vault_path"] in skip:
            continue
        by_vault.setdefault(r["vault_path"], {}).setdefault(r["run_id"], []).append(dict(r))
    # Runs in the order they began, as usage files sort by when they ended; close enough here.
    return [[_run(calls, None) for calls in runs.values()] for runs in by_vault.values()]


def _replay(runs: list[dict]) -> list[dict]:
    """For each extraction run, what `cost_estimate` would have shown before it, from the runs
    before it in the same investigation."""
    out = []
    for i, run in enumerate(runs):
        if run["kind"] != "extraction":
            continue
        prior = runs[:i]
        row = dict(run, first=not prior, history_runs=0, estimate_low=None, estimate_high=None,
                   implied_post=None, history_kinds=[])
        # `_tokens_calibration`: actual/estimated input over the last three runs that carry an estimate.
        cal = [p["real_input_tokens"] / p["est_input_tokens"]
               for p in reversed(prior) if p["est_input_tokens"] and p["real_input_tokens"]][:MAX_RUNS]
        est = run["est_input_tokens"] or 0
        est_tokens = est * (sum(cal) / len(cal)) if cal else est
        # The $/token history: the last three usage files, whatever they were (Ask and
        # post-processing-only runs included, as `cost_estimate` reads them).
        history = [p for p in prior[-MAX_RUNS:] if p["real_input_tokens"] > 0 and p["cost"]]
        if history and est_tokens:
            rates = [p["cost"] / p["real_input_tokens"] for p in history]
            row["history_runs"] = len(history)
            row["history_kinds"] = [p["kind"] for p in history]
            row["estimate_low"] = est_tokens * min(rates)
            row["estimate_high"] = est_tokens * max(rates)
            # The share of the history's dollars that was post-processing is what the blended rate
            # folds in for it: the estimate's implicit post-processing figure.
            share = sum(p["post_cost"] for p in history) / sum(p["cost"] for p in history)
            row["implied_post"] = (row["estimate_low"] * share, row["estimate_high"] * share)
        out.append(row)
    return out


def _band(values: list[float], fmt: str = "{:.2f}") -> str:
    if not values:
        return "none"
    if len(values) == 1:
        return fmt.format(values[0])
    return (f"{fmt.format(min(values))} to {fmt.format(max(values))} "
            f"(median {fmt.format(statistics.median(values))})")


def report(rows: list[dict], investigations: int, other_runs: dict[str, int]) -> list[str]:
    lines = [f"Investigations with usage records: {investigations}; extraction runs: {len(rows)}; "
             f"other runs: {other_runs}"]
    for label, group in (("Pay-as-you-go key", [r for r in rows if not r["subscription"]]),
                         ("Subscription (notional cost)", [r for r in rows if r["subscription"]])):
        if not group:
            continue
        lines.append(f"\n## {label}: {len(group)} extraction runs")
        with_post = [r for r in group if r["post_cost"] > 0]
        lines.append(f"Runs that post-processed in the same run: {len(with_post)} of {len(group)}")
        lines.append("Post-processing share of the run's cost: "
                     + _band([r["post_cost"] / r["cost"] for r in with_post if r["cost"]], "{:.0%}"))
        lines.append("Post-processing cost per run, USD: " + _band([r["post_cost"] for r in with_post], "${:.3f}"))
        lines.append("Post-processing cost per document, USD: "
                     + _band([r["post_cost"] / r["documents"] for r in with_post if r["documents"]], "${:.4f}"))
        lines.append("Post-processing USD per 1M estimated document tokens: "
                     + _band([r["post_cost"] / r["est_input_tokens"] * 1e6
                              for r in with_post if r["est_input_tokens"]], "${:.2f}"))
        lines.append("Extraction calls' real input / document estimate: "
                     + _band([r["extract_input_tokens"] / r["est_input_tokens"] for r in group
                              if r["est_input_tokens"] and r["extract_input_tokens"]], "{:.1f}x"))
        firsts = [r for r in group if r["first"]]
        lines.append(f"First runs (estimate shows no dollar figure): {len(firsts)}; "
                     f"their post-processing cost: {_band([r['post_cost'] for r in firsts], '${:.3f}')}; "
                     f"share of their total: "
                     + _band([r["post_cost"] / r["cost"] for r in firsts if r["cost"]], "{:.0%}"))
        replayed = [r for r in group if r["estimate_low"] is not None]
        lines.append(f"Runs with a replayable blended estimate: {len(replayed)}")
        for r in replayed:
            lo, hi = r["implied_post"]
            lines.append(
                f"  history {r['history_runs']} run(s) ({', '.join(r['history_kinds'])}): "
                f"total actual / estimate {r['cost'] / r['estimate_high']:.2f}x to "
                f"{r['cost'] / r['estimate_low']:.2f}x; post-processing actual / implied "
                + (f"{r['post_cost'] / hi:.2f}x to {r['post_cost'] / lo:.2f}x" if lo else
                   "n/a (history had no post-processing)")
                + f"; extraction actual / implied "
                f"{(r['cost'] - r['post_cost']) / (r['estimate_high'] - hi):.2f}x to "
                f"{(r['cost'] - r['post_cost']) / max(r['estimate_low'] - lo, 1e-12):.2f}x")
        tasks: dict[str, float] = {}
        for r in with_post:
            for t, c in r["post_by_task"].items():
                tasks[t] = tasks.get(t, 0.0) + c
        total = sum(tasks.values())
        if total:
            lines.append("Post-processing cost by task (pooled): " + ", ".join(
                f"{t} {c / total:.0%}" for t, c in sorted(tasks.items(), key=lambda kv: -kv[1])))
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", type=Path, help="also write the per-run rows (anonymous) to this file")
    args = ap.parse_args()

    per_vault: list[list[dict]] = []
    on_disk: set[str] = set()
    for vault in _vaults():
        runs = _from_usage_files(vault)
        if runs:
            per_vault.append(runs)
            on_disk.add(str(vault))
            if vault.exists():
                on_disk.add(str(vault.resolve()))
    per_vault += _from_telemetry(on_disk)

    rows: list[dict] = []
    other: dict[str, int] = {}
    for runs in per_vault:
        rows += _replay(runs)
        for r in runs:
            if r["kind"] != "extraction":
                other[r["kind"]] = other.get(r["kind"], 0) + 1
    # A run that recorded cost but no input tokens has nothing for a rate to divide by.
    empty = [r for r in rows if not r["real_input_tokens"]]
    rows = [r for r in rows if r["real_input_tokens"]]
    if empty:
        other["extraction, no tokens recorded (left out)"] = len(empty)
    print("\n".join(report(rows, len(per_vault), other)))
    if args.json:
        args.json.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
