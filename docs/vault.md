# The vault

This page explains what Watchdog builds on disk and how to read it: the folders, the notes, the dashboard, and the markers that tell you whether a fact was read from a document or inferred by the model. You can do all your work inside the Watchdog app without ever opening these folders, but it helps to know what is there: the files are yours, and they stay readable with or without the app. Read it once your first batch of documents has been added and you want to know what each file is.

## One vault per investigation

Each investigation is its own vault — an ordinary folder of markdown files (plain text with light formatting). Watchdog writes to it; you read, search, and annotate it. There is no proprietary format and nothing locked away. To see the folder, choose **File → Show in Folder** in the app, or **Show in folder** in the **⋯** menu on the All investigations screen.

[Obsidian](https://obsidian.md), a free note-taking program, is optional. The vault is laid out so that Obsidian can open it, and **File → Open in Obsidian** does that if Obsidian is installed. Watchdog does not need it, and nothing on this page requires it. Any program that reads markdown will do.

The vault is also safe to edit. Watchdog's internal records — machine-readable files under `.watchdog/`, including each document's extracted facts — are the source of truth, and the entity and document notes you see are generated from them. Deleting a note loses nothing: **Rebuild notes** under **Activity → Maintenance** writes every entity and document note again from those records, entity summaries included, without calling an AI model. Only your own **Notes** sections live in the notes alone, so they are kept as they are when a note is rebuilt, and lost if you delete the note.

## The directory tree

```
my-investigation/
├── incoming/               ← public records waiting to be read
│   ├── failed/             ← files that could not be processed
│   └── skipped/            ← exact duplicates and empty-text files, set aside
├── context/                ← background material (prior stories, notes)
├── morgue/                 ← original files after processing, each beside its full extracted text
├── .watchdog/              ← internal state: processing queue, staging area,
│                             registries — do not edit
├── entities/
│   ├── person/             ← people
│   ├── organization/       ← companies, banks, unions, funds, non-profits
│   ├── public-body/        ← governments, regulators, courts, agencies
│   ├── place/              ← addresses, properties, locations
│   ├── asset/              ← vehicles, accounts, domains, shares
│   └── proceeding/         ← lawsuits, insolvencies, inquiries
├── documents/              ← one note per added document
├── briefings/              ← briefings, leads, and watch-word alerts
├── wiki/                   ← investigation thread pages (matured angles)
├── queries/                ← saved answers to questions you have asked
├── .fulltext/              ← full-text search index
├── .embeddings/            ← semantic search index
├── log.md                  ← append-only processing history
├── timeline.md             ← chronological event log across the investigation
├── context.md              ← your investigation intent and key questions
├── watchlist.md            ← terms to watch for in new documents (one per line)
├── requests.md             ← documents to go and get, regenerated after every run
├── verification.md         ← the facts you have marked, generated from your checks
├── merges.md               ← every entity merge and every possible-same pair, generated
├── index.md                ← landing page linking to the dashboard
└── dashboard.base          ← dashboard of live tables (Obsidian Bases)
```

## Folder by folder

**`incoming/`** is where documents wait to be read. When you choose **Add documents**, the app copies your files here (the originals stay where they were) and reads them. You can also drop files into this folder yourself and read them from **Activity → Maintenance** (the Pre-processing card), or switch on **Watch incoming for new files** there to have them read as they arrive. See [Getting started](getting-started.md) for the workflow. Two subfolders catch problems: `failed/` holds files that could not be processed (password-protected, corrupted, or an unsupported format), and `skipped/` holds exact duplicates of documents already added, plus files in which no text could be found. [Troubleshooting](troubleshooting.md) covers what to do with each.

**`context/`** holds background material — prior published stories, your notes, screenshots. Files here are not added as records; they feed the context interview that writes `context.md`, described in [Getting started](getting-started.md).

Vaults created by earlier versions of Watchdog called these folders `_INCOMING/` and `_CONTEXT/`. They are renamed automatically, with nothing deleted, the first time the vault is opened. If you kept scripts or Obsidian plugins that point at the old names, update them.

**`morgue/`** is where original files land after a successful run, organized by entity and document type. Each original sits beside a markdown file of its full extracted text, so you can search the complete text of every document from Watchdog's Search screen or from any program that searches files. Nothing is ever discarded: the file you dropped in is the file in the morgue.

**`.watchdog/`** is Watchdog's internal state: the processing queue, a staging area for files mid-pipeline, and the registries that record every entity, document, and relationship. Do not edit anything in here by hand.

**`entities/`** holds one note per extracted entity, filed by type. Watchdog sorts every entity into one of six fixed classes: **person** (people), **organization** (companies, banks, unions, funds, non-profits), **public-body** (governments, regulators, courts, agencies), **place** (addresses, properties, locations), **asset** (vehicles, accounts, domains, shares), and **proceeding** (lawsuits, insolvencies, inquiries). Fixing the list to these six keeps the same real-world entity from being split across near-duplicate folders when the model describes it differently in two documents. These notes are the heart of the vault; their structure is described [below](#entity-notes).

**`documents/`** holds one note per added document: what it is, what was extracted from it, and a link to the original in the morgue. A fact you have marked Disputed ends in **✗ disputed** there; it is never removed.

**`briefings/`** collects the reports Watchdog writes after each run: a briefing of new entities, connections, and anomalies; a leads file (`leads-<date>.md`); and watch-word alerts (`alerts-<date>.md`) when a watchlist term appears. [Investigating](investigating.md) explains how to work with each.

**`wiki/`** holds investigation thread pages — angles that have matured beyond a single question, created in **Ask Claude** with `/watchdog-wiki`.

**`queries/`** holds saved answers that **Ask Claude** files (through `/watchdog-query`) when a question produces a substantive answer worth keeping.

**`.fulltext/`** and **`.embeddings/`** are the indexes behind the Search screen — one for exact matches, one for meaning. They are rebuilt from disk by **Rebuild the search index** under **Activity → Maintenance**; you never touch them directly.

### The root files

- **`hot.md`** — investigations started with an earlier version of Watchdog have one: a summary the briefing model rewrote after every run. Watchdog no longer writes or reads it, and leaves it where it is; you can delete it. Each Ask Claude conversation now starts with an account of the whole investigation that Watchdog builds from the records (see [Ask Claude](investigating.md#ask-claude)).
- **`log.md`** — an append-only, human-readable record of every run.
- **`timeline.md`** — every datable event extracted across the investigation, assembled into one chronological view. An event whose fact you have marked Disputed ends in **✗ disputed**.
- **`context.md`** — your investigation intent and key questions, written by the context interview (**Seed investigation context** on the Ask Claude screen). You can also edit it directly under **Briefings**. It is sent to the AI model with every document you add, so write it as open questions and keep confidential material out of it; see [Seed your context](getting-started.md#seed-your-context-optional-but-recommended).
- **`watchlist.md`** — terms you want flagged when they appear in new documents, one per line. Edit it on the **Watch list** tab of Review; the format and the scan are covered in [Investigating](investigating.md#the-watch-list).
- **`requests.md`** — documents named in what you have already added that you could go and get: a hearing transcript an order cites, an enabling regulation, a referenced filing. Regenerated after every run and covered in [Investigating](investigating.md#document-requests).
- <a id="verificationmd"></a>**`verification.md`** — the facts you have marked Verified, Disputed or Can't verify, grouped by status and then by document, with who marked each, when, and any note. It opens with progress ("12 of 340 facts verified"). A final section lists marks whose fact has since changed. Watchdog rewrites the file on every change, so mark facts in the app, not here; edits to the file are not kept. The marks themselves are stored in `.watchdog/registry/verification.json`, which you should not edit either. See [Checking facts](investigating.md#checking-facts).
- <a id="mergesmd"></a>**`merges.md`** — every time Watchdog treated two records as the same person, organization, place or thing, newest first: which records, how confident the decision was, who made it (Watchdog's rules, the AI model, or a reporter, by name), the reason and the documents. It opens with the pairs still waiting as **possible same entities**. Watchdog rewrites the file whenever the log changes, so edits to it are not kept. The log itself is `.watchdog/registry/merges.json`, which you should not edit; besides what the note shows, it keeps for each merge exactly which records, facts and relationships came with the merged record, so **Undo merge** can split it back apart, and marks a merge that has been undone. See [Merges](investigating.md#merges).
- **`index.md`** — a thin landing page that links to the dashboard.
- **`dashboard.base`** — the dashboard itself, described next.

## The dashboard

`dashboard.base` is a dashboard of live tables: most-mentioned entities, recent documents, people, companies, single-source entities to review, and possible duplicate documents. The tables refresh as you add documents.

The dashboard is for people who open the vault in Obsidian. It uses [Obsidian Bases](https://help.obsidian.md/bases), a core Obsidian feature in version 1.9 and up, so there is nothing to install — no community plugin, no restricted mode to clear. Click a column header to sort (by **Documents**, say, to surface the most-mentioned entities); click a row to open the note. If you work only in the Watchdog app, you do not need it: the Overview, Entities and Review screens show the same things.

Two tables deserve attention. **Possible duplicate documents** lists documents that closely match one already in the vault; Watchdog never discards them, so decide whether each pair is the same document. **Single-source entities** is where a duplicate entity — the same person or company under two ids — usually shows up; the fix is **Merge**, covered in [Investigating](investigating.md#duplicate-entities).

## Entity notes

An entity note is a view of the investigation's records, rewritten whenever something about the entity changes. It has up to six sections:

- **`## Summary`** — an overview of who this entity is and why they matter, written by an AI model from the entity's facts once the entity appears in two or more documents. Where a sentence rests on a fact, it ends in a citation such as `(p. 4)` that links to that fact (see [Fact citations](#fact-citations)). Sentences without a citation are the model's own framing: connecting, summing up. The summary can be wrong. The facts below it are the record. A summary written before another record was merged into this one, or before a merge was undone, ends in an **Out of date** warning; the next run that adds documents rewrites it.
- **`## Facts`** — every fact, from every document added so far, that is about this entity. Each line gives the fact, its document and a link to the page, any warning (*(inferred)*, a figure not found on the page), and your own check of it: **✓ verified**, **✗ disputed**, **? can't verify** or **not checked**. A fact you dispute stays in the list, labelled. Facts with dates come first, in the order the events happened; facts without their own date take their document's date. Where the model quoted the document, or Watchdog found the sentence the fact rests on, it is shown under the fact.
- **`## Contradictions`** — places where two documents disagree about the entity, each side cited.
- **`## Relationships`** — connections to other entities, with source citations.
- **`## Notes`** — yours. Watchdog never writes to this section, so annotations here survive every run and every rebuild.

An entity named in many documents can have hundreds of facts. Up to 40 are listed one by one. Past that, the list is grouped by document, five facts per document plus every fact you have marked, with a link to the document's own note for the rest; the 40 most recent documents get a group each and earlier ones one line each, with a count. The full list, with source passages, is always on the entity's page in the app.

Every source citation is a direct page link into the original file in the morgue — `[[morgue/.../file.pdf#page=3|p. 3]]` — so you can jump from any fact straight to the page it came from. Each fact line, in an entity note and in its document's note, ends with a short marker such as `^f-3a9c5b2e1d`, which Obsidian hides; it lets other notes link to that exact fact.

Investigations started with an earlier version of Watchdog have their notes rewritten in this form the next time documents are added, or when you choose **Rebuild notes**. Their summaries are kept. Claims recorded for documents whose extracted facts were not kept are kept too, as written, under **Earlier claims**.

## Fact citations

Text written by an AI model — entity summaries and briefings — and the pages Claude writes in Ask Claude (saved answers in `queries/`, threads in `wiki/`, research memos) cite facts by linking to the fact's line in its document's note:

```
The City paid $4,350,000 for the property ([[documents/payment-register#^f-264b05b025|p. 1]]).
```

In Obsidian the citation reads "(p. 1)" and opens the document's note at that fact. Several citations in a row read "(p. 1; p. 3)". A citation of a fact you have marked Disputed reads "(p. 1, disputed)".

Watchdog checks every citation against the facts it has stored. When it writes a summary or a briefing, a citation that names no stored fact is left out, and the sentence stays; it never links a sentence to a fact the model did not cite. A citation can stop resolving later, when a document is processed again and its facts are reworded, or when a merge is undone; the app then shows it as **source not found**. **Activity → Maintenance → Check citations** lists every such citation in saved answers, threads and briefings, without changing any page.

Not every sentence carries a citation, and that is expected. A model writing a summary or a briefing connects facts and frames them, and those sentences stand without one. A name, date, figure or event from a document should carry one.

## Stated vs inferred

Every extracted fact records its **basis** — whether the model says it read the fact in the document, or reasoned to it:

| Basis | Meaning |
|-------|---------|
| `stated` | The model's default: it reports reading the fact in the document — a quote, a figure, an explicit assertion. Left unmarked in the notes. |
| `inferred` | The model flagged the fact as its own reasoning rather than something the document says outright. Marked *(inferred)* in the notes. |

The label is the model's own hint, not a check. When the model marks a fact *(inferred)*, treat it as a lead that requires verification, not as an established fact. But the model marks very few facts this way, and it does not catch all of its own reasoning, so an **unmarked fact is not thereby guaranteed to be stated** in the document. To check any fact, read its source passage and record what you find with a verification mark (see [Checking facts](investigating.md#checking-facts)); a fact with no matching passage deserves a closer look.

## Figures that aren't on the page they cite

Watchdog checks every number in a stated fact against the page the fact cites (and the pages either side of it, allowing for statements printed in thousands or millions, and for figures reported to fewer digits than the page prints them at — a statement showing `360,291` in a thousands column backs a fact that says "$360.3 million"). Nothing is blocked or rewritten — but where a number doesn't check out, the fact carries a short note:

| Note | What it means |
|------|---------------|
| *(figure 173,471 not found in the document — may be derived; verify against source)* | The number appears nowhere in the source document. Usually it was calculated — a total, a difference, a gap between two figures — rather than read off the page. Check the arithmetic before you use it. |
| *(figure 197.6 (p. 3) found on another page, not the one cited)* | The number is real and appears in the document, just not where the fact says. The page link may point at the wrong page. |

Dates are not checked this way — only figures — and roughly two to three per cent of facts carry a note, so one is worth stopping on. A fact with no note either had its figures found where it said they were, or was not checked: facts marked *(inferred)*, facts that cite no page, and facts whose page has no text are skipped.

When a new document contradicts a fact already in the vault — a different address, a conflicting date, a mismatched role — that is not a basis level. It surfaces as a `[!contradiction]` callout in the entity's note, with both sources cited. The check compares each new document's facts with the facts already recorded about the entity, and the sentences on the page they rest on; it never compares against the summary. Facts you have marked Disputed are still compared, and still shown to the summary, labelled as disputed, so a conflict with one is not missed and the summary never presents one as established. Where Watchdog knows which fact each side comes from, the side links to that fact, and a side resting on a fact you dispute ends in *disputed*. A contradiction is often newsworthy in itself: two official records that disagree can be the story.

Contradictions are raised whatever the two claims are marked as — including where one side is *(inferred)* or carries a figure note. A conflict is too important to hide, and the occasional one that turns out to be the model's own error is the price of not missing a real one. Both sources and pages are always cited, so check them before you rely on it.

> **Verify before you publish.** AI extraction makes mistakes. Every fact links to its source document and page; facts the model flagged as inferred rather than read are marked *(inferred)* and are leads, not findings. An unmarked fact can still be the model's reasoning, so follow the link before publishing.

## Supported file types

| Format | Extensions | Notes |
|--------|-----------|-------|
| PDF | `.pdf` | Text-based or scanned, or a mix of both; OCR applied automatically, page by page, wherever the text layer is missing or garbled |
| Word document | `.docx` | Tables and formatting preserved |
| Excel spreadsheet | `.xlsx` | |
| PowerPoint presentation | `.pptx` | |
| Image | `.jpg`, `.jpeg`, `.png`, `.tiff`, `.tif`, `.bmp`, `.webp` | OCR applied automatically |
| Web page | `.html`, `.htm` | |
| Plain text | `.txt`, `.csv`, `.md` | |
| Audio | `.mp3`, `.m4a`, `.wav`, `.aac`, `.ogg`, `.oga`, `.opus`, `.flac` | Transcribed on your computer; see [Recordings](#recordings) |
| Video | `.mp4`, `.m4v`, `.mov`, `.webm`, `.mkv`, `.avi` | The sound track is transcribed; a video with no sound cannot be added |

### Recordings

Watchdog turns an interview, a council meeting or a press conference into text on your own computer, with a speech-recognition model, before anything else happens to it. Nothing about the recording is sent anywhere while it is transcribed. The first recording you add downloads the model once; see [Installation](install.md#the-transcription-model).

A transcript's "pages" are blocks of time: page 1 is the first five minutes, page 2 the next five, and so on. A fact from a recording therefore cites a stretch of it, shown in the app as a time range such as 10:00–15:00 rather than a page number. A five-minute block with no speech in it has no page, so the page numbers of a recording with a long silence can skip. Inside a page, each passage starts with the time it was spoken, written as `[00:12:05]`.

A machine transcript is a draft, not a record. It has no speaker names, so it cannot tell you who said what unless the speech itself does ("Councillor Ferreira, you have the floor"). It mishears: names are the usual casualty, then figures and technical terms. Watchdog tells the model it is reading a transcript and to flag names and figures that look wrong, but it cannot know what was really said. Before you quote anyone, click the timestamp in the app and listen.

Changing the transcription model in [Settings](configuration.md#transcription) affects recordings added afterwards; a recording already in the investigation keeps its transcript. Each transcript records the model and settings that produced it.

### Embedded file metadata

Most formats above carry metadata about themselves, separate from anything written in the document's own text: a PDF or Office file's author, creation and modification dates, and the software that produced it; an image's camera make and model and, if present, GPS coordinates; an audio or video file's duration and encoder. Word, Excel, and PowerPoint files often also record the company whose template they were built from, and the total number of minutes the file was actually edited. Watchdog reads whatever a file carries and records it in the document's `documents.json` registry entry — it does not appear in the document note itself.

Two of those fields repay a second look. A company name shared across documents that are supposedly unrelated points to a shared template, and therefore a shared drafter — the same kind of thread as two companies sharing a registered agent. And a long, weighty report with only a few minutes of editing time was assembled from something else, not written.

Treat this metadata as a lead, not a fact. It is trivially easy to forge, and often says nothing about who actually authored a document: a scanner's software name is not the scan's author, and a template's creation date is inherited by every document built from it. Watchdog does one thing with it automatically — if a document's embedded creation date falls a year or more after the date the document itself claims to be from, and the document was not OCR'd, Watchdog flags the mismatch as a warning during processing. A "2019 agreement" whose file was created in 2023 is worth asking about.



### Sidecar files

A `.yml` file with the same base name as a document is a **sidecar** — metadata attached to the file beside it, never added as a document itself:

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

Any other field is dropped — a sidecar isn't a place to invent your own metadata schema. This context is merged into the document record and preserved through processing. Watchdog also writes sidecars of its own: files downloaded with **Fetch Links** and by web research arrive in `incoming/` with a provenance sidecar already attached.

Edit a sidecar before the document is read (before you choose **Add documents**, or before running pre-processing in Maintenance): the sidecar is read once during that step, and the file is gone afterward, so a later edit has no effect. Add the document again if you need to change one.

A sidecar can also pin that one document's record skill:

```yaml
skill: bankruptcy
```

Unlike `notes` and `source`, this field never reaches the model — it is read directly and skips classification for that document, the same way the **Record skill** option does for a whole run. It must name a skill from the catalogue (**Settings → Record skills** lists them); a file path is ignored here and the document is classified instead, since a sidecar can arrive with a document you didn't write yourself. That means a batch mixing document types (a corporate filing next to a court order, say) can pin each one correctly in a single batch, rather than needing one batch per type. See [Skills](skills.md#reading-and-pinning-skills).

---

**Where next:** [Investigating](investigating.md) for the day-to-day work of reading and questioning the vault, or [Skills](skills.md) for the domain knowledge Watchdog applies while filling it.
