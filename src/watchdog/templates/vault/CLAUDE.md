# {name} — Watchdog

At the start of every session: (1) read `hot.md` for a summary of recent activity and open questions; (2) read `context.md` to understand what this investigation is about; (3) check `.watchdog/queue/` for files ready to extract — if any are present, run `/watchdog-ingest` before doing anything else.


## Vault layout

| Path | Purpose |
|------|---------| 
| `_INCOMING/` | Drop zone — drag files here, then run `watchdog chew` in your terminal |
| `_INCOMING/_FAILED/` | Created on failure — files that could not be processed |
| `_CONTEXT/` | Background material (prior stories, notes) — run `/watchdog-context` to seed context.md |
| `morgue/` | Original files after successful ingest |
| `.watchdog/queue/` | Chewed files ready for extraction — populated by `watchdog chew` |
| `.watchdog/staging/` | Original files waiting to move to morgue after ingest |
| `.watchdog/Registry/` | Internal state — do not edit manually |
| `entities/` | One note per real-world entity |
| `documents/` | One note per ingested document |
| `briefings/` | Post-ingest briefing notes |
| `wiki/` | Investigation thread pages |
| `hot.md` | Session-to-session context cache — updated after every ingest |
| `log.md` | Append-only ingest history — human-readable in Obsidian |
| `context.md` | Your investigation intent and key questions — read this before every skill |

## Hard rules

1. Public records only — never process confidential source material, private correspondence, or leaked documents. If a document cannot be identified as a public record, stop and ask before proceeding.
2. Registry updates are atomic with note creation — never one without the other.
3. No duplicate entities — check `.watchdog/Registry/manifest.json` before creating (it is lighter than `entities.json` and contains id, name, type, aliases, and note_path).
4. Entity IDs are kebab-case: `john-doe`, `shell-co-ltd`, `123-main-st`.
5. Every extracted fact must carry a confidence level: `high`, `medium`, `low`, or `disputed`. A `low`-confidence fact is a lead, not a finding.
6. The `## Notes` section in any note is reserved for journalist annotations — never overwrite it.
7. Acquire `.watchdog/Registry/.ingest-lock` before any vault writes; release it on completion or failure.

## Commands

| Command | Action |
|---------|--------|
| `/watchdog-context` | Seed context.md from background files in `_CONTEXT/` |
| `/watchdog-ingest` | Extract all preprocessed files in `.watchdog/queue/` |
| `/watchdog-ingest [file]` | Preprocess and extract a specific file |
| `/watchdog-query [question]` | Answer a question from the vault |
| `/watchdog-surface` | Find connections and anomalies across the vault |
| `/watchdog-wiki` | Create or update investigation thread pages |
| `/watchdog-health` | Check vault integrity |

## Confidence levels

| Level | When to use |
|-------|-------------|
| `high` | Fact directly stated in the source document |
| `medium` | Fact stated but requires one short inference |
| `low` | Fact inferred across multiple sources |
| `disputed` | Fact contradicted by another source in the vault |
