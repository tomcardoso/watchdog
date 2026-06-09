# Domain knowledge — Procurement and contract records

This skill is loaded by `/ingest` when the document type is a standing offer, task authorization, supply arrangement, vendor performance report, contract amendment, or similar post-award procurement document.

This skill complements the `government-contracts` skill, which covers the pre-award phase (RFPs, sole-source justifications, proactive disclosure). This skill covers what happens after the contract is awarded.

Apply this knowledge in addition to the standard extraction process. It tells you what to look for, what terminology means, and what patterns are worth flagging.

---

## Document types covered

- Standing offers, framework agreements, and call-ups against them
- Supply arrangements and resulting contracts
- Task-based professional services contracts and task authorizations
- Contract amendments and modifications
- Vendor performance reports and corrective action plans
- Contract close-out documents
- Sole-source contract justifications (post-award)
- Routine or proactive disclosure of contracts
- In Canada: Federal standing offer (SO) call-ups; TBIPS and ProServices task authorizations; proactive disclosure of contracts over $10,000
- In the US: GSA schedule orders; IDIQ (Indefinitely Deliverable, Indefinitely Quantity) task orders; USASpending.gov disclosures
- In the UK: Crown Commercial Service framework call-offs; Contracts Finder published awards
- In the EU: TED (Tenders Electronic Daily) contract award notices

---

## Always-present fields to extract

| Field | What to look for |
|-------|-----------------|
| **Contract or task number** | The unique identifier |
| **Contracting authority** | The government department or agency that issued the contract |
| **Vendor name** | Legal name of the contractor |
| **Contract type** | Standing offer call-up, task authorization, firm price, cost-reimbursable, etc. |
| **Original contract value** | The initial contracted amount |
| **Amended value** | Total value after amendments |
| **Amendment count** | How many amendments were made |
| **Start date** | When work was authorized to begin |
| **End date** | The contracted completion date |
| **Extended end date** | If the contract was extended, the new end date |
| **Commodity / work description** | What was being procured |
| **Vendor performance rating** | If a performance report exists: the rating and any corrective actions required |
| **Security classification** | Security requirement level, where applicable |

---

## Red flags — what to look for

### Amendments and scope creep

- **Repeated amendments increasing value significantly** — a contract awarded at one value that has been amended multiple times to reach a much higher value was not competitively procured at its actual value. Each amendment effectively bypasses the competitive process.
- **Amendment without documented justification** — contracting rules require justification for amendments above certain thresholds. A file with no documented basis for an amendment is a procurement irregularity.
- **Time extensions without deliverables completed** — a contract extended repeatedly because the vendor has not delivered what was originally promised.
- **Retroactive amendments** — an amendment dated after the period it covers, suggesting the work was already done (and paid for) outside the contract's authorized scope.

### Vendor concentration

- **Same vendor holding multiple standing offer call-ups** — a vendor who is on the preferred list for multiple categories or regions may be receiving a disproportionate share of government work without competition.
- **Call-ups clustered just below the direct award threshold** — a pattern of awards just below the threshold above which competition is required suggests deliberate limit-avoidance.
- **Subcontracting to the original competitor** — a prime contractor who wins a competitive process and then subcontracts the work to the bidder they beat; the competition result is effectively reversed.
- **Vendor that lost a competitive bid receiving a sole-source shortly after** — a company that failed in competition but was then awarded the work on a non-competitive basis.

### Vendor performance

- **Satisfactory rating despite documented issues** — a vendor performance report that notes delivery failures or quality problems but rates the vendor "satisfactory" overall may reflect reluctance to formally record a poor rating (which would affect future competitions).
- **Corrective action plans not followed up** — a corrective action plan was required but the file contains no record of whether it was implemented or the performance improved.
- **No vendor performance reports filed** — for contracts above the relevant threshold, performance reports are required. Absence of reports may indicate the department is not monitoring the contract.

### Security and access

- **Security clearance level vs. work description** — a contract requiring the highest clearance level for work that appears routine may indicate sensitive project scope. The security requirement is itself informative.
- **Contractor staff changes not documented** — ongoing access by individuals who are no longer on the authorized resource list.

---

## Jurisdiction terminology

| Term | Jurisdiction | Meaning |
|------|-------------|---------|
| **PSPC** | Canada | Public Services and Procurement Canada — the central contracting authority for the federal government |
| **Standing offer (SO)** | Canada | A pre-arranged agreement with a supplier at pre-negotiated prices; purchases are made through "call-ups" |
| **Supply arrangement (SA)** | Canada | An arrangement with qualified suppliers to submit bids on future requirements; not a commitment to purchase |
| **Call-up** | Canada | An individual purchase order under a standing offer |
| **Task authorization (TA)** | Canada | An order for specific work under a task-based contract (common in IT professional services) |
| **TBIPS** | Canada | Task-Based Informatics Professional Services — the federal standing offer vehicle for IT consulting |
| **ProServices** | Canada | A federal supply arrangement for professional services (non-IT) |
| **Proactive disclosure** | Canada | The federal requirement to publish contracts over $10,000 on the Open Government portal |
| **Buyandsell.gc.ca** | Canada | The federal portal for procurement notices and awarded contracts |
| **GSA Schedule** | US | US General Services Administration schedules — pre-negotiated contracts from which agencies can buy |
| **IDIQ** | US | Indefinitely Deliverable, Indefinitely Quantity contract — a master contract with a ceiling value |
| **USASpending.gov** | US | The US federal database of contract and grant spending |
| **Contracts Finder** | UK | The UK government's database of contracts over £10,000 |
| **TED** | EU | Tenders Electronic Daily — the EU's official database of public procurement notices and awards |
| **Framework agreement** | UK / EU | A pre-established agreement with one or more suppliers from which contracting authorities can place orders |

---

## Relationships to extract from procurement records

1. **Vendor → Contract**: Contractor and what they were paid (original and amended value)
2. **Department → Vendor**: Contracting relationship (with contract type and period)
3. **Contract → Amendment**: Each amendment (number, date, change in value or scope)
4. **Vendor → Performance report**: Performance rating and any corrective action required
5. **Prime contractor → Subcontractor**: Subcontracting relationships (where disclosed)

---

## What investigators typically miss

1. **The proactive or routine disclosure database** — many jurisdictions publish awarded contracts above a threshold in a searchable database. Comparing this database to other spending data can reveal discrepancies.
2. **Amendment history** — a single contract record shows the current value. The amendment history shows how the value grew over time and whether each increase was justified.
3. **Related contracts to the same vendor** — the contract you have is one; the full picture requires checking all contracts to the same vendor across departments over the same period. Total spend is more newsworthy than any individual contract.
4. **The winning bid vs. competing bids** — in a competitive process, the winning bid is sometimes available through access to information. Comparing bid prices can reveal whether the process was genuinely competitive.
5. **Conflict of interest attestations** — contractors are typically required to attest that they have no conflict of interest. If a conflict later emerges, the attestation is evidence that it was either undisclosed or not caught.
6. **Post-contract audit rights** — most contracts over certain thresholds give the government the right to audit the contractor's records. Whether those audit rights were exercised, and what was found, is often not public but obtainable through access to information.

---

## Sources and further reading

### Official and regulatory
- [Proactive Disclosure — Open Government Portal (Canada)](https://open.canada.ca/en/proactive-disclosure) — Central hub for Canada's proactive publication requirements; contracts over $10,000 from all federal departments, updated quarterly
- [Proactive Publication — Contracts Dataset (Canada)](https://open.canada.ca/data/en/dataset/d8f85d91-7dec-4fd1-8055-483b77225d8b) — Downloadable CSV dataset of federal contract disclosures; useful for bulk analysis of vendor patterns, amendment histories, and department-level spending trends
- [CanadaBuys / Buyandsell.gc.ca](https://buyandsell.gc.ca/) — Canada's procurement portal for notices, standing offers, and supply arrangements; contract history is accessible through this portal
- [USASpending.gov](https://www.usaspending.gov/) — Official US federal database of contract and grant spending including IDIQ task orders and GSA Schedule purchases; award data available from fiscal year 2001

### Practitioner and public interest
- [Open Contracting Partnership](https://www.open-contracting.org/) — Promotes open, fair, and efficient public contracting globally; publishes guidance on post-award monitoring, amendment red flags, and how to use OCDS data to detect irregularities
- [Open Contracting Data Standard (OCDS)](https://www.open-contracting.org/data-standard/) — International open standard for publishing full contracting process data from planning through contract implementation; endorsed by the G20 and G7
- [OECD Recommendation of the Council on Public Procurement](https://legalinstruments.oecd.org/en/instruments/OECD-LEGAL-0411) — Sets twelve principles for transparent and integrity-focused procurement; the framework against which post-award failures (scope creep, unsupported amendments, absent performance reports) can be benchmarked

### Journalism resources
- [GIJN — Tracking Government Contracts](https://gijn.org/resource/tracking-covid-19-contracts-a-gijn-guide-and-webinar) — Practical guide to pursuing procurement stories; covers post-award red flags including unnecessary purchases, inexperienced contractors, and secrecy at the implementation stage
