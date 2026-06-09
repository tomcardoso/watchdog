# Domain knowledge — Government contracts and procurement records

Loaded by `/ingest` when the document type is a procurement record, tender document, contract award, access-to-information/FOIA response, or similar government contracting record.

---

## Document types covered

- Requests for proposals (RFPs) and invitations to tender / bid
- Standing offers and framework agreements
- Contract award notices
- Sole-source / direct award justifications
- Contract amendments and modifications
- Routine or proactive disclosure records
- Access to information / FOIA responses containing contract documents
- Audit and evaluation reports on procurement
- Lobbyist registry filings related to contracts

---

## Always-present fields to extract

| Field | What to look for |
|-------|-----------------|
| **Contracting authority** | The government department or agency |
| **Vendor / contractor** | The company or individual awarded the contract |
| **Contract value** | Original contract value |
| **Amendment amounts** | Each amendment's value and new total |
| **Contract period** | Start and end dates |
| **Description of work** | What is being procured |
| **Procurement method** | Competitive (open bidding), limited tender, sole source / direct award |
| **Commodity code** | Federal commodity classification (where applicable) |
| **Contract number** | Unique identifier |
| **Amendment numbers** | Sequence of modifications to the original contract |

---

## Red flags

### Procurement method

- **Sole source without adequate justification** — the government bypassed competition. Justifications typically include national security, only one supplier, or genuine urgency. Weak or circular justifications are red flags.
- **Splitting contracts** — dividing one large contract into multiple smaller contracts to avoid competitive procurement thresholds. Watch for multiple contracts with the same vendor at the same time for similar work.
- **Urgency used repeatedly** — a department that regularly invokes urgency to sole-source may be using it to direct work to preferred vendors.
- **Advance contract award notice (ACAN) or equivalent** — some jurisdictions allow a mechanism to post-justify a sole source by publishing a brief notice. These are often posted during low-attention periods. Few competitors bother to challenge them.

### Contract amendments

- **Amendments exceeding the original value** — a common mechanism to expand a contract after award. If the original was below a competitive threshold and amendments push it well above, competition was effectively bypassed.
- **Many small amendments** — ten amendments of $50K each on a $100K contract have increased its value by 500% without triggering a new competition.
- **Scope creep** — amendments that add work unrelated to the original contract description.
- **Retroactive amendments** — amendments dated before the services were actually added, suggesting the work was done without authorization.

### Vendor patterns

- **Same vendor winning repeatedly in a department** — may indicate an appropriate relationship (incumbent advantage is real), but warrants scrutiny.
- **Vendor with a principal who is a former public servant** — revolving door concerns; check post-employment restrictions.
- **Numbered companies** — contracts awarded to numbered companies with no clear principal. Check the corporate registry to find directors.
- **Address issues** — vendor's address is a residence, a mailbox service, or the same as another frequent contractor. May indicate a shell or pass-through entity.
- **Vendor registered after RFP posted** — the company was incorporated specifically to pursue this contract. Not inherently improper but worth noting.

### Value anomalies

- **Round-number contract values just below thresholds** — a contract value of $24,999 or $499,999 may indicate the contracting officer is aware of thresholds and deliberately staying under them.
- **Long contract periods with no competitive renewal** — a contract that has been "amended and extended" for years without re-competition.

---

## Competitive thresholds

Most governments set thresholds above which a competitive process is required. Contracts deliberately kept below these warrant scrutiny. Thresholds vary by jurisdiction and procurement type — always check the applicable rules. Common examples:

| Jurisdiction | Goods | Services | Construction |
|-------------|-------|----------|-------------|
| Canada (federal) | $25,000 | $25,000 | $100,000 |
| Canada (CETA/CFTA covered) | $30,900 | $121,200 | varies |
| US (federal, simplified acquisition) | $250,000 | $250,000 | $2,000 (Davis-Bacon) |
| EU (public contracts directive) | €143,000 (central govt) | €143,000 | €5.5M |

Provincial, state, and municipal thresholds vary further.

---

## Terminology

| Term | Meaning |
|------|---------|
| **RFP** | Request for Proposals — evaluates both technical approach and price |
| **RFT / ITB** | Request for Tenders / Invitation to Bid — awarded on price alone |
| **Standing offer / framework agreement** | A pre-qualified arrangement allowing repeat purchases without full competition |
| **Supply arrangement** | A pre-qualification framework; doesn't guarantee any purchases |
| **Sole source / direct award** | Non-competitive award to a specific vendor |
| **ACAN** | Advance Contract Award Notice — Canada's mechanism to post-justify sole source |
| **PSPC** | Public Services and Procurement Canada — Canada's central procurement agency |
| **Crown Commercial Service (CCS)** | UK's central procurement body |
| **GSA** | US General Services Administration — manages federal purchasing |
| **Proactive / routine disclosure** | Government requirement to publish contract awards above a threshold |
| **Amendment** | A modification to an existing contract |
| **Task authorization** | An order under a standing offer; each task is often treated as a separate contract |
| **Fairness monitor** | An independent observer on large procurements — their reports are often obtainable via access to information |

---

## Relationships to extract

1. **Company → Person**: Vendor principal, the specific public servant who signed the contract, the contracting officer
2. **Company → Company**: Subcontractors named in the contract or proposal (often disclosed in competitive bids)
3. **Person → Company**: Former public servants now working for vendors — note their former department and role
4. **Company → Transaction**: Every contract, amendment, and payment with value and date

---

## What investigators typically miss

1. **The evaluation report** — in competitive procurements, an evaluation report scores the bids. This is often obtainable via access to information and shows why the winner won and by what margin.
2. **The losing bidders** — in open competitions, unsuccessful bids may be obtainable via access to information. They provide context for whether the winner's price was competitive.
3. **Conflict of interest declarations** — evaluation committee members must declare conflicts. If a member had a relationship with the winning vendor, this is usually documented.
4. **Lobbying registry** — before a major contract award, check the relevant lobbyist registries for registrations by the winning vendor or their lobbyists targeting the awarding department.
5. **The contract itself vs. the award notice** — the award notice is the public record; the actual contract may contain very different terms. Request the actual contract via access to information.
6. **Subcontract flow-through** — a contract awarded to a large company that subcontracts most of the work to a small company owned by a related party. The public record shows the large company; the actual beneficiary is obscured.
7. **State enterprise or Crown corporation contracts** — these bodies are sometimes exempt from standard government procurement rules and disclosure requirements. They warrant separate research.

---

## Sources and further reading

### Official and regulatory
- [Proactive Disclosure — Open Government Portal (Canada)](https://open.canada.ca/en/proactive-disclosure) — Central hub for Canada's proactive publication requirements; includes contracts over $10,000, travel and hospitality expenses, and grant data
- [Proactive Publication — Contracts (Canada Open Government)](https://open.canada.ca/data/en/dataset/d8f85d91-7dec-4fd1-8055-483b77225d8b) — The dataset itself: quarterly contract disclosures from all federal departments, downloadable as CSV; updated within 30 days of each quarter's end
- [USASpending.gov](https://www.usaspending.gov/) — Official US federal spending database covering contracts, grants, and loans from fiscal year 2001 onward; searchable by agency, vendor, and award type
- [OECD Recommendation of the Council on Public Procurement](https://legalinstruments.oecd.org/en/instruments/OECD-LEGAL-0411) — The 2015 OECD Council recommendation setting twelve integrated principles for transparent, accountable, and integrity-focused public procurement; adopted by all OECD members
- [OECD Principles for Integrity in Public Procurement](https://www.oecd.org/en/publications/oecd-principles-for-integrity-in-public-procurement_9789264056527-en.html) — Comprehensive framework and risk checklist covering the full procurement cycle; maps corruption risks useful to auditors and journalists alike

### Practitioner and public interest
- [Open Contracting Data Standard (OCDS)](https://www.open-contracting.org/data-standard/) — The only international open standard for publishing planning, tender, award, and contract implementation data; endorsed by the G20 and G7 and implemented by 50+ governments
- [Open Contracting Partnership](https://www.open-contracting.org/) — Nonprofit that promotes transparent and fair public contracting globally; publishes guidance, red-flag indicators, and country-level implementation reports
- [Government Defence Integrity Index — Transparency International Defence & Security](https://ti-defence.org/what-we-do/responsible-defence-governance/government-defence-integrity-index-gdi/) — Assesses corruption risk across procurement, personnel, finance, and operations in defence institutions in roughly 90 countries; useful benchmark for defence and security contract investigations

### Journalism resources
- [GIJN — Tracking Government Contracts](https://gijn.org/resource/tracking-covid-19-contracts-a-gijn-guide-and-webinar) — Global Investigative Journalism Network guide to researching government procurement stories; covers red flags for bid-rigging, collusion, and fraud across the five phases of the contracting process
