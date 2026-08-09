# Running the extraction benchmark (#361 / #215)

**Superseded day-to-day by `run_benchmark.py` (#466)**, which automates every step below against
`benchmark.yaml`'s arm matrix — cost preview, one go-ahead for the whole run, scoring, and a
written report under `benchmarks/<run-id>/`, instead of a human hand-running each step. This
document stays as the spec that tool automates, and as the manual fallback if `run_benchmark.py`
itself needs debugging.

**Running a subset (#475).** `--arms` selects specific arm ids, so one comparison can run without
paying for the whole ~$15 sweep. An unknown id is a hard error, not a silent full-sweep fallback:

```
run_benchmark.py --stages extractor --arms sonnet-med-sdk,sonnet-med-api
```

**Redoing an already-run arm needs its vault deleted first.** Each arm's vault is a fixed path
(`bench-ex-<arm-id>`, etc.) — rerunning the same arm id against an existing vault does not start
over. `run_benchmark.py` checks every target vault up front (queue files, a pending finalization,
a pending batch, or already-extracted documents) and refuses the whole run before anything is
touched, naming which vault(s) and the `rm -rf` to clear them (#494) — this comes up whenever an
arm's config changes after it already ran once (an `extractor_effort` pin, a corrected model id):

```
rm -rf benchmarks/.vaults/bench-ex-gpt-nano-low benchmarks/.vaults/bench-ex-gpt-mini-low
run_benchmark.py --stages extractor --arms gpt-nano-low,gpt-mini-low
```

No separate deregistration step needed — shadow vaults under `benchmarks/.vaults/` are never
added to `~/.watchdog/projects.json` or Obsidian's vault switcher in the first place (D146,
below), so a plain folder delete is the whole cleanup.

**A real run is terse — one line per arm, not the underlying pipeline's own verbose output.**
This is developer-only tooling, so `watchdog dig`/`watchdog bark`'s usual per-document progress,
warnings, and elapsed ticker are suppressed; you get `[i/N] stage:arm  ✓/✗  duration` and nothing
else per arm. Full failure detail — including a per-document failure that `cmd_extract` catches
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

**Ctrl+C stops the whole run, not just the current arm.** `cmd_extract`/`cmd_finalize` trap a
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
- the `sonnet-med-sdk` / `sonnet-med-api` arms pin the backend explicitly. Run them under api-key
  auth (claude-api cannot run on a subscription) or the pair is not comparable.

**Bracket the mode switch tightly around this pair.** Unlike DeepSeek/OpenAI/Gemini, a stored
Anthropic key is not enough on its own — `resolve_auth()` only returns it while `watchdog auth`'s
mode is `api-key`; a key sitting in credentials.json while mode is `subscription` is inert, pinned
backend or not. So: switch to api-key mode, run this pair alone (`--arms
sonnet-med-sdk,sonnet-med-api`), then switch straight back to subscription — leaving mode on
api-key would also meter every other bare `sonnet`/`haiku`/`opus` arm in the sweep.

**The third leg — `claude-agent-sdk` on subscription — is a separate stage, not part of this
pair's run.** Once `sonnet-med-sdk`/`sonnet-med-api` have metered numbers, `sdk_check` (its own
section in `benchmark.yaml`) re-runs the same `claude-agent-sdk:sonnet` pin under subscription
auth, giving a clean three-way read at the same effort level without conflating auth mode and
harness overhead into one variable. It uses its own small two-document corpus
(`corpora/sdk-check/`, not the frozen six-document `corpus-v1`) — a harness/backend spot-check
doesn't need the full corpus, and keeping it small matters here since subscription mode spends
session time, not dollars. Not part of the default `--stages` list — switch `watchdog auth` back
to subscription, then run it on its own:

```
run_benchmark.py --stages sdk-check
```

Excluded from the extractor sweep's recall scoring and the six-document cost summary in
`REPORT.md`/`docs-summary.md`, since its two-document corpus doesn't match either one — it gets
its own "SDK backend check" section instead.

**Benchmark vaults live in a shadow root, never in your real investigations folder (D146).**
Early runs of `run_benchmark.py` created `bench-*` vaults straight in the installed watchdog's
own `projects_dir` and let `cmd_new` register each one in the TWO places it always registers a new
vault — `~/.watchdog/projects.json` and Obsidian's own `obsidian.json` (its vault switcher) — which
is why a working copy of `watchdog projects`, or Obsidian's vault switcher itself, may already show
a stack of stray `bench-*` entries. Those are left alone deliberately, see D146, but nothing new
should join them. `run_benchmark.py` still creates vaults with the real `cmd_new` (fidelity to an
actual vault's layout is the point) but points it at `benchmarks/.vaults/` by default, and
deregisters each vault from both `projects.json` and `obsidian.json` immediately afterward.
Override the location with `--vault-root PATH` or a top-level `vault_root:` key in
`benchmark.yaml` (relative paths resolve against the config file, like `corpus.dir`). The shadow
root is gitignored; delete it any time with `bench clean` (below) or `rm -rf benchmarks/.vaults`.

**The `bench` wrapper (dev convenience, not shipped).** `benchmarks/bench` fronts every step of a
pass — the runner, the scorers and the judge tooling — so you don't have to remember the pipx
venv's interpreter path or where each script lives. `bench -h` is the full list:

```
benchmarks/bench estimate --stages extractor --arms haiku,gemini-flash-low   # --estimate-only
benchmarks/bench run --stages extractor --arms haiku                         # the real thing
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
benchmark, not part of the shipped `watchdog` CLI — see `docs/benchmarks.md` for that boundary.

A step-by-step guide to measuring the three ingest model stages against corpus-v1. Written to be
followed top to bottom. Nothing here spends money until Step 3, and every run gets a free cost
preview first.

**Do not run a live ingest (Steps 3, 4, 5, or 6) without Tom's explicit go-ahead.** These runs cost
real money and share his subscription session window — `--estimate` first, always, but the
estimate is a preview, not the ask.

**Running a pass?** Follow [RUNBOOK.md](RUNBOOK.md) — the steps, in order, including the
pinned judge prompt. This file is the reasoning behind those steps, not the procedure.

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
cd benchmarks/keys && shasum -a 256 *.yaml > keys-v<next>.sha256
```

Cut a **new** version rather than re-hashing the current one (`keys/README.md` explains why), and
update the `keys:` line in `benchmark.yaml` *and* any other config that pins a manifest —
`selfconsistency.yaml` today — in the same change. `verify_freeze` exits on a manifest it can't
find, so a rename that misses a config takes every benchmark run down until it's caught.

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

**Every arm pins an explicit effort, and that is deliberate.** An unpinned arm runs at whatever
the provider defaults to, which is neither reproducible nor comparable across providers. On OpenAI
that default was measured burning a median 12K-24K *output* tokens per section call on this corpus
(`gpt-mini` peaked at 46K against a 48K ceiling) — reasoning tokens dwarfing the JSON, on sections
capped at ~7K estimated input tokens. Haiku and DeepSeek are the exceptions: they have no effort
control at all (no `effort_levels` in `model_catalog.yaml`), and asking for one errors.

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

**`claude-batch` arms.** Since D144 a batch no longer needs a run-wide pinned skill — each
document resolves its own, so corpus-v1's per-document sidecar pins (D120) carry through and the
two-skill corpus batches correctly. A batch arm is submit-and-exit: the arm returns once the batch
is submitted, and a *later* run collects it, so its `REPORT.md` row is empty until then.

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

## Step 8 — the verification pass A/B (#535)

`benchmark.yaml` carries a verify/noverify pair for each of the three candidate models — Sonnet
4.6, `gpt-mini` and Luna. Each pair is the same extractor with and without the second-read
verification pass, so the difference within a pair is the pass and nothing else. Run **one pair at
a time**, on its own:

```
~/.local/pipx/venvs/watchdog-intel/bin/python benchmarks/run_benchmark.py \
    --stages extractor --arms gpt-mini-low-verify,gpt-mini-low-noverify
```

**On the cost preview.** `cost_reference._matches` only reuses an archived usage file when every
call in it shares one effort, so a verify arm's own history is reusable as a reference only when
`extractor_effort` equals the `verifier_effort` config value (default `low`). The `gpt-mini` and
Luna pairs satisfy that. The Sonnet pair is pinned `medium` — the judged candidate config, not
`low` — so expect its preview to fall back to the static projection and say so.

Score **recall** with `score_arms.py` and the judge pass, exactly as for any other arm pair — the
question is whether `must_not_miss` closes the gap it has never closed by raising effort.

Then score **precision** on the added facts, which is the gating requirement (D172): a
recall-biased gap-finder produces restatements and true-but-worthless detail, and the recall
scorers cannot see that at all, because those additions are *true*. `verifier_precision.py` builds
one self-contained judging packet per document — the added facts, the extractor's own facts as the
restatement reference, the page text as the grounding reference — and tallies the grades:

```
~/.local/pipx/venvs/watchdog-intel/bin/python benchmarks/verifier_precision.py build \
    benchmarks/.vaults/bench-ex-gpt-mini-verify --out /tmp/verifier-judge
# a judge grades each fact grounded_material / grounded_trivial / unsupported,
# writing judgment-<document>.json beside each packet, then:
~/.local/pipx/venvs/watchdog-intel/bin/python benchmarks/verifier_precision.py aggregate \
    /tmp/verifier-judge
```

Same judge discipline as Step 7: the judge model must not be the model under test. Read the
`unsupported` rows first — a fact the source doesn't support is a different and worse failure than
a trivial one, and the two should not be averaged together when deciding.

## Rough cost

Order-of-magnitude, from the per-token pricing (extraction is output-dominated). The full corpus on
Sonnet is around $5; scale by the model's output rate relative to Sonnet ($15/Mtok): Haiku ~⅓,
Opus ~1.7×, DeepSeek flash ~2% of Sonnet, DeepSeek pro ~6%; thinking variants add roughly 2–4× for
the chain-of-thought. Dropping `bark` from the extractor sweep (Step 4) removes a Sonnet/high
finalize from every arm, so the sweep now lands near **$15** instead of $20, still dominated by
the Opus and Sonnet extractor arms; the finalizer sweep (one extraction, six finalize calls) and
classifier test are **~$2** combined. **`watchdog dig --estimate` / `watchdog bark --estimate` are
the real numbers — trust them over this paragraph**, and run one before every condition.
