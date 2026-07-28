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
