# Getting Started with Watchdog

This guide walks you through a complete investigation from start to finish — creating a vault, dropping in documents, running the pipeline, and reading the results in Obsidian.

It assumes Watchdog is already installed and set up. If not, start with [INSTALL.md](INSTALL.md). It also assumes Claude Code is authenticated — either via a Claude.ai Pro or Max subscription, or an Anthropic API key (`claude login` to set that up).

---

## What Watchdog does

You accumulate public records — FOIA responses, corporate filings, court documents, land records. Reading all of them is impossible. Missing a connection between them is the story you don't write.

Watchdog handles the mechanical reading. It converts every document to structured text, extracts every person, company, address, and relationship it finds, and stores them as a linked knowledge graph in an Obsidian vault. You can then search across the entire document set in plain English, ask questions, and surface connections.

Two things to keep in mind before starting:

1. **Public records only.** Every document Watchdog processes is read by an AI. Do not process documents from confidential sources, leaked materials, or anything obtained under a promise of confidentiality. If in doubt, do not process it.

2. **Verify everything.** Watchdog extracts facts and assigns confidence levels, but AI makes mistakes. Every extracted claim links back to a source document and page. Follow the link before you publish anything.

---

## Step 1: Create the vault

```bash
watchdog new
```

Watchdog will prompt you for a name and an optional one-line description. The description pre-seeds `context.md` and is stored in your project registry — useful when you have several investigations open at once.

If you'd rather skip the prompts, pass everything on the command line:

```bash
watchdog new "Shell Company Investigation" --description "Offshore owners behind city-adjacent land deals"
```

Use a name that will still make sense in six months. Watchdog creates a folder at your configured projects directory (default: `~/Investigations/shell-company-investigation`) and sets up everything inside it.

What just happened:
- The vault directory was created with the full folder structure
- An Obsidian vault was registered in Obsidian's settings — you can open it immediately
- A Claude Code project was configured inside the vault
- Template files (`hot.md`, `log.md`, `context.md`, `index.md`) were created

Open the vault in Obsidian:

```bash
watchdog obsidian shell-company-investigation
```

You'll see an empty vault with the folder structure in place. It won't have any content yet — that comes after ingestion.

---

## Step 2: Seed your investigation context (optional but recommended)

Before dropping in records, it helps to give Watchdog context about what you're investigating. This is especially useful for large or long-running investigations.

1. Copy any background material into `_CONTEXT/` inside the vault — prior published stories, notes, screenshots of relevant web pages, anything that describes the investigation's scope.

2. Open Claude Code with the vault as the project, and run:

```
/watchdog-context
```

Claude will read the material and ask you a series of questions — who the key people and companies are, what you're looking for, what documents you're expecting. It then writes `context.md`, which persists across every future session and tells Claude what you already know.

This step is optional, but it significantly improves the quality of extracted summaries and the usefulness of connection-finding. An investigative brief in `context.md` means Claude enters every session already oriented to your investigation rather than starting cold.

---

## Step 3: Drop in documents

Copy public records into the `_INCOMING/` folder inside your vault.

```
~/Investigations/shell-company-investigation/_INCOMING/
```

Supported file types: PDF (scanned or text-based), Word documents (`.docx`), Excel spreadsheets (`.xlsx`), images (JPEG, PNG, TIFF), web pages (HTML), and plain text files.

A few practical tips:

**Rename files before dropping them in.** Watchdog uses the filename when labeling documents. `shell-co-annual-report-2023.pdf` is useful. `scan0042.pdf` is not. Rename files to something descriptive before adding them.

**Add sidecar files for provenance.** If you want to record where a document came from, create a `.yml` file with the same base name alongside it:

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

This context is merged into the document record and preserved through ingest.

**Near-duplicate detection is automatic.** Watchdog fingerprints every document using a hash of its content. If you drop in a document that's already been ingested — even renamed — it will be flagged as a duplicate and skipped.

---

## Step 4: Chew

From your terminal, navigate to the vault directory and run:

```bash
cd ~/Investigations/shell-company-investigation
watchdog chew
```

Chewing does the mechanical preprocessing work that runs outside Claude Code:

- Converts documents to structured text using Docling
- Detects and applies OCR to scanned documents (Apple Vision on macOS; Tesseract on Linux/Windows)
- Splits large PDFs into chunks and processes them in parallel
- Embeds documents for semantic search
- Detects near-duplicates

Each file produces a `.json` file in `.watchdog/queue/` containing the extracted text and metadata. The original file moves to `.watchdog/staging/`. Nothing is written to the Obsidian vault yet.

You'll see a progress bar as each file is processed. Each file shows one of three statuses: `OK` (queued for extraction), `SKP` (no text found — moved to `_INCOMING/_SKIPPED/`), or `ERR` (failed — moved to `_INCOMING/_FAILED/`). Files with noisy OCR output show a `· garbled OCR` note alongside `OK` — they're still queued, but worth verifying after extraction. On macOS, you'll receive a notification when the batch completes.

If a file fails (password-protected, corrupted, unsupported format), it moves to `_INCOMING/_FAILED/` with an error message. Fix the issue and move the file back to `_INCOMING/` to retry.

Press **Ctrl+C** to cancel a chew — the lock is cleaned up automatically and unfinished files remain in `_INCOMING/` for the next run.

When chewing finishes, run the next step from the same vault directory:

```bash
watchdog ingest
```

To chew a single specific file rather than the entire `_INCOMING/` folder:

```bash
watchdog chew path/to/specific-file.pdf
```

To override parallelism for a single run:

```bash
watchdog chew --chew-workers 4    # parallel files (default: adaptive)
watchdog chew --chunk-workers 2   # parallel chunks per file, for large PDFs
```

Both flags override the persistent `chew_workers` / `chunk_workers` settings from `watchdog configure` for that run only.

---

## Step 5: Open in Claude Code

From inside the vault directory, run:

```bash
watchdog ingest
```

Watchdog scans the queue and prompts you to open Claude Code. Once it's open, run:

```
/watchdog-ingest
```

Claude works through each chewed file in the queue, processing up to 5 documents in parallel. For each document, it:

1. Reads the extracted text
2. Identifies the document type and loads the relevant extraction skill (there are 34 built-in skills for corporate filings, court documents, real estate records, and more)
3. Extracts entities (people, companies, addresses) with page-level citations and confidence levels
4. Extracts relationships between entities
5. Extracts datable events for the timeline
6. Checks for contradictions against entities already in the vault
7. Writes everything to the vault

When extraction is complete, Claude produces a **post-ingest briefing** summarizing:
- What documents were processed and what types they were
- What entities were found and which already existed in the vault
- Connections between entities — shared addresses, overlapping roles, entities appearing across multiple documents
- Anything unusual or worth following up

Read the briefing carefully. The connections section is often where the story is.

For large batches (hundreds of documents), you can limit how many are processed per session:

```
/watchdog-ingest --limit 50
```

This processes 50 documents and stops cleanly. Start a new Claude Code session and run it again to continue. Files already ingested are in `morgue/` and won't be touched again.

---

## Step 6: Explore the vault in Obsidian

After ingest, open Obsidian:

```bash
watchdog obsidian shell-company-investigation
# or, from inside the vault directory:
watchdog obsidian
```

To browse the raw vault files in Finder / your file explorer:

```bash
watchdog open shell-company-investigation
# or, from inside the vault directory:
watchdog open
```

The vault now contains:

- **`entities/person/`** — one note per person mentioned in any document
- **`entities/company/`** — one note per company
- **`entities/address/`** — one note per address
- **`documents/`** — one note per ingested document
- **`hot.md`** — a current-state summary of the investigation, rewritten after every ingest
- **`log.md`** — a running record of every ingest session

Each entity note has the same structure:

- **Summary** — synthesized overview of who this entity is; rewritten on each ingest as new documents add context
- **Analysis** — accumulated investigative observations; never overwritten, only appended
- **Timeline** — chronological events involving this entity
- **Relationships** — connections to other entities, with source citations
- **Notes** — reserved for your own annotations; Watchdog never touches this section

Every link to a source document includes a direct page link, so you can jump from any extracted fact to the exact page it came from.

Use Obsidian's graph view to see the relationship network across your entire investigation. Entities that appear in many documents, or that are connected to many other entities, will be visually prominent.

---

## Asking questions

From inside a Claude Code session with the vault open:

```
/watchdog-query Who are the directors of Shell Co Ltd?
/watchdog-query Which companies share the address 123 Main St?
/watchdog-query What happened in 2019 involving Alice Smith?
```

Claude answers using only the documents and entities in your vault, and cites the source for every claim.

Semantic search is available directly from the terminal:

```bash
watchdog search shell-company-investigation "offshore account transfers"
watchdog search shell-company-investigation "shell company director" --top 10
```

This returns raw document pages and vault notes ranked by semantic similarity to your query.

---

## Finding connections

```
/watchdog-surface
```

This runs a full connection analysis across the entire vault. Claude looks for:

- Addresses shared by entities with no other apparent relationship
- People appearing in unusual roles (director of one company, beneficiary of another)
- Entities mentioned across many unrelated documents
- Chronological anomalies in timelines
- Relationships that were flagged as contradictions

Run this after each significant batch of ingest. The connections it surfaces are often the leads that require the most follow-up.

---

## Subsequent sessions

After the first ingest, the typical workflow is:

1. **Drop new documents** into `_INCOMING/`
2. **`watchdog chew`** from the vault directory (or `watchdog watch <name>` to chew automatically as files arrive)
3. **`watchdog ingest`** to set up the session and open Claude Code
4. **`/watchdog-ingest`** in Claude Code
5. **Read the briefing** — pay particular attention to connections with entities already in the vault
6. **`/watchdog-surface`** if the new batch was substantial

Claude Code doesn't need to be open while you're chewing. The queue accumulates until you're ready to run extraction.

At the start of each Claude Code session, Claude reads `hot.md` automatically — a current-state summary of the investigation that tells it what's already known without re-reading the entire vault. This is what makes it possible to continue an investigation across many separate sessions without losing context.

---

## Confidence levels

Every extracted fact carries one of four confidence levels:

| Level | Meaning |
|-------|---------|
| `high` | Explicitly stated in the document; direct quote or clear figure |
| `medium` | Reasonably inferred from document context |
| `low` | Plausible but uncertain — a lead, not a finding |
| `disputed` | Contradicts another fact already in the vault from a different source |

Treat `low`-confidence facts as leads that require verification, not as established facts. `disputed` facts should be examined carefully — the contradiction itself is often newsworthy.

---

## Managing investigations

**Check status at any time:**

```bash
watchdog status shell-company-investigation
```

Shows document and entity counts, pending files in `_INCOMING/`, files awaiting extraction, and last-updated date.

**View ingest history:**

```bash
watchdog log shell-company-investigation
watchdog log shell-company-investigation --lines 50   # last 50 lines
```

**List all investigations:**

```bash
watchdog list
```

**Archive when done:**
When an investigation concludes, archive it to keep your list clean. Archived investigations are hidden from `watchdog list` by default but nothing is deleted.

```bash
watchdog archive shell-company-investigation
watchdog list --all   # see archived investigations when needed
watchdog unarchive shell-company-investigation   # restore if needed
```

**Rename an investigation:**

```bash
watchdog rename shell-company-investigation "Oil Company Investigation"
```

Renames the vault folder, updates the registry, and updates the Obsidian vault entry. Blocked if a chew or ingest is in progress.

**Move a vault:**
If you reorganize your filesystem, update the registry:

```bash
watchdog move shell-company-investigation /Volumes/Archive/Investigations
```

If the files haven't been moved yet, Watchdog moves them. If you've already moved them manually, it just updates the registry.

**Remove an investigation:**

```bash
watchdog delete shell-company-investigation            # remove from registry; vault files stay on disk
watchdog delete shell-company-investigation --purge    # also permanently delete all vault files
```

`--purge` requires explicit confirmation and is permanent. Use `archive` instead if you might want the vault later.
