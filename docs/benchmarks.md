# Benchmarks

Watchdog's ingest pipeline calls a model three times per document — once to classify it, once to
extract its facts, once to write up what changed. The model and effort level each stage uses by
default isn't a guess. It's chosen by running the same fixed set of real documents through every
candidate combination and checking what came back against a frozen answer key. This page has
those results in plain terms: what to expect in time and cost, and what's still unmeasured.

## How the measurement works

A small, frozen set of real documents — six filings from a university's insolvency proceeding,
public record — is processed once under each model/effort combination being compared. Each run
is scored against an answer key drafted from the source documents themselves. The comparison is
*relative*: it tells you whether a cheaper model holds onto most of a more expensive one's
recall, not an absolute accuracy number you could quote on its own. The tooling behind this
(`benchmarks/run_benchmark.py`) is maintainer-only — it isn't part of the `watchdog` command a
working investigator runs day to day.

## What to expect

None of the numbers below need to reach 100% for Watchdog to be useful. Its job is to surface facts, entities, and connections for a journalist to check against the source — not to hand over a courtroom-ready read on the first pass. A cheap model that reliably catches most of what's on a page is doing real work, and that's what these numbers are actually comparing: which cheap option catches the most for the price, not who clears some accuracy bar first. It also means the recommendation these numbers lead to isn't fixed — cheap models keep improving, and it will move as the benchmark catches up to new ones.

Recall below is the *judged* kind: a person or a model checks each extraction against the
source document (RUNBOOK.md step 6), rather than just checking whether a figure happens to
match. That mechanical, figure-matching check is faster and free, but it misses roughly
two-thirds of what a document actually needs captured — enough that it's ranked a genuinely
better model below a worse one before (see [the wider sweep](#the-wider-sweep) below). "Main
facts" is a document's material content. "Easy to miss" is deliberately the buried, easy-to-skip
detail a routine read tends to lose first — a clause in standard-form wording, a line under a
table.

| Model | Main facts | Easy to miss | Cost per page |
|---|---|---|---|
| Claude Sonnet 4.6, high effort | 81% | 78% | $0.047 |
| DeepSeek V4 Pro (thinking), high effort | 71% | 64% | $0.002 |
| GPT-5.4 Nano, high effort | 73% | 62% | $0.001 |
| GPT-5.6 Luna, high effort | 69% | 62% | $0.001 |
| DeepSeek V4 Flash | 69% | 55% | $0.0003 |
| Claude Haiku 4.5 | 60% | 50% | $0.002 |
| Claude Sonnet 4.6, low effort | 56% | 49% | $0.006 |
| Gemini 3.7 Flash, high effort | 39% | 46% | $0.002 |

Sonnet at high effort recovers the most, and costs the most by a wide margin — on a page-for-page
basis it runs from roughly 8 times (Sonnet at low effort) to roughly 155 times (DeepSeek V4 Flash)
what the other rows here cost.

Turning Sonnet's own effort down loses more than switching models does: every row above Sonnet at
low effort both outrecalls it and costs less. DeepSeek V4 Flash is the standout on value — recall
close to the strongest non-Sonnet rows at a small fraction of any other row's price.

Gemini's row is worth a second look before reading too much into it: on a faster, cruder check
this same configuration scored close to the middle of the field, and only the slower check
surfaced how far it actually trails here — a reminder that a model's rank can depend on which
check produced it.

This isn't the full field — every model here is at one particular effort level, checked the slow
and reliable way. This table alone isn't a ranking to act on by itself; it's what the check has
actually found for each configuration measured, no more. [Controlling cost](configuration.md#controlling-cost)
is where these findings turn into an actual pick. `benchmarks/FINDINGS.md` has the fuller
narrative behind it: all six documents, the hand-checks that verify the judge, and what's true
only of this corpus.

## The wider sweep

Judging takes a person or a model reading closely, so only a handful of arms get it at a time. A
much wider sweep exists underneath the table above: every Claude, OpenAI, Gemini, and DeepSeek
model and effort tier this project tracks, scored the fast, mechanical way instead — checking
whether a fact's own numeric anchor (a dollar figure, a date, a percentage) shows up in the
extraction, rather than reading the extraction's prose. That check only reaches about a third of
what a document needs captured, so treat rank here as a rough first cut, not a verdict — the
table above exists because this one ranked `gpt-luna-high` below `sonnet-4.6-low` on this same
corpus, and a full judge read found `gpt-luna-high` actually won.

| Model | Main facts (numeric) | Easy to miss (numeric) | Cost (full 209-page corpus) |
|---|---|---|---|
| Claude Sonnet 4.6, high effort | 84% | 93% | $9.710 |
| DeepSeek V4 Pro (thinking), high effort | 70% | 68% | $0.414 |
| Claude Sonnet 4.6, low effort | 69% | 82% | $1.161 |
| DeepSeek V4 Flash | 68% | 61% | $0.072 |
| GPT-5.4 Nano, high effort | 61% | 66% | $0.270 |
| DeepSeek V4 Flash (thinking), high effort | 60% | 58% | $0.150 |
| Claude Haiku 4.5 | 58% | 59% | $0.418 |
| Claude Sonnet 4.6, medium effort | 58% | 79% | $1.472 |
| DeepSeek V4 Pro | 58% | 59% | $0.212 |
| GPT-5.6 Luna, high effort | 55% | 74% | $0.150 |
| DeepSeek V4 Pro (thinking), low effort | 52% | 50% | $0.303 |
| Claude Sonnet 5, high effort* | 49% | 62% | $1.398 |
| Claude Sonnet 5, medium effort* | 47% | 49% | $1.026 |
| Gemini 3.7 Flash, high effort | 47% | 55% | $0.456 |
| Claude Sonnet 5, low effort | 45% | 46% | $0.888 |
| GPT-5.4 Mini, low effort | 43% | 46% | $0.305 |
| GPT-5.6 Luna, medium effort | 40% | 42% | $0.081 |
| GPT-5.4 Nano, medium effort | 40% | 59% | $0.150 |
| GPT-5.4 Mini, medium effort | 40% | 42% | $1.005 |
| DeepSeek V4 Flash (thinking), low effort | 39% | 42% | $0.101 |
| GPT-5.4 Mini, high effort | 35% | 43% | $2.053 |
| GPT-5.6 Luna, low effort | 34% | 39% | $0.069 |
| Gemini 3.7 Flash, low effort | 34% | 38% | $0.279 |
| Gemini 3.7 Flash, medium effort | 34% | 47% | $0.292 |
| GPT-5.4 Nano, low effort | 32% | 42% | $0.090 |
| Gemini 3.5 Flash Lite | 18% | 22% | $0.105 |

\*This run's own telemetry for these two rows was contaminated by a second, concurrent run
writing into the same shared results — the recall figures are real, but the cost figures are
pulled from that other run instead. Treat Sonnet 5's ranking here as the least settled row in
this table until a clean re-run confirms it.

From this same run: `sonnet-4.6-high` is the clear leader on the numeric check too, but it's also
the priciest row by a wide margin. Three rows cluster well below it at a fraction of the cost —
`ds-pro-think-high`, `sonnet-4.6-low`, and `ds-flash` — with `ds-flash` holding onto 81% of
`sonnet-4.6-high`'s recall for about 1/135th the price. None of this settles a production
default on its own; it's `benchmarks/FINDINGS.md`'s 2026-08-31 sweep (run `2026-08-31-1645`),
reproduced here so it doesn't live only in a maintainer's local, not-checked-in
`benchmarks/index/index.md`.

## Roadmap

A few things this benchmark doesn't cover yet:

- **Classifying document types.** The current document set only spans two of Watchdog's record
  skills (bankruptcy filings and financial statements) — enough to confirm the classifier gets
  those two right, not enough to rank classifier models against each other. That needs a
  type-diverse set (a corporate filing, a real-estate document, a government report, a news
  clipping, and so on); see `benchmarks/classify-corpus/README.md` for what's needed to build it.
- **Judging the other effort levels.** [The wider sweep](#the-wider-sweep) above already has
  numeric-check figures for most models' lower, cheaper tiers — what's still missing is a judge
  pass confirming those figures rank the same way the top table's do.
- **Whether the verification pass earns its keep.** The optional second read
  (`verify_extraction`, see [Configuration](configuration.md#the-verification-pass)) reliably adds
  facts, but effort changes whether those facts are worth the extra cost:
  - **Low** — no recall gain worth 2x the cost; the added facts were mostly restatements.
  - **Medium** — a real, sizeable recall gain for 50% more cost; a case worth making.
  - **High** — the gain narrows to nearly nothing, since the model already catches most of it.

  The pass stays off by default — this is one run of each arm, not a settled number
  (`benchmarks/FINDINGS.md`, 2026-09-03).
- **Running this automatically.** Right now a maintainer runs the benchmark by hand when a new
  model or effort combination is worth checking. Wiring it into a scheduled or on-demand GitHub
  Actions run is a reasonable next step, once it's clear how often that's actually useful.

## Further reading

- `benchmarks/FINDINGS.md` — the running narrative: what each round of measurement found, and
  what it implied for a default.
- `benchmarks/BENCHMARKING.md` — the full protocol `run_benchmark.py` automates, kept as
  reference and as a manual fallback.
- `benchmarks/RUNBOOK.md` — the step-by-step procedure a maintainer follows to run a benchmark
  pass and turn it into an index like [the wider sweep](#the-wider-sweep) above.
- `benchmarks/index/index.md` — every arm measured so far, judged and not-yet-judged alike, kept
  current; regenerated by a maintainer and not checked into the repository. The wider-sweep table
  above is a dated snapshot of it, not a live view.
