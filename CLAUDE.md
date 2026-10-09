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

## Definition of done

Check every non-trivial change against this list in one pass before calling it finished. Each item's full rule lives in the linked section — this checklist is the single source; the sections carry the detail, not a restatement of the obligation.

- [ ] **Tests** written or updated, and the suite is green ([Testing](#testing))
- [ ] **Lint** clean: `pipx run ruff check src tests` ([Linting](#linting))
- [ ] **`ARCHITECTURE.md`** updated if the change alters the pipeline's structure, the code/model split, or the vault/registry layout ([Architecture](#architecture))
- [ ] **`DECISIONS.md`** has a new `D<n>` entry (ascending order, newest last) if the change forecloses a future option or would read as a bug without the rationale ([Architecture](#architecture))
- [ ] **Invariants** (`ARCHITECTURE.md` §15) updated if a governing rule was established or revised ([Architecture](#architecture))
- [ ] The canonical **`docs/`** page updated for any user-facing change — app screen or control, setting, default, workflow step, CLI flag ([Documentation](#documentation))
- [ ] **`README.md`** left alone unless the pitch, requirements, install two-liner, or quick start changed ([Documentation](#documentation))

## Architecture

Two files, split by how often they're read. **[ARCHITECTURE.md](ARCHITECTURE.md)** is the current-state map — how the pipeline is built, plus §15's **Invariants** (I1–I13), the canonical governing rules. Read it to orient; it's kept lean so it stays loadable every session. **[DECISIONS.md](DECISIONS.md)** is the dated, numbered history of specific decisions (D1, D2, …) — the rationale and tradeoff for each — read on demand when you need the *why* behind a past choice.

**When a change alters the pipeline's structure, the split between deterministic code and the model, or the vault/registry layout, update `ARCHITECTURE.md` in the same change**, and append a `### D<n>` entry to `DECISIONS.md` (ascending order, newest last). If the change establishes or revises a governing rule, update the Invariants section in `ARCHITECTURE.md` too. **Keep decision entries concise** — a few sentences of rationale, then the tradeoff; the full record is in git and the PR, not the log. A decision earns an entry only if it forecloses a future option or would read as a bug without the rationale; pure refactors belong in the commit message. Both are items on the [definition of done](#definition-of-done).

## Documentation

**When a change adds, removes, or modifies anything user-facing — an app screen or control, a setting, a default value, a workflow step, or (for the retiring command line) a command or flag — update the affected pages under `docs/` in the same change.** Every topic has exactly one canonical page; update that page and let the others keep linking to it, rather than re-explaining the topic in several places:

- `docs/commands.md` — the command line, being retired (D267): commands, flags and slash commands, kept as a reference for existing users
- `docs/configuration.md` — Settings: every tab and field, defaults, model backends, cost
- `docs/install.md` — downloading and installing the app, first-run setup, updating, uninstalling
- `docs/app.md` — the desktop app: what each screen does, folder access, updates
- `docs/getting-started.md` / `docs/investigating.md` — workflow walkthroughs
- `docs/methodology.md` — plain-English account of what chew/dig/bark do and why, and what the
  auxiliary local models (Docling, GLiNER, the embedding/reranker pair) are for — zero code
  background assumed; update when the pipeline's stages or their AI/local split changes
- `docs/vault.md` — vault layout, entity-note structure, supported file types
- `docs/skills.md` — the record-skill catalog (update when adding a skill)
- `docs/troubleshooting.md` — failure modes and fixes

`README.md` is a deliberately slim front door (~120 lines) — it names capabilities but documents nothing in detail; it only changes when the pitch, requirements, install two-liner, or quick start change. Do not add command tables, configuration keys, or workflow detail back into it. The docs describe the desktop app; users are never told to open a terminal. They are written for working journalists who may never have used one: serious, precise, conversational — no exclamation marks, no hype, short paragraphs, jargon defined at first use, Canadian English. Doc updates are an item on the [definition of done](#definition-of-done).

## Testing

Write tests for new features and any non-trivial function. The suite lives in `tests/`.

**One-time dev setup** — inject pytest into the watchdog-intel pipx venv so it runs with all the package's dependencies:

```
pipx inject watchdog-intel pytest numpy
```

**To run the suite:**

```
~/.local/pipx/venvs/watchdog-intel/bin/pytest
```

(`pipx run pytest` creates an isolated venv without watchdog's deps and will fail to collect most tests — don't use it for development.)

**No `pipx` available (e.g. a fresh container)?** Don't `pip install -e .[dev]` — that pulls in docling's full tree (torch, onnxruntime, …) and can take several minutes. Mirror `.github/workflows/ci.yml`'s `test` job instead: `pip install --no-deps -e .` plus the explicit lightweight dependency list from that job (pyyaml, pypdf, argcomplete, numpy, pytest, pytest-timeout, jsonschema, httpx, truststore, nh3, python-docx, python-pptx, openpyxl, Pillow, defusedxml) in a venv. Add `ruff` too if you also need to lint.

Tests use `tmp_path` and `monkeypatch` to redirect `WATCHDOG_HOME`, `PROJECTS_FILE`, and `CONFIG_FILE` away from the real home directory — patch all three when testing anything that touches the registry or projects list. `CONFIG_FILE` lives in more than one module: patch `watchdog.config.CONFIG_FILE` as well as `watchdog.cmd.base`'s, or a config read falls through to the real `~/.watchdog/config.json`. See the `wdg_home` and `configured` fixtures in `tests/test_cli.py` for the pattern.

CI (`.github/workflows/ci.yml`) runs on pull requests to `main`, not on plain pushes — open a PR to get a CI run. Third-party actions in `ci.yml` and `publish.yml` are pinned to commit SHAs with the version in a trailing comment; bump both together.

## Linting

Ruff runs in CI as a dedicated, blocking `lint` job (separate from the test matrix, run once). Run it locally before pushing:

```
pipx run ruff check src tests
```

The rule set is deliberately conservative — pyflakes (`F`) plus the pycodestyle logical-error subsets `E4`/`E7`/`E9`. It catches unused imports/variables and real logical errors, **not** formatting: line-length (`E501`) and import-sorting (`I`) are intentionally not enforced, and there is no autoformatter. `cli.py` is exempt from `F401` because it deliberately re-exports a wide surface for test monkeypatching (see the `# noqa` on `import sys` there); a genuine unused import anywhere else will still fail CI. See DECISIONS D106.

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
| Every `sonnet-4.6`/`opus`-tier arm in `benchmarks/benchmark.yaml` (`sonnet-4.6-*`, `sonnet-med-*`, `batch-sonnet-med`, the finalizer sweep's `sonnet-*` arms) (D206) | **The next benchmark run that touches these arms.** They were all measured before #635 turned Anthropic's `thinking` on for Sonnet 4.6/Opus 4.8 — every prior number for them reflects non-thinking behavior (`effort` as a verbosity dial only). Re-run before trusting a before/after comparison against archived figures; thinking bills as output, so cost went up, and quality/recall may have moved either way. |
| `claude-opus-5`'s, `claude-sonnet-5-5`'s and `claude-opus-5-5`'s `tokenizer_ratio` (1.28) in `model_catalog.yaml` (D206, D249) | **Whenever Opus 5 is next called for real** (a benchmark run, or convenient idle time). Both copied from the Opus 4.8/Sonnet 5 family rather than independently measured — plausible (Opus 4.8 and Sonnet 5 already share a tokenizer) but unconfirmed for this specific id. `benchmarks/tokenizer_ratio.py --count` measures it for free (Anthropic's non-generative token counter) once a master chew of corpus-v1 exists locally. |
| The "How many facts" paragraph in `prompts/extract_instructions.md` (D207), plus `document.summary`'s length guidance in the same file and in `prompts/digest.md` (D212) | **The next benchmark run that exercises the extractor.** D207 removed the two example fact counts ("a dense order may have fifteen, a routine form two") as anchors; D212 removed the parallel sentence/paragraph counts from the summary guidance for the same reason. Both shipped unmeasured; the expected effect is unknown in sign for either. Check facts-per-1,000-words against the archived baseline of **one fact per 219–280 words** (steady across five of six corpus documents, run `2026-08-05-2307`), check the *thin* documents specifically — the removed ceiling was what discouraged padding there — and check summary length/quality didn't drift longer on fact-dense documents now that the paragraph cap is qualitative. This is the fourth uncomparable prompt change to this file in a row; D203/D205/D207/D212 are no longer separable, so treat them as one revision when the run happens. |
| `entities` declared before `document` in `EXTRACTION`/`SECTION` (`pipeline/schemas.py`, D216, issue #651) | **The next benchmark run that exercises the extractor.** Shipped unmeasured. Check two things together: whether D211's dangling-entity-tag warning rate falls (the reorder's actual target), and whether entity-extraction quality/recall moved at all now that `entities` is the first thing the model commits to rather than the last — no archived run isolates this variable, since every prior corpus-v1 run used the old order. |
| The D246 prompt revision: invented examples in `extract_instructions.md`/`reconcile.md`, the `page`/empty-array fixes, `verify.md`'s rewrite, and display names in the briefing input; plus D262's replacement of the remaining corpus examples (the transcription example, the computed-figure amount, the sample document types, the `organization` definition, the `morgue_entity_id` examples, a `reconcile.md` example) | **The next benchmark run that exercises the extractor, verifier or reconciler.** Shipped unmeasured, alongside D203/D205/D207/D212/D216 — treat them as one revision. Also check recall on the PRESERVE CHANGES fact, the corpus merge pair the old examples named, and the dated-affidavit `must_not_miss` item D262's transcription example restated: if it drops, the archived figures for those items were inflated by the prompt itself. The same revision now also carries the October 2026 stale-instruction fixes: the record skills' red flags that told the extractor to compare against an "entity digest" it no longer receives (academic-research, administrative-tribunals, audio-video, bankruptcy, corporate-filings, dns-whois, government-contracts, legislature-transcripts, real-estate, regulatory-filings, tax-documents, vehicle-registrations, websites-html; government-reports and lobbying-records only in their journalist-facing notes), rewritten as within-document checks or leads, and `extract_instructions.md`'s basis paragraph, which no longer claims the contradiction check skips inferred claims (D214). Watch for a change in leads per document and in `inferred` rates on those skills' documents. |
| `gpt-6-luna`'s `tokenizer_ratio` (0.80) and `deepseek-flash`'s (0.81) in `model_catalog.yaml` (D249, D250) | **The next time a billed `benchmarks/tokenizer_ratio.py --probe` is run** (~$0.11 for the OpenAI and DeepSeek pair; neither provider has a free counter). Both copied from the family they replaced — GPT-5.x's 0.80 and DeepSeek V4's 0.81 — so they assume GPT-6 and V4.1 kept their predecessors' tokenizers, which neither vendor states. A smaller-than-real ratio widens the sectioning budget, so a wrong copy errs toward larger calls; the 60% threshold headroom covers a modest error. |
| `gemini-3.7-flash`'s and `gemini-3.5-flash-lite`'s `tokenizer_ratio` (0.91) in `model_catalog.yaml` (D217) | **Whenever a master chew of corpus-v1 next exists locally** — same trigger and same shape as the `claude-opus-5` row above, and cheaper: Gemini's `countTokens` is free and non-generative, so `benchmarks/tokenizer_ratio.py --count` confirms both ids for $0 with no run needed. Copied from the Gemini family value on this file's own convention (all three previous Gemini entries returned byte-identical counts), but unmeasured for these two ids, and they replaced the ids the 0.91 was actually measured on. |
| DeepSeek's peak/off-peak rates and the V4 checkpoints behind its ids (`model_catalog.yaml`, D217) | **The next benchmark run with a DeepSeek arm, and any comparison against an archived DeepSeek figure.** Prices were replaced from DeepSeek's page on 2026-08-20 and Flash again on 2026-10-04 (D250), and the peak windows — Monday to Friday UTC, holidays unmodelled — are transcribed from that page rather than confirmed against a bill; worth checking one real invoice against `watchdog usage` once a DeepSeek run has crossed a window boundary, and one that fell on a weekend. Separately, `deepseek-flash` now serves `V4.1-Flash` (the old `deepseek-v4-flash` name is a `legacy_ids` alias) and `deepseek-v4-pro` serves `V4-Pro-0813`, not the checkpoints every archived arm measured, so quality figures are as uncomparable as the cost ones. |
| The D278 arms in `benchmarks/benchmark.yaml`: `sonnet-5.5-med`/`-high` (the shipped default and its comparison), `haiku-5.5-low`/`-med` for extraction and `haiku-5.5-med` as finalizer, `sonnet-4.6-high` repeated, and the `sonnet-5.5-med` / `sonnet-5.5-med-brief` pair, three runs each | **The next benchmark run (the one these were added for).** Nothing about Sonnet 5.5 has been measured, so `docs/configuration.md`'s effort paragraph says so; once the run lands, replace that sentence with the measured gap and decide whether `medium` stays the default. Read a difference only where the repeated arms' ranges do not overlap. For the brief pair, check facts and `must_not_miss` recall and whether facts lean toward the brief's questions; `benchmarks/briefs/corpus-v1.md` is one brief, so a gap measures that wording, and Tom should read it before the run. Bare `haiku` arms change meaning when `haiku` moves to Haiku 5.5: repin them to `haiku-4.5` to keep the archived comparison. |
| `MATCH_THRESHOLD` (0.45) and the figure penalty (`0.5 + 0.5 × share`) in `pipeline/passages.py` (D270) | **The next extraction benchmark.** Both were fitted on the demo investigation's facts, which are written close to their sources, so the 98% precision / 85% recall measured there is optimistic. Re-measure with `benchmarks/passage_accuracy.py` on a vault whose facts carry quote locators from a real extraction (`passage_accuracy.py VAULT`), and refit the threshold and penalty on those before quoting either as tuned. |
| The machine-transcript note (`prompts/transcript_note.md`, added to the extraction and section prompts only when `source_type` is `transcript`), and `medium` as the default `transcription_model` (D273) | **The first benchmark run that includes a recording, or the first real investigation with several.** The note shipped unmeasured: check whether facts from a transcript attribute words to a named speaker only when the speech names them, and whether the "as transcribed, check against the recording" hedge lands on misheard names and figures without spreading to every fact. No other document's prompt changes, so this is separable from the D246/D262 revision. The default model was chosen on four samples on one Linux CPU (one a synthetic voice, none in French): re-time `medium` against `large-v3-turbo` on an Apple silicon and a Windows laptop, and on a real French recording, before quoting the speed figures in `docs/configuration.md` as typical. |
| Claude Haiku 5.5 as the bare `haiku` default (classifier and finalizer, D277), and its `tokenizer_ratio` (1.21) in `model_catalog.yaml` | **The upcoming benchmark run.** The default moved on 2026-10-09 before any measurement, at the owner's request. Check classification accuracy and post-processing quality (merges, contradictions, synthesis, briefing) against Haiku 4.5 (`haiku-4.5`); move the default back if 5.5 is worse. The ratio is derived from Anthropic's statement that the tokenizer counts about 30% more tokens than Haiku 4.5's (0.93 x 1.3), not measured: `benchmarks/tokenizer_ratio.py --count` measures it for free. |
| The entity-identity tiers in `pipeline/identity.py` (D279): the generic-word, title and legal-suffix lists, `is_distinctive`'s word counts, the shared-attribute rule (same relationship word to the same entity, or a street address), the identifier patterns, `FACT_CAP` (6 facts per side for a same-name pair) and `PROFILE_DOCS` (60 latest documents read per registered entity) | **The first finalizer evaluation with planted homonyms, or the first real investigation with more than a few hundred entities.** All chosen by judgement, never measured. Count how many pairs land in each tier, how many same-name medium pairs the model merges versus leaves (the medium tier costs tokens and leaves duplicates when the model is cautious), and whether any high-tier rule merged two different people or bodies. A generic word missing from the list makes a vague name merge in code; one too many sends a specific name to the model. |
| The `same_name` paragraph in `prompts/reconcile.md` and the removed "title, initial or middle name" merge example (D279) | **The next benchmark run that exercises the reconciler.** Shipped unmeasured, separately from the D246/D262 revision: check that the model declines same-name pairs whose facts do not connect, and still merges ones whose facts plainly do. |
| The synthesis prompt's rewrite (`prompts/synthesis.md`, D280): facts in, not earlier prose; `[f:…]` citations asked for, uncited framing allowed; Disputed facts shown labelled (never stated as established); `FACT_MAX` (120) and `FACT_BUDGET_CHARS` (24,000) in `pipeline/synthesis_bundle.py` with this batch's, then verified, then the most recent facts kept | **The first finalizer evaluation that scores synthesis (the demo answer key, recommendation 4 of the October 2026 review), or the next benchmark run that exercises the finalizer.** Shipped unmeasured, separately from every extraction-prompt revision above. Measure how many cited ids are real and how many sentences are uncited, whether summaries of hub entities drift toward the newest documents now that no earlier prose anchors them, and the token cost on an entity with several hundred facts before treating the budget as tuned. |
| The reconcile prompt's Job 2 rewrite (`prompts/reconcile.md`, D280): new facts compared with stored facts and with each other, stored facts not with each other, source passages cut to 240 characters (`_PASSAGE_CHARS`), Disputed facts included, labelled | **The next benchmark or finalizer evaluation that exercises the reconciler.** Shipped unmeasured, alongside the D279 `same_name` paragraph (separable: Job 1 is unchanged). Check contradiction precision and recall against the demo's planted conflicts, and whether the passages (most of the 35% larger input on the demo) earn their tokens; a missed old-vs-old conflict is no longer looked for again, by design. |
| Entity-note caps in `pipeline/entity_notes.py` (D280): `FLAT_MAX` 40 facts listed one by one, then `GROUP_FACTS` 5 per document and `FULL_GROUPS` 40 documents in full | **The first real investigation with an entity named in more than a hundred documents.** Chosen by judgement on a 14-document demo. Ask the reporter whether the folded note is still useful in Obsidian, and look at note size and render time on that entity before changing them. |

## Releasing to PyPI

Pushing a version tag publishes the package: `.github/workflows/publish.yml` runs on any `v*` tag, tests it, builds the sdist and wheel with `hatch`, creates (or updates) the GitHub release with generated notes, and uploads to PyPI. Publishing uses OIDC trusted-publisher auth — no API tokens or secrets.

**Release steps:**

1. On a branch named `chore/v<version>`, bump `version` in `pyproject.toml` (follows [PEP 440](https://peps.python.org/pep-0440/): `0.1.0a1`, `0.1.0b1`, `0.1.0`, `0.2.0`, etc.). Put the release notes in the PR description under a "What's new" heading — the workflow uses that section as the release notes, falling back to GitHub's generated list of merged PRs.
2. Merge the PR.
3. Tag the merge commit and push the tag: `git tag v<version> && git push origin v<version>`.
4. The workflow does the rest. Don't draft the release by hand in the GitHub UI first: the workflow overwrites its notes. A version ending in `a`, `b` or `rc` plus a number is marked as a pre-release.

The `pypi` GitHub environment and PyPI trusted-publisher entry for `watchdog-intel` are already configured — no further setup needed.

---

## Adding new record skills

See the `authoring-record-skills` skill (`.claude/skills/authoring-record-skills/SKILL.md`) for where record skills live, the required section-by-section structure, red-flag authoring rules, and the questions to ask the user before starting.

---

## Ingest workflow

Ingest runs in Python with no Claude Code session involved. Users start it from the desktop app (Add documents), which runs the same `watchdog add` command as a subprocess (D265, I10); the command line itself is being retired from users' view (D267). The Python orchestrator (`pipeline/orchestrate.py`) drives extraction, synthesis, and the briefing via direct model calls (the orchestrator replaced the old `/watchdog-ingest` skill).

**Intended workflow:**

1. `watchdog add [files…]` — chew (OCR/Docling, local, no API tokens), then extraction,
   synthesis and the briefing, in one terminal run (D251). `watchdog chew`, `dig` and `bark` run
   the same three steps one at a time. `watchdog ingest` still works but is deprecated
   (#441/D138); don't recommend it in new code or docs.
2. `watchdog ask ["question"]` (D253) opens a Claude Code session in the vault → ask investigation questions; the session reads `hot.md`, `briefings/`, and the registry fresh, with no ingest-time context baggage

Investigation sessions stay separate from ingest by construction, so a session's context is spent only on Q&A.

---

## CLI style guide

The terminal-output style guide for `cli.py` (colour semantics, layout conventions, adding a command or alias) lives in `src/watchdog/CLAUDE.md`, which loads automatically when working under `src/watchdog/`.
