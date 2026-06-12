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
- **Extracts entities** — people, companies, addresses, properties, court cases, transactions — with page-level citations and confidence levels on every fact
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

The pipeline has two stages — a CLI stage you run in your terminal, and an extraction stage that runs inside Claude Code.

```
Drop files into _INCOMING/
        ↓
watchdog chew  (terminal)
  SHA-256 dedup · OCR · Docling extraction · classification · embedding
  → originals moved to .watchdog/staging/<sha256>/
  → extracted data written to .watchdog/queue/<sha256>.json
        ↓
watchdog ingest  (terminal)
  acquires lock · scans queue · opens Claude Code
        ↓
/watchdog-ingest  (Claude Code)
  processes up to 5 documents in parallel, each in an isolated subagent
  reads queue files · applies domain knowledge · extracts entities,
  relationships, timeline events, key facts · flags contradictions
        ↓
watchdog post-flight  (called by each subagent)
  validates extraction · writes entity notes and document notes ·
  updates global timeline and registries · file-locked for parallel safety
  → originals moved to morgue/
        ↓
Post-ingest briefing: new entities · connections · leads · anomalies
```

Splitting the pipeline this way keeps token costs down — the slow mechanical work (OCR, Docling, embeddings, classification) runs outside Claude Code entirely. Claude only sees clean, already-extracted text. The subagent architecture keeps each document's extraction isolated, so the orchestrator context stays flat regardless of batch size.

The extraction step is a [Claude Code skill](src/watchdog/skills/watchdog-ingest.md). You keep the Obsidian vault and every original file.

### Document conversion with Docling

Watchdog uses [Docling](https://github.com/DS4SD/docling) for all document conversion. Docling is an open-source document understanding library from IBM Research that extracts text, tables, and layout from PDFs, Word documents, spreadsheets, HTML, and images.

Why Docling matters for investigative work:

- **Table extraction** — financial statements and creditor lists are full of tables. Docling reconstructs them as structured data rather than garbled text, so Claude can reason about rows and columns correctly.
- **Layout awareness** — multi-column layouts, footnotes, headers, and sidebars are handled correctly. A court document's header fields doesn't bleed into the body text.
- **OCR integration** — when text extraction fails or produces garbled output, Docling falls back to OCR automatically. On macOS, Apple Vision is used (fast, hardware-accelerated); on other platforms, Tesseract is the default (install via `brew install tesseract` or `apt install tesseract-ocr`). The engine is configurable — see [Configuration](#configuration).
- **Large document handling** — 400+ page PDFs are chunked into 40-page segments, processed in parallel, and reassembled in order with correct page numbers throughout.

Docling runs locally. Your documents never leave your machine during preprocessing.

### Automatic document classification

During chewing, Watchdog uses a lightweight embedding model to automatically classify each document's type. The first N pages (configurable via `classify_pages` in `watchdog configure`, default 10) are embedded and compared against cached skill embeddings — one per domain skill. The closest match above a confidence threshold determines which extraction skill Claude loads.

This means Claude enters each document already loaded with the right domain knowledge — what fields to look for, what patterns are anomalous, what an experienced investigative journalist would notice that a first-year reporter would miss. For document types that don't match any skill confidently, the [general-records fallback](src/watchdog/skills/records/general-records.md) applies.

Classification runs entirely locally using the same [fastembed](https://github.com/qdrant/fastembed) model as the search index — no additional model, no cloud call.

---

## Requirements

- **macOS, Linux, or Windows**
- **[Obsidian](https://obsidian.md) v1.6+** — free
- **[Claude Code](https://claude.ai/download)** — free to install
- **Claude.ai Pro or Max subscription** — required (Pro ~$20/month; Max from $100/month)
- **Python 3.10+**
- **qpdf + Ghostscript** — PDF decryption and repair
- **Tesseract OCR** — Linux/Windows only (macOS uses Apple Vision)

A Claude.ai Pro subscription is the recommended starting point. No API key setup, no per-token billing. If you prefer to authenticate with an Anthropic API key instead, run `claude login` in your terminal after installing Claude Code.

---

## Installation

```bash
pipx install watchdog-intel
watchdog setup
```

`watchdog setup` verifies system dependencies (qpdf, Ghostscript, Tesseract on Linux), configures your projects directory, and downloads the ML models used for document conversion and semantic search (~600 MB, one-time). Expect the model download step to take a few minutes on a slow connection.

Shell tab completion is enabled automatically by `watchdog setup` — it writes the activation line to your shell profile (`~/.zshrc`, `~/.bashrc`, or equivalent) and prompts you to reload.

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

# Set up the extraction session and open in Claude Code
watchdog ingest

# Open the vault in Obsidian
watchdog obsidian shell-company-investigation
```

**Optional but recommended:** before processing records, seed your investigation context from prior published stories or notes:

1. Drop background files (clips, notes, screenshots) into `_CONTEXT/`
2. Run `watchdog context` from inside the vault — opens Claude Code with the context skill pre-loaded, which reads the material, asks you questions, and writes `context.md`

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
| `watchdog log <name>` | Show ingest history; `--lines N` to tail |
| `watchdog archive <name>` | Mark an investigation complete — hidden from `watchdog list` |
| `watchdog unarchive <name>` | Restore an archived investigation |
| `watchdog rename <name> <new-name>` | Rename an investigation — updates the folder, registry, and Obsidian entry |
| `watchdog move <name> <path>` | Move vault to a new path and update the registry; if files are already at the new path, just updates the registry |
| `watchdog delete <name>` | Remove from registry (vault files are left on disk); `--purge` also permanently deletes all vault files |

### Processing

| Command | What it does |
|---------|-------------|
| `watchdog chew` | Process all files in `_INCOMING/` — run from inside the vault directory |
| `watchdog chew <file>` | Process a single specific file |
| `watchdog chew --chew-workers N` | Override parallel file workers for this run |
| `watchdog chew --chunk-workers N` | Override parallel chunk workers per file for this run |
| `watchdog ingest` | Acquire the ingest lock, scan the queue, and open Claude Code — `/watchdog-ingest` fires automatically |
| `watchdog context [name]` | Open Claude Code with the context seeding skill; omit name when inside the vault |
| `watchdog watch <name>` | Watch `_INCOMING/` and chew files automatically as they arrive |

`watchdog chew` sends a desktop notification when files finish processing (macOS only). Press **Ctrl+C** to cancel a chew in progress — the lock is cleaned up automatically and any partially-processed files remain in `_INCOMING/` for the next run.

### Info and settings

| Command | What it does |
|---------|-------------|
| `watchdog search <name> "<query>"` | Semantic search across ingested documents |
| `watchdog configure` | View or change configuration |
| `watchdog unlock <name>` | Release a stale chew or ingest lock; `--force` to remove even if recent |
| `watchdog setup` | Set up Watchdog after installation; `--force` to re-run |
| `watchdog refresh-skills [name]` | Update vault skill files after a watchdog upgrade; omit name when inside the project directory |
| `watchdog about` | Show version and project links |

### Claude Code slash commands

Run these inside a Claude Code session with your investigation open.

| Command | What it does |
|---------|-------------|
| `/watchdog-ingest` | Extract all chewed files into the vault (auto-fired by `watchdog ingest`) |
| `/watchdog-ingest [file]` | Chew and extract a specific file |
| `/watchdog-query [question]` | Answer a question from your vault |
| `/watchdog-surface` | Find connections and anomalies across the full vault |
| `/watchdog-entity [id ...]` | Refresh entity Summary and Timeline from all source documents |
| `/watchdog-wiki` | Create or update investigation thread pages |
| `/watchdog-context` | Seed `context.md` from background files in `_CONTEXT/` |
| `/watchdog-health` | Check vault integrity — orphaned notes, broken links, registry mismatches |

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
│   └── _FAILED/           ← Files that could not be processed
├── _CONTEXT/               ← Background material (prior stories, notes)
├── morgue/                 ← Original files after successful ingest
│   └── <entity>/
│       └── <doc-type>/
├── .watchdog/
│   ├── queue/             ← Extracted data awaiting ingest (.json per file)
│   ├── staging/           ← Originals held during processing
│   └── Registry/          ← Internal state — do not edit manually
│       ├── entities.json
│       ├── documents.json
│       ├── manifest.json  ← Lightweight entity lookup index
│       ├── registry.json
│       └── ingest.log
├── entities/
│   ├── person/            ← One note per person
│   ├── company/           ← One note per company
│   └── address/           ← One note per address
├── documents/             ← One note per ingested document
├── briefings/             ← Post-ingest briefing notes
├── wiki/                  ← Investigation thread pages
├── hot.md                 ← Current session state — rewritten after every ingest
├── log.md                 ← Append-only human-readable ingest history
├── context.md             ← Your investigation intent and key questions
└── index.md               ← Dataview index
```

### Supported file types

| Format | Extensions | Notes |
|--------|-----------|-------|
| PDF | `.pdf` | Text-based or scanned; OCR applied automatically when text layer is missing or garbled |
| Word document | `.docx` | Tables and formatting preserved |
| Excel spreadsheet | `.xlsx` | |
| Image | `.jpg`, `.jpeg`, `.png`, `.tiff`, `.tif` | OCR applied automatically |
| Web page | `.html`, `.htm` | |
| Plain text | `.txt`, `.md` | |
| Audio / video | `.mp3`, `.mp4`, `.m4a`, `.wav`, `.webm` | Requires optional transcription install — see [INSTALL.md](INSTALL.md) |

Sidecar files (`.yml`) are not ingested as documents — they are metadata attached to the adjacent file. See [GETTING_STARTED.md](GETTING_STARTED.md) for details.

### Entity notes

Each entity note has a consistent structure:

- **`## Summary`** — synthesized overview of who this entity is and their significance; replaced on each ingest
- **`## Analysis`** — accumulated investigative observations, dated and linked to source documents; never overwritten
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

Skills are installed into each vault's `.claude/commands/records/` folder when you run `watchdog new`, so they travel with the investigation and can be customized per-vault. After upgrading Watchdog, run `watchdog refresh-skills` from inside a vault to pull in updated skills.

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

`watchdog configure` reads and writes `~/.watchdog/config.json`. Run it with no arguments to see current values:

```bash
watchdog configure
```

To set a value:

```bash
watchdog configure <key> <value>
```

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
| `classify_pages` | `10` | Number of pages used to classify document type at chew time. Watchdog embeds the first N pages and compares them against skill embeddings to select the extraction skill. Higher values improve accuracy on documents with long preambles; lower values are faster on large batches. |
| `dup_threshold` | `0.85` | Jaccard similarity score at which two documents are flagged as near-duplicates. Range: 0.0–1.0. |
| `shingle_size` | `3` | Word n-gram size for near-duplicate fingerprinting. Changing this invalidates existing MinHash signatures — re-ingest to rebuild. |

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
```

---

## A note on AI and hallucination

Watchdog uses Claude to read documents and extract facts. AI can make mistakes — confabulate specificity, misread names, or draw incorrect inferences.

A few safeguards are built in:
- Every extracted fact carries a **confidence level** (`high`, `medium`, `low`, `disputed`)
- Every claim links to the **source document and page** so you can verify it directly
- `low`-confidence facts are **leads**, not findings — they belong in the vault but must not be treated as established
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
- **Two-stage queue** — `watchdog chew` writes extracted JSON to `.watchdog/queue/` and moves originals to `.watchdog/staging/`. After `/watchdog-ingest` completes, originals move from staging to `morgue/`. The queue is never touched by the journalist directly.
- **OCR engine:** Apple Vision on macOS (fast, hardware-accelerated); Tesseract on Linux/Windows (requires system install). Configurable via `watchdog configure ocr_engine`.
- **Near-duplicate detection** uses MinHash (128 hash functions) to approximate Jaccard similarity on word 3-gram shingles — no ML dependencies, runs locally.
- **Registries** (`.watchdog/Registry/documents.json`, `entities.json`, `manifest.json`) are the source of truth. Obsidian notes are generated outputs — deleting a note doesn't lose data. `manifest.json` is a lightweight id/name/type/aliases index used for entity lookup without loading full registry data.
- **Vault writes are file-locked** — `watchdog write-vault` acquires an exclusive lock on `.watchdog/Registry/.write-lock` before reading and writing registry files, so parallel subagent calls serialize safely without corruption.
- **Extraction runs in isolated subagents** — each document is processed by a separate Claude Code Agent call. This keeps the orchestrator context flat regardless of batch size.
- **Skills are per-vault** — domain knowledge skill files live in `.claude/commands/records/` inside each vault, installed by `watchdog new` and refreshed by `watchdog refresh-skills`. This means skills travel with the investigation and can be customized per-vault.
- **Single CLI entry point** — `watchdog` is the only command installed on your PATH. All pipeline utilities are subcommands.

---

## Acknowledgements

Watchdog's vault structure and session-context approach were partly inspired by [claude-obsidian](https://github.com/AgriciDaniel/claude-obsidian) by Daniel Agrici — a PKM framework built on Claude Code that demonstrated how to make an AI assistant genuinely vault-aware across sessions. The `hot.md` session state file and the general principle of teaching Claude to orient itself from structured vault context both draw on ideas in that project.

The semantic search index uses [fastembed](https://github.com/qdrant/fastembed) (by Qdrant) with the `BAAI/bge-small-en-v1.5` model — a lightweight ONNX-based embedding library that avoids the PyTorch dependency footprint while matching the quality of heavier alternatives. The idea of embedding raw document pages for retroactive search across a large corpus, separate from the extracted knowledge graph, was partly informed by [obsidian-smart-connections](https://github.com/brianpetro/obsidian-smart-connections) by Brian Petro. The pattern of using a structured vault index for entity lookup — rather than embedding everything — was informed by [obsidian-claude-code](https://github.com/Roasbeef/obsidian-claude-code).

---

## License

MIT — see [LICENSE](LICENSE).
