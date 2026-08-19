---
description: [one line naming the document types this skill covers — shown in the classifier index]
---
# Domain knowledge — [Document type name]

This skill is loaded by Watchdog when the document type is a [list the trigger document types]. [One sentence on what the skill helps extract or identify.]

[Authoring note — delete before saving: extraction runs as a single completion with no tool or network access, and the extraction prompt tells the model to convert any "check / search / cross-reference X" guidance into a recorded lead. Phrase red flags as things to observe and record in the document itself, not as verification steps to perform against outside sources. The prompt also already tells the model the field table is a floor, not a ceiling — don't add per-skill sentences about extraction behaviour; a skill carries only its scope sentence and domain content.]

---

## Document types covered

- [Specific document name or type]
- [Specific document name or type]
- [Jurisdiction-specific variants, grouped if needed:]
  - In Canada: [document names]
  - In the US: [document names]
  - In the UK: [document names]

---

## Fields to extract

| Field | What to look for |
|-------|-----------------|
| **[Field name]** | [What to extract and any nuances] |
| **[Field name]** | [What to extract and any nuances] |

---

## Red flags — what to look for

The only reader of this section is the extractor: one model pass over a single document, plus a digest of the entities already in the vault — no web, no databases, no prior knowledge of who anyone is. Write every red flag so it reduces to one of three moves:

- **Capture a stated pattern** the model can see in this document — a structure, a sequence, a value, a role.
- **Compare against the entity digest**, but only where both sides are stated: this document says one thing, the digest says another. A contradiction needs two explicit claims; it cannot fire on silence.
- **Log a lead** — something worth a human checking later, when the flag depends on knowledge the model doesn't have.

A flag that requires knowing who someone is, proving an absence ("undisclosed", "not filed", "failed to"), or looking something up is not a red flag the extractor can act on — move that insight to *What investigators typically miss*.

Watch for these recurring non-actionable patterns — convert each rather than delete it:

- **Counting or trend across documents** ("repeated", "a pattern of", "over several years") — narrow to what one document shows, make it a digest comparison, or move it to *What investigators typically miss*.
- **Outside reputation or benchmark** ("known bad actor", "above the industry average", "low-tax jurisdiction") — capture the value and log a lead.
- **Editorial judgement** ("less reliable", "reputable") — rewrite as an extraction action ("record the stated characterisation").
- **Vague thresholds** ("recent", "shortly after") — give a concrete window or tie it to a date stated in the document.

The usual fix is to keep the insight and end the flag with "record X; log a lead to check Y", not to drop it.

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

List as many as this record type genuinely has — no more, no fewer. Each should name something a first-year journalist would overlook but a twenty-year veteran would check automatically; stop when you run out of those, even if that's three, and don't pad past that point just because a longer list looks more thorough.

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
