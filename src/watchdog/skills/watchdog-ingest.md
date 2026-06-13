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

Set `EXTRACTOR_MODEL = INGEST.extractor_model` if present, else `"sonnet"`.

Set `QUEUE_FILES = INGEST.queue_files`. Set `ARROWS_FILES = INGEST.arrows_files`.

The lock is held (acquired by `watchdog ingest`). Every exit path — including errors — must release it by running `watchdog unlock`. That command removes the lock, deletes `ingest-state.json`, and cleans up temp files.

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
2. Print `[<N>/<TOTAL>] Launching: <FILENAME clamped to 50 chars> ...`
3. **Launch all agents in the batch simultaneously** — send a single message with all Agent tool calls in parallel. Set each Agent's `description` to `Watchdog extraction: <FILENAME clamped to 50 chars>` and `model` to `EXTRACTOR_MODEL`.
4. Process results (see "After each Agent call" below).

**Limit check:** if `LIMIT` is set and `EXTRACTED >= LIMIT`, stop before the next batch.

Substitute all `{placeholder}` values before sending.

---

### SUBAGENT PROMPT TEMPLATE

```
Read `.claude/commands/watchdog-ingest-subagent.md` for full extraction instructions. Then extract this document:

SHA256: {SHA256}
FILENAME: {FILENAME}
DOMAIN_SKILL_PATH: {DOMAIN_SKILL_PATH}
INVESTIGATION_BRIEF:
{INVESTIGATION_BRIEF}
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

## 6. Timeline reconciliation

Run:
```bash
watchdog timeline-collisions
```

Read the JSON output directly from the Bash tool — it is a JSON array. The tool has already promoted any pending raw files (dates with no prior canonical) to canonical. The array contains only **collision objects**: dates where a canonical already existed and new raw files were added in this ingest session.

For each collision object `{"date": "...", "canonical": "...", "raw": [...]}`:

1. Read the canonical file (all NDJSON lines) using the Read tool.
2. Read each raw file listed in `raw` (all NDJSON lines) using the Read tool.
3. Combine all event lines for this date. Identify semantic duplicates — events describing the same real-world occurrence even if worded differently. Remove the duplicate, keeping the more precise wording.
4. Write the deduplicated event list back to the canonical file, one JSON object per line, using the Write tool. Leave the raw file(s) in place as an audit trail.

If the array is empty, skip the dedup pass.

Then run:
```bash
watchdog rebuild-timeline
```

This renders `timeline.md` from all canonical `.watchdog/timeline/{date}.ndjson` files.

---

## 7. arrows.app import

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

## 8. Post-batch contradiction resolution

For each entry in `CONTRADICTION_FLAGS`:
- Read the entity note (`entities/{type_lowercase}/{id}.md`)
- Verify the contradiction is genuine, not a subagent false positive
- If false positive: edit the note to remove the `[!contradiction]` callout that `write-vault` wrote

This step reads at most a handful of entity notes — typically 0–3 per batch.

---

## 9. Post-ingest briefing

Print a batch summary:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Ingest complete: <extracted> processed, <skipped> skipped, <failed> failed
Total time: <elapsed>s
Entities in vault: <total from registry.json>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

Read all subagent scratchpads from `.watchdog/tmp/notes_*.md` — one per successfully extracted document. These contain the high-signal detail (key figures, leads, contradictions, chronological context) that the subagent's compact RESULT block cannot carry. Build the briefing from the scratchpads, `RESULTS` metadata, `NEARDUP_ALERTS`, `CONTRADICTION_FLAGS`, and `INVESTIGATION_BRIEF`. Do not re-read queue files or entity records.

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

## 10. Release lock

Run `watchdog unlock` to remove `.watchdog/Registry/.ingest-lock`, delete `ingest-state.json`, and clean up temp files.

---

## 11. Clarifying questions (optional)

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
