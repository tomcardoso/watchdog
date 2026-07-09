# Watchdog — developer notes

## Architecture

Two files, split by how often they're read. **[ARCHITECTURE.md](ARCHITECTURE.md)** is the current-state map — how the pipeline is built, plus §15's **Invariants** (I1–I5), the canonical governing rules. Read it to orient; it's kept lean so it stays loadable every session. **[DECISIONS.md](DECISIONS.md)** is the dated, numbered history of specific decisions (D1, D2, …) — the rationale and tradeoff for each — read on demand when you need the *why* behind a past choice.

**When a change alters the pipeline's structure, the split between deterministic code and the model, or the vault/registry layout, update `ARCHITECTURE.md` in the same change**, and append a `### D<n>` entry to `DECISIONS.md` (ascending order, newest last). If the change establishes or revises a governing rule, update the Invariants section in `ARCHITECTURE.md` too. **Keep decision entries concise** — a few sentences of rationale, then the tradeoff; the full record is in git and the PR, not the log. A decision earns an entry only if it forecloses a future option or would read as a bug without the rationale; pure refactors belong in the commit message. Treat both as part of the definition of done, like tests.

## Documentation

**When a change adds, removes, or modifies anything user-facing — CLI flags, `watchdog configure` keys, CLI commands, default values, or workflow steps — update the affected pages under `docs/` in the same change.** Every topic has exactly one canonical page; update that page and let the others keep linking to it, rather than re-explaining the topic in several places:

- `docs/commands.md` — CLI commands, flags, and slash commands (the reference)
- `docs/configuration.md` — `watchdog configure` keys, defaults, model backends, cost
- `docs/install.md` — install steps, prerequisites, optional installs
- `docs/getting-started.md` / `docs/investigating.md` — workflow walkthroughs
- `docs/vault.md` — vault layout, entity-note structure, supported file types
- `docs/skills.md` — the record-skill catalog (update when adding a skill)
- `docs/troubleshooting.md` — failure modes and fixes

`README.md` is a deliberately slim front door (~120 lines) — it names capabilities but documents nothing in detail; it only changes when the pitch, requirements, install two-liner, or quick start change. Do not add command tables, configuration keys, or workflow detail back into it. The docs are written for working journalists who may never have used a terminal: serious, precise, conversational — no exclamation marks, no hype, short paragraphs, jargon defined at first use, Canadian English. Treat doc updates as part of the definition of done, like tests.

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

### Where skills live

Record (domain) skill files live in `src/watchdog/skills/records/`. They are plain markdown and **global** — the ingest orchestrator reads them directly from the package via `watchdog.skills_catalog` (they are no longer copied into each vault). No code changes are needed when adding one. A user can add their own skills in `~/.watchdog/skills/records/`, which the catalog merges in (a user skill overrides a package skill of the same name). See DECISIONS D21.

### Standard structure

A blank template is at `src/watchdog/skills/records/_template.md` — copy it as the starting point for any new skill. Files starting with `_` are excluded from the catalog.

Every skill file should follow this structure in order:

0. **Frontmatter** — a YAML block with a `description:` line. This one line is the skill's entry in the classifier index, so make it a precise summary of the document types covered. (If omitted, the index falls back to the first sentence of the intro.)
1. **Intro paragraph** — one or two sentences explaining when this skill is loaded by `/ingest`. Name the document types that trigger it.
2. **Document types covered** — a bulleted list of the specific document types the skill applies to. This is the one section where it is acceptable to list jurisdiction-specific document names (since those are the literal names of the documents). Group by jurisdiction if there are many.
3. **Always-present fields table** — a two-column table (`Field` | `What to look for`) listing the fields that appear in virtually every document of this type. Extract these even when not prominently displayed.
4. **Red flags section** — the most important section. Use sub-headings to group related red flags. Each red flag should be a bolded label followed by a sentence or two explaining what to look for and why it matters. Write for pattern recognition, not just field extraction.
5. **Terminology table(s)** — one or more two-column tables (`Term` | `Meaning`) covering jargon a journalist would encounter. If the terminology varies significantly by jurisdiction, use a three-column table (`Term` | `Jurisdiction` | `Meaning`) or separate tables per jurisdiction.
6. **Relationships to extract** — a numbered list of entity relationships the skill should produce (e.g. `Person → Company: Director`). Use the `→` notation.
7. **What investigators typically miss** — a numbered list of six to eight specific things that experienced journalists often overlook when reading this document type. Be concrete and specific.
8. **Sources and further reading** — three subsections: **Official and regulatory** (government agencies, regulators, FATF, OECD, accounting standards bodies), **Practitioner and public interest** (law firm guides, NGO reports, public interest organizations), and **Journalism resources** (publicly accessible tipsheets, press freedom organizations). Omit a subsection entirely if there is nothing worth citing. End with a **Notes on unsourced claims** paragraph for any red flag claims that could not be traced to a specific source — these are flagged for editorial review, not silently included as fact. Every claim in the red flags section should be traceable to at least one source in this section.

### Authoring principles

- **Jurisdiction-agnostic by default.** Lead with principles and patterns that apply anywhere. Specific jurisdictions are examples, not the default frame. A journalist in Brazil or Germany should find the skill useful.
- **Jurisdiction-specific terminology tables are valuable** — but position them clearly as jurisdiction guides, not as the primary content. The always-present fields and red flags sections must be universal.
- **The red flags section is the most important.** This is where the skill earns its value. Think about what a twenty-year veteran investigative journalist would notice that a first-year reporter would miss.
- **Write for a smart investigative journalist, not a specialist.** Assume the reader knows how journalism works but may not know the specific document type deeply. Explain jargon; don't assume it.
- **Be specific.** "Look for unusual transactions" is useless. "A property transferred three or more times in 12 months may be involved in title fraud, mortgage fraud, or money laundering" is useful.

### Before writing a new skill, ask the user

1. What document type are you working with? (Get a sample if possible.)
2. What jurisdiction(s) are most common for your work? (This shapes the terminology table.)
3. Are there existing skills that overlap? (Check `src/watchdog/skills/records/` first — some document types are covered from a related angle by an existing skill.)

If the new skill would overlap significantly with an existing one, consider extending the existing skill rather than creating a new file.

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

All terminal output in `cli.py` follows a consistent visual language. The colour constants are defined at the top of the file — use them, never raw ANSI codes.

### Colour semantics

| Constant | Use for |
|----------|---------|
| `_BOLD` | Project names, important counts, section headers |
| `_DIM` | Secondary metadata: dates, slugs, path labels, quiet prompts |
| `_CYAN` | Actionable items: file paths, commands the user should type, directory names like `_INCOMING/` |
| `_GREEN` | Success states (`Created:`) |
| `_YELLOW` | Warnings (pending files, things that need attention) |
| `_RESET` | Always close every coloured span |

### Layout conventions

- **Indent everything 2 spaces** — all output lines start with `"  "`. The banner and list headers set this pattern; every command should match it.
- **Bold name, dim slug** — when showing a project, display its human name in bold and its slug in dim on the same line: `  **My Project**  [dim]my-project[/dim]`.
- **Cyan for paths, never dim** — file system paths and `watchdog …` commands the user should run are always `_CYAN`, not `_DIM`. Dim is for decorative/secondary text only.
- **Section headers: bold, no trailing colon** — e.g. `  **Documents by type**` not `Documents by type:`. The colon was dropped in the consistency pass.
- **Dim labels, normal counts** — in type-breakdown tables, the label is `_DIM`, the count is unstyled (so it reads at normal brightness).
- **No trailing colons on "Pending in" lines** — format is `Pending in _CYAN__INCOMING/_RESET  <label>`.

### Adding a new command

1. Print a blank line before the first content line and after the last, matching the spacing in `cmd_status`.
2. Use `_find_project` for any command that takes a project name — it handles prefix matching and exits cleanly.
3. Never call `print(f"Error: …")` and continue — use `sys.exit(f"Error: …")`.
4. If the command produces a success confirmation, use `_GREEN` for the label and `_BOLD` for the key value.

### Adding a new CLI alias

Add the alias → canonical mapping to `_ALIASES` at the top of `cli.py`. Aliases are resolved before argparse sees `sys.argv`, so they are invisible to `--help`. Add a parametrized test case to the `test_aliases_remap_argv` test in `tests/test_cli.py`.
