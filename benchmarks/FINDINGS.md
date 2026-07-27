# Benchmark findings — running log

Hand-maintained. Updated by whoever reads a run's `REPORT.md` next (`benchmarks/<run-id>/`) and
has something worth writing down about what it means — the automated run produces the numbers,
this file is where a human says what they add up to. Migrated 2026-07-26 from the pre-#466
`tests/documents/BENCHMARK-RESULTS.md`, which this file and `benchmarks/<run-id>/REPORT.md`
together replace.

## Findings so far

**DeepSeek's "thinking" toggle doesn't pay for itself on this corpus.** Confirmed on both model
sizes, not a one-run fluke — `bench-ex2-ds-pro-think` reproduced the same anomaly
`bench-ex2-ds-flash-think` showed while it was still mid-run:

- **Coverage:** both thinking variants skip roughly the back half of *Laurentian First Report of
  the Monitor* (pp. 15–34 / 17–34 of 34). Both non-thinking variants have zero coverage gaps
  anywhere, including the dense 70-page annual report — the document #339 expected cheap
  conditions to degrade on first. Thinking mode is degrading coverage on an *easier* document
  instead.
- **Reliability:** flash-think needed a retry on the corpus's easiest document (5 pages). Pro-think
  needed the pipeline to fall back to sectioning a 36-page document — well under the size
  threshold that normally triggers that — into 5 separate calls.
- **Cost/latency:** pro-think costs +20% over pro and runs at 2.6× pro's summed latency, for
  materially the same fact recall (90% vs 92%) and identical must_not_miss (83%).
- **Recall itself is roughly a wash** — thinking neither clearly helps nor hurts on the numeric
  slice. The case against it is entirely about coverage, reliability, and cost.

Implication for #361's DeepSeek decision: unlikely to be worth documenting both a thinking and
non-thinking DeepSeek recommendation. Pending the judge pass on the ~⅔ of key items with no
numeric anchor before that's final.

## Corrections logged along the way

- The original `bench-ex-sonnet-high`/`bench-ex-sonnet-med` vaults (run 2026-07-15, before #403's
  `dig`/`bark` split shipped) had no `.watchdog/extracted/*.json` and did not score through
  `score_arms.py`. Re-run under `bench-ex2-*` names instead.
- 2026-07-26 (#466): the whole benchmark harness moved from a hand-run protocol
  (`BENCHMARKING.md` + one-off scripts) to `run_benchmark.py`, driven by `benchmark.yaml`. Arm
  names changed again as part of that move (`bench-ex2-*` → `bench-ex3-*`) — treat any
  `bench-ex2-*`/`bench-fn-*` figures above as historical, not directly comparable to a fresh
  `bench-ex3-*`/`bench-fn-*` run without re-checking the config the new run actually used
  (`benchmarks/<run-id>/config.yaml`).
