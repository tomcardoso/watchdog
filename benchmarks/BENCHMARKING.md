# Running the extraction benchmark (#361 / #215)

**Superseded day-to-day by `run_benchmark.py` (#466)**, which automates every step below against
`benchmark.yaml`'s arm matrix — cost preview, one go-ahead for the whole run, scoring, and a
written report under `benchmarks/<run-id>/`, instead of a human hand-running each step. This
document stays as the spec that tool automates, and as the manual fallback if `run_benchmark.py`
itself needs debugging.

A step-by-step guide to measuring the three ingest model stages against corpus-v1. Written to be
followed top to bottom. Nothing here spends money until Step 3, and every run gets a free cost
preview first.

**Do not run a live ingest (Steps 3, 4, 5, or 6) without Tom's explicit go-ahead.** These runs cost
real money and share his subscription session window — `--estimate` first, always, but the
estimate is a preview, not the ask.

## What we are deciding

Watchdog makes three kinds of model call during ingest. This benchmark picks the right-size default
for each, across Claude tiers and DeepSeek, plus the reasoning-effort knob where it applies.

| Stage | Flag | Default | Effort knob? | Cost shape |
|---|---|---|---|---|
| **Classifier** | `--classifier-model` | `haiku` | no | 1 call/doc, first 5 pages only — trivial |
| **Extractor** | `--extractor-model` / `--extractor-effort` | `sonnet` / `high` | yes | per page — **essentially the whole bill** |
| **Finalizer** | `--finalizer-model` / `--finalizer-effort` | `haiku` / `high` | yes | ~3 calls + reconciliation per ingest — near-constant |

Candidate models per stage: Claude `haiku` / `sonnet` / `opus`, and DeepSeek `deepseek:deepseek-v4-flash` /
`deepseek:deepseek-v4-pro`. Notes that matter:

- **Haiku has no effort knob** (`_EFFORT_UNSUPPORTED`) — `--extractor-effort` is ignored on Haiku.
  So a Haiku arm is a single point, not a high/medium/low sweep.
- **The classifier has no effort knob at all.**
- **DeepSeek's "effort" is its thinking toggle, not high/medium/low.** Test it by swapping the
  model id: `deepseek:deepseek-v4-flash` (thinking off) vs `deepseek:deepseek-v4-flash-thinking` (on), same for
  `-pro`. Tom's prior testing found non-thinking DeepSeek weak, so test both.

## What changed with #381 (read this before designing runs)

Extraction is now **stateless** — a pure function of the document, its skill, the brief, and its
sidecar. Entity resolution and cross-document contradiction detection moved to the finalizer's
**reconciliation pass**. Two consequences for how you run and read this:

1. **Concurrency and ingest order no longer affect extraction output.** Run at the default
   concurrency, any order. (Earlier drafts of the protocol mandated `--concurrency 1` and a fixed
   order — that is obsolete. See `keys/README.md`.)
2. **Metric attribution split by stage.** Material-fact recall and `must_not_miss` measure the
   **extractor**. Entity-duplicate count and the C1/C2 contradictions measure the **finalizer**,
   because reconciliation runs there. This is why the finalizer model is now worth benchmarking at
   all, and why you must not leave it at the Haiku default for the DeepSeek decision without
   deciding to.

## The method: isolate one stage at a time

Each stage is measured by varying it while holding the other two fixed at a known baseline.

- **Extractor sweep** — vary `--extractor-model` × `--extractor-effort`; the finalizer never runs
  at all (`watchdog dig` only — see Step 4), since the metrics this sweep scores live entirely in
  the staged extraction artifacts; the corpus's per-document sidecar pins (D120) keep the
  classifier out of the loop, no `--skill` flag needed. This is still where the money goes — it's
  just no longer paying for a finalizer it doesn't need too.
- **Finalizer sweep** — vary `--finalizer-model` × `--finalizer-effort`; hold the extractor fixed
  at **`deepseek:deepseek-v4-flash`**. Two reasons for the cheap fixed extractor: (a) it keeps the wasted
  re-extraction cheap — you cannot re-finalize a finished vault (#384), so each finalizer arm is its
  own full ingest; (b) a messier extraction gives reconciliation *more* to do (more duplicate
  candidates, more borderline contradictions), which discriminates finalizer models better than a
  near-perfect one would. Because extraction is deterministic (#381), that fixed input is identical
  across every finalizer arm, so the comparison is clean.
- **Classifier** — a **smoke test only**. corpus-v1 spans just two skills (`bankruptcy`,
  `financial-statements`), so it cannot *rank* classifier models. It can confirm the default gets
  these six right. Ranking classifier models needs a type-diverse corpus — separate future work.

Every condition is its own fresh vault. Never reuse a vault — carried-over entities contaminate
everything downstream of pre-flight.

## Step 0 — freeze the keys

The answer keys must be frozen before any condition runs, or a key edited mid-benchmark
invalidates every comparison made against the earlier version. Review them (see `keys/README.md`),
then:

```
cd benchmarks/keys && shasum -a 256 *.yaml > keys-v1.sha256
```

## Step 1 — verify the corpus

```
cd benchmarks/corpus && shasum -a 256 -c corpus-v1.sha256
```

All six must report `OK`. If any fails, stop — a single changed byte makes runs incomparable.

## Step 2 — check auth

You need Anthropic and DeepSeek credentials stored:

```
watchdog auth
```

Confirm both providers show as authenticated. (Gemini is only needed later, for the DeepSeek-arm
judge cross-check in scoring — not for the runs.)

## Step 3 — the per-condition recipe

Every condition starts the same way — fresh vault, all six documents, chew. Worked below for
`bench-ex-sonnet-high`; swap the vault name for every other row in Steps 4–6 (Step 5's
`bench-fn-base` included).

```
# 1. Fresh vault
watchdog new "bench-ex-sonnet-high"
cd <projects_dir>/bench-ex-sonnet-high

# 2. All six documents, one pass — each PDF's own .yml sidecar pins its record skill (D120),
#    so no --skill flag is needed and nothing gets classified under the wrong one. The glob
#    below grabs each PDF and its same-named sidecar together.
cp "<corpus>/Laurentian Pre-Filing Report of the Proposed Monitor.pdf"* _INCOMING/
cp "<corpus>/CV-21-00656040-00CL Laurentian U Initial Order 1 FEB 2021.pdf"* _INCOMING/
cp "<corpus>/Laurentian First Report of the Monitor.pdf"* _INCOMING/
cp "<corpus>/Pension Order Morawetz CJ- March 17 2021(as stamped by Court).PDF"* _INCOMING/
cp "<corpus>/Annual-Financial-Report-19-20.pdf"* _INCOMING/
cp "<corpus>/Annual-Financial-Report-20-21.pdf"* _INCOMING/
watchdog chew
```

Confirm the sidecars landed before trusting the run: `ls _INCOMING/*.yml` should list six files.
Classification is skipped for every document (each is pinned by its own sidecar), so this is one
`chew`/`dig` pass instead of the two the corpus used to need — see `keys/README.md` for why.

From here the two sweeps diverge (see Step 4 and Step 5): the extractor sweep only ever needs
`watchdog dig`, since `score_arms.py` reads the staged `.watchdog/extracted/*.json` artifacts
directly and those are written before any finalizer call. The finalizer sweep needs `watchdog
bark` too, but only once per vault — extract once, copy the vault, finalize each copy
differently.

**Capture, per vault, before touching the next one** (the terminal output is not recoverable — page
-coverage warnings and per-document digest-size lines print only there):

```
watchdog usage --all > usage.txt      # token/cost/latency per stage
cp -r .watchdog/extracted ../captures/<vault>-extracted/   # what score_arms.py reads
# if bark ran: cp -r briefings/ ../captures/<vault>-briefings/
#              cp -r .watchdog/registry ../captures/<vault>-registry/
# and save the scrollback of each run
```

## Step 4 — extractor sweep (the spend)

`watchdog dig` only — **no `bark`**. Everything this step scores (material-fact recall,
`must_not_miss`, coverage warnings) is in the staged extraction artifacts before any finalizer
call runs, so finalizing here would just spend money and time on a stage this step doesn't
measure. One vault each. Run `--estimate` before each.

```
# FREE cost preview — always run this first
watchdog dig --extractor-model sonnet --extractor-effort high --estimate

# The real run (same flags, no --estimate)
watchdog dig --extractor-model sonnet --extractor-effort high
```

| Vault | `--extractor-model` | `--extractor-effort` | Serves |
|---|---|---|---|
| `bench-ex-sonnet-high` | `sonnet` | `high` | baseline · #215 high arm |
| `bench-ex-sonnet-med` | `sonnet` | `medium` | #215 medium arm |
| `bench-ex-haiku` | `haiku` | — (ignored) | #361: Haiku as shipped default? |
| `bench-ex-opus-high` | `opus` | `high` | is Opus worth the premium? |
| `bench-ex-ds-flash` | `deepseek:deepseek-v4-flash` | — | #361: DeepSeek, thinking off |
| `bench-ex-ds-flash-think` | `deepseek:deepseek-v4-flash-thinking` | — | DeepSeek flash, thinking on |
| `bench-ex-ds-pro` | `deepseek:deepseek-v4-pro` | — | DeepSeek pro, thinking off |
| `bench-ex-ds-pro-think` | `deepseek:deepseek-v4-pro-thinking` | — | DeepSeek pro, thinking on |

**Correction:** the original `bench-ex-sonnet-high` and `bench-ex-sonnet-med` vaults were run
(2026-07-15) before the `dig`/`bark` split shipped (#403, merged 2026-07-20/21) — they have no
`.watchdog/extracted/*.json` at all, just the old committed vault notes. They do **not** score
through the current `score_arms.py`, and still need a fresh `dig` run under new vault names
(e.g. `bench-ex2-sonnet-high`, `bench-ex2-sonnet-med`) alongside `bench-ex2-haiku`.

Optional expansion if the core set leaves a question open: `sonnet`/`low`, `opus`/`medium`.

Score against the frozen keys with `score_arms.py` (Step 7): **material-fact recall** and
**`must_not_miss`** (the extractor metrics). Also read the coverage warnings on the 70-page
report, printed during `dig` itself — that is where cheap conditions degrade first.

If you want a briefing to eyeball for a particular arm, run `watchdog bark` on that vault
afterward — it's optional and doesn't change how the arm scores.

## Step 5 — finalizer sweep (cheap)

Extractor fixed at `deepseek:deepseek-v4-flash`. Unlike Step 4, **extract once and reuse it** —
the finalizer metrics (entity-duplicate count, contradictions) depend only on `bark`, so paying
for six fresh extractions of the same fixed input would be wasted spend:

```
# Following Step 3 with vault name "bench-fn-base":
watchdog dig --extractor-model deepseek:deepseek-v4-flash --extractor-effort high

cd ..
cp -r bench-fn-base bench-fn-haiku
cp -r bench-fn-base bench-fn-sonnet-high
cp -r bench-fn-base bench-fn-sonnet-med
cp -r bench-fn-base bench-fn-opus-high
cp -r bench-fn-base bench-fn-ds-flash
cp -r bench-fn-base bench-fn-ds-pro-think
```

Then, in each copy, `--estimate` first, then finalize:

```
cd bench-fn-haiku && watchdog bark --finalizer-model haiku --estimate
watchdog bark --finalizer-model haiku
```

| Vault | `--finalizer-model` | `--finalizer-effort` |
|---|---|---|
| `bench-fn-haiku` | `haiku` | — (ignored) |
| `bench-fn-sonnet-high` | `sonnet` | `high` |
| `bench-fn-sonnet-med` | `sonnet` | `medium` |
| `bench-fn-opus-high` | `opus` | `high` |
| `bench-fn-ds-flash` | `deepseek:deepseek-v4-flash` | — |
| `bench-fn-ds-pro-think` | `deepseek:deepseek-v4-pro-thinking` | — |

Score: **entity-duplicate count** and **contradictions C1/C2** (the finalizer metrics). Read the
contradictions from the `[!contradiction]` callouts written into the entity notes — each carries a
label, both values, both document slugs, and both page numbers — not the briefing's "flagged" count.
This also answers #432 (should reconciliation use Haiku?) — no separate protocol needed.

## Step 6 — classifier smoke test

One vault, no `--skill` (let it classify), default classifier. **Copy only the six bare PDFs, not
their `.yml` sidecars** — a sidecar pin skips classification entirely (D120), which is exactly what
this step needs to exercise:

```
watchdog new "bench-classify"
# copy the six PDFs only (no *.yml), chew, then:
watchdog ingest --extractor-model deepseek:deepseek-v4-flash --finalizer-model deepseek:deepseek-v4-flash
```

Check the classified skill for each document against `expected_skill` in the keys. All six should
land on `bankruptcy` or `financial-statements` correctly. If they do, the classifier is fine on
these types. This does **not** rank classifier models — corpus-v1 has too few skills for that.
(A cheap extractor is used here because we only care about the classification decision.)

## Step 7 — score and decide

Full scoring protocol and the decision rules are in **#361** and **`keys/README.md`**. In brief:

- Judge model must not be a model under test; blind it (strip which condition produced which
  output, randomize order). Credit matches in three tiers — verbatim / credited normalization /
  ungrounded.
- **Claude-vs-Claude arms** (Sonnet vs Haiku, the #215 effort A/B): an **Opus** judge is fine —
  same family, bias cancels.
- **DeepSeek-vs-Claude arm:** cross-check with a **non-Anthropic judge (Gemini)** and compare the
  two verdicts. If they agree, the result stands; if they disagree, that disagreement is the
  finding.
- **Read the first scorecard by hand** before automating anything (#362).

For a free first pass before any judge model runs, `score_arms.py` (this directory) scores the
keys' numeric-anchored items — roughly a third of them — against one or more benchmark vaults
(dig-only or finalized, both work), offline, no model calls:

```
~/.local/pipx/venvs/watchdog-intel/bin/python benchmarks/score_arms.py \
    <projects_dir>/bench-ex-sonnet-med <projects_dir>/bench-t0-sonnet-med
```

It ranks arms against each other (never an absolute recall number) and is blob-level — no
citation provenance, so sibling documents can cross-credit; hand-adjudicate the rows where
paired arms diverge. Items with no numeric anchor are listed as unscorable for the judge pass.
First used for the Tier 0 checklist A/B (#412).

## Rough cost

Order-of-magnitude, from the per-token pricing (extraction is output-dominated). The full corpus on
Sonnet is around $5; scale by the model's output rate relative to Sonnet ($15/Mtok): Haiku ~⅓,
Opus ~1.7×, DeepSeek flash ~2% of Sonnet, DeepSeek pro ~6%; thinking variants add roughly 2–4× for
the chain-of-thought. Dropping `bark` from the extractor sweep (Step 4) removes a Sonnet/high
finalize from every arm, so the sweep now lands near **$15** instead of $20, still dominated by
the Opus and Sonnet extractor arms; the finalizer sweep (one extraction, six finalize calls) and
classifier test are **~$2** combined. **`watchdog dig --estimate` / `watchdog bark --estimate` are
the real numbers — trust them over this paragraph**, and run one before every condition.
