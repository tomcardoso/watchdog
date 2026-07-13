---
description: a procurement record, tender document, contract award, sole-source justification, standing offer or call-up, task authorization, contract amendment, vendor performance report, or similar government contracting record, plus bidder-side records — a bid or proposal, RFI/RFQ response, teaming agreement, debriefing letter, or bid protest — the full procurement lifecycle, pre-award and post-award
---
# Domain knowledge — Government contracts and procurement records

Loaded by Watchdog when the document type is a procurement record, tender document, contract award, sole-source justification, standing offer, task authorization, contract amendment, vendor performance report, or similar government contracting record — pre-award or post-award, whether produced by the government or by a bidder (a bid or proposal, teaming agreement, debriefing letter, or bid protest).

---

## Document types covered

- Requests for proposals (RFPs) and invitations to tender / bid
- Bids and proposals submitted by vendors — technical and financial volumes, and responses to requests for information (RFIs) or quotations (RFQs)
- Teaming agreements and joint-venture agreements between bidders
- Debriefing letters to unsuccessful bidders
- Bid protests and procurement challenges, and the tribunal or court decisions they produce
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
| **Proposed key personnel** | In a bid: the named individuals, their roles, and the qualifications claimed for them |
| **Teaming partners / proposed subcontractors** | In a bid or teaming agreement: the companies named to deliver part of the work |
| **Bid price** | Total bid or evaluated price, and the pricing breakdown where present |
| **Past-performance references** | Prior contracts the bidder cites as qualifications — client, value, period |
| **Attestations** | Certificates of independent bid determination, integrity declarations, conflict-of-interest attestations |

---

## Red flags

### Procurement method

- **Sole source without adequate justification** — the government bypassed competition. Justifications typically include national security, only one supplier, or genuine urgency. Weak or circular justifications are red flags.
- **Splitting contracts** — dividing one large contract into multiple smaller contracts to avoid competitive procurement thresholds. Watch for multiple contracts with the same vendor at the same time for similar work.
- **Urgency used repeatedly** — a department that regularly invokes urgency to sole-source may be using it to direct work to preferred vendors.
- **Advance contract award notice (ACAN) or equivalent** — some jurisdictions allow a mechanism to post-justify a sole source by publishing a brief notice. Few competitors bother to challenge them.

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

### Bid documents

When the bid itself is available — leaked, shared by a losing bidder, or obtained via access to information:

- **Bait-and-switch key personnel** — impressive résumés win the technical evaluation, then the named individuals are replaced after award with cheaper or more junior staff. Record who the bid proposed so they can be compared against the people who appear in task authorizations, amendments, or invoices.
- **Past-performance references from related parties** — the reference contracts the bidder cites trace back to companies with the same principals, or to clients with ties to the bidder.
- **Teaming with the incumbent** — a "new" competitor whose bid depends on the incumbent as a subcontractor; the competition is narrower than the bidder list suggests.
- **Bid tracks unpublished requirements** — a proposal that anticipates requirements, terminology, or evaluation criteria not found in the published solicitation suggests the bidder had inside information or helped shape the requirements.

### Bid rigging and collusion

Collusion patterns usually only become visible across several bids or award notices, not in one document — record what this document shows and flag the comparison as a lead:

- **Cover bidding** — a bid that appears designed to lose: non-compliant on a mandatory criterion, priced far above the winner, or far thinner than a genuine pursuit would justify. Its purpose is to make a pre-arranged competition look real.
- **Common authorship across competitors** — identical wording, formatting, typos, or file metadata in bids from supposedly competing companies.
- **Shared people or addresses across rivals** — the same personnel, address, phone number, or contact details appearing in bids from different bidders.
- **Bid rotation** — across a series of similar competitions, the same small group of bidders takes turns winning at similar prices.
- **Lowball then change orders** — a winning bid conspicuously below the others, followed by amendments that lift the value past what the losing bidders offered (see the amendments section above).

### Vendor performance

- **Satisfactory rating despite documented issues** — a vendor performance report that notes delivery failures or quality problems but rates the vendor "satisfactory" overall may reflect reluctance to formally record a poor rating (which would affect future competitions).
- **Corrective action plans not followed up** — a corrective action plan was required but the file contains no record of whether it was implemented or the performance improved.
- **Contract above the performance-reporting threshold** — for contracts above the relevant threshold, performance reports are required. Record the contract value and note that reports are expected; whether they were filed is an absence a single document can't show, so flag it as a lead to check against the department's records — missing reports may mean the contract went unmonitored.

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
| **RFI** | Request for Information — a market-sounding exercise before a formal procurement; responses often shape the eventual RFP |
| **RFQ** | Request for Quotation — a price-focused solicitation for well-defined goods or services |
| **Mandatory vs. rated criteria** | Mandatory criteria are pass/fail — a bid failing one is eliminated regardless of merit; rated criteria are scored |
| **Compliance matrix** | A table in a bid mapping each solicitation requirement to the proposal section that addresses it |
| **BAFO** | Best and Final Offer — a revised bid submitted after negotiations or clarifications |
| **Teaming agreement** | A pre-bid agreement between companies to pursue a contract together, typically as prime contractor and subcontractors |
| **Bid bond / performance bond** | Financial guarantees that a bidder will honour its bid / that the contractor will complete the work |
| **Debriefing** | The explanation a losing bidder can request on why it lost — often produces a written letter worth obtaining |
| **Bid protest / procurement challenge** | A formal challenge to a solicitation or award — heard by the GAO in the US, the CITT in Canada, and courts or review bodies elsewhere |
| **Certificate of independent bid determination** | A signed attestation that a bid was prepared without coordination with competitors |
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
| **Fairness monitor** | An independent observer on large procurements — their reports may be public or obtainable via access to information |

---

## Relationships to extract

1. **Company → Person**: Vendor principal, the specific public servant who signed the contract, the contracting officer
2. **Company → Company**: Subcontractors and teaming partners may be named in the contract, bid, or teaming agreement
3. **Person → Company**: Former public servants now working for vendors — note their former department and role
4. **Company → Transaction**: Every contract, amendment, call-up, and payment with value and date
5. **Department → Vendor**: Contracting relationship (with contract type and period)
6. **Contract → Amendment**: Each amendment (number, date, change in value or scope)
7. **Vendor → Performance report**: Performance rating and any corrective action required
8. **Company → Person**: Key personnel proposed in a bid, with the role and qualifications claimed

---

## What investigators typically miss

1. **The evaluation report** — in competitive procurements, an evaluation report scores the bids. This is often obtainable via access to information and shows why the winner won and by what margin.
2. **The losing bidders** — in open competitions, unsuccessful bids may be obtainable via access to information, and so are the debriefing letters sent to losing bidders. Comparing bid prices reveals whether the winner's price was genuinely competitive.
3. **Amendment history** — a single contract record shows the current value. The amendment history shows how the value grew over time and whether each increase was justified.
4. **Related contracts to the same vendor** — the contract you have is one; the full picture requires checking all contracts to the same vendor across departments over the same period. Total spend is more newsworthy than any individual contract. Note that Crown corporations and state enterprises are sometimes exempt from standard procurement rules and disclosure requirements, so their contracts warrant separate research.
5. **Conflict of interest declarations and attestations** — evaluation committee members must declare conflicts, and contractors typically attest that they have none. If a member had a relationship with the winning vendor, or a contractor conflict later emerges, those documents show it was either undisclosed or not caught.
6. **Lobbying registry** — before a major contract award, check the relevant lobbyist registries for registrations by the winning vendor or their lobbyists targeting the awarding department.
7. **The contract itself vs. the award notice** — the award notice is the public record; the actual contract may contain very different terms. Request the actual contract via access to information.
8. **Subcontract flow-through** — a contract awarded to a large company that subcontracts most of the work to a small company owned by a related party. The public record shows the large company; the actual beneficiary is obscured.
9. **Bid protest decisions** — procurement tribunals publish their decisions (the GAO in the US, the CITT in Canada). A protest over the contract at hand — or an earlier one involving the same vendor or department — dissects the procurement's conduct in detail, at no cost.

---

## Sources and further reading

### Official and regulatory
- [Proactive Disclosure — Open Government Portal (Canada)](https://open.canada.ca/en/proactive-disclosure) — Central hub for Canada's proactive publication requirements; includes contracts over $10,000, travel and hospitality expenses, and grant data
- [Proactive Publication — Contracts (Canada Open Government)](https://open.canada.ca/data/en/dataset/d8f85d91-7dec-4fd1-8055-483b77225d8b) — The dataset itself: quarterly contract disclosures from all federal departments, downloadable as CSV; useful for bulk analysis of vendor patterns, amendment histories, and department-level spending trends
- [CanadaBuys](https://canadabuys.canada.ca/en) — Canada's procurement portal for tender notices, standing offers, supply arrangements, and contract history
- [USASpending.gov](https://www.usaspending.gov/) — Official US federal spending database covering contracts, grants, and loans from fiscal year 2001 onward, including IDIQ task orders and GSA Schedule purchases; searchable by agency, vendor, and award type
- [OECD Recommendation of the Council on Public Procurement](https://legalinstruments.oecd.org/en/instruments/OECD-LEGAL-0411) — The 2015 OECD Council recommendation setting twelve integrated principles for transparent, accountable, and integrity-focused public procurement; adopted by all OECD members
- [OECD Principles for Integrity in Public Procurement](https://www.oecd.org/en/publications/oecd-principles-for-integrity-in-public-procurement_9789264056527-en.html) — Comprehensive framework and risk checklist covering the full procurement cycle; maps corruption risks useful to auditors and journalists alike
- [OECD Guidelines for Fighting Bid Rigging in Public Procurement (2025 Update)](https://www.oecd.org/en/publications/2025/09/oecd-guidelines-for-fighting-bid-rigging-in-public-procurement-2025-update_127880ea.html) — Includes the Bid-Rigging Detection List, a checklist of red flags for cover bidding, bid suppression, rotation, and suspicious pricing patterns; the source for most of the collusion indicators above
- [Bid Protests — U.S. Government Accountability Office](https://www.gao.gov/legal/bid-protests) — Searchable database of published GAO bid protest decisions and the active-case docket
- [Canadian International Trade Tribunal](https://www.citt-tcce.gc.ca/en) — Hears procurement complaints against Canadian federal contracting; publishes its determinations and notices of inquiry

### Practitioner and public interest
- [Open Contracting Data Standard (OCDS)](https://www.open-contracting.org/data-standard/) — The only international open standard for publishing planning, tender, award, and contract implementation data; endorsed by the G20 and G7 and implemented by 50+ governments
- [Open Contracting Partnership](https://www.open-contracting.org/) — Nonprofit that promotes transparent and fair public contracting globally; publishes guidance, red-flag indicators for every phase of the contracting process, and country-level implementation reports
- [Government Defence Integrity Index — Transparency International Defence & Security](https://ti-defence.org/what-we-do/responsible-defence-governance/government-defence-integrity-index-gdi/) — Assesses corruption risk across procurement, personnel, finance, and operations in defence institutions in roughly 90 countries; useful benchmark for defence and security contract investigations

### Journalism resources
- [GIJN — Tracking Government Contracts](https://gijn.org/resource/tracking-covid-19-contracts-a-gijn-guide-and-webinar) — Global Investigative Journalism Network guide to researching government procurement stories; covers red flags for bid-rigging, collusion, and fraud across the five phases of the contracting process, including post-award implementation

**Notes on unsourced claims.** Two bid-document red flags rest on practitioner experience rather than a specific citable source: past-performance references from related parties, and teaming with the incumbent narrowing a competition. Bait-and-switch of key personnel is a recognized ground of protest in GAO case law (searchable in the GAO decisions database above); the remaining collusion indicators trace to the OECD Bid-Rigging Detection List and the GIJN guide.
