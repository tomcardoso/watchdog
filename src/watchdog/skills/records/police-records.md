---
description: a police occurrence report, use-of-force report, disciplinary decision, public complaint decision, coroner's inquest, or similar law enforcement document. For parole board decisions, probation records, prison inspection reports, and corrections oversight documents, use `corrections-records`; for charging documents, bail decisions, or trial and sentencing records, use `criminal-proceedings`
---
# Domain knowledge — Police records

This skill is loaded by Watchdog when the document type is a police occurrence report, use-of-force report, disciplinary decision, public complaint decision, coroner's inquest, or similar law enforcement document. For parole board decisions, probation records, prison inspection reports, and corrections oversight documents, use `corrections-records` instead. For charging documents, bail decisions, or trial and sentencing records, use `criminal-proceedings`.

---

## Document types covered

- Police occurrence reports and general incident reports
- Use-of-force reports
- Subject behaviour / officer response reports
- Public complaints and disciplinary decisions (police oversight bodies)
- Officer conduct hearings and tribunal decisions
- Coroner's inquest recommendations
- Independent special investigations unit reports (civilian oversight of serious incidents)
- In Canada: LECA (formerly OIPRD), SIU, ASIRT, SIRT, OPCC decisions; Police Services Act charges
- In the US: Internal affairs investigation summaries; Civilian Complaint Review Board (CCRB) decisions
- In the UK: Independent Office for Police Conduct (IOPC) decisions; police misconduct hearing records

---

## Fields to extract

| Field | What to look for |
|-------|-----------------|
| **Occurrence / case number** | The unique identifier assigned to the incident |
| **Date and time of occurrence** | When the incident took place |
| **Location** | Address or description of where the incident occurred |
| **Reporting officer** | Name, badge number, and unit |
| **Subject / complainant** | Name, age, and any identifiers of the person involved |
| **Offence or incident type** | The classification (assault, use of force, complaint category) |
| **Outcome** | What happened — arrest, release, charges laid, officer found guilty/not guilty, etc. |
| **Witnesses** | Names and roles of witnesses (may be redacted) |
| **Investigator or adjudicator** | Who investigated or decided the matter |
| **Decision date** | When the decision or report was finalized |
| **Recommendations** | Any formal recommendations made (especially coroner's inquests) |

---

## Red flags — what to look for

### Use of force

- **Force level used vs. subject behaviour** — use-of-force frameworks require proportionality. A high level of force applied to a subject described as passively resistant or non-threatening is disproportionate and worth flagging.
- **Multiple officers involved in a use-of-force incident** — note whether each officer's actions are individually documented, or whether accounts are consolidated in a way that obscures individual accountability.
- **Injuries not documented as force-related** — injuries to a subject that appear in medical records but are not recorded in the use-of-force report suggest an incomplete or inaccurate account.
- **Inconsistency between the narrative and the structured report form** — structured force-reporting forms and free-text narratives often exist for the same incident. Discrepancies between them suggest the narrative may have been crafted after the fact.
- **Documented injuries with no accompanying use-of-force report** — officers must file a use-of-force report whenever force above a threshold is used. If an occurrence report documents injuries or a struggle but no use-of-force report accompanies it, record that gap as a lead to confirm against the force's reporting record — the extractor can't see a report that isn't there.

### Disciplinary and complaint records

- **Serial complaints against the same officer** — a pattern of complaints, even if individually dismissed, may indicate a systemic problem. Check the complainant demographics and complaint type.
- **Disposition rates** — the proportion of complaints that are substantiated is typically low. Compare the dismissal rate for similar complaint types to understand whether a particular decision is anomalous.
- **Conflicts of interest in the investigation** — a complaint investigated by officers in the same unit, division, or with a supervisory relationship to the subject officer.
- **Recommendations not implemented** — coroner's inquest recommendations and oversight body recommendations that were not implemented by the police service. Note whether the same recommendation appears across multiple inquests.
- **Delay between complaint and decision** — a complaint filed years before a decision may indicate a backlog or deliberate delay.

### Race and systemic factors

- **Subject demographics** — where disclosed, note the race, age, gender, and mental health status of subjects involved in use-of-force incidents. Patterns across multiple records in a jurisdiction are newsworthy.
- **Location patterns** — repeated use of force or complaints concentrated in specific neighbourhoods, housing complexes, or transit facilities may indicate over-policing.
- **Mental health crisis calls** — officers dispatched to mental health crisis calls who use force should trigger questions about whether mental health call diversion programs exist and why they were not used.

---

## Jurisdiction terminology

| Term | Jurisdiction | Meaning |
|------|-------------|---------|
| **LECA** | Canada (Ontario) | Law Enforcement Complaints Agency (formerly OIPRD) — receives and investigates public complaints against police |
| **SIU** | Canada (Ontario) | Special Investigations Unit — investigates incidents involving death, serious injury, or sexual assault by police |
| **ASIRT** | Canada (Alberta) | Alberta Serious Incident Response Team |
| **SIRT** | Canada (Atlantic) | Serious Incident Response Team — exists in Nova Scotia, New Brunswick, and other provinces |
| **OPCC** | Canada (BC) | Office of the Police Complaint Commissioner |
| **CRCC** | Canada | Civilian Review and Complaints Commission — reviews RCMP conduct |
| **IOPC** | UK | Independent Office for Police Conduct — investigates serious police misconduct in England and Wales |
| **PSNI Ombudsman** | UK (NI) | Police Ombudsman for Northern Ireland |
| **CCRB** | US (New York) | Civilian Complaint Review Board — reviews NYPD misconduct complaints |
| **GOR** | Canada | General Occurrence Report — a standardized incident report |
| **SB/OR form** | Canada | Subject Behaviour / Officer Response form — a structured use-of-force reporting document |
| **Section 810 recognizance** | Canada | A peace bond issued under the Criminal Code — imposes conditions on someone not convicted of an offence |

---

## Relationships to extract from police records

1. **Person → Officer**: Subject/complainant relationship with the officer involved
2. **Officer → Unit/Service**: Officer's posting and supervisory chain
3. **Person → Decision-maker**: Who investigated or adjudicated a complaint
4. **Officer → Prior complaints**: Pattern of prior complaints (note: may require multiple documents)
5. **Decision → Recommendation**: Formal recommendations and the body that made them

---

## What investigators typically miss

1. **Rights caution and access to counsel** — in occurrence reports involving statements by a subject, note whether the officer recorded advising the subject of their rights. Absence of this note may affect the admissibility of any statement and is itself a procedural flag.
2. **Witness accounts vs. officer narratives** — occurrence reports reflect the officer's account. Where civilian witnesses or video evidence exists, discrepancies between the officer narrative and other accounts are significant.
3. **The duty roster and radio logs** — a use-of-force incident involves multiple officers; the duty roster shows who was on shift and the radio logs show dispatch communications. These may be obtainable through a freedom of information request and may contradict or complement the occurrence report.
4. **Complaint withdrawal** — many complaints are withdrawn after informal resolution. A pattern of withdrawals in cases involving a particular officer may indicate complainants are being pressured or discouraged.
5. **Coroner's inquest jury recommendations** — juries at coroner's inquests make recommendations but have no power to compel action. Tracking whether those recommendations were implemented is an important accountability beat.

---

## Sources and further reading

### Official and regulatory

- [Law Enforcement Complaints Agency (LECA) — Ontario](https://leca.ca/) — Ontario's civilian police oversight body (formerly OIPRD), responsible for receiving and overseeing public complaints about police officer misconduct; publishes systemic review reports.
- [Civilian Review and Complaints Commission for the RCMP (CRCC)](https://crcc-ccetp.gc.ca/) — Independent federal agency that receives and reviews public complaints about on-duty RCMP conduct; publishes annual complaint statistics and investigation findings.
- [NYC Civilian Complaint Review Board (CCRB)](https://www.nyc.gov/site/ccrb/index.page) — New York City's independent agency investigating complaints of excessive force, abuse of authority, and misconduct by NYPD officers; publishes annual reports and case data.

### Practitioner and public interest

- [NACOLE — National Association for Civilian Oversight of Law Enforcement](https://www.nacole.org/) — US-based nonprofit supporting civilian police oversight bodies; maintains a jurisdiction-by-jurisdiction map of US oversight models and publishes research on accountability practices.

### Journalism resources

- [Reporters Committee for Freedom of the Press — Open Government Guide](https://www.rcfp.org/open-government-guide/) — State-by-state compendium of US open records laws covering police records, internal affairs files, and disciplinary records.
- [Reporters Committee — Access to Criminal Court Records](https://www.rcfp.org/open-court-sections/a-in-general-iv-access-to-criminal-court-records/) — Guide to legal rights of access to criminal court records across US jurisdictions, including records arising from police conduct prosecutions.
