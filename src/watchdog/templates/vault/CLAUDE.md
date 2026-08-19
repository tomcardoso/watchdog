# {name} — Watchdog

At the start of every session: (1) read `hot.md` for a summary of recent activity and open questions; (2) read `context.md` to understand what this investigation is about; (3) check `.watchdog/ingest-state.json` — if it exists, an ingest is running or was interrupted; tell the user to run `watchdog dig` in their terminal to resume it before doing anything else.


## Vault layout

| Path | Purpose |
|------|---------| 
| `_INCOMING/` | Drop zone — drag files here, then run `watchdog chew` in your terminal |
| `_INCOMING/_FAILED/` | Created on failure — files that could not be processed |
| `_CONTEXT/` | Background material (prior stories, notes) — run `/watchdog-context` to seed context.md |
| `morgue/` | Original files after successful ingest |
| `.watchdog/queue/` | Chewed files ready for extraction — populated by `watchdog chew` |
| `.watchdog/staging/` | Original files waiting to move to morgue after ingest |
| `.watchdog/registry/` | Internal state — do not edit manually |
| `entities/` | One note per real-world entity |
| `documents/` | One note per ingested document |
| `briefings/` | Post-ingest briefing notes |
| `wiki/` | Investigation thread pages — matured angles that deepen over time |
| `queries/` | Saved answers to questions you've asked — substantive findings filed here so explorations compound |
| `hot.md` | Session-to-session context cache — updated after every ingest |
| `log.md` | Append-only ingest history — human-readable in Obsidian |
| `context.md` | Your investigation intent and key questions — read this before every skill |

## Pre-authorized operations

The following are auto-allowed in `.claude/settings.json` — never ask for confirmation, never use workarounds:

| Operation | Permitted pattern |
|-----------|------------------|
| Read any file within this vault | always allowed |
| Write/edit files in `.watchdog/tmp/` | auto-allowed |
| Write/edit files in `.watchdog/registry/` | auto-allowed |
| Write/edit briefing notes in `briefings/` | auto-allowed |
| Write/edit `hot.md` | auto-allowed |
| Write/edit `log.md` | auto-allowed |
| `watchdog queue-status` | auto-allowed |
| `watchdog entity-index` | auto-allowed |
| `watchdog is-duplicate <sha256>` | auto-allowed |
| `watchdog write-entity --entity-id <id>` | auto-allowed |
| `watchdog unlock` | auto-allowed |
| `find .watchdog/queue/ …` | auto-allowed |

**What is NOT permitted and must never be attempted:** `mkdir`, `which`, `pip show`, `python3 -c "…"`, `watchdog <cmd> --help`, shell pipelines to parse JSON (`cat … | python3 -c "…"`), absolute paths in any bash command, or `cd <path> &&` prefixes. Always use paths relative to the vault root. `.watchdog/tmp/` always exists — do not create it.

**To read registry files** (`.watchdog/registry/entities.json`, `documents.json`, `manifest.json`, etc.), use the Read tool directly — never shell out to parse them.

**Never pass `--vault` to any watchdog command.** All watchdog commands default to the current directory as the vault. Passing `--vault` could affect a different project.

## Hard rules

1. Public records only — never process confidential source material, private correspondence, or leaked documents. If a document cannot be identified as a public record, stop and ask before proceeding.
2. Registry updates are atomic with note creation — never one without the other.
3. No duplicate entities — check `.watchdog/registry/manifest.json` before creating (it is lighter than `entities.json` and contains id, name, type, aliases, and note_path).
4. Entity IDs are kebab-case: `john-doe`, `shell-co-ltd`, `123-main-st`.
5. Every extracted fact records its `basis`: `stated` (directly in the document) or `inferred` (reasoned from it). An `inferred` fact is a lead, not a finding. `stated` is the default and is left implicit; only `inferred` facts are marked.
6. The `## Notes` section in any note is reserved for journalist annotations — never overwrite it.
7. Acquire `.watchdog/registry/.ingest-lock` before any vault writes; release it on completion or failure.

## Commands

| Command | Action |
|---------|--------|
| `/watchdog-context` | Seed context.md from background files in `_CONTEXT/` |
| `/watchdog-query [question]` | Answer a question from the vault; file substantive answers to `queries/` |
| `/watchdog-surface` | Find connections and anomalies across the vault |
| `/watchdog-wiki` | Create or update investigation thread pages |
| `/watchdog-health` | Check vault integrity |

## Compounding — file what you find

An investigation compounds when findings are written down instead of re-derived from scratch each session. Whenever a question you answer or an analysis you run in this session produces a **substantive** finding — a synthesis across documents, a surfaced connection, a resolved or newly-raised question — file it to `queries/<slug>.md` so it persists (preserve the citations; never touch a `## Notes` section). Skip trivial single-fact lookups. When a finding matures into an angle — at least two entities connected by at least two documents — promote it to a `wiki/` thread with `/watchdog-wiki`. This is the difference between an investigation that accumulates knowledge and one that starts cold every time.

---

## Fact basis

| Basis | When to use |
|-------|-------------|
| `stated` | Fact directly stated in the source document (the default — left implicit) |
| `inferred` | Fact reasoned from the document rather than stated outright — a lead to verify, not a finding (rendered as *(inferred)*) |

A fact that *conflicts* with another source is not a basis level — it is captured by a `[!contradiction]` callout in the entity's note.
