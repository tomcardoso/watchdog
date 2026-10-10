# Investigating

This guide covers the day-to-day work of an investigation once documents are in: checking what Watchdog flagged, asking questions, searching, chasing leads, researching on the web, and keeping several investigations organized. Read [Getting started](getting-started.md) first if you have not added your first documents yet, and [The desktop app](app.md) for a tour of each screen.

Everything here happens in the Watchdog app. The sidebar groups the screens: **Overview**, **Documents**, **Entities**, **Timeline** and **Network** under Investigation, and **Review**, **Search**, **Ask Claude**, **Web research** and **Briefings** under Work. **Activity** and **Settings** sit below them.

## The rhythm of an investigation

After the first batch, the typical loop is:

1. **Add documents.** Choose **Add documents** (or drop files onto the window). Watchdog reads them on your computer, asks you to confirm the public-records warning, then extracts and cross-checks them.
2. **Read the briefing.** The Overview shows the latest one. Pay particular attention to connections with entities already in the investigation.
3. **Work through Review.** The sidebar shows how many items are waiting on you. See [Review](#review).
4. **Ask questions.** In **Ask Claude**, run `/watchdog-surface` after a substantial batch to look for connections you did not think to ask about.

Watchdog does not need to be doing anything else while you work. If you would rather read documents now and extract them later, **Activity → Maintenance** runs each stage on its own; see [Maintenance runs](#maintenance-runs).

## Review

**Review** is where everything Watchdog flagged for a person waits. It has one tab for each kind of item, with a count on each, plus a tab for what you have already handled and one for your watch list. Open items move through the same few steps: read the item, open its note if you need more detail, then mark it handled, or keep it open and move on.

Marking an item **Mark handled** removes it from the queue, from the Overview and from future briefings. It is not deleted. **Handled**, at the top right of Review, lists everything you have handled, newest first, and **Bring back** returns one to its queue. The keyboard works throughout: J and K move between items, H marks one handled, O opens its note, S keeps it open and moves on, X selects it (for handling several at once), and U undoes the last action.

### Contradictions

When a new document disagrees with something already in the investigation, the entity's note gets a contradiction callout with both sources cited, and the item appears on the **Contradictions** tab. A contradiction is often newsworthy in itself: two official records that disagree can be the story. Open the note to read both claims side by side, decide which source to trust (or note the conflict), then mark it handled. **Ask Claude** on an item opens a conversation already pointed at it.

If you spot a conflict that Watchdog missed, open the entity and choose **Record a contradiction…** from its **⋯** menu. You give a short label for the disputed fact, then each side's claim, source document and (optionally) page.

<a id="re-checking-contradictions"></a>

#### Re-checking contradictions

Each time documents are added, their facts are compared with the facts already recorded. Facts already recorded are not compared with each other again, so if two earlier documents disagree and that run missed it, nothing looks again on its own. A conflict within a single document, between two facts about an entity named only there, is not looked for when documents are added either. **Re-check** beside an entity's Contradictions heading (also **Re-check contradictions…** in its **⋯** menu) does look: it sends every recorded fact about the entity to the AI model you chose for post-processing in Settings and asks it to find facts that cannot both be true. It works on an entity named in only one document too, as long as it has two or more facts. To do the same for every entity, use **Activity → Maintenance → Re-check contradictions**. An entity with so many facts that comparing them all would take more than 45 model calls cannot be re-checked: its **Re-check** button is turned off, and a note under the Contradictions heading says why.

Before anything is sent you see how many facts and model calls it takes, about how many tokens, and what that should cost at the model's list price (on a Claude subscription there is no separate charge). Nothing runs until you confirm. Every pair of facts is shown to the model at least once; a large entity is split across several calls to do that. An entity so large that this would take more than 45 calls is not re-checked, and the confirmation says so; its new documents are still checked as they arrive.

What it finds is filed like any other contradiction: on the entity's page with both sources linked to their facts, and on the Contradictions tab in Review. Facts you have marked Disputed are compared too, labelled. A conflict already recorded is not added again, and neither is one you marked handled, however the model words it this time. The re-check waits if documents are being added, can be stopped from Activity (what the finished calls found is kept), and appears in the investigation's version history as "Contradictions re-checked".

### Leads

At the end of every run, Watchdog sweeps the whole entity graph with plain code, no AI call, and lists what it finds on the **Leads** tab. It flags four things:

- **Named but never profiled.** An entity named as a relationship target (a company someone is director of, say) that has no documents of its own. A lead: go find records on it.
- **Mentioned often but unconnected.** An entity that recurs across several documents yet has no relationships at all. Why does this name keep coming up in isolation?
- **Unresolved contradictions.** Entities carrying contradiction flags, listed so they do not sit unreviewed.
- **Inferred facts to verify.** Entities carrying facts or roles the extractor flagged as inferred rather than read, or figures that could not be found on the cited page. Leads to verify, not findings.

The lists are read when you open the tab. **Run full lead sweep** rereads every entity note and refreshes them. The same sweep is on the Maintenance screen, and its written copy is saved under **Briefings** as a lead sweep.

### Possible duplicates

The **Duplicates** tab shows documents the pipeline judged to be near-copies of one already in the investigation, side by side. Watchdog never discards them. Compare the pair, then confirm they are the same record or that the difference matters. Marking a pair handled only hides it from the list and from reports; both documents stay, and the later one remains flagged on its own page.

<a id="merges"></a>

### Merges

Two documents often name the same person or company, and Watchdog has to decide whether they mean the same one. Two different people can share a name, so it never decides that on a name alone. How sure it is decides what happens (the methodology page explains [how Watchdog decides two names are the same](methodology.md#same-entity)):

- **High confidence: merged, and recorded.** The same registration, licence, court file or parcel number on both, or the same full name with the same role, employer or street address, or the same company or place name when it is specific enough to pick out one ("Northgate Civil Works Ltd.", but not "the City").
- **Medium confidence: the AI model decides, and the decision is recorded.** The same full name with nothing else in common, or a short or general name. The model reads both records' facts side by side and merges them only if it is confident.
- **Low confidence: never merged automatically.** One name is an initialled or shortened form of the other ("J. Smith" and "John Smith"), or both are the same short name ("Mr. Pike").

The **Merges** tab has two parts. At the top is the queue of **possible same entities**: low-confidence pairs, and same-name pairs the model was not confident about. Each card shows both records side by side, with their type, document count, roles and the facts recorded about each, every fact linked to its page. Choose **Merge…** if they are one, which opens the usual merge dialog with both filled in. Choose **Not the same** if they are not: the pair leaves the queue, and Watchdog will never merge those two records automatically, by rule or by model. **Not the same** counts as handling the item, so it appears under **Handled** and **Bring back** undoes it.

Below the queue is **Recent merges**: every merge, newest first, whoever made it. Each row says which records were joined, how confident the decision was, and who decided: Watchdog's rules, the AI model (by name), or the reporter who chose **Merge**. Open a row for the reason, the matching number or shared role if there was one, the facts that came with the merged record and the documents it came from. Recognising the same specific company or place name in another document is recorded too, but those rows are hidden until you turn on **Include exact-name recognitions**, so the list stays on the merges worth a look.

If a merge is wrong, open its row and choose **Undo merge**. Watchdog gives the facts, documents and relationships that came with the merged record back to a record of its own, rewrites both entities' notes, and marks the pair **Not the same** so it is never merged automatically again. It calls no AI model. Three things stay with the surviving entity, and the confirmation says so: contradictions recorded while the two were joined, your own **Notes** (including any written after the merge; move what belongs to the other record by hand), and its summary, which the next run that names it rewrites. A merge decided again for several documents is undone for all of them at once.

Some merges cannot be split exactly, and for those the button stays unavailable with the reason beside it: a merge made by a version of Watchdog older than this one, which did not record which facts moved; two names joined inside one long document that was read in sections; a surviving entity that was itself merged into another later (undo that one first); and a document that has been processed again since the merge. The same record is kept in `merges.md` in the investigation's folder (see [the vault layout](vault.md)), which also notes each merge that was undone.

### Document requests

Some documents refer to other documents you do not have yet: the transcript a hearing order cites, the regulation it enforces, an exhibit that was filed but never attached. Watchdog lists these on the **Requests** tab, apart from the open-ended leads, because each one names a specific thing known to exist. Each request carries the reason it matters, where it can plausibly be obtained, and a link back to the document that named it. The written list is `requests.md` in the investigation's folder, rewritten after every run.

Two filings rarely cite the same document in identical words. When a run adds a new request while others are open, Watchdog asks a model to compare the open requests with each other and merge any it judges to be the same real document. This is the one place where requests are read back into a model call, and only to compare them with each other. It is deliberately cautious, but it can be wrong. If a request you expected seems to be missing, check whether it was merged into a differently worded one still open.

When you have the document in hand, or decide not to pursue it, mark the request handled.

<a id="the-watchlist"></a>

### The watch list

**Watch list**, at the top right of Review, is where you keep terms you want flagged whenever they appear in new documents, one per line: a name, a company, an address, a phrase. Matching is case-insensitive and whole-word. Wrap a line in slashes, like `/14\s+Quay Street/`, to use a regular expression (a pattern-matching syntax) instead. An empty list does nothing. Choose **Save watch list** after editing.

The scan runs automatically at the end of every run, over that run's new documents. Matches appear on the **Watch-list hits** tab with the document, the page and the surrounding words, and a link to the matching entity if there is one. The details are also written to `briefings/alerts-<date>.md`.

Because the automatic scan only sees new documents, a term added after documents are already in is never checked against them. **Check every document now** sweeps everything already added against the current list. No model is called.

<a id="resolving-items"></a>

### Handled items and the briefing files

Every item has a short resolution id, and handling an item records it. If you prefer to work in the written files, you can tick an item's checkbox in a briefing file (or `requests.md`) with any text editor. Then choose **Sync ticked checkboxes from briefings** under **Handled** to import your ticks. Checkboxes shown in Briefings are display only; handle items in Review.

## Checking facts

Every fact Watchdog extracts is a claim to check, not a finding. The app gives you what you need to check it, and a place to record that you did.

### Source passages

Open a document and look at its **Facts** tab. Under each fact is the text from the document that supports it:

- **A quotation** is shown when the model quoted the source and Watchdog confirmed the wording is there.
- **A matched passage** is labelled "Matched passage" with its page. The model did not quote it. Watchdog found it by comparing the fact's names, figures and dates with the sentences on the cited page, with ordinary code and no AI model (see [how a passage is found](methodology.md#how-a-passage-is-found)). It is the sentence most likely to be the source, not proof that it supports the claim. If it is from a different page than the fact cites, the label says so.
- **"No matching passage found"** means nothing on the cited page shares enough of the fact's names, figures and dates. The fact may combine several passages, rest on reasoning, or cite the wrong page. Read the page yourself.

**find on page** beside a quotation or matched passage scrolls the original to it. Documents added before this feature existed show no passage; they get one when they are next processed.

### Marking facts

Each fact has three buttons. Click one to record your own check, and click it again to clear it.

- **Verified** — you read the source and the fact holds.
- **Disputed** — the source, or another record, does not support it.
- **Can't verify** — you could not confirm or rule it out.

Watchdog records your name and the time with each mark. Once a fact is marked, **Add note** lets you write what you checked it against or why it is in doubt (up to 2,000 characters; Enter saves, Shift+Enter starts a new line, Esc closes the box). Set the name under **Settings → Verification**; until you do, it records "Journalist". See [Your name](configuration.md#verification). Marking changes nothing in the fact itself, and it never calls an AI model.

The bar above the facts filters them: **All**, **Not checked**, **No passage** (shown only when some facts have none), **Inferred** and **Figures**, with a count of how many you have checked. Select a fact (click it or tab to it), then use the keyboard:

| Key | Does |
|---|---|
| J or ↓ | Next fact |
| K or ↑ | Previous fact |
| V, D, C | Mark Verified, Disputed or Can't verify; press it again to clear |
| N | Edit the note (when the fact is marked) |

### The Verification tab

**Review → Verification** lists every fact in the investigation with the same buttons, a progress bar ("12 of 340 facts verified") and a filter: **Not checked**, **Disputed**, **Can't verify**, **Verified**, **No passage**, and **Changed since marked** when there are any. It opens on Disputed when something is disputed, otherwise on Not checked. A fact you mark Disputed is never hidden: wherever it appears (the document's facts, the entity's facts, the Timeline, Review, citations, the notes in the investigation folder and the account Claude starts each conversation with) it carries the label **disputed**. A fact you have just marked stays in the list until you change the filter, so you can undo it. After V, D or C the focus moves on to the next fact. J and K move, N edits the note, and O (or Enter) opens the document at that fact's page. The Overview shows how many facts are verified, and lists disputed facts among what is waiting on you.

If a document is processed again and a fact's wording or page changes, your mark stays with the old wording and appears under **Changed since marked**. It is never moved to the new wording, because you did not check those words. Check the new fact and mark it again.

### The written record

Every change rewrites `verification.md` at the top of the investigation: a list of marked facts by status and document, with who checked them, when and your notes. See [the vault guide](vault.md#verificationmd). `facts.csv`, from the older command line's export, lists every fact with its passage and mark; see [commands](commands.md#watchdog-export).

## Entities

**Entities** lists every person, organization, public body, place, asset and proceeding Watchdog found, filterable by type, with the number of documents each appears in. Search it by name or alias, and sort it by most documents, name, most recent update or most contradictions. Filters narrow it to entities with contradictions, entities with no summary written yet, and single-source entities.

Open an entity to see everything the documents say about it.

- **Facts** lists every fact about the entity from every document, in the order things happened (or **By document**), each with its document and page, any warning, and the same **Verified**, **Disputed** and **Can't verify** buttons as in the document reader, with the same keys. **Show the matched passage** (or **Show the quotation**) opens the sentence on the page the fact rests on. A fact you mark Disputed stays in the list with a **disputed** label; from the next run on, the summary is told you dispute it, so it never presents it as established, and the contradiction check still compares it, labelled. Anywhere a summary, briefing or saved answer cites it, the citation is labelled **disputed**. The filters show what is not checked, disputed, inferred, or carries a figure warning.
- **Summary** is written by an AI model from the entity's facts once the entity appears in two or more documents. Where a sentence rests on a fact, it ends in a citation such as **p. 4**; see [Citations](#citations). Treat it as a reading aid, and the facts as the record.
- **Contradictions**, **Timeline** and **Relationships** follow, with thumbnails of the documents it appears in.

The **Notes** section is yours; Watchdog never writes to it, so anything you type there survives every run.

### Duplicate entities

Sometimes the same real-world person or company ends up extracted as two entities, most often because a name is spelled differently across documents, or because Watchdog was not sure enough to merge two records that share a name. The pairs it was unsure about wait on the [Merges tab](#merges) in Review; others are worth checking by hand.

The **Single-source** filter is the best place to look, since a duplicate usually appears in only one document. A pair of near-duplicate documents on the Duplicates tab often produces two copies of the same entities as well. To fold one entity into the other, select the two on the Entities screen and choose **Merge…**, or open one and choose **Merge into another entity…** from its **⋯** menu.

The dialog shows both entities (name, type, document and relationship counts) before you commit, and asks you to tick that you understand the two become one record. A merge you regret can be split back from **Review → Merges** (see [Merges](#merges)). The duplicate's aliases, documents, relationships and timeline events all combine onto the survivor, and every relationship elsewhere that pointed at the duplicate follows. A snapshot is taken first, and the merge is recorded under your name in the merge log (Review, Merges).

When it finishes, the dialog offers **Rebuild search index**, which drops the merged entity's stale entries from Search. The survivor's facts include both records' facts at once. The merge keeps only one of the two summaries, so when both entities had one, open the survivor and choose **Refresh summary from all sources** from its **⋯** menu, or wait for the next run that names it.

## Timeline and Network

**Timeline** lays out every dated event by year and month, each linked to the page it came from. Filter it by entity, entity type, year range or a word in the event. A bar chart at the top shows how many events fall in each year; click a bar to jump there. The **⋯** menu has **Rebuild timeline.md…**, which regenerates the written timeline file from the underlying records. Nothing is lost if the file is deleted or edited by mistake, because it is generated output.

**Network** draws the entities as a graph. Each dot is an entity, sized by how many documents name it and coloured by type, and each line is a relationship. Hover over a dot to see its connections, click it to see details, and double-click to open it. A slider sets the minimum number of documents an entity must appear in to be drawn, and **Show unconnected entities** adds the ones with no relationships. Only relationships stated in the documents are drawn.

## Search

**Search** covers everything in the investigation without involving Claude. Results come in three separate sections:

- **Exact matches.** Every literal occurrence of your words across source documents and notes, each with a page link back to the source.
- **Source passages.** What the documents say, ranked by how close they are in meaning to your query.
- **Notes.** What the investigation has concluded, drawn from entity notes and saved answers.

The ranking is a hybrid: passages are scored both by meaning and by exact terms, then re-ranked on your computer for precision. Searching for "conflict of interest" surfaces passages about recusals or related-party dealings even when the phrase never appears, while an exact token such as a case number or a dollar figure still finds its passage.

On a large investigation, the first search after new documents are added takes longer than the rest, because Watchdog rebuilds a saved copy of the index then. If Search reports that the index is empty, it offers a button to rebuild it.

**Search syntax** (under the search box) explains how to steer results. Lead a phrase with `-` to push away from it, or `+` to pull towards another idea. The whole phrase up to the next sign is one term; no quotes are needed. Put quotes around words that must appear together for an exact phrase match.

```
shell company -real estate
consulting fee +offshore -salary
```

**Options** holds the results per section, **Hide weak matches** (a score threshold), **Re-rank passages** and **Show full passages**.

Two other modes sit at the top of the screen:

- **Every investigation** answers "have I seen this name in any of my investigations?" It checks every registered, non-archived investigation and groups hits by investigation. Only entity lookups and exact matches run in this mode, because meaning-based search does not scale across investigations, so a name variant with no recorded alias and no literal occurrence will not surface. Investigations whose folder is missing are skipped and listed rather than failing the search.
- **Check a list of names** checks a whole list against the investigation (a board roster, a sanctions list, a list of donors). Type or load one name per line and you get a report of what each name hit. A name that could not be checked is shown as "Not checked", never as "No hits". It can run across every investigation at once.

### Citations

Summaries, briefings and the pages Claude saves in Ask Claude cite the facts they rest on. A citation is a small marker such as **p. 4** at the end of a sentence. Hover over it to see the fact, its document and page, and whether you have checked it. Click it to open the document at that page, with the fact selected in the Facts list and its passage found on the page.

A citation of a fact you marked Disputed reads **p. 4 · disputed**; the fact is never hidden. A citation Watchdog cannot match to a stored fact reads **source not found** and is not a link. That happens when a document is processed again and its facts are reworded, when a merge is undone, or when Claude wrote the citation wrong. Watchdog never rewrites the page; **Activity → Maintenance → Check citations** lists every such case.

AI-written text can also contain sentences with no citation: a model connecting facts, summing up, or proposing what to look at next. That is expected, and it is not flagged. Anything you plan to publish should rest on cited facts you have checked.

## Ask Claude

**Ask Claude** is a conversation with Claude Code about the investigation. The documents it reads are treated as untrusted, since any of them could contain text written to steer Claude. So a conversation stays inside its own investigation, cannot read Watchdog's settings or keys, and asks you before doing anything the investigation's settings do not already allow.

Each conversation starts fresh, but Claude is first given a short account of where the whole investigation stands: the questions in your investigation context, how many documents, entities and facts there are and how many facts you have checked, the entities named most often, what is waiting on you (contradictions, leads, documents to request, possible same entities, facts you dispute) and the latest briefings. Watchdog builds it from the investigation's records each time a conversation starts, without an AI model, so it is always current and covers every document, not only the latest run. That is what lets you continue across many separate conversations without losing context. You can read the same account under **Briefings → Current state**, and the exact text Claude receives, which also tells Claude how to cite facts, under **What Claude is given** on the same page. Past conversations are listed on the left and can be picked up again.

Type a question in plain language, or start from one of the shortcuts on the empty screen:

- `/watchdog-query` answers a question from the investigation, with a source for every claim. Substantive answers are filed to `queries/` with their citations, so your explorations accumulate instead of being re-derived. Trivial lookups are skipped.
- `/watchdog-surface` runs a connection analysis across the whole investigation: addresses shared by entities with no other relationship, people in unusual roles, entities mentioned across many unrelated documents, chronological anomalies and contradictions. Run it after each significant batch.
- `/watchdog-entity` refreshes an entity's summary and timeline from all its source documents.
- `/watchdog-wiki` creates or updates thread pages in `wiki/`. When a finding grows into a real angle (two or more entities tied together by two or more documents), it graduates to a thread page. Over a long investigation, `queries/` and `wiki/` become the record of what you have worked out.
- `/watchdog-health` checks integrity: orphaned notes, broken links, registry mismatches, open contradictions.
- `/watchdog-context` and **Seed investigation context** tell Watchdog what you want to find out. Claude reads the background files in `context/`, interviews you where they fall short, and writes `context.md` as open questions rather than conclusions. An existing one is updated, not replaced. The brief is sent to the AI model with every document you add; see [Seed your context](getting-started.md#seed-your-context-optional-but-recommended) for what to leave out of it and how to phrase it.

A selector at the top of the screen chooses the Claude model for new conversations: Default (Claude's own default model), Sonnet, Opus or Haiku. The cost so far is shown beside it; on a subscription this is what it would cost at published rates, not what you are billed.

Ask Claude needs Claude signed in. If a question will not start, check **Settings → Models & keys**.

## Web research

When the investigation raises a question its own documents cannot answer (a director you cannot profile, a contradiction you cannot resolve, a company you need background on), **Web research** can look it up. Enter a research question, or leave the box blank to let Claude propose one from the investigation's entities, leads and gaps, then choose **Start research**.

Claude proposes a research mission, confirms how wide to cast the net (quick, standard or deep), then researches in rounds, checking in with you between each. It writes a research memo to `briefings/` when it is done, which you can read under **Briefings**.

Web research **never writes notes to the investigation**. Claude queues every source it decides to keep, with its address, a reliability tag and why it matters. A strip at the top shows how many sources are queued, and nothing is downloaded until you choose the **Download** button, which reads "Download 5 sources into incoming" for five queued sources. Each source is checked and saved in `incoming/`, so findings go through the same read-and-extract steps as documents you obtained yourself: deduplicated, entity-extracted and cited. A scraped blog post is never confused with a primary document. After the download, choose **Add documents** to fold the findings in, then ask your questions in a fresh conversation.

A few things worth knowing:

- **Interrupted sessions lose nothing.** Queued sources are held in the investigation's internal state. If a session ends before you download, the Web research screen reminds you that sources from an earlier session are queued, with a **Download now** button. Choose **End session** to leave; queued sources stay queued.
- **Already-captured sources are skipped.** Across repeated research, Claude skips sources the investigation already holds, unless you ask it to re-check one.
- **Page snapshots.** Web pages are captured as full snapshots (images, styles, client-rendered content) when the optional capture browser is installed, and as sanitized text otherwise. See the [installation guide](install.md).
- **Wayback archiving.** Optionally, each downloaded source can also be saved to the Internet Archive's Wayback Machine, with the snapshot address recorded in the source's provenance record, so there is a citable copy if the original changes or disappears. It is off by default and never blocks a download. The keys are under **Web archiving** in [Settings](configuration.md#web-archiving).

### Already have the links?

If you already have a batch of links from a spreadsheet, a colleague or your own browsing, you do not need a research session. Choose **File → Fetch Links…**, paste one address per line (or choose **Use a links file…** for a text file with one per line), and Watchdog downloads them. Each is validated, size-capped and saved into `incoming/` with a provenance record, the same as research sources. Then choose **Add documents** as usual.

For clipping pages as you browse, the [Obsidian Web Clipper](https://obsidian.md/clipper) browser extension can save pages straight into a folder. Point it at your investigation's folder and set the destination folder to `incoming`.

## Briefings

**Briefings** gathers everything Watchdog writes for you to read: the briefing from each run (**Briefings**), **Lead sweeps**, **Watch-list alerts** and **Research memos**, plus the answers and thread pages Claude has saved. Three pinned pages sit at the top: **Current state** (where the whole investigation stands, built from its records when you open it; **What Claude is given** shows the same account as Claude receives it at the start of each conversation), **Processing history** (`log.md`) and **Investigation context** (`context.md`), which you can edit in place with **Edit**.

## Maintenance runs

**Activity → Maintenance** has a card for each step that **Add documents** runs for you, plus repairs. Reach for them to run one step at a time, to check an extraction before it reaches the investigation, or to fix something that stopped. Each card says what it does, and anything that sends text to a model shows the public-records warning first.

| Card | What it does |
|---|---|
| **Watch incoming for new files** | Reads files as they land in `incoming/`, starting with any already waiting. Nothing is sent to a model. |
| **Pre-processing** | Converts every file in `incoming/` to text on your computer, applying OCR to pages that need it, and checks for duplicates. |
| **Processing** | Extracts facts, entities and dates from each document with a model, and stages the result. **Estimate** and **Compare all models** quote the cost first. |
| **Post-processing** | Finishes a batch: merges duplicate entities, flags contradictions, writes entity summaries, reconciles the timeline and writes the briefing. Safe to run again if it stops partway. |
| **Requeue failed documents** | Moves documents that failed extraction back into the queue so a later processing run tries them again. |
| **Lead sweep** | Runs the full lead sweep, with no model call. |
| **Check citations** | Checks that every fact cited in saved answers, threads and briefings still exists, and lists the citations of facts you marked Disputed. Changes nothing and calls no model. |
| **Rebuild the timeline** | Regenerates the written timeline from the underlying records. |
| **Rebuild the search index** | Rebuilds the search indexes from what is already on disk. Run it after changing the embedding model in Settings, or after merging entities. |
| **Usage** | Opens token, cost and timing figures for each run. |
| **Export the graph** | Writes the entities and relationships for network-analysis tools such as Neo4j or Gephi, as CSV files or a Cypher script. |
| **Release a stuck lock** | An interrupted run can leave a lock that stops the next one starting. This releases a stale one; a recent-looking lock is left alone unless you force it. |
| **Refresh Claude setup** | Updates this investigation's shortcuts, Claude instructions and Claude settings after you update Watchdog. |

Running processing and post-processing separately, rather than back to back, is also how you compare finishing models against the same extraction. The **Processing history** tab beside Maintenance shows what each run added.

## Managing investigations

Each investigation is a separate folder; create as many as you need. **All investigations** (the first item when you click the investigation's name at the top of the sidebar) lists them with their document and entity counts and anything that needs attention. The **⋯** menu on each covers:

- **Rename…**, **Edit description…** and **Move to another folder…**. Renaming and moving are blocked while a run is in progress.
- **Archive** and **Unarchive.** Archiving hides an investigation from the list and from cross-investigation search without deleting anything.
- **Show in folder** and **Open in Obsidian**, for the investigation's files on disk.
- **Processing history**, which shows what each run added.
- **Remove from Watchdog…**, which takes it off the list but leaves its files on disk.
- **Remove and delete files…**, which permanently deletes the folder and its usage records, and asks you to type the investigation's name first. Use Archive instead if you might want it later.

To bring an existing investigation folder back, choose **Add existing folder…** on the All investigations screen. (**File → Open Investigation…** takes you to that list.) **Settings → Check vaults** looks for investigations whose folder has moved or been deleted, and suggests a fix for each.

## Trusting what you read

Where the model knows a fact is its own reasoning rather than something a document says, it marks it *(inferred)*: a lead to verify, not a finding. The mark is a hint from the model, not a check, and an unmarked fact is not guaranteed to be stated in the document. [Checking facts](#checking-facts) shows how to read each fact's source passage and record your own check, which is how to be sure. When a new document contradicts something already in the investigation, the entity note gets a contradiction callout with both sources cited. The [vault guide](vault.md#stated-vs-inferred) has the full explanation.

## Where next

[The desktop app](app.md) describes every screen. [The vault guide](vault.md) explains what Watchdog builds on disk and how to read it. [Settings](configuration.md) covers models, keys and cost. If you still use the older command line, [its reference](commands.md) remains available.
