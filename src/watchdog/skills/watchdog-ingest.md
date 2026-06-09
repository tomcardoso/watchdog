# /watchdog-ingest — Watchdog document ingest pipeline

Process all uningested files in `_INCOMING/` (or a specific file if one is named: `$ARGUMENTS`).

---

## 0. Pre-flight checks

**Read investigation context.**
Read `context.md` if it exists. This tells you what the journalist is pursuing, what questions they are trying to answer, and which entities they already know are relevant. Hold this context throughout — use it to orient the briefing and leads toward what actually matters to this investigation. If `context.md` is empty or missing, proceed without it.

**Check for an existing lock.**
Read `.watchdog/Registry/.ingest-lock`. If it exists and is less than 30 minutes old, stop:
> "Ingest is already running (lock acquired at [timestamp]). If this is stale, delete .watchdog/Registry/.ingest-lock and retry."

**Check for an interrupted batch.**
If `.watchdog/ingest.json` exists, a previous ingest was interrupted before it could finish. Tell the journalist:
> "Found interrupted batch results from [file timestamp]. Resume from checkpoint and skip preprocessing, or re-run preprocessing from scratch?"

- If **resume**: load `.watchdog/ingest.json` as the results array, skip Step 1, go directly to Step 2. Files already in `documents.json` will be skipped automatically by the duplicate check.
- If **re-preprocess**: delete `.watchdog/ingest.json` and continue normally.

**Acquire the lock.**
Write `.watchdog/Registry/.ingest-lock` containing:
```
pid: (use "claude-session")
started_at: <ISO 8601 timestamp>
```

From this point, every exit path — including errors — must release the lock by deleting `.watchdog/Registry/.ingest-lock`.

---

## 1. Discover files

If `$ARGUMENTS` names a specific file, process only that file. Otherwise:

List all files in `_INCOMING/` recursively (including subdirectories) that are:
- Not in `_FAILED/`
- Not a `.yml` sidecar (`.yml` extension)
- Not a `.DS_Store` or other hidden system file
- Not the lock file

`_CONTEXT/` is a separate background folder — never touch it here.

```bash
find _INCOMING/ -type f \
  -not -path "*/_FAILED/*" \
  -not -name "*.yml" \
  -not -name ".*"
```

If nothing is found, print: `_INCOMING/ is empty — nothing to ingest.` Release the lock and stop.

**Step 1 — batch preprocessing (parallel)**

Run preprocessing on all discovered files simultaneously before doing any extraction.
This is the slow step (OCR, Docling); parallelising it here means extraction runs
against already-finished results rather than waiting file-by-file.

Run in the **foreground** (not background). Redirect only stdout to the batch file — do **not** redirect stderr, so progress lines stream to the terminal:

```bash
watchdog preprocess-batch _INCOMING/ --workers 4 --vault-path "$(pwd)" > .watchdog/ingest.json
```

When that command returns, print:
```
Preprocessing complete. Starting extraction: <TOTAL> file(s)
```

Store `BATCH_START` (capture with `date +%s`) and `TOTAL` (file count). Do not load all results into context at once — process files one at a time in the loop below.

---

## 2. For each file — extraction

Before the loop, initialize: `CUMULATIVE_CHARS = 0`

Iterate over the batch results **one file at a time**, by index. For file at index N (0-based), load only that file's data:

```bash
watchdog batch-get .watchdog/ingest.json --index <N> --meta
```

Then fetch the text only when you need it for extraction:

```bash
watchdog batch-get .watchdog/ingest.json --index <N> --text
```

**Never load more than one file's text into context at a time.** After finishing all 42 files, delete the batch results file: `rm .watchdog/ingest.json`.

For each result:

**At the start of each file**, print:
```
[<N>/<TOTAL>] Extracting: <filename> ...
```

**After each file completes**, print a completion line and rolling ETA:
```bash
echo "[<N>/<TOTAL>] Done: <filename> — <entity_count> entities | ETA: ~$(( (<TOTAL>-<N>) * ($(date +%s)-<BATCH_START>) / <N> ))s"
```

If a preprocessing result contains `"error"`, move the source file to `_INCOMING/_FAILED/` (create the directory if it doesn't exist),
log the error, and continue to the next file.

### 2a. Exact duplicate check

Read `.watchdog/Registry/documents.json`. Get the SHA-256 for this file from the batch results:
```bash
watchdog batch-get .watchdog/ingest.json --index <N> --field sha256
```

If the SHA-256 already exists in `documents.json`, skip this file:
- Log: `[SKIP] <filename> — exact duplicate of <existing filename> (SHA-256 match)`
- Append to `Registry/ingest.log`
- Leave the file in `_INCOMING/` (do not move it)
- Continue to the next file

### 2b. Load page content from batch

```bash
watchdog batch-get .watchdog/ingest.json --index <N> --text
```

This returns the concatenated page markdown for this file. If the batch entry has `"error"`, you already handled that in step 2a — no need to check again here.

The batch entry (from `--meta`) gives you: `filename`, `sha256`, `page_count`, `pages[]` (each with `page` number and `markdown` content), `metadata`. Use `--text` for the full text, or `--meta` when you need individual page numbers.

**Do not re-run `watchdog preprocess` per file.** The batch already preprocessed and embedded all files in parallel. Re-running would double the processing time.

**Special case — arrows.app JSON:**
If the filename ends in `.json` and the JSON contains `"nodes"` and `"relationships"` keys at the top level, this is an arrows.app file. Run instead:
```bash
watchdog arrows "<file_path>"
```
Then skip to [Section 5: arrows.app import](#5-arrowsapp-import).

### 2c. Confidential material check

Before extracting anything, assess whether this document is a public record. Look for:
- Text marked "confidential", "privileged", "private", "not for distribution", or similar
- Private correspondence, internal memos, or personal communications
- Documents whose origin cannot be identified as a public database, court registry, government body, regulatory filing, or similar public source

If any of these apply, **stop immediately** and tell the journalist:

> "This document may not be a public record. Watchdog is designed for publicly available records only — court filings, corporate records, government contracts, regulatory filings. Confidential source material must not be processed through AI. Please confirm this is a public record before I continue, or remove it from _INCOMING/."

Wait for explicit confirmation before proceeding. If the journalist confirms it is public, continue. If they are unsure, treat it as confidential and move on to the next file.

### 2d. Load sidecar

Check for `_INCOMING/<filename>.yml` (same name as the document, with `.yml` appended). If it exists, read it. Extract these fields if present:
- `source` — URL or reference where the document was obtained
- `obtained` — date obtained
- `relevance` — why this document matters
- `notes` — journalist annotations

### 2e. Near-duplicate check

Write the extracted text to a temp file, then run the near-duplicate check against it. Using a file avoids shell argument size limits on large documents.

```bash
watchdog near-dup \
  --text-file /tmp/watchdog_neardup.txt \
  --registry .watchdog/Registry/documents.json
```

Write the extracted text to `/tmp/watchdog_neardup.txt` using the Write tool before running this command. Delete the temp file afterwards.

**Capture the full JSON output** and store it in memory as `NEARDUP_RESULT`. Also save it to a temp file — `write-vault` will read the shingles directly from there:

```bash
# Write tool: /tmp/watchdog-neardup-<sha256>.json  ← write NEARDUP_RESULT here
```

If `near_duplicates` is non-empty, pause ingest for this file and show the journalist:

> **Near-duplicate detected**
> `<filename>` is {similarity}% similar to `<existing filename>`.
>
> Options:
> - **Skip** — discard the new file (leave in _INCOMING/)
> - **Replace** — update the registry to point to the new file
> - **Keep both** — ingest as a separate document, linked as a likely duplicate
>
> Which would you like to do?

Wait for the journalist's response before proceeding. If they choose Skip, leave the file in `_INCOMING/` and continue to the next file.

---

## 3. Entity and relationship extraction

### 3a. Load domain skill (if applicable)

Inspect the document text to determine its type. If it matches one of the following, read the corresponding skill file from `~/.claude/commands/records/` before extracting:

| Document type | Skill file |
|--------------|-----------|
| Annual report, corporate registration, director filing | `records/corporate-filings.md` |
| Statement of claim, affidavit, judgment, court order | `records/court-documents.md` |
| Title transfer, mortgage, lien, property assessment | `records/real-estate.md` |
| Balance sheet, income statement, auditor report | `records/financial-statements.md` |
| Creditor list, trustee report, discharge document | `records/bankruptcy.md` |
| Procurement record, tender, contract award | `records/government-contracts.md` |
| Campaign finance return, donor list, third-party advertising return | `records/election-filings.md` |
| Lobbyist registration, communication report, lobbying disclosure | `records/lobbying-records.md` |
| ATI / FOI / FOIA response package, exemption index, severance log | `records/foi-responses.md` |
| IRB/immigration tribunal decision, deportation order, refugee ruling | `records/immigration-refugee.md` |
| Charity return, T3010, 990, T3, nonprofit tax filing | `records/tax-documents.md` |
| Securities disclosure, insider trading report, prospectus, SEDAR/EDGAR filing | `records/regulatory-filings.md` |
| NPRI/TRI/PRTR emissions report, environmental assessment, spill record | `records/environmental-filings.md` |
| Health regulatory college decision, fitness to practise finding, inspection report | `records/healthcare-licensing.md` |
| Council minutes, development permit, variance application, zoning amendment | `records/municipal-records.md` |
| Auditor general report, value-for-money audit, inspector general report | `records/audit-reports.md` |
| Standing offer call-up, task authorization, vendor performance report | `records/procurement-records.md` |
| Police occurrence report, use-of-force report, disciplinary decision, parole ruling | `records/police-records.md` |
| Criminal charge, bail decision, trial decision, sentencing decision | `records/criminal-proceedings.md` |
| Land register extract, title deed, hypothec, property conveyance | `records/land-registries.md` |
| Labour arbitration award, grievance decision, collective agreement | `records/labour-arbitration.md` |
| Grant application, research ethics decision, retraction notice | `records/academic-research.md` |
| OSFI return, reinsurance treaty, actuarial report | `records/insurance-filings.md` |
| Public inquiry report, royal commission report, task force report | `records/government-reports.md` |
| Parliamentary transcript, Hansard, committee hearing, legislative debate | `records/legislature-transcripts.md` |
| Aircraft registration, flight log, ADS-B data, aviation safety report | `records/aircraft-logs.md` |
| WHOIS record, DNS data, domain registration, IP allocation | `records/dns-whois.md` |
| News article, press clipping, wire story, press release | `records/news-clippings.md` |
| YouTube transcript, podcast transcript, earnings call transcript, broadcast | `records/audio-video.md` |

If no skill file matches the document type, read `records/general-records.md` before extracting — it provides a universal framework for orienting yourself and extracting from unfamiliar document types.

### 3b. Infer document metadata

From the document text, determine:
- **title** — infer from headings, cover page, or filename if nothing else
- **document_type** — what kind of document is this? (Annual Report, Affidavit, Title Transfer, etc.)
- **date_of_document** — the date on the document itself, not the file date. Format: YYYY-MM-DD. If only a year is clear, use YYYY-01-01 with confidence `low`.

### 3c. Extract entities

Before extracting, read `.watchdog/Registry/manifest.json`. It lists every entity already in the vault: `id`, `name`, `type`, `aliases`, and `note_path`. Use it to:
- Assign the correct existing `id` when the document mentions an entity already in the vault (match on name or any alias — be thorough, OCR errors are common)
- Avoid creating a new entity that is a variation of an existing one ("Shell Co." and "Shell Company Ltd." are the same entity if the aliases match)

Read through the full text carefully. For every real-world entity:

**Extract:**
- `name` — canonical full name as it appears most completely in the document
- `type` — Person / Company / Address / Property / CourtCase / Transaction / or a new type you determine is appropriate
- `aliases` — every other name or abbreviation used for this entity in the document
- `id` — use the existing manifest ID if the entity is already in the vault; otherwise generate a new kebab-case slug: `john-doe`, `shell-co-ltd`, `123-main-st-toronto-on`

**Record appearances:**
- Which pages the entity appears on
- In what context (role, relationship, transaction)

**Confidence rules (apply strictly):**
| Condition | Maximum confidence | Journalistic equivalent |
|-----------|-------------------|------------------------|
| Directly stated in the document | `high` | What the document says |
| Stated but requires one short inference | `medium` | What can reasonably be inferred |
| Inferred across multiple statements | `low` | Lead — still needs independent reporting |
| Contradicted elsewhere in the vault | `disputed` | Conflicting sources |

Never upgrade a claim beyond its weakest element. If the name is stated but the date is inferred, the whole claim is `medium`. A `low`-confidence fact is a lead, not a finding — it belongs in the vault but must not be treated as established.

### 3d. Extract relationships

For each meaningful relationship between entities:
- Who is a director / officer / shareholder of which company
- Who lives or is registered at which address
- Which property belongs to which entity
- Which person is a party to which court case
- What transaction connects which entities

Record for each relationship: the two entities involved, the relationship type, which page it comes from, and confidence.

### 3e. Extract key facts

Pull out the 5–15 most important facts from the document, with:
- The fact stated in one clear sentence
- Page number citation
- Confidence level

### 3f. Contradiction check

For each entity you extracted that already exists in the manifest, read its entity note from the `note_path` listed there (append `.md`).

Compare the following against what the new document states:
- Key dates (incorporation, appointment, transaction dates in the timeline)
- Roles and relationships (a person listed as director in one document but officer in another is worth noting; a person listed as director of Company A in one document but not in another is a potential contradiction if the dates overlap)
- Addresses and identifying details

If a **material discrepancy** exists — the same fact stated differently in the new document vs. the existing note, both at `high` or `medium` confidence — record it in the entity's `analysis` field using this callout format:

```
> [!contradiction] <short label, e.g. "Date of incorporation disputed">
> - **<value from existing note>** — [[documents/<slug>|<title>]], p. <n> (confidence: <level>)
> - **<value from new document>** — [[documents/<new-slug>|<new title>]], p. <n> (confidence: <level>)
```

Do not flag discrepancies where:
- One or both facts are `low` confidence
- The difference is trivially explainable (e.g. a company using both its full and abbreviated name)
- The existing note already contains a `[!contradiction]` callout for the same fact

`watchdog write-vault` will merge the `analysis` field into the entity note automatically.

---

## 4. Write vault artifacts

Build the extraction JSON from everything gathered in step 3. The JSON must match this schema exactly:

```json
{
  "document": {
    "sha256": "<from batch results>",
    "filename": "<original filename>",
    "original_path": "<source_path from batch meta results>",
    "title": "<inferred>",
    "document_type": "<inferred>",
    "date_of_document": "<YYYY-MM-DD or null>",
    "page_count": <n>,
    "source": "<from sidecar or null>",
    "obtained": "<from sidecar or null>",
    "near_duplicate_of": "<sha256 or null>",
    "summary": "<one paragraph summary>",
    "key_facts": [
      {"fact": "<text>", "page": <n or null>, "confidence": "<level>"}
    ]
  },
  "entities": [
    {
      "id": "<kebab-case>",
      "name": "<canonical name>",
      "type": "<Type>",
      "aliases": ["<alias>"],
      "summary": "<one sentence: who is this entity and what role do they play in the vault>",
      "analysis": "<investigative note for this ingest: patterns, anomalies, leads — omit if nothing notable>",
      "timeline_events": [
        {
          "date": "<YYYY-MM-DD, YYYY-MM, or YYYY — use finest granularity the document supports>",
          "event": "<what happened, one clear sentence>",
          "page": <n or null>,
          "confidence": "<level>"
        }
      ],

**What to include in timeline_events — two tracks:**

*Track 1 — investigative relevance.* If `context.md` states research questions or named entities of interest, extract events that bear directly on those questions: transactions, appointments, filings with legal effect, regulatory decisions, contradictions of existing vault entries, connections between entities the journalist is following.

*Track 2 — biographical record.* Regardless of research focus, extract events that materially define the entity's history: incorporation or dissolution, appointment or resignation of key officers, major transactions (acquisition, sale, merger), criminal charges or civil judgments, licensing actions, name changes. These form a useful factual record even when a document isn't directly on-topic.

**Do not extract:**
- Routine procedural filing dates with no substantive content (e.g. "Document filed with registry on 2019-03-12" when the filing content is already captured elsewhere)
- Standard administrative deadlines, notice periods, and boilerplate effective dates
- Dates that appear only in form headers, footers, or metadata fields
- Events already present in this entity's existing timeline (avoid duplicating what `write-vault` will de-duplicate anyway)

The resulting timeline may be long — a large investigation legitimately has thousands of material events. The filter is about substance, not length.
      "roles": [
        {
          "relationship": "<type>",
          "target_id": "<entity-id>",
          "target_type": "<Type>",
          "target_name": "<canonical name>",
          "page": <n or null>,
          "confidence": "<level>",
          "date_range": "<range or null>"
        }
      ]
    }
  ],
  "morgue_entity_id": "<primary-entity-id>",
  "morgue_document_type": "<document-type-slug>"
}
```

**`morgue_entity_id`** — kebab-case ID of the entity the document is *about* (its subject, not just mentioned). For a bankruptcy filing: the debtor. For an annual report: the company. For a court order: the defendant/respondent.

**`morgue_document_type`** — inferred type, lowercased and hyphenated: `annual-report`, `director-filing`, `bankruptcy-filing`, `court-order`, `financial-statement`, `corporate-registration`, etc.

Write the JSON to a temp file using the Write tool:
```
/tmp/watchdog-extraction-<sha256>.json
```

Then run:
```bash
watchdog write-vault \
  --extraction /tmp/watchdog-extraction-<sha256>.json \
  --neardup-file /tmp/watchdog-neardup-<sha256>.json \
  [--skip-timeline]
```

Pass `--skip-timeline` for every file **except the last** in the batch. Rebuilding `timeline.md` on every file is O(N) in vault size — skip it for mid-batch files and let the final write do it once.

Clean up:
```bash
rm /tmp/watchdog-extraction-<sha256>.json /tmp/watchdog-neardup-<sha256>.json
```

`watchdog write-vault` handles all vault writes atomically: entity notes (new or merged), document note, all 4 registry files (`entities.json`, `documents.json`, `registry.json`, `ingest.log`), and the morgue move. Do not perform any of these writes manually.

**Graph colour check** — after `watchdog write-vault` completes, check whether any entity type you extracted is missing from `.obsidian/graph.json`'s `colorGroups` array. If so, add a colour entry for it. The query pattern is `path:entities/<type_lowercase>` and the colour format is `{"a": 1, "rgb": <24-bit packed integer>}` where the integer is `(R << 16) | (G << 8) | B`. Pick a colour that is visually distinct from the ones already in use. Read the existing file first, update the `colorGroups` array, and write it back.

**Context compaction check** — after each successful `watchdog write-vault` call, add the file's `char_count` (from the batch `--meta` result) to `CUMULATIVE_CHARS`. If `CUMULATIVE_CHARS > 500000`, run `/compact` and reset `CUMULATIVE_CHARS = 0`.

**After `/compact` resumes** — your in-context variables (`N`, `CUMULATIVE_CHARS`, `BATCH_START`) are gone. Do not restart `preprocess-batch`. Instead, resume the extraction loop from index 0: step 2a's SHA-256 duplicate check will skip already-registered files cheaply. The batch file (`.watchdog/ingest.json`) is still on disk and is the source of truth for which files are in the batch.

---

## 5. arrows.app import

When processing an arrows.app JSON file (from step 2b):

For each entity in the parsed output, follow steps 4a and 4c (entity note creation/update and registry update) — treating the arrows.app file as the source document.

For each relationship:
- Add it to the `## Relationships` section of the `from` entity note
- Format: `- <relationship type> [[entities/<type>/<to-id>|<to name>]] — source: [[documents/<arrows-slug>]] — confidence: high`

Create a document note for the arrows.app file itself:
```yaml
---
title: <filename without extension>
type: Document
document_type: Relationship Diagram
file: <filename>
date_of_document: null
date_ingested: <today>
source: null
obtained: null
entities_mentioned:
  - <all entities from the diagram>
page_count: 1
---

## Summary

Relationship diagram imported from arrows.app. Contains <N> entities and <M> relationships.

## Notes

<!-- Journalist annotations. -->
```

---

## 6. Post-ingest briefing

**Before writing the briefing**, print a batch completion summary:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Ingest complete: <ok> processed, <skipped> skipped, <failed> failed
Total time: <elapsed>
Entities in vault: <total entity count from registry.json>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

After all files are processed, write a briefing note to `briefings/<YYYY-MM-DD-HH-MM>.md`.

### Briefing format

```markdown
---
date: <ISO 8601>
files_ingested: <n>
new_entities: <n>
---

# Ingest briefing — <date>

## What was ingested

<One line per file: filename, document type, date of document, entity count.>

## New entities

<List of entities that did not exist in the vault before this ingest, with their type and which document they came from.>

## Connections to existing entities

<List of entities from the new documents that match entities already in the vault. For each connection, state what the connection is and why it matters.>

## Leads and follow-up ideas

<For each actionable lead the documents suggest, typed and cited:>

- **[Question]** <An open question the documents raise but don't answer — e.g. "Who owned Shell Co before John Doe? The 2019 filing names a different director but gives no prior history."> *Source: [[documents/...]]*
- **[Contact]** <A person or entity worth reaching out to, with the reason.> *Source: [[entities/...]]*
- **[Document]** <A specific document that appears to exist but isn't in the vault — court file number, filing reference, etc.> *Source: [[documents/...]], p. N*
- **[FOI]** <A records request worth filing, based on a gap in the documents.> *Source: [[documents/...]], p. N*

If context.md is filled in, orient leads toward the journalist's stated questions and known gaps. A lead that directly addresses a stated question is more valuable than a generic anomaly.

If nothing warrants a lead, omit this section.

## Anomalies worth a closer look

<Flag any of the following, if present:>
- An address shared by entities that have no other apparent connection
- A person appearing in an unexpected role (e.g. listed as director but previously only seen as plaintiff)
- A transaction amount disproportionate to the apparent scale of the entity
- An entity appearing in many documents but with no documented relationships

<If nothing anomalous was found, write: "Nothing flagged.">

---

*Anything you'd like to add, correct, or flag before I close out?*
```

Print the briefing summary to the terminal (the "What was ingested", "New entities", and "Connections" sections — omit the full anomaly section unless something was flagged).

### Update hot.md

After writing the briefing, overwrite `hot.md` with a current-state summary. This is the file Claude reads at the start of the next session to orient itself without re-reading everything.

```markdown
# Hot cache

*Last updated: <YYYY-MM-DD> — [[briefings/<briefing-slug>|Briefing <date>]]*

## Investigation status

<One sentence describing where the investigation stands, drawing on context.md and what was just ingested. E.g. "Three filings processed; focus is on directorship network around Shell Co Ltd.">

## Recent additions

<Bullet list of new entities and documents added in this session, with type and source document.>

## Emerging patterns

<Any new connections, contradictions, or anomalies surfaced in this ingest that should stay top of mind. Omit if nothing notable.>

## Open questions

<The leads from the briefing condensed to short bullets — questions to pursue, documents to find, FOIs to file.>
```

Keep hot.md under ~40 lines. It is a prompt, not a report — the full briefing is in `briefings/`.

### Append to log.md

Append the following block to `log.md`:

```markdown
## <YYYY-MM-DD HH:MM> — Ingest

- **Files:** <n> processed, <n> skipped, <n> failed
- **New entities:** <n> (<n> new, <n> updated)
- **Briefing:** [[briefings/<briefing-slug>|<date>]]
<If contradictions were flagged:>
- **Contradictions flagged:** <n> — see entity notes for details
```

---

## 7. Release lock

Delete `.watchdog/Registry/.ingest-lock` and `.watchdog/ingest.json`.

---

## 8. Clarifying questions (optional)

After the briefing, if you encountered genuine ambiguities that would meaningfully change the entity graph, ask up to 3–5 targeted questions. Batch them together, not interleaved.

**Ask when:**
- Two entities might be the same person or company ("I found 'J. Smith' and 'John Smith' — are these the same person?")
- A document has no clear date, issuing authority, or subject
- A role seems inconsistent with prior appearances
- A near-duplicate was kept-both and confirmation would help

**Do not ask about:**
- Things you can reasonably infer
- Minor details with no impact on the entity graph
- Anything already covered in the briefing

Frame questions explicitly as optional: the journalist can answer, defer, or say "make your best guess." If deferred, note the uncertainty in the relevant entity or document note.

---

## Error handling

If any step fails for a specific file:
1. Log the error to `.watchdog/Registry/ingest.log`: `[<ISO 8601>] ERROR <filename>: <error message>`
2. Move the file to `_INCOMING/_FAILED/` (create if needed)
3. Continue to the next file — do not abort the entire batch

Always release the lock at the end, even if every file failed.
