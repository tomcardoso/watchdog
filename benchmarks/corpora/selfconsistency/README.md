# Self-consistency subset (issue #562 follow-up)

Three of corpus-v1's six documents, **symlinked** into `../extract/` rather than copied — the
bytes are identical by construction, so a self-consistency run scores against the same frozen
answer keys as any other arm, with no second copy of a 3.7 MB PDF in the repo. `corpus.sha256`
is generated from the link targets and is verified by `run_benchmark.py` exactly like
`corpus-v1.sha256`; if the source files ever drift, this run refuses to start too.

## Why these three

This subset exists to measure whether repeated sampling recovers `must_not_miss` items, so it is
picked for **headroom**, not coverage. Pooling every archived arm (43 of them) and joining each
scorable `M*` item's hit/miss to its position, five items sit below 100% recall. Three of them
are here:

| item | doc | recall across all archived arms | what it is |
|---|---|---|---|
| `annual-financial-report-19-20:M9` | AFR 19-20 (36p) | **0.0%** (0/40) | budget-to-actual reconciliation table: revenues $160,983 = expenses $160,983, net (0.0) |
| `initial-order-2021-02-01:M1` | Initial Order (17p) | 53.5% (23/43) | the backsheet — counsel names, **rotated 90°** (OCR hazard H5) |
| `pension-order-2021-03-17:M2` | Pension Order (5p) | 53.5% (23/43) | last page of a 5-page order |

`M9` is the interesting one: nothing has ever extracted it, across every model, effort, and
backend tried. If five Luna samples don't recover it, resampling is not the lever for
table-shaped misses and the anchored-verifier route (Tier 0 anchors differenced against the
extracted facts) is the one worth building.

The other two documents in corpus-v1 that carry sub-100% items — the 70-page AFR 20-21 and the
47-page Pre-Filing Report — are deliberately left out to keep the run cheap. Add them if the
first result is ambiguous.

## Cost

~$0.15 per arm on `gpt-5.6-luna` at `low` effort (measured from `2026-08-03-0048`'s
`bench-ex-gpt-luna-low` usage records for these same three documents: 4 extract/extract-section
calls, 67K input, 13K output). Five arms ≈ **$0.73**.
