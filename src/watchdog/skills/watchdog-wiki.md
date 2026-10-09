---
description: Create or update investigation thread pages in wiki/ — narrative syntheses of specific investigative angles that deepen over time
argument-hint: "[angle or entity]"
---

# /watchdog-wiki — Investigation thread pages

Create or update investigation thread pages in `wiki/`. Each thread is a narrative synthesis of what the evidence shows about a specific investigative angle — a place for the journalist's working theory to accumulate as documents arrive.

Run on demand, or after a major ingest batch when new connections were found.

---

## 0. Read investigation context

Read `context.md` if it exists. This tells you what the journalist is pursuing, what questions they are trying to answer, and what entities they already know are relevant. Use it to prioritise which angles deserve thread pages and what open questions to surface. If `context.md` is empty or missing, proceed without it.

---

## 1. Load the vault

Read the lightweight index files first with the Read tool — these are small and give you a complete picture without loading every note:

- `.watchdog/registry/manifest.json` — the entity index: id, name, type, aliases, note_path
- `.watchdog/registry/documents.json` — the document index: sha256 → title, document_type, entities_extracted, page_count, document_note

Read all briefing notes (these are small and inform which angles are already active) — list them with the Glob tool (`briefings/**/*.md`).

Read existing thread pages — list them with the Glob tool (`wiki/**/*.md`).

Do **not** load all entity notes or document notes upfront. Read individual notes on demand as you identify angles worth a thread. Use `note_path` from the manifest and `document_note` from documents.json to read specific notes when needed.

For each central entity you decide to write about, read its note's `## Summary (AI-written)` section — a model's overview of who the entity is and their significance — and check what you use against the `## Facts` list below it, which cites each document and page.

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

<Synthesized narrative. What do the documents collectively establish? Write in plain prose. Cite each fact by linking to its line in its document note: "John Doe is listed as director of Shell Co ([[documents/shell-co-annual-report-2023#^f-3a9c51d0e2|p. 3]])" — not just a link, a sentence with a purpose. State what is established, not just what was found.>

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
- **Cite facts by their block id.** Every fact line in an entity note's `## Facts` and a document note's `## Key facts` ends in a block id (`^f-3a9c51d0e2`). Cite a fact as `[[documents/<slug>#^f-<id>|p. N]]`, linking to the **document** note, with the id copied exactly from the fact line (or from `watchdog search --json`'s `facts[].cite`). Never invent an id: Watchdog checks every citation and shows one it cannot find as "source not found". A fact the journalist marked disputed may be cited, described as disputed, never as established. Framing sentences may stand uncited; a name, date, figure or event from a document carries a citation. A summary is not a source: cite the facts it rests on, not the `## Summary (AI-written)`.
- **Check before you finish.** Run `watchdog check-citations wiki/<slug>.md` for each thread you wrote, and fix anything it reports as not found.
- **Keep threads focused.** One angle per thread. If an angle splits into two distinct questions, create two threads.
- **Preserve journalist annotations.** The `## Notes` section is sacred — never overwrite it, even on update.
- **Threads are not briefings.** Briefings are point-in-time snapshots after a single ingest. Threads accumulate across the entire investigation and deepen over time.
