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

## Definition of done

Check every non-trivial change against this list in one pass before calling it finished. Each item's full rule lives in the linked section — this checklist is the single source; the sections carry the detail, not a restatement of the obligation.

- [ ] **Tests** written or updated, and the suite is green ([Testing](#testing))
- [ ] **Lint** clean: `pipx run ruff check src tests` ([Linting](#linting))
- [ ] **`ARCHITECTURE.md`** updated if the change alters the pipeline's structure, the code/model split, or the vault/registry layout ([Architecture](#architecture))
- [ ] **`DECISIONS.md`** has a new `D<n>` entry (ascending order, newest last) if the change forecloses a future option or would read as a bug without the rationale ([Architecture](#architecture))
- [ ] **Invariants** (`ARCHITECTURE.md` §15) updated if a governing rule was established or revised ([Architecture](#architecture))
- [ ] The canonical **`docs/`** page updated for any user-facing change — CLI flag, `configure` key, command, default, workflow step ([Documentation](#documentation))
- [ ] **`README.md`** left alone unless the pitch, requirements, install two-liner, or quick start changed ([Documentation](#documentation))

## Architecture

Two files, split by how often they're read. **[ARCHITECTURE.md](ARCHITECTURE.md)** is the current-state map — how the pipeline is built, plus §15's **Invariants** (I1–I7), the canonical governing rules. Read it to orient; it's kept lean so it stays loadable every session. **[DECISIONS.md](DECISIONS.md)** is the dated, numbered history of specific decisions (D1, D2, …) — the rationale and tradeoff for each — read on demand when you need the *why* behind a past choice.

**When a change alters the pipeline's structure, the split between deterministic code and the model, or the vault/registry layout, update `ARCHITECTURE.md` in the same change**, and append a `### D<n>` entry to `DECISIONS.md` (ascending order, newest last). If the change establishes or revises a governing rule, update the Invariants section in `ARCHITECTURE.md` too. **Keep decision entries concise** — a few sentences of rationale, then the tradeoff; the full record is in git and the PR, not the log. A decision earns an entry only if it forecloses a future option or would read as a bug without the rationale; pure refactors belong in the commit message. Both are items on the [definition of done](#definition-of-done).

## Documentation

**When a change adds, removes, or modifies anything user-facing — CLI flags, `watchdog configure` keys, CLI commands, default values, or workflow steps — update the affected pages under `docs/` in the same change.** Every topic has exactly one canonical page; update that page and let the others keep linking to it, rather than re-explaining the topic in several places:

- `docs/commands.md` — CLI commands, flags, and slash commands (the reference)
- `docs/configuration.md` — `watchdog configure` keys, defaults, model backends, cost
- `docs/install.md` — install steps, prerequisites, optional installs
- `docs/getting-started.md` / `docs/investigating.md` — workflow walkthroughs
- `docs/vault.md` — vault layout, entity-note structure, supported file types
- `docs/skills.md` — the record-skill catalog (update when adding a skill)
- `docs/troubleshooting.md` — failure modes and fixes

`README.md` is a deliberately slim front door (~120 lines) — it names capabilities but documents nothing in detail; it only changes when the pitch, requirements, install two-liner, or quick start change. Do not add command tables, configuration keys, or workflow detail back into it. The docs are written for working journalists who may never have used a terminal: serious, precise, conversational — no exclamation marks, no hype, short paragraphs, jargon defined at first use, Canadian English. Doc updates are an item on the [definition of done](#definition-of-done).

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

Tests use `tmp_path` and `monkeypatch` to redirect `WATCHDOG_HOME`, `PROJECTS_FILE`, and `CONFIG_FILE` away from the real home directory — patch all three when testing anything that touches the registry or projects list. See the `wdg_home` and `configured` fixtures in `tests/test_cli.py` for the pattern.

CI runs on every push and PR via `.github/workflows/ci.yml`.

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
| `claude-opus-5`'s `tokenizer_ratio` (1.28) in `model_catalog.yaml` (D206) | **Whenever Opus 5 is next called for real** (a benchmark run, or convenient idle time). Copied from the Opus 4.8/Sonnet 5 family rather than independently measured — plausible (Opus 4.8 and Sonnet 5 already share a tokenizer) but unconfirmed for this specific id. `benchmarks/tokenizer_ratio.py --count` measures it for free (Anthropic's non-generative token counter) once a master chew of corpus-v1 exists locally. |
| The "How many facts" paragraph in `prompts/extract_instructions.md` (D207), plus `document.summary`'s length guidance in the same file and in `prompts/digest.md` (D212) | **The next benchmark run that exercises the extractor.** D207 removed the two example fact counts ("a dense order may have fifteen, a routine form two") as anchors; D212 removed the parallel sentence/paragraph counts from the summary guidance for the same reason. Both shipped unmeasured; the expected effect is unknown in sign for either. Check facts-per-1,000-words against the archived baseline of **one fact per 219–280 words** (steady across five of six corpus documents, run `2026-08-05-2307`), check the *thin* documents specifically — the removed ceiling was what discouraged padding there — and check summary length/quality didn't drift longer on fact-dense documents now that the paragraph cap is qualitative. This is the fourth uncomparable prompt change to this file in a row; D203/D205/D207/D212 are no longer separable, so treat them as one revision when the run happens. |

## Releasing to PyPI

The package publishes to PyPI automatically when a GitHub release is created. Publishing uses OIDC trusted-publisher auth — no API tokens or secrets.

**Release steps:**

1. Bump `version` in `pyproject.toml` (follows [PEP 440](https://peps.python.org/pep-0440/): `0.1.0a1`, `0.1.0b1`, `0.1.0`, `0.2.0`, etc.)
2. Commit and push
3. On GitHub: Releases → Draft a new release → create a tag matching the version (e.g. `v0.1.0`) → Publish release
4. The `.github/workflows/publish.yml` workflow fires, builds the sdist + wheel with `hatch`, and uploads to PyPI

The `pypi` GitHub environment and PyPI trusted-publisher entry for `watchdog-intel` are already configured — no further setup needed.

---

## Adding new record skills

See the `authoring-record-skills` skill (`.claude/skills/authoring-record-skills/SKILL.md`) for where record skills live, the required section-by-section structure, red-flag authoring rules, and the questions to ask the user before starting.

---

## Ingest workflow

Ingest runs entirely in the terminal — the Python orchestrator (`pipeline/orchestrate.py`) drives extraction, synthesis, and the briefing via direct model calls. No Claude Code session is involved (the orchestrator replaced the old `/watchdog-ingest` skill).

**Intended workflow:**

1. `watchdog chew` — OCR/Docling (terminal, local, no API tokens)
2. `watchdog ingest` — lock + queue + extraction + synthesis + briefing (terminal)
3. Open a Claude Code session in the vault → ask investigation questions; the session reads `hot.md`, `briefings/`, and the registry fresh, with no ingest-time context baggage

Investigation sessions stay separate from ingest by construction, so a session's context is spent only on Q&A.

---

## CLI style guide

The terminal-output style guide for `cli.py` (colour semantics, layout conventions, adding a command or alias) lives in `src/watchdog/CLAUDE.md`, which loads automatically when working under `src/watchdog/`.
