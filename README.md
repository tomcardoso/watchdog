# 🔍🐕 Watchdog

**Investigative journalism document intelligence — drop records, find connections.**

Watchdog is a [Claude Code](https://claude.ai/download) tool for journalists who accumulate large sets of public records. Drop documents into a folder. Watchdog reads every page, extracts every person, company, address, and relationship it finds, stores them as linked notes in an [Obsidian](https://obsidian.md) vault, and proactively surfaces connections you might have missed.

> **Alpha.** Core pipeline works. Tested on macOS with real investigation documents. Not yet battle-hardened for production use. Feedback and contributions welcome.

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

```
Drop file into _INCOMING/
        ↓
watchdog preprocess — SHA-256 dedup · OCR detection · Docling extraction · near-duplicate check
        ↓
Claude extracts entities, relationships, timeline events, and key facts
        ↓
watchdog write-vault writes everything atomically:
  entity notes · document notes · global timeline · registries · morgue move
        ↓
Post-ingest briefing: new entities · connections · leads · anomalies
```

The ingest pipeline is a [Claude Code skill](src/watchdog/skills/watchdog-ingest.md) — Claude reads the document, applies domain knowledge, and produces a structured extraction JSON. The Python pipeline handles the mechanical work (OCR, hashing, similarity detection, vault writes). You keep the Obsidian vault and every original file.

---

## Docling

Watchdog uses [Docling](https://github.com/DS4SD/docling) for all document conversion. Docling is an open-source document understanding library from IBM Research that extracts text, tables, and layout from PDFs, Word documents, spreadsheets, HTML, and images.

Why Docling matters for investigative work:

- **Table extraction** — financial statements and creditor lists are full of tables. Docling reconstructs them as structured data rather than garbled text, so Claude can reason about rows and columns correctly.
- **Layout awareness** — multi-column layouts, footnotes, headers, and sidebars are handled correctly. A court document's header fields don't bleed into the body text.
- **OCR integration** — when text extraction fails or produces garbled output, Docling falls back to OCR automatically. On macOS, Apple Vision is used (fast, hardware-accelerated); on Linux, EasyOCR.
- **Large document handling** — 400+ page PDFs are chunked into 40-page segments, processed in parallel, and reassembled in order with correct page numbers throughout.

Docling runs locally. Your documents never leave your machine during preprocessing.

---

## Requirements

| | |
|---|---|
| macOS 12+ | Linux supported (manual setup); Windows via WSL2 |
| [Obsidian](https://obsidian.md) v1.6+ | Free |
| [Claude Code](https://claude.ai/download) | Free to install |
| Claude.ai Pro or Max subscription | Required (~$20–40/month) |
| Python 3.10+ | Installed by setup script if missing |
| qpdf + Ghostscript | Installed by setup script |

A Claude.ai Pro subscription is the recommended path. No API key setup, no per-token billing.

---

## Installation

```bash
git clone https://github.com/tomcardoso/watchdog.git
cd watchdog
bash setup.sh
```

The setup script installs system dependencies (qpdf, Ghostscript) and the Watchdog Python package via [pipx](https://pipx.pypa.io). Then run:

```bash
watchdog setup
```

This installs the Claude Code skills and configures your shell completions. Takes 5–10 minutes on first run (Docling downloads ML models).

Once Watchdog is on PyPI, installation will be:
```bash
pipx install watchdog-intel
watchdog setup
```

For step-by-step instructions written for journalists who have never used a terminal, see [INSTALL.md](INSTALL.md).

---

## Quick start

```bash
# Create a new investigation vault
watchdog new "Shell Company Investigation"

# Open the vault in Claude Code
watchdog open shell-company-investigation
```

**Optional but recommended:** before ingesting records, seed your investigation context from prior published stories or notes:

1. Drop background files (clips, notes, screenshots) into `_CONTEXT/`
2. Run `/watchdog-context` — Watchdog reads the material, asks you questions, and writes `context.md`

Then drop public records into `_INCOMING/`. At the start of every Claude Code session, Watchdog automatically checks for new files and ingests them. You can also trigger ingest manually:

```
/watchdog-ingest
/watchdog-ingest specific-document.pdf
```

---

## Commands

| Command | What it does |
|---------|-------------|
| `/watchdog-context` | Seed `context.md` from background files in `_CONTEXT/` |
| `/watchdog-ingest` | Process all files in `_INCOMING/` |
| `/watchdog-ingest [file]` | Process a specific file |
| `/watchdog-entity [id ...]` | Refresh entity Summary and Timeline from all source documents |
| `/watchdog-query [question]` | Answer a question from your vault |
| `/watchdog-surface` | Find connections and anomalies across the full vault |
| `/watchdog-wiki` | Create or update investigation thread pages |
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
│   └── Registry/           ← Internal state — do not edit manually
│       ├── entities.json
│       ├── documents.json
│       ├── registry.json
│       └── ingest.log
├── entities/
│   ├── person/             ← One note per person
│   ├── company/            ← One note per company
│   └── address/            ← One note per address
├── documents/              ← One note per ingested document
├── briefings/              ← Post-ingest briefing notes
├── wiki/                   ← Investigation thread pages
├── timeline.md             ← Global chronological view across all entities
├── hot.md                  ← Current session state — rewritten after every ingest
├── log.md                  ← Append-only human-readable ingest history
├── context.md              ← Your investigation intent and key questions
└── index.md                ← Dataview index
```

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

Watchdog ships with extraction skills for 28 document types. When Claude identifies a matching document, it loads the relevant skill before extracting — applying journalist expertise about what to look for, what constitutes a red flag, and what fields matter.

Skills are jurisdiction-agnostic by default: universal principles come first, with specific jurisdictions (Canada, US, UK, Australia, EU) treated as examples, not as defaults.

### Financial and corporate

| Skill | Covers |
|-------|--------|
| `records/corporate-filings` | Annual reports, registrations, director filings, beneficial ownership |
| `records/financial-statements` | Audited statements, MD&A, auditor opinions, related-party disclosures |
| `records/regulatory-filings` | Securities disclosures, insider trading reports, SEDAR+/EDGAR filings |
| `records/bankruptcy` | Bankruptcy filings, creditor lists, trustee reports, restructuring proceedings |
| `records/insurance-filings` | Regulatory returns, actuarial reports, reinsurance treaties, market conduct reviews |
| `records/tax-documents` | Charity information returns (T3010, Form 990), nonprofit filings, trust returns |

### Legal and regulatory

| Skill | Covers |
|-------|--------|
| `records/court-documents` | Civil claims, affidavits, judgments, orders, injunctions |
| `records/criminal-proceedings` | Charging documents, bail decisions, trial decisions, sentencing, forfeiture orders |
| `records/labour-arbitration` | Grievance awards, labour board decisions, collective agreements |
| `records/immigration-refugee` | Asylum decisions, detention reviews, deportation orders, judicial reviews |
| `records/healthcare-licensing` | Discipline decisions, fitness to practise, facility inspections |

### Government and public records

| Skill | Covers |
|-------|--------|
| `records/government-contracts` | RFPs, sole-source justifications, contract award notices |
| `records/procurement-records` | Post-award contracts, amendments, vendor performance, standing offer call-ups |
| `records/audit-reports` | Auditor general reports, performance audits, inspector general reports |
| `records/government-reports` | Royal commissions, public inquiries, parliamentary committee reports |
| `records/foi-responses` | FOI/ATI response packages, exemption indexes, redaction logs |
| `records/legislature-transcripts` | Hansard, committee transcripts, question period, congressional hearings |
| `records/lobbying-records` | Lobbyist registrations, communication reports, revolving door disclosures |
| `records/election-filings` | Campaign finance returns, donor lists, third-party advertising disclosures |
| `records/municipal-records` | Council minutes, zoning decisions, conflict-of-interest declarations |
| `records/police-records` | Occurrence reports, use-of-force records, complaint decisions, parole rulings |
| `records/environmental-filings` | Pollutant release inventories, environmental assessments, compliance orders |

### Property

| Skill | Covers |
|-------|--------|
| `records/real-estate` | Title transfers, mortgages, liens, assessments, market transactions |
| `records/land-registries` | Land registry and title systems — common law and civil law; deeds, charges, caveats |

### Specialized

| Skill | Covers |
|-------|--------|
| `records/academic-research` | Grant applications, ethics decisions, conflict-of-interest disclosures, retraction notices |
| `records/aircraft-logs` | Aircraft registrations, ADS-B flight tracks, safety investigation reports |
| `records/dns-whois` | WHOIS records, DNS data, IP allocation, SSL certificate transparency logs |
| `records/news-clippings` | News articles, press releases, wire stories, corrections, retractions |
| `records/audio-video` | YouTube transcripts, podcast transcripts, earnings calls, press conference recordings |

These skills encode real investigative knowledge — what fields are always present, what patterns are anomalous, what investigators typically miss. See [src/watchdog/skills/records/](src/watchdog/skills/records/) to read them or contribute new ones.

---

## Multiple investigations

Watchdog is installed once. Each investigation is a separate vault:

```bash
watchdog new "Municipal Contracts Investigation"
watchdog new "Healthcare Funding Investigation"
watchdog list
watchdog status municipal-contracts-investigation
watchdog open municipal-contracts-investigation
```

Project names tab-complete in zsh, bash, and fish after installation.

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

- **macOS only** for the scripted installer. Linux and Windows (WSL2) work but require manual setup — see [INSTALL.md](INSTALL.md).
- **Domain skills are v1.** The extraction skills are well-researched but have not yet been validated in a live investigation. Expect rough edges — and please contribute improvements.
- **No global entity registry.** Entities are scoped to a single vault. Cross-investigation entity matching is planned for a future release.
- **Audio/video requires extra setup.** Speech-to-text (`--with-asr`) adds significant install time and disk space.

---

## Contributing

Contributions most welcome in three areas:

**Domain knowledge skills** — if you have deep expertise reading a document type that isn't covered (regulatory filings, immigration records, tax documents, election filings, etc.), open an issue or submit a pull request to `src/watchdog/skills/records/`. The format is plain markdown — no code required.

**Pipeline fixes** — `src/watchdog/pipeline/` contains the Python preprocessing code. Bug reports with a sample document (redacted if needed) are especially useful.

**Installation and documentation** — `INSTALL.md` is written for non-technical journalists. Corrections, clarifications, and translations are welcome.

Please open an issue before starting significant work so we can discuss approach first.

---

## Architecture notes

- **[Docling](https://github.com/DS4SD/docling)** handles all document conversion — layout analysis, table extraction, OCR. Structured output (not raw text) is important for table-heavy documents like financial statements and creditor lists.
- **Large PDFs** are split into 40-page chunks and processed in parallel via `watchdog preprocess-batch`. Page numbers are preserved and reassembled in order.
- **OCR engine:** Apple Vision on macOS (fast, hardware-accelerated); EasyOCR on Linux/Windows.
- **Near-duplicate detection** uses Jaccard similarity on word 3-gram shingles — no ML dependencies, runs locally.
- **Registries** (`.watchdog/Registry/documents.json`, `entities.json`) are the source of truth. Obsidian notes are generated outputs — deleting a note doesn't lose data.
- **Vault writes are atomic** — `watchdog write-vault` handles entity notes, document notes, timeline, registries, and the morgue move in a single operation behind an ingest lock.
- **Single CLI entry point** — `watchdog` is the only command installed on your PATH. Pipeline utilities (`watchdog preprocess`, `watchdog write-vault`, etc.) are subcommands, not separate binaries.

---

## Acknowledgements

Watchdog's vault structure and session-context approach were partly inspired by [claude-obsidian](https://github.com/AgriciDaniel/claude-obsidian) by Daniel Agrici — a PKM framework built on Claude Code that demonstrated how to make an AI assistant genuinely vault-aware across sessions. The `hot.md` session state file and the general principle of teaching Claude to orient itself from structured vault context both draw on ideas in that project.

---

## License

MIT — see [LICENSE](LICENSE).
