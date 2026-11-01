# Benchmark runbook

Do-this-then-that steps for running a benchmark pass. Written to be followed by a person or by an
agent, without either having to make judgment calls about method — where a choice matters, this
file makes it.

For *why* any of it is shaped this way, see [BENCHMARKING.md](BENCHMARKING.md). For what past runs
measured, see [FINDINGS.md](FINDINGS.md). This file is the procedure only.

Everything here goes through `benchmarks/bench` — `bench -h` lists the commands, and
`bench runs` lists what is already on disk with the commit each run came from.

**Never start a real run without the operator's explicit go-ahead.** Every run spends real money
or real subscription session time. `--estimate-only` is free and always safe.

---

## 1. Decide what question the run answers

Pick one. A run that tries to answer two questions answers neither, because the arms stop being
comparable.

| Question | Arms |
|---|---|
| How do models compare on extraction? | a provider group, or the whole `extractor_sweep` |
| Does effort change anything for one model? | that model's `-low` / `-med` / `-high` ladder |
| Agent SDK vs API vs batch? | a backend pair you add (BENCHMARKING.md, "Pin the Claude backend") |
| Is the verification pass worth shipping? | a verify/noverify pair you add (BENCHMARKING.md, Step 8) |

The shipped matrix carries neither pair since D310. Paired comparisons are run **on their own**,
never inside a full sweep.

## 2. Preflight

```
git status --short                 # must be clean — see step 6
benchmarks/bench estimate --stages extractor --arms <ids>
```

Check before spending:

- **The tree is clean.** A dirty tree is recorded in the run, and it means the commit hash does
  not describe what ran. Commit or stash first.
- **Auth mode is right.** `claude-api` cannot run on a subscription; a bare `sonnet`/`haiku` arm
  silently resolves to whichever backend the current mode selects. The preview prints the resolved
  backend and auth mode per arm — read it.
- **The estimate is plausible.** For a model with no post-#547 history the preview falls back to a
  static projection and says so. Treat that number as an order of magnitude, not a budget.
- **Read the reset list.** Vaults holding an earlier run's results are named before the
  prompt and reset when you confirm. Nothing is deleted if you decline, and
  `--estimate-only` never deletes anything.

## 3. Run

```
benchmarks/bench run --stages extractor --arms <ids>
```

One confirmation covers the whole matrix. Output lands in `benchmarks/runs/<run-id>/` — gitignored,
because a run's figures are only valid against the commit that produced them.

## 4. Read the run

Read in this order:

0. `bench runs` to find it. Keep the directory: it holds the extractions, the usage, and the
   page text the run was extracted from. Vaults are reset on the next run of that arm, so this
   is the only copy.
1. **`errors.log`** — first, always. A rate-limited or partway-failed arm now has its own entry
   here (#559) — `_arm_line`'s terse output and `run.json`'s `partial`/`rate_limited` fields tell
   the two apart from a hard failure and from each other, so you no longer have to infer which
   happened from a bare `ok=True`/`ok=False`.
2. **`REPORT.md`** — the tables, and the code version the run came from. A partial arm's recall
   cell reads `100% (6/6) — partial, 2/6 docs` rather than a bare percentage, and it gets its own
   line under "Failed or incomplete arms" alongside hard failures — its figure answers a smaller
   question than a complete arm's and is not a quality signal against the full corpus.
3. **`run.json`** — the same numbers as data, for the composite score index.

Checking that each arm extracted every document — comparing `.watchdog/extracted/*.json` against
the corpus size — used to be a manual step here. `score_arms.py` now does it automatically
(#559): scoring is gated on which documents a vault actually extracted, so a document a model
never opened is excluded from that vault's denominator rather than **credited from its
siblings** (the old blob-level failure mode) or counted as a miss.

### Check the recorded cost against the bill

Every dollar figure in a run is Watchdog's own: the APIs return token counts, and
`model_client` prices them from `model_catalog.yaml`. A pricing rule the catalog misses (the
long-prompt card was one, D365) is invisible until it is compared with what the provider billed.
Do it for any run whose cost figure a decision leans on.

- **Run benchmarks in their own Anthropic workspace** (Console → Settings → Workspaces, with a key
  made there). The cost report is daily and organisation-wide; it groups by workspace and by
  description (model, token type), never by API key, so a workspace is the only way to isolate a
  run's spend from everything else the organisation did that day. Run one benchmark per workspace
  per UTC day if the figure matters.
- **Without an admin key:** read that day's total for the workspace on the Console's Cost page and
  compare it with the run's recorded total (`REPORT.md`, or `python benchmarks/reconcile_cost.py
  benchmarks/runs/<run-id> --day <YYYY-MM-DD>`, which sums the Claude calls that ended that day).
- **With an Admin API key** (`sk-ant-admin...`, made by an organisation admin): set
  `ANTHROPIC_ADMIN_KEY` and add `--workspace <wrkspc_id>`; the script then reads
  `GET /v1/organizations/cost_report` for that day and prints the bill by description next to the
  recorded figure. It sends nothing without the key, and the app never holds one.

A gap of a few per cent is rounding and calls outside the run; a gap that scales with long prompts,
cache writes or batch calls is a pricing rule to fix in the catalog. Cost data usually appears
within minutes of a call, occasionally later. OpenAI, Gemini and DeepSeek arms: compare with each
provider's own usage page the same way.

## 5. Score

```
benchmarks/bench score <vault> [<vault> ...]
```

Use the **sub-item** aggregation, not the binary per-item count, which flatters cheap arms. This
covers only the numerically-anchored items — roughly a third of the keys. For the rest, run the
qualitative pass below; the two slices have disagreed on the winner before.

## 6. The qualitative judge pass

**Run this identically every time.** It is the part where drift silently destroys comparability,
because the judgments are opinions and the prompt is what constrains them.

```
benchmarks/bench packets --arms sonnet-5.5-med-r1,sonnet-5.5-high,gpt-6-luna-med-r1 \
    --out benchmarks/<date>-judge-qualitative-<what>
```

Arms are ids from `benchmark.yaml`. It writes one blinded packet per document plus a
`mapping.json` that is **judge-eyes-only** — never show it to whoever or whatever is judging.

**Always pass `--out`.** It defaults to `benchmarks/qualitative/`, which is where the *last*
pass's packets and judgments already sit — so the default silently overwrites the previous
comparison, and those files are the only durable record of it (`FINDINGS.md` carries the totals
but not the per-item verdicts). Name the directory for the date and the question, as the
`2026-07-29-judge-qualitative` and `2026-08-03-judge-qualitative-luna` directories do.

**To judge an earlier run, add `--run benchmarks/runs/<run-id>/`.** Without it the live vaults are
read, and those are reset the next time that arm runs — so once you have run anything twice, the
run directory is the only place the earlier run still exists.

Rules that make one pass comparable to the next:

- **One judge per document**, each seeing only that document's packet.
- **The judge is never a model under test.** A model grading its own output is not evidence.
- **Blinded.** Arms appear as X/Y/Z, remapped independently per document. Do not reveal arm names,
  and do not let the judge see this file's arm table.
- **Hand-check the first document's verdicts** against the raw vault files before trusting the
  rest. This check is what caught `sonnet-high`'s silent empty extraction; it is not optional.
- **Verify every judgment file's completeness against its packet — every item, every label —
  before trusting a "graded successfully" report.** An interrupted judging pass (a hit usage
  limit, a dropped connection) can still write a complete-looking file with one item missing a
  verdict, and the judge's own final report is not reliable evidence it didn't — one 2026-09-02
  pass had exactly this happen and the interrupted subagent still reported success. Check
  `set(judgment ids) == set(packet ids)` and that every id carries a valid tier for every label,
  not just that the file parses.

Give the judge exactly this prompt, with nothing added:

> You are grading how completely three extractions captured a document's material facts. You are
> given the document's key items — facts a correct extraction should contain — and three
> extractions labelled X, Y and Z. You do not know which system produced which; do not speculate.
>
> For each key item, grade each of X, Y and Z as exactly one of:
>
> - `verbatim` — the extraction states this item's substance directly.
> - `credited` — the extraction states it in different words, or split across entries, but a
>   reader would come away knowing it. Normalisation and rewording are fine.
> - `ungrounded` — the extraction does not contain it, or contains a version contradicted by the
>   document.
>
> Grade only against what the extraction actually says. Do not credit an item because the
> extraction seems generally good, and do not penalise an extraction for including extra material
> beyond the key items. If an extraction is empty for this document, every item is `ungrounded`.
>
> Return JSON in exactly this shape, with one short `note` per verdict saying what in the
> extraction you graded against:
>
> ```json
> {"document": "<the packet's document id>",
>  "judgments": {
>    "<item id>": {"X": {"tier": "<tier>", "note": "<one clause>"},
>                  "Y": {"tier": "<tier>", "note": "<one clause>"},
>                  "Z": {"tier": "<tier>", "note": "<one clause>"}}
>  }}
> ```

Grade every id from both `items.facts` and `items.must_not_miss`; they go in one flat
`judgments` object.

**The shape is not cosmetic** — `aggregate.py` reads `["judgments"][id][label]["tier"]` and
raises `KeyError: 'judgments'` on anything flatter. The `note` is what makes the mandatory
hand-check cheap: without it you re-derive every verdict from the vaults by hand.

Judgments go in `judgment-<document>.json` beside each packet, then:

```
benchmarks/bench judge [out-dir]
```

`verbatim` + `credited` count as a hit; `ungrounded` does not.

**If any arm lost a document, report the totals twice** — whole corpus, and again excluding that
document. The two answer different questions and can rank the arms differently: the whole-corpus
figure is what a user would actually receive (a lost document is a real loss, and unlike
`score_arms.py` this pass charges it to the right arm instead of crediting it from siblings),
while the excluding figure is the only one that isolates extraction *quality* from reliability. A
single lost document is worth tens of items and will otherwise silently drive the entire ranking.

## 7. Verifier precision (only for a verify/noverify pair)

Recall alone cannot settle the verification pass, because its additions are *true* — the failure
mode is triviality and restatement, which no recall scorer can see (D172).

```
benchmarks/bench precision build benchmarks/runs/<run-id> \
    --arm gpt-6-luna-low-verify --out <out-dir>
# judge grades each added fact grounded_material / grounded_trivial / unsupported, then:
benchmarks/bench precision aggregate <out-dir>
```

Read the `unsupported` rows first. An unsupported fact is a different and worse failure than a
trivial one, and averaging the two together hides it.

## 8. Update the model index

```
benchmarks/bench index [run_dir ...] [--judged benchmarks/<judge-pass-dir>/summary.json ...]
```

Regenerates `benchmarks/index/index.json`/`index.md` (#551) — one row per extractor arm: model,
effort, facts, must_not_miss, cost/page, speed/page, reliability. Speed is the arm's real
wall-clock time per page, not summed per-call latency, which overstates it for any arm whose
documents extracted concurrently; a `*` flags an arm that sectioned at least one document, since
the sectioning-count formula is still an open confound (#555) even though the number itself is
now accurate elapsed time. Reliability is doc-count-vs-corpus, coverage gaps, and retries, read
straight from `run.json` — never inferred from a recall figure. With no arguments it reads every
kept run under `benchmarks/runs/`. For each arm id it picks the most recent measurement that isn't
`partial`/failed — never simply whichever run you pointed it at — so a run whose own attempt at an
arm failed can't have its report scored off a sibling run's leftover vault contents for that same
arm (#656); only when nothing clean exists anywhere does it fall back to a partial measurement,
flagged. Pass `--judged` with step 6's `summary.json` to move the arm ids it covers into the rated
table — repeat the flag for each judge pass you want folded in, since step 6's blinding needs a
small, fixed arm count per pass and building up coverage across many arms means running several;
an arm id rated by more than one `--judged` file is refused rather than resolved by
last-file-wins. Every arm no `--judged` file covers lands in "measured, not yet judged" instead,
with its numeric sub-item recall shown and clearly labelled as such — RUNBOOK step 6's warning about the
numeric slice applies here too. Runs whose corpus/keys digest or scorer/cost-model version don't
match the reference cohort are excluded and named, never silently blended in. `index.md` is a
hand-paste fragment for the "How the defaults were chosen" section of `docs/models-and-cost.md`, same convention as `docs-summary.md` — never
auto-inserted.

## 9. Write it down

Add an entry to `FINDINGS.md` — the run directory is gitignored, so this is the only durable
record. Say what the run *meant*, not just what it measured, and carry the caveats:

- the **commit** the run came from (`REPORT.md` names it)
- which slice the numbers are (numeric-anchored, qualitative, or both)
- anything that makes the figures non-comparable to earlier rows — a backend change, a pricing
  fix, a sectioning change

If the run corrects an earlier finding, add it to the **Corrections** list rather than editing the
old entry away. The corrections are the most re-read part of that file.
