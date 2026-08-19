# The vault

This page explains what Watchdog builds on disk and how to read it: the folders, the notes, the dashboard, and the markers that tell you whether a fact was read from a document or inferred by the model. Read it once your first ingest has finished and you are looking at a vault full of files, wondering what each one is.

## One vault per investigation

Each investigation is its own vault — a plain [Obsidian](https://obsidian.md) folder of markdown files. Watchdog writes to it; you read, search, and annotate it. There is no proprietary format and nothing locked away.

The vault is also safe to edit. Watchdog's internal registries — machine-readable files under `.watchdog/` — are the source of truth, and the notes you see in Obsidian are generated from them. Deleting a note loses nothing; it can be rebuilt from the registry.

## The directory tree

```
my-investigation/
├── _INCOMING/              ← drop public records here
│   ├── _FAILED/            ← files that could not be processed
│   └── _SKIPPED/           ← exact duplicates and empty-text files, set aside
├── _CONTEXT/               ← background material (prior stories, notes)
├── morgue/                 ← original files after ingest, each beside its full extracted text
├── .watchdog/              ← internal state: processing queue, staging area,
│                             registries — do not edit
├── entities/
│   ├── person/             ← people
│   ├── organization/       ← companies, banks, unions, funds, non-profits
│   ├── public-body/        ← governments, regulators, courts, agencies
│   ├── place/              ← addresses, properties, locations
│   ├── asset/              ← vehicles, accounts, domains, shares
│   └── proceeding/         ← lawsuits, insolvencies, inquiries
├── documents/              ← one note per ingested document
├── briefings/              ← post-ingest briefings, leads, and watch-word alerts
├── wiki/                   ← investigation thread pages (matured angles)
├── queries/                ← saved answers to questions you have asked
├── .fulltext/              ← full-text search index
├── .embeddings/            ← semantic search index
├── hot.md                  ← current session state, rewritten after every ingest
├── log.md                  ← append-only ingest history
├── timeline.md             ← chronological event log across the investigation
├── context.md              ← your investigation intent and key questions
├── watchlist.md            ← terms to watch for in new documents (one per line)
├── requests.md             ← documents to go and get, regenerated after every ingest
├── index.md                ← landing page linking to the dashboard
└── dashboard.base          ← dashboard of live tables (Obsidian Bases)
```

## Folder by folder

**`_INCOMING/`** is where you put documents. Everything you drop here gets processed by `watchdog chew` — see [Getting started](getting-started.md) for the workflow. Two subfolders catch problems: `_FAILED/` holds files that could not be processed (password-protected, corrupted, or an unsupported format), and `_SKIPPED/` holds exact duplicates of documents already ingested, plus files in which no text could be found. [Troubleshooting](troubleshooting.md) covers what to do with each.

**`_CONTEXT/`** holds background material — prior published stories, your notes, screenshots. Files here are not ingested as records; they feed the context interview that writes `context.md`, described in [Getting started](getting-started.md).

**`morgue/`** is where original files land after a successful ingest, organized by entity and document type. Each original sits beside a markdown file of its full extracted text, so you can search the complete text of every document from Obsidian or the terminal. Nothing is ever discarded: the file you dropped in is the file in the morgue.

**`.watchdog/`** is Watchdog's internal state: the processing queue, a staging area for files mid-pipeline, and the registries that record every entity, document, and relationship. Do not edit anything in here by hand.

**`entities/`** holds one note per extracted entity, filed by type. Watchdog sorts every entity into one of six fixed classes: **person** (people), **organization** (companies, banks, unions, funds, non-profits), **public-body** (governments, regulators, courts, agencies), **place** (addresses, properties, locations), **asset** (vehicles, accounts, domains, shares), and **proceeding** (lawsuits, insolvencies, inquiries). Fixing the list to these six keeps the same real-world entity from being split across near-duplicate folders when the model describes it differently in two documents. These notes are the heart of the vault; their structure is described [below](#entity-notes).

**`documents/`** holds one note per ingested document: what it is, what was extracted from it, and a link to the original in the morgue.

**`briefings/`** collects the reports Watchdog writes after each ingest: a briefing of new entities, connections, and anomalies; a leads file (`leads-<date>.md`); and watch-word alerts (`alerts-<date>.md`) when a watchlist term appears. [Investigating](investigating.md) explains how to work with each.

**`wiki/`** holds investigation thread pages — angles that have matured beyond a single question, created with `/watchdog-wiki`.

**`queries/`** holds saved answers filed by `/watchdog-query` when a question produces a substantive answer worth keeping.

**`.fulltext/`** and **`.embeddings/`** are the indexes behind `watchdog search` — one for exact matches, one for meaning. They are rebuilt from disk by `watchdog reindex`; you never touch them directly.

### The root files

- **`hot.md`** — a current-state summary of the investigation, rewritten after every ingest. Claude reads it at the start of each session to orient itself without re-reading the vault.
- **`log.md`** — an append-only, human-readable record of every ingest session.
- **`timeline.md`** — every datable event extracted across the investigation, assembled into one chronological view.
- **`context.md`** — your investigation intent and key questions, written by the context interview.
- **`watchlist.md`** — terms you want flagged when they appear in new documents, one per line. The format and the scan are covered in [Investigating](investigating.md).
- **`requests.md`** — documents named in what you have already ingested that you could go and get: a hearing transcript an order cites, an enabling regulation, a referenced filing. Regenerated after every ingest and covered in [Investigating](investigating.md#document-requests).
- **`index.md`** — a thin landing page that links to the dashboard.
- **`dashboard.base`** — the dashboard itself, described next.

## The dashboard

`dashboard.base` is a dashboard of live tables: most-mentioned entities, recent documents, people, companies, single-source entities to review, and possible duplicates. The tables refresh as you ingest.

It uses [Obsidian Bases](https://help.obsidian.md/bases), a core Obsidian feature in version 1.9 and up. There is nothing to install — no community plugin, no restricted mode to clear. Click a column header to sort (by **Documents**, say, to surface the most-mentioned entities); click a row to open the note.

One table deserves attention: **possible duplicates**. If a row turns out to be the same real-world person or company extracted under two different entity ids, the dashboard can only flag it — the fix is `watchdog merge-entities`, covered in [Investigating](investigating.md#duplicate-entities).

## Entity notes

Every entity note follows the same five-section anatomy:

- **`## Summary`** — a synthesized overview of who this entity is and why they matter; replaced on each ingest.
- **`## Analysis`** — investigative claims about the entity, each dated, page-linked, and with an optional verbatim quote. Claims accumulate as a list for single-document entities and are synthesized into prose once the entity appears in two or more documents.
- **`## Timeline`** — datable events involving this entity, in order, linked to source pages.
- **`## Relationships`** — connections to other entities, with source citations.
- **`## Notes`** — yours. Watchdog never writes to this section, so annotations here survive every ingest.

Every source citation is a direct page link into the original file in the morgue — `[[morgue/.../file.pdf#page=3|p. 3]]` — so you can jump from any fact straight to the page it came from.

## Stated vs inferred

Every extracted fact records its **basis** — whether the document said it, or Watchdog reasoned to it:

| Basis | Meaning |
|-------|---------|
| `stated` | Directly stated in the document — a quote, a figure, an explicit assertion. The default, left unmarked in the notes. |
| `inferred` | Reasoned from the document rather than stated outright. Marked *(inferred)* in the notes. |

Only inferred facts are flagged, so anything **unmarked is directly stated**. Treat an *(inferred)* fact as a lead that requires verification, not as an established fact.

## Figures that aren't on the page they cite

Watchdog checks every number in a stated fact against the page the fact cites (and the pages either side of it, allowing for statements printed in thousands or millions, and for figures reported to fewer digits than the page prints them at — a statement showing `360,291` in a thousands column backs a fact that says "$360.3 million"). Nothing is blocked or rewritten — but where a number doesn't check out, the fact carries a short note:

| Note | What it means |
|------|---------------|
| *(figure 173,471 not found in the document — may be derived; verify against source)* | The number appears nowhere in the source document. Usually it was calculated — a total, a difference, a gap between two figures — rather than read off the page. Check the arithmetic before you use it. |
| *(figure 197.6 (p. 3) found on another page, not the one cited)* | The number is real and appears in the document, just not where the fact says. The page link may point at the wrong page. |

Dates are not checked this way — only figures — and roughly two to three per cent of facts carry a note, so one is worth stopping on. A fact with no note had its figures found where it said they were.

When a new document contradicts a fact already in the vault — a different address, a conflicting date, a mismatched role — that is not a basis level. It surfaces as a `[!contradiction]` callout in the entity's note, with both sources cited. A contradiction is often newsworthy in itself: two official records that disagree can be the story.

Contradictions are raised whatever the two claims are marked as — including where one side is *(inferred)* or carries a figure note. A conflict is too important to hide, and the occasional one that turns out to be the model's own error is the price of not missing a real one. Both sources and pages are always cited, so check them before you rely on it.

> **Verify before you publish.** AI extraction makes mistakes. Every fact links to its source document and page; facts the model inferred rather than read are marked *(inferred)* and are leads, not findings. Follow the link before publishing.

## Supported file types

| Format | Extensions | Notes |
|--------|-----------|-------|
| PDF | `.pdf` | Text-based or scanned, or a mix of both; OCR applied automatically, page by page, wherever the text layer is missing or garbled |
| Word document | `.docx` | Tables and formatting preserved |
| Excel spreadsheet | `.xlsx` | |
| Image | `.jpg`, `.jpeg`, `.png`, `.tiff`, `.tif` | OCR applied automatically |
| Web page | `.html`, `.htm` | |
| Plain text | `.txt`, `.md` | |
| Audio / video | `.mp3`, `.mp4`, `.m4a`, `.wav` | Requires the optional transcription install — see [Installation](install.md) |

### Embedded file metadata

Most formats above carry metadata about themselves, separate from anything written in the document's own text: a PDF or Office file's author, creation and modification dates, and the software that produced it; an image's camera make and model and, if present, GPS coordinates; an audio or video file's duration and encoder. Word, Excel, and PowerPoint files often also record the company whose template they were built from, and the total number of minutes the file was actually edited. Watchdog reads whatever a file carries and records it in the document's `documents.json` registry entry — it does not appear in the document note itself.

Two of those fields repay a second look. A company name shared across documents that are supposedly unrelated points to a shared template, and therefore a shared drafter — the same kind of thread as two companies sharing a registered agent. And a long, weighty report with only a few minutes of editing time was assembled from something else, not written.

Treat this metadata as a lead, not a fact. It is trivially easy to forge, and often says nothing about who actually authored a document: a scanner's software name is not the scan's author, and a template's creation date is inherited by every document built from it. Watchdog does one thing with it automatically — if a document's embedded creation date falls a year or more after the date the document itself claims to be from, and the document was not OCR'd, Watchdog flags the mismatch as a warning during ingest. A "2019 agreement" whose file was created in 2023 is worth asking about.



### Sidecar files

A `.yml` file with the same base name as a document is a **sidecar** — metadata attached to the file beside it, never ingested as a document itself:

```
shell-co-annual-report-2023.pdf
shell-co-annual-report-2023.yml
```

The sidecar can record where the document came from and any note you want attached to it, using these fields:

```yaml
source: https://www.sedar.com/filing/xyz
obtained: 2026-06-05
notes: Check the director change on page 12.
```

Any other field is dropped — a sidecar isn't a place to invent your own metadata schema. This context is merged into the document record and preserved through ingest. Watchdog also writes sidecars of its own: files downloaded by `watchdog fetch` and web research arrive in `_INCOMING/` with a provenance sidecar already attached.

Edit a sidecar before running `chew`: chew reads it once, and the file is gone afterward, so an edit made between `chew` and `ingest` has no effect. Re-run `chew` if you need to change one.

A sidecar can also pin that one document's record skill:

```yaml
skill: bankruptcy
```

Unlike `notes` and `source`, this field never reaches the model — it is read directly and skips classification for that document, the same way `--skill` does for a whole run. That means a batch mixing document types (a corporate filing next to a court order, say) can pin each one correctly in a single `ingest`, rather than needing one run per type. See [Skills](skills.md#reading-and-pinning-skills).

---

**Where next:** [Investigating](investigating.md) for the day-to-day work of reading and questioning the vault, or [Skills](skills.md) for the domain knowledge Watchdog applies while filling it.
