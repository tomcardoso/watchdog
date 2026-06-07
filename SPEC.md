# Watchdog — Specification

> A Claude Code plugin for investigative journalists to ingest, connect, and query public records.

---

## Purpose

Journalists accumulate large, heterogeneous sets of public records — corporate filings, court documents, real estate transfers, financial statements, annual reports, bankruptcy records, contracts, OSINT output, and more. These documents arrive in various formats, some machine-readable, many not. The connections between them — the same address on two filings, a person appearing as director of three companies, a transaction that looks out of proportion to context — are often where the story lives.

This tool ingests those records, extracts structured entities and relationships, stores everything in a queryable Obsidian vault, and proactively surfaces connections the journalist might have missed.

**Target user:** A journalist with no coding background but willing to install software with the help of clear documentation.

**Scope:** Public records only. No cloud APIs are used for non-public documents.

---

## Design Principles

1. **Drop and forget.** A journalist drops a file. It gets processed. No command to run, no form to fill.
2. **Provenance is sacred.** Every extracted fact links to a specific document and page number. Nothing is asserted without a citation.
3. **Emergent structure.** The entity schema is not rigidly pre-defined. Claude infers types and fields from the document. A small set of universal fields is enforced; everything else is discovered.
4. **Obsidian is a view layer, not the truth.** The document registry and extracted data are the source of truth. Obsidian notes are generated outputs. Deleting a note doesn't lose data.
5. **Surface, don't bury.** After every ingest batch, produce a short briefing: what was found, what connects to existing entities, what looks anomalous.
6. **Journalism-grade duplication handling.** Two scans of the same record are flagged, not silently discarded. The journalist decides what to keep.

---

## Architecture Overview

```
Drop zone (Incoming/)
     │
     ▼
Concurrency lock acquired (Registry/.ingest-lock)
     │
     ▼
Preprocessing (Python / Docling)
  ├── Exact duplicate check (SHA-256)
  ├── File type detection
  ├── Garbled text detection → force OCR if needed
  └── Structured text + page metadata output
     │
     ▼
Extraction agent (Claude)
  ├── Entity extraction (typed, with aliases, kebab-case IDs)
  ├── Relationship extraction
  ├── Key facts with page-level citations and confidence classification
  ├── Near-duplicate check against registry
  └── Domain skill loaded per document type (if available)
     │
     ▼
Vault update (atomic — note and registry updated together)
  ├── Create/update entity notes
  ├── Create document note
  ├── Update registries (per-type + master)
  └── Log operation
     │
     ▼
Concurrency lock released
     │
     ▼
Post-ingest briefing
  ├── New entities found
  ├── Connections to existing entities
  └── Anomalies and things worth a closer look
```

Auto-ingest is triggered by a `SessionStart` hook: every time Claude Code opens in the project directory, it checks `Incoming/` for files that have not yet been processed and ingests them before any other work begins. The journalist drops files at any point; they are processed at the start of the next session.

### Ingest hard rules

Adapted from Spotlight's ingest skill. These apply unconditionally:

1. **Registry updates are atomic with note creation.** Never create an entity note without updating its registry entry. Never update the registry without the note existing.
2. **No duplicates.** Check registries before creating. Match on ID. If an entity exists, update it — don't create a second note.
3. **IDs are kebab-case.** Lowercase, hyphens, no spaces: `john-doe`, `shell-co-ltd`, `123-main-st`.
4. **Frontmatter is the contract.** Every note must have complete frontmatter. Agents and Dataview queries rely on it programmatically. Never omit or rename fields.
5. **Confidence is required.** Every extracted fact must carry a confidence level. No fact enters the vault unclassified.
6. **Only one writer at a time.** The ingest lock (`Registry/.ingest-lock`) must be acquired before any vault writes and released — even on failure — before the session ends.
7. **The journalist's Notes sections are sacred.** The ingestion agent never overwrites content in any `## Notes` section.

---

## Document Pipeline

### Supported input formats

| Format | Handler |
|--------|---------|
| PDF (machine-readable) | Docling |
| PDF (scanned, image-based) | Docling + OCR |
| PDF (garbled text layer) | Garbled text detection → force full-page OCR |
| DOCX, PPTX, XLSX | Docling |
| HTML, XHTML | Docling (saved web pages, scraped content) |
| XBRL XML | Docling (financial statements filed with regulators — SEC, SEDAR, etc.) |
| JATS XML | Docling (academic journal articles) |
| USPTO XML | Docling (patent filings) |
| Markdown | Docling (OSINT output, notes, reports) |
| AsciiDoc | Docling (technical documents) |
| LaTeX | Docling (academic papers, technical reports) |
| TXT, CSV | Direct read |
| Images (JPG, PNG, TIFF, BMP, WEBP) | Docling + OCR |
| Audio (WAV, MP3, M4A, AAC, OGG, FLAC) | Docling + speech-to-text (`asr` extra + ffmpeg required) |
| Video (MP4, AVI, MOV) | Docling + audio extraction + speech-to-text (`asr` extra + ffmpeg required) |
| WebVTT | Docling (closed captions and timed transcripts) |
| arrows.app JSON | Dedicated parser (see below) |
| `.yml` sidecar | Merged into document note at ingest time |

**Note on audio and video:** These require installing Docling with the `asr` extra (`pip install docling[asr]`) and `ffmpeg`. Useful for ingesting recorded hearings, press conferences, or interview transcripts. Not installed by default — the setup script will ask whether to include it.

### Garbled text detection

Before passing a PDF to Docling, a lightweight check runs on the extracted text layer:

```python
def is_garbled(text: str) -> bool:
    if not text.strip():
        return False
    printable = sum(1 for c in text if c.isalpha())
    return (printable / len(text)) < 0.4
```

If garbled, Docling runs with `force_full_page_ocr=True`. Threshold is configurable in project settings.

### arrows.app JSON

arrows.app exports a structured JSON format with explicit nodes and relationships. This file type bypasses the document pipeline entirely and is parsed directly:

- Each node becomes an entity note (or updates an existing one)
- Each relationship becomes a typed link between entity notes
- The arrows.app file itself is recorded in the document registry as the source

This is the preferred input for manually-built relationship diagrams.

### Pipeline output per document

- Clean extracted text (full document)
- Per-page text segments with page numbers
- Document metadata: title (inferred), document type (inferred), date (extracted or file date)
- Page count

---

## Data Model

### Document registry

Stored at `Registry/documents.json`. Records every file that has been ingested.

```json
{
  "sha256": "a3f9...",
  "filename": "shell-co-annual-report-2023.pdf",
  "original_path": ".raw/shell-co-annual-report-2023.pdf",
  "ingested_at": "2026-06-05T14:32:00Z",
  "page_count": 47,
  "document_type": "Annual Report",
  "entities_extracted": ["entity-john-doe", "entity-shell-co-ltd"],
  "near_duplicate_of": null
}
```

### Entity notes

One Obsidian note per unique real-world entity. Filed under `entities/[type]/`.

```yaml
---
id: john-doe
name: John Doe
type: Person
aliases:
  - J. Doe
  - John D.
appears_in:
  - document: "[[documents/shell-co-annual-report-2023]]"
    pages: [3, 7]
  - document: "[[documents/court-doc-2023-06-01]]"
    pages: [1]
roles:
  - entity: "[[entities/company/shell-co-ltd]]"
    role: Director
    source: "[[documents/shell-co-annual-report-2023]]"
    page: 3
    confidence: high
date_first_seen: 2023-12-31
date_last_updated: 2026-06-05
---

## Notes

<!-- Free-text space for journalist annotations. Not overwritten by ingestion. -->
```

The `## Notes` section is reserved for journalist-written annotations. The ingestion agent never overwrites it.

### Document notes

One Obsidian note per ingested document. Filed under `documents/`.

```yaml
---
title: Annual Report 2023 — Shell Co Ltd
type: Document
document_type: Annual Report
file: shell-co-annual-report-2023.pdf
date_of_document: 2023-12-31
date_ingested: 2026-06-05
source: https://www.sedar.com/filing/xyz   # where the document was obtained
obtained: 2026-06-05                        # date obtained (may differ from doc date)
entities_mentioned:
  - "[[entities/person/john-doe]]"
  - "[[entities/company/shell-co-ltd]]"
  - "[[entities/address/123-main-st]]"
page_count: 47
---

## Summary

[One-paragraph summary generated by Claude at ingest time.]

## Key facts

- John Doe listed as Director (p. 3) — confidence: high — directly stated in primary source
- Registered address: 123 Main St (p. 1) — confidence: high — directly stated
- Net revenue: $4.2M, down 38% from prior year (p. 12) — confidence: medium — figure stated, prior-year comparison inferred from adjacent table

## Entities mentioned

[Auto-generated list of wiki links to entity notes.]

## Notes

<!-- Reserved for journalist annotations: why this document matters, context, follow-up questions. Never overwritten by ingestion. -->
```

### Optional metadata sidecar

A journalist can optionally drop a `.yml` sidecar file alongside any document to pre-populate provenance and context at the moment of receipt — useful when you want to record where something came from before you forget.

The sidecar must share the document's filename with a `.yml` extension:

```
Incoming/
  shell-co-annual-report-2023.pdf
  shell-co-annual-report-2023.yml   ← optional
```

Supported sidecar fields:

```yaml
source: https://www.sedar.com/filing/xyz   # URL or system reference
obtained: 2026-06-05                        # date obtained
relevance: Confirms address match with court filing from June 2024
notes: Note director change on p. 12
```

`source` and `obtained` are merged into the document note frontmatter. `relevance` and `notes` are appended to the `## Notes` section. All sidecar fields are optional. If no sidecar exists, ingest proceeds normally and the journalist fills in provenance post-ingest.

If a document is re-ingested, sidecar fields are not overwritten — journalist annotations are always preserved.

### Confidence classification

Every extracted fact carries a confidence level. Rules adapted from Spotlight's epistemic-grounding skill:

| Condition | Maximum confidence |
|-----------|-------------------|
| Fact directly stated in primary source document | high |
| Fact stated in document but requires one short inference | medium |
| Fact inferred across multiple sources or assumptions | low |
| Fact contradicted by another source in the vault | disputed |

**Never upgrade a claim beyond its weakest element.** If the entity name is directly stated but the date is inferred, the whole claim is medium at most.

### Registries

Watchdog maintains four JSON registries in `Registry/`:

- `documents.json` — every ingested file (hash, path, type, entities extracted)
- `entities.json` — every unique real-world entity (ID, type, name, aliases, appears_in)
- `registry.json` — master index with counts and `last_updated` timestamp
- `ingest.log` — append-only log of all operations

Registry updates are atomic with note creation (see Hard Rules). The master registry is updated last, after all per-type registries are consistent.

### Entity types (emergent, not exhaustive)

Common types Claude will use by default. New types are created as needed.

| Type | Example |
|------|---------|
| Person | An individual named in a document |
| Company | A corporation, LLC, partnership, or other registered entity |
| Address | A physical or registered address |
| Property | A real estate parcel |
| Court Case | A legal proceeding |
| Transaction | A financial transfer or payment |
| Role | A relationship between a person and an entity (Director, Registered Agent, etc.) |

All entity types share the universal fields: `name`, `type`, `aliases`, `appears_in`, `date_first_seen`, `date_last_updated`. Type-specific fields are added as discovered and consistent across documents.

---

## Vault Structure

```
[project-name]/
├── Incoming/                    # Drop zone — drag files here to ingest
│   ├── Processed/               # Moved here after successful ingest
│   └── Failed/                  # Moved here if pipeline errors
├── Registry/                    # Internal state (excluded from Obsidian)
│   ├── documents.json           # Document registry
│   └── ingest.log               # Append-only operation log
├── entities/                    # Entity notes
│   ├── person/
│   ├── company/
│   ├── address/
│   └── [other types as discovered]
├── documents/                   # Document notes
├── briefings/                   # Post-ingest briefing notes
├── queries/                     # Saved query results (optional)
├── index.md                     # Master index (auto-maintained)
├── CLAUDE.md                    # Project instructions for Claude Code
└── .obsidian/                   # Obsidian configuration (standard Obsidian folder, cannot be renamed)
    ├── plugins/
    └── snippets/
```

`Incoming/_Processed/`, `Incoming/_Failed/`, and `Registry/` are excluded from Obsidian's file index via `.obsidian/app.json` so they don't clutter the vault view.

---

## Core Skills / Commands

| Invocation | What it does |
|------------|-------------|
| *(session start)* | Auto-checks `.raw/` and ingests any unprocessed files |
| `/ingest` | Manually trigger ingest of everything in `.raw/` |
| `/ingest [filename]` | Ingest a specific file |
| `/query [question]` | Answer a natural language question from the vault |
| `/surface` | Run connection and anomaly analysis across the full vault on demand |
| `/find-duplicates` | Scan for near-duplicates across all ingested documents |
| `/health` | Check vault integrity: orphan notes, missing links, stale entries |

---

## Post-Ingest Briefing

After every ingest batch (one or many files), a briefing note is written to `briefings/YYYY-MM-DD-HH-MM.md` and summarised in the terminal. It contains:

**New entities found** — entities that did not exist in the vault before this ingest.

**Connections to existing entities** — entities from the new documents that match entities already in the vault, with the nature of the connection.

**Anomalies and things worth a closer look** — heuristics include:
- An address shared by entities that have no other apparent connection
- A person appearing in an unexpected role (e.g., listed as a director but previously only seen as a plaintiff)
- A transaction amount that is disproportionate to the apparent scale of the entity
- An entity that appears in many documents but has no documented relationship to any other entity

The briefing is intentionally short. It is a prompt for the journalist, not a comprehensive analysis. Claude does not speculate; it flags and cites.

The briefing ends with an open prompt rather than a closing statement — something like: *"Anything you'd like to add, correct, or flag before I close out?"* This keeps the session open for conversational annotation.

### Clarifying questions

After producing the briefing, Claude may — at its own discretion — ask the journalist a small number of targeted questions when genuine uncertainty would meaningfully change the extraction output. This is distinct from conversational annotation (the journalist volunteering context) — here Claude is proactively flagging what it couldn't resolve on its own.

**When Claude should ask:**
- Entity disambiguation: two entities that may or may not be the same real-world person or company ("I found 'J. Smith' in this filing and 'John Smith' in a previous one — are these the same person?")
- Missing document context: a document with no clear date, no clear issuing authority, or no clear subject
- Relationship ambiguity: a person appearing in a role that seems inconsistent with how they've appeared elsewhere
- Near-duplicate confirmation: a document that scored high on similarity to an existing one but not high enough to trigger the duplicate review automatically

**When Claude should not ask:**
- Things it can reasonably infer from the document
- Minor details that don't affect the entity graph or relationships
- Anything already flagged in the briefing (don't repeat yourself)
- More than ~3–5 questions per ingest session — if there are many uncertainties, prioritise the ones with the highest impact on the graph and note the rest in the briefing

Questions are batched and presented after the briefing, not interleaved during processing. They are explicitly framed as optional: the journalist can answer them, defer them, or tell Claude to make its best guess. If deferred, the uncertainty is noted in the relevant entity or document note for later resolution.

### Conversational annotation

After the briefing, the journalist can provide context in plain language and Claude will apply it directly to the vault:

> "Company X was sourced from the Ontario court registry."
> "These three filings are all related to the same FOIA request — number 2024-1182."
> "Flag the Shell Co filing for follow-up — the director change on page 12 needs more digging."

Claude translates these into edits: updating `source` or `obtained` frontmatter fields, appending to `## Notes` sections, adding custom frontmatter tags. No special command required. This is the primary post-ingest annotation workflow; the `.yml` sidecar exists for provenance that needs to be recorded at the moment of receipt, before a session is open.

---

## Duplicate Detection

### Exact duplicates (always on)

Before any processing, SHA-256 hash of the file is checked against `Registry/documents.json`. If a match is found, the file is skipped and a note is logged. The file remains in `.raw/` and is not moved to `_processed/`.

### Near-duplicates (flagged for review)

After OCR and text extraction, the extracted text is compared against all previously ingested documents using TF-IDF cosine similarity. Documents above a similarity threshold (default: 0.85, configurable) are flagged.

When a near-duplicate is detected:
1. Ingest is paused for that file
2. The journalist is shown both documents side by side with similarity score
3. Options: **Skip** (discard new file), **Replace** (update registry to point to new file), **Keep both** (ingest as separate documents, linked as likely duplicates)

Near-duplicates are not auto-discarded. Two scans of the same record may have different legibility on different pages.

---

## Obsidian Integration

### Required plugins

| Plugin | Purpose |
|--------|---------|
| **Dataview** | Structured queries across entity frontmatter |
| **Obsidian Git** | Auto-commit vault every 15 minutes |

### Useful plugins (optional)

| Plugin | Purpose |
|--------|---------|
| **Templater** | Consistent note templates |
| **Bases** | Native database views (Obsidian v1.9.10+) |

### Example Dataview queries

All companies sharing an address with a given entity:
```dataview
TABLE roles, appears_in
FROM "entities/company"
WHERE contains(file.outlinks, [[entities/address/123-main-st]])
```

All people who appear as director of more than two companies:
```dataview
TABLE length(roles) as role_count
FROM "entities/person"
WHERE length(roles) > 2
SORT role_count DESC
```

### Graph view

The Obsidian graph view provides visual network exploration out of the box. Color coding:
- Blue — Person
- Green — Company
- Orange — Address
- Grey — Document

Color configuration is set in `.obsidian/graph.json` by the setup script.

---

## Installation

### Platform support

| Platform | Status |
|----------|--------|
| macOS | Fully supported, v1 |
| Linux | Supported, v1 (manual setup — no scripted installer) |
| Windows | Supported via WSL2, v1 (see below) |

**Windows:** Claude Code, Python, Docling, and Obsidian all run natively on Windows. The setup script and any shell scripts require WSL2 (Windows Subsystem for Linux). A native PowerShell setup script is planned for v2. For v1, Windows users should install WSL2 first, then follow the Linux setup path for the backend, while running Claude Code and Obsidian natively in Windows.

### Stack

| Component | Required |
|-----------|----------|
| macOS, Linux, or Windows (via WSL2) | Required |
| Obsidian v1.6+ | Required |
| Claude Code (latest) | Required |
| Python 3.10+ | Required |
| Docling | Required |
| Claude.ai Pro or Max subscription | Required (recommended) |
| Anthropic API key | Alternative to subscription, for advanced users |
| Homebrew (macOS) | Recommended |

A Claude.ai Pro or Max subscription is the recommended path for most journalists — no API key setup, no per-token billing. Raw API key access is an option for users who need higher rate limits or want to manage costs explicitly.

**Rate limits and resilience:** On a Pro plan, very large batch ingests (50+ documents in one session) may hit rate limits mid-session. The ingest pipeline must handle this gracefully: log the stopping point, move successfully processed files to `_processed/`, leave unprocessed files in `.raw/`, and resume automatically at the next session start.

### Setup script

A single script handles the full install on macOS:

```bash
bash setup.sh
```

The script:
1. Checks for Homebrew, installs if missing
2. Installs Python 3.10+ if not present
3. Installs Docling via pip
4. Checks for Claude Code, prints install instructions if missing
5. Prompts for Anthropic API key, writes to environment
6. Scaffolds the vault structure
7. Installs required Obsidian plugins
8. Writes the `SessionStart` hook to `.claude/hooks.json`

Linux support follows the same steps with `apt`/`dnf` substituted for Homebrew.

### Multiple projects

Watchdog is installed once. Each investigation is a separate vault folder created from that single installation — there is no need to clone or reinstall anything per project.

```bash
watchdog new "shell-company-investigation"
```

This scaffolds a new vault folder, creates `CLAUDE.md`, and registers the `SessionStart` hook for that directory. Open the new folder in Obsidian as a vault and in Claude Code as a project. Each vault is fully independent — separate `Incoming/`, `Registry/`, entities, documents, and briefings.

To switch between investigations, open a different folder in Obsidian and Claude Code. Both applications support multiple vaults/projects natively.

Projects are stored wherever the journalist chooses — a `~/Investigations/` folder is a sensible default. The Watchdog plugin itself lives in the Claude Code global plugin directory and is available to all projects automatically.

Each project is registered by name when created, enabling a helper command to open any project without remembering its path:

```bash
watchdog open shell-company-investigation
```

This navigates to the correct folder and launches a Claude Code session in one step. Project names tab-complete. The setup script writes this helper to the shell profile so it is available immediately after install.

### Primary interface

Installation documentation is written for the **Claude Code desktop app** as the primary interface — the journalist opens Claude Code, navigates to the project folder, and the session begins. The CLI (`cd ~/Investigations/[project] && claude`, or `watchdog open [project]`) is documented as an alternative for users comfortable with the terminal.

---

## Skills

Watchdog is built as a set of Claude Code skill files. There are two distinct types.

### Operational skills

These define what Claude does. They live in the Watchdog plugin directory and are available to all projects.

| Skill | Invocation | What it does |
|-------|------------|-------------|
| `ingest` | auto / `/ingest` | Process documents from `Incoming/`, extract entities, write vault notes, update registries, produce briefing |
| `query` | `/query [question]` | Answer natural language questions from the vault |
| `surface` | `/surface` | Find connections and anomalies across the full vault |
| `health` | `/health` | Check vault integrity: orphan notes, missing registry entries, stale links |

### Domain knowledge skills

These encode journalist expertise about specific document types. They are loaded by the ingest skill when it identifies a matching document type. Without them, Claude extracts entities reasonably well from any document. With them, it knows what to look for, what the terminology means, what constitutes a red flag, and what fields are most significant for that document type.

| Skill | Loaded when |
|-------|-------------|
| `records/corporate-filings` | Annual reports, corporate registrations, director filings |
| `records/court-documents` | Statements of claim, affidavits, judgments, orders |
| `records/real-estate` | Title transfers, mortgage instruments, liens, property assessments |
| `records/financial-statements` | Balance sheets, income statements, auditor reports |
| `records/bankruptcy` | Creditor lists, trustee reports, discharge documents |
| `records/government-contracts` | Procurement records, tender documents, contract awards |

These skills are built through a structured interview process with experienced journalists (see below). They are not generated from training data alone — they encode real investigative expertise about how to read these documents, what anomalies look like, and what connections are worth surfacing.

### Building domain knowledge skills through interview

Domain skills are generated by interviewing experienced journalists about their expertise with specific document types. The interview covers:

- What entities and fields are always present in this document type?
- What terminology is specific to this document type and what does it mean?
- What does a red flag look like — what patterns are anomalous or worth investigating?
- What connections across documents are most significant?
- What do investigators typically miss or overlook in this document type?
- What jurisdiction-specific variations exist (e.g. Canadian vs. US corporate filings)?

From the interview, Claude generates a reference SKILL.md file that the ingest agent consults when processing that document type. These skills can be contributed back to the open-source project and refined over time.

Domain skills are not required for Watchdog to function — the ingest agent works without them. They are enhancements that meaningfully improve extraction quality for specific document types.

## MVP (v1) Scope

The following is in scope for the first release:

- [ ] Operational skill files: `ingest`, `query`, `surface`, `health`
- [ ] At least one domain knowledge skill (corporate filings) generated through journalist interview
- [ ] Document pipeline: Docling preprocessing with garbled text detection
- [ ] Ingest concurrency lock
- [ ] Atomic registry updates
- [ ] Confidence classification on all extracted facts
- [ ] Auto-ingest via `SessionStart` hook
- [ ] Entity extraction with emergent schema and page-level provenance
- [ ] arrows.app JSON ingestion
- [ ] Optional `.yml` sidecar for pre-ingest provenance and context
- [ ] Document registry with SHA-256 duplicate detection
- [ ] Near-duplicate detection with journalist review prompt
- [ ] Post-ingest briefing
- [ ] Natural language query skill (`/query`)
- [ ] On-demand connection surfacing (`/surface`)
- [ ] Vault health check (`/health`)
- [ ] Obsidian vault with Dataview, graph view, color-coded node types
- [ ] macOS setup script
- [ ] Installation documentation written for non-technical journalists

**Out of scope for v1:**
- Global entity registry across projects
- Linux setup script (documented but not scripted)
- Windows support
- Web or Electron interface
- Multi-user / shared vault

---

## Future Versions

### v2 — Global entity registry

A persistent registry that lives outside any individual project vault. When a new entity is extracted during project ingest, it is cross-referenced against the global registry. Matches are surfaced: "Shell Co Ltd also appeared in your 2024 municipal project."

The global registry stores entity names, aliases, types, and links to which project vaults they appear in. It does not store documents or document content.

Entity resolution across projects (deciding that two differently-named entities are the same real-world entity) requires human confirmation before merging.

### v3 — Installation simplification

A guided installer or wrapper that removes the terminal requirement for non-technical journalists. Scope and form to be determined based on v1 adoption.

---

## Open Questions

- **Project name.** To be decided before public release. Names currently under consideration: Folio, Fonds, Dossier, Watchdog, Gazette, Archivist.
- **Linux setup script.** Documented for v1, scripted for v2.
- **Relationship notes as first-class objects.** Currently, relationships are encoded as frontmatter fields on entity notes. If typed relationships with their own metadata become important (e.g., a relationship that has a date range, a source document, and a contested status), they may need their own note type. Revisit after real-world use.
- **Export.** No export format is specified for v1. When a story is filed, how does a journalist archive or share their vault? To be designed based on actual workflow needs.
- **API cost guardrails.** Relevant only for users on raw API key access. Pro/Max subscribers pay a flat monthly fee. For API users ingesting large batches, a pre-ingest token estimate may be worth adding.
