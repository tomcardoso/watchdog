# Methodology

This page explains what actually happens to your documents when you run Watchdog — what a piece of software does on its own, what an AI model reads and writes, and why the pipeline is built this way. It assumes no programming background. If you want the practical walkthrough instead, start with [Getting started](getting-started.md); if you need the precise technical reference, see `ARCHITECTURE.md` in the project's code repository.

Knowing this matters for the same reason a reporter needs to know how a database query works before quoting its results: if an editor, a lawyer, or a source asks how a fact in your notes was produced, you should be able to answer in plain terms.

## Three stages, one document at a time

Every document you add to an investigation goes through three stages, in order. Watchdog calls them **pre-processing**, **processing** and **post-processing**. (The command line and older material call them chew, dig and bark.) **Add documents** runs all three for you. When you want to check the results in between, **Activity → Maintenance** has a card for each stage, so you can run them one at a time.

| Stage | What happens | Who does it |
|---|---|---|
| Pre-processing | Convert the file into readable text | A local program on your computer — no AI, no cost, nothing sent anywhere |
| Processing | Read the text and note the facts | A cloud AI model, reading one document at a time |
| Post-processing | Cross-check, merge, and write up the vault | A mix of plain bookkeeping and a cloud AI model, working across every document you have |

The rest of this page walks through each stage.

## Pre-processing: turning a document into text

Before anything can be read, it has to become text a computer can search. That is pre-processing's job, and it runs entirely on your own computer — no document leaves your machine at this stage, and it costs nothing.

If a PDF already has selectable text, Watchdog reads it directly. If it doesn't — a scanned court filing, a photographed ledger page, a fax — Watchdog runs optical character recognition (OCR): the same idea as a scanner turning a photo of a page into text you can copy and paste, just automated. The tool doing this reading is called Docling, an open-source document-reading program; it also recognizes tables and page layout, so a financial statement's rows and columns come through as structured text rather than a jumble.

An audio or video recording is transcribed instead. A speech-recognition model called Whisper, released by OpenAI as open source and run here through a program called faster-whisper, listens to the sound track and writes down what it hears, with the time each passage was spoken. It runs on your computer like everything else at this stage: the recording is not uploaded anywhere to be transcribed. The transcript is cut into five-minute "pages", so a fact can cite the stretch of the recording it came from, and you can click a time in the app to hear it. Speech recognition is good but not faithful: it has no idea who is speaking, and it mishears names and numbers it has not met before. The extraction stage is told it is reading a machine transcript for that reason. See [Recordings](vault.md#recordings).

Pre-processing also does two pieces of housekeeping, both without any AI involved:

- **Duplicate checking.** Every document gets a digital fingerprint of its contents. If you drop in a file you (or a colleague) already gave Watchdog, it is set aside rather than processed twice. A looser version of the same check flags documents that are *similar but not identical* — a redlined revision of a contract, say — so you can decide whether they matter separately.
- **Splitting large files.** A hundred-page PDF is broken into pieces so the next stage can handle it efficiently, then stitched back together with the page numbers intact.

Nothing at this stage decides what a document *means*. Pre-processing only produces clean, searchable text — the raw material for the next stage.

## Processing: reading like a reporter

This is where an AI model reads each document and takes notes — the way a reporter marks up a printout with a highlighter, except every note is structured and every one is tied to a specific page.

Two things happen here, both cloud AI calls:

**First, the model figures out what kind of document it is** — a bankruptcy filing, a municipal contract, a real-estate transfer, a court affidavit, and so on. Watchdog keeps a library of instructions for dozens of document types (a "record skill"), each one written for what matters in that kind of record — a bankruptcy filing and a real-estate deed are read for very different things. Identifying the type first means the model gets the right set of instructions for the document actually in front of it.

**Second, the model reads the whole document and writes down what matters.** For each material fact — a dollar figure, a date, an allegation, a stated change of ownership — it records the fact itself, which page it came from, and a short quoted phrase you can search the source text for. It also lists every person, company, and place named in the document. This is deliberately close to how a journalist reads primary source material: not summarizing in the abstract, but pulling out the concrete, checkable claims and flagging who is involved.

A model reading quickly can still skim past a fact buried in a footnote or a table row. As a backup, Watchdog runs a small, local, non-AI check alongside the model's reading — it scans the page for the shapes of names, dollar figures, dates, and case numbers, and hands the model a checklist of everything it found, so a genuinely material figure sitting in a dense table is less likely to be missed. This checklist tool is called GLiNER; it runs on your computer and is installed automatically as part of setup.

The model is asked to mark any fact it **inferred** — reasoned from what the document says, rather than read in it — and Watchdog shows that mark beside the fact. An inferred fact is a lead worth chasing, not a finding you can cite on its own. The mark is the model's own hint, though, not a check: the model marks very few facts this way and does not catch all of its own reasoning, so an unmarked fact is not guaranteed to be stated in the document. Always follow the citation back to the source passage before you rely on any fact, and record what you find with a verification mark.

The document's full text is never sent anywhere at this stage beyond the one cloud AI call reading it — see [what stays on your machine](#what-stays-on-your-machine-and-what-doesnt) below for the exact boundary.

### How a passage is found

The model usually records only a page number for a fact, and a quoted phrase just when the exact wording matters. So that you can see the evidence beside the claim, Watchdog then looks for the sentence on that page that best supports each fact. This is ordinary code with no AI model: it breaks the page into sentences, picks out the fact's figures, dates and distinctive words, gives the rarer ones more weight, and keeps the sentence (or pair of sentences) that shares the most of them. If the model's quotation was confirmed in the source, that quotation is used instead. When nothing on the page is close enough, the app says "No matching passage found" rather than guess. That means the fact may rest on several passages, on reasoning, or on a wrong page citation; read the page. A matched passage is a pointer to where to look, not a confirmation that the fact is right. See [Checking facts](investigating.md#checking-facts).

## Post-processing: cross-checking and writing up

Reading one document at a time only gets you so far — the real value of an investigation is in what connects across documents. That happens at post-processing, after every document in the batch has been read.

Some of this is plain bookkeeping, done by ordinary software with no AI involved at all: gathering every fact about a person or company, from every document they appear in, onto one note, and sorting events into a timeline. The facts on an entity's note are always the documents' own facts, each with its page, never a model's retelling of them, and the note is rewritten from those facts every time it changes. That is also why a note can be deleted and rebuilt without calling a model.

<a id="same-entity"></a>

**How Watchdog decides two names are the same.** A name alone is weak evidence, because two different people can share one. So Watchdog asks what else the two records have in common, and acts on how sure that makes it. When both carry the same registration, licence, court file or parcel number, or a person's full name comes with the same role, employer or street address, or a company's or place's name is specific enough to pick out one ("Northgate Civil Works Ltd.", not "the City"), ordinary software merges them. When two people share a full name and nothing else, or a name is short or general, a cloud AI model reads both records' facts side by side and merges them only if it is confident. When one name is an initialled or shortened form of the other ("J. Smith" and "John Smith"), nothing merges them automatically: you decide, on the Merges tab in Review. Every merge, whoever made it, is written to a log you can read, and a pair you mark "not the same" is never merged automatically afterwards.

The parts that need judgment go to a cloud AI model, working across the whole batch at once:

- **Recognizing the same person or company under different names.** "Laurentian University" and "Laurentian University of Sudbury" are the same institution, but only a model can confidently say two *different-looking* names refer to the same real-world entity. It also judges same-name pairs that ordinary software cannot settle, as described above. It is deliberately cautious, since a wrong merge would quietly conflate two different people.
- **Flagging contradictions.** If one document says a company was dissolved in March and another says it filed papers in June of the same year, that discrepancy gets written down as a flagged contradiction on that entity's note — not resolved, not hidden, just surfaced for you to look into. The model compares the facts the new documents add against the facts already recorded about the entity, with the page and the sentence each one rests on; it never compares against a summary.
- **Writing summaries.** Once a person or company has come up in two or more documents, a model writes a short summary of what's known about them, similar to a researcher briefing you before an interview. It is given the entity's facts, each with an id, and asked to cite the ids its sentences rest on; it is not given its own earlier summary, so a mistake in one summary is not carried into the next. For an entity with very many facts it sees a selection — the newest documents' facts, the ones you have verified, then the most recent — and the summary says how many it saw. Facts you have marked Disputed are included with that label, and the summary is told not to present them as established. It is told that one passing mention should not redefine an entity many documents describe. The summary sits above the facts on the entity's note, labelled **AI-written**, and is stored separately, so it never replaces a fact. A name that has only come up once gets no summary, just its facts.
- **Writing the briefing.** At the end of every run, Watchdog writes a short memo: what came in, what's new, what connects to entities you were already tracking, and what's worth following up. This is the first thing worth reading after any run.

## The other models at work

Two more local, no-cost models support the parts of Watchdog you use after adding documents — searching the vault. The transcription model is described under pre-processing, above. Neither reads or writes anything about what a document means; both just help you find things faster.

- **The embedding model** (`bge-small-en-v1.5` by default) turns every passage of text — and every note Watchdog writes — into a numeric fingerprint that captures its *meaning*, not just its exact words. That is what lets the Search screen find a passage about a "shell arrangement" when you searched for "offshore trust" — the words differ, but the fingerprints are close. It runs entirely on your machine.
- **The reranker** (`bge-reranker-base` by default) takes the passages that search turns up and re-orders them for precision, the same way a research assistant might skim a first pass of results and put the genuinely relevant ones on top. It also runs locally, and only at the moment you search — nothing about it is stored.

Both are configurable, and neither is required for the pipeline itself to work — they only affect how well Search finds what you're looking for. See [Settings](configuration.md#search) for the settings.

## What stays on your machine, and what doesn't

Pre-processing (transcription included), search and the two models above never leave your computer and never cost anything. The one network use is downloading a model the first time it is needed. The only things that go to a cloud AI provider are the extracted text sent during processing (one document at a time, each accompanied by your investigation's brief, `context.md`) and the cross-document material assembled during post-processing (facts, names, and short quoted excerpts — never the raw original file). This is why Watchdog must only be used on documents that are public or presumptively public; see the [public-records notice](getting-started.md) for what that means in practice.

## Where to go from here

- [Getting started](getting-started.md) — the practical walkthrough, from creating an investigation to reading your first briefing.
- [Settings](configuration.md) — which model runs each stage, what it costs, and how to change either.
- [Investigating](investigating.md) — everything you do with an investigation day to day, once documents are in it.
- [One document, from dropped file to result](https://claude.ai/code/artifact/d16050d6-3357-411c-9b88-26271a330435) — an illustrated walkthrough of pre-processing and processing for a single document, with diagrams of the sectioning and OCR decisions and the token budgets involved.
- `ARCHITECTURE.md`, in the project repository — the precise technical reference this page is a plain-English companion to.
