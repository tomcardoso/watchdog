You are finalizing one Watchdog ingest batch. Two jobs: (1) reconcile the timeline, (2) write the post-ingest briefing. Follow every step below exactly. Return ONLY the confirmation block at the end — no other output.

**Hard constraints — violations will break the pipeline:**
- Never pipe or post-process command output with `python3`, `awk`, `jq`, `sed`, `grep`, or any other tool. The Bash tool returns output directly — read it as-is.
- Never use absolute paths in bash commands. Always use paths relative to the vault root.
- Never prefix commands with `cd <path> &&`.
- Never run `watchdog <command> --help` or any exploration command.
- Read vault files with the Read tool, not bash.

**Stop conditions (runaway guard).** Finish in a bounded number of steps. If a step can't complete — a collision file won't parse, a scratchpad won't read — skip that item and move on rather than retrying it repeatedly. Always reach Step 4 and return; write the briefing with whatever you have. Do not loop. (There is no vault state to undo here — the briefing and timeline are regenerable.)

**Inputs (supplied in the prompt):**
- `INVESTIGATION_BRIEF` — condensed investigation context. Use this instead of reading `context.md`.
- `RESULTS` — one compact metadata block per successfully extracted document (filename, type, date, entity counts, new/updated entity ids).
- `NEARDUP_ALERTS` — near-duplicate alerts (may be empty).
- `CONTRADICTION_FLAGS` — contradictions flagged during extraction (may be empty).

Source the narrative detail from the scratchpads (Step 2), not from `RESULTS` — `RESULTS` carries machine-readable metadata only.

---

## Step 1 — Timeline reconciliation

Run:
```bash
watchdog timeline-collisions
```

Read the JSON array directly from the Bash output. The tool has already promoted pending raw files (dates with no prior canonical) to canonical. The array contains only **collision objects**: dates where a canonical already existed and new raw files were added this session.

For each collision object `{"date": "...", "canonical": "...", "raw": [...]}`:

1. Read the canonical file (all NDJSON lines) with the Read tool.
2. Read each raw file listed in `raw` (all NDJSON lines) with the Read tool.
3. Combine all event lines for this date. Identify semantic duplicates — events describing the same real-world occurrence even if worded differently. Remove the duplicate, keeping the more precise wording.
4. Write the deduplicated event list back to the canonical file, one JSON object per line, with the Write tool. Leave the raw file(s) in place as an audit trail.

If the array is empty, skip the dedup. Track the number of collisions you deduplicated for the return block.

Then run:
```bash
watchdog rebuild-timeline
```

This renders `timeline.md` from all canonical `.watchdog/timeline/{date}.ndjson` files.

---

## Step 2 — Read scratchpads

Read all subagent scratchpads from `.watchdog/tmp/notes_*.md` — one per successfully extracted document. These hold the high-signal detail (key figures, leads, contradictions, chronological context) the compact `RESULTS` blocks cannot carry. Build the briefing from the scratchpads, `RESULTS`, `NEARDUP_ALERTS`, `CONTRADICTION_FLAGS`, and `INVESTIGATION_BRIEF`. Do not read queue files or entity records.

---

## Step 3 — Write the briefing

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

If `INVESTIGATION_BRIEF` is non-empty, orient leads toward the journalist's stated questions. Omit this section if nothing warrants a lead.

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

<One sentence on where the investigation stands, drawing on INVESTIGATION_BRIEF and what was just ingested.>

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

- **Files:** <n> processed
- **New entities:** <n> (<n> new, <n> updated)
- **Briefing:** [[briefings/<briefing-slug>|<date>]]
<If CONTRADICTION_FLAGS is non-empty:>
- **Contradictions flagged:** <n> — see entity notes for details
```

---

## Step 4 — Return result

Return ONLY the following block. No other output.

```
STATUS: ok
BRIEFING: briefings/<briefing-slug>.md
FILES: <n>
TIMELINE: <n collisions deduplicated, or none>
```
