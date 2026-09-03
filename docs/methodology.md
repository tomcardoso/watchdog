# Methodology

This page explains what actually happens to your documents when you run Watchdog — what a piece of software does on its own, what an AI model reads and writes, and why the pipeline is built this way. It assumes no programming background. If you want the practical walkthrough instead, start with [Getting started](getting-started.md); if you need the precise technical reference, see `ARCHITECTURE.md` in the project's code repository.

Knowing this matters for the same reason a reporter needs to know how a database query works before quoting its results: if an editor, a lawyer, or a source asks how a fact in your notes was produced, you should be able to answer in plain terms.

## Three stages, one document at a time

Every document you drop into a vault goes through three stages, in order. Watchdog calls them **chew**, **dig**, and **bark** — you can run all three with one command, or run them one at a time when you want to check the results in between.

| Stage | What happens | Who does it |
|---|---|---|
| Chew | Convert the file into readable text | A local program on your computer — no AI, no cost, nothing sent anywhere |
| Dig | Read the text and note the facts | A cloud AI model, reading one document at a time |
| Bark | Cross-check, merge, and write up the vault | A mix of plain bookkeeping and a cloud AI model, working across every document you have |

The rest of this page walks through each stage.

## Chew: turning a document into text

Before anything can be read, it has to become text a computer can search. That is chew's job, and it runs entirely on your own computer — no document leaves your machine at this stage, and it costs nothing.

If a PDF already has selectable text, Watchdog reads it directly. If it doesn't — a scanned court filing, a photographed ledger page, a fax — Watchdog runs optical character recognition (OCR): the same idea as a scanner turning a photo of a page into text you can copy and paste, just automated. The tool doing this reading is called Docling, an open-source document-reading program; it also recognizes tables and page layout, so a financial statement's rows and columns come through as structured text rather than a jumble.

Chew also does two pieces of housekeeping, both without any AI involved:

- **Duplicate checking.** Every document gets a digital fingerprint of its contents. If you drop in a file you (or a colleague) already gave Watchdog, it is set aside rather than processed twice. A looser version of the same check flags documents that are *similar but not identical* — a redlined revision of a contract, say — so you can decide whether they matter separately.
- **Splitting large files.** A hundred-page PDF is broken into pieces so the next stage can handle it efficiently, then stitched back together with the page numbers intact.

Nothing at this stage decides what a document *means*. Chew only produces clean, searchable text — the raw material for the next stage.

## Dig: reading like a reporter

This is where an AI model reads each document and takes notes — the way a reporter marks up a printout with a highlighter, except every note is structured and every one is tied to a specific page.

Two things happen here, both cloud AI calls:

**First, the model figures out what kind of document it is** — a bankruptcy filing, a municipal contract, a real-estate transfer, a court affidavit, and so on. Watchdog keeps a library of instructions for dozens of document types (a "record skill"), each one written for what matters in that kind of record — a bankruptcy filing and a real-estate deed are read for very different things. Identifying the type first means the model gets the right set of instructions for the document actually in front of it.

**Second, the model reads the whole document and writes down what matters.** For each material fact — a dollar figure, a date, an allegation, a stated change of ownership — it records the fact itself, which page it came from, and a short quoted phrase you can search the source text for. It also lists every person, company, and place named in the document. This is deliberately close to how a journalist reads primary source material: not summarizing in the abstract, but pulling out the concrete, checkable claims and flagging who is involved.

A model reading quickly can still skim past a fact buried in a footnote or a table row. As a backup, Watchdog runs a small, local, non-AI check alongside the model's reading — it scans the page for the shapes of names, dollar figures, dates, and case numbers, and hands the model a checklist of everything it found, so a genuinely material figure sitting in a dense table is less likely to be missed. This checklist tool is called GLiNER; it runs on your computer and is installed automatically as part of setup.

Every fact the model records is either something the document **states** outright, or something the model **inferred** from what's stated — and Watchdog marks the difference. An inferred fact is a lead worth chasing, not a finding you can cite on its own; always follow the citation back to the source page before you rely on either kind.

The document's full text is never sent anywhere at this stage beyond the one cloud AI call reading it — see [what stays on your machine](#what-stays-on-your-machine-and-what-doesnt) below for the exact boundary.

## Bark: cross-checking and writing up

Reading one document at a time only gets you so far — the real value of an investigation is in what connects across documents. That happens at bark, after every document in the batch has been read.

Some of this is plain bookkeeping, done by ordinary software with no AI involved at all: merging a person's or company's facts from every document they appear in onto one note, sorting events into a timeline, filing away exact-duplicate names.

The parts that need judgment go to a cloud AI model, working across the whole batch at once:

- **Recognizing the same person or company under different names.** "Laurentian University" and "Laurentian University of Sudbury" are the same institution; a mechanical check can fold together names that are identical, but only a model can confidently say two *different-looking* names refer to the same real-world entity — and it is deliberately cautious about it, since a wrong merge would quietly conflate two different people.
- **Flagging contradictions.** If one document says a company was dissolved in March and another says it filed papers in June of the same year, that discrepancy gets written down as a flagged contradiction on that entity's note — not resolved, not hidden, just surfaced for you to look into.
- **Writing summaries.** Once a person or company has come up in two or more documents, Watchdog writes a short prose summary of what's known about them so far, similar to a researcher briefing you before an interview. A name that has only come up once gets no summary — just its raw facts — so a single passing mention never overwrites an established account.
- **Writing the briefing.** At the end of every run, Watchdog writes a short memo: what came in, what's new, what connects to entities you were already tracking, and what's worth following up. This is the first thing worth reading after any ingest.

## The other models at work

Two more local, no-cost models support the parts of Watchdog you use after ingest — searching the vault. Neither reads or writes anything about what a document means; both just help you find things faster.

- **The embedding model** (`bge-small-en-v1.5` by default) turns every passage of text — and every note Watchdog writes — into a numeric fingerprint that captures its *meaning*, not just its exact words. That is what lets `watchdog search` find a passage about a "shell arrangement" when you searched for "offshore trust" — the words differ, but the fingerprints are close. It runs entirely on your machine.
- **The reranker** (`bge-reranker-base` by default) takes the passages that search turns up and re-orders them for precision, the same way a research assistant might skim a first pass of results and put the genuinely relevant ones on top. It also runs locally, and only at the moment you search — nothing about it is stored.

Both are configurable, and neither is required for the pipeline itself to work — they only affect how well `watchdog search` finds what you're looking for. See [Configuration](configuration.md#search-indexing) for the settings.

## What stays on your machine, and what doesn't

Chew, search, and the two models above never leave your computer and never cost anything. The only things that go to a cloud AI provider are the extracted text sent during dig (one document at a time) and the cross-document material assembled at bark (facts, names, and short quoted excerpts — never the raw original file). This is why Watchdog must only be used on documents that are public or presumptively public; see the [public-records notice](getting-started.md) for what that means in practice.

## Where to go from here

- [Getting started](getting-started.md) — the practical walkthrough, from creating a vault to reading your first briefing.
- [Configuration](configuration.md) — which model runs each stage, what it costs, and how to change either.
- [Investigating](investigating.md) — everything you do with a vault day to day, once documents are in it.
- [One document, from dropped file to result](https://claude.ai/code/artifact/d16050d6-3357-411c-9b88-26271a330435) — an illustrated walkthrough of chew and dig for a single document, with diagrams of the sectioning and OCR decisions and the token budgets involved.
- `ARCHITECTURE.md`, in the project repository — the precise technical reference this page is a plain-English companion to.
