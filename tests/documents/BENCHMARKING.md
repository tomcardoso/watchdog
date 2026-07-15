# Running the extraction benchmark (#361 / #215)

A step-by-step guide to measuring the three ingest model stages against corpus-v1. Written to be
followed top to bottom. Nothing here spends money until Step 4, and every run gets a free cost
preview first.

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

- **Extractor sweep** — vary `--extractor-model` × `--extractor-effort`; hold the finalizer fixed
  at a strong baseline (`sonnet` / `high`); pin `--skill` so the classifier is out of the loop.
  These are full ingests: this is where the money goes.
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
cd tests/documents/keys && shasum -a 256 *.yaml > keys-v1.sha256
```

## Step 1 — verify the corpus

```
cd tests/documents && shasum -a 256 -c corpus-v1.sha256
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

Every condition follows the same shape. Example for the baseline (`extractor sonnet/high`,
`finalizer sonnet/high`):

```
# 1. Fresh vault
watchdog new "bench-ex-sonnet-high"
cd <projects_dir>/bench-ex-sonnet-high

# 2. Pass 1 — the four bankruptcy documents
cp "<corpus>/Laurentian Pre-Filing Report of the Proposed Monitor.pdf" _INCOMING/
cp "<corpus>/CV-21-00656040-00CL Laurentian U Initial Order 1 FEB 2021.pdf" _INCOMING/
cp "<corpus>/Laurentian First Report of the Monitor.pdf" _INCOMING/
cp "<corpus>/Pension Order Morawetz CJ- March 17 2021(as stamped by Court).PDF" _INCOMING/
watchdog chew

# FREE cost preview — always run this first
watchdog ingest --skill bankruptcy \
  --extractor-model sonnet --extractor-effort high \
  --finalizer-model sonnet --finalizer-effort high --estimate

# The real run (same flags, no --estimate)
watchdog ingest --skill bankruptcy \
  --extractor-model sonnet --extractor-effort high \
  --finalizer-model sonnet --finalizer-effort high

# 3. Pass 2 — the two annual reports, SAME model flags
cp "<corpus>/Annual-Financial-Report-19-20.pdf" _INCOMING/
cp "<corpus>/Annual-Financial-Report-20-21.pdf" _INCOMING/
watchdog chew
watchdog ingest --skill financial-statements \
  --extractor-model sonnet --extractor-effort high \
  --finalizer-model sonnet --finalizer-effort high
```

Two passes because `--skill` pins **one** skill for the whole queue, and the corpus needs two (see
`expected_skill` in each key). Keep the model flags **identical across both passes** — they are one
condition. Run the `bankruptcy` pass first so all four insolvency documents are present when the
annual reports are reconciled (the scored contradiction C1 spans a `bankruptcy` document and a
`financial-statements` one).

**Capture, per vault, before touching the next one** (the terminal output is not recoverable — page
-coverage warnings and per-document digest-size lines print only there):

```
watchdog usage --all > usage.txt      # token/cost/latency per stage
cp -r briefings/ ../captures/<vault>-briefings/
cp -r .watchdog/registry ../captures/<vault>-registry/
# and save the scrollback of each ingest run
```

## Step 4 — extractor sweep (the spend)

Finalizer fixed at `sonnet` / `high`. One vault each. Run `--estimate` before each.

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

Optional expansion if the core set leaves a question open: `sonnet`/`low`, `opus`/`medium`.

Score against the frozen keys: **material-fact recall** and **`must_not_miss`** (the extractor
metrics). Also read the coverage warnings on the 70-page report — that is where cheap conditions
degrade first.

## Step 5 — finalizer sweep (cheap)

Extractor fixed at `deepseek:deepseek-v4-flash`. One vault each.

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

## Step 6 — classifier smoke test

One vault, no `--skill` (let it classify), default classifier:

```
watchdog new "bench-classify"
# load all six documents, chew, then:
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

## Rough cost

Order-of-magnitude, from the per-token pricing (extraction is output-dominated). The full corpus on
Sonnet is around $5; scale by the model's output rate relative to Sonnet ($15/Mtok): Haiku ~⅓,
Opus ~1.7×, DeepSeek flash ~2% of Sonnet, DeepSeek pro ~6%; thinking variants add roughly 2–4× for
the chain-of-thought. The extractor sweep lands near **$20**, dominated by the Opus and Sonnet arms;
the finalizer sweep and classifier test are **~$2** combined. **`watchdog ingest --estimate` is the
real number — trust it over this paragraph**, and run it before every condition.
