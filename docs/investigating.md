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

Marking an item **Mark handled** removes it from the queue, from the Overview and from future briefings. It is not deleted. The **Handled** tab lists everything you have handled, newest first, and **Bring back** returns one to its queue. The keyboard works throughout: J and K move between items, H marks one handled, O opens its note, S keeps it open and moves on, X selects it (for handling several at once), and U undoes the last action.

### Contradictions

When a new document disagrees with something already in the investigation, the entity's note gets a contradiction callout with both sources cited, and the item appears on the **Contradictions** tab. A contradiction is often newsworthy in itself: two official records that disagree can be the story. Open the note to read both claims side by side, decide which source to trust (or note the conflict), then mark it handled. **Ask Claude** on an item opens a conversation already pointed at it.

If you spot a conflict that Watchdog missed, open the entity and choose **Record a contradiction…** from its **⋯** menu. You give a short label for the disputed fact, then each side's claim, source document and (optionally) page.

### Leads

At the end of every run, Watchdog sweeps the whole entity graph with plain code, no AI call, and lists what it finds on the **Leads** tab. It flags four things:

- **Named but never profiled.** An entity named as a relationship target (a company someone is director of, say) that has no documents of its own. A lead: go find records on it.
- **Mentioned often but unconnected.** An entity that recurs across several documents yet has no relationships at all. Why does this name keep coming up in isolation?
- **Unresolved contradictions.** Entities carrying contradiction flags, listed so they do not sit unreviewed.
- **Inferred facts to verify.** Entities carrying facts or roles the extractor flagged as inferred rather than read, or figures that could not be found on the cited page. Leads to verify, not findings.

The lists are read when you open the tab. **Run full lead sweep** rereads every entity note and refreshes them. The same sweep is on the Maintenance screen, and its written copy is saved under **Briefings** as a lead sweep.

### Possible duplicates

The **Duplicates** tab shows documents the pipeline judged to be near-copies of one already in the investigation, side by side. Watchdog never discards them. Compare the pair, then confirm they are the same record or that the difference matters. Marking a pair handled only hides it from the list and from reports; both documents stay, and the later one remains flagged on its own page.

### Document requests

Some documents refer to other documents you do not have yet: the transcript a hearing order cites, the regulation it enforces, an exhibit that was filed but never attached. Watchdog lists these on the **Requests** tab, apart from the open-ended leads, because each one names a specific thing known to exist. Each request carries the reason it matters, where it can plausibly be obtained, and a link back to the document that named it. The written list is `requests.md` in the investigation's folder, rewritten after every run.

Two filings rarely cite the same document in identical words. When a run adds a new request while others are open, Watchdog asks a model to compare the open requests with each other and merge any it judges to be the same real document. This is the one place where requests are read back into a model call, and only to compare them with each other. It is deliberately cautious, but it can be wrong. If a request you expected seems to be missing, check whether it was merged into a differently worded one still open.

When you have the document in hand, or decide not to pursue it, mark the request handled.

### The watch list

The **Watch list** tab is where you keep terms you want flagged whenever they appear in new documents, one per line: a name, a company, an address, a phrase. Matching is case-insensitive and whole-word. Wrap a line in slashes, like `/14\s+Quay Street/`, to use a regular expression (a pattern-matching syntax) instead. An empty list does nothing. Choose **Save watch list** after editing.

The scan runs automatically at the end of every run, over that run's new documents. Matches appear on the **Watch-list hits** tab with the document, the page and the surrounding words, and a link to the matching entity if there is one. The details are also written to `briefings/alerts-<date>.md`.

Because the automatic scan only sees new documents, a term added after documents are already in is never checked against them. **Check every document now** sweeps everything already added against the current list. No model is called.

### Handled items and the briefing files

Every item has a short resolution id, and handling an item records it. If you prefer to work in the written files, you can tick an item's checkbox in a briefing file (or `requests.md`) with any text editor. Then choose **Sync ticked checkboxes from briefings** on the **Handled** tab to import your ticks. Checkboxes shown in Briefings are display only; handle items in Review.

## Entities

**Entities** lists every person, organization, public body, place, asset and proceeding Watchdog found, filterable by type, with the number of documents each appears in. Search it by name or alias, and sort it by most documents, name, most recent update or most contradictions. Filters narrow it to entities with contradictions, entities with no summary written yet, and single-source entities.

Open an entity to see its summary, analysis, contradictions, timeline and relationships, and thumbnails of the documents it appears in. Every fact links to the page it came from. The **Notes** section is yours; Watchdog never writes to it, so anything you type there survives every run.

### Duplicate entities

Sometimes the same real-world person or company ends up extracted as two entities, most often because a name is spelled differently across documents. Watchdog merges the pairs it is confident about when it finishes a batch; the ones it leaves are worth checking by hand.

The **Single-source** filter is the best place to look, since a duplicate usually appears in only one document. A pair of near-duplicate documents on the Duplicates tab often produces two copies of the same entities as well. To fold one entity into the other, select the two on the Entities screen and choose **Merge…**, or open one and choose **Merge into another entity…** from its **⋯** menu.

The dialog shows both entities (name, type, document and relationship counts) before you commit, and asks you to tick that you understand the merge cannot be undone from the app. The duplicate's aliases, documents, relationships and timeline events all combine onto the survivor, and every relationship elsewhere that pointed at the duplicate follows. A snapshot is taken first.

When it finishes, the dialog offers **Rebuild search index**, which drops the merged entity's stale entries from Search. The merge keeps only one of the two written summaries, so when both entities had one, open the survivor and choose **Refresh summary from all sources** from its **⋯** menu. That asks Claude to rewrite its Summary and Timeline from every source.

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

## Ask Claude

**Ask Claude** is a conversation with Claude Code about the investigation. The documents it reads are treated as untrusted, since any of them could contain text written to steer Claude. So a conversation stays inside its own investigation, cannot read Watchdog's settings or keys, and asks you before doing anything the investigation's settings do not already allow.

Each conversation starts fresh, but Claude reads `hot.md` first: a current-state summary of the investigation, rewritten after every run. That is what lets you continue across many separate conversations without losing context. Past conversations are listed on the left and can be picked up again.

Type a question in plain language, or start from one of the shortcuts on the empty screen:

- `/watchdog-query` answers a question from the investigation, with a source for every claim. Substantive answers are filed to `queries/` with their citations, so your explorations accumulate instead of being re-derived. Trivial lookups are skipped.
- `/watchdog-surface` runs a connection analysis across the whole investigation: addresses shared by entities with no other relationship, people in unusual roles, entities mentioned across many unrelated documents, chronological anomalies and contradictions. Run it after each significant batch.
- `/watchdog-entity` refreshes an entity's summary and timeline from all its source documents.
- `/watchdog-wiki` creates or updates thread pages in `wiki/`. When a finding grows into a real angle (two or more entities tied together by two or more documents), it graduates to a thread page. Over a long investigation, `queries/` and `wiki/` become the record of what you have worked out.
- `/watchdog-health` checks integrity: orphaned notes, broken links, registry mismatches, open contradictions.
- `/watchdog-context` and **Seed investigation context** tell Watchdog what the story is. Claude reads the background files in `context/`, interviews you where they fall short, and writes `context.md`. An existing one is updated, not replaced.

A selector at the top of the screen chooses the Claude model for new conversations: Default (Claude Code's own setting), Sonnet, Opus or Haiku. The cost so far is shown beside it; on a subscription this is what it would cost at published rates, not what you are billed.

Ask Claude needs Claude Code signed in. If a question will not start, check **Settings → Models & keys**.

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

**Briefings** gathers everything Watchdog writes for you to read: the briefing from each run (**Ingest briefings**), **Lead sweeps**, **Watch-list alerts** and **Research memos**, plus the answers and thread pages Claude has saved. Three pinned pages sit at the top: **Current state** (`hot.md`), **Ingest history** (`log.md`) and **Investigation context** (`context.md`), which you can edit in place with **Edit**.

## Maintenance runs

**Activity → Maintenance** has a card for each step that **Add documents** runs for you, plus repairs. Reach for them to run one step at a time, to check an extraction before it reaches the investigation, or to fix something that stopped. Each card says what it does, and anything that sends text to a model shows the public-records warning first.

| Card | What it does |
|---|---|
| **Watch incoming for new files** | Reads files as they land in `incoming/`, starting with any already waiting. Nothing is sent to a model. |
| **Chew** | Converts every file in `incoming/` to text on your computer, applying OCR to pages that need it, and checks for duplicates. |
| **Dig** | Extracts facts, entities and dates from each document with a model, and stages the result. **Estimate** and **Compare all models** quote the cost first. |
| **Bark** | Finishes a batch: merges duplicate entities, flags contradictions, writes entity summaries, reconciles the timeline and writes the briefing. Safe to run again if it stops partway. |
| **Requeue failed documents** | Moves documents that failed extraction back into the queue so a later Dig tries them again. |
| **Lead sweep** | Runs the full lead sweep, with no model call. |
| **Rebuild the timeline** | Regenerates the written timeline from the underlying records. |
| **Rebuild the search index** | Rebuilds the search indexes from what is already on disk. Run it after changing the embedding model in Settings, or after merging entities. |
| **Usage** | Opens token, cost and timing figures for each run. |
| **Export the graph** | Writes the entities and relationships for network-analysis tools such as Neo4j or Gephi, as CSV files or a Cypher script. |
| **Release a stuck lock** | An interrupted run can leave a lock that stops the next one starting. This releases a stale one; a recent-looking lock is left alone unless you force it. |
| **Refresh Claude setup** | Updates this investigation's shortcuts, Claude instructions and Claude Code settings after you update Watchdog. |

Running Dig and Bark separately, rather than back to back, is also how you compare finishing models against the same extraction. The **Ingest history** tab beside Maintenance shows what each run added.

## Managing investigations

Each investigation is a separate folder; create as many as you need. **All investigations** (the first item when you click the investigation's name at the top of the sidebar) lists them with their document and entity counts and anything that needs attention. The **⋯** menu on each covers:

- **Rename…**, **Edit description…** and **Move to another folder…**. Renaming and moving are blocked while a run is in progress.
- **Archive** and **Unarchive.** Archiving hides an investigation from the list and from cross-investigation search without deleting anything.
- **Show in folder** and **Open in Obsidian**, for the investigation's files on disk.
- **Ingest history**, which shows what each run added.
- **Remove from Watchdog…**, which takes it off the list but leaves its files on disk.
- **Remove and delete files…**, which permanently deletes the folder and its usage records, and asks you to type the investigation's name first. Use Archive instead if you might want it later.

To bring an existing investigation folder back, choose **Add existing folder…** on the All investigations screen. (**File → Open Investigation…** takes you to that list.) **Settings → Check vaults** looks for investigations whose folder has moved or been deleted, and suggests a fix for each.

## Trusting what you read

Every extracted fact is either stated (read directly from a document) or inferred, which is marked *(inferred)* in the notes and is a lead to verify, not a finding. When a new document contradicts something already in the investigation, the entity note gets a contradiction callout with both sources cited. The [vault guide](vault.md#stated-vs-inferred) has the full explanation.

## Where next

[The desktop app](app.md) describes every screen. [The vault guide](vault.md) explains what Watchdog builds on disk and how to read it. [Settings](configuration.md) covers models, keys and cost. If you still use the older command line, [its reference](commands.md) remains available.
