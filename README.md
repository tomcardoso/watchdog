# 🔍🐕 Watchdog

**Investigative journalism document intelligence — drop records, find connections.**

[![PyPI](https://img.shields.io/pypi/v/watchdog-intel)](https://pypi.org/project/watchdog-intel/) [![CI](https://github.com/tomcardoso/watchdog/actions/workflows/ci.yml/badge.svg)](https://github.com/tomcardoso/watchdog/actions/workflows/ci.yml)

Watchdog is a [Claude Code](https://claude.ai/download) tool for journalists who accumulate large sets of public records. Drop documents into a folder. Watchdog reads every page, extracts every person, company, address, and relationship it finds, stores them as linked notes in an [Obsidian](https://obsidian.md) vault, and proactively surfaces connections you might have missed.

> **Alpha.** Core pipeline works. Tested on macOS with real investigation documents. Not yet battle-hardened for production use. Feedback and contributions welcome.

---

## Contents

- [Public records only](#️-public-records-only)
- [What it does](#what-it-does)
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Commands](#commands)
- [Vault structure](#vault-structure)
  - [Supported file types](#supported-file-types)
- [Domain knowledge skills](#domain-knowledge-skills)
- [Multiple investigations](#multiple-investigations)
- [Configuration](#configuration)
- [A note on AI and hallucination](#a-note-on-ai-and-hallucination)
- [Alpha limitations](#alpha-limitations)
- [Contributing](#contributing)
- [Architecture notes](#architecture-notes)
- [Acknowledgements](#acknowledgements)
- [License](#license)

---

## ⚠️ Public records only

**Watchdog is designed exclusively for publicly available documents** — court filings, corporate registrations, government contracts, regulatory filings, land registry records, and similar public-interest material.

**Do not use Watchdog with:**
- Confidential source communications
- Unpublished tips or leaked documents
- Private correspondence
- Any material that could identify a confidential source
- Documents obtained under a promise of confidentiality

Every document Watchdog processes is read by an AI. There is no way to take that back. If you are unsure whether a document is safe to process, do not process it.

---

## What it does

- **Ingests anything** — PDFs (scanned or text), Word documents, spreadsheets, images, court documents, corporate filings, financial statements, and more, powered by [Docling](https://github.com/DS4SD/docling)
- **Extracts entities** — people, companies, addresses, properties, court cases, transactions — with page-level citations on every fact, flagging any it inferred rather than read
- **Builds timelines** — datable events are extracted per entity and assembled into a global chronological view across the entire investigation
- **Finds connections** — shared addresses, overlapping directors, unusual role combinations, entities appearing across unrelated documents
- **Flags contradictions** — when a new document conflicts with a known fact (different address, conflicting date, mismatched role), Watchdog adds a `[!contradiction]` callout to the entity note with both sources cited
- **Tracks session state** — `hot.md` is rewritten after every ingest with a current-state summary so Claude can orient itself instantly at the start of a new session without re-reading the vault
- **Logs every ingest** — `log.md` is a human-readable append-only record of every ingest session, visible in Obsidian
- **Seeds investigation context** — drop prior published stories into `_CONTEXT/` and Watchdog interviews you to build a rich `context.md` that orients every subsequent ingest
- **Handles large documents** — 400+ page PDFs are split and processed in parallel; no truncation
- **Auto-OCRs scanned documents** — detects missing or garbled text layers and applies OCR automatically; falls back to encrypted/malformed PDF repair
- **Preserves provenance** — every extracted fact, timeline event, and relationship links to the source document and page; every vault note is directly linked to the original file
- **Domain knowledge built in** — dedicated extraction skills for corporate filings, court documents, real estate records, financial statements, bankruptcy filings, and government contracts
- **Stores everything in Obsidian** — your vault is yours; Watchdog writes to it, you query and annotate it

---

## How it works

Watchdog runs in two places: a **document pipeline** you run in your terminal (`watchdog chew`, then `watchdog ingest` — a Python orchestrator that calls a model only for the bounded reasoning steps, and can be pointed at Claude, OpenAI, or DeepSeek), and your **investigation**, which you run inside Claude Code (`/watchdog-query`, `/watchdog-surface`, `/watchdog-wiki`, and the other commands — interactive, multi-turn, always on Claude). The diagram below covers the terminal pipeline.

```
Drop files into _INCOMING/
        ↓
watchdog chew  (terminal)
  SHA-256 dedup · OCR · Docling extraction · embedding
  → originals moved to .watchdog/staging/<sha256>/
  → extracted data written to .watchdog/queue/<sha256>.json
        ↓
watchdog ingest  (terminal — a Python orchestrator)
  acquires lock · scans queue · extracts documents in parallel
  per document: classify → extract entities, relationships, timeline
  events, key facts → flag contradictions
        ↓
post-flight  (Python, per document)
  validates extraction · writes entity notes and document notes ·
  stages timeline events and updates registries · file-locked for parallel safety
  → originals moved to morgue/
        ↓
post-ingest  (Python)
  entity synthesis (multi-mention entities) · timeline collision
  resolution → renders the global timeline · briefing — new entities,
  connections, leads, anomalies
```

The model is called **only for the reasoning steps** (classify, extract, synthesize, dedup the timeline, write the briefing); everything mechanical — OCR/Docling, dispatch, pre/post-flight, registry writes — is deterministic Python. `watchdog ingest` runs the whole thing in your terminal; you keep the Obsidian vault and every original file.

### Document conversion with Docling

Watchdog uses [Docling](https://github.com/DS4SD/docling) for all document conversion. Docling is an open-source document understanding library from IBM Research that extracts text, tables, and layout from PDFs, Word documents, spreadsheets, HTML, and images.

Why Docling matters for investigative work:

- **Table extraction** — financial statements and creditor lists are full of tables. Docling reconstructs them as structured data rather than garbled text, so Claude can reason about rows and columns correctly.
- **Layout awareness** — multi-column layouts, footnotes, headers, and sidebars are handled correctly. A court document's header fields doesn't bleed into the body text.
- **OCR integration** — when text extraction fails or produces garbled output, Docling falls back to OCR automatically. On macOS, Apple Vision is used (fast, hardware-accelerated); on other platforms, Tesseract is the default (install via `brew install tesseract` or `apt install tesseract-ocr`). The engine is configurable — see [Configuration](#configuration).
- **Large document handling** — 400+ page PDFs are chunked into 40-page segments, processed in parallel, and reassembled in order with correct page numbers throughout.

Docling runs locally. Your documents never leave your machine during preprocessing.

### Document classification

Each document's type is identified at ingest time by a quick classification step that picks the closest-matching domain skill from the [records skills](src/watchdog/skills/records/) — one per document type — by their descriptive filenames. That skill is then fed into the extraction prompt.

This means Claude enters each document already loaded with the right domain knowledge — what fields to look for, what patterns are anomalous, what an experienced investigative journalist would notice that a first-year reporter would miss. For document types that don't match any skill, the [general-records fallback](src/watchdog/skills/records/general-records.md) applies.

Because the model that extracts the document is the one that classifies it — reading the actual text rather than an embedding of it — no separate classifier, model, or cloud call is involved.

---

## Requirements

- **macOS, Linux, or Windows**
- **[Obsidian](https://obsidian.md) v1.6+** — free
- **[Claude Code](https://claude.ai/download)** — free to install; **required**
- **Claude access** — a Claude.ai Pro or Max subscription, or an Anthropic API key
- **Python 3.10+**
- **qpdf + Ghostscript** — PDF decryption and repair
- **Tesseract OCR** — Linux/Windows only (macOS uses Apple Vision)

A Claude.ai Pro or Max subscription is the simplest starting point — no API key setup, no per-token billing. If you have an Anthropic API key, run `claude login` in your terminal after installing Claude Code and authenticate that way instead.

**Claude Code is required and is not optional.** The investigation commands — `/watchdog-query`, `/watchdog-surface`, `/watchdog-wiki`, `/watchdog-context`, `/watchdog-health` — are agentic, multi-turn sessions (they read across the vault, follow links, and ask you questions), and they run inside Claude Code on Claude. What *is* flexible is the **document-ingestion pipeline**: its reasoning steps (classification, extraction, post-ingest synthesis) can be offloaded to other model providers — OpenAI or DeepSeek — to cut cost, while the interactive analysis stays on Claude. See [Model backends](#model-backends).

---

## Installation

```bash
pipx install watchdog-intel
watchdog setup
```

`watchdog setup` verifies system dependencies (qpdf, Ghostscript, Tesseract on Linux/Windows), configures your projects directory, offers to install the optional Playwright/Chromium capture browser (~150 MB — declines by default), and downloads the ML models used for document conversion and semantic search (one-time). Expect the model download step to take a few minutes on a slow connection.

Shell tab completion is enabled automatically by `watchdog setup` — it writes the activation line to your shell profile (`~/.zshrc`, `~/.bashrc`, or equivalent) and prompts you to reload. This relies on detecting `$SHELL`, so it's a no-op on Windows unless you're in a Unix-like shell (e.g. Git Bash, WSL) — native Command Prompt/PowerShell users don't get tab completion.

For step-by-step instructions written for journalists who have never used a terminal, see [INSTALL.md](INSTALL.md).

---

## Quick start

```bash
# Create a new investigation vault (interactive — prompts for name and description)
watchdog new

# Or pass the name directly
watchdog new "Shell Company Investigation"

# Drop documents into _INCOMING/ then chew them (run from inside the vault)
cd ~/Investigations/shell-company-investigation
watchdog chew

# Extract all queued documents (runs in your terminal)
watchdog ingest

# Open the vault in Obsidian
watchdog obsidian shell-company-investigation
```

**Optional but recommended:** before processing records, seed your investigation context from prior published stories or notes:

1. Drop background files (clips, notes, screenshots) into `_CONTEXT/`
2. Run `watchdog context` from inside the vault — opens Claude Code with the context skill pre-loaded, which reads the material, asks you questions, and writes `context.md`

Running `watchdog` with no arguments from inside a vault walks you through whatever's next — seed context, chew `_INCOMING/`, then ingest — offering each step that has pending work.

For a full end-to-end walkthrough of a first investigation, see [GETTING_STARTED.md](GETTING_STARTED.md).

---

## Commands

### Investigation management

| Command | What it does |
|---------|-------------|
| `watchdog new [<name>]` | Create a new investigation vault; omit name to be prompted interactively |
| `watchdog obsidian [name]` | Open the vault in Obsidian; omit name when inside the project directory |
| `watchdog open [name]` | Open the vault folder in Finder / file explorer; omit name when inside the project directory |
| `watchdog list` | List all active investigations; `--all` includes archived |
| `watchdog status [name]` | Show detailed status; omit name to show all |
| `watchdog log [name]` | Show ingest history; omit name when inside the project directory; `--lines N` to tail |
| `watchdog archive <name>` | Mark an investigation complete — hidden from `watchdog list` |
| `watchdog unarchive <name>` | Restore an archived investigation |
| `watchdog rename <name> <new-name>` | Rename an investigation — updates the folder, registry, and Obsidian entry |
| `watchdog describe <name> ["text"]` | Set or update an investigation's description; omit text to be prompted |
| `watchdog move <name> <path>` | Move vault to a new path and update the registry; if files are already at the new path, just updates the registry |
| `watchdog delete <name>` | Remove from registry (vault files are left on disk); `--purge` also permanently deletes all vault files |
| `watchdog register [path]` | Register an existing vault with watchdog; omit path when inside the vault directory |

### Processing

| Command | What it does |
|---------|-------------|
| `watchdog fetch <url…>` | Download one or more URLs into `_INCOMING/` (validated, with a provenance sidecar) — for when you already have a list of links and just want them pulled into the pipeline, no research session needed. HTML pages get a full rendered snapshot (images, styles, client-rendered content) via headless Chromium when the optional `playwright` extra is installed, falling back to a sanitized plain fetch otherwise — see [INSTALL.md](INSTALL.md). Then run `watchdog chew` and `watchdog ingest`. |
| `watchdog fetch <file>` | Same, from a links file — one URL per line, or the tab-separated `url⇥title⇥source_type⇥relevance` form |
| `watchdog chew` | Process all files in `_INCOMING/` — run from inside the vault directory |
| `watchdog chew <file>` | Process a single specific file |
| `watchdog chew --chew-workers N` | Override parallel file workers for this run |
| `watchdog chew --chunk-workers N` | Override parallel chunk workers per file for this run |
| `watchdog ingest` | Extract all queued documents — runs the Python pipeline in your terminal |
| `watchdog ingest --extractor-model M` | Override the extraction model for this run (`sonnet`/`opus`/`haiku`; default from `watchdog configure`) |
| `watchdog ingest --finalizer-model M` | Override the post-ingest model for this run — synthesis + timeline + briefing (`sonnet`/`opus`/`haiku`; default from `watchdog configure`) |
| `watchdog ingest --classifier-model M` | Override the document-classification model for this run (`sonnet`/`opus`/`haiku`; default from `watchdog configure`: `haiku`) |
| `watchdog ingest --extractor-effort E` | Override the extraction reasoning effort for this run (`low`/`medium`/`high`; default from `watchdog configure`: `high`). Lower spends fewer tokens |
| `watchdog ingest --finalizer-effort E` | Override the post-ingest reasoning effort for this run (`low`/`medium`/`high`; default from `watchdog configure`: `high`) |
| `watchdog ingest --concurrency N` | Documents extracted in parallel for this run (default from `watchdog configure`: 5) |
| `watchdog ingest --classify-pages N` | Pages shown to the document classifier for this run (default from `watchdog configure`: 5) |
| `watchdog ingest --skill [NAME\|PATH]` | Pin a record skill (a name or a path to a skill file) for every document, skipping classification. `--skill` with no value picks from the list |
| `watchdog ingest --wait` | On a rate limit, sleep until it resets and resume automatically instead of stopping for you to re-run ingest — for an unattended overnight batch. Not with a `claude-batch` extractor model |
| `watchdog ingest --estimate` | Print a token/cost estimate for the queue and exit — no lock, no confirm, no extraction. Cost is projected from this vault's own usage history (last 3 runs); on subscription auth, or before any run has completed, only the token estimate is shown |
| `watchdog finalize` | Complete post-ingest (entity synthesis + timeline + briefing) for an already-extracted batch — run it if a rate limit or interrupt stopped post-processing before it finished. `--finalizer-model M` / `--finalizer-effort E` override the model and effort |
| `watchdog requeue` | Move documents quarantined in `queue/_failed/` back into the active queue, then re-run `watchdog ingest` to retry them |
| `watchdog` | With no arguments inside a vault: walk the pipeline — offer to seed context, chew `_INCOMING/`, then ingest — skipping any stage with no pending work |
| `watchdog context [name]` | Open Claude Code with the context seeding skill; omit name when inside the vault |
| `watchdog context --model M` | Override the model for context seeding (`sonnet`/`opus`/`haiku`, default: `sonnet`) |
| `watchdog watch [name]` | Watch `_INCOMING/` and chew files automatically as they arrive; omit name when inside the project directory |

`watchdog chew` sends a desktop notification when files finish processing (macOS only). Press **Ctrl+C** to cancel a chew in progress — the lock is cleaned up automatically and any partially-processed files remain in `_INCOMING/` for the next run.

`watchdog ingest` is resumable: pressing **Ctrl+C**, or hitting a Claude subscription rate limit, stops the batch cleanly — finished documents are saved and unfinished ones stay queued, so re-running `watchdog ingest` (after the limit resets) picks up where it left off. A document that genuinely fails extraction is set aside in `queue/_failed/`; the run reports how many, and `watchdog requeue` moves them back to retry. For a large batch run unattended, `--wait` does that re-run for you: it sleeps until the rate limit resets (using the reset time the provider reports, or a fixed fallback interval when it doesn't) and resumes automatically, repeating until the queue drains — Ctrl+C during the wait stops it exactly as it would mid-extraction.

Every ingest finalizes automatically at the end (entity synthesis + timeline + briefing). If that **post-processing** step is interrupted — e.g. the rate limit is hit *after* the documents extract — the batch is left finalizable. `watchdog status` flags it, and `watchdog finalize` completes synthesis and the briefing without re-extracting. If you start another `watchdog ingest` while a batch is pending, it asks what to do: **merge** the pending batch into the new run (finalize everything together), **finalize** it first then ingest, or **discard** it.

### Info and settings

| Command | What it does |
|---------|-------------|
| `watchdog search <name> "<query>"` | Hybrid search across ingested documents, in three sections: **exact matches** (a local full-text index — every literal occurrence of the term/phrase, with a page link back to the source), **source passages** (ranked by meaning via embeddings **and** exact terms via BM25, then reranked by a local cross-encoder), and **notes** (what the investigation concluded). Matches concepts *and* exact tokens like case numbers, dollar amounts, and names. Matched query terms are bolded in the printed snippet, and the snippet window is centred on the first match rather than always showing the start of the passage. Supports `+`/`-` phrases to steer toward/away from a concept (`"shell company -real estate"`) and `"quoted phrases"` for an exact-match phrase; `--threshold S` to hide weak semantic matches, `--no-rerank` to skip the reranker, `--full` to print the complete passage/note instead of a snippet, `--json` for machine-readable output, and `--batch <file>` to check a whole list of names/terms (one per line) against the vault at once, reporting hits per term instead of ranking a single query. `watchdog search --everywhere "<query>"` (no project name) instead searches every registered, non-archived investigation's manifest + exact-match lanes (semantic/rerank skipped — doesn't scale per-vault) and reports hits grouped by investigation; combine with `--batch <file>` to check a term list across every vault. Vaults with a broken path are skipped |
| `watchdog leads [name]` | Surface investigative leads from the entity graph — fully deterministic, no model call. Reports entities named as a relationship target but never profiled ("named by 3 people across 4 documents, no profile"), entities recurring across documents with no relationships, entities carrying unresolved contradiction flags, and entities carrying facts or roles flagged `basis: inferred` (a lead to verify, not a finding). The same sweep runs automatically at the end of every `watchdog ingest` (writing `briefings/leads-<date>.md`) and is nudged by a bare `watchdog` when nothing is pending; this command re-runs it on demand. Items you've acknowledged with `watchdog resolve` (or by ticking their checkbox in the briefing) drop out of the list, giving it a workable-queue property. Omit the name when inside the project folder |
| `watchdog resolve <id…>` | Acknowledge one or more leads, watch-word alerts, or contradiction callouts so the deterministic reports stop re-surfacing them. Run from inside the vault. Each report prints a copyable resolution id next to every item (e.g. `lead:isolated:acme`, `alert:ab12c34:…`, `contradiction:…`); pass those ids here. `--sync` instead imports any `- [x]` checkboxes you've ticked in the `briefings/` files; `--list` shows what's currently acknowledged. Pure Python, no model call — the acknowledgments live in `.watchdog/Registry/resolutions.json` and follow an entity through `watchdog merge-entities` |
| `watchdog unresolve <id…>` | Bring acknowledged items back into the active list — the inverse of `watchdog resolve`. Run from inside the vault |
| `watchdog merge-entities <keep-id> <merge-id>` | Fold a duplicate entity into another — fully deterministic, no model call. Run from inside the vault. Unions aliases, `appears_in`, roles, and timeline events onto `keep-id`; remaps every relationship anywhere in the registry that targeted `merge-id` (not just the two entities involved); concatenates the losing entity's `## Analysis` into the survivor's with provenance intact; and redirects the losing note to a short stub linking to the survivor. Prints both entities (name, type, document/relationship counts) and asks for confirmation before doing anything, since this is irreversible — anything other than `y`/`yes` cancels with no changes made; pass `--force` to skip the prompt. This is the fix for what the dashboard's "Possible duplicates" view and `/watchdog-health`'s near-duplicate check can only ever flag — run `watchdog reindex` afterward to drop the merged entity's stale search-index entries. The merge keeps only one of the two prose Summaries, so when both entities had one it prints a reminder to run `/watchdog-entity <keep-id>` in a Claude Code session, which re-synthesizes the survivor's Summary and Timeline from every merged source |
| `watchdog timeline [name]` | Rebuild `timeline.md` from the canonical `.watchdog/timeline/` event files — fully deterministic, no model call. Omit the name when inside the project folder |
| `watchdog reindex [name]` | Rebuild `.embeddings/` **and** `.fulltext/` from disk — no OCR re-run, no model calls. Reads the document/entity registries and the morgue's page-marked full text to rebuild every corpus passage/note vector and every full-text index row from scratch. Run it after changing `embed_model` in `watchdog configure`, instead of re-ingesting — the previous model's vectors aren't comparable to the new one's. (`rerank_model` needs no reindex: reranking runs fresh at query time and nothing about it is persisted, so a change takes effect on the next `watchdog search`.) Documents ingested before D26 (no morgue text on disk) are skipped and reported; their notes still reindex. Omit the name when inside the project folder |
| `watchdog research [name]` | Open Claude Code to research the vault's open questions on the web. Seeded by the vault's entities, leads, and gaps, Claude conducts bounded web research and **queues the sources it finds**; when the session ends, watchdog downloads them into `_INCOMING/` (with egress hygiene — full rendered snapshots for HTML when Playwright is installed, sanitized plain fetch otherwise), so findings flow through the normal `chew → ingest` pipeline. Claude never writes vault notes directly, and skips sources the vault already has. `--question "<q>"` (or `-q`) seeds a question (omit to be prompted); `--model M` overrides the model. After download, run `watchdog chew` then `watchdog ingest`. If a session is interrupted before the download runs, the queued URLs are held durably and `watchdog`, `watchdog chew`, and `watchdog status` warn that they're still pending. Omit the name when inside the project folder |
| `watchdog watchlist [name]` | Sweep **every already-ingested document** in the vault against the current `watchlist.md` — fully deterministic, no model call. The scan that runs automatically at the end of `watchdog ingest` only ever sees that run's documents, so a term added to the watchlist afterward never gets checked against the existing corpus; this command builds the same scan over the whole vault instead. Writes to the same `briefings/alerts-<date>.md` as the per-run scan (appending a new dated section if one exists). A term you've acknowledged for a document with `watchdog resolve` no longer re-reports on later scans. Omit the name when inside the project folder |
| `watchdog export [name]` | Export the investigation's entity/relationship graph as Neo4j-import CSV (`nodes.csv` + `relationships.csv`, also loadable in Gephi); `--format cypher` writes a single `graph.cypher` of `MERGE` statements instead; `--output DIR` sets the destination (default `<slug>-export/`). Fully deterministic — reads the registry, no model calls. Only stated-direction relationships are exported (auto-generated reverse edges are skipped) and edges to never-profiled entities are dropped so the import stays valid. Graph quality is bounded by ingest-time entity deduplication |
| `watchdog doctor` | Check all registered investigations for missing or broken vaults; suggests `watchdog move` or `watchdog delete` for each issue |
| `watchdog configure` | View or change configuration |
| `watchdog auth` | Show the auth mode and API-key status (masked) |
| `watchdog auth use <mode>` | Switch auth mode: `subscription` (Claude Code login, not metered) or `api-key` (metered). Normally chosen during `watchdog setup` |
| `watchdog auth set [provider]` | Store an API key (prompted, hidden); `provider` is `anthropic` (default), `openai`, or `deepseek` |
| `watchdog auth get [provider]` | Show one provider's key status and source (env var or stored) |
| `watchdog auth remove [provider]` | Delete a stored API key |
| `watchdog unlock <name>` | Release a stale chew or ingest lock; `--force` to remove even if recent |
| `watchdog setup` | Set up Watchdog after installation; `--force` to re-run |
| `watchdog refresh-skills [name]` | Update a vault's Claude Code command skills after a watchdog upgrade (record skills are global, so they never need refreshing); omit name when inside the project directory |
| `watchdog show-skills [name]` | List the record skills (and open the skills folder on GitHub), or print one skill in full |
| `watchdog about` | Show version and project links |

### Claude Code slash commands

Run these inside a Claude Code session with your investigation open.

Extraction is **not** a slash command — run `watchdog ingest` in your terminal. These run inside a Claude Code session with your investigation open:

| Command | What it does |
|---------|-------------|
| `/watchdog-query [question]` | Answer a question from your vault |
| `/watchdog-surface` | Find connections and anomalies across the full vault |
| `/watchdog-entity [id ...]` | Refresh entity Summary and Timeline from all source documents |
| `/watchdog-wiki` | Create or update investigation thread pages |
| `/watchdog-context` | Seed `context.md` from background files in `_CONTEXT/` |
| `/watchdog-health` | Check vault integrity — orphaned notes, broken links, registry mismatches, unresolved contradictions, unreviewed near-duplicates |
| `/watchdog-research [question]` | Research open questions on the web, queuing sources that watchdog downloads into `_INCOMING/` to flow through `chew → ingest` (launch with `watchdog research`) |

**Query examples:**

```
/watchdog-query Who are the directors of Shell Co Ltd?
/watchdog-query Which companies share the address 123 Main St?
/watchdog-query What happened in 2019 involving Alice Smith?
/watchdog-surface
```

---

## Vault structure

Each investigation is an independent Obsidian vault:

```
my-investigation/
├── _INCOMING/              ← Drop public records here
│   ├── _FAILED/           ← Files that could not be processed
│   └── _SKIPPED/          ← Exact duplicates and empty-text files, set aside
├── _CONTEXT/               ← Background material (prior stories, notes)
├── morgue/                 ← Original files after successful ingest, each beside
│   └── <entity>/             a <name>.md of its full extracted text (greppable)
│       └── <doc-type>/
├── .watchdog/
│   ├── queue/             ← Extracted data awaiting ingest (.json per file)
│   │   └── _failed/       ← Documents that failed extraction, held for `watchdog requeue`
│   ├── staging/           ← Originals held during processing
│   ├── research/          ← Queued web-research sources awaiting download into _INCOMING/
│   └── Registry/          ← Internal state — do not edit manually
│       ├── entities.json
│       ├── documents.json
│       ├── manifest.json  ← Lightweight entity lookup index
│       ├── registry.json
│       ├── ingest.log
│       ├── usage-<ts>.json    ← Per-run token/cost telemetry, kept for the last few runs
│       └── batch-pending.json ← State for a batch left unfinalized by an interrupted ingest
├── entities/
│   ├── person/            ← One note per person
│   ├── company/           ← One note per company
│   └── address/           ← One note per address
├── documents/             ← One note per ingested document
├── briefings/             ← Post-ingest briefing notes + watch-word alerts (alerts-<date>.md)
├── wiki/                  ← Investigation thread pages (matured angles)
├── queries/               ← Saved answers to questions you've asked (filed by /watchdog-query)
├── .fulltext/             ← Full-text search index (index.db) — rebuild with `watchdog reindex`
├── hot.md                 ← Current session state — rewritten after every ingest
├── log.md                 ← Append-only human-readable ingest history
├── timeline.md            ← Chronological event log, rebuilt from canonical timeline files
├── context.md             ← Your investigation intent and key questions
├── watchlist.md           ← Terms to watch for in new documents (one per line)
├── index.md               ← Landing page linking to the dashboard
└── dashboard.base         ← Dashboard of live tables (Obsidian Bases — built in, no plugin)
```

`dashboard.base` is a dashboard of live tables — most-mentioned entities, recent documents, people, companies, single-source entities to review, and possible duplicates — that refresh as you ingest (`index.md` is a thin landing page that links to it). The tables use [Obsidian Bases](https://help.obsidian.md/bases), a **core** Obsidian feature (version 1.9 and up), so there is nothing to install: the dashboard renders out of the box, no community plugin and no "restricted mode" to clear. Click a column header to sort (e.g. by **Documents** to surface the most-mentioned entities); click a row to open the note. If a row on the dashboard or `/watchdog-health` turns out to be the same real-world person or company extracted under two different entity ids, `watchdog merge-entities <keep-id> <merge-id>` folds them into one — see the command table above.

### Supported file types

| Format | Extensions | Notes |
|--------|-----------|-------|
| PDF | `.pdf` | Text-based or scanned; OCR applied automatically when text layer is missing or garbled |
| Word document | `.docx` | Tables and formatting preserved |
| Excel spreadsheet | `.xlsx` | |
| Image | `.jpg`, `.jpeg`, `.png`, `.tiff`, `.tif` | OCR applied automatically |
| Web page | `.html`, `.htm` | |
| Plain text | `.txt`, `.md` | |
| Audio / video | `.mp3`, `.mp4`, `.m4a`, `.wav` | Requires optional transcription install — see [INSTALL.md](INSTALL.md) |

Sidecar files (`.yml`) are not ingested as documents — they are metadata attached to the adjacent file. See [GETTING_STARTED.md](GETTING_STARTED.md) for details.

### Entity notes

Each entity note has a consistent structure:

- **`## Summary`** — synthesized overview of who this entity is and their significance; replaced on each ingest
- **`## Analysis`** — investigative claims about the entity, each dated, page-linked, and with an optional verbatim quote; rendered as accumulated claims for single-document entities, and synthesized into prose once an entity appears in two or more documents
- **`## Timeline`** — chronological list of datable events involving this entity, linked to source pages
- **`## Relationships`** — connections to other entities, with source citations
- **`## Notes`** — reserved for journalist annotations; never touched by Watchdog

Every link to a source document includes a direct page link into the original file (`[[morgue/.../file.pdf#page=3|p. 3]]`), so you can jump from any fact straight to the page it came from.

---

## Domain knowledge skills

Watchdog ships with extraction skills for 34 document types. When Claude identifies a matching document, it loads the relevant skill before extracting — applying journalist expertise about what to look for, what constitutes a red flag, and what fields matter. For document types that don't match a specific skill, a [general-records fallback](src/watchdog/skills/records/general-records.md) provides a universal framework for orienting yourself and reading any unfamiliar record.

Skills are jurisdiction-agnostic by default: universal principles come first, with specific jurisdictions (Canada, US, UK, Australia, EU) treated as examples, not as defaults.

### Financial and corporate

| Skill | Covers |
|-------|--------|
| [`records/corporate-filings`](src/watchdog/skills/records/corporate-filings.md) | Annual reports, registrations, director filings, beneficial ownership |
| [`records/financial-statements`](src/watchdog/skills/records/financial-statements.md) | Audited statements, MD&A, auditor opinions, related-party disclosures |
| [`records/regulatory-filings`](src/watchdog/skills/records/regulatory-filings.md) | Securities disclosures, insider trading reports, SEDAR+/EDGAR filings |
| [`records/bankruptcy`](src/watchdog/skills/records/bankruptcy.md) | Bankruptcy filings, creditor lists, trustee reports, restructuring proceedings |
| [`records/insurance-filings`](src/watchdog/skills/records/insurance-filings.md) | Regulatory returns, actuarial reports, reinsurance treaties, market conduct reviews |
| [`records/tax-documents`](src/watchdog/skills/records/tax-documents.md) | Charity information returns (T3010, Form 990), nonprofit filings, trust returns |

### Legal and regulatory

| Skill | Covers |
|-------|--------|
| [`records/court-documents`](src/watchdog/skills/records/court-documents.md) | Civil claims, affidavits, judgments, orders, injunctions |
| [`records/criminal-proceedings`](src/watchdog/skills/records/criminal-proceedings.md) | Charging documents, bail decisions, trial decisions, sentencing, forfeiture orders |
| [`records/administrative-tribunals`](src/watchdog/skills/records/administrative-tribunals.md) | Quasi-judicial administrative bodies: human rights, competition, environmental review, privacy, utility regulation |
| [`records/labour-arbitration`](src/watchdog/skills/records/labour-arbitration.md) | Grievance awards, labour board decisions, unfair labour practices, collective agreements |
| [`records/immigration-refugee`](src/watchdog/skills/records/immigration-refugee.md) | Asylum decisions, detention reviews, deportation orders, judicial reviews |
| [`records/healthcare-licensing`](src/watchdog/skills/records/healthcare-licensing.md) | Discipline decisions, fitness to practise, facility inspections (medicine, nursing, pharmacy) |
| [`records/professional-licensing`](src/watchdog/skills/records/professional-licensing.md) | Discipline decisions for lawyers, accountants, engineers, financial advisers, real estate agents |
| [`records/legislation`](src/watchdog/skills/records/legislation.md) | Statutes, regulations, orders-in-council, policy directives, white papers |

### Government and public records

| Skill | Covers |
|-------|--------|
| [`records/government-contracts`](src/watchdog/skills/records/government-contracts.md) | RFPs, sole-source justifications, contract award notices |
| [`records/procurement-records`](src/watchdog/skills/records/procurement-records.md) | Post-award contracts, amendments, vendor performance, standing offer call-ups |
| [`records/audit-reports`](src/watchdog/skills/records/audit-reports.md) | Auditor general reports, performance audits, inspector general reports |
| [`records/government-reports`](src/watchdog/skills/records/government-reports.md) | Royal commissions, public inquiries, parliamentary committee reports |
| [`records/foi-responses`](src/watchdog/skills/records/foi-responses.md) | FOI/ATI response packages, exemption indexes, redaction logs |
| [`records/legislature-transcripts`](src/watchdog/skills/records/legislature-transcripts.md) | Hansard, committee transcripts, question period, congressional hearings |
| [`records/lobbying-records`](src/watchdog/skills/records/lobbying-records.md) | Lobbyist registrations, communication reports, revolving door disclosures |
| [`records/election-filings`](src/watchdog/skills/records/election-filings.md) | Campaign finance returns, donor lists, third-party advertising disclosures |
| [`records/municipal-records`](src/watchdog/skills/records/municipal-records.md) | Council minutes, zoning decisions, conflict-of-interest declarations |
| [`records/police-records`](src/watchdog/skills/records/police-records.md) | Occurrence reports, use-of-force records, public complaint decisions, coroner's inquests |
| [`records/corrections-records`](src/watchdog/skills/records/corrections-records.md) | Parole board decisions, probation orders, prison inspection reports, correctional oversight |
| [`records/environmental-filings`](src/watchdog/skills/records/environmental-filings.md) | Pollutant release inventories, environmental assessments, compliance orders |

### Property

| Skill | Covers |
|-------|--------|
| [`records/real-estate`](src/watchdog/skills/records/real-estate.md) | Title transfers, mortgages, liens, assessments, market transactions |
| [`records/land-registries`](src/watchdog/skills/records/land-registries.md) | Land registry and title systems — common law and civil law; deeds, charges, caveats |
| [`records/vehicle-registrations`](src/watchdog/skills/records/vehicle-registrations.md) | Motor vehicle and vessel registrations, title transfers, liens, fleet records |

### Specialized

| Skill | Covers |
|-------|--------|
| [`records/academic-research`](src/watchdog/skills/records/academic-research.md) | Grant applications, ethics decisions, conflict-of-interest disclosures, retraction notices |
| [`records/aircraft-logs`](src/watchdog/skills/records/aircraft-logs.md) | Aircraft registrations, ADS-B flight tracks, safety investigation reports |
| [`records/dns-whois`](src/watchdog/skills/records/dns-whois.md) | WHOIS records, DNS data, IP allocation, SSL certificate transparency logs |
| [`records/news-clippings`](src/watchdog/skills/records/news-clippings.md) | News articles, press releases, wire stories, corrections, retractions |
| [`records/audio-video`](src/watchdog/skills/records/audio-video.md) | YouTube transcripts, podcast transcripts, earnings calls, press conference recordings |

These skills encode real investigative knowledge — what fields are always present, what patterns are anomalous, what investigators typically miss. See [src/watchdog/skills/records/](src/watchdog/skills/records/) to read them or contribute new ones. A contributor template is at [`src/watchdog/skills/records/_template.md`](src/watchdog/skills/records/_template.md).

Record skills are **global** — the ingest pipeline reads them straight from the installed package, so they're always current with no per-vault copies to refresh. Add your own in `~/.watchdog/skills/records/` (a custom skill overrides a built-in one of the same name), point a single run at any file with `watchdog ingest --skill path/to/skill.md`, and read what a skill says with `watchdog show-skills`.

---

## Multiple investigations

Watchdog is installed once. Each investigation is a separate vault:

```bash
watchdog new "Municipal Contracts Investigation" --description "City hall contracts awarded to councillors' donors"
watchdog new "Healthcare Funding Investigation"
watchdog list
watchdog status municipal-contracts-investigation
```

Project names tab-complete in zsh and bash after running `watchdog setup` (which enables completion automatically). Internal pipeline commands are intentionally hidden from tab completion and `--help`.

When an investigation concludes, archive it to keep the list clean:

```bash
watchdog archive municipal-contracts-investigation
watchdog list --all   # shows archived investigations alongside active ones
```

To move a vault after reorganizing your filesystem:

```bash
watchdog move municipal-contracts-investigation /Volumes/Archive/Investigations
```

---

## Configuration

`watchdog configure` reads and writes `~/.watchdog/config.json`. Run it with no arguments to see current values, grouped by section:

```bash
watchdog configure
```

In an interactive terminal, the listing ends with a prompt to launch a **configuration wizard** — a flat arrow-key menu of every setting and its current value. Arrow to a setting, press Enter to see its help and change it, and repeat; press `q` to quit. (When the terminal can't support arrow keys, the wizard falls back to a numbered prompt.)

To set a value directly:

```bash
watchdog configure <key> <value>
```

Or run `watchdog configure <key>` with no value to see that one key's help and change it interactively.

| Key | Default | Description |
|-----|---------|-------------|
| `projects_dir` | `~/Investigations` | Where new investigation vaults are created. Set during `watchdog setup`, change here afterwards. |
| `ocr_engine` | `auto` | OCR engine for scanned documents. `auto` uses Apple Vision on macOS and Tesseract elsewhere. Options: `auto`, `apple_vision`, `tesseract`, `easyocr`, `rapidocr`. |
| `ocr_languages` | *(auto-detect)* | Language codes for Apple Vision OCR, comma-separated (e.g. `en-US,fr-FR`). Leave unset to auto-detect. |
| `garbled_threshold` | `0.75` | Fraction of alphanumeric characters below which a PDF text layer is considered garbled and OCR is triggered. Range: 0.0–1.0. |
| `chew_workers` | `auto` | Parallel files during chewing. `auto` (default) picks adaptively based on batch content. Set to a whole number to fix it. |
| `chunk_size` | `40` | Pages per chunk when splitting large PDFs for parallel processing. |
| `chunk_workers` | `auto` | Parallel subprocesses for large-PDF chunks. |
| `chunk_timeout` | `300` | Seconds before a chunk subprocess is killed. |
| `table_structure` | `true` | Whether Docling runs its table detection model on PDFs. Set to `false` to speed up ingestion of text-only documents. |
| `embed_images` | `false` | Embed figures as base64 in the extracted markdown so Claude can read charts and image-based tables. Significantly increases token usage. |
| `section_token_threshold` | `120000` | Estimated tokens above which a document is split for sectioned (sequential, per-section) extraction instead of extracted whole. Lower it if whole-document extraction is overrunning the model's output ceiling on dense documents. |
| `section_token_budget` | `60000` | Target estimated tokens per section when a document is sectioned. |
| `section_overlap_tokens` | `4000` | Estimated-token overlap between consecutive sections, so entities/events spanning a section boundary aren't lost. |
| `dup_threshold` | `0.85` | Jaccard similarity score at which two documents are flagged as near-duplicates. Range: 0.0–1.0. |
| `shingle_size` | `3` | Word n-gram size for near-duplicate fingerprinting. Changing this invalidates existing MinHash signatures — re-ingest to rebuild. |
| `embed_model` | `BAAI/bge-small-en-v1.5` | Local fastembed model used to index passages and notes for `watchdog search`. Must be a model fastembed can load; stronger options include `BAAI/bge-base-en-v1.5` and `mxbai-embed-large-v1`. Vectors from two models aren't comparable — after changing it, run `watchdog reindex` to rebuild the index from disk (no re-ingest needed). |
| `rerank_model` | `BAAI/bge-reranker-base` | Local cross-encoder that reranks `watchdog search` corpus results after dense + BM25 fusion — the biggest retrieval-quality lever. Runs on-machine via fastembed (no API); pre-downloaded by `watchdog setup` (~300 MB), or on first search if missing. Set to `none` to rank by fusion alone, or pass `--no-rerank` per query. Lighter option: `Xenova/ms-marco-MiniLM-L-6-v2`. |
| `classifier_model` | `haiku` | Model that reads a document's first pages and picks its record skill. Haiku is plenty; raise it only if classification goes wrong on ambiguous documents. Value: a Claude tier (`haiku`/`sonnet`/`opus`) or a `backend:model` form (see [Model backends](#model-backends)). Per-run override: `--classifier-model`. |
| `extractor_model` | `sonnet` | Model for document extraction. Value: a Claude tier (`haiku`/`sonnet`/`opus`) or a `backend:model` form (see [Model backends](#model-backends)). Per-run override: `--extractor-model`. |
| `finalizer_model` | `haiku` | Model for post-ingest — entity synthesis (Summary + Analysis for entities in ≥2 documents) + timeline reconciliation + briefing. This step composes prose from compact digests rather than reading raw documents, so Haiku is the default; raise it if synthesized prose feels thin. Value: a Claude tier (`haiku`/`sonnet`/`opus`) or a `backend:model` form (see [Model backends](#model-backends)). Per-run override: `--finalizer-model`. |
| `extractor_effort` | `high` | How hard the extractor model thinks. Thinking tokens bill as output, so a lower effort spends fewer tokens per document — the main cost lever for an extraction run. `high` is the model default (unchanged behaviour); try `medium` or `low` to cut cost and verify quality holds. Ignored when the extractor is Haiku (no effort control). Options: `low`, `medium`, `high`. Per-run override: `--extractor-effort`. |
| `finalizer_effort` | `high` | How hard the finalizer model thinks during post-ingest. Reasoning helps the prose steps, so keep it higher than the extractor unless cost-trimming. Ignored when the finalizer is Haiku. Options: `low`, `medium`, `high`. Per-run override: `--finalizer-effort`. |
| `extract_concurrency` | `5` | Documents extracted in parallel during `watchdog ingest`. Lower it if you hit model rate limits; raise it for throughput. Per-run override: `--concurrency`. |
| `classify_pages` | `5` | Leading pages of each document shown to the classifier (`min(page_count, this)`). More pages classify ambiguous documents better at a small extra cost on the cheap classifier model. Per-run override: `--classify-pages`. |
| `default_skill` | _(unset)_ | Pin a record skill (a name from the global catalog, or a path to a skill file) for every ingested document, skipping classification — for vaults that are always one document type. Per-run override: `--skill`. |
| `preflight_alias_min_length` | `3` | Shortest entity *alias* that can match a document during extraction. Short aliases (initials, abbreviations) false-match common words and drag whole entity digests into the prompt, inflating cost as a vault matures. The canonical name always matches at any length (short real names like `BP`/`GE`/`3M` are unaffected). Set to `1` to match all aliases. |
| `research_max_rounds` | `3` | Default number of search rounds `watchdog research` runs before it must check in and stop. An advisory budget the interactive skill self-limits to; the `deep` effort tier overrides it per run. |
| `research_max_fetches` | `25` | Roughly how many web sources `watchdog research` captures into `_INCOMING/` in a default (standard-effort) run. Advisory; the `quick` and `deep` effort tiers scale it down or up per run. Bounds scope and ingest cost, not session tokens. |
| `wayback_save` | `false` | Also submit every research source to the Internet Archive's Wayback Machine (Save Page Now), recording the snapshot URL in each source's provenance sidecar — a citable copy that survives if the original changes or is taken down. Off by default; a no-op until both keys below are set. Best-effort — never blocks or fails a download. |
| `wayback_access_key` | _(unset)_ | archive.org S3 access key for `wayback_save`. Generate a free pair at [archive.org/account/s3.php](https://archive.org/account/s3.php). Stored in the config file; masked in `watchdog configure`. |
| `wayback_secret_key` | _(unset)_ | archive.org S3 secret key, paired with `wayback_access_key`. |

**Examples:**

```bash
# Switch to Tesseract on a non-Mac machine
watchdog configure ocr_engine tesseract

# Disable table detection for a project that is all court decisions
watchdog configure table_structure false

# Override OCR languages for a collection of French and Arabic documents
watchdog configure ocr_languages "fr-FR,ar-SA"

# Move investigation storage to an external drive
watchdog configure projects_dir /Volumes/SecureDrive/Investigations

# Use Haiku for extraction by default (faster and cheaper)
watchdog configure extractor_model haiku

# Spend fewer thinking tokens on extraction (the main per-run cost lever)
watchdog configure extractor_effort medium

# Lower parallelism if you hit model rate limits
watchdog configure extract_concurrency 2
```

### Model backends

Backend choice applies **only to the `watchdog ingest` pipeline** — the batch reasoning steps that run in your terminal. The interactive investigation commands (`/watchdog-query`, `/watchdog-surface`, `/watchdog-wiki`, `/watchdog-context`, `/watchdog-health`) are not affected: they run inside Claude Code, on Claude, always. (Those commands are open-ended, multi-turn agent sessions, where model capability is hardest to substitute; the ingest stages are bounded single-shot calls, which tolerate a cheaper provider far better.)

Within ingest, Watchdog is designed around Claude and uses it by default, but each stage — classification, extraction, post-ingest — can run on a different model provider. A stage's model knob takes either a **Claude tier** (`haiku`/`sonnet`/`opus`, routed by your `watchdog auth` mode) or a **`backend:model`** value naming the provider and its model:

| Value | Runs on |
|---|---|
| `sonnet` | Claude, via your auth mode (subscription or API key) |
| `claude-api:opus` / `claude-agent-sdk:sonnet` | Claude, forcing a specific backend |
| `openai:gpt-5-mini` | OpenAI (Chat Completions) |
| `deepseek:deepseek-chat` | DeepSeek (Chat Completions) |

Store the provider's key first, then point a stage at it (persistently or per run):

```bash
watchdog auth set deepseek                              # store the key (or set DEEPSEEK_API_KEY)
watchdog configure extractor_model deepseek:deepseek-chat
watchdog ingest --extractor-model openai:gpt-5-mini     # one-off override
```

Each stage is independent — e.g. keep extraction on Claude Sonnet while routing the cheaper classification or post-ingest steps to another provider. Non-Claude backends are unproven on dense legal/financial extraction, so the defaults stay on Claude and nothing routes to another provider unless you ask it to. The reasoning-effort knobs apply where the provider supports them (Claude effort, OpenAI `reasoning_effort` on reasoning models) and are ignored where they don't.

#### `claude-batch`: bulk extraction on a metered key, at half price

If you're running the Claude API on a metered key (not a subscription) and ingesting a large,
same-type dump — the common case this tool targets, e.g. 200 pages of one filing type —
`extractor_model claude-batch:sonnet` submits every whole-document extraction as one Anthropic
Message Batch: 50% off every token, stacking with the prompt caching `claude-api` already gets.
The tradeoff is latency, not cost: a batch typically finishes within an hour (up to 24h), so
`watchdog ingest` submits it and exits rather than waiting — run `watchdog ingest` again later
(or check `watchdog status`) to collect the results once it's ready.

Constraints, both enforced with a clear error if unmet:
- **Requires a pinned skill** (`--skill` or `default_skill`) — classification is inherently
  one-document-at-a-time and isn't batchable, so there's nothing to classify against.
- **Requires `api-key` auth mode** (`watchdog auth use api-key`) — the Batches API needs a
  metered key; it's not available on a subscription.
- Valid only as `extractor_model` — not `classifier_model`/`finalizer_model`.
- A document large enough to need **sectioned** extraction can't go through the batch (each
  section depends on the previous one's result), so those extract via `claude-api` instead,
  automatically — announced in the run's output, not silent.

This is also the recipe for keeping a Claude subscription's session limits for interactive work
only, spending zero subscription tokens on bulk ingest:

```bash
watchdog auth use api-key                                 # a metered key, not your subscription
watchdog configure classifier_model claude-api:haiku
watchdog configure extractor_model claude-batch:sonnet
watchdog configure finalizer_model claude-api:haiku
watchdog ingest --skill court-documents                   # submits the batch, exits
watchdog ingest                                            # later: collects it once ready
```

---

## A note on AI and hallucination

Watchdog uses Claude to read documents and extract facts. AI can make mistakes — confabulate specificity, misread names, or draw incorrect inferences.

A few safeguards are built in:
- Every extracted fact records its **basis** — `stated` (directly in the document) or `inferred` (reasoned from it); only `inferred` facts are marked, so anything unmarked is directly stated
- Every claim links to the **source document and page** so you can verify it directly
- `inferred` facts are **leads**, not findings — they belong in the vault but must not be treated as established
- `/watchdog-entity` lets you refresh an entity's Summary and Timeline at any time, re-synthesizing from all source documents rather than relying on a chain of incremental updates

Treat everything Watchdog produces as a structured first read, not a finished product. The vault is a tool for your reporting, not a replacement for it.

---

## Alpha limitations

- **Tested primarily on macOS.** Linux and Windows are supported but have seen less real-world use — feedback welcome.
- **Domain skills are v1.** The extraction skills are well-researched but have not yet been validated in a live investigation. Expect rough edges — and please contribute improvements.
- **No global entity registry.** Entities are scoped to a single vault. Cross-investigation entity matching is planned for a future release.
- **Audio/video requires extra setup.** Speech-to-text adds significant install time and disk space — see [INSTALL.md](INSTALL.md).

---

## Contributing

Contributions most welcome in three areas:

**Domain knowledge skills** — if you have deep expertise reading a document type that isn't covered, open an issue or submit a pull request to `src/watchdog/skills/records/`. The format is plain markdown — no code required. Copy [`_template.md`](src/watchdog/skills/records/_template.md) as your starting point; it includes the standard structure and authoring notes.

**Pipeline fixes** — `src/watchdog/pipeline/` contains the Python preprocessing code. Bug reports with a sample document (redacted if needed) are especially useful.

**Installation and documentation** — `INSTALL.md` is written for non-technical journalists. Corrections, clarifications, and translations are welcome.

To run from source:

```bash
git clone https://github.com/tomcardoso/watchdog
cd watchdog
pipx install --editable . --force
watchdog setup
```

The `--editable` flag points pipx directly at your source directory instead of copying it, so any changes you make to `.py` files are picked up immediately without reinstalling.

Please open an issue before starting significant work so we can discuss approach first.

---

## Architecture notes

- **[Docling](https://github.com/DS4SD/docling)** handles all document conversion — layout analysis, table extraction, OCR. Structured output (not raw text) is important for table-heavy documents like financial statements and creditor lists.
- **Large PDFs** are split into 40-page chunks and processed in parallel. Page numbers are preserved and reassembled in order.
- **Two-stage queue** — `watchdog chew` writes extracted JSON to `.watchdog/queue/` and moves originals to `.watchdog/staging/`. After `watchdog ingest` completes, originals move from staging to `morgue/`. The queue is never touched by the journalist directly.
- **OCR engine:** Apple Vision on macOS (fast, hardware-accelerated); Tesseract on Linux/Windows (requires system install). Configurable via `watchdog configure ocr_engine`.
- **Near-duplicate detection** uses MinHash (128 hash functions) to approximate Jaccard similarity on word 3-gram shingles — no ML dependencies, runs locally.
- **Registries** (`.watchdog/Registry/documents.json`, `entities.json`, `manifest.json`) are the source of truth. Obsidian notes are generated outputs — deleting a note doesn't lose data. `manifest.json` is a lightweight id/name/type/aliases index used for entity lookup without loading full registry data.
- **Vault writes are file-locked** — `write_vault` acquires an exclusive lock on `.watchdog/Registry/.write-lock` before reading and writing registry files, so the concurrent document workers serialize safely without corruption. On macOS/Linux this blocks indefinitely (`flock`); on Windows it uses `msvcrt.locking`, which retries for ~10 seconds under contention and then raises rather than waiting indefinitely.
- **Ingest is a Python orchestrator** — `watchdog ingest` runs the pipeline in your terminal and calls the model only for reasoning (classify, extract, synthesize, dedup the timeline, brief). Documents are extracted in parallel (bounded by `extract_concurrency`); the slow mechanical work and all bookkeeping stay in Python.
- **Record skills are global** — domain-knowledge skill files ship with the package and are read directly by the ingest pipeline (`watchdog.skills_catalog`); nothing is copied per vault, so they're always current. Users add custom skills in `~/.watchdog/skills/records/`. (Claude Code *command* skills like `/watchdog-query` are still installed per vault by `watchdog new` / `refresh-skills`.)
- **Single CLI entry point** — `watchdog` is the only command installed on your PATH. All pipeline utilities are subcommands.

---

## Acknowledgements

Watchdog's vault structure and session-context approach were partly inspired by [claude-obsidian](https://github.com/AgriciDaniel/claude-obsidian) by Daniel Agrici — a PKM framework built on Claude Code that demonstrated how to make an AI assistant genuinely vault-aware across sessions. The `hot.md` session state file and the general principle of teaching Claude to orient itself from structured vault context both draw on ideas in that project.

The semantic search index uses [fastembed](https://github.com/qdrant/fastembed) (by Qdrant) with the `BAAI/bge-small-en-v1.5` model — a lightweight ONNX-based embedding library that avoids the PyTorch dependency footprint while matching the quality of heavier alternatives. Rather than embedding a whole page (which averages many topics into one vector and dilutes a short query), each page is split into overlapping word *windows*, so a result points at a specific passage with its page — the citable span, not a paraphrase. The windowing approach, `+`/`-` associative queries, and the "show the source passage, don't generate an answer" principle are borrowed from [Semantra](https://github.com/freedmand/semantra) by Dylan Freedman. The idea of embedding the raw corpus for retroactive search, separate from the extracted knowledge graph, was partly informed by [obsidian-smart-connections](https://github.com/brianpetro/obsidian-smart-connections) by Brian Petro. The pattern of using a structured vault index for entity lookup — rather than embedding everything — was informed by [obsidian-claude-code](https://github.com/Roasbeef/obsidian-claude-code).

The ASCII dog displayed by `watchdog new` was created by Felix Lee. The ASCII dog displayed by `watchdog about` was created by Sarah Kearsley.

---

## License

MIT — see [LICENSE](LICENSE).
