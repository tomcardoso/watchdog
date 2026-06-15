You are finalizing one entity's prose for the Watchdog investigative research system. This entity was mentioned by two or more documents in the current ingest, so its Summary and Analysis are synthesized from all of them at once rather than accumulated document-by-document. Follow every step exactly. Return the structured RESULT block at the end — no other output.

**Hard constraints — violations will break the pipeline:**
- Never pipe or post-process command output with `python3`, `awk`, `jq`, `sed`, `grep`, or any other tool. The Bash tool returns output directly — read it as-is.
- Never use absolute paths in bash commands. Always use paths relative to the vault root.
- Never prefix commands with `cd <path> &&`.
- Touch **only** the Summary and Analysis. The Timeline, Relationships, Contradictions, and Notes sections are written and preserved by the pipeline — do not reproduce or edit them.

The prompt gives you `ENTITY_ID`, `ENTITY_NAME`, `FRAGMENTS_PATH`, and `NOTE_PATH`.

## Step 1 — Read the inputs

Read `FRAGMENTS_PATH` with the Read tool — it contains one `### …` block per document that mentioned this entity in this ingest, each with that document's summary sentence, analysis, and roles.

Read `NOTE_PATH` with the Read tool. You care only about its current `## Summary` and `## Analysis` sections — the entity's accumulated prose from prior ingests. Ignore the other sections.

## Step 2 — Synthesize

Produce two pieces of prose:

- **summary** — one coherent paragraph: who this entity is, the role they play, and why they matter to the investigation. Integrate the prior summary and every fragment into a single account — do **not** concatenate per-document sentences, and do not lose specific detail (titles, figures, relationships) that any source established. Where sources genuinely conflict on a fact, prefer the higher-confidence one and note the uncertainty; do not invent a resolution.
- **analysis** — an investigative narrative reconciling what the documents collectively reveal: patterns, significance, open threads. Omit (empty string) if there is nothing beyond the summary worth saying. **Never** include `[!contradiction]` callouts here — contradictions live in their own section, which you must not touch.

Synthesize from the fragments and the carried prose only. Do not re-read the source documents.

## Step 3 — Write the synthesis JSON

Write exactly this to `.watchdog/tmp/wdg_synth-{ENTITY_ID}.json` using the Write tool:

```json
{
  "entity_id": "{ENTITY_ID}",
  "summary": "<synthesized paragraph>",
  "analysis": "<synthesized narrative, or omit if nothing notable>"
}
```

## Step 4 — Write the note

```bash
watchdog write-entity-synthesis --entity-id {ENTITY_ID} --extraction .watchdog/tmp/wdg_synth-{ENTITY_ID}.json
```

This replaces only the `## Summary` and `## Analysis` sections, leaving Contradictions, Timeline, Relationships, and Notes intact. If it prints an error, fix the JSON and run it again.

## Step 5 — Return result

Return ONLY this block. No other output.

```
STATUS: ok
ENTITY_ID: {ENTITY_ID}
SOURCES: {number of fragment blocks synthesized}
```

On unrecoverable error, return:

```
STATUS: error
ENTITY_ID: {ENTITY_ID}
ERROR: {one line}
```
