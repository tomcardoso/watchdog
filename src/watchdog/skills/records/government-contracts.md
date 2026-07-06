---
description: a procurement record, tender document, contract award, sole-source justification, standing offer or call-up, task authorization, contract amendment, vendor performance report, or similar government contracting record — the full procurement lifecycle, pre-award and post-award
---
# Domain knowledge — Government contracts and procurement records

Loaded by Watchdog when the document type is a procurement record, tender document, contract award, sole-source justification, standing offer, task authorization, contract amendment, vendor performance report, or similar government contracting record — pre-award or post-award.

Apply this knowledge in addition to the standard extraction process. It tells you what to look for, what terminology means, and what patterns are worth flagging.

---

## Document types covered

- Requests for proposals (RFPs) and invitations to tender / bid
- Standing offers, framework agreements, and supply arrangements — and the call-ups, call-offs, and task authorizations issued against them
- Contract award notices
- Sole-source / direct award justifications
- Contract amendments and modifications
- Vendor performance reports and corrective action plans
- Contract close-out documents
- Routine or proactive disclosure of contract awards
- Access to information / FOIA responses containing contract documents (for the response package itself, see `foi-responses`)
- Audit and evaluation reports on procurement
- Lobbyist registry filings related to contracts (see also `lobbying-records`)
- In Canada: federal standing offer (SO) call-ups; TBIPS and ProServices task authorizations; proactive disclosure of contracts over $10,000
- In the US: GSA schedule orders; IDIQ task orders; USASpending.gov disclosures
- In the UK: Crown Commercial Service framework call-offs; Contracts Finder published awards
- In the EU: TED (Tenders Electronic Daily) contract award notices

---

## Fields to extract

Fields expected in most government contracting records, or fields that are high-value whenever present. Extract them even when the document doesn't highlight them prominently. This list isn't exhaustive — also extract other fields or details you judge important, even if they aren't listed here.

| Field | What to look for |
|-------|-----------------|
| **Contracting authority** | The government department or agency |
| **Vendor / contractor** | The company or individual awarded the contract |
| **Contract or task number** | The unique identifier |
| **Contract type** | Competitive award, standing offer call-up, task authorization, firm price, cost-reimbursable, etc. |
| **Contract value** | Original contract value |
| **Amendment amounts** | Each amendment's value and the new total |
| **Amendment count** | How many amendments were made |
| **Contract period** | Start and end dates — and the extended end date if the contract was extended |
| **Description of work** | What is being procured |
| **Procurement method** | Competitive (open bidding), limited tender, sole source / direct award |
| **Commodity code** | Federal commodity classification (where applicable) |
| **Vendor performance rating** | If a performance report exists: the rating and any corrective actions required |
| **Security classification** | Security requirement level, where applicable |

---

## Red flags

### Procurement method

- **Sole source without adequate justification** — the government bypassed competition. Justifications typically include national security, only one supplier, or genuine urgency. Weak or circular justifications are red flags.
- **Splitting contracts** — dividing one large contract into multiple smaller contracts to avoid competitive procurement thresholds. Watch for multiple contracts with the same vendor at the same time for similar work.
- **Urgency used repeatedly** — a department that regularly invokes urgency to sole-source may be using it to direct work to preferred vendors.
- **Advance contract award notice (ACAN) or equivalent** — some jurisdictions allow a mechanism to post-justify a sole source by publishing a brief notice. These are often posted during low-attention periods. Few competitors bother to challenge them.

### Contract amendments and scope creep

- **Amendments exceeding the original value** — a common mechanism to expand a contract after award. If the original was below a competitive threshold and amendments push it well above, competition was effectively bypassed.
- **Many small amendments** — ten amendments of $50K each on a $100K contract have increased its value by 500% without triggering a new competition.
- **Amendment without documented justification** — contracting rules require justification for amendments above certain thresholds. A file with no documented basis for an amendment is a procurement irregularity.
- **Scope creep** — amendments that add work unrelated to the original contract description.
- **Retroactive amendments** — an amendment dated after the period it covers, suggesting the work was already done (and paid for) outside the contract's authorized scope.
- **Time extensions without deliverables completed** — a contract extended repeatedly because the vendor has not delivered what was originally promised.

### Vendor patterns

- **Same vendor winning repeatedly in a department** — may indicate an appropriate relationship (incumbent advantage is real), but warrants scrutiny — especially a vendor holding standing offer call-ups across multiple categories or regions without competition.
- **Vendor with a principal who is a former public servant** — revolving door concerns; check post-employment restrictions.
- **Numbered companies** — contracts awarded to numbered companies with no clear principal. The corporate registry entry for the vendor is a lead worth recording.
- **Address issues** — vendor's address is a residence, a mailbox service, or the same as another frequent contractor. May indicate a shell or pass-through entity.
- **Vendor registered after RFP posted** — the company was incorporated specifically to pursue this contract. Not inherently improper but worth noting.
- **Subcontracting to the original competitor** — a prime contractor who wins a competitive process and then subcontracts the work to the bidder they beat; the competition result is effectively reversed.
- **Vendor that lost a competitive bid receiving a sole-source shortly after** — a company that failed in competition but was then awarded the work on a non-competitive basis.

### Vendor performance

- **Satisfactory rating despite documented issues** — a vendor performance report that notes delivery failures or quality problems but rates the vendor "satisfactory" overall may reflect reluctance to formally record a poor rating (which would affect future competitions).
- **Corrective action plans not followed up** — a corrective action plan was required but the file contains no record of whether it was implemented or the performance improved.
- **No vendor performance reports filed** — for contracts above the relevant threshold, performance reports are required. Absence of reports may indicate the department is not monitoring the contract.

### Value anomalies

- **Contract values just below competitive thresholds** — most governments set thresholds above which a competitive process is required; the values vary by jurisdiction, procurement type, and applicable trade agreement, and are revised on a fixed schedule, so verify the current figures with the relevant procurement authority rather than relying on remembered numbers. The pattern to watch is a value like $24,999 or $499,999 sitting conspicuously just under a round threshold, or a cluster of awards to one vendor each just below the limit — it suggests the contracting officer knows the threshold and is deliberately staying under it.
- **Long contract periods with no competitive renewal** — a contract that has been "amended and extended" for years without re-competition.

### Security and access

- **Security clearance level vs. work description** — a contract requiring the highest clearance level for work that appears routine may indicate sensitive project scope. The security requirement is itself informative.
- **Contractor staff changes not documented** — ongoing access by individuals who are no longer on the authorized resource list.

---

## Terminology

| Term | Meaning |
|------|---------|
| **RFP** | Request for Proposals — evaluates both technical approach and price |
| **RFT / ITB** | Request for Tenders / Invitation to Bid — awarded on price alone |
| **Standing offer / framework agreement** | A pre-qualified arrangement allowing repeat purchases without full competition |
| **Supply arrangement** | A pre-qualification framework; doesn't guarantee any purchases |
| **Call-up** | An individual purchase order under a standing offer |
| **Task authorization** | An order for specific work under a task-based contract (common in IT professional services); each task is often treated as a separate contract |
| **TBIPS** | Task-Based Informatics Professional Services — the Canadian federal standing offer vehicle for IT consulting |
| **ProServices** | A Canadian federal supply arrangement for professional services (non-IT) |
| **Sole source / direct award** | Non-competitive award to a specific vendor |
| **ACAN** | Advance Contract Award Notice — Canada's mechanism to post-justify sole source |
| **PSPC** | Public Services and Procurement Canada — Canada's central procurement agency |
| **CanadaBuys** | Canada's federal procurement portal for notices, standing offers, and awarded contracts (successor to Buyandsell.gc.ca) |
| **Crown Commercial Service (CCS)** | UK's central procurement body |
| **GSA** | US General Services Administration — manages federal purchasing |
| **GSA Schedule** | US pre-negotiated contracts from which agencies can buy |
| **IDIQ** | Indefinite Delivery, Indefinite Quantity contract — a US master contract with a ceiling value |
| **Contracts Finder** | The UK government's database of contracts over £10,000 |
| **TED** | Tenders Electronic Daily — the EU's official database of public procurement notices and awards |
| **Proactive / routine disclosure** | Government requirement to publish contract awards above a threshold |
| **Amendment** | A modification to an existing contract |
| **Fairness monitor** | An independent observer on large procurements — their reports are often obtainable via access to information |

---

## Relationships to extract

1. **Company → Person**: Vendor principal, the specific public servant who signed the contract, the contracting officer
2. **Company → Company**: Subcontractors named in the contract or proposal (often disclosed in competitive bids)
3. **Person → Company**: Former public servants now working for vendors — note their former department and role
4. **Company → Transaction**: Every contract, amendment, call-up, and payment with value and date
5. **Department → Vendor**: Contracting relationship (with contract type and period)
6. **Contract → Amendment**: Each amendment (number, date, change in value or scope)
7. **Vendor → Performance report**: Performance rating and any corrective action required

---

## What investigators typically miss

1. **The evaluation report** — in competitive procurements, an evaluation report scores the bids. This is often obtainable via access to information and shows why the winner won and by what margin.
2. **The losing bidders** — in open competitions, unsuccessful bids may be obtainable via access to information. Comparing bid prices reveals whether the winner's price was genuinely competitive.
3. **Amendment history** — a single contract record shows the current value. The amendment history shows how the value grew over time and whether each increase was justified.
4. **Related contracts to the same vendor** — the contract you have is one; the full picture requires checking all contracts to the same vendor across departments over the same period. Total spend is more newsworthy than any individual contract. Note that Crown corporations and state enterprises are sometimes exempt from standard procurement rules and disclosure requirements, so their contracts warrant separate research.
5. **Conflict of interest declarations and attestations** — evaluation committee members must declare conflicts, and contractors typically attest that they have none. If a member had a relationship with the winning vendor, or a contractor conflict later emerges, those documents show it was either undisclosed or not caught.
6. **Lobbying registry** — before a major contract award, check the relevant lobbyist registries for registrations by the winning vendor or their lobbyists targeting the awarding department.
7. **The contract itself vs. the award notice** — the award notice is the public record; the actual contract may contain very different terms. Request the actual contract via access to information.
8. **Subcontract flow-through** — a contract awarded to a large company that subcontracts most of the work to a small company owned by a related party. The public record shows the large company; the actual beneficiary is obscured.

---

## Sources and further reading

### Official and regulatory
- [Proactive Disclosure — Open Government Portal (Canada)](https://open.canada.ca/en/proactive-disclosure) — Central hub for Canada's proactive publication requirements; includes contracts over $10,000, travel and hospitality expenses, and grant data
- [Proactive Publication — Contracts (Canada Open Government)](https://open.canada.ca/data/en/dataset/d8f85d91-7dec-4fd1-8055-483b77225d8b) — The dataset itself: quarterly contract disclosures from all federal departments, downloadable as CSV; useful for bulk analysis of vendor patterns, amendment histories, and department-level spending trends
- [CanadaBuys](https://canadabuys.canada.ca/en) — Canada's procurement portal for tender notices, standing offers, supply arrangements, and contract history
- [USASpending.gov](https://www.usaspending.gov/) — Official US federal spending database covering contracts, grants, and loans from fiscal year 2001 onward, including IDIQ task orders and GSA Schedule purchases; searchable by agency, vendor, and award type
- [OECD Recommendation of the Council on Public Procurement](https://legalinstruments.oecd.org/en/instruments/OECD-LEGAL-0411) — The 2015 OECD Council recommendation setting twelve integrated principles for transparent, accountable, and integrity-focused public procurement; adopted by all OECD members
- [OECD Principles for Integrity in Public Procurement](https://www.oecd.org/en/publications/oecd-principles-for-integrity-in-public-procurement_9789264056527-en.html) — Comprehensive framework and risk checklist covering the full procurement cycle; maps corruption risks useful to auditors and journalists alike

### Practitioner and public interest
- [Open Contracting Data Standard (OCDS)](https://www.open-contracting.org/data-standard/) — The only international open standard for publishing planning, tender, award, and contract implementation data; endorsed by the G20 and G7 and implemented by 50+ governments
- [Open Contracting Partnership](https://www.open-contracting.org/) — Nonprofit that promotes transparent and fair public contracting globally; publishes guidance, red-flag indicators for every phase of the contracting process, and country-level implementation reports
- [Government Defence Integrity Index — Transparency International Defence & Security](https://ti-defence.org/what-we-do/responsible-defence-governance/government-defence-integrity-index-gdi/) — Assesses corruption risk across procurement, personnel, finance, and operations in defence institutions in roughly 90 countries; useful benchmark for defence and security contract investigations

### Journalism resources
- [GIJN — Tracking Government Contracts](https://gijn.org/resource/tracking-covid-19-contracts-a-gijn-guide-and-webinar) — Global Investigative Journalism Network guide to researching government procurement stories; covers red flags for bid-rigging, collusion, and fraud across the five phases of the contracting process, including post-award implementation
