# Investigating

This guide covers the day-to-day work of an investigation once documents are in — asking questions, searching, chasing leads, researching on the web, and keeping several investigations organized. Read [getting started](getting-started.md) first if you have not run your first ingest yet.

## How a session starts

Every investigation question runs inside a Claude Code session opened in the vault. At the start of each session, Claude reads `hot.md` automatically — a current-state summary of the investigation, rewritten after every ingest. That is what lets you continue an investigation across many separate sessions without losing context: Claude arrives already oriented, without re-reading the entire vault.

## Asking questions

From inside a Claude Code session with the vault open:

```
/watchdog-query Who are the directors of Shell Co Ltd?
/watchdog-query Which companies share the address 123 Main St?
/watchdog-query What happened in 2019 involving Alice Smith?
```

Claude answers using only the documents and entities in your vault, and cites the source for every claim.

Substantive answers do not vanish into the chat. Anything that synthesizes across documents or surfaces a connection is filed to `queries/<slug>.md` with its citations preserved, so your explorations accumulate instead of being re-derived each session. Trivial one-off lookups are skipped.

When a finding grows into a real angle — two or more entities tied together by two or more documents — it graduates to a thread page in `wiki/` via `/watchdog-wiki`. Over a long investigation, `queries/` and `wiki/` become the compounding record of what you have worked out.

## Searching from the terminal

You can search the whole document set without opening Claude Code:

```bash
watchdog search shell-company-investigation "offshore account transfers"
```

Omit the investigation name when you are inside the vault directory. Results come in three sections:

- **Exact matches** — every literal occurrence of the term or phrase across source documents and notes, each with a page link back to the source
- **Source passages** — what the documents say, ranked by relevance to your query
- **Notes** — what the investigation has concluded, drawn from entity notes and saved answers

The ranking is a hybrid: passages are scored both by *meaning* and by *exact terms*, then re-ranked locally on your machine for precision. In practice, that means searching for `"conflict of interest"` surfaces passages about recusals or related-party dealings even when the phrase never appears — while an exact token like a case number or a dollar figure still lands its passage.

You can steer results with `+` and `-` phrases — lead a phrase with `-` to push away from it, `+` to pull toward another idea. The whole phrase up to the next sign is one term; no quotes needed:

```bash
watchdog search shell-company-investigation "shell company -real estate"
watchdog search shell-company-investigation "consulting fee +offshore -salary"
```

Wrap a phrase in quotes (`"jane doe"`) for an exact phrase match in the exact-matches section. Add `--threshold 0.5` to hide weak semantic matches, or `--full` to print complete passages instead of snippets.

Two special modes are worth knowing:

- **Checking a list.** To check a whole list of names or terms against the vault — a board roster, a sanctions list, a list of donors — put one term per line in a text file and pass `--batch`:

  ```bash
  watchdog search shell-company-investigation --batch names-to-check.txt
  ```

  You get a report of what each term hit, instead of a ranking for a single query.

- **Checking every investigation.** If you work across several investigations, `--everywhere` answers "have I seen this name in any of my vaults?" Drop the project name and it checks every registered, non-archived investigation, reporting hits grouped by investigation:

  ```bash
  watchdog search --everywhere "acme holdings"
  ```

  It combines with `--batch` to check a term list across every vault. Two limits: only entity lookups and exact matches run in this mode (meaning-based search does not scale across vaults), so a name variant with no recorded alias and no literal occurrence will not surface. Investigations with a broken or missing vault path are skipped rather than failing the scan.

The remaining flags and the full query syntax are in the [command reference](commands.md).

## Finding connections

```
/watchdog-surface
```

This runs a full connection analysis across the entire vault. Claude looks for:

- Addresses shared by entities with no other apparent relationship
- People appearing in unusual roles — director of one company, beneficiary of another
- Entities mentioned across many unrelated documents
- Chronological anomalies in timelines
- Relationships that were flagged as contradictions

Run it after each significant batch of ingest. The connections it surfaces are often the leads that require the most follow-up.

## Leads

At the end of every ingest, Watchdog runs a deterministic sweep over the whole entity graph — plain code, no AI call — and writes what it finds to `briefings/leads-<date>.md`, printing a one-line count in the terminal. It flags four things:

- **Named but never profiled** — an entity named as a relationship target (a company someone is director of, say) that has no documents of its own. A lead: go find records on it.
- **Mentioned often but unconnected** — an entity that recurs across several documents yet has no relationships at all. Why does this name keep coming up in isolation?
- **Unresolved contradictions** — entities carrying contradiction flags recorded at ingest, listed so they do not sit unreviewed.
- **Inferred facts to verify** — entities carrying facts or roles the extractor flagged as inferred rather than read. Leads to verify, not findings.

Re-run the sweep any time:

```bash
watchdog leads
```

A bare `watchdog` with nothing pending also nudges you when leads are open.

## Document requests

Some documents refer to other documents you don't have yet — the transcript a hearing order cites, the regulation it enforces, an exhibit that was filed but never attached. Watchdog surfaces these as document requests: a concrete artifact to go and get, filed apart from the open-ended leads described above because it names a specific known-to-exist thing rather than a thread to chase.

Requests are written to `requests.md` in the vault root, grouped by document type, each carrying the reason it matters, where it can plausibly be obtained, and a link back to the document (or documents) that named it — if two different documents refer to the same artifact in the same words, that's one entry with both links, not two to resolve separately. The file is regenerated after every ingest, so it always reflects what is still outstanding — a routine document that names nothing worth chasing simply adds nothing to it.

Two filings rarely cite the same document in identical words, though — a court order and an affidavit both naming "the Monitor's Pre-Filing Report" and "the Pre-Filing Report of Ernst & Young Inc." are the same thing to go and get, described differently. When an ingest adds a new request and other requests are already open, Watchdog runs one extra check: a model reviews the currently open requests and folds any it judges to be the same real document into one entry. This is the one exception to requests never being read back into a later model call — it only ever compares requests against each other to decide what is duplicate, never reads them as context for anything else. It is deliberately cautious about it (a genuinely different document sharing a date, a person's name, or a document type is kept separate, not merged), but it can occasionally be wrong; if a request you were expecting to see seems to be missing, check whether it was folded into a differently-worded entry still open in `requests.md`.

Resolve a request the same way as a lead, once you have the document in hand:

```bash
watchdog resolve --sync
```

## The watchlist

`watchlist.md` in the vault root is a list of terms you want flagged whenever they appear in new documents — one per line: a name, a company, an address, a phrase. Matching is case-insensitive and whole-word; wrap a line in `/.../` to use a regular expression (a pattern-matching syntax) instead. An empty watchlist does nothing.

The scan runs automatically at the end of every ingest, over that run's new documents. On a match, Watchdog prints a terminal alert and writes the details — document, page, surrounding text, and a link to the matching entity if it resolved to one — to `briefings/alerts-<date>.md`.

Because the automatic scan only ever sees new documents, a term added after documents are already in the vault is never checked against them. To sweep everything already ingested against the current watchlist:

```bash
watchdog watchlist
```

It writes to the same `briefings/alerts-<date>.md`.

## Resolving items

Once you have dealt with a lead, a watchlist alert, a contradiction, or a document request, mark it done so it stops reappearing. Every item in the leads, alerts, and requests files carries a short resolution id, printed next to it:

```bash
watchdog resolve lead:isolated:acme
```

Or tick the item's `- [x]` checkbox in the briefing file (or `requests.md`) and import your ticks:

```bash
watchdog resolve --sync
```

Resolved items drop out of the next sweep, so `watchdog leads` and `watchdog watchlist` become a shrinking to-do list rather than an ever-growing wall. `watchdog resolve --list` shows what you have acknowledged; `watchdog unresolve <id>` brings an item back. Acknowledgments follow an entity through a merge.

## Duplicate entities

Sometimes the same real-world person or company ends up extracted as two separate entities — most often because a name is spelled differently across documents. The dashboard's "Possible duplicates" table and `/watchdog-health` flag candidates; once you have confirmed two entries are the same, fold one into the other:

```bash
watchdog merge-entities <keep-id> <merge-id>
```

The duplicate's aliases, documents, relationships, and timeline events all combine onto the surviving entity, and every relationship elsewhere in the vault that pointed at the losing id follows the merge. Before doing anything, Watchdog prints both entities — name, type, document and relationship counts — and asks for confirmation, because a merge is irreversible.

Afterward, run `watchdog reindex` to drop the merged entity's stale search-index entries. The merge keeps only one of the two prose summaries, so when both notes had one, Watchdog prints a reminder to run `/watchdog-entity <keep-id>` in a Claude Code session — that re-synthesizes the survivor's Summary and Timeline from every merged source.

## Researching on the web

When the vault raises a question its own documents cannot answer — a director you cannot profile, a contradiction you cannot resolve, a company you need background on — Watchdog can research it on the web:

```bash
watchdog research shell-company-investigation
watchdog research shell-company-investigation -q "Who controls Acme Holdings?"
```

This opens Claude Code on the research skill. Seeded by your vault's entities, leads, and gaps, Claude proposes a research mission, confirms how wide to cast the net — quick, standard, or deep — then researches in rounds, checking in with you between each. It writes a research memo to `briefings/` when it is done.

Crucially, web research **never writes vault notes**. Instead, Claude queues every source it decides to keep — the URL, a reliability tag, and why it matters. When you exit the session, Watchdog downloads the queued sources into `_INCOMING/`, validating each one, so the findings flow through the same chew-and-ingest pipeline as documents you obtained yourself: deduplicated, entity-extracted, and cited. A scraped blog post is never confused with a primary document. After the download, fold the findings in the normal way:

```bash
watchdog
```

Then open a fresh session to investigate what came back.

A few things worth knowing:

- **Interrupted sessions lose nothing.** The queued links are held durably in the vault's internal state, so even a long deep run keeps what it queued if it is cut off. If a session dies before the download runs, `watchdog`, `watchdog chew`, and `watchdog status` all warn that sources are queued but not downloaded; re-run `watchdog research` (which offers to download the leftover queue) or run `watchdog research-fetch` to finish.
- **Already-captured sources are skipped.** Across repeated research on the same investigation, Claude skips sources the vault has already captured — unless you ask it to re-check one for updates.
- **Page snapshots.** HTML pages are captured as full rendered snapshots — images, styles, client-rendered content — when the optional capture browser is installed, falling back to a sanitized plain fetch otherwise. See the [installation guide](install.md) for the optional install.
- **Wayback archiving.** Optionally, each downloaded source can also be saved to the Internet Archive's Wayback Machine, with the snapshot URL recorded in the source's provenance record — a citable public copy that survives if the original is later changed or taken down. It is off by default and never blocks a download; the [configuration guide](configuration.md) covers the keys to set.

### Already have the URLs?

If you already have a batch of links — from a spreadsheet, a colleague, or your own browsing — you do not need a research session. Hand them straight to `watchdog fetch`:

```bash
watchdog fetch https://example.gov/filing https://news.example/article
watchdog fetch links.txt
```

A links file has one URL per line. Each URL is validated, size-capped, and saved into `_INCOMING/` with a provenance sidecar — the same hygiene as research sources — then you chew and ingest as normal.

For clipping pages as you browse, install the [Obsidian Web Clipper](https://obsidian.md/clipper) browser extension, point it at your investigation vault, and set the destination folder to `_INCOMING`. Any web page — a news article, a company profile, a government announcement — then goes into the ingest pipeline with one click.

## Ongoing rhythm

After the first ingest, the typical loop is:

1. **Drop new documents** into `_INCOMING/`
2. **`watchdog`** from the vault directory — the guided front door: it offers to chew and then ingest whatever is new, confirming before each step
3. **Read the briefing** — pay particular attention to connections with entities already in the vault
4. **`/watchdog-surface`** in a fresh Claude Code session, if the new batch was substantial

Claude Code does not need to be open while you are chewing; the queue accumulates until you are ready to extract. If you would rather run each step yourself instead of the guided walkthrough — chewing now and extracting later, say — run `watchdog chew`, `watchdog dig`, and `watchdog bark` directly; running `dig` and `bark` separately (rather than back to back) is also how you compare finalizer models against the same extraction. See the [command reference](commands.md) for all of it. If you are dropping files into a vault over a period of time, `watchdog watch` monitors `_INCOMING/` and chews new files automatically as they arrive — press Ctrl+C to stop.

## Managing investigations

Each investigation is a separate vault; create as many as you need. The commands below keep them organized — the [command reference](commands.md) has the full flag-by-flag detail.

**Status.** `watchdog status shell-company-investigation` shows document and entity counts, pending files in `_INCOMING/`, files awaiting extraction, and the last-updated date. Omit the name to see all investigations.

**History.** `watchdog log shell-company-investigation` shows the ingest history; `--lines 50` shows the last 50 lines.

**List.** `watchdog list` shows every active investigation; `--all` includes archived ones.

**Archive.** When an investigation concludes, `watchdog archive shell-company-investigation` hides it from the list without deleting anything. `watchdog unarchive` restores it.

**Rename.** `watchdog rename shell-company-investigation "Oil Company Investigation"` renames the vault folder and updates the registry and the Obsidian vault entry. Blocked while a chew or ingest is in progress.

**Describe.** `watchdog describe shell-company-investigation "One-line summary"` sets or updates the description; omit the text to be prompted.

**Move.** `watchdog move shell-company-investigation /Volumes/Archive/Investigations` moves the vault to a new location and updates the registry. If you have already moved the files by hand, it just updates the registry.

**Delete.** `watchdog delete shell-company-investigation` removes an investigation from the registry but leaves the vault files on disk. Adding `--purge` also permanently deletes all vault files — it requires explicit confirmation, and it is permanent. Use `archive` instead if you might want the vault later.

**Register.** `watchdog register` adds an existing vault folder to the registry; run it from inside the vault directory, or pass the path.

## Trusting what you read

Every extracted fact is either stated — read directly from a document — or inferred, which is marked `(inferred)` in the notes and is a lead to verify, not a finding. When a new document contradicts something already in the vault, the entity note gets a contradiction callout with both sources cited — and the contradiction itself is often newsworthy. The [vault guide](vault.md#stated-vs-inferred) has the full explanation.

## Where next

The [command reference](commands.md) documents every command and flag. The [vault guide](vault.md) explains what Watchdog builds on disk and how to read it.
