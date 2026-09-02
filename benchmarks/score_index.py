"""The extractor model index (#551): one row per model+effort arm — Model, Effort, Main facts,
Easy to miss, Cost/page, Speed/page, Reliability — sourced from kept `benchmarks/runs/*/run.json`
files.

    ~/.local/pipx/venvs/watchdog-intel/bin/python benchmarks/score_index.py \\
        [run_dir ...] [--out benchmarks/index] [--sort-by must_not_miss] \\
        [--judged SUMMARY.json ...]

Deliberately not a composite score — that shape was tried (`FINDINGS.md`, 2026-07-29) and
rejected: a blended `must_not_miss`/`facts` number divided by cost ranked a 55%-recall arm as
36x "better value" than one at 90%+, because a pure ratio treats a large recall gap as something
a low enough price buys back. The gate/floor/composite this issue also describes are deferred,
not designed away — they are machinery for a *recommendation*, and this is measurement only. See
the issue's second comment (2026-08-31) for the full set of decisions this module follows.

**An arm is not rated without a judge pass.** `score_arms.py`'s numeric-anchor recall covers only
about a third of the key items (the rest need a human or judge verdict — RUNBOOK.md step 6), and
the two slices have disagreed on the winner before. Passing `--judged` with one or more
`qualitative/aggregate.py` summary.json files moves any arm id they cover into the rated table,
using `verbatim + credited` as a hit, same as RUNBOOK.md's convention. `--judged` may be repeated
— each judge pass covers a different subset of arms (RUNBOOK step 6's blinding requires a small,
fixed arm count per pass, so building up coverage across many arms means running several passes),
and an arm id appearing in more than one file is refused rather than silently resolved by
last-file-wins, since two passes rating the same arm would mean picking one pass's verdict over
another's for no principled reason. Every arm no `--judged` file covers — which, as of this
module's introduction, was all of them, since no judge pass had been run against the current keys
— lands in "measured, not yet judged" instead, with its numeric-anchor sub-item recall shown and
clearly labelled as such rather than silently upgraded to a rating.

**Comparability gate.** Two arms are only put in the same table if they come from runs sharing a
cohort key — corpus digest, keys digest, `score_arms.SCORER_VERSION`, `bench_report.
COST_MODEL_VERSION` — computed from `run.json`'s `frozen_refs`/`versions`. The reference cohort is
whichever cohort the most recent qualifying run belongs to; a run outside it is excluded and named
in `excluded_runs` rather than silently mixed in (the issue's blocker 3). Billing class (metered /
subscription-notional / batch, from each arm's own `notional_cost`/`backends`) is carried per row
instead: `render_markdown` prefixes a non-metered cost with `~` and foots which arms it covers,
mirroring `bench_report._cost_cell`/`_notional_note`'s existing convention.

**Picking which run measures an arm.** Among cohort-matching runs, the most recent run where the
arm actually completed (not `partial`, no hard failure) wins; only if every measurement of that
arm is partial does the most recent partial one get used, flagged. This is what keeps one run's
own failed attempt at an arm from being scored off a sibling run's leftover vault contents for
that same arm id (#656) — the fix generalizes past that one bug: whichever run *actually* produced
a clean measurement of an arm wins, regardless of which run this tool was pointed at.

Recall for the "measured, not yet judged" table is recomputed fresh via `score_arms.score()`
against the run's *archived* `artifacts/<vault>/extracted/*.json` (never the live `.vaults/`,
which reset on the next run of that arm) — so a rescoring pass changing `score_arms.py`'s scoring
logic can be re-run against every kept run without spending anything, same as `#591`'s rescore.

**Speed is real wall-clock, not summed per-call latency, and it stays in `--sort-by`'s choices.**
`bench_report._usage_totals`'s `wall_clock_s` (`max(end) - min(start)` across an arm's calls,
imported from `watchdog.cmd.usage._wall_span`) is what `speed_per_page_s` divides by pages —
summed latency overstates real elapsed time for any arm whose documents extracted concurrently,
which every `extractor_sweep` arm does. Falls back to the older, summed `latency_s` only for a
run from before `end_ts` was recorded. The issue's blocker 4 asked for speed to be excluded from
ranking entirely until `#555` (the sectioning-count formula being over-conservative in a
model-dependent way) lands — `#555` is closed on GitHub but the underlying formula is not
actually fixed (a later comment on it measured the spread still widening), so the confound this
blocker named is still real: a model sectioned more aggressively than another by the same
possibly-wrong formula pays for it in wall-clock too, on top of the concurrency-inflation this
fix corrects. Tom's call, given real elapsed time matters for planning a run regardless: report
it and keep it rankable, with a standing caveat (`render_markdown`'s footer) naming which arms
were sectioned at all, rather than hide the number.

**Reliability is a column, not just the `(partial)` flag.** Sourced from `run.json`'s own
fields — documents extracted vs. the corpus total, `coverage_gaps`, retries — never inferred from
a recall figure (the issue's blocker 2). This does not cover everything the issue asked for:
truncation and the empty-extraction guard firing are not yet instrumented per-arm in `run.json`,
so a clean-looking reliability cell does not rule either out. What's shown is what's cheaply
available today, not the full picture.
"""
import argparse
import glob
import json
import os

import score_arms

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_run(run_dir):
    try:
        return json.loads(open(os.path.join(run_dir, "run.json"), encoding="utf-8").read())
    except (OSError, json.JSONDecodeError):
        return None


def cohort_key(run):
    """(corpus digest, keys digest, scorer version, cost_model version) — two arms are only
    comparable when every one of these four matches. `None` in any slot means the run predates
    #551's instrumentation (no digests recorded) and can never be a reference cohort, though it
    can still match one by coincidence if every field is somehow present."""
    fr = run.get("frozen_refs") or {}
    v = run.get("versions") or {}
    return (fr.get("corpus_digest"), fr.get("keys_digest"), v.get("scorer"), v.get("cost_model"))


def _billing_class(arm):
    if arm.get("notional_cost"):
        return "notional"
    if "claude-batch" in (arm.get("backends") or ""):
        return "batch"
    return "metered"


def _is_bad(arm):
    """An arm measurement that answers a smaller or different question than a clean run — never
    preferred over a clean one for the same arm id, regardless of recency (#656)."""
    return bool(arm.get("partial")) or not arm.get("ok", True)


def _reliability_str(arm):
    """`"6/6"`, or `"6/6 (2 gaps, 1 retry)"` when anything is worth flagging — sourced entirely
    from `run.json`'s own fields (never inferred from a recall figure, the issue's blocker 2).
    `"—"` when the arm has no document counts at all (a hard failure before its first call)."""
    total, done = arm.get("documents_total"), arm.get("documents_extracted")
    docs = f"{done}/{total}" if total is not None and done is not None else "—"
    extras = []
    gaps = arm.get("coverage_gaps") or 0
    if gaps:
        extras.append(f"{gaps} gap" + ("s" if gaps != 1 else ""))
    retries = arm.get("retries") or 0
    if retries:
        extras.append(f"{retries} retry" if retries == 1 else f"{retries} retries")
    doc_errors = len(arm.get("doc_errors") or [])
    if doc_errors:
        extras.append(f"{doc_errors} doc error" + ("s" if doc_errors != 1 else ""))
    return f"{docs} ({', '.join(extras)})" if extras else docs


def _reference_cohort(named_runs):
    """The cohort of the most recent run (by run_id, which sorts chronologically) that has
    complete digests and at least one extractor arm. `None` if nothing qualifies."""
    candidates = [(rid, run) for rid, run in named_runs
                 if all(v is not None for v in cohort_key(run))
                 and any(a.get("stage") == "extractor" for a in run.get("arms", []))]
    if not candidates:
        return None
    return cohort_key(max(candidates, key=lambda t: t[0])[1])


def _sub_item_recall(vault_path, keys_dir=None):
    """Numeric-anchor sub-item recall (anchors matched / anchors present) for one arm's vault —
    the aggregation RUNBOOK.md recommends over `score_arms.py`'s own binary-per-item default,
    which flatters cheap arms (2026-07-27 entry, `FINDINGS.md`). `keys_dir` overrides the real
    keys for testing, same parameter `score_arms.score` already exposes."""
    result = score_arms.score([vault_path], keys_dir=keys_dir)
    vb = os.path.basename(vault_path)
    facts_hit = facts_tot = mnm_hit = mnm_tot = 0
    for d in result["detail"]:
        cell = d["cells"].get(vb)
        if not cell or cell["hit"] in ("not_extracted", None):
            continue
        is_fact = d["qid"].split(":")[1].startswith("F")
        if is_fact:
            facts_hit += cell["hits"]
            facts_tot += cell["total"]
        else:
            mnm_hit += cell["hits"]
            mnm_tot += cell["total"]
    return {
        "facts": f"{facts_hit}/{facts_tot}" if facts_tot else None,
        "facts_pct": round(facts_hit / facts_tot * 100) if facts_tot else None,
        "must_not_miss": f"{mnm_hit}/{mnm_tot}" if mnm_tot else None,
        "must_not_miss_pct": round(mnm_hit / mnm_tot * 100) if mnm_tot else None,
    }


def build_index(run_dirs, judged_summary=None, keys_dir=None):
    """Pure aggregation (reads the given files, no printing). `judged_summary` is an already
    -parsed `qualitative/aggregate.py` summary.json (its `"summary"` key), or `None`. `keys_dir`
    overrides the real keys, for testing.

    Returns {"reference_cohort": {...} | None, "rated": [...], "measured_not_yet_judged": [...],
    "excluded_runs": [...]} — see the module docstring for what each list means."""
    named_runs = []
    for d in run_dirs:
        data = _load_run(d)
        if data is not None:
            named_runs.append((os.path.basename(os.path.normpath(d)), d, data))

    ref_cohort = _reference_cohort([(rid, data) for rid, _d, data in named_runs])

    best = {}
    excluded_runs = []
    for rid, d, data in named_runs:
        if cohort_key(data) != ref_cohort:
            if any(a.get("stage") == "extractor" for a in data.get("arms", [])):
                excluded_runs.append({"run_id": rid, "cohort": cohort_key(data)})
            continue
        for arm in data.get("arms", []):
            if arm.get("stage") != "extractor":
                continue
            aid = arm["arm_id"]
            candidate = (rid, d, arm)
            cur = best.get(aid)
            if cur is None:
                best[aid] = candidate
                continue
            cur_rid, _cur_d, cur_arm = cur
            cur_bad, new_bad = _is_bad(cur_arm), _is_bad(arm)
            if cur_bad and not new_bad:
                best[aid] = candidate
            elif new_bad and not cur_bad:
                continue
            elif rid > cur_rid:
                best[aid] = candidate

    judged_summary = judged_summary or {}
    rated, unjudged = [], []
    for aid in sorted(best):
        rid, d, arm = best[aid]
        pages = arm.get("pages_extracted") or 0
        cost = arm.get("cost_usd")
        # Real elapsed time first; only a run from before `end_ts` was recorded falls back to
        # summed per-call latency, which overstates it for a concurrently-run arm.
        elapsed = arm.get("wall_clock_s")
        if elapsed is None:
            elapsed = arm.get("latency_s")
        row = {
            "arm_id": aid, "model": arm.get("model"), "effort": arm.get("effort"),
            "cost_per_page": round(cost / pages, 5) if pages and cost is not None else None,
            "speed_per_page_s": round(elapsed / pages, 2) if pages and elapsed is not None else None,
            "billing_class": _billing_class(arm),
            "sectioned_calls": arm.get("sectioned_calls") or 0,
            "reliability": _reliability_str(arm),
            "source_run": rid, "partial": _is_bad(arm),
        }
        if aid in judged_summary:
            j = judged_summary[aid]
            row.update(
                facts=f"{j['facts']['hit']}/{j['facts']['total']}", facts_pct=j["facts"]["pct"],
                must_not_miss=f"{j['must_not_miss']['hit']}/{j['must_not_miss']['total']}",
                must_not_miss_pct=j["must_not_miss"]["pct"], judged=True)
            rated.append(row)
        else:
            vault_path = os.path.join(d, "artifacts", arm.get("vault") or aid)
            row.update(_sub_item_recall(vault_path, keys_dir=keys_dir), judged=False)
            unjudged.append(row)

    return {
        "reference_cohort": ({"corpus_digest": ref_cohort[0], "keys_digest": ref_cohort[1],
                              "scorer_version": ref_cohort[2], "cost_model_version": ref_cohort[3]}
                             if ref_cohort else None),
        "rated": rated,
        "measured_not_yet_judged": unjudged,
        "excluded_runs": excluded_runs,
    }


_SORT_KEYS = {
    "must_not_miss": (lambda r: r.get("must_not_miss_pct"), True),
    "facts": (lambda r: r.get("facts_pct"), True),
    "cost": (lambda r: r.get("cost_per_page"), False),
    "speed": (lambda r: r.get("speed_per_page_s"), False),
}


def _sorted_rows(rows, sort_by):
    """`must_not_miss`/`facts` sort highest-first, `cost`/`speed` sort lowest-first; `None`
    (unmeasurable — a partial arm with nothing scorable, or no pages) always sorts last either
    way, rather than floating to the top of an ascending sort or requiring `None < None`, which
    raises."""
    key_fn, want_desc = _SORT_KEYS[sort_by]

    def sort_key(r):
        v = key_fn(r)
        if v is None:
            return (True, 0)
        return (False, -v if want_desc else v)

    return sorted(rows, key=sort_key)


def _row_line(r):
    cost = "—" if r["cost_per_page"] is None else f"${r['cost_per_page']:.4f}"
    if r["billing_class"] != "metered":
        cost = f"~{cost}"
    speed = "—" if r["speed_per_page_s"] is None else f"{r['speed_per_page_s']:.1f}s"
    if r["sectioned_calls"]:
        speed += "\\*"
    facts = f"{r['facts_pct']}% ({r['facts']})" if r.get("facts_pct") is not None else "—"
    mnm = (f"{r['must_not_miss_pct']}% ({r['must_not_miss']})"
          if r.get("must_not_miss_pct") is not None else "—")
    flag = " (partial)" if r["partial"] else ""
    return (f"| `{r['arm_id']}`{flag} | {r['model'] or '—'} | {r['effort'] or '—'} | {facts} | "
           f"{mnm} | {cost}/page | {speed}/page | {r['reliability']} |")


def render_markdown(index, sort_by="must_not_miss"):
    lines = ["## Extraction model index", ""]
    rc = index["reference_cohort"]
    if rc:
        lines.append(f"Reference cohort: corpus `{rc['corpus_digest'][:12]}`, "
                    f"keys `{rc['keys_digest'][:12]}`, scorer v{rc['scorer_version']}, "
                    f"cost model v{rc['cost_model_version']}.")
    else:
        lines.append("No run with complete provenance found — nothing to index.")
    lines.append("")
    header = ["| Arm | Model | Effort | Main facts | Easy to miss | Cost | Speed | Reliability |",
              "|---|---|---|---|---|---|---|---|"]

    lines.append("### Rated (judged)")
    lines.append("")
    if index["rated"]:
        lines += header + [_row_line(r) for r in _sorted_rows(index["rated"], sort_by)]
    else:
        lines.append("*No arm has a judge-pass verdict against the current keys yet — "
                     "see \"measured, not yet judged\" below (RUNBOOK.md step 6).*")
    lines.append("")

    lines.append("### Measured, not yet judged")
    lines.append("")
    lines.append("Numeric-anchor sub-item recall only (~1/3 of key items) — not a substitute for "
                "a judge verdict, see the module docstring.")
    lines.append("")
    if index["measured_not_yet_judged"]:
        lines += header + [_row_line(r) for r in _sorted_rows(index["measured_not_yet_judged"], sort_by)]
    else:
        lines.append("*Nothing measured in the reference cohort.*")
    lines.append("")

    all_rows = index["rated"] + index["measured_not_yet_judged"]
    non_metered = {r["arm_id"] for r in all_rows if r["billing_class"] != "metered"}
    if non_metered:
        lines += ["> Costs marked `~` (" + ", ".join(f"`{a}`" for a in sorted(non_metered)) +
                 ") are subscription-notional or batch-rate figures, not directly comparable to "
                 "a metered arm's real per-token spend.", ""]

    sectioned = {r["arm_id"] for r in all_rows if r["sectioned_calls"]}
    if sectioned:
        lines += ["> Speed marked `*` (" + ", ".join(f"`{a}`" for a in sorted(sectioned)) +
                 ") split at least one document into multiple calls. Speed here is real "
                 "wall-clock, not summed per-call latency, but the sectioning-count formula is "
                 "over-conservative in a model-dependent way (#555) — a speed comparison "
                 "involving one of these arms may partly reflect that, not just the model.", ""]

    if index["excluded_runs"]:
        lines.append("### Excluded runs (different cohort)")
        lines.append("")
        for e in index["excluded_runs"]:
            lines.append(f"- `{e['run_id']}` — {e['cohort']}")
        lines.append("")

    return "\n".join(lines)


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("run_dirs", nargs="*",
                    help="run directories to index (default: every kept run under benchmarks/runs/)")
    ap.add_argument("--out", default=os.path.join(_HERE, "index"),
                    help="directory to write index.json/index.md into (default: benchmarks/index/)")
    ap.add_argument("--sort-by", choices=sorted(_SORT_KEYS), default="must_not_miss")
    ap.add_argument("--judged", action="append", default=[],
                    help="a qualitative/aggregate.py summary.json to rate arms from; repeatable "
                         "for several judge passes covering different arms. Arms none of them "
                         "cover are listed as measured, not yet judged")
    return ap.parse_args(argv)


def _load_judged_summaries(paths):
    """Merges one or more `qualitative/aggregate.py` summary.json files' `"summary"` dicts.
    Refuses (rather than picking one silently) if the same arm id is rated by more than one
    file — that arm needs one of the passes re-run to cover a different set, not an arbitrary
    tie-break."""
    merged = {}
    for path in paths:
        summary = (json.loads(open(path, encoding="utf-8").read()) or {}).get("summary", {})
        overlap = set(merged) & set(summary)
        if overlap:
            raise SystemExit(f"{path}: arm(s) {sorted(overlap)} already rated by an earlier "
                             f"--judged file — a judge pass must not double-rate an arm")
        merged.update(summary)
    return merged


def main(argv=None):
    args = _parse_args(argv)
    run_dirs = args.run_dirs or sorted(glob.glob(os.path.join(_HERE, "runs", "*")))
    judged_summary = _load_judged_summaries(args.judged)
    index = build_index(run_dirs, judged_summary=judged_summary)
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2)
        f.write("\n")
    md = render_markdown(index, sort_by=args.sort_by)
    with open(os.path.join(args.out, "index.md"), "w", encoding="utf-8") as f:
        f.write(md)
    print(md)


if __name__ == "__main__":
    main()
