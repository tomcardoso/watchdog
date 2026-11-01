"""Compare what a run recorded with what Anthropic billed for the day (D366).

    python benchmarks/reconcile_cost.py RUN_DIR --day 2026-10-31 [--workspace wrkspc_...]

Watchdog prices every call itself (the Messages API returns tokens, not dollars), so a pricing rule
it gets wrong shows up only against the bill. This sums `cost_usd` over the Claude calls in every
`usage-*.json` under RUN_DIR that ended on `--day` (UTC), then, only when `ANTHROPIC_ADMIN_KEY`
holds an Admin API key (`sk-ant-admin...`), reads that day's `GET /v1/organizations/cost_report`
grouped by workspace and description and prints the two side by side. Without the key it prints
the recorded figure and stops: it never sends a request.

The cost report is daily and organisation-wide, groupable by workspace but not by API key, so the
comparison is only clean when the run had its workspace to itself that day (RUNBOOK step 4). Data
usually appears within minutes, occasionally later. The app never holds an admin key; this is a
developer's tool.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

API = "https://api.anthropic.com/v1/organizations/cost_report"
CLAUDE_BACKENDS = ("claude-api", "claude-batch")


def recorded(run_dir: Path, day: dt.date) -> tuple[float, int]:
    """(USD, calls) Watchdog recorded for Claude API and batch calls ending on `day`, UTC."""
    total, n = 0.0, 0
    for uf in sorted(run_dir.rglob("usage-*.json")):
        try:
            calls = json.loads(uf.read_text(encoding="utf-8")).get("calls") or []
        except (OSError, json.JSONDecodeError):
            continue
        for c in calls:
            if c.get("backend") not in CLAUDE_BACKENDS or not c.get("end_ts"):
                continue
            if dt.datetime.fromtimestamp(c["end_ts"], dt.timezone.utc).date() != day:
                continue
            total += c.get("cost_usd") or 0.0
            n += 1
    return total, n


def billed(key: str, day: dt.date, workspace: str | None) -> dict[str, float]:
    """USD per description for `day` from the cost report, limited to `workspace` when given
    (`null` in the report is the default workspace)."""
    import httpx
    start = dt.datetime.combine(day, dt.time(), dt.timezone.utc)
    params = [("starting_at", start.strftime("%Y-%m-%dT%H:%M:%SZ")),
              ("ending_at", (start + dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")),
              ("group_by[]", "workspace_id"), ("group_by[]", "description")]
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    out: dict[str, float] = {}
    page = None
    while True:
        resp = httpx.get(API, params=params + ([("page", page)] if page else []), headers=headers,
                         timeout=30)
        resp.raise_for_status()
        body = resp.json()
        for bucket in body.get("data") or []:
            for r in bucket.get("results") or []:
                if workspace and r.get("workspace_id") != workspace:
                    continue
                desc = r.get("description") or "(no description)"
                out[desc] = out.get(desc, 0.0) + float(r.get("amount") or 0) / 100   # cents
        if not body.get("has_more"):
            return out
        page = body.get("next_page")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--day", required=True, type=dt.date.fromisoformat, help="UTC day, YYYY-MM-DD")
    ap.add_argument("--workspace", help="the run's workspace id (wrkspc_...)")
    args = ap.parse_args(argv)
    ours, n = recorded(args.run_dir, args.day)
    print(f"Watchdog recorded ${ours:,.4f} over {n} Claude call(s) on {args.day}.")
    key = os.environ.get("ANTHROPIC_ADMIN_KEY", "")
    if not key.startswith("sk-ant-admin"):
        print("No Admin API key in ANTHROPIC_ADMIN_KEY: compare with the Console's Cost page instead.")
        return 0
    rows = billed(key, args.day, args.workspace)
    for desc, usd in sorted(rows.items(), key=lambda kv: -kv[1]):
        print(f"  ${usd:>10,.4f}  {desc}")
    total = sum(rows.values())
    scope = f"workspace {args.workspace}" if args.workspace else "the whole organisation"
    print(f"Anthropic billed ${total:,.4f} for {scope} on {args.day}; "
          f"difference ${total - ours:+,.4f}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
