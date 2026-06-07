# /ingest — Watchdog document ingest pipeline

Process all uningested files in `Incoming/` (or a specific file if one is named: `$ARGUMENTS`).

---

## 0. Pre-flight checks

**Check for an existing lock.**
Read `Registry/.ingest-lock`. If it exists and is less than 30 minutes old, stop:
> "Ingest is already running (lock acquired at [timestamp]). If this is stale, delete Registry/.ingest-lock and retry."

**Acquire the lock.**
Write `Registry/.ingest-lock` containing:
```
pid: (use "claude-session")
started_at: <ISO 8601 timestamp>
```

From this point, every exit path — including errors — must release the lock by deleting `Registry/.ingest-lock`.

---

## 1. Discover files

If `$ARGUMENTS` names a specific file, process only that file. Otherwise:

List all files in `Incoming/` recursively (including subdirectories) that are:
- Not in `Incoming/_Processed/` or `Incoming/_Failed/`
- Not a `.yml` sidecar (`.yml` extension)
- Not a `.DS_Store` or other hidden system file
- Not the lock file

```bash
find Incoming/ -type f \
  -not -path "*/_Processed/*" \
  -not -path "*/_Failed/*" \
  -not -name "*.yml" \
  -not -name ".*"
```

If nothing is found, print: `Incoming/ is empty — nothing to ingest.` Release the lock and stop.

**Record the batch start time and file count:**
```bash
python3 -c "import time; print(int(time.time()))"
```
Store this as `BATCH_START`. Store the total file count as `TOTAL`. Print:
```
Ingest starting: <TOTAL> file(s)
Rough estimate: ~<TOTAL × 30> seconds (<TOTAL × 0.5> min) — updates as files complete
```
The 30s-per-file estimate is conservative and will be refined as each file finishes.

---

## 2. For each file — preprocessing

**At the start of each file**, print a progress line:
```
[<N>/<TOTAL>] Processing: <filename> ...
```
Record the file start time:
```bash
python3 -c "import time; print(int(time.time()))"
```

**After each file completes** (whether OK, skipped, or failed), print a completion line and updated ETA:
```bash
python3 -c "
import time
file_elapsed = int(time.time()) - FILE_START
completed = N  # files done so far including this one
remaining = TOTAL - completed
# rolling average over all completed files
avg = (int(time.time()) - BATCH_START) / completed
eta_sec = int(remaining * avg)
eta_str = f'{eta_sec // 60}m {eta_sec % 60}s' if eta_sec >= 60 else f'{eta_sec}s'
print(f'[{completed}/{TOTAL}] Done: <filename> — <entity_count> entities, <elapsed>s elapsed | ETA: ~{eta_str}')
"
```

---

### 2a. Exact duplicate check

Read `Registry/documents.json`. Compute the SHA-256 of the file:
```bash
watchdog-preprocess "<file_path>" 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('sha256',''))"
```

Or run the full preprocess step first (step 2b) and extract the sha256 from its output — whichever is more efficient. If the SHA-256 already exists in `documents.json`, skip this file:
- Log: `[SKIP] <filename> — exact duplicate of <existing filename> (SHA-256 match)`
- Append to `Registry/ingest.log`
- Leave the file in `Incoming/` (do not move it)
- Continue to the next file

### 2b. Run preprocessing

```bash
watchdog-preprocess "<file_path>"
```

Capture the JSON output. If the output contains `"error"`, move the file to `Incoming/_Failed/`, log the error, and continue to the next file.

The output gives you: `filename`, `sha256`, `page_count`, `text`, `pages[]`, `metadata`.

**Special case — arrows.app JSON:**
If the filename ends in `.json` and the JSON contains `"nodes"` and `"relationships"` keys at the top level, this is an arrows.app file. Run instead:
```bash
watchdog-arrows "<file_path>"
```
Then skip to [Section 5: arrows.app import](#5-arrowsapp-import).

### 2c. Load sidecar

Check for `Incoming/<filename>.yml` (same name as the document, with `.yml` appended). If it exists, read it. Extract these fields if present:
- `source` — URL or reference where the document was obtained
- `obtained` — date obtained
- `relevance` — why this document matters
- `notes` — journalist annotations

### 2d. Near-duplicate check

Write the extracted text to a temp file, then run the near-duplicate check against it. Using a file avoids shell argument size limits on large documents.

```bash
watchdog-near-dup \
  --text-file /tmp/watchdog_neardup.txt \
  --registry Registry/documents.json
```

Write the extracted text to `/tmp/watchdog_neardup.txt` using the Write tool before running this command. Delete the temp file afterwards.

If `near_duplicates` is non-empty, pause ingest for this file and show the journalist:

> **Near-duplicate detected**
> `<filename>` is {similarity}% similar to `<existing filename>`.
>
> Options:
> - **Skip** — discard the new file (leave in Incoming/)
> - **Replace** — update the registry to point to the new file
> - **Keep both** — ingest as a separate document, linked as a likely duplicate
>
> Which would you like to do?

Wait for the journalist's response before proceeding. If they choose Skip, leave the file in `Incoming/` and continue to the next file.

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
| Condition | Maximum confidence |
|-----------|-------------------|
| Directly stated in the document | `high` |
| Stated but requires one short inference | `medium` |
| Inferred across multiple statements | `low` |
| Contradicted elsewhere in the vault | `disputed` |

Never upgrade a claim beyond its weakest element. If the name is stated but the date is inferred, the whole claim is `medium`.

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

## 4. Write vault notes (atomic)

### 4a. For each extracted entity

**Check Registry/entities.json** for an existing entry with this `id`. 

**If the entity already exists:**
- Read the existing note at `entities/<type>/<id>.md`
- Merge: add this document to `appears_in`, add new aliases not already listed, add new roles/relationships not already listed
- Preserve everything in `## Notes` exactly as-is
- Update `date_last_updated`
- Write the updated note

**If the entity is new:**
- Create `entities/<type-lowercase>/<id>.md` with this frontmatter:

```yaml
---
id: <id>
name: <canonical name>
type: <Type>
aliases:
  - <alias 1>
  - <alias 2>
appears_in:
  - document: "[[documents/<document-slug>]]"
    pages: [<page numbers>]
roles: []
date_first_seen: <date_of_document or today>
date_last_updated: <today>
---

## Notes

<!-- Journalist annotations — never overwritten by ingestion. -->
```

Add roles/relationships below the frontmatter as a `## Relationships` section:
```markdown
## Relationships

- Director of [[entities/company/shell-co-ltd]] — p. 3 — confidence: high — source: [[documents/<document-slug>]]
```

### 4b. Create the document note

Create `documents/<document-slug>.md`:

The document slug is the filename without extension, lowercased and hyphens substituted for spaces/underscores.

```yaml
---
title: <inferred title>
type: Document
document_type: <document type>
file: <original filename>
date_of_document: <YYYY-MM-DD or null>
date_ingested: <today>
source: <from sidecar if present, else null>
obtained: <from sidecar if present, else null>
entities_mentioned:
  - "[[entities/<type>/<id>]]"
  - ...
page_count: <from preprocessing>
near_duplicate_of: <sha256 if kept-both scenario, else null>
---

## Summary

<One paragraph summary of what this document is and what it contains. Be specific — name the parties, the date, the subject matter.>

## Key facts

<Bullet list of 5–15 key facts, each with page citation and confidence level. Format:>
- <Fact statement> (p. <N>) — confidence: <level>

## Entities mentioned

<Auto-generated wiki links, one per line:>
- [[entities/<type>/<id>|<canonical name>]]

## Notes

<!-- Reserved for journalist annotations — never overwritten by ingestion. -->
```

If a sidecar was loaded:
- Merge `source` and `obtained` into frontmatter
- Append `relevance` and `notes` to the `## Notes` section, below the comment line

### 4c. Update registries (in this order — do not skip any step)

**Step 1: Update `Registry/documents.json`**

Add or update the entry for this document's SHA-256:
```json
{
  "<sha256>": {
    "sha256": "<sha256>",
    "filename": "<filename>",
    "original_path": "Incoming/<filename>",
    "document_note": "documents/<slug>",
    "ingested_at": "<ISO 8601>",
    "page_count": <n>,
    "document_type": "<type>",
    "entities_extracted": ["<entity-id-1>", "<entity-id-2>"],
    "near_duplicate_of": null,
    "shingles": [<first 200 shingles from near_dup output>]
  }
}
```

**Step 2: Update `Registry/entities.json`**

For each entity (new or updated), add/update:
```json
{
  "<id>": {
    "id": "<id>",
    "name": "<canonical name>",
    "type": "<Type>",
    "aliases": ["<alias>"],
    "appears_in": ["<sha256 of this document>"],
    "note_path": "entities/<type-lowercase>/<id>"
  }
}
```

**Step 3: Update `Registry/registry.json`**

Update `last_updated` to now, and recount `document_count` and `entity_count` from the other two registries.

**Step 4: Append to `Registry/ingest.log`**

```
[<ISO 8601>] INGEST <filename> sha256=<hash> entities=<count> type=<document_type>
```

### 4d. Move the file

Move `Incoming/<filename>` (and the `.yml` sidecar if it exists) to `Incoming/_Processed/`.

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

Delete `Registry/.ingest-lock`.

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
1. Log the error to `Registry/ingest.log`: `[<ISO 8601>] ERROR <filename>: <error message>`
2. Move the file to `Incoming/_Failed/`
3. Continue to the next file — do not abort the entire batch

Always release the lock at the end, even if every file failed.
