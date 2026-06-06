# /surface — Find connections and anomalies across the vault

Perform a full connection and anomaly analysis across every entity and document in the vault. Surface things the journalist may have missed.

Run this on demand. It is computationally expensive on large vaults; run after major ingest batches.

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

Read `Registry/entities.json` and `Registry/documents.json` for the structured view.

Build a working index in memory:
- Entity ID → type, name, aliases, roles, appears_in
- Document slug → type, date, entities_mentioned

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

Find entities that appear in 3 or more documents but have no `roles` defined in their entity note. These are entities the vault knows about but hasn't mapped into the relationship graph.

### Company clusters

Find groups of companies that share 2 or more of: the same address, the same director, the same registered agent, the same filing date. Flag any cluster larger than 2.

### Timeline anomalies

For entities with `date_first_seen` in their note, look for:
- An entity that appears in a document dated significantly earlier than the vault's record of it (may indicate a missed prior document)
- A company formed or dissolved within 30 days of a large transaction involving it

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
- **Entities involved:** [[entity-1]], [[entity-2]]
- **Nature of connection:** <what they share or how they relate>
- **Documents:** [[doc-1]] (p. X), [[doc-2]] (p. Y)
- **Why it matters:** <one sentence on investigative significance>

## Anomalies

<For each anomaly:>

### <Anomaly title>
- **Entity:** [[entity-id]]
- **What's unusual:** <specific description>
- **Source:** [[document]] (p. N)
- **Suggested follow-up:** <one concrete next step>

## Gaps in the vault

<Entities or relationships that appear to be missing:>
- <gap description>

---

*Run `/query` to dig into any of these. Run `/health` to check vault integrity.*
```

Print a summary to the terminal: connection count, anomaly count, gap count.

---

## Guidelines

- **Cite everything.** Every finding must link to a specific entity note and document.
- **Don't speculate.** Flag what the data shows; don't invent explanations.
- **Distinguish levels of certainty.** "Shares an address" is a fact. "May be a shell company" is an inference — label it as such.
- **Be brief.** The report is a prompt for investigation, not a comprehensive analysis. Each finding should be one short paragraph.
