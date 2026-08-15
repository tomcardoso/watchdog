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

**As of #559, this class of mistake is caught automatically** rather than needing a human to
notice a suspiciously bad score and go check `ingest.log`. A rate limit now surfaces distinctly
from a plain Ctrl-C (`⚠ rate-limited after 4/6 docs`, an `errors.log` entry, `run.json`'s
`rate_limited`/`partial` fields), and `score_arms.py` restricts each vault's denominator to the
documents it actually extracted instead of scoring a missing one as a miss — so a table built
today from an arm like `bench-ex2-sonnet-med` would report its real 4-document recall rather than
a corpus-wide figure dragged down by two documents it never opened. The hand-restricted table
below is the manual version of what the harness now does by construction.

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

**2026-08-03 (#509) — the qualitative judge pass on the three re-run `gpt-luna` effort tiers (low/med/high) shows a large, monotonic effort-vs-recall curve, but `gpt-luna-high` does NOT clearly beat `sonnet-high` once cost, latency, and the known `sonnet-high` outlier are accounted for.** Artifacts: `benchmarks/2026-08-03-judge-qualitative-luna/` (blinded packets, per-document judgments, mapping, aggregated tally) — same protocol as `benchmarks/2026-07-29-judge-qualitative/`: six subagents (one per corpus document, inheriting Sonnet, no model override) each judged that document's numeric-anchor-free key items against the three arms blinded as X/Y/Z, with the label mapping randomized independently per document and withheld from the judging subagents. Extraction sizes scaled sensibly with effort (30/40/53 `key_facts` for low/med/high on the Initial Order document, checked directly against the vault files) — no repeat of the silent-empty-extraction artifact found in `sonnet-high` on 2026-07-29.

| Arm | Facts (hit/total) | must_not_miss (hit/total) |
|---|---|---|
| `gpt-luna-low` | 63% (55/87) | 53% (28/53) |
| `gpt-luna-med` | 77% (67/87) | 64% (34/53) |
| `gpt-luna-high` | 83% (72/87) | 64% (34/53) |

Excluding the Initial Order document (same isolation the 2026-07-29 table used, since low effort was noticeably sparser there — 14 `key_facts` vs. 24/26 for med/high — though not a silent-failure artifact, just less thorough):

| Arm | Facts (hit/total) | must_not_miss (hit/total) |
|---|---|---|
| `gpt-luna-low` | 69% (48/70) | 55% (24/44) |
| `gpt-luna-med` | 79% (55/70) | 64% (28/44) |
| `gpt-luna-high` | 84% (59/70) | 68% (30/44) |

**Cost and latency, pulled from each vault's `usage` registry (full six-document corpus, api-key auth, so directly comparable):**

| Arm | Cost | Wall time |
|---|---|---|
| `gpt-mini` | $0.54 | 183s |
| `gpt-luna-low` | $0.60 | 170s |
| `sonnet-high` | $1.19 | 247s |
| `gpt-luna-med` | $0.74 | 206s |
| `gpt-luna-high` | $1.59 | 647s |

**Putting quality and cost together changes the conclusion.** On the raw numbers `gpt-luna-high` looks like the new leader — ahead of `gpt-mini` (75%/57%), `sonnet-med` (70%/62%), and `sonnet-high` (67%/57%) on both metrics. But `sonnet-high`'s 67%/57% includes its own known silent-failure document (0/26 on Initial Order, logged above on 2026-07-29); the honest, outlier-corrected comparison over the same five documents is `sonnet-high` 83%/68% vs. `gpt-luna-high` 84%/68% — a **tie** on quality, while `gpt-luna-high` costs 34% more ($1.59 vs. $1.19) and takes 2.6x longer (647s vs. 247s). That is not a win for `gpt-luna-high`. `gpt-luna-med` is the more interesting result: cheaper than `sonnet-high` at $0.74, and ahead of `gpt-mini` on must_not_miss (64% vs. 57%) for about 37% more cost than `gpt-mini`'s $0.54. `gpt-mini` remains the cost floor with quality comparable to `sonnet-high`'s honest number. **This does not settle #361/#215 in `gpt-luna`'s favour** — it reopens the question for `gpt-luna-med` specifically (cheaper than Sonnet, a real must_not_miss edge over `gpt-mini`) but rules out `gpt-luna-high` as a clear pick on the numbers gathered so far. `xhigh`/`max` are not yet run (prerequisite #518).

One correction to the #509 planning comment: the shared-output-ceiling truncation risk it flagged for `high`/`xhigh`/`max` (the extractor's 16K `_TASK_MAX_TOKENS` ceiling shared between reasoning and JSON) did not materialize here, and checking why turned up a wrinkle in that risk assessment — `output_ceiling_for_sectioning` (`model_client.py`) resolves OpenAI reasoning models to a separately raised 48K wire ceiling (`_OPENAI_REASONING_MAX_TOKENS`, D354), not the flat 16K; several `gpt-luna-high` section calls logged 16-18K output tokens with zero retries or truncated `finish_reason`s, consistent with running comfortably inside the real 48K ceiling. The 16K figure only bounds what sectioning plans the visible JSON against, not the actual wire limit.

---

**2026-08-06 — reasoning effort above `low` does not buy extraction quality on this corpus, and
costs up to 10× more.** First run with `reasoning_tokens` recorded per call (#354/#547), so
chain-of-thought could finally be separated from the visible answer. `gpt-5.4-mini`, corpus-v1,
three arms differing only in `extractor_effort`:

| effort | facts | must_not_miss | cost | failures |
|---|---|---|---|---|
| `low` | **39/39 (100%)** | **23/24 (96%)** | **$0.53** | 0 |
| `medium` | 36/39 (92%) | 20/24 (83%) | $2.47 | 0 |
| `high` | 38/39 (97%)\* | 21/24 (88%)\* | $5.17 | 2 calls, 1 document lost |

\* **`high`'s figures are inflated by the same cross-crediting artifact recorded above for
`gpt-nano` and `sonnet-high`.** It aborted *Laurentian Pre-Filing Report* outright (reasoning
starvation — the full 96,000-token envelope spent thinking, zero visible output, twice, including
after #540's re-split retry) and has only 5 extracted artifacts against the others' 6. Yet all six
`prefiling-report-monitor` key items still score as hits, credited from the five sibling documents
that share the same figures. On a corpus without that redundancy those items would simply be lost.

**`medium` placing last on both metrics is the load-bearing result**, because it cannot be
explained away as a failure artifact — it completed all six documents. More thinking produced less
complete extraction, not more.

**Why the visible answer doesn't change with effort.** Fitting visible output (`output_tokens −
reasoning_tokens`) against input gives a marginal rate of **0.199 / 0.213 / 0.200** at low/medium/
high — flat. What scales is reasoning alone: 4% → 87% → 94% of all output tokens, reaching
`19,837 + 2.490 × input` at high, i.e. a ~$0.09-per-call thinking floor before any document text is
read. (That floor is also why any "split the work into more calls" strategy is the wrong direction
on a reasoning model, and why schema-partitioning is the worst of them — it repeats the whole input
per pass, so reasoning grows *linearly* in passes where sectioning only re-pays the floor.)

**How much to trust this.** The quality ranking is soft: only ~⅓ of key items carry a numeric
anchor (140 unscorable, judge pass not run for these arms), and low-vs-medium is 3 items out of 39
— not enough to claim `low` is genuinely *better* than `medium`. The decision is robust anyway,
because it doesn't rest on the ranking: `low` is 10× cheaper than `high`, 5× cheaper than `medium`,
and the only arm that never failed. It wins whether or not the quality gap is real.

**Scope — one model, one corpus, six documents.** `extractor_effort`'s shipped default is `medium`
(D26), and `setup.py`'s help text justifies it with "benchmark testing found it ties `high` on
recall while cutting extraction output/cost substantially." That specific claim does not hold here:
`medium` didn't tie `high`, it came last. But the default is cross-backend and the reasoning-cost
curve measured here is very OpenAI-shaped, so this is **not** on its own grounds to change it — a
Claude effort ladder would be needed first. Treat it for now as: do not pin `high` on an OpenAI
reasoning extractor, and treat the help text's recall claim as unverified for OpenAI.

Related: #542 (the section-size constant, measured from the same run), #558 (starvation is a
distinct failure from truncation and the re-split recovery cannot fix it).

---

**2026-08-08 — the qualitative judge pass on the three `gpt-mini` effort tiers reverses the entry
above: on the judged slice `medium` wins both metrics, not `low`. But once the document `high`
lost is set aside, all three arms are within noise of each other — what separates them is
reliability, not comprehension.** Artifacts:
`benchmarks/2026-08-08-judge-qualitative-mini/` (blinded packets, per-document judgments, mapping,
raw pre-conversion judgments in `raw-flat/`). Same protocol and same key items as the 2026-07-29
and 2026-08-03 passes — six Sonnet subagents, one per document, arms blinded X/Y/Z with the
mapping randomized independently per document. **This is the qualitative slice; the entry above is
the numeric-anchored slice of the same extractions.** No new extraction was run: the judging reads
the vaults the 2026-08-06 arms left behind.

| Arm | Facts (hit/total) | must_not_miss (hit/total) | Cost |
|---|---|---|---|
| `gpt-mini-low` | 75% (65/87) | 57% (30/53) | $0.53 |
| `gpt-mini-med` | **77% (67/87)** | **68% (36/53)** | $2.47 |
| `gpt-mini-high` | 51% (44/87) | 47% (25/53) | $5.17 |

Excluding *Laurentian Pre-Filing Report* — the document `high` lost to reasoning starvation, and
the single largest item block in the corpus (31 of 140):

| Arm | Facts (hit/total) | must_not_miss (hit/total) |
|---|---|---|
| `gpt-mini-low` | 69% (47/68) | 54% (22/41) |
| `gpt-mini-med` | 71% (48/68) | 59% (24/41) |
| `gpt-mini-high` | 65% (44/68) | **61% (25/41)** |

**Read the second table for quality and the first for what a user receives.** `high` is last
overall only because it lost a document; on the five it completed it leads `must_not_miss`. Unlike
`score_arms.py`, this pass charges a lost document to the arm that lost it rather than crediting
the items from siblings — which is why `high`'s honest figures here (51%/47%) are so much worse
than the numeric slice's (97%/88%).

**The low-vs-medium gap is real but half of it is one document.** Medium's 11-point
`must_not_miss` lead shrinks to 5 points once the pre-filing report is excluded (it scored 12/12
there against low's 8/12). The direction replicates the 2026-08-03 `gpt-luna` pass, which found
the same +11 low→med move on `must_not_miss` — two models, two independent passes, same sign — so
the effect is probably real, but at 4.7x the cost it is not obviously worth buying.

**Extraction volume is not coverage.** On the pre-filing report `low` emitted 159 `key_facts` to
`medium`'s 90 and still scored worse on the keyed items (8/12 vs 12/12). Fact count is not a proxy
for recall and should not be used as one.

### What the misses actually are — and why model choice will not fix them

`must_not_miss` items caught by **at least one** arm: 43/53 (81%). Missed by **every** arm:
**10/53 (19%)**. That 19% is the current pipeline's ceiling: no effort level and no backend
reaches it, so the low/medium/high argument is a fight over the ~13-point band between 68% and
81% while a fifth of the keyed items sits off the table for reasons unrelated to model capability.
**Model selection is not where the remaining recall is.**

Reading the missed items individually (rather than counting them) gives three causes, none of
which is reasoning effort:

**1. The extraction records end-states and discards transitions.** `first-report-monitor` M6 asks
for the Administration Charge tripling ($400K → $1.25M) and the Directors' Charge more than
doubling ($2M → $5M). The document states this in one clause — *"an increase in the Administration
Charge from $400,000 to $1,250,000; ... the Directors' Charge from $2,000,000 to $5,000,000"* — so
no arithmetic and no cross-document lookup is involved. All three arms captured the **new** figure
and dropped the **prior** one ("increase the administration charge to $1,250,000"). A `key_fact` is
naturally written as a current-state assertion, so the *change* — which is the newsworthy part —
is normalised away. Any keyed item phrased as a delta is systematically exposed to this.
(`medium` additionally recorded the Directors' Charge as "$2 million", the DIP-priority sub-cap,
rather than the $5M total; `high` got the split right. A substantive error, not just an omission.)

**2. Salience, not context or linkage.** `pension-order` M6 asks that Derek Harland be noticed in
two capacities — deponent of the Affidavit of Service (p. 1) and counsel at TGF (p. 5). The
document is five pages and ran as a **single whole-document call** (8,751 input tokens): both
mentions were in the same prompt, with no sectioning and no context pressure. The extraction
mentions Harland **zero times** — not in `key_facts`, not in `entities`. This is not a cross-page
reasoning failure; the name was simply never judged worth recording. Procedural boilerplate that
carries a signal to a reporter is invisible to a salience judgment tuned for operative content.

**3. Curator's angle vs. stated content.** Several items encode a characterisation the document
never uses ("triples" appears nowhere in the First Report) or reach across documents (M8's caveat
"repeated from the Pre-Filing Report" cannot be satisfied by a single-document pass, though the
erroneous affidavit date it hinges on *is* in the document).

All three point at the skill/schema and the salience instruction, not at the model. Worth weighing
against #551 and #555, which both assume the extraction-benchmark axis is where the headroom is.

### The `must_not_miss` key items are weaker instruments than the `facts` items

Found while reading the misses, and it bears on every figure in the qualitative column of this
file, including the 2026-07-29 and 2026-08-03 passes:

- **No quotes.** In `benchmarks/keys/*.yaml`, every `facts` entry carries a verbatim `quote`
  (21/21 in `first-report-monitor.yaml`); **no `must_not_miss` entry carries one** (0/10). They
  have `id`, `item`, `page`, `paragraph` and a `why`, but nothing anchoring the assertion to
  document text. A `facts` item is self-verifying; a `must_not_miss` item can only be checked by
  going back to the PDF by hand, so a mis-keyed one is invisible.
- **Bundled sub-facts, graded all-or-nothing.** M6 bundles three claims (admin charge triples,
  directors' charge more than doubles, both rank ahead of every secured creditor). An arm holding
  two of three lands on the judge's discretion, and judges in this pass flagged exactly that
  ("credited for capturing half a two-part item"). This makes `must_not_miss` both noisier and
  more pessimistic than the facts column, and the two are not on the same footing.

Neither defect explains away the misses above — the dropped "$400,000" and the absent Harland are
real regardless of how the key is written. But the `must_not_miss` percentages should be read as
a stress test with a soft denominator, not as a calibrated recall figure. Adding quotes and
splitting bundled items would make the next pass measurably sharper.

**Caveats.** One model, one corpus, six documents; the excluding-table differences (2–7 points on
n=68/41) are within noise and should not be used to rank the arms. The arms' extraction predates
the provenance stamping added in #554 (merged 22:58 on 2026-08-05, after all three arms ran at
21:45–22:45), so the exact extraction commit is not recorded — only the date. The judging ran at
`5819dfa`.

**Bearing on #361.** `low` remains defensible on cost and `high` is ruled out on this backend
(cost plus the starvation loss), but the earlier reading that effort above `low` buys nothing does
not survive the judged slice. The open question is whether medium's small edge is worth 4.7x, and
that is a product call rather than a measurement gap.

### The verification pass doubles cost and adds 220 facts without moving `must_not_miss` (2026-08-09)

The A/B D172 shipped `verify_extraction` off by default pending. Run `2026-08-09-1523`,
`gpt-5.6-luna` at `low`, full six-document corpus, **scored under `keys-v2`** (#573) — so these
figures are not comparable with the v1-scored rows above:

| Arm | facts | `must_not_miss` | `key_facts` staged | Cost | Latency |
|---|---|---|---|---|---|
| `gpt-luna-low-noverify` | 90% (35/39) | **77% (36/47)** | 311 | $0.110 | 287s |
| `gpt-luna-low-verify` | 95% (37/39) | **79% (37/47)** | 537 | $0.220 | 415s |

**Rescored under #591's fixed scorer, in two passes** (2026-08-10) — the original figures
(97%/81% and 100%/79%) were inflated by pre-#591 bugs, and the first rescore pass undercounted
before a second fix landed:

1. A key item was matched against the whole vault's extraction text rather than its own
   document, so an anchor absent from the right document could still be credited from an
   unrelated one elsewhere in the corpus.
2. A rounding chain in the thousands-to-millions variant generator could collapse a 5+ digit
   anchor to a bare 1–3 character string that then matched almost any digit run in the corpus
   (`78003` → `"78"`, `1000` → `"1"`).
3. A plain-dollar anchor (a court order's `$5,000,000`, not a thousands-denominated financial
   figure) had no matching conversion at all — `variants()`'s thousands-to-millions logic only
   covers an anchor already denominated in thousands, so an extraction that correctly wrote
   "$5 million" scored a miss purely on transcription format. Tom's ruling on this: the benchmark
   scores on numeric *value*, not on transcription format — "$5 million" is a hit against a
   $5,000,000 anchor because it is the same number. `millions_prose()` now generates the "N
   million"/"N.Nm" forms for a whole-dollar anchor ≥ $1,000,000, always with the word "million" or
   an "m" suffix directly adjacent to the digits so a bare short token is never produced.

Bugs 1 and 2 inflated `noverify` more than `verify` — most of the ten counsel-of-record items
across the two court orders round-tripped through it, and now resolve consistently instead of
splitting on which arm's cross-document noise happened to line up. Bug 3 affected `noverify`
only in this run — `first-report-monitor:F3/F4/M6.1/M6.2` (the Directors' Charge, independently
confirmed captured as "$5 million"/"$2 million" by issue #572) and `prefiling-report-monitor:M7/
M15.3` flip from miss to hit; `verify`'s own extraction phrased the same figures differently and
was unaffected either way (see #591).

The pass still added **220 facts** — a 71% larger ledger — for **exactly double the cost** and
45% more wall-clock. Under the corrected scorer the `must_not_miss` gap between the two arms
narrowed to +1 item — close to the original −1, just with the composition corrected (the
counsel-of-record items no longer split on coincidence, and both arms' whole-dollar figures now
score on value). This is still a single run of each arm, and #581 found up to a 3× spread in
facts extracted between independent samples of the *same* arm, so a one-item gap between two
single runs of *different* arms isn't enough on its own to call either way. The original
conclusion stands: **no evidence of a recall gain that would justify 2× cost.**

**The suppression thresholds are too loose.** Across 12 verify calls: 220 candidates added, **1**
suppressed as a duplicate. D172 picked Jaccard 0.75 / containment 0.9 by hand and flagged that
"only a real run will say which way they are wrong" — a 0.5% suppression rate is the answer.

### The 220 additions, graded: 41% material, and duplication is not the problem (2026-08-15)

All 220 additions from the run above were graded by hand through `verifier_precision.py` — the
instrument written for this in #535 and never run until now. No model calls; the judge reads the
packets. Grades are `grounded_material` (supported and worth a reporter's attention, not already
in the extractor's list in any wording), `grounded_trivial` (true and supported, but a restatement
or too minor to be worth a line) and `unsupported`.

| Document | material | trivial | unsupported | precision |
|---|---|---|---|---|
| Annual-Financial-Report-19-20 | 15 | 12 | 0 | 56% |
| Annual-Financial-Report-20-21 | 28 | 49 | 1 | 36% |
| Initial Order | 3 | 5 | 0 | 38% |
| First Report of the Monitor | 10 | 15 | 0 | 40% |
| Pre-Filing Report | 33 | 45 | 0 | 42% |
| Pension Order | 2 | 2 | 0 | 50% |
| **All** | **91** | **128** | **1** | **41%** |

**Grounding is not the failure.** Every one of the 220 quotes appears verbatim in its document,
218 of them on the cited page. The single `unsupported` grade is a scrambled-infographic page
where two adjacent KPI tiles were merged into one false figure.

**Duplication is not the failure either, and this is the finding that matters.** Of the 128
trivial additions only 73 restate a fact the extractor already had; the other 55 are distinct,
correctly grounded, and worthless — standard-form CCAA clauses (aid-and-recognition, liability
protection, effectiveness), accounting-policy notes, cash-flow-forecast mechanics. No
near-duplicate guard of any design can suppress those, because they are not duplicates. And the
73 that *are* restatements have a median containment of 0.39 against their nearest existing fact:
these are paraphrases, and token-set overlap cannot see a paraphrase. The two grade classes are
barely separable on word overlap at all — median containment 0.27 for material against 0.33 for
trivial.

**So the thresholds were wrong, but retuning them is a small lever.** Swept through
`_is_restatement` itself against the grades, document-scoped: 0.9 catches 3, 0.6 catches 11, and
nothing material is lost anywhere down to 0.5 (the numeric carve-out is what makes a bar that low
safe — a material fact at high word overlap almost always carries a figure the matched fact
lacks). Shipped at 0.6, with the strict bar retained below 8 content tokens where the ratio is
too coarsely quantized to mean anything.

**A second defect, mechanical and larger than the thresholds.** `merge_candidates` compared each
candidate against *the current section's* facts, while the ledger is the whole document's.
Section ranges overlap by a page, so the same content is offered twice by construction. All three
additions in the final ledger that `_is_restatement` scores as duplicates — including a
word-for-word restatement of an actuarial-valuation fact at containment 0.93 — got there this
way: the rule fired correctly and never saw the pair. Fixed by threading earlier sections' facts
into the comparison (#589).

**Both fixes together, replayed against this run: 11 of 220 suppressed, all trivial, none
material.** The staged ledger goes 537 → 526 and precision 41% → 44%. That is the honest size of
the dedup lever. The ledger is still 69% larger than the noverify arm's 311 for no measured recall
gain, and closing that gap means changing what the verifier considers worth reporting, not how
duplicates are detected.

**This converges with #581.** Five independent samples produced 3× the distinct facts and
recovered none of the hard items; the verifier produced 220 extra facts and recovered none
either. Two unrelated mechanisms for generating more candidates, both yielding volume and neither
yielding the items that matter. Consistent with the `annual-financial-report-19-20:M9` dissection
(#580): the binding constraint is the materiality criterion, and the verifier inherits it — its
prompt deliberately reuses `extract_instructions.md`, so it applies the same bar that caused the
miss.

**Still unmeasured on Anthropic.** `sonnet-4.6-med-verify` has never run. D172's cost case rests
on the prompt cache, which per D181 works on `claude-api` and cannot work on OpenAI — so the
economics there are a genuinely different product and this result should not decide that default.

**Cache behaviour, flagged not concluded.** First full-corpus run since #577. The noverify arm
read 100% cached on `extract` and 32.2% on `extract-section`; the verify arm read **0% on every
task**, including its own 15 `verify` calls, which share a ~4,000-token instructions+skill prefix
and a stable `prompt_cache_key` and should have warmed up internally. Arm ordering confounds it —
the verify arm ran first and cold, the noverify arm second and warm — so this cannot be
attributed to the verification pass. But one mechanism is worth checking: #577 derives the cache
key from the *first* breakpoint, so `extract` and `verify` share a key while their differing
`response_format` schemas mean they can never share a cache entry. Same routing slot, mutually
unusable contents. See #586 for the related unexplained `digest` zero.

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
- 2026-08-05: **the qualitative table above does not reconcile with its own artifacts.** Re-running
  `qualitative/aggregate.py` over the committed judgments reproduces `summary.json` exactly — 87
  fact items and 53 `must_not_miss` items, 140 judgments total, which is the "~140 numeric-anchor-
  free items" the entry describes. But the table reports denominators of 70 and 44 (114 items), so
  26 judged items are excluded by some rule that was never written down. The numerators match
  (58 and 30 for `sonnet-high`), so this changes the percentages, not the ranking: `sonnet-high`'s
  facts figure is 83% against the table's denominator and 67% against the tool's. **Trust neither
  percentage until the exclusion is identified**; the tool's is at least reproducible from
  committed files, which the table's is not. The ordering of the three arms is unaffected either
  way, so the conclusions drawn from it still stand. `aggregate.py` now takes its denominator
  from the answer keys rather than from the judgments that came back, so a keyed item nobody
  graded counts as a miss and is reported by id — adding documents or key items raises the bar
  for later passes automatically, and a shortfall like this one can no longer pass unremarked.
- 2026-08-08: **the 2026-08-06 entry's headline — "reasoning effort above `low` does not buy
  extraction quality" — is not supported.** It rests on the numeric-anchored slice alone, where
  `low` scored 39/39 and the metric is saturated: an arm cannot be ranked above a ceiling its
  rival already hit. The judged slice run on the same extractions two days later puts `medium`
  ahead on both metrics. The entry's *cost* argument is unaffected and still stands — `low` is 5x
  cheaper than `medium` and was the only arm that never failed — but that is a
  price-and-reliability case, not a quality one. **General lesson: do not draw a quality
  conclusion from the numeric slice alone when the leading arm is at or near 100% on it.** It
  covers roughly a third of the key items, and they are the anchored, easy ones.
- 2026-08-08: `RUNBOOK.md`'s judge prompt specified a return shape (`{id: {X: "tier"}}`) that
  `aggregate.py` cannot read — it reads `["judgments"][id][label]["tier"]` and raises
  `KeyError: 'judgments'`. Any judgments produced by following the runbook literally before this
  date need converting before they will tally. Fixed in the runbook, along with two related traps:
  `bench packets --out` defaults to `qualitative/` and silently overwrites the previous pass's
  artifacts, and the prompt did not ask judges for the per-verdict `note` the hand-check relies on.
- 2026-08-10 (#591): **`score_arms.py` had four compounding bugs that distorted absolute
  `must_not_miss`/facts recall — three inflating it, one deflating it:**
  1. Matching was against the whole vault's extraction text rather than the one document a key
     item is actually about, so sibling documents could cross-credit an anchor. *(inflated)*
  2. A rounding step in the thousands-to-millions variant generator could drop the decimal point
     entirely and collapse a 5+ digit anchor to a bare 1–3 character string that then matched
     almost any digit run in the corpus. *(inflated)*
  3. An item whose only anchor is an identifier (an LSO/bar number) rather than a quantity scored
     zero whenever the extractor reasonably omitted that number, even though the substance — the
     person, correctly related — was captured. *(deflated)*
  4. A plain-dollar anchor ($5,000,000, not a thousands-denominated financial figure) had no
     matching millions-prose conversion at all, so an extraction that correctly wrote "$5 million"
     scored a miss on transcription format alone. Tom's ruling: the benchmark scores on numeric
     *value*, not transcription format — this is a governing rule for every future A/B read
     through this scorer, not a one-off fix. *(deflated)*

  All four are fixed: matching is per-document, the rounding chain no longer emits a variant that
  lost its decimal point, an item whose every anchor reads as an identifier falls back to a
  non-numeric name-presence test, and a whole-dollar anchor ≥ $1,000,000 also generates "N
  million"/"N.Nm" variants (always with the word "million" or an "m" suffix directly touching the
  digits, so this can't reintroduce bug 2's short-token collision). **Absolute recall figures from
  before this fix are not comparable to figures after it** — only the D172 verify A/B above (run
  `2026-08-09-1523`) has been rescored against the fixed instrument, using the archived
  `.watchdog/extracted/*.json` artifacts under that run's `artifacts/` directory, which the fix
  reads directly (no live model calls). Other `must_not_miss` tables in this file (the 13-arm
  sub-item sweep and the earlier `gpt-luna` effort-tier passes) were produced by the same
  pre-#591 scorer and likely carry the same class of distortion, but their archived vaults were
  not available to re-score in the environment this fix was written in — treat their absolute
  percentages as provisional until a fresh rescoring pass is done. The paired-arm *rankings* in
  those tables are less affected than the absolute numbers, since the same instrument scored every
  arm in a given table and shared bias partly cancels in a comparison — but "partly" is doing real
  work in that sentence, and it does not cancel evenly (see the D172 rescore, where the two arms'
  `must_not_miss` gap moved from −1 to +5 after bugs 1–3 alone, then settled at +1 once bug 4 was
  also fixed).
