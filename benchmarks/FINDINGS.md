# Benchmark findings — running log

Hand-maintained. Updated by whoever reads a run's `REPORT.md` next (`benchmarks/<run-id>/`) and
has something worth writing down about what it means — the automated run produces the numbers,
this file is where a human says what they add up to. Migrated 2026-07-26 from the pre-#466
`tests/documents/BENCHMARK-RESULTS.md`, which this file and `benchmarks/<run-id>/REPORT.md`
together replace.

## Findings so far

**DeepSeek's "thinking" toggle doesn't pay for itself on this corpus.** Confirmed on both model
sizes, not a one-run fluke — `bench-ex2-ds-pro-think` reproduced the same anomaly
`bench-ex2-ds-flash-think` showed while it was still mid-run:

- **Coverage:** both thinking variants skip roughly the back half of *Laurentian First Report of
  the Monitor* (pp. 15–34 / 17–34 of 34). Both non-thinking variants have zero coverage gaps
  anywhere, including the dense 70-page annual report — the document #339 expected cheap
  conditions to degrade on first. Thinking mode is degrading coverage on an *easier* document
  instead.
- **Reliability:** flash-think needed a retry on the corpus's easiest document (5 pages). Pro-think
  needed the pipeline to fall back to sectioning a 36-page document — well under the size
  threshold that normally triggers that — into 5 separate calls.
- **Cost/latency:** pro-think costs +20% over pro and runs at 2.6× pro's summed latency, for
  materially the same fact recall (90% vs 92%) and identical must_not_miss (83%).
- **Recall itself is roughly a wash** — thinking neither clearly helps nor hurts on the numeric
  slice. The case against it is entirely about coverage, reliability, and cost.

This comparison is **unaffected by the truncation correction below**: all four DeepSeek arms
completed all six documents, so they are scored on identical ground.

Implication for #361's DeepSeek decision: unlikely to be worth documenting both a thinking and
non-thinking DeepSeek recommendation. Pending the judge pass on the ~⅔ of key items with no
numeric anchor before that's final.

---

**Sonnet is clearly ahead of DeepSeek on this corpus — the earlier "ds-pro beats Sonnet" reading
was an artifact of a truncated run.** (Corrected 2026-07-27, #475.)

`bench-ex2-sonnet-med` **rate-limited after 4 of its 6 documents** — `ingest.log` ends
`INGEST rate-limited — 4 extracted, 0 skipped, 0 failed`, and only four `.watchdog/extracted/
*.json` artifacts exist. `score_arms.py` scores a missing document as a miss, so the whole-corpus
table credited Sonnet zero on two documents it never attempted. That produced the earlier
headline of ds-pro 92% vs Sonnet 90% on facts, which is not a real result.

Restricted to the **four documents every arm completed** (pension order, first report of the
monitor, both annual financial reports):

| Arm | Facts (binary) | Facts (sub-item) | must_not_miss (binary) | must_not_miss (sub-item) |
|---|---|---|---|---|
| `sonnet-med` | **94%** (30/32) | **84%** (58/69) | **89%** (16/18) | **94%** (34/36) |
| `ds-pro` | 91% (29/32) | 74% (51/69) | 78% (14/18) | 72% (26/36) |
| `ds-pro-think` | 88% (28/32) | 75% (52/69) | 83% (15/18) | 69% (25/36) |
| `ds-flash` | 81% (26/32) | 59% (41/69) | 78% (14/18) | 64% (23/36) |
| `ds-flash-think` | 75% (24/32) | 62% (43/69) | 72% (13/18) | 64% (23/36) |

Two things to read from this. **Sonnet leads on every metric**, and the gap widens under the
stricter scoring: binary credit (any sub-item hit counts the whole key item) flatters the cheap
arms, because a key item asking for four figures scores the same for one as for four. The
sub-item columns are the honest comparison. **The `must_not_miss` gap is the one that matters
journalistically** — 94% against 72% is not a wash, and `must_not_miss` is by definition the set
of items where a miss is a story-level failure, not a thinner note.

Caveats, both of which cut against over-reading the table:

- These are the **numeric-anchored items only** — roughly a third of the keys. The remaining ~140
  items have no numeric anchor and are unscorable offline; they still need the judge pass (#362).
  A cheap model that captures the figures but writes vaguer surrounding prose would look better
  here than it deserves.
- `score_arms.py` is **blob-level** — no citation provenance, so sibling documents can
  cross-credit. It ranks arms against each other; it is not an absolute recall number.

**What this does not settle.** It does not make Sonnet the answer — it makes Sonnet the *quality
ceiling measured so far*, at 2.07¢/page against ds-pro's 0.057¢/page. The models that would
actually decide the cost question (`haiku`, `gemini-flash`, `gemini-flash-lite`) have **never
been run**, and `sonnet`/`low` has never been tested. See #475.

---

**2026-07-29 — gpt-mini currently matches Sonnet-high's must_not_miss recall at under half the
cost; this is the first real inflection point the benchmark has found.** Full cross-arm picture
below, freshest data per arm (Gemini/OpenAI arms post-#490 sectioning fixes; Claude/DeepSeek arms
are the one existing run, pre-fix but not expected to be affected — see #490/D147). Facts/
must_not_miss are the **sub-item** aggregation (anchors matched / anchors present across all 6
documents' numeric-anchored key items — 80 fact-anchors, 47 must_not_miss-anchors), not
`score_arms.py`'s default flattering binary-per-item count — see the 2026-07-27 entry above for
why sub-item is the one to trust. Cost is the full 209-page corpus, real billed dollars
(`claude-api`/`openai`/`gemini`/`deepseek` backends, not agent-SDK list-price-equivalents).

| Arm | Facts (sub-item) | must_not_miss (sub-item) | Cost | Latency (summed) | Failures |
|---|---|---|---|---|---|
| `sonnet-high` | 92% (74/80) | 94% (44/47) | $1.193 | 676s | none |
| `gpt-nano` (low) | 91%* (73/80) | 94%* (44/47) | $0.198 | 773s | **1/6 docs (#505)** |
| `gpt-mini` (low) | 89% (71/80) | 94% (44/47) | $0.543 | 338s | none |
| `sonnet-med` | 90% (72/80) | 91% (43/47) | $1.115 | 601s | none |
| `sonnet-low` | 89% (71/80) | 83% (39/47) | $1.041 | 499s | none |
| `gpt-luna` (low) | 85% (68/80) | 83% (39/47) | $0.602 | 262s | none |
| `gemini-flash` (low) | 84% (67/80) | 74% (35/47) | $0.762† | 251s | none |
| `haiku` | 82% (66/80) | 70% (33/47) | $0.343 | 258s | none |
| `ds-pro` | 79% (63/80) | 74% (35/47) | $0.117 | 490s | none |
| `ds-flash-think` | 68% (54/80) | 55% (26/47) | $0.010 | 248s | none |
| `ds-pro-think` | 68% (54/80) | 68% (32/47) | $0.083 | 1225s | none |
| `ds-flash` | 62% (50/80) | 47% (22/47) | $0.031 | 120s | none |
| `gemini-flash-lite` | 57% (46/80) | 45% (21/47) | $0.099† | 107s | none |

*gpt-nano's numbers include full credit for a document it never extracted — see the scoring
artifact below. Every other row is a clean 6/6.

†Both Gemini costs are **understated floors**, not billed dollars. Gemini's OpenAI-compatibility
endpoint omits its thinking tokens from `completion_tokens`, so until #547 they were never priced
— even though Google bills them at the output rate. The gap scales with how hard the model thought
(on one high-effort call it was 15,137 unpriced tokens against 847 priced ones), so these
low-effort rows are the least-affected case and still wrong. Re-run both arms on post-#547 code
before using either figure to compare Gemini against another backend — the `gemini-flash` arm is
now `gemini-flash-low` in `benchmark.yaml` (same model, same pinned effort, renamed when the
effort ladder made the bare id ambiguous).

**gpt-mini (low effort) matches Sonnet-high's must_not_miss recall (94%) at 46% of the cost
($0.543 vs $1.193), full 6/6 coverage, zero failures.** Every cheaper-than-Sonnet arm tried before
this (Haiku, all four DeepSeek arms, both Gemini arms) traded down meaningfully on must_not_miss
(≤83%) to get there. gpt-mini doesn't. This is the strongest candidate so far for #361's "cheaper
than Sonnet without giving up the metric that matters" question — pending the judge pass below,
since the numeric slice is only a third of the keys and this is close enough (94% vs 94%) that the
unscored two-thirds could still separate them.

**Scoring artifact: gpt-nano's numbers double-count a document it never processed.**
`bench-ex-gpt-nano` failed post-flight (`morgue_entity_id is missing or empty`, #505, open) on
*Laurentian First Report of the Monitor.pdf* — no `.watchdog/extracted/*.json` exists for it. But
`score_arms.py` is blob-level (scores against the concatenated text of every extracted document in
the vault, with no per-document attribution), and this corpus's six documents are all about the
same case (Laurentian University's CCAA proceeding), so the missing document's 8 key items
(F3/F4/F13/F18/F19/M1/M3/M9) get credited anyway from sibling documents gpt-nano *did* extract —
confirmed directly, gpt-nano's per-item hit counts on those 8 items are identical to
`sonnet-high`'s. Net effect on the ranking above is small (excluding those 8 items moves gpt-nano
to roughly 92%/93%), but the row is really "5/6 documents, cross-credited to look like 6/6," not a
clean 6/6 the way every other arm here is. Don't extrapolate this cross-crediting behavior to a
less-redundant corpus, and don't ship `gpt-nano` as a default while #505 is open — a model that
can't reliably resolve which entity a document is about is a production gap, not a benchmark
artifact.

**The DeepSeek thinking-cost story doesn't hold on this run.** The 2026-07-27 entry above found
`ds-pro-think` costing +20% over `ds-pro` on `bench-ex2-*`. This run's live `bench-ex-*` vaults
show the opposite: `ds-pro-think` at $0.083 vs `ds-pro`'s $0.117 (thinking cheaper, not costlier),
and `ds-flash-think` at $0.010 vs `ds-flash`'s $0.031 (also cheaper). Latency still tracks the old
finding (2.5× for pro-think, close to the earlier "2.6×"). Not independently re-verified beyond
confirming the report matches the live vault — worth a token-usage hand-check before citing
DeepSeek thinking-mode cost either direction. The coverage/reliability case against thinking mode
is unaffected either way: `ds-pro-think` and `ds-flash-think` are still the two costliest-latency
arms in the table above.

**Backend note, not part of the model decision:** `bench-ex-batch-sonnet-med` (claude-batch,
$0.548) scores identically to `sonnet-med-api`'s numeric slice (100%/96% binary) at roughly half
`sonnet-med-api`'s $1.155 — consistent with Anthropic's standard 50% batch discount — but trades
away synchronous turnaround (submit-and-collect-later, see #475/#466). `sonnet-med-sdk`
(claude-agent-sdk, $2.017, 79% must_not_miss) is both pricier and worse-scoring than
`sonnet-med-api` ($1.155, 88%) at the same model/effort — the harness overhead #475 flagged, not a
quality signal about Sonnet itself.

**Still needed before #361/#215 close:** a judge-model pass on the ~140 (of 203) key items with no
numeric anchor — none exists yet, for any arm, OpenAI included. `score_arms.py` only rewards
getting the right figure; a model that hits every number but writes thin, ungrounded surrounding
prose would look identical to one that doesn't. Given how close `gpt-mini`/`sonnet-high`/
`sonnet-med` now sit on the numeric slice, the judge pass is what actually separates them — not a
"nice to have" at this point, the next required step before shipping a default.

**A composite cost/quality score needs a quality floor, not a raw ratio — a raw ratio picks
`ds-flash-think`.** Dividing a blended quality score (0.6·must_not_miss + 0.4·facts) by cost/page
ranks `ds-flash-think` as "best value" by a 36× margin over `sonnet-high` ($0.0048¢/page,
"ratio" 12,582 vs 163), purely because it's cheap — despite sitting at 55% must_not_miss. A pure
ratio treats a 30-40 point recall gap as something any low enough price buys back, which is the
wrong trade for a tool whose job is not missing things in a legal filing. The defensible version
gates on must_not_miss first (floor: 83%, the natural gap in this run — the next arm down sits at
74%) and only optimizes cost among arms that clear it:

| Arm | Cost/page | Facts | must_not_miss |
|---|---|---|---|
| `gpt-nano` (low) | 0.0947¢ | 91% | 94% | *caveated — cross-credited, see above*
| `gpt-mini` (low) | 0.2598¢ | 89% | 94% |
| `gpt-luna` (low) | 0.2880¢ | 85% | 83% |
| `sonnet-low` | 0.4981¢ | 89% | 83% |
| `sonnet-med` | 0.5335¢ | 90% | 91% |
| `sonnet-high` | 0.5708¢ | 92% | 94% |

Haiku, both Gemini arms, and all four DeepSeek arms fall below the 83% floor and are out
regardless of price. `gpt-mini` is the standout: cheapest *uncaveated* arm on the list, tied with
`sonnet-high` on must_not_miss (94%) at 46% of its cost. `gpt-luna` is dominated outright —
worse than `gpt-mini` on both cost and quality, no reason to prefer it. The ranking is not
sensitive to the exact floor value — moving it to 91% drops `gpt-luna`/`sonnet-low` but leaves the
same winner. It *is* sensitive to gating on must_not_miss rather than facts, and to trusting the
numeric-anchor slice at all — the judge-pass caveat above still applies on top of this.

**2026-07-29 — the qualitative judge pass (the 140 numeric-anchor-free items) is done, judged in-session by Sonnet rather than a separate paid Opus/Gemini pass — and it surfaced a silent extraction failure in `sonnet-high` that the numeric scorer never caught.** Artifacts: `benchmarks/qualitative/` (blinded packets, per-document judgments, the label mapping, and the aggregated tally).

**Methodology, and a deliberate deviation from the written protocol.** BENCHMARKING.md/`keys/README.md` call for an Opus judge (fine for the Claude-vs-Claude leg) plus a non-Anthropic cross-check (Gemini) for the `gpt-mini`-vs-Claude legs. Before spending anything, Tom was given a concrete estimate — ~810K input tokens across 12 Opus+Gemini calls, roughly $4-8 — and asked to approve it. His answer was to skip the paid judge calls entirely and have this session (Sonnet) do the judging directly instead: "I'm not concerned about the bias effect that much. Just be honest... keep it blind for them (no information on model name)." So: six subagents (one per corpus document, inheriting Sonnet, no model override) each read that document's unscorable key items plus the three arms' extracted JSON blinded as X/Y/Z — the real arm names were withheld, and the X/Y/Z-to-arm mapping was randomized independently per document — and returned a three-tier verdict (verbatim / credited normalization / ungrounded) per item per arm. No Opus or Gemini API spend occurred. The first document's verdicts were hand-checked against the raw vault files before trusting the rest (#362's required sanity check) — that check is what surfaced the finding below.

**`sonnet-high` produced an essentially empty extraction of the Initial Order document (0/26 qualitative items credited) — a real, billed, silent failure invisible to `score_arms.py`.** Confirmed directly against the vault files, not just the subagent's say-so: `bench-ex-sonnet-high`'s extracted JSON for that document has `key_facts: []`, one entity, and a placeholder `summary`; `ingest.log` logs it as a plain `OK` with no coverage-gap warning; the pipeline billed `$0.032` against `8690` input tokens of substantive chewed OCR text (page 1 alone contains the full operative "insolvent," CCAA, and stay-of-proceedings language). `sonnet-med` (20KB extracted) and `gpt-mini` (41KB extracted) both extracted normally from the identical chewed input. This is the same masking mechanism as the `gpt-nano` scoring artifact recorded above (#505) — blob-level, cross-document credit from the other five same-case documents papers over a missing one — except here it's silent: no post-flight error, no `coverage_gap` flag, just an `OK` line and an empty result. `sonnet-high` is the current shipped default's basis, so this is a production-readiness concern in its own right, independent of how the three-arm comparison shakes out, and probably warrants its own tracked issue before `sonnet-high` (or `sonnet` generally) is confirmed as anything.

**With that document included, `sonnet-high` is *not* the leader on the qualitative slice — it's tied-last on `must_not_miss` and behind both other arms on facts:**

| Arm | Facts (hit/total) | must_not_miss (hit/total) |
|---|---|---|
| `gpt-mini` | 75% (65/87) | 57% (30/53) |
| `sonnet-med` | 70% (61/87) | 62% (33/53) |
| `sonnet-high` | 67% (58/87) | 57% (30/53) |

**Excluding the Initial Order document** (isolating the one-document wipeout to see what `sonnet-high` would otherwise score) puts `sonnet-high` back in front, consistent with its numeric-slice standing:

| Arm | Facts (hit/total) | must_not_miss (hit/total) |
|---|---|---|
| `sonnet-high` | 83% (58/70) | 68% (30/44) |
| `gpt-mini` | 80% (56/70) | 59% (26/44) |
| `sonnet-med` | 76% (53/70) | 66% (29/44) |

Per-document win pattern (hit % of that document's qualitative items): `sonnet-high` wins clearly on `annual-financial-report-19-20` (86 vs 71 vs 52), `pension-order` (88 vs 83 vs 75), and `prefiling-report-monitor` (87 vs 84 vs 77, `gpt-mini` edges `sonnet-med` here); `gpt-mini` wins on `annual-financial-report-20-21` (87 vs 73 vs 67 — `sonnet-high` placing *last*), `first-report-monitor` (61 vs 52 vs 52, tied with `sonnet-high`), and, narrowly, `initial-order` itself (50 vs 46 vs 0). `sonnet-med` never outright wins a document on this slice — its best showings are second place.

**What this does and doesn't settle for #361/#215.** It does not hand `sonnet-high` a clean win the way the numeric-slice table implied it might — on the honest, full accounting (which is what a customer would actually receive), `gpt-mini` and `sonnet-med` both edge it out on at least one qualitative metric, and the gap that briefly favoured `sonnet-high` on `must_not_miss` (94% vs 94%, see the entry above) does not carry over to the qualitative slice at all. `gpt-mini` remains the standout on cost (46% of `sonnet-high`'s price) with no qualitative-slice quality collapse to justify ruling it out. What's newly **not** settled: `sonnet-high`'s silent Initial-Order failure needs investigation (is it a one-off, or does it reproduce?) before treating `sonnet-high`/`sonnet` as a safe default at all, independent of the `gpt-mini` question. Recommend opening a tracked issue for that before closing #361/#215 outright.

## Corrections logged along the way

- The original `bench-ex-sonnet-high`/`bench-ex-sonnet-med` vaults (run 2026-07-15, before #403's
  `dig`/`bark` split shipped) had no `.watchdog/extracted/*.json` and did not score through
  `score_arms.py`. Re-run under `bench-ex2-*` names instead.
- 2026-07-26 (#466): the whole benchmark harness moved from a hand-run protocol
  (`BENCHMARKING.md` + one-off scripts) to `run_benchmark.py`, driven by `benchmark.yaml`. Arm
  names changed again as part of that move (`bench-ex2-*` → `bench-ex3-*`) — treat any
  `bench-ex2-*`/`bench-fn-*` figures above as historical, not directly comparable to a fresh
  `bench-ex3-*`/`bench-fn-*` run without re-checking the config the new run actually used
  (`benchmarks/<run-id>/config.yaml`).
- 2026-07-27 (#475): the Sonnet-vs-DeepSeek comparison above was corrected for
  `bench-ex2-sonnet-med`'s truncated run. **General lesson for reading any arm table: check the
  arm's `ingest.log` tail and its `.watchdog/extracted/` count against the corpus size before
  trusting a whole-corpus percentage.** A rate-limited arm scores as a bad arm, and nothing in
  the summary output distinguishes the two. `run_benchmark.py`'s `REPORT.md` records retries and
  failures per arm, which helps, but a rate-limit stop is a clean early return, not a failure.
- 2026-07-27 (#475): every Claude cost figure recorded before this date was inflated by a fixed
  ~11.2K tokens per call — the full Claude Code tool suite was being defined in every
  agent-SDK request (D145). The effect is **per call, not per page**, so it distorts arms with
  many small documents far more than arms with few large ones, and it inflates Claude arms only
  (DeepSeek/Gemini/OpenAI backends were never affected). Do not compare a pre-D145 Claude cost
  against a post-D145 one. The recall figures above are unaffected — this changed the bill, not
  the output.
- 2026-08-05: `extractor_sweep` was regrouped by provider and every arm's effort pinned
  explicitly, which **renamed most arm ids**. Historical rows above use the old names; the
  mapping is `sonnet-{high,med,low}` → `sonnet-4.6-{high,med,low}` (bare `sonnet` already
  resolved to 4.6, so the model is unchanged), `gpt-{nano,mini,luna}` → `gpt-{nano,mini,luna}-low`
  (all three were pinned low), `gemini-flash` → `gemini-flash-low`, and
  `gpt-mini-{verify,noverify}` → `gpt-mini-low-{verify,noverify}`. Nothing was dropped and the
  model/effort combinations behind those rows are unchanged, so the figures remain comparable —
  only the labels moved. One genuine change: `gemini-flash-lite` now pins `low` instead of
  leaving effort unset, so its historical row measured Google's implicit default and a fresh run
  will not. Tooling is unaffected either way — `cost_reference` matches archived runs on
  model/effort/backend, never on arm id. BENCHMARKING.md's Step 3 walkthrough is the
  pre-#466 hand-run protocol and keeps its own older vault names throughout — it documents
  a different workflow, not these arms.
- 2026-08-05: `benchmarks/` was reorganised. The three corpora moved under one parent
  (`corpus/` → `corpora/extract/`, `classify-corpus/` → `corpora/classify/`,
  `sdk-check-corpus/` → `corpora/sdk-check/`); the judge pass moved to `qualitative/`; and run
  directories now land in the gitignored `benchmarks/runs/`. **Individual runs are no longer
  committed** — a run's figures are only valid against the commit that produced them, and a
  committed run keeps looking authoritative long after it stops being true (every correction in
  this list is an instance of that). This file is the durable record. Archived runs sitting
  directly under `benchmarks/` from before the move are still found by the cost preview, which
  reads both layouts.
