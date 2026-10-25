# Running the extraction benchmark (#361 / #215)

**Superseded day-to-day by `run_benchmark.py` (#466)**, which automates every step below against
`benchmark.yaml`'s arm matrix — cost preview, one go-ahead for the whole run, scoring, and a
written report under `benchmarks/<run-id>/`, instead of a human hand-running each step. This
document stays as the spec that tool automates, and as the manual fallback if `run_benchmark.py`
itself needs debugging.

**Running a subset (#475).** `--arms` selects specific arm ids, so one comparison can run without
paying for the whole sweep. An unknown id is a hard error, not a silent full-sweep fallback:

```
run_benchmark.py --stages extractor --arms sonnet-5.5-med,haiku-5.5-med
```

**Repeated runs and the brief (D278).** `repeats: N` on an arm (or in a sweep's `defaults:`)
runs it N times as `<id>-r1` … `<id>-rN`, each in its own vault; `--arms <id>` selects all N, and
REPORT.md's "Spread across repeated runs" table (and `run.json`'s `repeat_groups`) gives the mean,
lowest–highest and sample standard deviation of each group's recall and cost, counting only
complete runs. Use it for any comparison you intend to act on: single runs of one configuration
have differed by up to 3x (#581). `brief: <file>` on an extractor arm writes that file into the
arm vault as `context.md`; `brief: false` withholds a vault's own `context.md` from extraction.
`brief:` in the extractor sweep's `defaults:` applies to every arm that sets none, and on the
finalizer sweep's `base:` it gives the shared extraction, and so every finalizer arm, the brief.
Benchmark vaults have no `context.md` otherwise. The shipped matrix gives every arm
`briefs/corpus-v1.md` (D310), as real use always has a brief; every archived arm extracted with
none, so archived figures are not comparable with a briefed run.

**Redoing an already-run arm needs its vault deleted first.** Each arm's vault is a fixed path
(`bench-ex-<arm-id>`, etc.) — rerunning the same arm id against an existing vault does not start
over. `run_benchmark.py` checks every target vault up front (queue files, a pending finalization,
a pending batch, or already-extracted documents) and refuses the whole run before anything is
touched, naming which vault(s) and the `rm -rf` to clear them (#494) — this comes up whenever an
arm's config changes after it already ran once (an `extractor_effort` pin, a corrected model id):

```
rm -rf benchmarks/.vaults/bench-ex-gpt-6-luna-low-r1 benchmarks/.vaults/bench-ex-sonnet-5.5-high
run_benchmark.py --stages extractor --arms gpt-6-luna-low,sonnet-5.5-high
```

No separate deregistration step needed — shadow vaults under `benchmarks/.vaults/` are never
added to `~/.watchdog/projects.json` or Obsidian's vault switcher in the first place (D146,
below), so a plain folder delete is the whole cleanup.

**A real run is terse — one line per arm, not the underlying pipeline's own verbose output.**
This is developer-only tooling, so processing's and post-processing's usual per-document progress,
warnings, and elapsed ticker are suppressed; you get `[i/N] stage:arm  ✓/✗  duration` and nothing
else per arm. Full failure detail — including a per-document failure that the processing run catches
and tallies internally rather than raising, which an `ok`-only summary would miss entirely — goes
to `errors.log` in the run's report folder, not the terminal; a failed or partially-failed arm's
terse line just says `(see errors.log)`. Report folders are named to the minute
(`benchmarks/2026-07-29-1432/`), not just the date, so more than one session in a day gets its
own folder without a manual `-2`/`-3` disambiguation. A rate limit gets its own line rather than
reading as an interrupt: `⚠ rate-limited after 2/6 docs  4m12s  (see errors.log)` — distinct from
a plain Ctrl-C, which now carries the same document count (`interrupted after 3/6 docs`) but no
`⚠` and no errors.log pointer, since stopping it was a deliberate choice, not a failure (#559).

**Under `_quiet`, an arm can pause silently for up to ~15 minutes without anything being wrong
(#559).** `run_extractor_arm` runs every extractor arm with `--wait` and a bounded
`max_rate_limit_waits` (default 2, see `benchmark.yaml`'s `extractor_sweep.defaults`), so a rate
limit mid-arm doesn't just stop it — it sleeps until the provider's reported reset time (or a
15-minute fallback when none is reported) and resumes automatically. `_quiet` suppresses the
underlying pipeline's own "Rate limit — resuming at HH:MM" notice along with everything else it
prints, so that wait is invisible at the terminal: an arm that seems to be hanging on the
`running…` line for several minutes may simply be waiting out a rate limit, not stuck. It gives
up and reports a partial arm once `max_rate_limit_waits` is exhausted, rather than waiting forever.

**Ctrl+C stops the whole run, not just the current arm.** `ops.ingest.process`/`finish` trap a
SIGINT internally and return normally (finishing in-flight writes first) rather than raising —
without something checking for that, a single interrupt only ever stopped the *current* arm, and
the runner just started the next one's real API calls. The runner now reads that signal off the
return value and stops the whole matrix after the interrupted arm finishes, instead of requiring
one Ctrl+C per remaining arm.

**The cost preview works even though every arm vault is fresh (#478).** `ingest_setup.cost_estimate`
normally derives its $/token ratio from *this vault's own* recent `usage-<ts>.json` history — which
an arm vault never has, by design (every condition is its own fresh vault, above). So the preview
instead borrows usage archived from a past benchmark run of the exact same model/effort/backend
combination (`benchmarks/<run-id>/artifacts/<vault-name>/usage/`, written by every prior real run),
and only when no such run exists anywhere yet falls back to a rough catalog-list-price projection —
printed with an explicit `(rough projection, no matching run history yet)` caption rather than
shown indistinguishably from a run-calibrated range. See `cost_reference.py`.

**Pin the Claude backend when it is what you are measuring (#475).** A bare `sonnet` arm carries
no backend of its own — `_effective_extract_backend` picks one from the *current auth mode*
(subscription → `claude-agent-sdk`, api-key → `claude-api`). Those two bill materially different
input-token counts for identical documents: telemetry from the `bench-ex2` round put actual/
estimated input at ~2.4x on the agent SDK against ~1.24x elsewhere, and the SDK also pays a
cache-*write* premium on text it never reads back. So:

- the cost preview now prints each arm's resolved backend and auth mode before you confirm;
- every arm row in `REPORT.md` names the backend that served it;
- a subscription arm's costs are marked `~` and captioned as list-price equivalents rather than
  amounts billed — they are comparable to each other, not to a metered arm's real spend;
- to measure the backends themselves, pin them in a pair of arms (`claude-agent-sdk:sonnet-5.5`
  and `claude-api:sonnet-5.5`, same effort) and run the pair under api-key auth (claude-api cannot
  run on a subscription) or it is not comparable. The shipped matrix no longer carries that pair
  (D310); add it to `extractor_sweep` when the question comes back.

**Bracket the mode switch tightly around such a pair.** Unlike DeepSeek/OpenAI/Gemini, a stored
Anthropic key is not enough on its own — `resolve_auth()` only returns it while the Anthropic
sign-in mode (`mode` in `~/.watchdog/credentials.json`, set in the app's Models & keys) is `api-key`; a key sitting in credentials.json while mode is `subscription` is inert, pinned
backend or not. So: switch to api-key mode, run the pair alone with `--arms`, then switch
straight back to subscription — leaving mode on api-key would also meter every other bare
Claude arm in the sweep.

**The third leg — `claude-agent-sdk` on subscription — is a separate stage, not part of that
pair's run.** Once the pair has metered numbers, an `sdk_check` section in `benchmark.yaml` (not
shipped since D310; its shape is `vault_prefix`, `corpus_dir` and `arms`, as in
`tests/test_benchmark_runner.py`) re-runs the same `claude-agent-sdk` pin under subscription
auth, giving a clean three-way read at the same effort level without conflating auth mode and
harness overhead into one variable. It uses its own small two-document corpus
(`corpora/sdk-check/`, not the frozen six-document `corpus-v1`) — a harness/backend spot-check
doesn't need the full corpus, and keeping it small matters here since subscription mode spends
session time, not dollars. Not part of the default `--stages` list — switch the sign-in mode back
to subscription, then run it on its own:

```
run_benchmark.py --stages sdk-check
```

Excluded from the extractor sweep's recall scoring and the six-document cost summary in
`REPORT.md`/`docs-summary.md`, since its two-document corpus doesn't match either one — it gets
its own "SDK backend check" section instead.

**Benchmark vaults live in a shadow root, never in your real investigations folder (D146).**
Early runs of `run_benchmark.py` created `bench-*` vaults straight in the installed watchdog's
own `projects_dir` and let project creation register each one in the TWO places it always registers a new
vault — `~/.watchdog/projects.json` and Obsidian's own `obsidian.json` (its vault switcher) — which
is why the app's investigation list, or Obsidian's vault switcher itself, may already show
a stack of stray `bench-*` entries. Those are left alone deliberately, see D146, but nothing new
should join them. `run_benchmark.py` still creates vaults with the real `ops.projects.create` (fidelity to an
actual vault's layout is the point) but points it at `benchmarks/.vaults/` by default, and
deregisters each vault from both `projects.json` and `obsidian.json` immediately afterward.
Override the location with `--vault-root PATH` or a top-level `vault_root:` key in
`benchmark.yaml` (relative paths resolve against the config file, like `corpus.dir`). The shadow
root is gitignored; delete it any time with `bench clean` (below) or `rm -rf benchmarks/.vaults`.

**One run per vault root (D337, #656).** Arm vaults are keyed by arm id under the root, so two
overlapping `bench run`s sharing it would write into the same vaults and each score the other's
files. A run (an estimate too, since it seeds vaults) holds `<vault-root>/.bench-run.lock` for its
whole duration and a second one against the same root exits at once, naming the first. The lock
is released on any exit, Ctrl+C included, and one left by a crashed run is taken over
automatically. To run two at once, give the second its own `--vault-root`. Separately, an
extractor arm whose own run extracted 0 documents is reported as "not scored" instead of with a
recall read from whatever files its vault holds.

**The `bench` wrapper (dev convenience, not shipped).** `benchmarks/bench` fronts every step of a
pass — the runner, the scorers and the judge tooling — so you don't have to remember the
development Python's path or where each script lives. `bench -h` is the full list:

```
benchmarks/bench estimate --stages extractor --arms haiku-5.5-low,gemini-3.8-flash-low  # --estimate-only
benchmarks/bench run --stages extractor --arms haiku-5.5-low                         # the real thing
benchmarks/bench arms                                                        # list arm ids per stage
benchmarks/bench runs                                                        # kept runs + their commit
benchmarks/bench score <vault> ...                                           # numeric-anchor recall
benchmarks/bench packets --arms a,b,c                                        # qualitative packets
benchmarks/bench judge                                                       # tally judgments
benchmarks/bench precision build <run> --arm <id> --out <dir>                # verifier precision
benchmarks/bench clean                                                       # wipe the shadow vault root
```

`clean` reclaims disk; it is no longer needed to make a re-run possible, since stale arm vaults
are reset at the start of a run. [RUNBOOK.md](RUNBOOK.md) is the step-by-step.

Run `benchmarks/bench --help` for the full list. It is dev tooling for whoever is running the
benchmark, not part of the app; `docs/models-and-cost.md` summarizes its results for users.

A step-by-step guide to measuring the three ingest model stages against corpus-v1. Written to be
followed top to bottom. Nothing here spends money until Step 3, and every run gets a free cost
preview first.

**Do not run a live ingest (Steps 3, 4, 5, or 6) without Tom's explicit go-ahead.** These runs cost
real money and share his subscription session window — `bench estimate` first, always, but the
estimate is a preview, not the ask.

**Running a pass?** Follow [RUNBOOK.md](RUNBOOK.md) — the steps, in order, including the
pinned judge prompt. This file is the reasoning behind those steps, not the procedure.

## What we are deciding

Watchdog makes three kinds of model call during ingest. This benchmark picks the right-size default
for each, across each provider's current frontier models (Anthropic, OpenAI, Gemini and DeepSeek,
D310), plus the reasoning-effort knob where it applies.

| Stage | Option (`RunOptions`, an arm's keys) | Default | Effort knob? | Cost shape |
|---|---|---|---|---|
| **Classifier** | `classifier_model` / `classifier_effort` | `haiku` / `low` | not swept | 1 call/doc, first 5 pages only — trivial |
| **Extractor** | `extractor_model` / `extractor_effort` | `sonnet` / `medium` | yes | per page — **essentially the whole bill** |
| **Finalizer** | `finalizer_model` / `finalizer_effort` | `haiku` / unset | yes | ~3 calls + reconciliation per ingest — near-constant |

Candidate models: every model in `model_catalog.yaml`, which holds each provider's current
frontier models only, short of the flagship class (D311–D313) — Claude Haiku 5.5 and Sonnet
5.5; GPT-6 Luna and GPT-5.6 Luna; Gemini 3.8 Flash and 3.5 Flash-Lite; DeepSeek Flash
and V4 Pro.
Arms pin a Claude model by its versioned tier (`sonnet-5.5`, not `sonnet`), so an arm means the
same model whatever the bare name resolves to later. Notes that matter:

- **Effort levels differ by provider** (`effort_levels` in `model_catalog.yaml`): every Claude and
  OpenAI model takes all five, Gemini takes `low`/`medium`/`high`, DeepSeek only what is below.
  `tests/test_benchmark_runner.py` checks every shipped arm's model and effort against the
  catalogue, so a typo fails the suite rather than the first paid call.
- **The classifier's effort is not swept**; it stays at its `low` default.
- **DeepSeek's main knob is its thinking toggle**, swapped via the model id:
  `deepseek:deepseek-flash` (thinking off) vs `deepseek:deepseek-flash-thinking` (on), same for
  `-pro`. Tom's prior testing found non-thinking DeepSeek weak, so test both. Since 2026-08-13 the
  thinking ids *also* take an effort level — `low`/`high`/`max` only (`medium`/`xhigh` are DeepSeek's
  own aliases for `high` and are rejected here rather than run as duplicates, D217) — and a
  thinking-off id still takes none. The sweep runs Flash with thinking off and on (at `high`, the
  provider's default), and Pro with thinking on. On the numeric check the archived runs found
  `high` well ahead of `low` on both thinking ids (FINDINGS.md, run `2026-08-31-1645`), so the
  frontier matrix runs `high` only.

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

- **Extractor sweep** — vary `extractor_model` × `extractor_effort`; the finalizer never runs
  at all (processing only — see Step 4), since the metrics this sweep scores live entirely in
  the staged extraction artifacts; the corpus's per-document sidecar pins (D120) keep the
  classifier out of the loop, no `skill` option needed. This is still where the money goes — it's
  just no longer paying for a finalizer it doesn't need too.
- **Finalizer sweep** — vary `finalizer_model` × `finalizer_effort`; hold the extractor fixed
  at **`deepseek:deepseek-flash`**. Two reasons for the cheap fixed extractor: (a) it keeps the wasted
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
cd benchmarks/keys
mkdir v<current> && cp *.yaml v<current>/ && git mv keys-v<current>.sha256 v<current>/
# ...then edit the keys, and only then:
shasum -a 256 *.yaml > keys-v<next>.sha256
```

Cut a **new** version rather than re-hashing the current one (`keys/README.md` explains why), and
update the `keys:` line in `benchmark.yaml` *and* any other config that pins a manifest —
`selfconsistency.yaml` today — in the same change. `verify_freeze` exits on a manifest it can't
find, so a rename that misses a config takes every benchmark run down until it's caught.

Archive the outgoing version's **key files** alongside its manifest, as the first line above does
(`v1/`, `v2/`). The manifest alone only proves an archived figure was scored against keys that
hashed to some value; it can't show what those keys said. Add the new row to the version table in
`keys/README.md` while you're there.

## Step 1 — verify the corpus

```
cd benchmarks/corpora/extract && shasum -a 256 -c corpus-v1.sha256
```

All six must report `OK`. If any fails, stop — a single changed byte makes runs incomparable.

## Step 2 — check auth

You need Anthropic and DeepSeek credentials. Keys the desktop app stored are encrypted and the
scripts here can't read them (D295): export them for the session instead (`ANTHROPIC_API_KEY`,
`DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`), which always take precedence over a stored
key. A key stored in plain text (a computer with no secure storage) still works. (Gemini is only
needed later, for the DeepSeek-arm judge cross-check in scoring — not for the runs.)

## Step 3 — how a run is built

There is no command line (D300); `bench` (a thin wrapper around `run_benchmark.py`) drives the
same library operations the app runs. For every arm it creates a fresh vault under
`benchmarks/.vaults/` with `ops.projects.create`, copies the frozen corpus in with each PDF's own
`.yml` sidecar (so each document's record skill is pinned, D120, and nothing is classified under
the wrong one), pre-processes it once into a master vault (`ops.ingest.preprocess`), and copies the
master for each arm. Extraction runs `ops.ingest.process` with the arm's `RunOptions`
(`finalize=False`); post-processing runs `ops.ingest.finish`. Each arm's usage records, staged
extractions and, when it finalized, its briefings and registry stay in its vault and are copied
into the run's report folder.

```
bench arms                                   # every arm id in benchmark.yaml, by stage
bench estimate --stages extractor            # free: what each arm would cost
bench run --stages extractor --arms sonnet-5.5-high,haiku-5.5-low
```

## Step 4 — extractor sweep (the spend)

**Every arm pins an explicit effort, and that is deliberate.** An unpinned arm runs at whatever
the provider defaults to, which is neither reproducible nor comparable across providers. On OpenAI
that default was measured burning a median 12K-24K *output* tokens per section call on this corpus
(`gpt-mini` peaked at 46K against a 48K ceiling) — reasoning tokens dwarfing the JSON, on sections
capped at ~7K estimated input tokens. DeepSeek is the one exception — effort exists only on its
`-thinking` ids (D217), so the thinking arms pin a level like everyone else and the thinking-off
arm pins nothing, because there is no knob on it to pin.

Processing only — **no post-processing**. Everything this step scores (material-fact recall,
`must_not_miss`, coverage warnings) is in the staged extraction artifacts before any finalizer
call runs, so finalizing here would just spend money and time on a stage this step doesn't
measure. The arms are `extractor_sweep` in `benchmark.yaml`; run `bench estimate --stages
extractor` before `bench run --stages extractor`.

**`claude-batch` arms.** Since D144 a batch no longer needs a run-wide pinned skill — each
document resolves its own, so corpus-v1's per-document sidecar pins (D120) carry through and the
two-skill corpus batches correctly. A batch arm is submit-and-exit: the arm returns once the batch
is submitted, and a *later* run collects it, so its `REPORT.md` row is empty until then.

Score against the frozen keys with `score_arms.py` (Step 7): **material-fact recall** and
**`must_not_miss`** (the extractor metrics). Also read the coverage warnings on the 70-page
report, in the arm's `errors.log` and usage records — that is where cheap conditions degrade first.

## Step 5 — finalizer sweep (cheap)

The extractor is fixed (`finalizer_sweep.extractor` in `benchmark.yaml`). The runner **extracts
once and reuses it**: the finalizer metrics (entity-duplicate count, contradictions) depend only
on post-processing, so each `finalizer_sweep` arm finalizes its own copy of one extracted vault.

```
bench estimate --stages finalizer
bench run --stages finalizer
```

Score: **entity-duplicate count** and **contradictions C1/C2** (the finalizer metrics). Read the
contradictions from the registry's contradiction ledger (`entities.json`, each entity's
`contradictions`), rendered as `[!contradiction]` callouts in the entity notes — each carries a
label, both values, both document slugs, and both page numbers — not the briefing's "flagged" count.
This also answers #432 (should reconciliation use Haiku?) — no separate protocol needed.

## Step 6 — classifier smoke test

`bench run --stages classifier` runs one vault with the PDFs only, no `.yml` sidecars — a sidecar
pin skips classification entirely (D120), which is exactly what this step needs to exercise —
on the default classifier and a cheap extractor (`classifier_smoke` in `benchmark.yaml`).

Check the classified skill for each document against `expected_skill` in the keys. All six should
land on `bankruptcy` or `financial-statements` correctly. If they do, the classifier is fine on
these types. This does **not** rank classifier models — corpus-v1 has too few skills for that;
`classifier-sweep` does, on its own corpus.

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
python benchmarks/score_arms.py \
    <projects_dir>/bench-ex-sonnet-med <projects_dir>/bench-t0-sonnet-med
```

It ranks arms against each other (never an absolute recall number) and is blob-level — no
citation provenance, so sibling documents can cross-credit; hand-adjudicate the rows where
paired arms diverge. Items with no numeric anchor are listed as unscorable for the judge pass.
First used for the Tier 0 checklist A/B (#412).

## Step 8 — the verification pass A/B (#535)

The archived runs measured verify/noverify pairs on Sonnet 4.6, GPT-5.4 Mini and GPT-5.6 Luna; the
frontier matrix no longer carries them (D310). To measure the pass on a current model, add a pair
to `extractor_sweep`: the same model and effort with `verify: true` and `verify: false`, both at
`concurrency: 3` (verify re-sends full-size section input, so its tokens per minute run about 2.7x
the same model without it, #559). The difference within a pair is the pass and nothing else. Run
**one pair at a time**, on its own:

```
python benchmarks/run_benchmark.py \
    --stages extractor --arms gpt-6-luna-low-verify,gpt-6-luna-low-noverify
```

**On the cost preview.** `cost_reference._matches` only reuses an archived usage file when every
call in it shares one effort, so a verify arm's own history is reusable as a reference only when
`extractor_effort` equals the `verifier_effort` config value (default `low`). A pair pinned at any
other effort falls back to the static projection and says so.

Score **recall** with `score_arms.py` and the judge pass, exactly as for any other arm pair — the
question is whether `must_not_miss` closes the gap it has never closed by raising effort.

Then score **precision** on the added facts, which is the gating requirement (D172): a
recall-biased gap-finder produces restatements and true-but-worthless detail, and the recall
scorers cannot see that at all, because those additions are *true*. `verifier_precision.py` builds
one self-contained judging packet per document — the added facts, the extractor's own facts as the
restatement reference, the page text as the grounding reference — and tallies the grades:

```
python benchmarks/verifier_precision.py build \
    benchmarks/.vaults/bench-ex-gpt-6-luna-low-verify --out /tmp/verifier-judge
# a judge grades each fact grounded_material / grounded_trivial / unsupported,
# writing judgment-<document>.json beside each packet, then:
python benchmarks/verifier_precision.py aggregate \
    /tmp/verifier-judge
```

Same judge discipline as Step 7: the judge model must not be the model under test. Read the
`unsupported` rows first — a fact the source doesn't support is a different and worse failure than
a trivial one, and the two should not be averaged together when deciding.

## Token density — `tokenizer_ratio.py` (#617)

Separate from the arm matrix: measures how many real tokens each model counts for text the chars/4
`est_tokens` heuristic scores as one estimated token. That multiplier is `model_catalog.yaml`'s
`tokenizer_ratio`, which `pipeline/section.py` divides its window-derived threshold/budget by, and
every value in the catalog now comes from this script rather than a vendor sentence (D198).

```
python benchmarks/tokenizer_ratio.py
```

**A bare run is free** — no extraction calls, no generation, no subscription session window. It
needs the master chew (`--vault`, default `benchmarks/.vaults/bench-master`), not a set of arm
vaults. Three modes; the two free ones both run unless you name one, and the billed one never runs
unless you ask for it:

- `--count` (free) queries Anthropic's `messages.count_tokens` and Gemini's
  `models/<id>:countTokens`, non-generative endpoints that cost nothing. Whole corpus. This is the
  ground truth for those two providers.
- `--probe` (**billed, ~$0.11**) is the only way to measure OpenAI and DeepSeek, which expose no
  counter: a one-character baseline call plus one sample call per model, subtracted so message
  framing cancels. It measures a 12,028-est-token strided sample, not the whole corpus — `--count`
  measures that same sample too, and it tracks the whole-corpus ratio to within 0.1–1.8%, which is
  what licenses reading a probed sample ratio as a corpus ratio.
- `--history` (free, offline) fits `actual = ratio × est + overhead` over usage already archived
  under `benchmarks/<run-id>/artifacts/*/usage/*.json`. **Read `r²` and the slope's standard error
  before believing a row.** Where counted ground truth also exists the regression reads 12–17%
  high (the harvested-candidate block scales with document size, so some per-token overhead lands
  in the slope), so treat a history-only slope as an upper bound. The `gpt-5.4-nano` row is the
  cautionary case: it fits at r² 0.38 and reads 1.354, against a probed 0.795.

Do **not** reach for tiktoken as a shortcut. It cannot map any GPT-5.x id in this catalog
(`encoding_for_model("gpt-5.4-mini")` raises, checked 2026-08-15), and it is simply the wrong
tokenizer for Claude.

Re-run it when adding a model on a tokenizer no catalogued model already uses — every model within
a family returned byte-identical counts, on all four providers, so a family member can just copy
its family's value — or when the corpus itself changes.

## Rough cost

Order-of-magnitude only (extraction is output-dominated): scale by each model's output rate
against Sonnet 5.5's $10 per million tokens. Gemini 3.8 Flash costs about 0.4x Sonnet (double
from 2027-01-01), Flash-Lite about 0.25x, Haiku 5.5 and GPT-6 Luna about 1/20 and DeepSeek Flash
about 6% (double in its peak hours). Thinking spends output, so effort moves every
figure. The Sonnet arms dominate the sweep. **`bench estimate` gives the real
numbers — trust it over this paragraph**, and run it before every run.
