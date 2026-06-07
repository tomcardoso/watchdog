# Domain knowledge — Healthcare licensing and regulatory records

This skill is loaded by `/ingest` when the document type is a health regulatory body discipline decision, fitness to practise finding, hospital incident report, public health inspection report, or similar professional licensing or healthcare regulatory document.

Apply this knowledge in addition to the standard extraction process. It tells you what to look for, what terminology means, and what patterns are worth flagging.

---

## Document types covered

- Health regulatory body discipline committee decisions
- Fitness to practise panel decisions
- Registration committee decisions (licence refusals, conditions, revocations)
- Consent orders and undertakings
- Investigations committee outcomes
- Hospital patient safety incident reports
- Critical incident reviews
- Public health inspection reports (food premises, long-term care, hospitals)
- Long-term care inspection reports and compliance orders
- Coroner's inquests into healthcare deaths
- In Canada: Health regulatory college discipline decisions; provincial college public registers
- In the US: State medical board disciplinary orders; state nursing board orders; CMS hospital conditions of participation compliance surveys
- In the UK: General Medical Council (GMC) fitness to practise decisions; Nursing and Midwifery Council (NMC) decisions; Care Quality Commission (CQC) inspection reports

---

## Always-present fields to extract

| Field | What to look for |
|-------|-----------------|
| **Member / registrant name** | The regulated professional's name |
| **Registration number** | The regulatory body's identifier for the member |
| **Regulatory body** | Which regulatory body or college made the decision |
| **Profession** | Physician, nurse, pharmacist, dentist, physiotherapist, etc. |
| **Decision date** | When the decision was rendered |
| **Panel composition** | Members of the discipline or fitness panel |
| **Allegations / charges** | What the member was alleged to have done |
| **Finding** | Guilty, not guilty, no finding, conditional |
| **Order / penalty** | Reprimand, suspension (length), revocation, conditions of practice |
| **Publication decision** | Whether the decision is published with the member's name |
| **Facility name** | Hospital, clinic, or practice where the conduct occurred |
| **Patient information** | Patient identifiers (usually initials or pseudonyms) |

---

## Red flags — what to look for

### Discipline patterns

- **Sexual abuse or boundary violation findings** — sexual misconduct by a health professional is treated with particular seriousness in most jurisdictions, often triggering mandatory revocation. Note whether the finding is for the most severe category of conduct.
- **Prior discipline not disclosed** — a member who has been disciplined previously in another jurisdiction or who moved after discipline without disclosure. Cross-check registration status across jurisdictions using national or international databases.
- **Practice conditions not complied with** — a member who was placed under conditions (mandatory supervision, restricted scope) and continued to practise outside those conditions.
- **Repeated complaints with minor findings** — a member with a pattern of minor discipline committee findings may indicate a systemic problem that each individual case understates.
- **Agreed statement of facts and penalty** — many regulatory cases are resolved by consent. An agreed statement of facts is not a full hearing; the agreed facts are less adversarially tested than a full hearing finding.
- **Conduct pre-dating registration** — conduct that occurred before the member was registered with the body in the jurisdiction (e.g. conduct in another country or profession) that may not be captured in the current body's records.

### Scope and standard of care

- **Departure from accepted standard** — discipline decisions often state that the member "departed from the standard of practice." Note what the standard was and how far the departure went.
- **Delegation to unqualified persons** — a regulated professional delegating controlled or restricted acts to an unqualified assistant is a systematic safety issue, not just an individual failing.
- **Billing irregularities connected to care quality** — overbilling (e.g., billing for procedures not performed) often co-occurs with quality-of-care problems.

### Facility and inspection

- **Repeat non-compliance in inspection reports** — a facility cited for the same deficiency across multiple inspection cycles has a systemic problem.
- **Immediate risk to patients or residents** — inspection reports distinguish between compliance failures that pose an immediate risk to safety and those that are administrative. Flag any "immediate risk" findings.
- **Staffing levels** — inadequate staffing is a common finding in care facility inspections and is often the root cause of other cited deficiencies.
- **Unreported critical incidents** — facilities are required to report critical incidents (serious adverse events) to the regulator. A finding that a critical incident was not reported is significant.

---

## Jurisdiction terminology

| Term | Jurisdiction | Meaning |
|------|-------------|---------|
| **College** | Canada | A self-governing professional regulatory body (e.g. College of Physicians and Surgeons of Ontario) — not a school |
| **Registrant / member** | Canada | A person registered with a college to practise the profession |
| **RHPA** | Canada (Ontario) | Regulated Health Professions Act — the umbrella legislation governing most health professions in Ontario |
| **Controlled act** | Canada | An act that may only be performed by a regulated health professional (e.g. surgery, prescribing, diagnosis) |
| **CPSO / CNO / OCP** | Canada (Ontario) | College of Physicians and Surgeons of Ontario / College of Nurses of Ontario / Ontario College of Pharmacists |
| **Public Register** | Universal | The regulatory body's public database of registrant status and discipline history |
| **GMC** | UK | General Medical Council — the UK regulator for doctors |
| **NMC** | UK | Nursing and Midwifery Council — the UK regulator for nurses and midwives |
| **Fitness to Practise** | UK / Canada | The regulatory stream dealing with a member's capacity to practise due to physical or mental incapacity or misconduct |
| **CQC** | UK | Care Quality Commission — inspects and regulates health and social care services in England |
| **State medical board** | US | The state-level body that licenses and disciplines physicians — each US state has one |
| **FSMB** | US | Federation of State Medical Boards — publishes a national database of physician disciplinary actions (DocInfo) |
| **CMS** | US | Centers for Medicare and Medicaid Services — publishes hospital inspection and compliance survey results |

---

## Relationships to extract from healthcare records

1. **Person → Regulatory body**: Registered member (with registration number, status, and any conditions)
2. **Person → Facility**: Practised at (hospital, clinic, long-term care home)
3. **Person → Patient**: Alleged misconduct (identified by patient initials or pseudonym)
4. **Person → Decision**: Discipline finding (with date, finding, and order)
5. **Facility → Regulator**: Inspection outcome (with deficiencies, compliance orders, and dates)

---

## What investigators typically miss

1. **The public register** — every regulated health body maintains a public register of members. The register shows current registration status, any conditions, and a summary of discipline history. It is the first place to check before reading a discipline decision in depth.
2. **Out-of-jurisdiction registration** — most countries do not have a single national health professional registry. A professional revoked in one jurisdiction may practise in another. Check FSMB (US), FMRAC (Canada), or equivalent cross-jurisdictional databases.
3. **The complainant** — discipline proceedings are usually brought by the regulatory body, not the patient. But the underlying complaint may have come from a patient, a colleague, or the employer. The source of the complaint can be significant.
4. **Agreed statements vs. full hearings** — an agreed statement of facts in a consent order is negotiated. The agreed facts may describe less egregious conduct than what actually occurred. Compare the agreed statement to any underlying police or coroner records.
5. **Unreported critical incidents** — hospitals and care facilities are required to report critical incidents internally and to the regulator. If you have patient safety data, compare it to what was formally reported.
6. **Long-term care inspection history** — care facility inspection reports are publicly available in many jurisdictions. Comparing a facility's inspection history over several years reveals whether identified problems were corrected or recurred.
