# /watchdog-wiki — Investigation thread pages

Create or update investigation thread pages in `wiki/`. Each thread is a narrative synthesis of what the evidence shows about a specific investigative angle — a place for the journalist's working theory to accumulate as documents arrive.

Run on demand, or after a major ingest batch when new connections were found.

---

## 0. Read investigation context

Read `context.md` if it exists. This tells you what the journalist is pursuing, what questions they are trying to answer, and what entities they already know are relevant. Use it to prioritise which angles deserve thread pages and what open questions to surface. If `context.md` is empty or missing, proceed without it.

---

## 1. Load the vault

Read the lightweight index files first — these are small and give you a complete picture without loading every note:

```bash
# Entity index: id, name, type, aliases, note_path
cat .watchdog/Registry/manifest.json

# Document index: sha256 → title, document_type, date, entities_mentioned, page_count, document_note
cat .watchdog/Registry/documents.json
```

Read all briefing notes (these are small and inform which angles are already active):
```bash
find briefings/ -name "*.md" | sort
```

Read existing thread pages:
```bash
find wiki/ -name "*.md" | sort
```

Do **not** load all entity notes or document notes upfront. Read individual notes on demand as you identify angles worth a thread. Use `note_path` from the manifest and `document_note` from documents.json to read specific notes when needed.

For each central entity you decide to write about, read its note to get the `## Summary` section — this is the synthesized overview of who the entity is and their significance.

---

## 2. Identify angles worth a thread

An angle is worth a thread page if there are at least two entities connected by at least two documents, or if a briefing has flagged an anomaly involving multiple entities.

Look for:

- **Entity clusters** — two or more entities that share an address, a director, a registered agent, or a transaction
- **Cross-document patterns** — an entity or relationship that appears in three or more documents
- **Anomalies** — anything flagged in briefings: disproportionate transactions, unexpected roles, dormant entities reactivated
- **Unresolved questions** — near-duplicates kept as separate documents, deferred clarifying questions from prior ingests

For each angle, determine:
- A short descriptive title (e.g. "Shared address — Shell Co and XYZ Holdings")
- Which entities are central to it
- Which documents establish the key facts
- What is established, what is ambiguous, what is missing

If `$ARGUMENTS` names a specific angle or entity, focus only on that. Otherwise produce threads for all angles above the threshold.

---

## 3. For each angle — create or update thread page

The thread slug is the angle title lowercased and hyphenated: `shared-address-shell-co-xyz-holdings`.

**If `wiki/<slug>.md` already exists:**
- Read it
- Preserve everything in `## Notes` exactly as-is
- Update `## What the evidence shows` if new documents or entities have arrived since the last update
- Update `## Open questions` — close any questions now resolved, add any new ones
- Update `last_updated` in frontmatter
- Update `entities` and `documents` lists if new ones are now relevant

**If the thread is new:**
- Create `wiki/<slug>.md`:

```yaml
---
id: <slug>
title: <angle title>
type: InvestigationThread
entities:
  - "[[entities/<type>/<id>|Entity Name]]"
  - ...
documents:
  - "[[documents/<slug>|Document Title]]"
  - ...
created: <today>
last_updated: <today>
---

## What the evidence shows

<Synthesized narrative. What do the documents collectively establish? Write in plain prose. Cite inline: "John Doe is listed as director of Shell Co ([[documents/shell-co-annual-report-2023|Shell Co Annual Report 2023]], p. 3)" — not just a link, a sentence with a purpose. State what is established, not just what was found.>

## Open questions

<What is unresolved: a missing document that would confirm or deny a connection, an entity whose identity is ambiguous, a date gap, a transaction with no known counterparty. Each question should be one sentence stating what is unknown and why it matters.>

## Notes

<!-- Journalist annotations — never overwritten. -->
```

---

## 4. Report

Print a summary:

```
Wiki update — <date>
====================
Threads created:  <n>
Threads updated:  <n>

<list of thread titles with path, one line each>

Run /watchdog-surface for a fresh connection analysis.
```

---

## Guidelines

- **Never speculate.** State what the evidence shows; label inferences explicitly ("this may indicate", "consistent with"). The thread is a working theory, not a conclusion.
- **Cite everything.** Every factual claim in "What the evidence shows" must link to an entity note or document note.
- **Keep threads focused.** One angle per thread. If an angle splits into two distinct questions, create two threads.
- **Preserve journalist annotations.** The `## Notes` section is sacred — never overwrite it, even on update.
- **Threads are not briefings.** Briefings are point-in-time snapshots after a single ingest. Threads accumulate across the entire investigation and deepen over time.
