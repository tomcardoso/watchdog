# Benchmarks

Watchdog's ingest pipeline calls a model three times per document — once to classify it, once to
extract its facts, once to write up what changed. Which model and effort level each of those
stages uses by default is not a guess: it comes from running the same fixed set of real
court-and-financial documents through every candidate combination and comparing what came back
against a frozen answer key. This page is where those results live, in plain terms — what to
expect in time and cost, and what still needs to be measured.

## How the measurement works

A small, frozen set of real documents (six filings from a university's insolvency proceeding,
public record) gets processed once under each model/effort combination being compared. Each run
is scored against an answer key drafted from the source documents themselves, and the
comparison is *relative* — it tells you whether a cheaper model holds onto most of a more
expensive one's recall, not an absolute accuracy number you could quote on its own. The tooling
behind this (`benchmarks/run_benchmark.py`) is maintainer-only — it is not part of the
`watchdog` command a working investigator runs day to day.

## What to expect

Figures below come from the most recent completed run; see `benchmarks/<date>/docs-summary.md`
for the exact numbers and `benchmarks/FINDINGS.md` for the fuller narrative behind them.

*(No run has been scored end to end under the current tooling yet — this section fills in once
one has. Historical, pre-automation figures are in `benchmarks/FINDINGS.md`.)*

## Roadmap

A few things this benchmark doesn't cover yet:

- **Classifying document types.** The current document set only spans two of Watchdog's record
  skills (bankruptcy filings and financial statements) — enough to confirm the classifier gets
  those two right, not enough to rank classifier models against each other. That needs a
  type-diverse set (a corporate filing, a real-estate document, a government report, a news
  clipping, and so on); see `benchmarks/classify-corpus/README.md` for what's needed to build it.
- **OpenAI models.** Left out of the current comparison while Watchdog's approach to talking to
  model providers is under review (issue #453) — no point freezing a comparison against model
  names that might not be how it's done shortly.
- **Running this automatically.** Right now a maintainer runs the benchmark by hand when a new
  model or effort combination is worth checking. Wiring it into a scheduled or on-demand GitHub
  Actions run is a reasonable next step, once it's clear how often that's actually useful.

## Further reading

- `benchmarks/FINDINGS.md` — the running narrative: what each round of measurement found, and
  what it implied for a default.
- `benchmarks/BENCHMARKING.md` — the full protocol `run_benchmark.py` automates, kept as
  reference and as a manual fallback.
