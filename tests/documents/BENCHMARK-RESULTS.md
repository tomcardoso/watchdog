# Extraction benchmark results (#361 / #215) — running log

Living document. Updated as each arm in `BENCHMARKING.md`'s Step 4/5 tables completes. Numbers
below are from `score_arms.py` (deterministic, numeric-anchor-only — ranks arms against each
other, not an absolute recall measure; see `keys/README.md`) plus each vault's own usage
telemetry and coverage-gap field. No judge-model pass has run yet — everything here is the free,
offline slice.

## Extractor sweep (Step 4)

| Arm | Facts | must_not_miss | Cost (6 docs) | Latency (summed) | Retries / sectioning | Coverage gap |
|---|---|---|---|---|---|---|
| `bench-ex2-ds-flash` | 82% (32/39) | 71% (17/24) | $0.032 | 152s | 0 | none |
| `bench-ex2-ds-flash-think` | 79% (31/39) | 71% (17/24) | $0.014† | 239s | 1 (on the 5-page doc) | First Report, pp.15–34 (20/34 skipped) |
| `bench-ex2-ds-pro` | 92% (36/39) | 83% (20/24) | $0.119 | 438s | 1 (on the 70-page doc — expected) | none |
| `bench-ex2-ds-pro-think` | 90% (35/39) | 83% (20/24) | $0.143 | 1,143s | the 36-page doc needed 5 calls (1 retry + 3 overlapping re-sections + a digest) | First Report, pp.17–34 (18/34 skipped) |
| `bench-ex2-sonnet-high` | — pending — | | | | | |
| `bench-ex2-sonnet-med` | — pending — | | | | | |
| `bench-ex2-haiku` | — pending — | | | | | |
| `bench-ex-opus-high` (optional) | — not run — | | | | | |

† Looks low relative to `ds-flash`, which is suspicious — DeepSeek's reasoning-token billing may
not be captured the same way in `cost_usd`. Sanity-check against DeepSeek's own dashboard before
citing this figure.

## Finalizer sweep (Step 5)

Not started — needs a `dig` at `deepseek:deepseek-v4-flash` into `bench-fn-base`, then a copy per
finalizer arm (see `BENCHMARKING.md` Step 5).

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
  `dig`/`bark` split shipped) have no `.watchdog/extracted/*.json` and do not score through the
  current `score_arms.py`. Re-run under `bench-ex2-*` names instead — see `BENCHMARKING.md`.

## Next

`watchdog dig --estimate` for `bench-ex2-sonnet-high`, `bench-ex2-sonnet-med`, `bench-ex2-haiku` —
pending Tom's go-ahead before the live runs (session token spend).
