# Domain knowledge — [Document type name]

This skill is loaded by `/ingest` when the document type is a [list the trigger document types]. [One sentence on what the skill helps extract or identify.]

Apply this knowledge in addition to the standard extraction process. It tells you what to look for, what terminology means, and what patterns are worth flagging.

---

## Document types covered

- [Specific document name or type]
- [Specific document name or type]
- [Jurisdiction-specific variants, grouped if needed:]
  - In Canada: [document names]
  - In the US: [document names]
  - In the UK: [document names]

---

## Always-present fields to extract

These fields appear in virtually every document of this type. Extract them even when not prominently displayed.

| Field | What to look for |
|-------|-----------------|
| **[Field name]** | [What to extract and any nuances] |
| **[Field name]** | [What to extract and any nuances] |

---

## Red flags — what to look for

### [Red flag category]

- **[Red flag label]** — [One or two sentences: what to look for and why it matters. Write for pattern recognition, not just field extraction. Be specific — "transferred three or more times in 12 months" is useful; "unusual transaction" is not.]
- **[Red flag label]** — [Explanation]

### [Red flag category]

- **[Red flag label]** — [Explanation]

---

## Terminology

| Term | Meaning |
|------|---------|
| **[Term]** | [Plain-language definition] |
| **[Term]** | [Plain-language definition] |

If terminology varies significantly by jurisdiction, use separate tables per jurisdiction or a three-column table:

| Term | Jurisdiction | Meaning |
|------|-------------|---------|
| **[Term]** | [Jurisdiction] | [Meaning] |

---

## Relationships to extract

1. **Person → [EntityType]**: [Role or relationship type]
2. **Company → [EntityType]**: [Role or relationship type]
3. **[EntityType] → [EntityType]**: [Role or relationship type]

Use `→` notation. Include the relationship type after the colon.

---

## What investigators typically miss

1. [Specific, concrete thing — name the document section, field, or pattern. "The notes to financial statements" not "background information".]
2. [Specific thing]
3. [Specific thing]
4. [Specific thing]
5. [Specific thing]
6. [Specific thing]

Aim for six to eight items. Each should name something a first-year journalist would overlook but a twenty-year veteran would check automatically.

---

## Sources and further reading

### Official and regulatory
- [Source name](url) — one-line description of what it covers and why it's relevant

### Practitioner and public interest
- [Source name](url) — one-line description

### Journalism resources
- [Source name](url) — one-line description (omit this subsection if no publicly accessible journalism resources exist)

### Notes on unsourced claims
[If any red flag claims above could not be traced to a specific source, list them here and explain the basis — e.g., "practitioner knowledge" or "established in case law but no single canonical citation". These are flagged for editorial review, not silently included as fact. Delete this section if all claims are sourced.]
