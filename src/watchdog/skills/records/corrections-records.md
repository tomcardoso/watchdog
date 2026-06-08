# Domain knowledge — Corrections, parole, and custody records

This skill is loaded by `/ingest` when the document type is a parole board decision, probation order, correctional investigator report, prison inspection report, institutional grievance decision, conditional release record, or similar document originating from correctional services, parole boards, or corrections oversight bodies. For police-generated documents (use-of-force reports, occurrence reports, misconduct decisions), use `police-records` instead.

Apply this knowledge in addition to the standard extraction process. It tells you what to look for, what terminology means, and what patterns are worth flagging.

---

## Document types covered

- Parole and conditional release board decisions (full parole, day parole, statutory release, detention orders)
- Parole suspension and revocation decisions
- Probation orders and probation officer reports
- Community supervision orders and reintegration plans
- Correctional investigator / ombudsman reports and annual reports
- Prison and jail inspection reports (government inspectorates, independent monitors)
- Institutional grievance and complaint decisions
- Correctional service population statistics and reporting
- Transfer decisions (institution to institution, security classification changes)
- Administrative segregation / solitary confinement records and reviews
- Death-in-custody reviews and reports (not SIU/police oversight — those are `police-records`)
- Conditional sentence orders (served in community)
- In Canada: Parole Board of Canada (PBC) decisions; Correctional Service of Canada (CSC) documents; Office of the Correctional Investigator reports; provincial parole board decisions; provincial correctional service reports
- In the US: State parole board decisions; Bureau of Prisons inspection reports; Prison Policy Initiative and DOJ oversight reports; state department of corrections records
- In the UK: Parole Board decisions; His Majesty's Inspectorate of Prisons reports; Prison and Probation Ombudsman reports
- In Australia: State parole board decisions; Commonwealth and state prison inspection reports

---

## Always-present fields to extract

| Field | What to look for |
|-------|-----------------|
| **Offender name or identifier** | Full name and correctional identifier (FPS number in Canada, federal BOP number in US) |
| **Original offence(s)** | The offence(s) for which the person is serving a sentence |
| **Sentence length** | Total sentence, and whether indeterminate or determinate |
| **Eligibility dates** | Parole eligibility date, statutory release date, warrant expiry date |
| **Decision type** | What was being decided (full parole, day parole, detention, revocation) |
| **Decision date** | When the board or authority issued its decision |
| **Board members / adjudicators** | Who made the decision |
| **Decision outcome** | Granted, denied, revoked, continued, deferred |
| **Conditions imposed** | Specific conditions attached to any release |
| **Risk assessment** | The actuarial tool or classification used and the score or rating |
| **Institutional behaviour** | Programme participation, disciplinary record cited in the decision |

---

## Red flags — what to look for

### Parole and conditional release

- **Decisions based primarily on actuarial risk scores** — parole boards often rely on actuarial risk assessment tools (e.g., Static-99, LSI-R). A denial grounded primarily in a tool score rather than an individualized assessment is worth scrutiny, particularly where the tool has known limitations for specific populations (Indigenous people, women, the elderly).
- **Repeat denials without changed circumstances** — a series of parole denials where the stated reasons are identical and no new material is considered. Courts have found this practice unlawful in some jurisdictions.
- **Detention beyond statutory release** — in Canada, federal offenders have a right to statutory release at two-thirds of their sentence. Detention orders (which override this) must meet a high legal threshold; examine the stated reasons carefully.
- **Conditions that amount to set-up for failure** — conditions that require abstaining from a substance without providing treatment access, no-contact conditions that cover an offender's entire social network, or residence requirements in areas the offender has no connection to.
- **Parole granted to people with serious institutional records, denied to those without** — inconsistency in decision-making is a systemic story. Compare the reasoning across multiple decisions.

### Probation and community supervision

- **Technical violations as a path back to custody** — a return to custody for a technical breach (missed appointment, failed drug test) rather than new criminal conduct. The proportion of revocations driven by technical violations is a systemic indicator.
- **Geographic restrictions that preclude employment or family contact** — community supervision conditions that make reintegration practically impossible are a known driver of reoffending and re-incarceration.
- **Supervision intensity vs. stated risk level** — high-intensity supervision conditions applied to someone assessed as low risk, or vice versa.

### Institutional conditions

- **Prolonged administrative segregation** — time in segregation beyond legislative limits (in Canada, the Structured Intervention Unit framework following Bill C-83; in the US, limits vary by state and facility). Segregation records that predate court orders limiting the practice are a historical baseline.
- **Overcrowding and its consequences** — inspection reports citing overcrowding should be cross-referenced with population statistics and capacity numbers. Overcrowding is often a precursor to violence, inadequate programming, and health crises.
- **Mental health crisis responses** — whether the institution has a mental health diversion process, whether it was used, and whether the individual had a documented mental health history prior to an incident.
- **Death in custody** — the mandatory reporting framework, the cause of death classification (suicide, natural, homicide, accident, undetermined), and whether a prior grievance or flagged risk was on file.

### Systemic and oversight

- **Recommendations from prior inspections not implemented** — correctional investigator and inspectorate reports typically list outstanding recommendations from prior reports. Tracking the implementation rate reveals systemic non-compliance.
- **Overrepresentation statistics** — virtually all Western correctional systems show significant overrepresentation of Indigenous, Black, and racialized populations. When a decision document includes demographic data, extract it; when an inspection report includes population statistics, the overrepresentation figures are often buried.
- **Legal aid access** — parole hearings are quasi-judicial proceedings where offenders have a right to assistance. Whether legal aid was available and whether the person appeared with representation affects the reliability of the outcome.

---

## Jurisdiction terminology

| Term | Jurisdiction | Meaning |
|------|-------------|---------|
| **Parole Board of Canada (PBC)** | Canada | The federal body that makes conditional release decisions for federal offenders (sentences of 2+ years) |
| **CSC** | Canada | Correctional Service of Canada — manages federal institutions and community supervision |
| **Correctional Investigator** | Canada | The federal ombudsman for federal offenders; reports annually to Parliament |
| **Full parole** | Canada | Early release into the community with conditions; the normal form of conditional release |
| **Day parole** | Canada | Release for specific purposes (employment, treatment) with return to facility each night |
| **Statutory release** | Canada | Automatic release at two-thirds of sentence; can be overridden by a detention order |
| **Warrant expiry** | Canada | The end of the sentence; after this date the correctional system has no jurisdiction |
| **FPS number** | Canada | Fingerprint Section number — the unique identifier for federal offenders |
| **Structured Intervention Unit (SIU)** | Canada | Replaced administrative segregation in federal institutions under Bill C-83 (2019) |
| **Parole Board** | UK | The independent body that assesses whether prisoners can be safely released |
| **Probation Service** | UK/US | The agency supervising offenders in the community |
| **Determinate sentence** | Universal | A fixed-length sentence with a set release date |
| **Indeterminate sentence** | Universal | A sentence with no fixed end date; release depends on board decision |
| **Risk assessment tool** | Universal | A standardized instrument used to predict the likelihood of reoffending |
| **LSI-R** | Universal | Level of Service Inventory — Revised; a widely used recidivism risk assessment tool |
| **Static-99** | Universal | A risk assessment tool used for sexual offenders |
| **Technical violation** | Universal | A breach of a condition of release that is not itself a new criminal offence |
| **Administrative segregation** | Universal | Separation from the general prison population, often used as a disciplinary measure; increasingly limited by law |

---

## Relationships to extract from corrections records

1. **Person → Correctional institution**: Current and past placements
2. **Person → Parole board**: Decision type, date, outcome, and conditions
3. **Person → Probation officer / supervisor**: Community supervision relationship
4. **Person → Risk assessment**: Tool used, score or rating
5. **Institution → Oversight body**: Inspection findings and recommendations
6. **Correctional service → Legislative framework**: Which statutory provisions governed the decision

---

## What investigators typically miss

1. **The gap between statutory release date and warrant expiry** — this window (from statutory release to the end of the sentence) is when community supervision is most intense and when the correctional system has the most leverage. How this period is managed reveals reintegration philosophy and resource allocation.
2. **The victim notification process** — in most jurisdictions, registered victims have a right to be notified of conditional release decisions. Whether this process worked, and how victims' submissions are weighted, is both a procedural and a human story.
3. **Annual reports of the Correctional Investigator / Ombudsman** — these reports document systemic problems, sometimes for years before a crisis event. The annual report of a correctional ombudsman is often the most data-rich and underreported document on prison conditions available.
4. **The institutional record behind the parole decision** — the parole decision summarizes the institutional file; it does not reproduce it. Disciplinary records, programme completion certificates, psychological assessments, and case management plans are separate documents that can be requested and often provide context the decision omits.
5. **Overrepresentation data at the facility level** — national statistics on Indigenous and racialized overrepresentation in custody are reported but underused. The same data broken down by individual facility, region, or offence category is far more powerful and often buried in appendices.
6. **The legal standard that was applied** — parole and detention decisions invoke a legal standard (e.g., "undue risk to public safety"). Whether the board correctly applied the legal test is a question journalists rarely examine but courts regularly overturn decisions on.
7. **Deaths in custody that are not homicides** — suicides, overdoses, and "natural" deaths in custody are often undercounted and underinvestigated relative to their public significance. Mandatory reporting frameworks exist in most jurisdictions; tracking compliance with those frameworks is a distinct beat.
