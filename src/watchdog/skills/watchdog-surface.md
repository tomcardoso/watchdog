---
description: Full connection and anomaly analysis across the vault — shared addresses, director overlaps, clusters, contradictions — reported to briefings/
---

# /watchdog-surface — Find connections and anomalies across the vault

Perform a full connection and anomaly analysis across every entity and document in the vault. Surface things the journalist may have missed.

Run this on demand. It is computationally expensive on large vaults; run after major ingest batches.

---

## 0. Read investigation context

Read `context.md` if it exists. This tells you what the journalist is pursuing and what questions they are trying to answer. Use it to prioritise which connections and anomalies to surface — findings that speak directly to the stated questions or known entities go first. If `context.md` is empty or missing, proceed without it.

---

## 1. Load the vault

Read these files to build your working index:

1. **`.watchdog/Registry/manifest.json`** — every entity's `id`, `name`, `type`, `aliases`, `note_path`. This is your entity directory. Do not read individual entity notes yet.
2. **`.watchdog/Registry/documents.json`** — every document's `sha256`, `filename`, `title`, `document_type`, `entities_extracted`, `page_count`, `document_note`.
3. **`timeline.md`** — global chronological view of all events across all entities.

Build a working index in memory:
- Entity ID → type, name, aliases, note_path (from manifest)
- Document → document_type, entities_extracted, document_note (from documents.json; a document's own date is the `date_of_document` frontmatter field in its note)

**Read individual entity notes on demand** — only when a specific analysis step requires the full `## Summary`, `## Timeline`, `## Analysis`, or `## Relationships` content. Do not read all notes upfront.

---

## 2. Connection analysis

### Shared addresses

Find every Address entity. For each, find all other entities that share it:
```bash
grep -r "<address-id>" entities/ --include="*.md" -l
```

Flag any address shared by entities that have no other apparent connection — especially if those entities are companies or have different registered agents.

### Shared directors / officers

Find every Person entity with a `Director` or `Officer` role. Find all companies they appear in. Flag any person who:
- Appears as director of 3 or more companies
- Appears in a role inconsistent with their prior appearances (e.g. previously only as a plaintiff, now as a director)

### Deterministic leads

Run `watchdog leads` and fold its output into the report — it already computes entities named but never profiled, entities recurring but unconnected, and unresolved contradictions from the entity graph. Do not re-derive these by hand; spend the analysis effort on the connection patterns above and below, which code cannot judge.

### Company clusters

Find groups of companies that share 2 or more of: the same address, the same director, the same registered agent, the same filing date. Flag any cluster larger than 2.

### Timeline anomalies

Read each entity's `## Timeline` section and the global `timeline.md`. Look for:
- An entity that appears in a document dated significantly earlier than `date_first_seen` in the registry (may indicate a missed prior document)
- A company formed or dissolved within 30 days of a large transaction involving it
- Clusters of events from multiple entities that all fall within a narrow date window — these often indicate a coordinated action worth examining

---

## 3. Contradiction scan

Scan every entity note in `entities/` for `[!contradiction]` callouts. These are inserted by the ingest pipeline when a new document contradicts an existing fact.

For each callout found:
- Record the entity, the disputed fact, both values, and both source documents
- Note the basis (`stated` vs `inferred`) of each claim

Also perform a cross-document scan for contradictions not yet flagged:

For each entity that appears in 3 or more documents, compare the following fields across all documents it appears in (read from the `## Timeline` and `## Relationships` sections of the entity note, and from the source documents if needed):
- Date of incorporation / formation / registration
- Registered address or business address
- Director or officer appointments (is the person listed as the same role in all documents?)
- Transaction amounts (does the same transaction appear with different values in different documents?)

Flag any case where the same fact is stated differently in two documents — both values directly stated (not `inferred`) — and that discrepancy is not already captured in a `[!contradiction]` callout.

**Do not write `[!contradiction]` callouts into entity notes.** Entity notes are pipeline-owned: callouts are verified at extraction time and tracked by the resolutions layer (`watchdog resolve` / `unresolve`), and hand-inserted ones bypass both. Report newly found discrepancies in the surface report only, labelled as **candidate contradictions** so the journalist can verify them against the sources.

Include all contradictions (pre-existing callouts and new candidates, labelled as such) in the surface report under a dedicated section. For each **candidate**, ask the journalist whether to promote it now. If they explicitly confirm, run `watchdog contradiction-add` yourself from the terminal with that candidate's values and report success/failure in the session output. If they do not confirm, leave it as a candidate in the report only.

---

## 4. Anomaly analysis

### Disproportionate transactions

Find all Transaction entities. Compare their amounts to the apparent scale of the entities involved (revenue, assets mentioned in nearby documents). Flag any transaction that is more than 2x the annual revenue of either party, or that involves round numbers with no stated purpose.

### Dormant entities in active documents

Find entities that have `date_first_seen` more than 3 years ago but appear in a recently ingested document. This may indicate a dormant entity being reactivated — worth investigating.

### Documents with no extracted entities

Find document notes where `entities_mentioned` is empty or null. These documents were ingested but not fully extracted — they may need re-ingestion.

### Entities mentioned in documents but missing entity notes

Cross-reference `entities_mentioned` in document frontmatter against the `entities/` directory. Any wiki link that doesn't resolve to an actual file is a gap.

---

## 5. Write a surface report

Write to `briefings/surface-<YYYY-MM-DD>.md`:

```markdown
---
date: <ISO 8601>
type: surface-report
entity_count: <n>
document_count: <n>
---

# Surface report — <date>

## Contradictions

<For each contradiction — pre-existing callouts, and new candidates found by this scan labelled **(candidate — verify against sources)**:>

### <Entity name> — <disputed fact>
- **[[entities/<type>/<id>|Entity Name]]**
- <Value A> — [[documents/<slug>|Document Title]], p. <n>
- <Value B> — [[documents/<slug>|Document Title]], p. <n>
- **Suggested follow-up:** <what would resolve this discrepancy>
- **Promotion status (candidates only):** <left as candidate | promoted via watchdog contradiction-add>

<If no contradictions found: "No contradictions found.">

## Connections found

<For each significant connection discovered:>

### <Connection title>
- **Entities involved:** [[entities/person/entity-id|Entity Name]], [[entities/company/entity-id|Company Name]]
- **Nature of connection:** <what they share or how they relate>
- **Documents:** [[documents/doc-slug|Doc Title]] (p. X), [[documents/doc-slug|Doc Title]] (p. Y)
- **Why it matters:** <one sentence on investigative significance>

## Anomalies

<For each anomaly:>

### <Anomaly title>
- **Entity:** [[entities/<type>/<id>|Entity Name]]
- **What's unusual:** <specific description>
- **Source:** [[documents/<slug>|Document Title]] (p. N)
- **Suggested follow-up:** <one concrete next step>

## Leads and follow-up ideas

<Actionable leads the full vault suggests, typed and cited:>

- **[Question]** <Open question the vault raises but can't answer.> *Source: [[entities/<type>/<id>|Name]] or [[documents/<slug>|Title]]*
- **[Contact]** <Person or entity worth reaching out to, and why.>
- **[Document]** <Specific document that appears to exist but isn't in the vault.>
- **[FOI]** <Records request worth filing, based on a gap in the evidence.>

If context.md is filled in, prioritise leads that speak directly to the journalist's stated questions.

## Gaps in the vault

<Entities or relationships that appear to be missing:>
- <gap description>

---

*Run `/watchdog-query` to dig into any of these. Run `/watchdog-health` to check vault integrity.*
```

Print a summary to the terminal: contradiction count, connection count, anomaly count, gap count.

---

## Guidelines

- **Cite everything.** Every finding must link to a specific entity note and document.
- **Don't speculate.** Flag what the data shows; don't invent explanations.
- **Distinguish levels of certainty.** "Shares an address" is a fact. "May be a shell company" is an inference — label it as such.
- **Be brief.** The report is a prompt for investigation, not a comprehensive analysis. Each finding should be one short paragraph.
