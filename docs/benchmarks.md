# Benchmarks

Watchdog uses an AI model at three points: once to classify each document, once to extract its
facts, and once per batch to write up what changed. The model and effort level each step uses by
default isn't a guess. It's chosen by running the same fixed set of real documents through every
candidate combination and checking what came back against a frozen answer key. This page has
those results in plain terms: what each model found, what it cost, how long it took, and what is
still unmeasured.

## How the measurement works

A small, frozen set of real documents — six filings from a university's insolvency proceeding,
209 pages in all, public record — is processed once under each model and effort combination being
compared. Each run is scored against an answer key drafted from the source documents themselves.
The comparison is *relative*: it tells you whether a cheaper model holds onto most of a more
expensive one's recall (the share of the key's facts it found), not an absolute accuracy number
you could quote on its own. The tooling behind this (`benchmarks/run_benchmark.py`) is
maintainer-only; it isn't part of the app.

None of the numbers on this page need to reach 100% for Watchdog to be useful. Its job is to
surface facts, entities and connections for a journalist to check against the source, not to hand
over a courtroom-ready read on the first pass. A cheap model that reliably catches most of what's
on a page is doing real work, and that is what these numbers compare: which option catches the
most for the price. The answer moves as models improve, so the defaults move with it.

The answer key has two kinds of item:

- **Main facts** are a document's material content.
- **Must not miss** are the items where a miss would be a failure at the level of the story, not
  just a thinner note: often a clause in standard-form wording or a line under a table, the kind
  of detail a routine read loses first.

Each item is scored one of two ways:

- **Judged.** A model reads each extraction against the answer key and decides whether the item
  was captured. In the latest run the judge was Claude Opus, which was not one of the models
  being tested, and it saw the extractions without knowing which model wrote them. This is the
  score the tables use unless they say otherwise.
- **Numeric.** Code checks whether an item's figure (a dollar amount, a date, a percentage)
  appears in the extraction. It is free and instant, but only about a third of the key's items have
  a figure to check, and a model that states figures without the claim around them scores well on
  it. It has ranked a better model below a worse one before, so no ranking here rests on it alone.

## Today's models

This run, in October 2026, is the one behind the current defaults. Each provider's current model
read the six documents at low, medium and high effort, every one with the same short
investigation brief written as open questions, as real use always has one. Claude Haiku 5.5 and
Sonnet 5.5, OpenAI's GPT-6 Luna and Google's Gemini 3.8 Flash ran at all three levels, and Gemini
3.5 Flash-Lite at low. The DeepSeek models did not complete, so DeepSeek is not in it.

| Model and effort | Main facts | Must not miss | Cost per page | Time per page |
|---|---|---|---|---|
| Claude Haiku 5.5, high | 74% | 72% | $0.0009 | 4.1 s |
| Claude Sonnet 5.5, high | 67% | 65% | $0.0049 | 0.6 s |
| GPT-6 Luna, high | 55% | 44% | $0.0003 | 1.0 s |
| GPT-6 Luna, medium | 52% | 43% | $0.0002 | 0.5 s |
| Claude Sonnet 5.5, medium | 45% | 47% | $0.0035 | 0.4 s |
| Claude Sonnet 5.5, low | 43% | 38% | $0.0036 | 0.4 s |
| GPT-6 Luna, low | 41% | 33% | $0.0002 | 0.4 s |
| Gemini 3.8 Flash, high | 41% | 44% | $0.0028\* | 1.2 s |
| Claude Haiku 5.5, medium | 39% | 39% | $0.0003 | 0.5 s |
| Gemini 3.8 Flash, medium† | 32% | 39% | $0.0014\* | 0.5 s |
| Claude Haiku 5.5, low† | 30% | 32% | $0.0002 | 0.3 s |
| Gemini 3.8 Flash, low† | 27% | 33% | $0.0014\* | 0.4 s |
| Gemini 3.5 Flash-Lite, low‡ | 17% | 17% | $0.0011 | 0.7 s |

\* At Google's promotional price, which ends on 31 December 2026. From then on, Gemini 3.8 Flash
costs twice as much: about $0.0055 a page at high effort, more than Sonnet 5.5.
† Stopped citing one 34-page document after page 16. The scores count what these settings missed
there.
‡ Judged in a separate pass from the others.

Costs are what each setting would cost on a metered API key, at list prices. On a Claude
subscription the Claude rows are not billed per page, but they count against the subscription's
usage limits.

What the run shows:

- **Haiku 5.5 at high effort found the most**, on every measure, at about a fifth of Sonnet 5.5's
  cost a page. It was also the slowest setting by far, about four seconds a page against 0.6 for
  Sonnet. That is why extraction defaults to Haiku at `high`; Sonnet 5.5 at `high` is the choice
  when speed matters more than recall.
- **Effort matters differently for each model.** Haiku barely moved from low to medium and jumped
  at high. Sonnet was close to flat from low to medium and also gained most at high. GPT-6 Luna
  gained most of its recall by medium, and adds little at high for twice the time. Gemini 3.8
  Flash rose with effort, but doubles its cost from medium to high.
- **GPT-6 Luna is the cheapest setting that holds up.** At medium it found more of the main facts
  than Haiku or Sonnet at medium, for about a twentieth of Sonnet's cost a page, though it found
  less than either at high. It needs its own OpenAI key.
- **The two scores can disagree.** Gemini 3.5 Flash-Lite found 49% of the main facts' figures on
  the numeric check, among the best in the run, but 17% when judged: it tends to state figures
  without the claim around them.

**Read every gap as a lead, not a result.** Each setting ran once, and single runs of one setting
have differed by up to three times. Before the default is treated as settled, Haiku 5.5 at
medium and high and Sonnet 5.5 at high will each be run three times, to confirm the gaps between
them hold. [Controlling cost](configuration.md#controlling-cost) turns these
results into settings.

## Earlier results

The two tables below were measured in August and September 2026, on models Watchdog no longer
offers (Claude Sonnet 4.6, Sonnet 5 and Haiku 4.5, GPT-5.4, Gemini 3.7, the first DeepSeek V4
releases), and without an investigation brief. Their numbers are not comparable with the run
above. They are kept as the record of how the earlier defaults were chosen.

### Judged

| Model | Main facts | Must not miss | Cost per page |
|---|---|---|---|
| Claude Sonnet 4.6, high effort | 81% | 78% | $0.047 |
| DeepSeek V4 Pro (thinking), high effort | 71% | 64% | $0.002 |
| GPT-5.4 Nano, high effort | 73% | 62% | $0.001 |
| GPT-5.6 Luna, high effort | 69% | 62% | $0.001 |
| DeepSeek V4 Flash | 69% | 55% | $0.0003 |
| Claude Haiku 4.5 | 60% | 50% | $0.002 |
| Claude Sonnet 4.6, low effort | 56% | 49% | $0.006 |
| Gemini 3.7 Flash, high effort | 39% | 46% | $0.002 |

Sonnet 4.6 at high effort found the most and cost the most by a wide margin, from about 8 times
(Sonnet at low effort) to about 155 times (DeepSeek V4 Flash) what the other rows cost a page.
Turning Sonnet's effort down lost more than switching models did. Gemini 3.7 Flash scored near the
middle of the field on the numeric check and last when judged, an early example of the two scores
disagreeing.

### The wider sweep

Judging every setting was too slow at the time, so a wider sweep of every model and effort level
then tracked was scored with the numeric check only. Treat its order as a rough first cut: this
table ranked GPT-5.6 Luna at high effort below Sonnet 4.6 at low effort, and the judged table above
found the reverse.

| Model | Main facts (numeric) | Must not miss (numeric) | Cost (whole 209-page set) |
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
| Claude Sonnet 5, high effort\* | 49% | 62% | $1.398 |
| Claude Sonnet 5, medium effort\* | 47% | 49% | $1.026 |
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

\*The cost figures for these two rows came from a second run that was writing to the same results
at the same time; the recall figures are their own.

## Not measured yet

- **Repeat runs.** Every figure in [Today's models](#todays-models) is one run. The repeat of the
  settings closest to the default comes first.
- **DeepSeek's current models.** Both failed to complete in the latest run, so the newest DeepSeek
  figures are the earlier ones above, on models since replaced.
- **The finishing step.** The run above measured extraction only. Which model writes the best
  summaries, catches the most contradictions and merges entities most accurately has not been
  compared; Haiku 5.5 is the default there by judgement, not measurement.
- **Classifying document types.** The six documents span only two record skills (bankruptcy
  filings and financial statements), enough to confirm the classifier gets those two right, not
  enough to rank classifier models against each other. That needs a set of documents of many
  types; see `benchmarks/corpora/classify/README.md`.
- **Whether the verification pass earns its keep.** The optional second read
  ([Verify extraction](configuration.md#the-verification-pass)) reliably adds facts. On the earlier
  models, at low effort it added mostly restatements for twice the cost, at medium it gave a real
  gain for 50% more, and at high the gain nearly vanished. That was one run of each, on models
  since removed, so the pass stays off by default.
- **Local models.** A model on your own computer has not been run through the benchmark at all;
  see [Local and self-hosted models](configuration.md#local-and-self-hosted-models).

## Further reading

These are in the project's code repository:

- `benchmarks/2026-10-18-frontier-results.md` — the full results of the run in
  [Today's models](#todays-models), with every score's counts and the run's caveats.
- `benchmarks/FINDINGS.md` — the running narrative: what each round of measurement found, and
  what it implied for a default.
- `benchmarks/BENCHMARKING.md` — the full protocol `run_benchmark.py` automates.
- `benchmarks/RUNBOOK.md` — the step-by-step procedure a maintainer follows to run a benchmark.
