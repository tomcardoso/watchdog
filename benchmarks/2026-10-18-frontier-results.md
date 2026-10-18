# Frontier extractor matrix — results (scored 2026-10-18)

Run `2026-10-13-1702`, extractor stage only, 13 arms, corpus-v1, keys-v4. The run came from commit
`07de6ccac` on `electron` (clean tree). Scored from the run's own `artifacts/`, not the live
vaults. The three DeepSeek arms failed on a network problem and the finalizer stage has not run;
both are out of scope here.

## Read this first

- **Each arm ran once (D314).** A gap between two arms is a lead, not a result. Before changing a
  default on one, re-run both arms with `repeats: 3` and act only if their ranges do not overlap.
- **Every arm was briefed with `briefs/corpus-v1.md`.** Every figure measures that wording.
- **Not comparable with any archived figure in FINDINGS.md or `docs/benchmarks.md`.** Every
  archived arm extracted with no brief and on models since removed. Read this run against itself.
- **Three arms skimmed one document.** `haiku-5.5-low`, `gemini-3.8-flash-low` and
  `gemini-3.8-flash-med` extracted all six documents, but their facts on the *First Report of the
  Monitor* (34 pages) cite only pages 1–16. The postflight coverage check flagged pages 17–34
  (18 pages, about 36% of the document's words, and 14 of the 93 page anchors in its key file).
  The extractions are complete and error-free, with normal output sizes; this is a model that
  stopped early, not a failed call. `run.json` and `REPORT.md` do not show it. The scores charge
  the missed items to those arms, which is the right treatment, so both totals are reported below
  (whole corpus, and without that document). Other low-effort arms cite almost nothing past page
  16 either (sonnet-5.5-low 2 facts, gpt-6-luna-low 2), so this is the extreme end of a pattern,
  not a separate failure.
- **`gemini-3.5-flash-lite-low` is not on the judged table.** Its facts carry no page anchors, so
  the coverage check cannot assess it. Its judge pass was the cross-model anchor (pass e), and
  `bench index` refuses an arm rated twice, so it is reported separately below.
- **`gemini-3.8-flash` costs are at a promotional price through 2026-12-31.** From 2027-01-01 the
  catalogue price doubles (see CLAUDE.md's standing follow-ups), so every Gemini 3.8 Flash cost
  below is about half the post-promotion cost.
- **`haiku-5.5-high` sectioned 3 calls** and ran 4.1 s/page. The sectioning-count formula is an
  open confound (#555), so its speed may partly reflect that, and its recall may partly reflect
  sectioning.

## Two slices, and what each measures

- **Numeric** (`bench score`, sub-item aggregation): only the key items with a numeric anchor,
  about a third of them: 240 fact sub-items and 76 must_not_miss sub-items. Free and
  deterministic.
- **Qualitative** (five blinded passes, Opus judges): all 377 key items, 251 facts and 126
  must_not_miss. A hit is `verbatim` or `credited`. Passes a–d rated three arms each; pass (e)
  re-rated two of them next to `gemini-3.5-flash-lite-low`.

All 30 judgments came from Opus (`model: "opus"` on every call), never from a model under test.
Judges saw only a packet, never `mapping.json`. The first document's verdicts were hand-checked
against the extraction files (7 of 7 matched). Every judgment file was checked against its packet
for every id, every label and a valid tier before `bench judge` ran. Four anchor files and one
sonnet file were written by judges that a rate-limit error then terminated; they pass the same
completeness check, their notes are non-empty and varied, and I cross-checked them by matching
each item's figures against the extractions (the match rate sat near the other passes', which is
a weak check, since bare years match too). The one anchor file that was
missing was re-judged.

## Per arm — whole corpus

| Arm | Numeric facts | Numeric must_not_miss | Qualitative facts | Qualitative must_not_miss | Cost / page | Speed / page |
|---|---|---|---|---|---|---|
| sonnet-5.5-low | 40% (95/240) | 50% (38/76) | 43% (109/251) | 38% (48/126) | $0.0036 | 0.37 s |
| sonnet-5.5-med | 45% (109/240) | 57% (43/76) | 45% (113/251) | 47% (59/126) | $0.0035 | 0.36 s |
| sonnet-5.5-high | 63% (152/240) | 61% (46/76) | 67% (169/251) | 65% (82/126) | $0.0049 | 0.60 s |
| haiku-5.5-low † | 41% (98/240) | 50% (38/76) | 30% (75/251) | 32% (40/126) | $0.0002 | 0.31 s |
| haiku-5.5-med | 42% (102/240) | 54% (41/76) | 39% (99/251) | 39% (49/126) | $0.0003 | 0.54 s |
| haiku-5.5-high ‡ | 70% (168/240) | 74% (56/76) | 74% (186/251) | 72% (91/126) | $0.0009 | 4.07 s |
| gpt-6-luna-low | 35% (85/240) | 38% (29/76) | 41% (102/251) | 33% (42/126) | $0.0002 | 0.42 s |
| gpt-6-luna-med | 46% (110/240) | 41% (31/76) | 52% (131/251) | 43% (54/126) | $0.0002 | 0.48 s |
| gpt-6-luna-high | 50% (120/240) | 47% (36/76) | 55% (137/251) | 44% (56/126) | $0.0003 | 0.97 s |
| gemini-3.8-flash-low † | 46% (110/240) | 42% (32/76) | 27% (69/251) | 33% (41/126) | $0.0014 | 0.41 s |
| gemini-3.8-flash-med † | 40% (96/240) | 51% (39/76) | 32% (80/251) | 39% (49/126) | $0.0014 | 0.46 s |
| gemini-3.8-flash-high | 40% (97/240) | 55% (42/76) | 41% (103/251) | 44% (56/126) | $0.0028 | 1.16 s |
| gemini-3.5-flash-lite-low § | 49% (118/240) | 58% (44/76) | 17% (43/251) | 17% (21/126) | $0.0011 | 0.66 s |

† skimmed the Monitor report (pages 17–34 uncited). ‡ sectioned 3 calls. § judged in pass (e)
only, so its qualitative figures come from a different pass than the others.

## Per arm — without the First Report of the Monitor

| Arm | Numeric facts | Numeric must_not_miss | Qualitative facts | Qualitative must_not_miss |
|---|---|---|---|---|
| sonnet-5.5-low | 37% (85/228) | 44% (26/59) | 40% (90/224) | 45% (44/97) |
| sonnet-5.5-med | 43% (98/228) | 51% (30/59) | 40% (90/224) | 49% (48/97) |
| sonnet-5.5-high | 62% (141/228) | 56% (33/59) | 65% (146/224) | 70% (68/97) |
| haiku-5.5-low | 39% (88/228) | 47% (28/59) | 27% (61/224) | 39% (38/97) |
| haiku-5.5-med | 39% (90/228) | 53% (31/59) | 35% (79/224) | 43% (42/97) |
| haiku-5.5-high | 68% (156/228) | 71% (42/59) | 72% (161/224) | 69% (67/97) |
| gpt-6-luna-low | 33% (76/228) | 34% (20/59) | 37% (82/224) | 34% (33/97) |
| gpt-6-luna-med | 43% (99/228) | 32% (19/59) | 47% (106/224) | 45% (44/97) |
| gpt-6-luna-high | 48% (110/228) | 41% (24/59) | 51% (115/224) | 46% (45/97) |
| gemini-3.8-flash-low | 43% (99/228) | 34% (20/59) | 25% (57/224) | 36% (35/97) |
| gemini-3.8-flash-med | 38% (86/228) | 49% (29/59) | 31% (69/224) | 45% (44/97) |
| gemini-3.8-flash-high | 38% (86/228) | 51% (30/59) | 37% (82/224) | 48% (47/97) |
| gemini-3.5-flash-lite-low | 50% (113/228) | 64% (38/59) | 15% (33/224) | 20% (19/97) |

Excluding the Monitor report moves every arm by up to 5 points, because that document is one of
six and a minority of the key items. It leaves the order of the qualitative facts column unchanged apart from ties (for example
`sonnet-5.5-low` and `-med` both 40%), so the skim is not what drives the ranking.

## What the effort ladder shows, model by model

All of these are single runs, so read them as leads.

- **Sonnet 5.5.** Low and medium are close: qualitative facts 43% → 45%, must_not_miss 38% →
  47%, and cost per page is the same (medium is slightly cheaper, $0.0035 against $0.0036). High
  is a real step: facts 67%, must_not_miss 65%, at about 40% more per page ($0.0049) and 1.7× the
  time. Without the Monitor report, medium's qualitative facts fall to 40%, level with low, so
  the low → medium gain is small and not robust. The medium → high gain is 18–22 points on
  three of the four slices (numeric must_not_miss moves only 57% → 61%). On this run, if effort is going to be paid for, it pays at `high`, not `medium`.
- **Haiku 5.5.** The steepest ladder in the matrix: qualitative facts 30% → 39% → 74%,
  must_not_miss 32% → 39% → 72%. `high` costs about 3.5× `medium` ($0.0009 against $0.0003) and
  is still about 5× cheaper per page than Sonnet 5.5 high. It also had the slowest speed (4.07
  s/page) and the only sectioned calls, which the sectioning confound (#555) could be inflating
  in either direction. `haiku-5.5-high` is the highest recall in the run on all four slices. The
  jump from medium to high is large enough that a 3-run repeat of those two arms is the first
  thing to do.
- **GPT-6 Luna.** The cleanest ladder: qualitative facts 41% → 52% → 55%, must_not_miss 33% →
  43% → 44%, numeric rising on facts (35% → 46% → 50%). Most of the gain comes at medium; high
  adds little for about 50% more cost and twice the time. Cheapest arm per page at every tier
  ($0.0002–$0.0003). Sonnet 5.5 and Haiku 5.5 are also monotone on all four slices; only Gemini
  3.8 Flash is not.
- **Gemini 3.8 Flash.** The two slices disagree. Numeric facts are flat to falling (46% → 40% →
  40%) while qualitative facts rise (27% → 32% → 41%) and must_not_miss rises on both. That is a
  case where the numeric slice alone would pick the wrong effort level. Medium costs the same as
  low, and high doubles cost per page, at the promotional price; after 2027-01-01 high is about
  $0.0055 a page, more than Sonnet 5.5 high ($0.0049) at much lower recall.
- **Gemini 3.5 Flash-Lite** was run at `low` only: no ladder.

## How the models compare at medium effort

| Arm | Qualitative facts | Qualitative must_not_miss | Numeric facts | Numeric must_not_miss | Cost / page |
|---|---|---|---|---|---|
| gpt-6-luna-med | 52% | 43% | 46% | 41% | $0.0002 |
| sonnet-5.5-med | 45% | 47% | 45% | 57% | $0.0035 |
| haiku-5.5-med | 39% | 39% | 42% | 54% | $0.0003 |
| gemini-3.8-flash-med † | 32% | 39% | 40% | 51% | $0.0014 |

At medium, the four models sit within about 20 points on qualitative facts and about 10 points on
must_not_miss; no gap is large enough to read as more than a lead from single runs. The pattern
that holds on both slices is that GPT-6 Luna matches or beats Sonnet 5.5 on facts at about a
twentieth of the cost ($0.0002 against $0.0035 per page), and Sonnet 5.5 leads on must_not_miss. Haiku 5.5
and Gemini 3.8 Flash are behind at this tier on the judged slice.

**Pass (e), the cross-model anchor.** One pass rated `gemini-3.5-flash-lite-low`, `sonnet-5.5-med`
and `haiku-5.5-med` together:

| Arm | Qualitative facts | Qualitative must_not_miss |
|---|---|---|
| sonnet-5.5-med | 46% (116/251) | 48% (60/126) |
| haiku-5.5-med | 40% (101/251) | 40% (51/126) |
| gemini-3.5-flash-lite-low | 17% (43/251) | 17% (21/126) |

Two things come out of it. First, the judging is stable: the two arms that were rated twice moved
by one point at most between passes (`sonnet-5.5-med` 45% → 46% on facts and 47% → 48% on
must_not_miss, `haiku-5.5-med` 39% → 40% on both), against different peer arms. That is
evidence the blinded passes rate an arm consistently, not that the arms are equal. Second,
`gemini-3.5-flash-lite-low` is far behind on the judged slice (17%) even though its numeric
sub-item recall is among the highest in the run (49% facts, 58% must_not_miss). It extracted only
8 facts from the Monitor report (against 13–62 for the other arms) and none carries a page
anchor. A plausible reading is that it states figures without the surrounding claim, which a
number match credits and a judge does not. This is a hypothesis, not tested.

## Numeric and qualitative disagree for some arms

Three arms rank very differently on the two slices: `gemini-3.5-flash-lite-low` (numeric 49%,
qualitative 17%), `gemini-3.8-flash-low` (46% against 27%) and `haiku-5.5-low` (41% against 30%).
As in earlier runs, the numeric slice alone would have misranked cheap arms, so no ranking here
rests on it by itself. `gpt-6-luna-*` and `sonnet-5.5-*` keep the same order within each model on both.

## Housekeeping notes

- `bench judge` printed "Total item-arm judgments: 1131 (expect 420)" and a "sonnet-high on
  initial-order" check that finds 0 items. The totals are right (377 items × 3 arms = 1131); the
  "420" and the sonnet-high line look like stale checks from an earlier corpus. Not changed.
- The index (`benchmarks/index/`, gitignored) lists `haiku-5.5-high`'s effort as "high, None"
  and `gemini-3.5-flash-lite-low` under "measured, not yet judged", as noted above.
- `deepseek-flash` appears in the index as a partial arm (0/6). Out of scope.
- No default, prompt or catalogue value was changed.
