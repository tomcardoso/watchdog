You are extracting **one page-range section** of a large document for the Watchdog research system. Long documents are split into sections that are processed in reading order; you carry a running scratchpad forward so later sections stay consistent with earlier ones. Follow every step exactly. Return the compact RESULT block at the end — no other output.

**Hard constraints — violations will break the pipeline:**
- Never pipe or post-process command output with `python3`, `awk`, `jq`, `sed`, `grep`, or any other tool. The Bash tool returns output directly — read it as-is.
- Never use absolute paths in bash commands. Always use paths relative to the vault root.
- Never read `.watchdog/Registry/manifest.json`, `entities.json`, or `documents.json` directly — entity candidates come from PRE_FLIGHT.existing_entities (Step 1).
- Never prefix commands with `cd <path> &&`. Never run any `--help` or exploration command.
- **You do NOT write to the vault.** No `post-flight`, no entity/document notes, no timeline files. You only write your section JSON and append to the scratchpad. The merge + post-flight happen after all sections finish.

**Stop conditions (runaway guard).** Finish in a bounded number of steps. If you cannot make progress — pre-flight errors, your section file is empty or too garbled to extract, or you are repeating the same action without new information — **stop instead of looping** and return the FAILED block (Step 9). You write no vault state, so there is nothing to undo; the orchestrator runs `watchdog ingest-abort` to clear this document's section files and aborts the rest of the document.

**Inputs (from the prompt):** `SHA256`, `FILENAME`, `DOMAIN_SKILL_PATH`, `SECTION_INDEX` (`i of N`), `SECTION_LABEL` (e.g. `pages 12–34` or `part 2 of 5`), `SECTION_PAGES_PATH`, `SCRATCHPAD_PATH`, `OUTPUT_PATH`, `INVESTIGATION_BRIEF`.

---

## Step 1 — Pre-flight (vault context)

```bash
watchdog pre-flight {SHA256}
```

Read the stdout directly. Use only these fields (ignore `pages_path` — you read your section file instead):
- `already_extracted` — **only if `SECTION_INDEX` is 1**, and it is true, stop immediately and return the SKIPPED block. (Later sections: ignore.)
- `near_dup.near_duplicates`, `near_dup.top_similarity`
- `existing_entities[]` — vault entities with `{id, name, type, aliases, timeline_events, roles, analysis}`. Match against these for vault dedup (use the existing `id`) and for the contradiction check (Step 6).

## Step 2 — Read the scratchpad (carry-forward)

If `SCRATCHPAD_PATH` exists, read it. Its **Entities so far** block lists `id | name | type | aliases` for entities earlier sections already extracted — reuse those ids, and resolve back-references in your section (e.g. "the Company") against those names/aliases. Its **Observations** are prior salient notes. If the file does not exist (you are section 1), there is no carry-forward yet.

## Step 3 — Read your section

Read `SECTION_PAGES_PATH` in a single Read. A **paginated** section contains `<!-- PAGE N -->` markers — cite those page numbers in `page`. A **non-paginated** section (from a `.txt`/`.csv`/`.md` source) is a plain text slice with no markers — set `page` to `null` in every citation for this section. Overlapping content may repeat from the adjacent section — that is expected; the merge deduplicates.

## Step 4 — Load domain skill

If `DOMAIN_SKILL_PATH` is not `"none"`, read `.claude/commands/{DOMAIN_SKILL_PATH}` and use it. Otherwise scan your pages, identify the document type, and read the closest match in `.claude/commands/records/` (or `general-records.md`).

## Step 5 — Extract entities (this section only)

Extract every real-world entity that appears in your page range. Assign ids consistently:
- matches a `PRE_FLIGHT.existing_entities` entry → use that entry's `id` (vault dedup)
- matches an entry in the scratchpad's **Entities so far** → use that id (cross-section consistency)
- otherwise → a new kebab-case slug

For each entity: `id`, `name` (most complete form), `type`, `aliases` (every other surface form in this section, including resolved back-references like "the Company"), `timeline_events` (`date`, `event`, `page`, `confidence`), `roles` (`relationship`, `target_id`, `target_type`, `target_name`, `page`, `confidence`, `date_range`).

Confidence: `high` (directly stated) / `medium` (one inference) / `low` (multi-statement inference) / `disputed` (contradicts the vault). Never upgrade a claim past its weakest element. **Do not set `match_id`** — ids are already canonical.

## Step 6 — Key facts and contradictions

Pull the most important facts from your section (`fact`, `page`, `confidence`). For any entity matching `PRE_FLIGHT.existing_entities`, compare against that entry's `timeline_events` / `roles` / `analysis`; emit a `[!contradiction]` callout in the entity's `analysis` only when you are confident the discrepancy is genuine (this is the sole verification step — no later pass removes false positives). Do not re-flag contradictions already in `analysis`.

## Step 7 — Write your section JSON

Write to `OUTPUT_PATH` exactly this shape (a partial extraction — your section's contribution):

```json
{
  "document": {
    "sha256": "<SHA256>", "filename": "<FILENAME>",
    "original_path": "<source_path from queue JSON, section 1 only — else omit>",
    "title": "<inferred, section 1 only — else omit>",
    "document_type": "<inferred, section 1 only — else omit>",
    "date_of_document": "<YYYY-MM-DD or null, section 1 only — else omit>",
    "page_count": <total document page count, section 1 only — else omit>,
    "summary": "<one paragraph for your section>",
    "key_facts": [{"fact": "...", "page": <n or null>, "confidence": "..."}]
  },
  "entities": [
    {"id": "...", "name": "...", "type": "...", "aliases": [...],
     "summary": "<one sentence>", "analysis": "<contradictions/notes; omit if none>",
     "timeline_events": [{"date": "...", "event": "...", "page": <n or null>, "confidence": "..."}],
     "roles": [{"relationship": "...", "target_id": "...", "target_type": "...", "target_name": "...", "page": null, "confidence": "...", "date_range": null}]}
  ],
  "morgue_entity_id": "<the entity this document is *about* — section 1 only, else omit>",
  "morgue_document_type": "<type slug — section 1 only, else omit>"
}
```

The merge takes document metadata and `morgue_*` from the first section that provides them, so **section 1 must fill them in**; later sections may omit those fields and just supply `entities` + `document.key_facts` + `document.summary`.

## Step 8 — Update the scratchpad

Append to `SCRATCHPAD_PATH` (create it if you are section 1) so the next section inherits your context. Keep this structure — append, don't rewrite prior content:

```markdown
# Running notes — {FILENAME}

## Entities so far
- <id> | <name> | <type> | <aliases, comma-separated>

## Observations
- p.<N>: <something that stuck out — a figure, a relationship, an anomaly, a "see Note X" cross-reference>
```

Add any entities new to this section to **Entities so far**, and your salient notes to **Observations**. The Observations become the document's briefing notes, so keep them high-signal — what a reporter would jot down, not a summary.

## Step 9 — Return result

Return ONLY:

```
STATUS: ok
SECTION: {SECTION_INDEX}
ENTITY_COUNT: {entities extracted in this section}
NEAR_DUP: {top_similarity% similar to {filename} — or none}
CONTRADICTIONS: {entity_id — brief; … — or none}
```

Or, if section 1 and already extracted:

```
STATUS: skipped
REASON: already extracted (SHA-256 match)
```

Or, if a stop condition fired:

```
STATUS: failed
SECTION: {SECTION_INDEX}
REASON: <one line — why you stopped>
```
