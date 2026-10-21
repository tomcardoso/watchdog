# DeepSeek extractor arms — results (scored 2026-10-21)

Run `2026-10-21-0106`, extractor stage only, the three DeepSeek arms of the frontier matrix,
corpus-v1, keys-v4. The run came from commit `27e5cec20` on `main` (clean tree). Scored from the
run's own `artifacts/`, not the live vaults. This fills the gap left by run `2026-10-13-1702`,
whose DeepSeek arms failed on a network problem (see `2026-10-18-frontier-results.md`), for
issue #361's question: is DeepSeek good enough to recommend as the cheaper route for
large-investigation ingest?

## Read this first

- **Each arm ran once (D314).** A gap between two arms is a lead, not a result. Before changing a
  default or a recommendation on one, re-run both arms with `repeats: 3` and act only if their
  ranges do not overlap.
- **An Anthropic model judged DeepSeek output against Claude's figures.** The judge passes ran on
  Opus, the frontier table's Claude arms were judged by Opus too, and #361 records that as an
  asymmetric bias in Claude's favour. The Gemini cross-check was declined. The bias, if present,
  would understate DeepSeek against Claude; it cannot explain a DeepSeek lead.
- **Judged in a different session from the frontier run.** Same prompt, same packet builder, same
  keys, but different judge instances on a different day. The frontier run's re-rated arms moved
  by one point at most between passes; a difference of a few points between this table and the
  frontier's may be judge drift.
- **Comparable with the frontier table, by construction.** Every arm extracted from the same
  pre-processed page text (the 13 October master vault's `staging/` and `queue/`, copied in;
  all six staged PDFs matched `corpus-v1.sha256`), with the same brief (`briefs/corpus-v1.md`).
  Between `07de6ccac` (the frontier run) and `27e5cec20` the prompts, schemas, model client,
  keys and scorer are unchanged; the only diffs in that path are comments, an error string and a
  lock label.
- **`deepseek-flash` is partial: 4 of 6 documents.** Two documents failed JSON validation (below).
  Its whole-corpus figures charge the lost documents as misses; a second table isolates quality.
- **Not the archived DeepSeek checkpoints.** DeepSeek's quick-start page now says `deepseek-flash`
  "is now served by DeepSeek-V4.1-Flash-1017". The catalogue names V4.1-Flash and
  `deepseek-v4-pro` V4-Pro-0813; neither is confirmed against what served this run beyond that
  page.
- **Two arms ran at DeepSeek's peak price.** The run crossed into the 01:00–04:00 UTC weekday peak
  window (2× every rate). Every `deepseek-pro-think-high` call and the two largest
  `deepseek-flash-think-high` calls were costed at peak. Off-peak equivalents are given below;
  they apply Watchdog's own peak model and have not been checked against a DeepSeek bill (see the
  DeepSeek row of CLAUDE.md's standing follow-ups).

## Two slices

As in the frontier report: **numeric** (`bench score`, sub-item aggregation, 240 fact and 76
must_not_miss sub-items, deterministic, directly comparable) and **qualitative** (one blinded
pass, all 377 key items, 251 facts and 126 must_not_miss; a hit is `verbatim` or `credited`).

The six judges ran on Opus, one per document, each given only its packet and the RUNBOOK prompt
verbatim; none read `mapping.json`. The first document judged (the Pension Order) was
hand-checked against the extraction text: 8 of its 28 items, all 24 verdicts, matched. Every
judgment file was checked against its packet for every id, every label, a valid tier and a
non-empty note before `bench judge` ran. A consistency check needing no mapping: on the two
documents `deepseek-flash` lost, exactly one label per document is `ungrounded` on every item,
as an empty extraction should be.

## Per arm — whole corpus, with the frontier's Claude `high` arms

| Arm | Numeric facts | Numeric must_not_miss | Qualitative facts | Qualitative must_not_miss | Cost / page | Speed / page | Documents |
|---|---|---|---|---|---|---|---|
| deepseek-flash † | 41% (98/240) | 43% (33/76) | 44% (110/251) | 40% (51/126) | $0.0017 | 1.8 s ‡ | 4/6 |
| deepseek-flash-think-high | 72% (173/240) | 87% (66/76) | 75% (187/251) | 79% (99/126) | $0.0008 (≈$0.0006 off-peak) | 0.6 s | 6/6 |
| deepseek-pro-think-high | 64% (154/240) | 59% (45/76) | 65% (164/251) | 62% (78/126) | $0.0043 (≈$0.0022 off-peak) | 2.0 s | 6/6 |
| *haiku-5.5-high (frontier, the default, D315)* | 70% (168/240) | 74% (56/76) | 74% (186/251) | 72% (91/126) | $0.0009 | 4.07 s ‡ | 6/6 |
| *sonnet-5.5-high (frontier)* | 63% (152/240) | 61% (46/76) | 67% (169/251) | 65% (82/126) | $0.0049 | 0.60 s | 6/6 |

† partial: two documents lost, charged as misses. Its cost per page is over the 92 pages it
extracted, and includes the spend on the two lost documents. ‡ sectioned at least one document
(#555 confound on speed).

## Per arm — without the First Report of the Monitor

| Arm | Numeric facts | Numeric must_not_miss | Qualitative facts | Qualitative must_not_miss |
|---|---|---|---|---|
| deepseek-flash † | 38% (86/228) | 31% (18/59) | 38% (85/224) | 31% (30/97) |
| deepseek-flash-think-high | 71% (161/228) | 86% (51/59) | 72% (161/224) | 80% (78/97) |
| deepseek-pro-think-high | 62% (142/228) | 58% (34/59) | 62% (138/224) | 61% (59/97) |
| *haiku-5.5-high (frontier)* | 68% (156/228) | 71% (42/59) | 72% (161/224) | 69% (67/97) |
| *sonnet-5.5-high (frontier)* | 62% (141/228) | 56% (33/59) | 65% (146/224) | 70% (68/97) |

No DeepSeek arm skimmed the Monitor report: all three cite pages past 16 (`deepseek-flash` 40
facts, `-flash-think-high` 9, `-pro-think-high` 6), and the postflight coverage check found no
gaps in any arm.

## Quality without the reliability loss — the four documents `deepseek-flash` extracted

| Arm | Numeric facts | Numeric must_not_miss | Qualitative facts | Qualitative must_not_miss |
|---|---|---|---|---|
| deepseek-flash | 81% (98/121) | 87% (33/38) | 69% (110/159) | 75% (51/68) |
| deepseek-flash-think-high | 69% (83/121) | 92% (35/38) | 69% (110/159) | 81% (55/68) |
| deepseek-pro-think-high | 64% (77/121) | 50% (19/38) | 61% (97/159) | 66% (45/68) |

On the documents it did extract, non-thinking Flash is level with Flash thinking on judged facts
and a little behind on must_not_miss. Its problem is reliability, not what it extracts when it
succeeds.

## Failures and reliability

- **`deepseek-flash` lost two documents** (Pre-Filing Report, Annual Financial Report 2020-21),
  each after a section call failed JSON validation twice: one "response was not valid JSON", one
  valid JSON missing the required `key_facts`. More telling is what preceded them: the
  whole-document call failed validation twice on **five of the six documents**, including the
  5-page Pension Order, and each fell back to sectioning. That is where its 16 sectioned calls,
  9 retries and 1.8 s/page come from. Only the Initial Order extracted whole. The logs do not
  record why each whole-document attempt failed; the one invalid-JSON section produced 19,262
  output tokens, far below the 384,000 cap, so it was not a length cut-off.
- **Why it is possible at all:** DeepSeek's `response_format` accepts only `text` and
  `json_object`, so Watchdog sends `json_object` with the schema in the prompt
  (`model_client._openai_response_format`); nothing on DeepSeek's side enforces the schema.
  DeepSeek documents a beta strict mode for *tool calls* (`https://api.deepseek.com/beta`,
  every property required, `additionalProperties: false`), which Watchdog does not use. It is a
  possible fix, not one this run measured.
- **The thinking arms were reliable on this run.** `deepseek-flash-think-high` extracted every
  document whole on the first attempt (0 retries, 0 sectioned calls). `deepseek-pro-think-high`
  needed one retry (the 70-page Annual Financial Report 2020-21) and no sectioning.

## Cost and time

| Arm | Cost (as costed) | Off-peak equivalent | Wall clock | Pages |
|---|---|---|---|---|
| deepseek-flash | $0.157 | $0.157 (all off-peak) | 163 s | 92 of 209 |
| deepseek-flash-think-high | $0.173 | ≈$0.115 | 122 s | 209 |
| deepseek-pro-think-high | $0.908 | ≈$0.454 | 412 s | 209 |

The whole run cost $1.24 as costed. `deepseek-flash` cost more than `-flash-think-high` while
extracting 92 pages to its 209, because every failed whole-document attempt is still billed.

## What this says for #361

All single runs, so these are leads.

- **DeepSeek V4.1 Flash with thinking at `high` is the lead.** On this run it matches or beats
  the shipped default, `haiku-5.5-high`, on all four slices, by 1 to 13 points (largest on
  must_not_miss: 87% against 74% numeric, 79% against 72% judged). Its cost per page is about
  the same ($0.0008 as costed and about $0.0006 off-peak, against $0.0009). It extracted every
  document in one call each, at about 0.6 s per page against Haiku's 4.07 s. It also beats
  `sonnet-5.5-high` on every slice at about a sixth of the cost.
- **Plain `deepseek-flash` (no thinking) should not be recommended on this evidence.** It lost a
  third of the corpus to schema failures and fell back to sectioning on almost everything else.
  `docs/models-and-cost.md` lists `deepseek:deepseek-flash` as an available model (with
  `-thinking` as a suffix) without recommending it; any recommendation should name the
  `-thinking` id and an effort.
- **`deepseek-v4-pro` with thinking is not worth its price here.** It trails Flash thinking by
  8–28 points across the four slices at about four to five times the cost per page.
- **Before recommending anything in the docs:** re-run `deepseek-flash-think-high` and
  `haiku-5.5-high` with `repeats: 3` and recommend only if the ranges do not overlap. The
  frontier report already calls for a Haiku medium/high repeat; adding the DeepSeek arm to that
  run would settle both. Also worth a cross-family judge pass, since an Anthropic judge rated
  every figure here, and a real DeepSeek invoice check, since the peak-window costing is
  unconfirmed.

## Housekeeping notes

- **Claude auth gate.** The first attempt (`2026-10-21-0051`) failed every arm in 0 seconds
  with "No model provider is set up", before any DeepSeek call. `ops.ingest._require_auth`
  demands Claude auth whenever any stage routes to Claude, and an extractor-only arm still
  carries the Haiku classifier and finalizer defaults, though here neither runs: every queued
  document pins its skill in the sidecar, and the arms do not finalize. On a machine with Claude
  set up this passes unnoticed. In the cloud container the run used, a key-free
  `~/.watchdog/credentials.json` holding only `{"mode": "subscription"}` let it through; nothing
  called Claude. A benchmark operator on a DeepSeek-only machine will hit the same gate.
- `bench judge` again printed "expect 420" and an empty "sonnet-high on initial-order" check;
  both are the stale checks the frontier report notes.
- `bench index` was written outside the repository for this run; `deepseek-flash` lists as
  partial.
- No default, prompt or catalogue value was changed.
