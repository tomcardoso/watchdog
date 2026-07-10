# Getting started

This walkthrough takes you through your first investigation from start to finish — creating a vault, dropping in documents, running the pipeline, and reading the results in Obsidian. It assumes Watchdog is already installed and set up; if not, start with the [installation guide](install.md).

Two things to keep in mind before you begin.

> **Public records only.** Your original files never leave your computer — all the file conversion and OCR happens locally. But the extracted text of each document (a plain-text representation of its contents) is sent to a cloud AI model for analysis. That is why Watchdog must only be used on documents that are public or presumptively public. Never process confidential source material, leaked documents, private correspondence, or anything that could identify a source. If in doubt, do not process it.

> **Verify before you publish.** AI extraction makes mistakes. Every fact Watchdog extracts links back to its source document and page, and facts the model inferred rather than read are marked `(inferred)` — those are leads, not findings. Follow the link before you publish anything.

## Create the vault

Each investigation lives in its own vault — a folder of linked notes that you read in Obsidian. Create one:

```bash
watchdog new
```

Watchdog prompts you for a name and an optional one-line description. The description pre-seeds `context.md` and is stored in your project registry, which is useful when you have several investigations open at once.

If you would rather skip the prompts, pass everything on the command line:

```bash
watchdog new "Shell Company Investigation" --description "Offshore owners behind city-adjacent land deals"
```

Use a name that will still make sense in six months. Watchdog creates a folder in your configured projects directory — by default, `~/Investigations/shell-company-investigation` — and sets up everything inside it:

- The vault directory, with the full folder structure
- An Obsidian vault, registered in Obsidian's settings so you can open it immediately
- A Claude Code project configured inside the vault
- Template files: `hot.md`, `log.md`, `context.md`, `index.md`

Open the vault in Obsidian:

```bash
watchdog obsidian shell-company-investigation
```

You will see an empty vault with the folder structure in place. Content comes after ingestion.

## Seed your context (optional but recommended)

Before dropping in records, it helps to tell Watchdog what you are investigating. This is especially useful for large or long-running investigations.

First, copy any background material into the `_CONTEXT/` folder inside the vault — prior published stories, notes, screenshots of relevant web pages, anything that describes the investigation's scope. Then, from inside the vault directory, run:

```bash
watchdog context
```

Claude reads the material and interviews you — who the key people and companies are, what you are looking for, what documents you expect. It then writes `context.md`, an investigative brief that persists across every future session and tells Claude what you already know. It also proposes a short list of watchlist candidates — names, companies, and addresses drawn from the same material — for you to accept, edit, or skip. Anything you approve is added to `watchlist.md` right away.

This step is optional, but it noticeably improves the quality of extracted summaries and connection-finding. With a brief in `context.md`, Claude enters every session already oriented rather than starting cold.

## Drop in documents

Copy public records into the `_INCOMING/` folder inside your vault:

```
~/Investigations/shell-company-investigation/_INCOMING/
```

Watchdog handles PDFs (scanned or not), Word documents, spreadsheets, images, web pages, plain text, and — with an optional install — audio and video; see the [supported file types](vault.md#supported-file-types) table for the full list.

**Rename files before dropping them in.** Watchdog uses the filename when labelling documents. `shell-co-annual-report-2023.pdf` is useful; `scan0042.pdf` is not.

**Add sidecar files for provenance.** To record where a document came from, create a `.yml` file with the same base name alongside it:

```
shell-co-annual-report-2023.pdf
shell-co-annual-report-2023.yml
```

The `.yml` file can contain:

```yaml
source: https://www.sedar.com/filing/xyz
obtained: 2026-06-05
notes: Check the director change on page 12.
```

This context is merged into the document record and preserved through ingest. The [vault guide](vault.md#supported-file-types) covers the sidecar format in full.

**Duplicates are handled automatically.** A document that is byte-identical to one already ingested — even under a new name — is set aside in `_INCOMING/_SKIPPED/` rather than processed twice. A separate near-duplicate check flags similar-but-not-identical files (a redlined revision, say) for your review, but never skips them.

## Chew

Chewing is the mechanical preprocessing step. It runs entirely on your machine — no AI involved yet. From your terminal, go to the vault directory and run:

```bash
cd ~/Investigations/shell-company-investigation
watchdog chew
```

Chewing:

- Converts documents to structured text using Docling, a document-understanding library
- Detects scanned documents and applies OCR — the process of turning a scanned image into machine-readable text (Apple Vision on macOS; Tesseract on Linux and Windows)
- Splits large PDFs into chunks and processes them in parallel
- Detects near-duplicates

Each file produces an entry in the processing queue, and the original moves to internal staging. Nothing is written to the Obsidian vault yet.

Chewing has a privacy bonus: only the extracted text moves forward from this step. The metadata that rides along inside a file — the author and creator fields in a PDF, camera and location details in a photo, revision history in an office document — is never extracted, so it never reaches an AI model in the later steps. What the model eventually sees is the text itself, the file's name, and any sidecar notes you wrote. The original file, metadata intact, stays on your machine in the vault's document morgue.

Files are processed in parallel. Each shows a live status row while it is worked on, with an overall progress row beneath; finished files settle into the log above with one of three statuses: `OK` (queued for extraction), `SKP` (no text found — moved to `_INCOMING/_SKIPPED/`), or `ERR` (failed — moved to `_INCOMING/_FAILED/` with an explanation). Files with noisy OCR output show a `· garbled OCR` note alongside `OK` — they are still queued, but worth verifying after extraction. On macOS, you get a notification when the batch completes. If a file fails, see [troubleshooting](troubleshooting.md) for the common causes and fixes.

Press **Ctrl+C** to cancel a chew — the lock is cleaned up automatically and unfinished files stay in `_INCOMING/` for the next run.

When chewing finishes, Watchdog asks whether to ingest now (`Ingest now? [Y/n]`). Press Enter to start extraction straight away, or decline and it prints the command to run when you are ready.

To chew a single file rather than the whole folder:

```bash
watchdog chew path/to/specific-file.pdf
```

Flags for controlling how many files and chunks are processed in parallel are covered in the [command reference](commands.md).

## Ingest

Ingestion is the extraction step — the part that uses AI. From inside the vault directory, run:

```bash
watchdog ingest
```

This runs in your terminal; there is no Claude Code session to open. Watchdog scans the queue, shows a token estimate — tokens are the units AI usage is measured in — and asks you to confirm before starting. On a metered API key with prior runs, it also shows a rough dollar range based on this vault's own usage history. Run `watchdog ingest --estimate` any time to see that estimate without starting extraction.

For each document, the pipeline:

1. Reads the extracted text
2. Classifies the document type and loads the matching domain skill (34 built-in skills for corporate filings, court documents, real estate records, and more, plus a general fallback — see [skills](skills.md))
3. Extracts entities (people, companies, addresses) with page-level citations, flagging any fact it inferred rather than read
4. Extracts relationships between entities
5. Extracts datable events for the timeline
6. Checks for contradictions against entities already in the vault
7. Writes everything to the vault

Large documents can take several minutes each, so a long pause on a status row is normal.

When extraction is complete, a post-ingest step synthesizes prose for entities that appear in multiple documents, reconciles the timeline, and produces a **briefing** summarizing what was processed, what entities were found, connections to entities already in the vault, and anything unusual or worth following up. Read the briefing carefully — the connections section is often where the story is.

By default Watchdog uses Sonnet (Claude's mid-tier model) for extraction and Haiku (the fast, inexpensive tier) for classification and post-ingest. You can change the models, tune how much reasoning each stage spends, and pin a specific domain skill — per run or as persistent defaults. The [configuration guide](configuration.md) covers all of it, including how to cut cost.

Three features run alongside every ingest; each has its full treatment in the [investigating guide](investigating.md):

- **Watchlist.** If you have listed terms in the vault's `watchlist.md` — a name, company, address, or phrase, one per line — Watchdog scans every newly ingested document for them and writes matches to `briefings/alerts-<date>.md`, flagging them in the terminal too. See [the watchlist](investigating.md#the-watchlist).

- **Leads.** At the end of each ingest, a deterministic sweep of the entity graph flags things worth chasing — an entity named repeatedly but never profiled, for instance — and writes them to `briefings/leads-<date>.md`. See [leads](investigating.md#leads).

- **Resolving.** Once you have dealt with a lead or an alert, you can mark it done so it stops reappearing, which turns those reports into a shrinking to-do list. See [resolving items](investigating.md#resolving-items).

If you hit a rate limit — a temporary cap on how much you can send the model — ingest stops cleanly and re-running `watchdog ingest` picks up where it left off. For an unattended overnight batch, `--wait` sleeps through the limit and resumes automatically; see the [command reference](commands.md) for details.

## Explore the vault in Obsidian

After ingest, open Obsidian:

```bash
watchdog obsidian shell-company-investigation
```

To browse the raw files in Finder or your file explorer instead, run `watchdog open shell-company-investigation`. From inside the vault directory, both commands work without the name.

The vault now contains one note per person, company, and address found in any document (`entities/`), one note per ingested document (`documents/`), a current-state summary of the investigation (`hot.md`), and a running record of every ingest session (`log.md`). The [vault guide](vault.md) explains every folder and file, including the anatomy of an entity note.

The vault also has a dashboard. `dashboard.base` is a set of live tables — most-mentioned entities, recent documents, people, companies, single-source entities to review, possible duplicates — that refresh as you ingest; `index.md` is a landing page that links to it. The tables use Obsidian Bases, a core Obsidian feature (version 1.9 and up), so there is nothing to install. Click a column header to sort; click a row to open the note. If a "Possible duplicates" row turns out to be the same entity extracted twice, see [duplicate entities](investigating.md#duplicate-entities) for the fix.

Use Obsidian's graph view to see the relationship network across the whole investigation. Entities that appear in many documents, or that connect to many other entities, are visually prominent.

For deeper network analysis in a dedicated graph tool, `watchdog export` writes the entity and relationship graph as CSV files loadable in Neo4j or Gephi; see the [command reference](commands.md) for the options.

## Ask questions in a fresh session

When ingest finishes, open a **new** Claude Code session to ask investigation questions — do not reuse a session that has been sitting open. At the start of each session, Claude reads `hot.md` automatically, so it knows the current state of the investigation without re-reading the vault. A fresh session has the full working room it needs for your questions; a stale one is carrying leftover baggage that crowds that room out.

From inside a Claude Code session with the vault open:

```
/watchdog-query Who are the directors of Shell Co Ltd?
```

Claude answers using only the documents in your vault, and cites the source for every claim.

## Where next

The [investigating guide](investigating.md) covers everything you do from here — asking questions, searching, finding connections, researching on the web, and running the investigation day to day.
