# Post-processing in the Add documents estimate — measured (2026-11-02, #820 step 1)

How far the Add documents estimate (`ingest_setup.cost_estimate`) is from what post-processing
billed, measured on the usage records already on one Mac. No model call was made.

To run it again: `benchmarks/postprocessing_estimate_gap.py` (its docstring has the method). It
reads every registered investigation's `usage-*.json`, and the telemetry store for investigations
whose usage files are gone; it prints aggregates only.

## Read this first

- **Very little data.** 8 investigations had records. 6 pay-as-you-go runs extracted documents;
  3 more (on a subscription) recorded no tokens and are left out; 2 runs did neither. Only **one**
  run had earlier history, so only one blended estimate can be replayed. No investigation on this
  Mac is large, so the case #820 worries about most (a small batch added to a large registry) is
  not measured at all.
- **Costs are Watchdog's own pricing,** not the bill (`reconcile_cost.py` compares the two).
- **"Post-processing"** is every call whose task is in `orchestrate.FINALIZE_TASKS`. Everything
  else in a run (extraction, classification, request dedup) is counted as extraction, as the
  estimate counts it.
- The replay uses the run's recorded document estimate (`est_input_tokens`, the chars/4 of the
  text extracted) as the queue's `est_tokens`; the two are the same quantity, but the recorded one
  covers only documents that extracted.

## Findings

1. **Most runs got no dollar figure.** 5 of the 6 runs were an investigation's first, where the
   estimate shows tokens only. In the 3 of them that post-processed, it was 21% to 35% of the
   run's cost (median 28%); the other 2 did not post-process in that run.

2. **Post-processing does not follow the batch's document tokens.** Across the 4 runs that
   post-processed: $0.25 to $3.91 per million estimated document tokens (a 15x spread), and
   $0.003 to $0.055 per document (21x). A single blended $/token rate cannot carry it.

3. **The one replayable estimate was about 10x low, and not mainly because of post-processing.**
   It was built from one earlier run. Post-processing came in at 15.8x what the blended rate
   implied for it; everything else at 9.2x. That batch's documents were short (a few thousand
   estimated tokens for 5 documents), so the fixed part of every call (instructions, record skill,
   brief, the post-processing prompts) dwarfed the document text the estimate scales from.
   Across all 6 runs, extraction calls really sent 1.5x to 10.7x the document estimate (median
   2.2x); the calibration from an earlier run carries that factor only when the batches are alike.

4. **Three tasks are nearly all of post-processing's cost.** Pooled over the 4 runs: entity
   synthesis 51%, reconcile 26%, briefing 21%. The housekeeping passes (dates, relationship labels,
   request sources) and the timeline passes together came to about 2%.

## What this means for #820

Pricing post-processing on its own line is supported (findings 1, 2, 4), but the larger error in
the one measurable case is the token base: chars/4 of the document text, scaled by one earlier
run's ratio, misses the fixed per-call prompt for both stages. A design that prices whole prompts
per call (as `ingest_setup.planned_prompts` already plans extraction, D370) addresses both. The
measurement should be repeated once investigations with several runs and a larger registry exist.
