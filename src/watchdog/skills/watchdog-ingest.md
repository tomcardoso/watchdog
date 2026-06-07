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
watchdog preprocess-batch _INCOMING/ --workers 4 > .watchdog/ingest.json
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

### 2b. Run preprocessing

```bash
watchdog preprocess "<file_path>"
```

Capture the JSON output. If the output contains `"error"`, move the file to `_INCOMING/_FAILED/` (create if needed), log the error, and continue to the next file.

The output gives you: `filename`, `sha256`, `page_count`, `text`, `pages[]`, `metadata`.

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

If no skill file is found for a type, proceed with general extraction.

### 3b. Infer document metadata

From the document text, determine:
- **title** — infer from headings, cover page, or filename if nothing else
- **document_type** — what kind of document is this? (Annual Report, Affidavit, Title Transfer, etc.)
- **date_of_document** — the date on the document itself, not the file date. Format: YYYY-MM-DD. If only a year is clear, use YYYY-01-01 with confidence `low`.

### 3c. Extract entities

Read through the full text carefully. For every real-world entity:

**Extract:**
- `name` — canonical full name as it appears most completely in the document
- `type` — Person / Company / Address / Property / CourtCase / Transaction / or a new type you determine is appropriate
- `aliases` — every other name or abbreviation used for this entity in the document
- `id` — kebab-case slug: `john-doe`, `shell-co-ltd`, `123-main-st-toronto-on`

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
    "shingles": [],
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
watchdog write-vault --extraction /tmp/watchdog-extraction-<sha256>.json
```

Clean up:
```bash
rm /tmp/watchdog-extraction-<sha256>.json
```

`watchdog write-vault` handles all vault writes atomically: entity notes (new or merged), document note, all 4 registry files (`entities.json`, `documents.json`, `registry.json`, `ingest.log`), and the morgue move. Do not perform any of these writes manually.

**Context compaction check** — after each successful `watchdog write-vault` call, add the file's `char_count` (from the batch `--meta` result) to `CUMULATIVE_CHARS`. If `CUMULATIVE_CHARS > 500000`, run `/compact` and reset `CUMULATIVE_CHARS = 0`.

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
