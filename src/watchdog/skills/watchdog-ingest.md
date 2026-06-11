# /watchdog-ingest — Watchdog document extraction pipeline

Extract queued files from `.watchdog/queue/` into the vault.

Chewing (OCR, Docling) is handled separately by the `watchdog chew` CLI command. This skill only runs extraction — reading queued results and writing entity notes, document notes, and registry updates.

**Argument parsing** — parse `$ARGUMENTS` before doing anything else:
- If `$ARGUMENTS` is empty: `LIMIT = null`
- If `$ARGUMENTS` matches `--limit <N>` (e.g. `/watchdog-ingest --limit 50`): `LIMIT = N`

`LIMIT` caps how many files are **extracted** this run. When the limit is reached, stop cleanly and report how many files remain.

**Architecture note:** Each document is extracted in an isolated Agent subagent. This keeps the orchestrator context flat regardless of batch size — queue file text, skill files, and extraction output never accumulate in this session. The orchestrator holds only: the investigation brief, a compact entity index (id/name/type/aliases), and a running list of per-document result summaries.

**CWD:** All bash commands run from the vault root. Never prefix commands with `cd <path> &&`.

**Pre-created directories:** `.watchdog/tmp/`, `.watchdog/queue/`, and `.watchdog/Registry/` always exist — `watchdog queue-status` ensures `.watchdog/tmp/` on every run. Never run `mkdir` for any of them.

**No exploration commands:** Never run `watchdog <command>` without arguments to probe its usage, and never run `which watchdog`, `pip show`, `python3 -c "import watchdog..."`, or any other command to locate or inspect the watchdog installation. If a step fails, re-read the relevant section of this skill and retry with the correct arguments.

---

## 0. Pre-flight checks

**Check for an existing lock.**
Read `.watchdog/Registry/.ingest-lock`. If it exists and is less than 30 minutes old, stop:
> "Ingest is already running (lock acquired at [timestamp]). If this is stale, run `watchdog unlock <project>` and retry."

**Acquire the lock.**
Run `date -u +"%Y-%m-%dT%H:%M:%SZ"` to get the current timestamp, then write `.watchdog/Registry/.ingest-lock` containing:
```
pid: claude-session
started_at: <timestamp from date command>
```

From this point, every exit path — including errors — must release the lock by deleting `.watchdog/Registry/.ingest-lock`.

---

## 1. Read investigation context

Read `context.md` if it exists. Condense it to an **investigation brief** of at most 300 words — the key entities of interest, the research questions, and any known gaps. Store as `INVESTIGATION_BRIEF`. If `context.md` is missing or empty, set `INVESTIGATION_BRIEF = ""`.

Read `hot.md` if it exists. Note current investigation state.

---

## 2. Build compact entity index

Run:
```bash
watchdog entity-index
```

Store the output as `COMPACT_ENTITY_INDEX`. This outputs a compact JSON array of `{id, name, type, aliases}` for every entity in the vault — the minimum needed for entity deduplication. If the vault has no entities yet, it outputs `[]`.

This index is passed to each document subagent so it can identify existing entities without the manifest content ever entering this session's context.

---

## 3. Find queued files

Run:
```bash
watchdog queue-status
```

This outputs `{"total": N, "files": [{"path": "...", "source_type": "..."}, ...]}`. Parse it.

If `total == 0`, stop:
> "No files queued for extraction. Run `watchdog chew` in your terminal from this folder to chew documents in `_INCOMING/`, then come back and run `/watchdog-ingest`."

Release the lock and stop.

Set `TOTAL` = `total` from the output. Set `BATCH_START` = current unix timestamp (`date +%s`). Set `EXTRACTED = 0`. Set `RESULTS = []`, `NEARDUP_ALERTS = []`, `CONTRADICTION_FLAGS = []`.

Partition the file list: any entry with `source_type == "arrows"` goes to `ARROWS_FILES`; everything else goes to `QUEUE_FILES`.

Do not read any queue file content into this session.

---

## 4. Process each file

Process files in **batches of up to 5**. `watchdog write-vault` uses file locking to serialize registry writes — concurrent calls are safe.

Process `ARROWS_FILES` inline first (see §6) before the subagent loop.

Split `QUEUE_FILES` into batches of at most 5 files. For each batch:

1. Run `watchdog entity-index` → update `COMPACT_ENTITY_INDEX` (picks up entities written by the previous batch)
2. For each file `PREP_FILE` in the batch, prepare its subagent prompt:
   - Set `SKIP_TIMELINE` = `false` only if this is the very last file in the entire run (last file of the last batch, accounting for `LIMIT`); `true` for all others
   - Print `[<N>/<TOTAL>] Launching: <filename> ...`
3. **Launch all agents in the batch simultaneously** — send a single message with all Agent tool calls in parallel. Do not send them one at a time.
4. Process results as they arrive (see "After each Agent call" below).

**Limit check:** if `LIMIT` is set and `EXTRACTED >= LIMIT` after processing a batch's results, stop before starting the next batch.

Substitute all `{placeholder}` values in the prompt below before sending — do not send literal placeholders.

---

### SUBAGENT PROMPT TEMPLATE

```
You are extracting one document for the Watchdog investigative research system. Follow every step below. Write the results to the vault. Then return the structured RESULT block at the end — no other output.

VAULT: {absolute path of the current working directory}
PREP_FILE: {PREP_FILE}
SKIP_TIMELINE: {true or false}
INVESTIGATION_BRIEF:
{INVESTIGATION_BRIEF — or "None" if empty}

KNOWN_ENTITIES:
{COMPACT_ENTITY_INDEX — the JSON array}

---

## Step 1 — Read the queue file

Read `{PREP_FILE}`. Parse the JSON. Fields present: `source_path`, `sha256`, `page_count`, `pages[]` (each has `page` integer and `markdown` string), `metadata`, `char_count`.

Set SHA256 = the `sha256` value. Set FILENAME = the filename portion of `source_path` (basename only).

## Step 2 — Exact duplicate check

```bash
watchdog is-duplicate {SHA256}
```

If output is `dup`, return this block and stop:
```
STATUS: skipped
FILENAME: {FILENAME}
REASON: already extracted (SHA-256 match)
```

## Step 3 — Load sidecar

Check whether `_INCOMING/{FILENAME}.yml` exists. If it does, read it. Note `source`, `obtained`, `relevance`, `notes` fields.

## Step 5 — Near-duplicate check

Write all page markdown to a temp file using the Write tool at path `.watchdog/tmp/wdg_nd_{SHA256}.txt` (concatenate all `pages[].markdown` values, separated by newlines). Then:

```bash
watchdog near-dup \
  --text-file .watchdog/tmp/wdg_nd_{SHA256}.txt \
  --registry .watchdog/Registry/documents.json \
  > .watchdog/tmp/wdg_nd_{SHA256}.json
rm .watchdog/tmp/wdg_nd_{SHA256}.txt
```

Read only the decision summary — do NOT read the full JSON output, which contains large minhash arrays you don't need:
```bash
watchdog near-dup --summary .watchdog/tmp/wdg_nd_{SHA256}.json
```

Store this as NEARDUP_DECISION. The full JSON stays at `.watchdog/tmp/wdg_nd_{SHA256}.json` for write-vault — do not delete it yet.

If `near_duplicates` is non-empty, note the match in your result. Continue processing regardless — the orchestrator handles the near-dup decision, not this subagent.

## Step 6 — Load domain skill

Determine the document type from its text. Read the matching skill file from `.claude/commands/`:

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
| ATI / FOI / FOIA response package | `records/foi-responses.md` |
| IRB/immigration tribunal decision, deportation order, refugee ruling | `records/immigration-refugee.md` |
| Charity return, T3010, 990, nonprofit tax filing | `records/tax-documents.md` |
| Securities disclosure, insider trading, prospectus, SEDAR/EDGAR | `records/regulatory-filings.md` |
| NPRI/TRI/PRTR emissions report, environmental assessment | `records/environmental-filings.md` |
| Health regulatory college decision, fitness to practise, inspection | `records/healthcare-licensing.md` |
| Council minutes, development permit, zoning amendment | `records/municipal-records.md` |
| Auditor general report, value-for-money audit | `records/audit-reports.md` |
| Standing offer, task authorization, vendor performance | `records/procurement-records.md` |
| Police occurrence, use-of-force, disciplinary decision, parole | `records/police-records.md` |
| Criminal charge, bail, trial, sentencing decision | `records/criminal-proceedings.md` |
| Land register extract, title deed, hypothec, conveyance | `records/land-registries.md` |
| Labour arbitration award, grievance decision, collective agreement | `records/labour-arbitration.md` |
| Grant application, research ethics decision, retraction notice | `records/academic-research.md` |
| OSFI return, reinsurance treaty, actuarial report | `records/insurance-filings.md` |
| Public inquiry report, royal commission, task force report | `records/government-reports.md` |
| Parliamentary transcript, Hansard, committee hearing, debate | `records/legislature-transcripts.md` |
| Aircraft registration, flight log, ADS-B data, aviation safety | `records/aircraft-logs.md` |
| WHOIS record, DNS data, domain registration, IP allocation | `records/dns-whois.md` |
| News article, press clipping, wire story, press release | `records/news-clippings.md` |
| YouTube/podcast transcript, earnings call, broadcast | `records/audio-video.md` |

If nothing matches, read `.claude/commands/records/general-records.md`.

## Step 7 — Infer document metadata

From the text:
- `title` — from headings, cover page, or filename
- `document_type` — descriptive (Annual Report, Affidavit, Title Transfer, etc.)
- `date_of_document` — YYYY-MM-DD; YYYY-01-01 with confidence `low` if only year is clear; null if unknown

## Step 8 — Extract entities

Using KNOWN_ENTITIES for deduplication (match on name or any alias — OCR errors are common, be generous), extract every real-world entity.

For each entity:
- `id` — existing id from KNOWN_ENTITIES if matched; otherwise new kebab-case slug
- `name` — canonical full name as it appears most completely
- `type` — Person / Company / Address / Property / CourtCase / Transaction / or a new type you determine is appropriate
- `aliases` — every other name or abbreviation used in this document
- `timeline_events` — datable events directly involving this entity. Include any event where a journalist would want to know the date: something that happened, was decided, or changed. Ask "when did this entity do or experience X?" — if the date answers that, include it. Exclude dates that only answer "when was this form stamped?" unless that date connects causally to something substantive (e.g. a filing date that immediately precedes or follows a meeting or decision of interest).
- `roles` — relationships to other entities with page citations

Confidence rules:
- `high` — directly stated
- `medium` — stated but requires one inference
- `low` — inferred across multiple statements
- `disputed` — contradicts the vault

Never upgrade a claim past its weakest element.

## Step 9 — Extract key facts

5–15 most important facts, each with text, page number, and confidence level.

## Step 10 — Contradiction check

For each entity that matched an entry in KNOWN_ENTITIES, read its vault note. Construct the path as:
- Person → `entities/person/{id}.md`
- Company → `entities/company/{id}.md`
- Address → `entities/address/{id}.md`
- Other → `entities/{type_lowercase}/{id}.md`

Compare key dates, roles, and relationships against what this document states. Flag material discrepancies where both sides are `high` or `medium` confidence. Format contradiction callouts as:

```
> [!contradiction] <short label>
> - **<existing value>** — [[documents/<slug>|<title>]], p. <n> (confidence: <level>)
> - **<new value>** — [[documents/<new-slug>|<title>]], p. <n> (confidence: <level>)
```

Do not flag: low-confidence differences, trivially explainable name variations, contradictions already in the note for the same fact.

Read entity notes **only for matched entities** — do not read notes for new entities.

## Step 11 — Build extraction JSON and write vault

Build this JSON exactly:

```json
{
  "document": {
    "sha256": "<SHA256>",
    "filename": "<FILENAME>",
    "original_path": "<source_path from queue JSON>",
    "title": "<inferred>",
    "document_type": "<inferred>",
    "date_of_document": "<YYYY-MM-DD or null>",
    "page_count": <n>,
    "source": "<from sidecar or null>",
    "obtained": "<from sidecar or null>",
    "near_duplicate_of": "<sha256 from NEARDUP_DECISION if near_duplicates non-empty, else null>",
    "summary": "<one paragraph>",
    "key_facts": [{"fact": "...", "page": <n or null>, "confidence": "..."}]
  },
  "entities": [
    {
      "id": "...",
      "name": "...",
      "type": "...",
      "aliases": [...],
      "summary": "<one sentence: who is this entity and what role do they play>",
      "analysis": "<contradiction callouts from Step 10 and any investigative notes; omit field entirely if nothing notable>",
      "timeline_events": [
        {"date": "...", "event": "...", "page": <n or null>, "confidence": "..."}
      ],
      "roles": [
        {"relationship": "...", "target_id": "...", "target_type": "...", "target_name": "...", "page": <n or null>, "confidence": "...", "date_range": null}
      ]
    }
  ],
  "morgue_entity_id": "<id of the entity this document is *about*>",
  "morgue_document_type": "<type-slug e.g. annual-report, court-order, bankruptcy-filing>"
}
```

`morgue_entity_id`: the document's subject — debtor for bankruptcy, company for annual report, defendant for court order.

Write to `.watchdog/tmp/wdg_ex_{SHA256}.json` using the Write tool. Then validate it:

```bash
watchdog validate-extraction .watchdog/tmp/wdg_ex_{SHA256}.json
```

If this prints errors, fix the JSON and re-validate before continuing. Do not call `write-vault` on a file that failed validation.

```bash
watchdog write-vault \
  --extraction .watchdog/tmp/wdg_ex_{SHA256}.json \
  --neardup-file .watchdog/tmp/wdg_nd_{SHA256}.json \
  {if SKIP_TIMELINE is true: add --skip-timeline}
```

Clean up:
```bash
rm .watchdog/tmp/wdg_ex_{SHA256}.json .watchdog/tmp/wdg_nd_{SHA256}.json {PREP_FILE}
```

## Step 12 — Return result

Return ONLY the following block. No other output.

```
STATUS: ok
FILENAME: {FILENAME}
DOCUMENT_TYPE: {document_type}
DATE: {date_of_document or unknown}
ENTITY_COUNT: {total entities extracted}
NEW_ENTITIES: {id:name, id:name — or none}
UPDATED_ENTITIES: {id:name, id:name — or none}
NEAR_DUP: {top_similarity% similar to {existing filename} — or none}
CONTRADICTIONS: {entity_id — brief description; entity_id — brief description — or none}
SUMMARY: {one paragraph summary of this document and what was extracted}
```
```

---

### After each Agent call

Parse the returned block:

**`STATUS: skipped`** — log and continue.

**`STATUS: ok`** — add the full result to `RESULTS`. If `NEAR_DUP` is not `none`, add to `NEARDUP_ALERTS`. If `CONTRADICTIONS` is not `none`, add to `CONTRADICTION_FLAGS`.

Print:
```
[<N>/<TOTAL>] Done: <FILENAME> — <ENTITY_COUNT> entities (<new_count> new) | ETA: ~<rolling estimate>s
```

---

## 5. Post-loop: graph colour check

After the loop completes, check whether any new entity type from the results is missing from `.obsidian/graph.json`'s `colorGroups` array. If so, read the file, add a colour entry (`{"query": "path:entities/<type_lowercase>", "color": {"a": 1, "rgb": <24-bit int>}}`), and write it back. Pick a colour visually distinct from existing ones.

---

## 6. arrows.app import

When a queued file has `metadata.source_type == "arrows"`, handle it inline (not via subagent) — it contains pre-parsed entities and relationships, not document text.

Read the queue JSON directly. For each entity in `entities`, create or update its entity note and registry entry (treat the arrows file as the source document). For each relationship, add it to the `from` entity's Relationships section.

Create a document note for the arrows.app file:
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

Delete the queue file when done.

---

## 7. Post-batch contradiction resolution

For each entry in `CONTRADICTION_FLAGS`:
- Read the entity note (`entities/{type_lowercase}/{id}.md`)
- Verify the contradiction is genuine, not a subagent false positive
- If false positive: edit the note to remove the `[!contradiction]` callout that `write-vault` wrote

This step reads at most a handful of entity notes — typically 0–3 per batch.

---

## 8. Post-ingest briefing

Print a batch summary:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Ingest complete: <extracted> processed, <skipped> skipped, <failed> failed
Total time: <elapsed>s
Entities in vault: <total from registry.json>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

Build everything that follows from `RESULTS`, `NEARDUP_ALERTS`, `CONTRADICTION_FLAGS`, and `INVESTIGATION_BRIEF`. Do not re-read any queue files or entity records.

Write a briefing note to `briefings/<YYYY-MM-DD-HH-MM>.md`:

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

<Entities that did not exist in the vault before this ingest, with type and source document.>

## Connections to existing entities

<Entities from the new documents that matched entities already in the vault. For each, state what the connection is and why it matters.>

## Leads and follow-up ideas

<Actionable leads from the documents:>
- **[Question]** <Open question the documents raise but don't answer.> *Source: [[documents/...]]*
- **[Contact]** <Person or entity worth reaching out to.> *Source: [[entities/...]]*
- **[Document]** <Specific document that appears to exist but isn't in the vault.> *Source: [[documents/...]], p. N*
- **[FOI]** <Records request worth filing based on a gap.> *Source: [[documents/...]], p. N*

If context.md is filled in, orient leads toward the journalist's stated questions. Omit this section if nothing warrants a lead.

## Anomalies worth a closer look

<Flag if present: shared addresses between apparently unrelated entities; a person appearing in an unexpected role; a transaction disproportionate to the entity's apparent scale; an entity appearing in many documents with no documented relationships. Write "Nothing flagged." if clean.>
```

If `NEARDUP_ALERTS` is non-empty, append:

```markdown
## Near-duplicate alerts

The following files are similar to existing documents. Review and decide whether to keep both or treat as the same document.

- <FILENAME>: <similarity>% similar to <existing document title>
```


### Update hot.md

Overwrite `hot.md`:

```markdown
# Hot cache

*Last updated: <YYYY-MM-DD> — [[briefings/<briefing-slug>|Briefing <date>]]*

## Investigation status

<One sentence on where the investigation stands, drawing on context.md and what was just ingested.>

## Recent additions

<Bullet list of new entities and documents added this session, with type and source.>

## Emerging patterns

<New connections, contradictions, or anomalies surfaced in this ingest. Omit if nothing notable.>

## Open questions

<Leads from the briefing condensed to short bullets.>
```

Keep hot.md under ~40 lines.

### Append to log.md

```markdown
## <YYYY-MM-DD HH:MM> — Ingest

- **Files:** <n> processed, <n> skipped, <n> failed
- **New entities:** <n> (<n> new, <n> updated)
- **Briefing:** [[briefings/<briefing-slug>|<date>]]
<If contradictions were flagged:>
- **Contradictions flagged:** <n> — see entity notes for details
```

---

## 9. Release lock

Delete `.watchdog/Registry/.ingest-lock` and `.watchdog/ingest.json`.

---

## 10. Clarifying questions (optional)

After the briefing, if you encountered genuine ambiguities that would meaningfully change the entity graph, ask up to 3–5 targeted questions, batched together:
- Two entities that might be the same person or company
- A document with no clear date, authority, or subject
- A near-duplicate where journalist input would help

Frame questions as optional. If deferred, note the uncertainty in the relevant note.

---

## Error handling

If a subagent exits with an error or returns an unparseable result:
1. Log to `.watchdog/Registry/ingest.log`: `[<ISO 8601>] ERROR <filename>: <error>`
2. Move the queue file to `_INCOMING/_FAILED/` if possible
3. Continue to the next file

Always release the lock at the end, even if every file failed.
