# How it works

This page explains what happens to your documents when you add them to Watchdog: what ordinary software does on its own, what an AI model reads and writes, and why it is built this way. It assumes no programming background. If an editor, a lawyer or a source asks how a fact in your notes was produced, you should be able to answer in plain terms.

## Three stages

Every document goes through three stages, in order. **Add documents** runs all three for you; **Activity → Maintenance** has a card for each, so you can run them one at a time.

| Stage | What happens | Who does it |
|---|---|---|
| Pre-processing | Convert the file into readable text | Software on your computer: no AI, no cost, nothing sent anywhere |
| Processing | Read the text and note the facts | A cloud AI model, one document at a time |
| Post-processing | Cross-check, merge and write up the investigation | Ordinary software and a cloud AI model, across every document you have |

## Pre-processing: turning a document into text

Before anything can be read, it has to become text a computer can search. This stage runs entirely on your computer, and costs nothing.

If a PDF already has selectable text, Watchdog reads it directly. If it does not (a scanned court filing, a photographed ledger, a fax), Watchdog runs optical character recognition, or OCR: the same idea as a scanner turning a photo of a page into text you can copy, just automated. It decides page by page, so a 200-page filing with two scanned exhibits gets OCR on those two pages only. The tool doing this is [Docling](https://github.com/docling-project/docling), an open-source document reader that also recognizes tables and page layout, so a financial statement's rows and columns come through as structured text rather than a jumble. Watchdog keeps where each line of OCR'd text sits on the page, so the app can highlight a word on the scan itself; the scan is never altered.

An audio or video recording is transcribed instead, by [Whisper](https://github.com/openai/whisper), an open-source speech-recognition model from OpenAI, running on your computer through [faster-whisper](https://github.com/SYSTRAN/faster-whisper): the recording is not uploaded anywhere. Speech recognition is good but not faithful. It cannot tell who is speaking, and it mishears names and numbers it has not met before, so the next stage is told it is reading a machine transcript.

Pre-processing also fingerprints every document, so a file you (or a colleague) already added is set aside rather than processed twice, and a looser check flags documents that are similar but not identical, such as a redlined revision of a contract.

## Processing: reading like a reporter

This is where an AI model reads each document and takes notes, the way a reporter marks up a printout with a highlighter, except that every note is structured and tied to a page.

**First, the model works out what kind of document it is**: a bankruptcy filing, a municipal contract, a land transfer, a court affidavit. Each type has its own [record skill](#record-skills), a set of instructions for what matters in that kind of record, and the model reads the document with the right one.

**Then it reads the whole document and writes down what matters.** For each material fact (a dollar figure, a date, an allegation, a change of ownership) it records the fact, the page it came from and, when the exact wording matters, a short quotation. It also lists every person, company and place named. This is deliberately close to how a journalist reads primary sources: not summarizing in the abstract, but pulling out the concrete, checkable claims.

<a id="dates"></a>**Dates.** The model writes a fact's date as the document printed it, in whatever language. Ordinary code then turns it into a calendar date using [dateparser](https://github.com/scrapinghub/dateparser), which knows the month names of every language it supports ("12 septembre 2019", "3 maggio 2020", "15 березня 2021", "2021年3月15日"). It is careful in three ways. It places a date only when the year is printed, so "March" or "le 3" is never assumed to mean this year. It keeps only the precision the wording gives, so "mai 2020" goes on the timeline as May 2020, not a made-up day. And it never guesses the order of a date written in figures: 03/04/2021 could be 3 April or 4 March, so it goes on the timeline at 2021 until the step described [under post-processing](#date-pass) settles it. Relative dates ("the following Tuesday") are never read by code.

A model reading quickly can skim past a figure in a footnote or a table row. As a backup, a small model on your computer, [GLiNER](https://github.com/urchade/GLiNER), scans each page for the shapes of names, dollar figures, dates and case numbers, and hands the model a checklist of what it found.

The model is asked to mark any fact it **inferred** (reasoned to, rather than read) and Watchdog shows that mark beside the fact. The mark is only the model's own hint, so always follow a fact back to its source before you rely on it. [Checking facts](using-watchdog.md#checking-facts) explains how.

### How a passage is found

The model usually records only a page number for a fact. So that you can see the evidence beside the claim, Watchdog then looks for the sentence on that page that best supports it. This is ordinary code, not AI: it breaks the page into sentences, picks out the fact's figures, dates and distinctive words, gives the rarer ones more weight, and keeps the sentence (or pair of sentences) that shares the most. If the model's quotation was confirmed in the source, that is used instead. When nothing on the page is close enough, the app says "No matching passage found" rather than guess. A matched passage shows where to look; it does not confirm the fact.

## Post-processing: cross-checking and writing up

The real value of an investigation is in what connects across documents. That happens once every document in the batch has been read.

Much of it is plain bookkeeping by ordinary software: gathering every fact about a person or company onto one note, and sorting events into a timeline. The facts on an entity's note are always the documents' own facts, each with its page, never a model's retelling of them.

<a id="same-entity"></a>**How Watchdog decides two names are the same.** A name alone is weak evidence, because two different people can share one. So Watchdog asks what else the two records have in common, and acts on how sure that makes it:

- When both carry the same registration, licence, court file or parcel number, or a person's full name comes with the same role, employer or street address, or a company's or place's name is specific enough to pick out one ("Northgate Civil Works Ltd.", not "the City"), ordinary software merges them.
- When two people share a full name and nothing else, or a name is short or general, an AI model reads both records' facts side by side and merges them only if it is confident.
- When one name is an initialled or shortened form of the other ("J. Smith" and "John Smith"), nothing merges them automatically: you decide, on the Merges tab in Review.

A short name can turn up in dozens of documents, each time recorded on its own, because the name alone cannot say whether they mean one thing. The model reads all the records with that name together, up to 30 at a time, and says which of them are the same and which it cannot tell apart; those it cannot tell apart wait for you in Review. A larger set is read in parts, then the results of the parts are read together. Anything a run could not get to is compared again on the next run, and the run's output says how many.

Every merge is written to a log you can read, and a pair you mark "not the same" is never merged automatically afterwards.

The parts that need judgement go to an AI model:

- **Recognizing the same entity under different names.** "Calder Valley University" and "CVU" are the same institution, but only a model can say two different-looking names refer to the same one. It is deliberately cautious, since a wrong merge would quietly conflate two different people.
- **Flagging contradictions.** If one document says a company was dissolved in March and another says it filed papers in June, the discrepancy is flagged on the entity, with both sources: not resolved, not hidden. The model compares each new document's facts with the facts already recorded, and the sentences they rest on, never a summary. Older facts are not compared with each other on every run, which keeps each run's cost tied to what it adds; **Re-check contradictions** does that on demand.
- <a id="relationship-wordings"></a>**Grouping relationship wordings.** One document calls a person "lawyer for" a group, another "counsel for" it. Software first treats wordings as the same when they differ only in capitals, punctuation, an article or a plain plural, and never treats a change of word as trivial. A model then decides which of the remaining wordings name exactly the same relationship, and is told to keep apart anything that differs in rank, side, scope or time (a partner and a senior partner, counsel for and counsel against, a director and a former director). The documents' own wordings stay visible beside the one shown.
- <a id="date-pass"></a>**Settling the dates code could not.** Before anything is saved, the dates code could not read (a period such as "April to June 2024", a quarter, a date in words, a relative date, or one written in figures that can be read two ways) go to a model with the fact and the document's own date. It answers a date, a start and an end, or nothing. Code checks every answer: it must be a real calendar date, fall within the years the document printed, and, for a date in figures, be one of its two readings. A date the model settled is marked **date inferred** on the timeline, with the printed wording beside it. When the step does not run or finds no answer, a date written in figures keeps its year and the rest stay off the timeline; the fact itself is always kept.
- **Writing summaries.** Once an entity has come up in two or more documents, a model writes a short summary of what is known about it, citing the facts its sentences rest on. It works from the facts, not from its own earlier summary, so a mistake is not carried forward. Facts you have disputed are labelled, and the summary is told not to present them as established. The summary sits above the facts and never replaces them.
- **Writing the briefing.** At the end of every run, a model writes a short memo on what came in, what is new, what connects to entities you were already tracking, and what is worth following up, citing the facts it rests on.

**Checking citations** is code, not a model. Each citation names a fact by its id; code turns it into a link, drops it from a summary or briefing if it names no stored fact, and labels it if you have disputed the fact. Sentences without a citation are allowed, because a summary connects and frames facts; the cited facts remain the record.

**Starting a conversation with Claude** is bookkeeping too: ordinary software builds a short account of the whole investigation from its records and gives it to Claude first.

## Search

Two more small models run on your computer and support Search. The **embedding model** ([bge-small-en-v1.5](https://huggingface.co/BAAI/bge-small-en-v1.5) by default, run through [fastembed](https://github.com/qdrant/fastembed)) turns every passage and note into a numeric fingerprint of its meaning, which is what lets a search for "offshore trust" find a passage about a "shell arrangement". The **reranker** ([bge-reranker-base](https://huggingface.co/BAAI/bge-reranker-base)) re-orders what search turns up, putting the most relevant results on top. Neither reads anything into what a document means; both just help you find things.

## Record skills

A record skill is a plain Markdown file that encodes what an experienced investigative journalist knows about one type of document: which fields are always there, which patterns are anomalous, what the terminology means, and what investigators typically miss. It is the difference between a first-year reporter reading a bankruptcy filing and a twenty-year veteran, who notices the related-party transfer buried in the creditor list.

Watchdog ships with skills for 33 document types. A quick classification step, on a small, inexpensive model, reads each document's opening pages and picks the closest skill. A document that matches none is read with [general records](https://github.com/tomcardoso/watchdog/blob/main/src/watchdog/skills/records/general-records.md), a framework for any unfamiliar record. **Settings → Record skills** lets you read every skill in full.

You can skip classification by pinning a skill: for one run with **Record skill** in the Add documents options, permanently with **Default skill** under [Processing](models-and-cost.md#processing), or for one document with a [sidecar file](using-watchdog.md#sidecar-files).

Skills are jurisdiction-agnostic by default: universal principles come first, with specific jurisdictions (Canada, US, UK, Australia, EU) treated as examples, not defaults.

#### Financial and corporate

| Skill | Covers |
|-------|--------|
| [`corporate-filings`](https://github.com/tomcardoso/watchdog/blob/main/src/watchdog/skills/records/corporate-filings.md) | Registrations, director filings, shareholder registers, beneficial ownership |
| [`financial-statements`](https://github.com/tomcardoso/watchdog/blob/main/src/watchdog/skills/records/financial-statements.md) | Audited statements, annual reports (10-K/10-Q), MD&A, auditor opinions, related-party disclosures |
| [`regulatory-filings`](https://github.com/tomcardoso/watchdog/blob/main/src/watchdog/skills/records/regulatory-filings.md) | Securities disclosures, insider trading reports, SEDAR+/EDGAR filings |
| [`bankruptcy`](https://github.com/tomcardoso/watchdog/blob/main/src/watchdog/skills/records/bankruptcy.md) | Bankruptcy filings, creditor lists, trustee reports, restructuring proceedings |
| [`insurance-filings`](https://github.com/tomcardoso/watchdog/blob/main/src/watchdog/skills/records/insurance-filings.md) | Regulatory returns, actuarial reports, reinsurance treaties, market conduct reviews |
| [`tax-documents`](https://github.com/tomcardoso/watchdog/blob/main/src/watchdog/skills/records/tax-documents.md) | Charity information returns (T3010, Form 990), nonprofit filings, trust returns |

#### Legal and regulatory

| Skill | Covers |
|-------|--------|
| [`court-documents`](https://github.com/tomcardoso/watchdog/blob/main/src/watchdog/skills/records/court-documents.md) | Civil claims, affidavits, judgments, orders, injunctions |
| [`criminal-proceedings`](https://github.com/tomcardoso/watchdog/blob/main/src/watchdog/skills/records/criminal-proceedings.md) | Charging documents, bail decisions, trial decisions, sentencing, forfeiture orders |
| [`administrative-tribunals`](https://github.com/tomcardoso/watchdog/blob/main/src/watchdog/skills/records/administrative-tribunals.md) | Quasi-judicial bodies: housing and services human rights, competition, privacy, utility regulation |
| [`labour-arbitration`](https://github.com/tomcardoso/watchdog/blob/main/src/watchdog/skills/records/labour-arbitration.md) | Grievance awards, labour board decisions, unfair labour practices, collective agreements |
| [`immigration-refugee`](https://github.com/tomcardoso/watchdog/blob/main/src/watchdog/skills/records/immigration-refugee.md) | Asylum decisions, detention reviews, deportation orders, judicial reviews |
| [`healthcare-licensing`](https://github.com/tomcardoso/watchdog/blob/main/src/watchdog/skills/records/healthcare-licensing.md) | Discipline decisions, fitness to practise, facility inspections (medicine, nursing, pharmacy) |
| [`professional-licensing`](https://github.com/tomcardoso/watchdog/blob/main/src/watchdog/skills/records/professional-licensing.md) | Discipline decisions for lawyers, accountants, engineers, financial advisers, real estate agents |
| [`legislation`](https://github.com/tomcardoso/watchdog/blob/main/src/watchdog/skills/records/legislation.md) | Statutes, regulations, orders-in-council, bills, policy directives |

#### Government and public records

| Skill | Covers |
|-------|--------|
| [`government-contracts`](https://github.com/tomcardoso/watchdog/blob/main/src/watchdog/skills/records/government-contracts.md) | The full procurement lifecycle: RFPs, bids, sole-source justifications, awards, amendments, vendor performance |
| [`audit-reports`](https://github.com/tomcardoso/watchdog/blob/main/src/watchdog/skills/records/audit-reports.md) | Auditor general reports, performance audits, inspector general reports |
| [`government-reports`](https://github.com/tomcardoso/watchdog/blob/main/src/watchdog/skills/records/government-reports.md) | Royal commissions, public inquiries, committee reports, white papers, consultations |
| [`foi-responses`](https://github.com/tomcardoso/watchdog/blob/main/src/watchdog/skills/records/foi-responses.md) | FOI/ATI response packages, exemption indexes, redaction logs |
| [`legislature-transcripts`](https://github.com/tomcardoso/watchdog/blob/main/src/watchdog/skills/records/legislature-transcripts.md) | Hansard, committee transcripts, question period, congressional hearings |
| [`lobbying-records`](https://github.com/tomcardoso/watchdog/blob/main/src/watchdog/skills/records/lobbying-records.md) | Lobbyist registrations, communication reports, revolving door disclosures |
| [`election-filings`](https://github.com/tomcardoso/watchdog/blob/main/src/watchdog/skills/records/election-filings.md) | Campaign finance returns, donor lists, third-party advertising disclosures |
| [`municipal-records`](https://github.com/tomcardoso/watchdog/blob/main/src/watchdog/skills/records/municipal-records.md) | Council minutes, zoning decisions, conflict-of-interest declarations |
| [`police-records`](https://github.com/tomcardoso/watchdog/blob/main/src/watchdog/skills/records/police-records.md) | Occurrence reports, use-of-force records, public complaint decisions, coroner's inquests |
| [`corrections-records`](https://github.com/tomcardoso/watchdog/blob/main/src/watchdog/skills/records/corrections-records.md) | Parole board decisions, probation orders, prison inspection reports, correctional oversight |
| [`environmental-filings`](https://github.com/tomcardoso/watchdog/blob/main/src/watchdog/skills/records/environmental-filings.md) | Pollutant release inventories, environmental assessments, compliance orders |

#### Property

| Skill | Covers |
|-------|--------|
| [`real-estate`](https://github.com/tomcardoso/watchdog/blob/main/src/watchdog/skills/records/real-estate.md) | Title transfers, mortgages, liens, assessments, land registry and title systems — common law and civil law; caveats, PPSA/RDPRM charges |
| [`vehicle-registrations`](https://github.com/tomcardoso/watchdog/blob/main/src/watchdog/skills/records/vehicle-registrations.md) | Motor vehicle and vessel registrations, title transfers, liens, fleet records |

#### Specialized

| Skill | Covers |
|-------|--------|
| [`academic-research`](https://github.com/tomcardoso/watchdog/blob/main/src/watchdog/skills/records/academic-research.md) | Grant applications, ethics decisions, conflict-of-interest disclosures, retraction notices |
| [`aircraft-logs`](https://github.com/tomcardoso/watchdog/blob/main/src/watchdog/skills/records/aircraft-logs.md) | Aircraft registrations, ADS-B flight tracks, safety investigation reports |
| [`dns-whois`](https://github.com/tomcardoso/watchdog/blob/main/src/watchdog/skills/records/dns-whois.md) | WHOIS records, DNS data, IP allocation, SSL certificate transparency logs |
| [`news-clippings`](https://github.com/tomcardoso/watchdog/blob/main/src/watchdog/skills/records/news-clippings.md) | News articles, press releases, wire stories, corrections, retractions |
| [`audio-video`](https://github.com/tomcardoso/watchdog/blob/main/src/watchdog/skills/records/audio-video.md) | YouTube transcripts, podcast transcripts, earnings calls, press conference recordings |
| [`websites-html`](https://github.com/tomcardoso/watchdog/blob/main/src/watchdog/skills/records/websites-html.md) | Any HTML file or downloaded website page — both its presentation content and its underlying markup (tracking IDs, hidden elements, outbound links) |

## Reading and pinning skills

### Your own skills

You can add your own skills as plain Markdown files, no code required. **Settings → Record skills** shows the folder they go in, with **Show in folder** to open it. A skill of your own overrides a built-in one of the same name, so you can adapt one to your beat by copying and editing it. Start from the [template](https://github.com/tomcardoso/watchdog/blob/main/src/watchdog/skills/records/_template.md), and lead with patterns that apply anywhere, treating specific jurisdictions as examples. To contribute a skill to the project, see the [authoring guide](https://github.com/tomcardoso/watchdog/blob/main/.claude/skills/authoring-record-skills/SKILL.md).

## What leaves your computer

Pre-processing (transcription included), search and the small local models never leave your computer and never cost anything; the only network use is downloading a model the first time it is needed. What goes to the AI provider you chose is the extracted text of each document during processing, with your investigation's brief, `context.md`, and during post-processing the facts, names and short quoted excerpts it works across, never the original file. That is why Watchdog must only be used on documents that are public or presumptively public.

---

**Where next:** [Models and cost](models-and-cost.md) for which model runs each stage and what it costs, or [Using Watchdog](using-watchdog.md) for the day-to-day work. The precise technical reference is [`ARCHITECTURE.md`](https://github.com/tomcardoso/watchdog/blob/main/ARCHITECTURE.md) in the project's repository, and an [illustrated walkthrough](https://claude.ai/code/artifact/d16050d6-3357-411c-9b88-26271a330435) follows one document from dropped file to result.
