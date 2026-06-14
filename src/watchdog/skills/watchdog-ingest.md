# /watchdog-ingest — Watchdog document extraction pipeline

Extract queued files from `.watchdog/queue/` into the vault.

Chewing (OCR, Docling) is handled separately by the `watchdog chew` CLI command. This skill only runs extraction — reading queued results and writing entity notes, document notes, and registry updates.

**Argument parsing** — parse `$ARGUMENTS` before doing anything else:
- If `$ARGUMENTS` is empty: `LIMIT = null`
- If `$ARGUMENTS` matches `--limit <N>` (e.g. `/watchdog-ingest --limit 50`): `LIMIT = N`

`LIMIT` caps how many files are **extracted** this run. When the limit is reached, stop cleanly and report how many files remain.

**Architecture note:** Each document is extracted in an isolated Agent subagent. This keeps the orchestrator context flat regardless of batch size — queue file text, skill files, and extraction output never accumulate in this session. Timeline reconciliation and the post-ingest briefing are likewise delegated to a single finalize subagent, so scratchpad prose and timeline NDJSON never enter this session. The orchestrator holds only: the investigation brief, a compact entity index (id/name/type/aliases), and a running list of compact per-document result blocks.

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

Set `FINALIZER_MODEL = INGEST.finalizer_model` if present, else `"sonnet"`.

Set `SECTION_TOKEN_THRESHOLD = INGEST.section_token_threshold` if present, else `120000`.

Set `QUEUE_FILES = INGEST.queue_files`.

The lock is held (acquired by `watchdog ingest`). Every exit path — including errors — must release it by running `watchdog unlock`. That command removes the lock, deletes `ingest-state.json`, and cleans up temp files.

---

## 1. Read investigation context

Read `context.md` if it exists. Condense it to an **investigation brief** of at most 300 words — the key entities of interest, the research questions, and any known gaps. Store as `INVESTIGATION_BRIEF`. If `context.md` is missing or empty, set `INVESTIGATION_BRIEF = ""`.

Read `hot.md` if it exists. Note current investigation state.

---

## 2. Resolve skill paths

For each entry in `QUEUE_FILES`, set `DOMAIN_SKILL_PATH` = `records/<document_type>.md` if `document_type` is non-null, else `"none"`. No file reads at this stage — the skill is read by the subagent.

Then **sort `QUEUE_FILES` by `document_type`** (nulls last). Files sharing a `document_type` will be batched together, keeping the same domain skill file in the prompt cache across consecutive subagent spawns.

---

## 3. Process each file

Partition `QUEUE_FILES` by estimated size: **LARGE** = files whose `est_tokens` is greater than `SECTION_TOKEN_THRESHOLD`; **NORMAL** = all others (including files with no `est_tokens`). Estimated tokens — not page count — is the trigger, so dense table-heavy reports and large non-paginated files (`.txt`/`.csv`/`.md`, which are a single page) are handled correctly. Process NORMAL files first (§3a), then LARGE files (§3b). The `LIMIT` check applies across both — if `LIMIT` is set and `EXTRACTED >= LIMIT`, stop before starting the next batch or document.

### 3a. Normal documents — parallel extraction

Process NORMAL files in **batches of up to 5**. Registry writes are serialized internally — concurrent subagents are safe.

Split the NORMAL files (already sorted by `document_type`) into batches of at most 5 files. For each batch:

1. For each file in the batch, get its `SHA256`, `FILENAME`, and `DOMAIN_SKILL_PATH` (already resolved in §2).
2. Print `[<N>/<TOTAL>] Launching: <FILENAME clamped to 50 chars> ...`
3. **Launch all agents in the batch simultaneously** — send a single message with all Agent tool calls in parallel. Set each Agent's `description` to `Watchdog extraction: <FILENAME clamped to 50 chars>` and `model` to `EXTRACTOR_MODEL`.
4. Process results (see "After each Agent call" below).

**Limit check:** if `LIMIT` is set and `EXTRACTED >= LIMIT`, stop before the next batch.

Substitute all `{placeholder}` values before sending.

---

### SUBAGENT PROMPT TEMPLATE

The stable prefix (the instruction to read the skill file) must remain byte-identical across all spawns so the Anthropic prompt cache can absorb it. Per-document values (`SHA256`, `FILENAME`, `DOMAIN_SKILL_PATH`, `INVESTIGATION_BRIEF`) always appear at the end.

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

### 3b. Large documents — sequential sectioned extraction

Each LARGE document is too big to extract in one context, so it is split into overlapping page-range sections, extracted **in reading order** with a running scratchpad carried forward, then merged deterministically. Process LARGE files **one at a time**.

For each LARGE file (`SHA256`, `FILENAME`, `DOMAIN_SKILL_PATH` from §2):

1. Run `watchdog section-plan {SHA256}` and read the JSON.
   - If it returns `"sectioned": false`, the file isn't actually large — extract it via §3a instead.
   - Otherwise you have `sections: [{index, label, paginated, pages_path}, …]` (`label` is e.g. `"pages 12–34"` or `"part 2 of 5"`).
2. Extract the sections **strictly in order, one at a time** — launch a section subagent, wait for it to return, then launch the next. Do **not** parallelize them: each section reads the scratchpad the previous one wrote. Use the SECTION SUBAGENT PROMPT TEMPLATE below; set the Agent `description` to `Watchdog section {index}/{count}: <FILENAME clamped to 40 chars>` and `model` to `EXTRACTOR_MODEL`.
   - If **section 1** returns `STATUS: skipped`, the document is already extracted — skip the whole file; do not process the remaining sections.
   - From the section returns, remember `NEAR_DUP` (from section 1) and any `CONTRADICTIONS` (union across sections).
3. After every section has returned, run `watchdog merge-sections {SHA256}` and read the JSON: `extraction_path`, `entity_count`, `new_entities`, `updated_entities`.
4. Run `watchdog post-flight --extraction {extraction_path}`. If it reports `errors`, log to `.watchdog/Registry/ingest.log` and continue to the next document.
5. Record this document in `RESULTS` (entity_count, new_entities, updated_entities, document metadata). If section 1's `NEAR_DUP` is not `none`, add to `NEARDUP_ALERTS`; if any section reported `CONTRADICTIONS`, add to `CONTRADICTION_FLAGS`. Increment `EXTRACTED` by 1. Print:
   ```
   [done] <FILENAME> — <entity_count> entities (<count> sections merged)
   ```

#### SECTION SUBAGENT PROMPT TEMPLATE

Keep the stable prefix byte-identical across spawns; per-section values appear at the end. `OUTPUT_PATH` uses a two-digit zero-padded index (`01`, `02`, …) so `merge-sections` reads the sections in order.

```
Read `.claude/commands/watchdog-ingest-section-subagent.md` for full instructions. Then extract this section.

SHA256: {SHA256}
FILENAME: {FILENAME}
DOMAIN_SKILL_PATH: {DOMAIN_SKILL_PATH}
SECTION_INDEX: {index} of {count}
SECTION_LABEL: {label}
SECTION_PAGES_PATH: {pages_path}
SCRATCHPAD_PATH: .watchdog/tmp/notes_{SHA256}.md
OUTPUT_PATH: .watchdog/tmp/section_ex_{SHA256}_{index:02d}.json
INVESTIGATION_BRIEF:
{INVESTIGATION_BRIEF}
```

The running scratchpad (`notes_{SHA256}.md`) is the same per-document notes file the finalize subagent reads for the briefing — so a sectioned document's briefing notes are produced as a byproduct of carry-forward, with no extra step.

---

## 4. Post-loop: graph colour check

After the loop completes, check whether any new entity type from the results is missing from `.obsidian/graph.json`'s `colorGroups` array. If so, read the file, add a colour entry (`{"query": "path:entities/<type_lowercase>", "color": {"a": 1, "rgb": <24-bit int>}}`), and write it back. Pick a colour visually distinct from existing ones.

---

## 5. Finalize: timeline + briefing

Print the batch summary:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Ingest complete: <extracted> processed, <skipped> skipped, <failed> failed
Total time: <elapsed>s
Entities in vault: <total from registry.json>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

Read `.watchdog/Registry/registry.json` with the Read tool for the entity total used in the banner.

If no documents were successfully extracted (`EXTRACTED == 0`), skip the finalize subagent and continue to §6.

Otherwise launch **one finalize subagent** (a single Agent call) to reconcile the timeline and write the briefing. This keeps scratchpad prose and timeline NDJSON out of this session. Set the Agent's `description` to `Watchdog finalize` and `model` to `FINALIZER_MODEL`. Send this prompt (substitute every `{placeholder}` first):

```
Read `.claude/commands/watchdog-ingest-finalize-subagent.md` for full instructions. Then finalize this ingest batch.

INVESTIGATION_BRIEF:
{INVESTIGATION_BRIEF}

RESULTS:
{RESULTS}

NEARDUP_ALERTS:
{NEARDUP_ALERTS}

CONTRADICTION_FLAGS:
{CONTRADICTION_FLAGS}
```

When it returns, print its `BRIEFING:` path so the user can open it. Do not read the scratchpads, timeline files, or the briefing yourself — the finalize subagent owns those.

---

## 6. Release lock

Run `watchdog unlock` to remove `.watchdog/Registry/.ingest-lock`, delete `ingest-state.json`, and clean up temp files.

---

## 7. Clarifying questions (optional)

After finalizing, if the extraction results surfaced genuine ambiguities that would meaningfully change the entity graph, ask up to 3–5 targeted questions, batched together. Draw these from `RESULTS`, `NEARDUP_ALERTS`, and `CONTRADICTION_FLAGS` — do not re-read documents:
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
