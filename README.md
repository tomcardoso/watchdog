# Watchdog

**Investigative journalism document intelligence — drop records, find connections.**

Watchdog is a [Claude Code](https://claude.ai/download) tool for journalists who accumulate large sets of public records. You drop documents into a folder. Watchdog extracts every person, company, address, and relationship it finds, stores them as structured notes in an [Obsidian](https://obsidian.md) vault, and proactively surfaces connections you might have missed.

> **Alpha.** Core pipeline works. Tested on macOS with real investigation documents. Not yet battle-hardened for production use. Feedback and contributions welcome.

---

## What it does

- **Ingests anything** — PDFs (scanned or machine-readable), Word documents, spreadsheets, web pages, images, court documents, corporate filings, financial statements, and more
- **Extracts entities** — people, companies, addresses, properties, court cases, transactions — with page-level citations and confidence levels on every fact
- **Finds connections** — shared addresses, overlapping directors, unusual role combinations, entities appearing across unrelated documents
- **Handles large documents** — 400+ page PDFs are split and processed in parallel; no truncation
- **Auto-OCRs scanned documents** — detects missing or garbled text layers and applies OCR automatically; falls back to encrypted/malformed PDF repair
- **Preserves provenance** — every extracted fact links to the specific document and page it came from; nothing is asserted without a citation
- **Domain knowledge built in** — dedicated extraction skills for corporate filings, court documents, real estate records, financial statements, bankruptcy filings, and government contracts
- **Stores everything in Obsidian** — your vault is yours; Watchdog writes to it, you query and annotate it

---

## How it works

```
Drop file into Incoming/
        ↓
preprocess.py — SHA-256 dedup · OCR detection · Docling extraction · near-duplicate check
        ↓
Claude extracts entities and relationships from the text
        ↓
Entity notes and document notes written to the Obsidian vault
Registries updated atomically
        ↓
Post-ingest briefing: new entities · connections · anomalies
```

The ingest pipeline is a [Claude Code skill](skills/ingest.md) — Claude reads the document text, applies domain knowledge, and writes structured notes. The Python pipeline handles the mechanical work (OCR, hashing, similarity detection). You keep the Obsidian vault.

---

## Requirements

| | |
|---|---|
| macOS 12+ | Linux supported (manual setup); Windows via WSL2 |
| [Obsidian](https://obsidian.md) v1.6+ | Free |
| [Claude Code](https://claude.ai/download) | Free to install |
| Claude.ai Pro or Max subscription | Required (~$20–40/month) |
| Python 3.10+ | Installed by setup script if missing |

A Claude.ai Pro subscription is the recommended path. No API key setup, no per-token billing.

---

## Installation

```bash
git clone https://github.com/tomcardoso/watchdog.git
cd watchdog
bash setup.sh
```

The setup script installs Python dependencies (Docling, pypdf), PDF preprocessing tools (qpdf, Ghostscript), Claude Code skills, and the `watchdog` CLI. Takes 5–10 minutes on first run.

For step-by-step instructions written for journalists who have never used a terminal, see [INSTALL.md](INSTALL.md).

---

## Quick start

```bash
# Create a new investigation vault
watchdog new "Shell Company Investigation"

# Open the vault in Claude Code
watchdog open shell-company-investigation
```

Drop documents into `~/Investigations/shell-company-investigation/Incoming/`. At the start of every Claude Code session, Watchdog automatically checks for new files and ingests them.

You can also trigger ingest manually:

```
/ingest
/ingest specific-document.pdf
```

---

## Commands

| Command | What it does |
|---------|-------------|
| `/ingest` | Process all files in `Incoming/` |
| `/ingest [file]` | Process a specific file |
| `/query [question]` | Answer a question from your vault |
| `/surface` | Find connections and anomalies across the full vault |
| `/health` | Check vault integrity — orphaned notes, broken links, registry mismatches |

**Examples:**

```
/query Who are the directors of Shell Co Ltd?
/query Which companies share the address 123 Main St?
/query When did John Doe first appear in these documents?
/surface
```

---

## Supported file types

| Type | Handler |
|------|---------|
| PDF (machine-readable) | Docling |
| PDF (scanned) | Docling + auto-OCR |
| PDF (garbled text layer) | Detected automatically → forced OCR |
| DOCX, PPTX, XLSX | Docling |
| HTML, XML (XBRL, JATS) | Docling |
| Images (JPG, PNG, TIFF, etc.) | Docling + OCR |
| TXT, CSV, Markdown | Direct read |
| arrows.app JSON | Dedicated parser |
| Audio/video | Requires `--with-asr` flag at install time + ffmpeg |

---

## Vault structure

Each investigation is an independent Obsidian vault:

```
my-investigation/
├── Incoming/          ← Drop files here
│   ├── Processed/     ← Moved here after successful ingest
│   └── Failed/        ← Moved here on pipeline error
├── Registry/          ← Internal state (excluded from Obsidian)
├── entities/
│   ├── person/        ← One note per person
│   ├── company/       ← One note per company
│   └── address/       ← One note per address
├── documents/         ← One note per ingested document
├── briefings/         ← Post-ingest briefing notes
└── index.md           ← Auto-maintained master index
```

---

## Domain knowledge skills

Watchdog ships with extraction skills for six document types. When Claude identifies a matching document, it loads the relevant skill before extracting — applying journalist expertise about what to look for, what constitutes a red flag, and what fields matter.

| Skill | Covers |
|-------|--------|
| `records/corporate-filings` | Annual reports, registrations, director filings (Canada + US) |
| `records/court-documents` | Claims, affidavits, judgments, orders |
| `records/real-estate` | Title transfers, mortgages, liens, assessments |
| `records/financial-statements` | Audited statements, MD&A, related-party disclosures |
| `records/bankruptcy` | BIA/CCAA filings, creditor lists, trustee reports |
| `records/government-contracts` | RFPs, sole-source justifications, proactive disclosure |

These skills encode real investigative knowledge — what fields are always present, what patterns are anomalous, what investigators typically miss. See [skills/records/](skills/records/) to read them or contribute new ones.

---

## Multiple investigations

Watchdog is installed once. Each investigation is a separate vault:

```bash
watchdog new "Municipal Contracts Investigation"
watchdog new "Healthcare Funding Investigation"
watchdog list
watchdog open municipal-contracts-investigation
```

Project names tab-complete in zsh and bash after installation.

---

## Alpha limitations

- **macOS only** for the scripted installer. Linux and Windows (WSL2) work but require manual setup — see [INSTALL.md](INSTALL.md).
- **No end-to-end test suite yet.** The pipeline is tested against real documents but automated tests are not written.
- **Domain skills are v1.** The extraction skills are well-researched but have not yet been validated in a live investigation. Expect rough edges — and please contribute improvements.
- **No global entity registry.** Entities are scoped to a single vault. Cross-investigation matching is planned for v2.
- **Audio/video requires extra setup.** Speech-to-text (`--with-asr`) adds significant install time and disk space.

---

## Contributing

Contributions most welcome in three areas:

**Domain knowledge skills** — if you have deep expertise reading a document type that isn't covered (regulatory filings, immigration records, tax documents, election filings, etc.), open an issue or submit a pull request to `skills/records/`. The format is plain markdown — no code required.

**Pipeline fixes** — `pipeline/` contains the Python preprocessing code. Bug reports with a sample document (redacted if needed) are especially useful.

**Installation and documentation** — `INSTALL.md` is written for non-technical journalists. Corrections, clarifications, and translations are welcome.

Please open an issue before starting significant work so we can discuss approach first.

---

## Architecture notes

- **Docling** handles document conversion — layout analysis, table extraction, OCR. Watchdog uses Docling's structured output rather than raw text, which is important for table-heavy documents (financial statements, creditor lists).
- **Large PDFs** are split into 40-page chunks and processed in parallel. Page numbers are preserved and reassembled in order.
- **OCR engine:** Apple Vision on macOS (fast, hardware-accelerated); EasyOCR on Linux/Windows.
- **Near-duplicate detection** uses Jaccard similarity on word 3-gram shingles — no ML dependencies, runs locally.
- **Registries** (`Registry/documents.json`, `Registry/entities.json`) are the source of truth. Obsidian notes are generated outputs. Deleting a note doesn't lose data.
- **Ingest lock** (`Registry/.ingest-lock`) prevents concurrent writes to the vault.

---

## License

MIT — see [LICENSE](LICENSE).
