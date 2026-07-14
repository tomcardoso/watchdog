# Watchdog — developer notes

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

Two files, split by how often they're read. **[ARCHITECTURE.md](ARCHITECTURE.md)** is the current-state map — how the pipeline is built, plus §15's **Invariants** (I1–I5), the canonical governing rules. Read it to orient; it's kept lean so it stays loadable every session. **[DECISIONS.md](DECISIONS.md)** is the dated, numbered history of specific decisions (D1, D2, …) — the rationale and tradeoff for each — read on demand when you need the *why* behind a past choice.

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

**Run the suite:**

```
~/.local/pipx/venvs/watchdog-intel/bin/pytest
```

(`pipx run pytest` creates an isolated venv without watchdog's deps and will fail to collect most tests — don't use it for development.)

Tests use `tmp_path` and `monkeypatch` to redirect `WATCHDOG_HOME`, `PROJECTS_FILE`, and `CONFIG_FILE` away from the real home directory — patch all three when testing anything that touches the registry or projects list. See the `wdg_home` and `configured` fixtures in `tests/test_cli.py` for the pattern.

CI runs on every push and PR via `.github/workflows/ci.yml`.

## Linting

Ruff runs in CI as a dedicated, blocking `lint` job (separate from the test matrix, run once). Run it locally before pushing:

```
pipx run ruff check src tests
```

The rule set is deliberately conservative — pyflakes (`F`) plus the pycodestyle logical-error subsets `E4`/`E7`/`E9`. It catches unused imports/variables and real logical errors, **not** formatting: line-length (`E501`) and import-sorting (`I`) are intentionally not enforced, and there is no autoformatter. `cli.py` is exempt from `F401` because it deliberately re-exports a wide surface for test monkeypatching (see the `# noqa` on `import sys` there); a genuine unused import anywhere else will still fail CI. See DECISIONS D106.

---

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
