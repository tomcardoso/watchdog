---
description: "This skill is loaded when the document type does not match any specific record skill. It provides a universal framework for reading an unfamiliar document: how to orient yourself, what to extract regardless of type, and what patterns are worth flagging in any record"
---
# Domain knowledge — General records

This skill is loaded by Watchdog when the document type does not match any specific record skill. It provides a universal framework for reading an unfamiliar document: how to orient yourself, what to extract regardless of type, and what patterns are worth flagging in any record.

---

## Before extracting: orient yourself

Before pulling fields, answer four questions about the document:

1. **What is it?** Identify the document type from its header, format, layout, and any reference numbers. If the document doesn't announce its type, infer it from the issuing authority and its structure.
2. **Why does it exist?** Every official document exists because some obligation, process, or event required it. Understanding that purpose tells you what the document is supposed to contain — and what its absence or incompleteness might mean.
3. **Who created it, and for whom?** A document produced by its subject (a company's own annual report) has different evidentiary weight than one produced by a regulator or court. The intended audience also shapes what is disclosed and what is withheld.
4. **Is it sworn, certified, or self-reported?** A sworn affidavit, a notarized document, or a certified copy carries legal weight. A self-reported filing does not. Note which one you're dealing with.

---

## Fields to extract

| Field | What to look for |
|-------|-----------------|
| **Document type** | What kind of record this is — name it precisely if possible |
| **Issuing authority** | The body, office, or individual that produced or certified the document |
| **Jurisdiction** | The legal or regulatory jurisdiction governing this document |
| **Date(s)** | Date of creation, date of events described, date of signatures — these may be different |
| **Reference / file number** | Any identifier assigned by the issuing authority |
| **Named parties** | Every person and organization named, with their stated role or capacity |
| **Addresses** | All addresses — residential, business, registered, mailing |
| **Monetary amounts** | Every dollar figure, with context (what it represents, who owes or paid it) |
| **Signatures and certifications** | Who signed, in what capacity, and whether it is sworn or certified |
| **Attachments and exhibits** | Any documents referenced as attached — note them even if they are not present |

---

## Red flags — what to look for

The universal red flags — document integrity, what's missing, date and timeline anomalies, and self-reported-versus-verified information — apply to every document and are carried in the standing extraction instructions, so they are not restated here. For an unfamiliar record they *are* the core of what to watch for: read this document against them and treat anything they surface as a fact or a lead, exactly as those instructions describe.

---

## Terminology

Extraction runs as a single completion with no tool access — there is no glossary to browse and no web to check mid-document. When you encounter an unfamiliar term in an unknown document type, work only from the document itself and what you already know:

| Situation | Approach |
|-----------|---------|
| **Term defined within the document** | Many official documents include a definitions section — check the beginning and end of the document first |
| **Regulatory, legal, or accounting term you recognize** | Define it inline from your own knowledge so the extraction stays self-contained |
| **Jurisdiction-specific or unfamiliar term** | Don't guess. Record the term verbatim and its jurisdiction in `observations` as an open question — specific record skills (corporate-filings, land-registries, court-documents, etc.) may already cover it; otherwise it's a lead for a later `/watchdog-research` session or human review |

---

## Relationships to extract

For any document type, look for these universal relationship types:

1. **Person → Organization**: Named role (director, officer, employee, member, signatory, counsel, agent)
2. **Person / Organization → Address**: All addresses stated in the document, with date context where available
3. **Organization → Organization**: Parent, subsidiary, related party, contracting party, regulator/regulated
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
