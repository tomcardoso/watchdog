# Getting Started with Watchdog

This guide walks you through a complete investigation from start to finish — creating a vault, dropping in documents, running the pipeline, and reading the results in Obsidian.

It assumes Watchdog is already installed and set up. If not, start with [INSTALL.md](INSTALL.md). It also assumes Claude Code is authenticated — either via a Claude.ai Pro or Max subscription, or an Anthropic API key (`claude login` to set that up).

---

## What Watchdog does

You accumulate public records — FOIA responses, corporate filings, court documents, land records. Reading and re-reading all of them is onerous, and if you miss a connection, that's a missed story.

Watchdog handles the mechanical reading. It converts every document to structured text, extracts every person, company, address, and relationship it finds, and stores them as a linked knowledge graph in an Obsidian vault. You can then search across the entire document set in plain English, ask questions, and surface connections.

Two things to keep in mind before starting:

1. **Public records only.** Every document Watchdog processes is read by an AI. Do not process documents from confidential sources, leaked materials, or anything obtained under a promise of confidentiality. If in doubt, do not process it.

2. **Verify everything.** Watchdog extracts facts and flags the ones it inferred rather than read, but AI makes mistakes. Every extracted claim links back to a source document and page. Follow the link before you publish anything.

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

2. From inside the vault directory, run:

```bash
watchdog context
```

Claude will read the material and ask you a series of questions — who the key people and companies are, what you're looking for, what documents you're expecting. Alongside the `context.md` draft, it also proposes a short list of watchlist candidates — names, companies, addresses drawn from that same material — for you to accept, edit, or skip; anything approved is appended to `watchlist.md` right away, so you don't have to remember to seed it separately. It then writes `context.md`, which persists across every future session and tells Claude what you already know.

This step is optional, but it significantly improves the quality of extracted summaries and the usefulness of connection-finding. An investigative brief in `context.md` means Claude enters every session already oriented to your investigation rather than starting cold.

---

## Step 3: Drop in documents

Copy public records into the `_INCOMING/` folder inside your vault.

```
~/Investigations/shell-company-investigation/_INCOMING/
```

See the [supported file types](README.md#supported-file-types) table in the README for the full list.

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
- Detects near-duplicates

Each file produces a `.json` file in `.watchdog/queue/` containing the extracted text and metadata. The original file moves to `.watchdog/staging/`. Nothing is written to the Obsidian vault yet.

Files are processed in parallel, each shown as a live status row while it's worked on, with an overall progress row beneath; finished files settle into the log above. Each settled file shows one of three statuses: `OK` (queued for extraction), `SKP` (no text found — moved to `_INCOMING/_SKIPPED/`), or `ERR` (failed — moved to `_INCOMING/_FAILED/`). Files with noisy OCR output show a `· garbled OCR` note alongside `OK` — they're still queued, but worth verifying after extraction. On macOS, you'll receive a notification when the batch completes.

If a file fails (password-protected, corrupted, unsupported format), it moves to `_INCOMING/_FAILED/` with an error message. Fix the issue and move the file back to `_INCOMING/` to retry.

Press **Ctrl+C** to cancel a chew — the lock is cleaned up automatically and unfinished files remain in `_INCOMING/` for the next run.

When chewing finishes, Watchdog asks whether to **ingest now** (`Ingest now? [Y/n]`). Press Enter (or `y`) to extract the queued documents straight away — no need to type the next command yourself. If you decline, it prints the command to run when you're ready:

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

### Already have a list of links?

If you have a batch of URLs — from a spreadsheet, a colleague, or your own browsing — hand them to `watchdog fetch` and Watchdog downloads each one into `_INCOMING/` for you, no research session needed:

```bash
watchdog fetch https://example.gov/filing https://news.example/article
watchdog fetch links.txt        # one URL per line (or the tab-separated form)
```

Each URL is validated, size-capped, and saved with a provenance sidecar — the same hygiene as web-research sources — then you `chew` and `ingest` as normal. HTML pages get a full rendered snapshot (images, styles, client-rendered content) if you've installed the optional capture browser (see [INSTALL.md](INSTALL.md)); otherwise they fall back to a sanitized plain fetch. (This is the "give me the URL, you fetch it faithfully" companion to the Obsidian Web Clipper, which clips already-rendered pages.)

---

## Step 5: Ingest

From inside the vault directory, run:

```bash
watchdog ingest
```

This runs the extraction pipeline **in your terminal** — there's no Claude Code session to open. Watchdog scans the queue, shows a token estimate (and, on a metered key with prior runs, a rough dollar range based on this vault's own usage history), confirms, and processes documents in parallel. The model is called only for the reasoning steps; everything mechanical runs in Python. (Authentication is set during `watchdog setup` — your Claude subscription or an API key; see INSTALL.) Run `watchdog ingest --estimate` any time to see that same estimate without starting extraction — useful for deciding whether to split a large batch.

By default Watchdog uses Sonnet for extraction, and Haiku for the post-ingest step (synthesis + timeline + briefing) and the quick document classification. Set persistent defaults with `watchdog configure`, or override per run:

```bash
watchdog ingest --extractor-model haiku             # faster, cheaper extraction
watchdog ingest --finalizer-model opus              # stronger synthesis + briefing
watchdog ingest --classifier-model sonnet           # stronger document classification
watchdog ingest --extractor-effort medium           # fewer thinking tokens — the main cost lever
watchdog ingest --extractor-model deepseek:deepseek-chat  # route extraction to another provider
watchdog ingest --extractor-model claude-batch:sonnet --skill corporate-filings  # bulk, half-price, on a metered key
watchdog ingest --concurrency 2                     # fewer docs in parallel (if you hit rate limits)
watchdog ingest --classify-pages 10                 # show the classifier more pages of each document
watchdog ingest --skill corporate-filings           # pin one record skill, skip classification
```

A model knob also accepts a `backend:model` form to run a stage on another provider (`openai:gpt-5-mini`, `deepseek:deepseek-chat`) — store the key first with `watchdog auth set openai|deepseek`. A plain tier keeps the stage on Claude. `claude-batch` is a special case: it submits extraction as one Anthropic Message Batch (50% off, requires a pinned skill and API-key auth) and **exits rather than waiting** — batches can take up to a day, so `watchdog ingest` submits and re-running it later collects the results. See [Model backends](README.md#model-backends) for the constraints and the full cost-saving recipe.

`--skill` with no value lists the available record skills and lets you pick one interactively; `--skill path/to/skill.md` pins an ad-hoc skill file. Run `watchdog show-skills` to see what the built-in skills cover (it also opens the skills folder on GitHub), and add your own in `~/.watchdog/skills/records/`. For a vault that's always one document type, set it once:

```bash
watchdog configure extractor_model haiku
watchdog configure classifier_model sonnet
watchdog configure extractor_effort medium
watchdog configure extract_concurrency 2
watchdog configure classify_pages 10
watchdog configure default_skill corporate-filings
```

Run `watchdog configure default_skill` with no value to pick from the catalog interactively (arrow keys to choose a skill, an "unset" row to turn pinning off, or "Type my own…" for a name or file path).

As a vault matures, extraction also carries forward every known entity a document mentions (so the extractor can dedup and catch contradictions) — and short entity *aliases* (initials, abbreviations) can false-match common words and quietly inflate the prompt. `preflight_alias_min_length` (default 3) sets the shortest alias allowed to match; the canonical name always matches at any length, so short real names like `BP` are unaffected. During ingest, watchdog prints each document's carried-forward digest size, so you can see whether this is worth tuning on your vault.

Not sure which knob you need? Run `watchdog configure` with no arguments: it prints every setting and its current value, then offers a wizard — an arrow-key menu of all the settings, so you can browse, read each one's help, and change values without memorizing key names.

For each document, the pipeline:

1. Reads the extracted text
2. Classifies the document type and loads the matching domain skill (34 built-in skills for corporate filings, court documents, real estate records, and more)
3. Extracts entities (people, companies, addresses) with page-level citations, flagging any fact it inferred rather than read
4. Extracts relationships between entities
5. Extracts datable events for the timeline
6. Checks for contradictions against entities already in the vault
7. Writes everything to the vault

When extraction is complete, the post-ingest step synthesizes prose for multi-mention entities, reconciles the timeline, and produces a **post-ingest briefing** summarizing:
- What documents were processed and what types they were
- What entities were found and which already existed in the vault
- Connections between entities — shared addresses, overlapping roles, entities appearing across multiple documents
- Anything unusual or worth following up

Read the briefing carefully. The connections section is often where the story is.

If you've listed any terms in the vault's `watchlist.md` (one per line — a name, company, address, or phrase), Watchdog also scans every newly-ingested document for them deterministically and, on a match, prints a terminal alert and writes the details (document, page, surrounding text, and a link to the matching entity if it resolved to one) to `briefings/alerts-<date>.md`. Matching is case-insensitive and whole-word; wrap a line in `/.../` for a regular expression. An empty watchlist does nothing.

Since this scan only ever looks at the run's own new documents, adding a term to `watchlist.md` after documents are already in the vault won't retroactively check them. Run `watchdog watchlist` to sweep every already-ingested document against the current `watchlist.md` — it writes to the same `briefings/alerts-<date>.md`.

Watchdog also runs a deterministic **lead sweep** over the whole entity graph at the end of each ingest, printing a one-line count and writing `briefings/leads-<date>.md`. It flags four things, all without a model call: entities named as a relationship target but never profiled (a company you should go find records on), entities that recur across several documents with no relationships at all, entities carrying unresolved contradiction flags, and entities carrying facts or roles the extractor flagged as inferred rather than stated outright — a lead to verify, not a finding. Re-run it any time with `watchdog leads`; a bare `watchdog` with nothing pending nudges you when leads are open.

Once you've dealt with a lead, a watch-word alert, or a specific contradiction, you can mark it done so it stops re-appearing. Every item in the leads and alerts files carries a short resolution id; run `watchdog resolve <id>` (the ids are printed next to each item), or just tick its `- [x]` checkbox in the briefing and run `watchdog resolve --sync`. Resolved items drop out of the next sweep, so `watchdog leads` and `watchdog watchlist` become a shrinking to-do list rather than an ever-growing wall. `watchdog resolve --list` shows what you've acknowledged; `watchdog unresolve <id>` brings an item back. Acknowledgments follow an entity through `watchdog merge-entities`.


If the same real-world person or company ends up extracted under two different entity ids — a name spelled differently across documents, most often — `watchdog merge-entities <keep-id> <merge-id>` folds the duplicate into the survivor: aliases, documents, relationships, and timeline events all combine onto one id, and every relationship elsewhere in the vault that named the losing id follows the merge. Run `watchdog reindex` afterward to drop the merged entity's stale search-index entries.

A failed document is logged to `.watchdog/Registry/ingest.log` and set aside in `.watchdog/queue/_failed/` — the rest of the batch still completes. For very large batches, chew and ingest in groups. When ingest finishes, **open a fresh Claude Code session** to ask investigation questions (`/watchdog-query`, `/watchdog-surface`).

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
- **`watchlist.md`** — terms to watch for in new documents; matches are written to `briefings/alerts-<date>.md`
- **`dashboard.base`** — a dashboard of live tables (most-mentioned entities, recent documents, people, companies, single-source entities to review, possible duplicates) that refresh as you ingest; **`index.md`** is a landing page that links to it

The dashboard is built on **Obsidian Bases**, a core Obsidian feature (version 1.9 and up), so there is nothing to add per vault — open `dashboard.base` and the tables are already rendered. Click a column header to sort (e.g. by **Documents** to surface the most-mentioned entities); click a row to open the note. The "Possible duplicates" and "Single-source entities to review" tables flag candidates for a look — `watchdog merge-entities <keep-id> <merge-id>` is the fix once you've confirmed two rows are the same entity.

Each entity note has the same structure:

- **Summary** — synthesized overview of who this entity is; rewritten on each ingest as new documents add context
- **Analysis** — accumulated investigative observations; never overwritten, only appended
- **Timeline** — chronological events involving this entity
- **Relationships** — connections to other entities, with source citations
- **Notes** — reserved for your own annotations; Watchdog never touches this section

Every link to a source document includes a direct page link, so you can jump from any extracted fact to the exact page it came from.

Use Obsidian's graph view to see the relationship network across your entire investigation. Entities that appear in many documents, or that are connected to many other entities, will be visually prominent.

For deeper network analysis in a dedicated graph tool, export the relationship graph:

```bash
watchdog export shell-company-investigation
```

This writes `nodes.csv` and `relationships.csv` (loadable with `neo4j-admin database import` or directly in Gephi) to a `shell-company-investigation-export/` folder; `--format cypher` writes a single `graph.cypher` of `MERGE` statements instead. The export reads the registry directly — no model calls — and reflects only what ingest-time entity deduplication resolved, so the same person under slightly different names may appear as separate nodes.

---

## Asking questions

From inside a Claude Code session with the vault open:

```
/watchdog-query Who are the directors of Shell Co Ltd?
/watchdog-query Which companies share the address 123 Main St?
/watchdog-query What happened in 2019 involving Alice Smith?
```

Claude answers using only the documents and entities in your vault, and cites the source for every claim.

Substantive answers don't vanish into the chat: `/watchdog-query` files anything that synthesises across documents or surfaces a connection to `queries/<slug>.md` (citations preserved), so your explorations accumulate instead of being re-derived each session. Trivial one-off lookups are skipped. When a finding grows into a real angle — two or more entities tied together by two or more documents — it graduates to a `wiki/` thread via `/watchdog-wiki`. Over a long investigation, `queries/` and `wiki/` become the compounding record of what you've worked out.

Search is available directly from the terminal. Source passages are ranked by a hybrid of *meaning* (embeddings) and *exact terms* (BM25), then reranked by a local cross-encoder for precision — so searching `"conflict of interest"` surfaces passages about recusals or related-party dealings even when that phrase never appears, while an exact token like a case number or dollar figure still lands its passage. It returns the matching **source passage with its page**, not a generated answer:

```bash
watchdog search shell-company-investigation "offshore account transfers"
watchdog search shell-company-investigation "shell company director" --top 10
```

Steer results with `+`/`-` phrases — lead a phrase with `-` to push away from it, `+` to pull toward another idea (the whole phrase up to the next sign is one term, no quotes needed):

```bash
watchdog search shell-company-investigation "shell company -real estate"
watchdog search shell-company-investigation "consulting fee +offshore -salary"
```

Results come in three sections — **exact matches** (every literal occurrence of the term/phrase across source documents and notes, with a page link back to the source — a local full-text index, no embeddings involved), **source passages** (what a document says, ranked by meaning), and **notes** (what the investigation concluded). Matched query terms are bolded in the printed snippet, and the snippet is centred on the first match rather than always showing the start of the passage. The score shown on source passages and notes is cosine similarity (0–1; a strong conceptual match sits around 0.5–0.65), even though the passage order is set by the fusion + rerank; exact matches carry no score — they're exhaustive, not ranked. Add `--threshold 0.5` to hide weak semantic matches, `--no-rerank` to skip the cross-encoder (faster, lower quality — the reranker downloads a ~300 MB local model on first use), `--full` to print the complete passage/note instead of a snippet, or `--json` for machine-readable output (what `/watchdog-query` uses internally). Wrap a phrase in quotes (`"jane doe"`) for an exact phrase match in the exact-matches section. Run `watchdog search --help` for the full query syntax.

Checking a whole list of names or terms against the vault — a leaked board roster, a sanctions list, a list of donors — is a separate mode, `--batch <file>`: pass a text file with one term per line and get a report of what each one hit (manifest entities plus exact-match occurrences), instead of ranking a single query:

```bash
watchdog search shell-company-investigation --batch names-to-check.txt
```

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

## Researching open questions on the web

When the vault raises a question its own documents can't answer — a director you can't profile, a contradiction you can't resolve, a company you need background on — Watchdog can research it on the web:

```bash
watchdog research shell-company-investigation
watchdog research shell-company-investigation -q "Who controls Acme Holdings?"
```

This opens Claude Code on the research skill. Seeded by your vault's entities, leads, and gaps, it proposes a research mission, confirms how wide to cast the net (quick / standard / deep), then researches in rounds — checking in with you between each. Crucially, it **doesn't write vault notes**: it *queues* every source it keeps (URL, a reliability tag, and why it matters) in a links file. When you exit the session, `watchdog research` downloads the queued sources into `_INCOMING/` — validating each one, and capturing HTML pages as a full rendered snapshot (images, styles, client-rendered content) when the optional capture browser is installed, or a sanitized plain fetch otherwise (see [INSTALL.md](INSTALL.md)) — so findings flow through the same `chew → ingest` pipeline as documents you obtained yourself: deduped, entity-extracted, and cited. A scraped blog post is never confused with a primary document.

It writes a research memo to `briefings/`, then offers to download. Confirm, then fold the findings in the normal way:

```bash
watchdog chew
watchdog ingest
```

Then open a fresh session to investigate what came back. The links file is the durable product of the session, held in `.watchdog/research/` — so even a long "deep" run never loses what it queued if it's interrupted. If a session dies before the download runs, `watchdog`, `watchdog chew`, and `watchdog status` all warn that sources are queued but not downloaded; run `watchdog research-fetch` (or re-run `watchdog research`, which offers to download a leftover queue) to finish. Across repeated research on the same investigation, Claude also skips sources the vault has already captured, so it doesn't re-fetch what you already have — unless you ask it to re-check a source for updates.

**Optional: archive sources to the Wayback Machine.** If you set `wayback_save true` and add archive.org S3 keys (`watchdog configure wayback_access_key` / `wayback_secret_key`, free from [archive.org/account/s3.php](https://archive.org/account/s3.php)), each downloaded source is also saved to the Internet Archive, and its snapshot URL is recorded in the source's provenance sidecar — a citable public copy that survives if the original is later changed or taken down. It's off by default and never blocks a download.

---

## Subsequent sessions

After the first ingest, the typical workflow is:

1. **Drop new documents** into `_INCOMING/`
2. **`watchdog chew`** from the vault directory (or `watchdog watch <name>` to chew automatically as files arrive)
3. **`watchdog ingest`** — extracts the queued documents in your terminal
4. **Read the briefing** — pay particular attention to connections with entities already in the vault
5. **`/watchdog-surface`** if the new batch was substantial

Claude Code doesn't need to be open while you're chewing. The queue accumulates until you're ready to run extraction.

At the start of each Claude Code session, Claude reads `hot.md` automatically — a current-state summary of the investigation that tells it what's already known without re-reading the entire vault. This is what makes it possible to continue an investigation across many separate sessions without losing context.

---

## Fact basis: stated vs inferred

Every extracted fact records its **basis** — whether the document said it, or Watchdog reasoned to it:

| Basis | Meaning |
|-------|---------|
| `stated` | Directly stated in the document — a quote, a figure, an explicit assertion. The default, and left implicit in the notes. |
| `inferred` | Reasoned from the document rather than stated outright. A lead to verify, not a finding. Marked *(inferred)* in the notes. |

Only `inferred` facts are flagged, so anything **unmarked is directly stated**. Treat an *(inferred)* fact as a lead that requires verification, not as an established fact.

When a new document **contradicts** a fact already in the vault, that is not a basis level — it surfaces as a `[!contradiction]` callout in the entity's note, and the contradiction itself is often newsworthy.

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

---

## Token usage

Watchdog is designed to keep token costs predictable. A few things to know:

- **Extraction runs outside Claude Code.** OCR, Docling document conversion, and embeddings all run in your terminal. Claude only sees clean, pre-extracted text — not raw document bytes.
- **Ingest runs in Python, not Claude Code.** `watchdog ingest` drives the pipeline in your terminal and calls the model only for reasoning (classify, extract, synthesize, dedup the timeline, brief). Each document is classified by a quick step that loads only the single matching domain skill — not all of them.
- **Documents are extracted in parallel.** Bounded by `extract_concurrency` (default 5); lower it with `watchdog configure extract_concurrency N` or `--concurrency N` if you hit model rate limits.
- **Failed documents don't sink the batch.** A doc that fails extraction is logged to `.watchdog/Registry/ingest.log` and moved to `.watchdog/queue/_failed/`; move it back to retry. For very large collections, chew and ingest in groups.

A Pro subscription ($20/month) is sufficient for most journalism work. If you're ingesting hundreds of documents at a time, a Max subscription gives higher session limits.
