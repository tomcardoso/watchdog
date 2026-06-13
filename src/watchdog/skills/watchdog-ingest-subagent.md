You are extracting one document for the Watchdog investigative research system. Follow every step below exactly. Return the structured RESULT block at the end — no other output.

**Hard constraints — violations will break the pipeline:**
- Never pipe or post-process command output with `python3`, `awk`, `jq`, `sed`, `grep`, or any other tool. The Bash tool returns output directly — read it as-is.
- Never use absolute paths in bash commands. Always use paths relative to the vault root.
- Never read `.watchdog/Registry/manifest.json`, `entities.json`, or `documents.json` directly — entity candidates come from PRE_FLIGHT.existing_entities (Step 1).
- Never prefix commands with `cd <path> &&`.
- Never run `watchdog <command> --help` or any exploration command.

---

## Step 1 — Pre-flight

```bash
watchdog pre-flight {SHA256}
```

The stdout output is **metadata only** — it does not include page content. Read it directly from the Bash tool output. Fields:
- `sha256`, `page_count`
- `already_extracted` — if true, return the SKIPPED block immediately
- `near_dup.near_duplicates`, `near_dup.top_similarity`
- `existing_entities[]` — entities already in vault whose names appear in this document: `{id, name, type, aliases, note_path}`
- `pages_path` — path to a markdown file containing all pages separated by `<!-- PAGE N -->` markers and `---` dividers

If `already_extracted` is true, stop and return:
```
STATUS: skipped
FILENAME: {FILENAME}
REASON: already extracted (SHA-256 match)
```

**Do NOT pipe or redirect this command's output.** Do NOT run `python3`, `awk`, `grep`, `sed`, or any other tool on its output.

**Reading pages:** Use the Read tool on `pages_path`. For long documents, read in chunks using `offset` and `limit` (lines). The `<!-- PAGE N -->` markers tell you which page you are on. Read until you have seen all pages up to `page_count`. Do not proceed to extraction with a partial document.

Set SHA256 = PRE_FLIGHT.sha256. (FILENAME is already set from the prompt header.)

## Step 2 — Load sidecar

Check whether `_INCOMING/{FILENAME}.yml` exists. If it does, read it. Note `source`, `obtained`, `relevance`, `notes` fields.

## Step 3 — Load domain skill

If `DOMAIN_SKILL_PATH` is not `"none"`, read `.claude/commands/{DOMAIN_SKILL_PATH}` and use it. Skip the inference below — the document was pre-classified at chew time.

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

## Step 9 — Write scratchpad

Write a curated scratchpad to `.watchdog/tmp/notes_{SHA256}.md`. This is what the orchestrator uses to write the briefing — keep it tight and high-signal. Use this structure:

```markdown
# {FILENAME}

## Key figures
- {Specific numbers, amounts, dates, percentages that matter to the investigation}

## Leads
- {Anything that warrants follow-up — unusual relationships, conflicts of interest, gaps}

## Contradictions
- {Entity or fact that conflicts with something else in this document or the vault}

## Chronological note
{One sentence on where this document sits in the story's timeline, if relevant}
```

Omit any section that has nothing worth saying. Do not summarize — include only what a reporter would actually want to know.

## Step 10 — Return result

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
