# Domain knowledge — General records

This skill is loaded by `/ingest` when the document type does not match any specific record skill. It provides a universal framework for reading an unfamiliar document: how to orient yourself, what to extract regardless of type, and what patterns are worth flagging in any record.

Apply this knowledge in addition to the standard extraction process. If you can identify the document type during extraction and a more specific skill exists, use that skill's guidance instead — it will be more precise.

---

## Before extracting: orient yourself

Before pulling fields, answer four questions about the document:

1. **What is it?** Identify the document type from its header, format, layout, and any reference numbers. If the document doesn't announce its type, infer it from the issuing authority and its structure.
2. **Why does it exist?** Every official document exists because some obligation, process, or event required it. Understanding that purpose tells you what the document is supposed to contain — and what its absence or incompleteness might mean.
3. **Who created it, and for whom?** A document produced by its subject (a company's own annual report) has different evidentiary weight than one produced by a regulator or court. The intended audience also shapes what is disclosed and what is withheld.
4. **Is it sworn, certified, or self-reported?** A sworn affidavit, a notarized document, or a certified copy carries legal weight. A self-reported filing does not. Note which one you're dealing with.

---

## Always-present fields to extract

These fields appear in virtually every official or semi-official document. Extract them even when not prominently displayed.

| Field | What to look for |
|-------|-----------------|
| **Document type** | What kind of record this is — name it precisely if possible |
| **Issuing authority** | The body, office, or individual that produced or certified the document |
| **Jurisdiction** | The legal or regulatory jurisdiction governing this document |
| **Date(s)** | Date of creation, date of events described, date of signatures — these are often different |
| **Reference / file number** | Any identifier assigned by the issuing authority |
| **Named parties** | Every person and organization named, with their stated role or capacity |
| **Addresses** | All addresses — residential, business, registered, mailing |
| **Monetary amounts** | Every dollar figure, with context (what it represents, who owes or paid it) |
| **Signatures and certifications** | Who signed, in what capacity, and whether it is sworn or certified |
| **Attachments and exhibits** | Any documents referenced as attached — note them even if they are not present |

---

## Red flags — what to look for

### Document integrity

- **Missing pages or unexplained gaps** — a document numbered pages 1–4 and 7–12 is missing pages 5 and 6. This may be a redaction, a copying error, or deliberate concealment. Note it explicitly.
- **Document that doesn't match its claimed type** — wrong letterhead, inconsistent formatting, different fonts mid-document, dates that don't match the issuing authority's known practices. These can indicate alteration or fabrication.
- **Version discrepancies** — "amended," "revised," or "restated" versions without a clear explanation of what changed. Always try to obtain the original to compare.
- **Signature anomalies** — a document signed by someone not identified in the body; a signature block left blank; a date of signature earlier than the date of the events it describes.

### What's missing

- **Redactions** — what is hidden is often more informative than what is shown. Note every redaction: its approximate size, its location in the document, and any surrounding context that suggests what it covers.
- **Absent parties** — if a document describes a transaction, relationship, or event that would normally involve a named party, and that party is not named, ask why. Absence is a signal.
- **Attachments listed but not provided** — a document may reference schedules, annexes, or exhibits that were not included in what you received. List them; they may be obtainable separately.
- **Expected fields left blank** — a form with a mandatory field left empty is as significant as a completed one. Note blank fields that appear required.

### Dates and timelines

- **Backdating** — a document signed or filed after the events it describes, where the date implies it was contemporaneous. Common in fraud.
- **Implausible timelines** — an approval granted before the application was submitted; a contract executed after the work was completed; a meeting that occurred on a weekend or holiday for an organisation that wouldn't normally operate then.
- **Date of document vs. date of events** — always distinguish between when something was written and when the events described occurred.

### Self-reporting vs. independent verification

- **Self-reported figures without audit** — financial information, headcounts, or statistics provided by the subject without independent verification should be treated as claims, not facts.
- **Conflict of interest in the issuing authority** — a document produced by or for the party it describes (a company's own environmental audit, a police force's own use-of-force review) has lower evidentiary weight than one produced by an independent regulator or court.

---

## Terminology

When you encounter an unfamiliar term in an unknown document type:

| Situation | Approach |
|-----------|---------|
| **Regulatory or legal term** | Look for the issuing authority's official glossary or enabling legislation — most regulatory bodies publish plain-language guides to their forms |
| **Jurisdiction-specific term** | Note the jurisdiction and flag the term for research; terminology tables in specific record skills (corporate-filings, land-registries, court-documents, etc.) may cover it |
| **Accounting or financial term** | IFRS and GAAP standards bodies publish free plain-language summaries |
| **Term defined within the document** | Many official documents include a definitions section — check the beginning and end of the document |

---

## Relationships to extract

For any document type, look for these universal relationship types:

1. **Person → Organization**: Named role (director, officer, employee, member, signatory, counsel, agent)
2. **Person / Organization → Address**: All addresses stated in the document, with date context where available
3**Organization → Organization**: Parent, subsidiary, related party, contracting party, regulator/regulated
4. **Person / Organization → Document**: Issued by, filed by, named in, subject of, signatory of
5. **Person / Organization → Amount**: Owes, paid, awarded, claimed — with date and counterparty
6. **Document → Document**: Amended by, superseded by, referenced in, attached to

---

## What investigators typically miss

1. **The issuing authority's mandate** — every regulatory body or court has a statutory mandate that defines what it can and cannot do. If a document from that body omits something its mandate requires, that omission is the story.
2. **The difference between "filed" and "accepted"** — a document filed with a regulator or court has not necessarily been reviewed or accepted. Check whether the filing was acknowledged, approved, or challenged.
3. **Who is authorised to sign** — not everyone who signs an official document is authorised to bind the organisation. A signature from someone without authority can void an agreement or signal an internal breakdown.
4. **The covering letter or transmittal memo** — documents released in response to FOI or litigation requests often come with a transmittal memo that describes what was and was not provided. This memo is itself a document worth reading.
5. **The difference between a copy and an original** — "certified true copy" has legal meaning; "copy" does not. A photocopy of a photocopy may have had pages removed or reordered.
6. **What the document is not saying** — an annual report that discusses every business line except one; a regulator's letter that addresses every concern raised except the most serious one. Structured silence is a pattern.
7. **The date stamp vs. the creation date** — filing systems and registries record when a document was received, not when it was created. The gap between creation and filing can be significant.
8. **Cross-referencing against other documents you already have** — a name, address, or amount that appears in this unfamiliar document may connect to a well-documented entity in your vault. Run the extraction before assuming the document stands alone.

---

## Sources and further reading

### Practitioner and public interest
- [GIJN Guide to Investigating Organised Crime](https://gijn.org/resource/guide-to-investigating-organized-crime/) — broad investigative methodology applicable to unfamiliar document types
- [OCCRP Research Desk](https://www.occrp.org/en/resources/) — practical guides to document research across multiple jurisdictions

### Journalism resources
- [IRE — Investigative Reporters and Editors](https://www.ire.org/resources/) — tipsheets and training on document-based reporting (many resources require membership; public catalogue available)
- [GIJN Helpdesk](https://helpdesk.gijn.org/) — free research assistance for journalists working with unfamiliar documents or jurisdictions
