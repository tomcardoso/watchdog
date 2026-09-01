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

Every model and effort level that's been benchmarked gets one row in a maintainer-generated
index — cost per page, speed per page, and how much of a document's material facts it recovered —
so this section can eventually be "here's the table" rather than a narrative. That table isn't
ready to publish yet: recall needs a person (or a model acting as a judge) to check each
extraction against the source document, and that pass hasn't been run against the current answer
key. Until it has, every model measured so far sits in a "measured, not yet judged" list rather
than a rated one — see `benchmarks/FINDINGS.md` for the fuller narrative and caveats behind the
numbers that do exist.

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
- **Whether the verification pass earns its keep.** The optional second read
  (`verify_extraction`, see [Configuration](configuration.md#the-verification-pass)) reliably adds
  facts; what has not been measured is how many of those facts are worth a reporter's attention
  rather than restatements or trivia. Until that number exists, the pass stays off by default —
  a recall gain that quietly fills your fact lists with noise is not an improvement.
- **Running this automatically.** Right now a maintainer runs the benchmark by hand when a new
  model or effort combination is worth checking. Wiring it into a scheduled or on-demand GitHub
  Actions run is a reasonable next step, once it's clear how often that's actually useful.

## Further reading

- `benchmarks/FINDINGS.md` — the running narrative: what each round of measurement found, and
  what it implied for a default.
- `benchmarks/BENCHMARKING.md` — the full protocol `run_benchmark.py` automates, kept as
  reference and as a manual fallback.
- `benchmarks/RUNBOOK.md` — the step-by-step procedure a maintainer follows to run a benchmark
  pass and turn it into the index this page will eventually show.
