# /watchdog-entity — Entity note refresh

Refresh the Summary and Timeline for one or more entities by re-synthesizing from all documents they appear in.

Usage: `/watchdog-entity <entity-id> [entity-id ...]`

Run this when an entity has accumulated enough documents that its ingest-time Summary may have drifted, or when you want a definitive Timeline for a central figure in the investigation.

---

## For each entity ID in `$ARGUMENTS`

### 1. Load the entity

Read `.watchdog/Registry/entities.json`. Find the entry for this entity ID. If not found, print an error and skip to the next ID.

Extract:
- `name`, `type`, `note_path`, `appears_in` (list of SHA-256s)

### 2. Load all document notes

Read `.watchdog/Registry/documents.json`. For each SHA-256 in `appears_in`:
- Look up `document_note` (e.g. `documents/form-79`)
- Read `<document_note>.md` from the vault

Collect the content of all those document notes. You do not need to re-read the original preprocessed text — the document notes contain the key facts, summary, and entities that were extracted during ingest.

Also read the entity's current note at `<note_path>.md` to see its existing Summary, Timeline, and Analysis.

### 3. Synthesize fresh Summary

Write a new Summary for this entity, synthesizing everything you know from all document notes. The Summary should:
- State who or what this entity is (type, role, sector)
- Describe their significance to this investigation
- Mention the range of documents they appear in (earliest to latest date, document types)
- Note any patterns, key relationships, or unresolved questions
- Be as long as needed — a central figure with 20 documents warrants several paragraphs

The new Summary **replaces** the existing one entirely.

### 4. Re-extract Timeline events

Re-read all document notes and extract every datable event involving this entity. For each event:
- `date` — finest granularity the document supports: `YYYY-MM-DD`, `YYYY-MM`, or `YYYY`
- `event` — what happened, one clear sentence naming this entity
- `source_sha256` — SHA-256 of the source document (from `documents.json`)
- `page` — page number if known, else null
- `confidence` — `high`, `medium`, `low`, or `disputed`

Include only events that directly involve this entity — not background context. The Timeline **replaces** the existing one entirely (this is a full re-extraction, not an append).

### 5. Write the refresh JSON and call watchdog write-entity

Build the extraction JSON:

```json
{
  "entity_id": "<id>",
  "summary": "<refreshed summary>",
  "timeline_events": [
    {
      "date": "<YYYY-MM-DD, YYYY-MM, or YYYY>",
      "event": "<what happened>",
      "source_sha256": "<sha256>",
      "page": <n or null>,
      "confidence": "<level>"
    }
  ]
}
```

Write it to `/tmp/entity-refresh-<entity-id>.json` using the Write tool, then run:

```bash
watchdog write-entity --entity-id <entity-id> --extraction /tmp/entity-refresh-<entity-id>.json
```

Clean up:

```bash
rm /tmp/entity-refresh-<entity-id>.json
```

`watchdog write-entity` rewrites the entity note and rebuilds `timeline.md`. The `## Analysis` and `## Notes` sections are preserved.

Print a completion line after each entity:
```
Refreshed: <name> — <N> timeline events
```

---

## After all entities are processed

Print a summary:
```
Entity refresh complete: <N> entities updated
timeline.md rebuilt
```

If any entity IDs were not found, list them:
```
Not found: <id1>, <id2>
```
