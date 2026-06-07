# /watchdog-surface — Find connections and anomalies across the vault

Perform a full connection and anomaly analysis across every entity and document in the vault. Surface things the journalist may have missed.

Run this on demand. It is computationally expensive on large vaults; run after major ingest batches.

---

## 0. Read investigation context

Read `context.md` if it exists. This tells you what the journalist is pursuing and what questions they are trying to answer. Use it to prioritise which connections and anomalies to surface — findings that speak directly to the stated questions or known entities go first. If `context.md` is empty or missing, proceed without it.

---

## 1. Load the vault

Read all entity notes:
```bash
find entities/ -name "*.md" | sort
```

Read all document notes:
```bash
find documents/ -name "*.md" | sort
```

Read `.watchdog/Registry/entities.json` and `.watchdog/Registry/documents.json` for the structured view.

Build a working index in memory:
- Entity ID → type, name, aliases, roles (from `entities.json`), appears_in
- Document slug → type, date, entities_mentioned

Also read `timeline.md` for a global chronological view of all events.

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

### Recurring names without explicit relationships

Find entities in `entities.json` that appear in 3 or more documents but have an empty `roles` list. These are entities the vault knows about but haven't been mapped into the relationship graph.

### Company clusters

Find groups of companies that share 2 or more of: the same address, the same director, the same registered agent, the same filing date. Flag any cluster larger than 2.

### Timeline anomalies

Read each entity's `## Timeline` section and the global `timeline.md`. Look for:
- An entity that appears in a document dated significantly earlier than `date_first_seen` in the registry (may indicate a missed prior document)
- A company formed or dissolved within 30 days of a large transaction involving it
- Clusters of events from multiple entities that all fall within a narrow date window — these often indicate a coordinated action worth examining

---

## 3. Anomaly analysis

### Disproportionate transactions

Find all Transaction entities. Compare their amounts to the apparent scale of the entities involved (revenue, assets mentioned in nearby documents). Flag any transaction that is more than 2x the annual revenue of either party, or that involves round numbers with no stated purpose.

### Dormant entities in active documents

Find entities that have `date_first_seen` more than 3 years ago but appear in a recently ingested document. This may indicate a dormant entity being reactivated — worth investigating.

### Documents with no extracted entities

Find document notes where `entities_mentioned` is empty or null. These documents were ingested but not fully extracted — they may need re-ingestion.

### Entities mentioned in documents but missing entity notes

Cross-reference `entities_mentioned` in document frontmatter against the `entities/` directory. Any wiki link that doesn't resolve to an actual file is a gap.

---

## 4. Write a surface report

Write to `briefings/surface-<YYYY-MM-DD>.md`:

```markdown
---
date: <ISO 8601>
type: surface-report
entity_count: <n>
document_count: <n>
---

# Surface report — <date>

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

Print a summary to the terminal: connection count, anomaly count, gap count.

---

## Guidelines

- **Cite everything.** Every finding must link to a specific entity note and document.
- **Don't speculate.** Flag what the data shows; don't invent explanations.
- **Distinguish levels of certainty.** "Shares an address" is a fact. "May be a shell company" is an inference — label it as such.
- **Be brief.** The report is a prompt for investigation, not a comprehensive analysis. Each finding should be one short paragraph.
