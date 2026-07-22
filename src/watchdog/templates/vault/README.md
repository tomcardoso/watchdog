# {name}

An investigation vault built with **[Watchdog](https://github.com/tomcardoso/watchdog)** — document intelligence for investigative journalism. Drop public records in; get linked, source-cited notes you can explore in [Obsidian](https://obsidian.md).

> Created with Watchdog `v{version}` · [GitHub](https://github.com/tomcardoso/watchdog) · [Report an issue](https://github.com/tomcardoso/watchdog/issues)

## ⚠️ Public records only

Never add confidential source material, leaked documents, private correspondence, or anything obtained under a promise of confidentiality. **Every document in here is read by an AI** — there is no taking that back.

## How to use it

1. Drop documents into `_INCOMING/`.
2. From this folder in your terminal:
   - `watchdog chew` — OCR and prepare the documents
   - `watchdog dig` — extract entities, relationships, and timelines
   - `watchdog bark` — reconcile, synthesize, and write the briefing
3. Browse the results in [Obsidian](https://obsidian.md), or open this folder in Claude Code to ask questions across the whole vault.

## Common commands

In your terminal, from this folder:

| Command | What it does |
|---------|--------------|
| `watchdog chew` | Process the files in `_INCOMING/` |
| `watchdog dig` | Extract from the chewed documents |
| `watchdog bark` | Finish post-processing if an ingest was interrupted |
| `watchdog status` | Vault stats, plus anything queued or pending |
| `watchdog requeue` | Retry documents that failed extraction |

In a Claude Code session opened on this folder:

| Command | What it does |
|---------|--------------|
| `/watchdog-query <question>` | Answer a question from the vault |
| `/watchdog-surface` | Surface connections and anomalies |
| `/watchdog-wiki` | Build investigation thread pages |

## What's in here

| Path | Purpose |
|------|---------|
| `_INCOMING/` | Drop zone for new documents |
| `_CONTEXT/` | Background material (prior stories, notes) that seeds the investigation |
| `entities/` | One note per person, company, address… |
| `documents/` | One note per ingested document |
| `briefings/` | Post-ingest summaries of what was found |
| `timeline.md` | Chronology across the whole investigation |
| `morgue/` | Your original files, kept after ingest |
| `.watchdog/` | Internal state — leave it alone |
