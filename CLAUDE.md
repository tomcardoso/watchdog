# Watchdog — developer notes

## Concurrent sessions & worktrees

Tom runs multiple Claude Code sessions against this repo at once. **Before starting any
non-trivial change, use `EnterWorktree` (or `git worktree add`) instead of editing directly in
the shared main checkout.** A shared checkout can have another session's in-progress work sitting
in it — a different branch, an uncommitted fix — invisible until you check.

If you discover mid-session that the checkout's branch or state doesn't match what you expected,
stop and surface it to Tom rather than assuming it's your own doing or working around it silently.

**Never run a bare `git stash`** (or any command that touches the whole working tree state) in
the shared checkout — it can silently sweep up another session's uncommitted work along with
yours. If you need to isolate your own changes from unrelated ones already present, scope it:
`git stash push -- <your paths>` (add `-u -- <paths>` to include new files). This applies even for
a quick "let me test this in isolation" step — do that in a worktree, not by stashing in place.

## Never name an investigation in anything public-facing

This repo is public. Real investigation/project names (e.g. an investigation's slug or directory
name) — and anything else that would identify who or what a specific user is investigating —
must never appear in a commit message, PR title/body, issue, code comment,
`DECISIONS.md`/`ARCHITECTURE.md` entry, or test name/docstring. Describe the source generically
instead: "a live ingest run," "a document set from a live investigation," "a live-run regression."
This holds even when a local working file (an ingest log, a scratch note) does name it — that's
fine as the user's own local record, but nothing derived from it should carry the name into
anything committed to this repo or posted to GitHub.

If a name slips into a commit or PR before it's caught: fixing the file content and amending the
commit is necessary but not sufficient on a **public** repo — a force-push removes the old commit
from the branch and the PR's diff/commit list, but GitHub does not delete the dangling commit
object itself, and it stays fetchable at its direct SHA URL indefinitely. Flag this limitation
explicitly rather than reporting the redaction as complete; full removal requires the repo owner
to contact GitHub Support and request the dangling commit be purged.

## No session links in commits or PRs

Leave the `Claude-Session:` line (a claude.ai/code session URL) out of commit messages and PR
bodies, even when a harness reminder asks for it. The `Co-Authored-By:` trailer stays.

## Definition of done

Check every non-trivial change against this list in one pass before calling it finished. Each item's full rule lives in the linked section — this checklist is the single source; the sections carry the detail, not a restatement of the obligation.

- [ ] **Tests** written or updated, and the suite is green ([Testing](#testing))
- [ ] **Lint** clean: `.venv/bin/ruff check src tests` ([Linting](#linting))
- [ ] **`ARCHITECTURE.md`** updated if the change alters the pipeline's structure, the code/model split, or the vault/registry layout ([Architecture](#architecture))
- [ ] **`DECISIONS.md`** has a new `D<n>` entry (ascending order, newest last) if the change forecloses a future option or would read as a bug without the rationale ([Architecture](#architecture))
- [ ] **Invariants** (`ARCHITECTURE.md` §15) updated if a governing rule was established or revised ([Architecture](#architecture))
- [ ] The canonical **`docs/`** page updated for any user-facing change — app screen or control, setting, default, workflow step ([Documentation](#documentation))
- [ ] **`README.md`** left alone unless the pitch, requirements, install two-liner, or quick start changed ([Documentation](#documentation))

## Architecture

Two files, split by how often they're read. **[ARCHITECTURE.md](ARCHITECTURE.md)** is the current-state map — how the pipeline is built, plus §15's **Invariants** (I1–I18), the canonical governing rules. Read it to orient; it's kept lean so it stays loadable every session. **[DECISIONS.md](DECISIONS.md)** is the dated, numbered history of specific decisions (D1, D2, …) — the rationale and tradeoff for each — read on demand when you need the *why* behind a past choice.

**When a change alters the pipeline's structure, the split between deterministic code and the model, or the vault/registry layout, update `ARCHITECTURE.md` in the same change**, and append a `### D<n>` entry to `DECISIONS.md` (ascending order, newest last). If the change establishes or revises a governing rule, update the Invariants section in `ARCHITECTURE.md` too. **Keep decision entries concise** — a few sentences of rationale, then the tradeoff; the full record is in git and the PR, not the log. A decision earns an entry only if it forecloses a future option or would read as a bug without the rationale; pure refactors belong in the commit message. Both are items on the [definition of done](#definition-of-done).

There is no command line (D300): the app starts Python through module entry points (`python -m watchdog.gui.server`, `python -m watchdog.worker`), and every change it makes is an operation in `watchdog/ops/`. An Ask Claude session never runs a command; give it a tool in `watchdog/session_tools.py` instead (D299).

## Documentation

**When a change adds, removes, or modifies anything user-facing — an app screen or control, a setting, a default value or a workflow step — update the affected pages under `docs/` in the same change.** Every topic has exactly one canonical page; update that page and let the others keep linking to it, rather than re-explaining the topic in several places:

- `docs/install.md` — downloading and installing the app, first-run setup, updating, uninstalling
- `docs/getting-started.md` — the sample investigation, then a first investigation start to finish
- `docs/using-watchdog.md` — day-to-day work, task by task: every screen, adding documents and
  supported file types, checking facts, Review, Ask Claude, history, the files on disk, folder access
- `docs/how-it-works.md` — plain-English account of the three stages (pre-processing, processing,
  post-processing) and why, the auxiliary local models (Docling, GLiNER, the embedding/reranker pair),
  and the record-skill catalogue — zero code background assumed; update when the pipeline's stages
  or their AI/local split changes, or when adding a skill
- `docs/models-and-cost.md` — Claude billing, other providers and local models, keys, controlling
  cost, the benchmark summary behind the defaults, and the Settings reference (every tab and field)
- `docs/troubleshooting.md` — failure modes and fixes

Keep it to these six pages: a new topic goes into the page it belongs to, not a new one. A new page
must also be added to `PAGES` in `site/build.mjs`, or the website build fails.

The same pages are the website (https://tomcardoso.github.io/watchdog/, D317): `site/build.mjs`
renders `docs/` and `site/src/index.html` (the landing page) into `site/dist`, and
`.github/workflows/pages.yml` publishes it on every push to main that touches `docs/` or `site/`.
Preview with `cd site && npm ci && npm run dev`. Link a tool or service the first time a page
names it. Screenshots live in `docs/images/`, in a light and a `-dark` version; `npm run
screenshots` in `gui/` (after `npm run build`, with `WATCHDOG_PYTHON` set, under `xvfb-run` on
Linux) captures them all again from the sample investigation; App release does this after every stable
release, merging the new images into `main` through a pull request it merges itself (D320). The landing page's network is the
sample's own (`site/src/data/graph.json`, from `site/scripts/sample-graph.py`). Internals a
reporter never needs (registry files, hidden folders' contents, tuning rationale) belong in
`ARCHITECTURE.md`, `DECISIONS.md` or `benchmarks/`, not in `docs/`.

`README.md` is a deliberately slim front door (~120 lines) — it names capabilities but documents nothing in detail; it only changes when the pitch, requirements, install two-liner, or quick start change. Do not add command tables, configuration keys, or workflow detail back into it. The docs describe the desktop app; users are never told to open a terminal. They are written for working journalists who may never have used one: serious, precise, conversational — no exclamation marks, no hype, short paragraphs, jargon defined at first use, Canadian English. Doc updates are an item on the [definition of done](#definition-of-done).

## Testing

Write tests for new features and any non-trivial function. The suite lives in `tests/`.

**Dev setup** — a plain venv with the package installed editable, without its heavy dependencies. Don't `pip install -e .[dev]`: that pulls in docling's full tree (torch, onnxruntime, …) and can take several minutes. Mirror the lightweight list `.github/actions/run-tests/action.yml` installs for CI:

```
python3 -m venv .venv
.venv/bin/pip install --no-deps -e .
.venv/bin/pip install pyyaml pypdf numpy pytest pytest-timeout pytest-xdist jsonschema httpx truststore nh3 python-docx python-pptx openpyxl Pillow defusedxml anthropic av claude-agent-sdk dateparser ruff
```

**To run the suite:** `.venv/bin/pytest`.

**Faster: `scripts/check.sh`** runs the Python half (ruff, then the suite on every core with `pytest-xdist`) and the app half (typecheck, unit tests, build, the end-to-end tests side by side) at the same time; `scripts/check.sh py` or `scripts/check.sh app` runs one half. Set `PYTHON` to the venv's interpreter (`PYTHON=.venv/bin/python`). While iterating, run the tests a change touches; run the whole script once before merging.

Tests use `tmp_path` and `monkeypatch` to move Watchdog's own folder away from the real home directory: patch `watchdog.config.HOME`, the one place `~/.watchdog` is decided (settings, the list of investigations, credentials, chats). See the `wdg_home` fixture in `tests/gui_support.py` for the pattern.

CI (`.github/workflows/ci.yml`) runs on pull requests to `main`, not on plain pushes — open a PR to get a CI run. Third-party actions in `ci.yml`, `app-release.yml` and `pages.yml` are pinned to commit SHAs with the version in a trailing comment; bump both together.

## Linting

Ruff runs in CI as a dedicated, blocking `lint` job (separate from the test matrix, run once). Run it locally before pushing:

```
.venv/bin/ruff check src tests
```

The rule set is deliberately conservative — pyflakes (`F`) plus the pycodestyle logical-error subsets `E4`/`E7`/`E9`. It catches unused imports/variables and real logical errors, **not** formatting: line-length (`E501`) and import-sorting (`I`) are intentionally not enforced, and there is no autoformatter. See DECISIONS D106.

## Note on running tests and linters

For a change that only touches comments, docstrings, or documentation — not any runnable code path — skip running the test suite and `ruff check` afterward. They can't catch anything for a change like that, so running them is pure overhead. Before running the suite/lint, ask: does this diff change anything that executes? A comment, a docstring, a `DECISIONS.md`/`ARCHITECTURE.md`/`docs/*.md` edit, a string that's never parsed — skip verification, just make the edit. Still run tests/lint for anything that touches actual logic, even a small tweak — this carve-out is specifically for non-executing text.

---

## Standing follow-ups

Constants and choices that were fitted against a *specific* benchmark run and are provisional until refit. **Each entry names the trigger that should make you revisit it — if you're doing the thing in the trigger, raise the entry with Tom rather than treating the current value as settled.** Delete an entry once it's been refit; don't let this section become a graveyard.

| Item | Trigger to revisit |
|---|---|
| `_CONTAINMENT_SUPPRESS` = 0.6 in `pipeline/verify.py` (near-duplicate suppression, D199) | **The next benchmark run that exercises the verifier.** The value was fitted against the 220 additions of run `2026-08-09-1523`, whose `prompts/verify.md` has since been rewritten (#619). The population it was tuned on no longer exists, so refit against the new run's additions before quoting it as tuned. Nothing breaks meanwhile — no material fact was lost anywhere from 0.9 down to 0.5 in the original sweep. |
| The "a computed figure is `inferred`" rule in `prompts/extract_instructions.md` (D203) | **Measured 2026-08-19 and it did not take — decide whether to keep the sentence at all.** Post-merge Sonnet 4.6 runs tagged **0** of the genuinely derived figures `figure_verify` identifies, so the rule changed nothing on the class it was written for. Do not re-check it against the old `figures_unverified` baseline (0.9%): D213 showed most of that population was correct rounded readings, not derivations, and the rate is now 0.64% for a different reason. The open question is no longer "did it work" but whether to delete the sentence — which would be a *fourth* consecutive unmeasured change to this file, so it should ride along with the D203/D205/D207 re-measurement rather than going alone. |
| The CONVERSION ARTIFACTS paragraph in `prompts/extract_instructions.md` (D205) | **The next benchmark run that exercises the extractor.** Shipped unmeasured — check whether the model actually declines implausible label/figure pairings on merged-table pages (the #625 cash-flow-forecast case) without over-applying the hedge to clean pages where the pairing was fine. This is the second uncomparable prompt change to this file in a row (alongside `_CONTAINMENT_SUPPRESS` above and D203): treat any pre-#631 benchmark numbers on extraction quality as measuring a different prompt. |
| `claude-sonnet-5-5`'s `tokenizer_ratio` (1.28) in `model_catalog.yaml` (D249, D310) | **Whenever a master chew of corpus-v1 next exists locally** (the D310 benchmark run makes one). It is copied from the new Claude tokenizer's value, measured on Opus 4.8 and Sonnet 5, both since removed from the catalog; no current Claude id has been measured. Anthropic says the current tokenizer dates from Opus 4.7, which makes the copy plausible but unconfirmed. `benchmarks/tokenizer_ratio.py --count` measures it for free (Anthropic's non-generative token counter). |
| The "How many facts" paragraph in `prompts/extract_instructions.md` (D207), plus `document.summary`'s length guidance in the same file and in `prompts/digest.md` (D212) | **The next benchmark run that exercises the extractor.** D207 removed the two example fact counts ("a dense order may have fifteen, a routine form two") as anchors; D212 removed the parallel sentence/paragraph counts from the summary guidance for the same reason. Both shipped unmeasured; the expected effect is unknown in sign for either. Check facts-per-1,000-words against the archived baseline of **one fact per 219–280 words** (steady across five of six corpus documents, run `2026-08-05-2307`), check the *thin* documents specifically — the removed ceiling was what discouraged padding there — and check summary length/quality didn't drift longer on fact-dense documents now that the paragraph cap is qualitative. This is the fourth uncomparable prompt change to this file in a row; D203/D205/D207/D212 are no longer separable, so treat them as one revision when the run happens. |
| `entities` declared before `document` in `EXTRACTION`/`SECTION` (`pipeline/schemas.py`, D216, issue #651) | **The next benchmark run that exercises the extractor.** Shipped unmeasured. Check two things together: whether D211's dangling-entity-tag warning rate falls (the reorder's actual target), and whether entity-extraction quality/recall moved at all now that `entities` is the first thing the model commits to rather than the last — no archived run isolates this variable, since every prior corpus-v1 run used the old order. |
| The D246 prompt revision: invented examples in `extract_instructions.md`/`reconcile.md`, the `page`/empty-array fixes, `verify.md`'s rewrite, and display names in the briefing input; plus D262's replacement of the remaining corpus examples (the transcription example, the computed-figure amount, the sample document types, the `organization` definition, the `morgue_entity_id` examples, a `reconcile.md` example) | **The next benchmark run that exercises the extractor, verifier or reconciler.** Shipped unmeasured, alongside D203/D205/D207/D212/D216 — treat them as one revision. Also check recall on the PRESERVE CHANGES fact, the corpus merge pair the old examples named, and the dated-affidavit `must_not_miss` item D262's transcription example restated: if it drops, the archived figures for those items were inflated by the prompt itself. The same revision now also carries the October 2026 stale-instruction fixes: the record skills' red flags that told the extractor to compare against an "entity digest" it no longer receives (academic-research, administrative-tribunals, audio-video, bankruptcy, corporate-filings, dns-whois, government-contracts, legislature-transcripts, real-estate, regulatory-filings, tax-documents, vehicle-registrations, websites-html; government-reports and lobbying-records only in their journalist-facing notes), rewritten as within-document checks or leads, and `extract_instructions.md`'s basis paragraph, which no longer claims the contradiction check skips inferred claims (D214). Watch for a change in leads per document and in `inferred` rates on those skills' documents. |
| `gpt-6-luna`'s `tokenizer_ratio` (0.80) and `deepseek-flash`'s (0.81) in `model_catalog.yaml` (D249, D250, D310) | **The next time a billed `benchmarks/tokenizer_ratio.py --probe` is run** (about $0.11 for one OpenAI and one DeepSeek model; neither provider has a free counter). Copied from the family they replaced — GPT-5.x's 0.80 and DeepSeek V4's 0.81 — so they assume GPT-6 and V4.1 kept their predecessors' tokenizers, which neither vendor states. A smaller-than-real ratio widens the sectioning budget, so a wrong copy errs toward larger calls; the 60% threshold headroom covers a modest error, and the 272K long-context clamp on GPT-6 Luna is sized through the same ratio. |
| `gemini-3.8-flash`'s and `gemini-3.5-flash-lite`'s `tokenizer_ratio` (0.91) in `model_catalog.yaml` (D217, D310) | **Whenever a master chew of corpus-v1 next exists locally** — same trigger as the Claude row above, and free: Gemini's `countTokens` is non-generative, so `benchmarks/tokenizer_ratio.py --count` confirms both for $0. Copied from the Gemini family value, which was measured on three Gemini ids all since removed; no current Gemini id has been measured. |
| DeepSeek's peak/off-peak rates and the V4 checkpoints behind its ids (`model_catalog.yaml`, D217) | **The next benchmark run with a DeepSeek arm, and any comparison against an archived DeepSeek figure.** Prices were replaced from DeepSeek's page on 2026-08-20 and Flash again on 2026-10-04 (D250), and the peak windows — Monday to Friday UTC, holidays unmodelled — are transcribed from that page rather than confirmed against a bill; worth checking one real invoice against Activity → Usage once a DeepSeek run has crossed a window boundary, and one that fell on a weekend. Separately, `deepseek-flash` now serves `V4.1-Flash` and `deepseek-v4-pro` serves `V4-Pro-0813`, not the checkpoints every archived arm measured, so quality figures are as uncomparable as the cost ones. |
| Gemini 3.8 Flash's dated price in `model_catalog.yaml` (D310): its promotional $0.75 / $3.75 / $0.075 cached through 2026-12-31 | **2027-01-01 (to $1.50 / $7.50 / $0.15)**, per ai.google.dev/gemini-api/docs/pricing (fetched 2026-10-13). The catalog has no dated-price mechanism, by the owner's choice (prices move too often to model), so change the three numbers on the day, re-checking the page first; until then every estimate and usage figure for that model is half the real cost. Delete this row when done. |
| The D310 frontier matrix in `benchmarks/benchmark.yaml`: every arm briefed with `benchmarks/briefs/corpus-v1.md`; Sonnet 5.5, Haiku 5.5, GPT-6 Luna and Gemini 3.8 Flash at `low`, `medium` and `high`; one run per arm (D314) | **Ran 2026-10-13 (`benchmarks/2026-10-18-frontier-results.md`, FINDINGS) and moved the extractor default to Haiku 5.5 at `high` (D315), ahead of the re-run below.** **Still open: re-run `haiku-5.5-med`, `haiku-5.5-high` and `sonnet-5.5-high` with `repeats: 3` and confirm their ranges do not overlap**; the D315 default stands on single runs. The effort default is global (`defaults.EXTRACTOR_EFFORT`), so it also moved Luna and Gemini extraction to `high`; if the repeat shows `medium` is enough there, make the default per-model. **Archived numbers are not comparable with this run:** every archived arm extracted with no brief and on models since removed, so read this run against itself, not against FINDINGS.md's archived tables. Every arm runs once (D314), so a gap between two arms is a lead: before changing a default on one, re-run both arms with `repeats: 3` and act only if their ranges do not overlap. `briefs/corpus-v1.md` is one brief, so every figure measures that wording; Tom should read it before the run. Haiku 5.5's extraction arms now run without the no-reasoning-channel scaffold (D310), and a `high` request on Haiku 5.5 now actually runs at `high` (before D310 it was dropped as the default, and Haiku 5.5 defaults to `medium`). |
| `MATCH_THRESHOLD` (0.45) and the figure penalty (`0.5 + 0.5 × share`) in `pipeline/passages.py` (D270) | **The next extraction benchmark.** Both were fitted on the demo investigation's facts, which are written close to their sources, so the 98% precision / 85% recall measured there is optimistic. Re-measure with `benchmarks/passage_accuracy.py` on a vault whose facts carry quote locators from a real extraction (`passage_accuracy.py VAULT`), and refit the threshold and penalty on those before quoting either as tuned. |
| The machine-transcript note (`prompts/transcript_note.md`, added to the extraction and section prompts only when `source_type` is `transcript`), and `medium` as the default `transcription_model` (D273) | **The first benchmark run that includes a recording, or the first real investigation with several.** The note shipped unmeasured: check whether facts from a transcript attribute words to a named speaker only when the speech names them, and whether the "as transcribed, check against the recording" hedge lands on misheard names and figures without spreading to every fact. No other document's prompt changes, so this is separable from the D246/D262 revision. The default model was chosen on four samples on one Linux CPU (one a synthetic voice, none in French): re-time `medium` against `large-v3-turbo` on an Apple silicon and a Windows laptop, and on a real French recording, before quoting the speed figures in `docs/models-and-cost.md` as typical. |
| Claude Haiku 5.5 as the bare `haiku` default (classifier and finalizer, D277), and its `tokenizer_ratio` (1.21) in `model_catalog.yaml` | **The upcoming benchmark run.** The default moved on 2026-10-09 before any measurement, at the owner's request. Haiku 4.5 is no longer catalogued (D310), so the comparison is against the other finalizer arms (Sonnet 5.5, GPT-6 Luna, Gemini 3.8 Flash, DeepSeek Flash): check post-processing quality (merges, contradictions, synthesis, briefing), and classification accuracy once the classifier corpus exists; move the default if Haiku 5.5 is clearly worse. The ratio is derived from Anthropic's statement that the tokenizer counts about 30% more tokens than Haiku 4.5's (0.93 x 1.3), not measured: `benchmarks/tokenizer_ratio.py --count` measures it for free. |
| The entity-identity tiers in `pipeline/identity.py` (D279): the generic-word, title and legal-suffix lists, `is_distinctive`'s word counts, the shared-attribute rule (same relationship word to the same entity, or a street address), the identifier patterns, `FACT_CAP` (6 facts per side for a same-name pair) and `PROFILE_DOCS` (60 latest documents read per registered entity) | **The first finalizer evaluation with planted homonyms, or the first real investigation with more than a few hundred entities.** All chosen by judgement, never measured. Count how many pairs land in each tier, how many same-name medium pairs the model merges versus leaves (the medium tier costs tokens and leaves duplicates when the model is cautious), and whether any high-tier rule merged two different people or bodies. A generic word missing from the list makes a vague name merge in code; one too many sends a specific name to the model. Since D325, records with the very same name are judged as groups rather than pairs, so count per group as well: records in, clusters out, and how many groups are carried to the next run. On a copy of one live registry (2,128 entities) a generic name accounted for most identical-name records (164 groups, largest 91), so the generic-word list now decides the size of the group calls, not just their number. |
| Same-name groups (D325): `_GROUP_MEMBERS` (30 per call) and `_GROUP_ROUNDS` (3) in `pipeline/reconcile.py`, the anchor rule for later rounds (grown clusters, at most a third of a slice, in every slice), low-tier same-name items paired only with the most-documented record, `_GROUP_MEMBER_EST_CHARS` (1,100) in the cost estimate, and the "Same-name groups" section of `prompts/reconcile.md` (`same`/`unsure` by member number) | **The next benchmark or finalizer evaluation that exercises the reconciler, or the first real run with a group of more than 30 records.** All shipped unmeasured; the cap comes from the largest member measured on one registry, not from quality. Plant a large group with two or three real things among many records in the answer key: check that the model's sets are right, how many members it calls `unsure` (that is the Review queue now), how often an answer is rejected as malformed, whether three rounds settle the group or it is carried every run, and estimated against billed cost in Activity → Usage. |
| The `same_name` paragraph in `prompts/reconcile.md` and the removed "title, initial or middle name" merge example (D279) | **The next benchmark run that exercises the reconciler.** Shipped unmeasured, separately from the D246/D262 revision: check that the model declines same-name pairs whose facts do not connect, and still merges ones whose facts plainly do. Since D325 the paragraph covers only pairs whose names match through an alias, a title or word order (identical names go to the group section, the row above), so measure the two separately. D296 added the reporter's marks to each side's facts (`flags`) and one sentence (a disputed fact is weak evidence for identity and never justifies a merge on its own), also unmeasured: check that a pair whose only connecting fact is disputed stays apart. |
| The synthesis prompt's rewrite (`prompts/synthesis.md`, D280): facts in, not earlier prose; `[f:…]` citations asked for, uncited framing allowed; Disputed facts shown labelled (never stated as established); `FACT_MAX` (120) and `FACT_BUDGET_CHARS` (24,000) in `pipeline/synthesis_bundle.py` with this batch's, then verified, then the most recent facts kept | **The first finalizer evaluation that scores synthesis (the demo answer key, recommendation 4 of the October 2026 review), or the next benchmark run that exercises the finalizer.** Shipped unmeasured, separately from every extraction-prompt revision above. Measure how many cited ids are real and how many sentences are uncited, whether summaries of hub entities drift toward the newest documents now that no earlier prose anchors them, and the token cost on an entity with several hundred facts before treating the budget as tuned. |
| The reconcile prompt's Job 2 rewrite (`prompts/reconcile.md`, D280): new facts compared with stored facts and with each other, stored facts not with each other, source passages cut to 240 characters (`_PASSAGE_CHARS`), Disputed facts included, labelled | **The next benchmark or finalizer evaluation that exercises the reconciler.** Shipped unmeasured, alongside the D279 `same_name` paragraph (separable: Job 1 is unchanged). Check contradiction precision and recall against the demo's planted conflicts, and whether the passages (most of the 35% larger input on the demo) earn their tokens; a missed old-vs-old conflict is no longer looked for again by a run, by design (D287 adds an on-demand re-check). |
| Entity-note caps in `pipeline/entity_notes.py` (D280): `FLAT_MAX` 40 facts listed one by one, then `GROUP_FACTS` 5 per document and `FULL_GROUPS` 40 documents in full | **The first real investigation with an entity named in more than a hundred documents.** Chosen by judgement on a 14-document demo. Ask the reporter whether the folded note is still useful in Obsidian, and look at note size and render time on that entity before changing them. |
| The briefing prompt's citation paragraph and its citable fact lines (`prompts/briefing.md`, `orchestrate._briefing_cited_facts`, D283), the optional `a_fact`/`b_fact` in the reconcile schema and prompt, and the fact-citation instructions in the query, wiki, surface and research skills and the vault `CLAUDE.md` | **The first finalizer evaluation that scores the briefing, or the next benchmark run that exercises the finalizer; for the skills, the first real Ask Claude sessions after this ships.** All shipped unmeasured. Count, per briefing, citations made, dropped (`.watchdog/briefings/<ts>.json`: `unknown`, `missing`) and statements left uncited, and check that a cited fact actually supports its sentence (code checks only that it exists); check the briefing input's growth on a large batch, since each fact line now carries a ref, page and flags. For contradictions, count how often `a_fact`/`b_fact` resolve. For sessions, run Maintenance → Check citations over real `queries/` and `wiki/` pages and count "source not found". |
| The contradiction re-check (D287): `MAX_ENTITY_CALLS` (45) in `pipeline/recheck.py`, `_RECHECK_OUTPUT_RATIO` (2% to 10%) in `pipeline/ingest_setup.py`, and the reconcile prompt's Job 2 used over an entity's whole record (every fact as `new_facts`) | **The first real re-checks, or the next finalizer evaluation that exercises the reconciler.** All judgement, none measured. Compare estimated with billed cost in Activity → Usage, count findings per re-check and how many the reporter marks handled as not real (precision on a whole record may differ from new-vs-stored), and check whether any real entity reaches the call limit. |
| The relationship-labels prompt (`prompts/relationship_labels.md`, D291), code's normalization in `relationships.normalize` (articles, a leading "is"/"are", a plain plural `-s`), and `MAX_ITEMS_PER_CALL` (120) / `MAX_KEYS_PER_PAIR` (12) | **The next benchmark or finalizer evaluation that exercises post-processing, or the first real investigation with a few hundred relationships.** Shipped unmeasured; the demo's canned answer measures cost only (one call, about 880 input and 35 output tokens, about 1% of the batch's post-processing input). Count groups made per run, how many the reporter splits (`status: split` in `registry/relationships.json`) and whether any group joins different relationships (rank, side, scope, time); check that the plain-plural rule never folds two different words on a real corpus. Plant a near-synonym pair and a near-but-different pair in the finalizer evaluation's answer key. |
| The date pass (`prompts/date_resolve.md`, `dates.validate`, `CHORE.max_items` 80, D330), the range sentence added to the `date` field in `prompts/extract_instructions.md`, and D329's guards in `pipeline/dates.py` (the qualifier-word list, the two reference dates) | **The next benchmark run that exercises the extractor or the finalizer, or the first real investigation with many non-English documents.** All shipped unmeasured. Count dates code read, dates sent to the pass, answers accepted, rejected and empty, and spot-check accepted answers against the source (a wrong day within the printed years passes every check); check that ambiguous numeric dates are settled only when the fact shows which reading is meant. The range sentence is a further extraction-prompt change: watch the share of facts dated with a period, and whether dated-fact counts rose. Sample the dates `dateparser` read in languages the qualifier list does not cover for a bounded wording read as a single date. |

## Releasing the app

Bump `pyproject.toml`'s version, then push a tag `v<semver>` (`v1.0.5`; `v1.1.0-beta.1` for a beta) on that commit, or, for a stable release, run App release by hand on `main` with **release** ticked, which tags it after the builds pass (D319); a cloud session can't push tags, so that is its way to release: `.github/workflows/app-release.yml` builds and signs the installers and publishes them. A stable release becomes the repository's latest release, which installed copies read for updates; betas go to the rolling `app-beta` pre-release. Details in `gui/DISTRIBUTION.md`.

---

## Adding new record skills

See the `authoring-record-skills` skill (`.claude/skills/authoring-record-skills/SKILL.md`) for where record skills live, the required section-by-section structure, red-flag authoring rules, and the questions to ask the user before starting.
