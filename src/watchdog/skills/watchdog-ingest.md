# /watchdog-ingest — Watchdog document extraction pipeline

Extract queued files from `.watchdog/queue/` into the vault.

Chewing (OCR, Docling) is handled separately by the `watchdog chew` CLI command. This skill only runs extraction — reading queued results and writing entity notes, document notes, and registry updates.

**Argument parsing** — parse `$ARGUMENTS` before doing anything else:
- If `$ARGUMENTS` is empty: `LIMIT = null`
- If `$ARGUMENTS` matches `--limit <N>` (e.g. `/watchdog-ingest --limit 50`): `LIMIT = N`

`LIMIT` caps how many files are **extracted** this run. When the limit is reached, stop cleanly and report how many files remain.

**Architecture note:** Each document is extracted in an isolated Agent subagent. This keeps the orchestrator context flat regardless of batch size — queue file text, skill files, and extraction output never accumulate in this session. The orchestrator holds only: the investigation brief, a compact entity index (id/name/type/aliases), and a running list of per-document result summaries.

**CWD:** All bash commands run from the vault root. Never prefix commands with `cd <path> &&`. Never use absolute paths in any bash command — always use paths relative to the vault root (e.g. `.watchdog/tmp/file.json`, not `/Users/…/file.json`).

**Pre-created directories:** `.watchdog/tmp/`, `.watchdog/queue/`, and `.watchdog/Registry/` always exist — `watchdog queue-status` ensures `.watchdog/tmp/` on every run. Never run `mkdir` for any of them.

**No exploration commands:** Never run `watchdog <command>` without arguments to probe its usage, and never run `which watchdog`, `pip show`, `python3 -c "..."`, or any other command to locate or inspect the watchdog installation. If a step fails, re-read the relevant section of this skill and retry with the correct arguments.

**Read registry files with the Read tool, not bash:** To inspect `.watchdog/Registry/entities.json`, `.watchdog/Registry/documents.json`, `.watchdog/Registry/manifest.json`, or any other file in the vault, use the Read tool directly. Never use `cat … | python3 -c "…"` or any shell pipeline to parse or format them.

---

## 0. Setup

Read `.watchdog/ingest-state.json`. Store the parsed contents as `INGEST`.

If the file does not exist:
> "Run `watchdog ingest` in your terminal first to initialise the session, then re-run `/watchdog-ingest`."
Stop — no lock was acquired in this session, so no cleanup is needed.

If `INGEST.total == 0` or `INGEST.lock_acquired` is false:
> "No files queued. Run `watchdog chew` then `watchdog ingest` in your terminal, then re-run `/watchdog-ingest`."
Stop.

Set `TOTAL = INGEST.total`. Set `BATCH_START = INGEST.batch_start`. Set `EXTRACTED = 0`. Set `RESULTS = []`, `NEARDUP_ALERTS = []`, `CONTRADICTION_FLAGS = []`.

Set `QUEUE_FILES = INGEST.queue_files`. Set `ARROWS_FILES = INGEST.arrows_files`.

The lock is held (acquired by `watchdog ingest`). Every exit path — including errors — must release it by running `watchdog unlock` and deleting `.watchdog/ingest-state.json`.

---

## 1. Read investigation context

Read `context.md` if it exists. Condense it to an **investigation brief** of at most 300 words — the key entities of interest, the research questions, and any known gaps. Store as `INVESTIGATION_BRIEF`. If `context.md` is missing or empty, set `INVESTIGATION_BRIEF = ""`.

Read `hot.md` if it exists. Note current investigation state.

---

## 2. Resolve skill paths

For each entry in `QUEUE_FILES`, set `DOMAIN_SKILL_PATH` = `records/<document_type>.md` if `document_type` is non-null, else `"none"`. No file reads at this stage — the skill is read by the subagent.

---

## 3. Process each file

Process files in **batches of up to 5**. Registry writes are serialized internally — concurrent subagents are safe.

Process `ARROWS_FILES` inline first (see §5) before the subagent loop.

Split `QUEUE_FILES` into batches of at most 5 files. For each batch:

1. For each file in the batch, get its `SHA256`, `FILENAME`, and `DOMAIN_SKILL_PATH` (already resolved in §2).
2. Set `SKIP_TIMELINE` = `false` only for the very last file of the entire run; `true` for all others.
3. Print `[<N>/<TOTAL>] Launching: <FILENAME clamped to 50 chars> ...`
4. **Launch all agents in the batch simultaneously** — send a single message with all Agent tool calls in parallel. Set each Agent's `description` to `Watchdog extraction: <FILENAME clamped to 50 chars>`.
5. Process results (see "After each Agent call" below).

**Limit check:** if `LIMIT` is set and `EXTRACTED >= LIMIT`, stop before the next batch.

Substitute all `{placeholder}` values before sending.

---

### SUBAGENT PROMPT TEMPLATE

```
You are extracting one document for the Watchdog investigative research system. Follow every step below exactly. Return the structured RESULT block at the end — no other output.

SHA256: {SHA256}
FILENAME: {FILENAME}
SKIP_TIMELINE: {true or false}
DOMAIN_SKILL_PATH: {DOMAIN_SKILL_PATH — e.g. records/court-documents.md, or "none" if unclassified}
INVESTIGATION_BRIEF:
{INVESTIGATION_BRIEF — or "None" if empty}

**Hard constraints — violations will break the pipeline:**
- Never run `python3 -c "..."`, `cat file | python3`, or any shell pipeline. Use the Read tool to inspect files.
- Never read `.watchdog/Registry/manifest.json`, `entities.json`, or `documents.json` directly — entity candidates come from PRE_FLIGHT.existing_entities (Step 1).
- Never use absolute paths in bash commands. Always use paths relative to the vault root.
- Never prefix commands with `cd <path> &&`.
- Never run `watchdog <command> --help` or any exploration command.

---

## Step 1 — Pre-flight

```bash
watchdog pre-flight {SHA256}
```

Parse the JSON output. Store as PRE_FLIGHT. Fields:
- `sha256`, `page_count`
- `already_extracted` — if true, return the SKIPPED block immediately
- `pages[]` — each has `page` integer and `markdown` string; this is your document content
- `near_dup.near_duplicates`, `near_dup.top_similarity`
- `existing_entities[]` — entities already in vault whose names appear in this document: `{id, name, type, aliases, note_path}`

If `already_extracted` is true, stop and return:
```
STATUS: skipped
FILENAME: {FILENAME}
REASON: already extracted (SHA-256 match)
```

Set SHA256 = PRE_FLIGHT.sha256. (FILENAME is already set from the prompt header.)

## Step 2 — Load sidecar

Check whether `_INCOMING/{FILENAME}.yml` exists. If it does, read it. Note `source`, `obtained`, `relevance`, `notes` fields.

## Step 3 — Load domain skill

If `DOMAIN_SKILL_PATH` is not `"none"`, read `.claude/commands/{DOMAIN_SKILL_PATH}` and use it. Skip the inference table below — the document was pre-classified at chew time.

**Escape hatch:** if after reading the document in Step 1 the loaded skill is clearly wrong, read the correct skill from `.claude/commands/records/` instead. This should be rare.

If `DOMAIN_SKILL_PATH` is `"none"`, scan the first few pages to identify the document type, then find and read the closest matching skill in `.claude/commands/records/`. Skill filenames are descriptive. If nothing clearly matches, read `.claude/commands/records/general-records.md`.

## Step 4 — Infer document metadata

From the text:
- `title` — from headings, cover page, or filename
- `document_type` — descriptive (Annual Report, Affidavit, Title Transfer, etc.)
- `date_of_document` — YYYY-MM-DD; YYYY-01-01 with confidence `low` if only year is clear; null if unknown

## Step 5 — Extract entities

Using `PRE_FLIGHT.existing_entities` for deduplication (match on name or any alias — OCR errors are common, be generous), extract every real-world entity.

For each entity:
- `id` — existing id from PRE_FLIGHT.existing_entities if matched; otherwise new kebab-case slug
- `match_id` — if this entity matches an existing one, set to that entity's id; otherwise omit
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

## Step 6 — Extract key facts

5–15 most important facts, each with text, page number, and confidence level.

## Step 7 — Contradiction check

For each entity that matched an entry in PRE_FLIGHT.existing_entities, read its vault note at `{note_path}.md`.

Compare key dates, roles, and relationships against what this document states. Flag material discrepancies where both sides are `high` or `medium` confidence. Format contradiction callouts as:

```
> [!contradiction] <short label>
> - **<existing value>** — [[documents/<slug>|<title>]], p. <n> (confidence: <level>)
> - **<new value>** — [[documents/<new-slug>|<title>]], p. <n> (confidence: <level>)
```

Do not flag: low-confidence differences, trivially explainable name variations, contradictions already in the note for the same fact.

Read entity notes **only for matched entities** — do not read notes for new entities.

## Step 8 — Build extraction JSON and write vault

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
    "near_duplicate_of": "<sha256 from PRE_FLIGHT.near_dup.near_duplicates[0] if non-empty, else null>",
    "summary": "<one paragraph>",
    "key_facts": [{"fact": "...", "page": <n or null>, "confidence": "..."}]
  },
  "entities": [
    {
      "id": "<matched id from PRE_FLIGHT, or new kebab-case slug>",
      "match_id": "<matched entity id — omit entirely for new entities>",
      "name": "...",
      "type": "...",
      "aliases": [...],
      "summary": "<one sentence: who is this entity and what role do they play>",
      "analysis": "<contradiction callouts from Step 7 and any investigative notes; omit field entirely if nothing notable>",
      "timeline_events": [
        {"date": "...", "event": "...", "page": <n or null>, "confidence": "..."}
      ],
      "roles": [
        {"relationship": "<Director of / Shareholder of / Counsel for / …>", "target_id": "…", "target_type": "person|company|address|…", "target_name": "…", "page": null, "confidence": "high|medium|low|disputed", "date_range": null}
      ]
    }
  ],
  "morgue_entity_id": "<id of the entity this document is *about*>",
  "morgue_document_type": "<type-slug e.g. annual-report, court-order, bankruptcy-filing>"
}
```

`morgue_entity_id`: the document's subject — debtor for bankruptcy, company for annual report, defendant for court order.

Common mistakes that will cause post-flight to reject the extraction:
- `roles` entries must be **objects** (with `relationship`, `target_id`, `target_type`, `target_name`, `page`, `confidence`, `date_range` keys) — never plain strings
- `morgue_entity_id` is required — the kebab-case id of the entity this document is primarily about
- `morgue_document_type` is required — a type slug like `annual-report`, `court-order`, `bankruptcy-filing`
- Every `confidence` value must be exactly one of: `high`, `medium`, `low`, `disputed`
- `match_id` must be omitted entirely for new entities — do not set it to `null` or `""`

Write to `.watchdog/tmp/wdg_ex_{SHA256}.json` using the Write tool. Then run post-flight:

```bash
watchdog post-flight --extraction .watchdog/tmp/wdg_ex_{SHA256}.json
```

Post-flight validates, applies entity merges, writes the vault, and cleans up temp files. If it prints errors, fix the JSON and run it again. Do not run `--help` or any exploration command to debug schema errors.

## Step 9 — Return result

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

Run `watchdog unlock` to remove `.watchdog/Registry/.ingest-lock` and clean up any temp files. Then delete `.watchdog/ingest-state.json`.

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
