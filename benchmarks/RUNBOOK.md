# Benchmark runbook

Do-this-then-that steps for running a benchmark pass. Written to be followed by a person or by an
agent, without either having to make judgment calls about method — where a choice matters, this
file makes it.

For *why* any of it is shaped this way, see [BENCHMARKING.md](BENCHMARKING.md). For what past runs
measured, see [FINDINGS.md](FINDINGS.md). This file is the procedure only.

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
| Agent SDK vs API vs batch? | `sonnet-med-sdk,sonnet-med-api` then `batch-sonnet-med` |
| Is the verification pass worth shipping? | one verify/noverify pair |

The paired comparisons are run **on their own**, never inside a full sweep.

## 2. Preflight

```
git status --short                 # must be clean — see step 6
~/.local/pipx/venvs/watchdog-intel/bin/python benchmarks/run_benchmark.py \
    --stages extractor --arms <ids> --estimate-only
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
~/.local/pipx/venvs/watchdog-intel/bin/python benchmarks/run_benchmark.py \
    --stages extractor --arms <ids>
```

One confirmation covers the whole matrix. Output lands in `benchmarks/runs/<run-id>/` — gitignored,
because a run's figures are only valid against the commit that produced them.

## 4. Read the run

Read in this order:

0. Keep the run directory. It holds the extractions, the usage, and the page text the run was
   extracted from — vaults are reset on the next run of that arm, so this is the only copy.
1. **`errors.log`** — first, always. An arm that rate-limited or failed partway scores as a *bad
   arm*, and nothing in the summary distinguishes the two.
2. **`REPORT.md`** — the tables, and the code version the run came from.
3. **`run.json`** — the same numbers as data, for the composite score index.

Then check each arm extracted every document: compare `.watchdog/extracted/*.json` against the
corpus size. `score_arms.py` is blob-level, so on this single-case corpus a document a model never
extracted gets **credited from its siblings** — a silent failure that looks like a good score.

## 5. Score

```
~/.local/pipx/venvs/watchdog-intel/bin/python benchmarks/score_arms.py <vault> [<vault> ...]
```

Use the **sub-item** aggregation, not the binary per-item count, which flatters cheap arms. This
covers only the numerically-anchored items — roughly a third of the keys. For the rest, run the
qualitative pass below; the two slices have disagreed on the winner before.

## 6. The qualitative judge pass

**Run this identically every time.** It is the part where drift silently destroys comparability,
because the judgments are opinions and the prompt is what constrains them.

```
~/.local/pipx/venvs/watchdog-intel/bin/python benchmarks/qualitative/build_packets.py \
    --arms sonnet-4.6-high,sonnet-4.6-med,gpt-mini-low
```

Arms are ids from `benchmark.yaml`. It writes one blinded packet per document plus a
`mapping.json` that is **judge-eyes-only** — never show it to whoever or whatever is judging.

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
> Return JSON: `{"<item id>": {"X": "<tier>", "Y": "<tier>", "Z": "<tier>"}, ...}`.

Judgments go in `judgment-<document>.json` beside each packet, then:

```
~/.local/pipx/venvs/watchdog-intel/bin/python benchmarks/qualitative/aggregate.py [out-dir]
```

`verbatim` + `credited` count as a hit; `ungrounded` does not.

## 7. Verifier precision (only for a verify/noverify pair)

Recall alone cannot settle the verification pass, because its additions are *true* — the failure
mode is triviality and restatement, which no recall scorer can see (D172).

```
~/.local/pipx/venvs/watchdog-intel/bin/python benchmarks/verifier_precision.py build \
    benchmarks/runs/<run-id>/artifacts/bench-ex-<arm> --out <out-dir>
# judge grades each added fact grounded_material / grounded_trivial / unsupported, then:
~/.local/pipx/venvs/watchdog-intel/bin/python benchmarks/verifier_precision.py aggregate <out-dir>
```

Read the `unsupported` rows first. An unsupported fact is a different and worse failure than a
trivial one, and averaging the two together hides it.

## 8. Write it down

Add an entry to `FINDINGS.md` — the run directory is gitignored, so this is the only durable
record. Say what the run *meant*, not just what it measured, and carry the caveats:

- the **commit** the run came from (`REPORT.md` names it)
- which slice the numbers are (numeric-anchored, qualitative, or both)
- anything that makes the figures non-comparable to earlier rows — a backend change, a pricing
  fix, a sectioning change

If the run corrects an earlier finding, add it to the **Corrections** list rather than editing the
old entry away. The corrections are the most re-read part of that file.
