# Domain knowledge — Government contracts and procurement records

Loaded by `/ingest` when the document type is a procurement record, tender document, contract award, ATIP/FOIA response, or similar government contracting record.

---

## Document types covered

- Request for proposals (RFPs) and request for tenders (RFTs)
- Standing offers and supply arrangements
- Contract award notices
- Sole-source justifications
- Contract amendments and modifications
- Proactive disclosure records (Canada: contracts over $10K)
- Federal Accountability Act disclosures
- ATIP/FOIA responses containing contract documents
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
| **Procurement method** | Competitive (open bidding), limited tender, sole source |
| **GSIN / commodity code** | Federal commodity classification |
| **Contract number** | Unique identifier |
| **Amendment numbers** | Sequence of modifications to the original contract |

---

## Red flags

### Procurement method

- **Sole source without adequate justification** — the government bypassed competition. Justifications include national security, only one supplier, and urgency. Weak or circular justifications are red flags.
- **Splitting contracts** — dividing one large contract into multiple smaller contracts to avoid competitive procurement thresholds. Watch for multiple contracts with the same vendor at the same time for similar work.
- **Urgency used repeatedly** — a department that regularly invokes urgency to sole-source may be using it to direct work to preferred vendors.
- **"Advanced contract award notice" (ACAN)** — Canada's mechanism to sole-source while appearing to compete. A 15-day notice is posted; if a competitor challenges, a real competition must occur. Few contractors bother to challenge. Watch for ACANs issued at holidays or other low-attention periods.

### Contract amendments

- **Amendments exceeding the original value** — common mechanism to expand a contract after award. If the original was below a competitive threshold and amendments push it well above, competition was effectively bypassed.
- **Many small amendments** — death by a thousand amendments. Ten amendments of $50K each on a $100K contract have increased its value by 500% without triggering a new competition.
- **Scope creep** — amendments that add work unrelated to the original contract description.
- **Retroactive amendments** — amendments dated before the services were actually added, suggesting the work was done without authorization.

### Vendor patterns

- **Same vendor winning repeatedly in a department** — may indicate an appropriate relationship (incumbent advantage is real), but warrants scrutiny.
- **Vendor with a single director who is a former public servant** — revolving door concerns; check the Conflict of Interest Act and post-employment restrictions.
- **Numbered companies** — contracts awarded to numbered companies with no clear principal. Check corporate registry to find directors.
- **Address issues** — vendor's address is a residence, a UPS Store, or the same as another frequent contractor. May indicate a shell or pass-through entity.
- **Vendor registered after RFP posted** — the company was incorporated specifically to pursue this contract. Not inherently improper but worth noting.

### Value anomalies

- **Round-number contract values** — $499,999 or $24,999 may indicate the contracting officer is aware of thresholds and deliberately staying under them.
- **Proactive disclosure vs. contract management system discrepancies** — if the declared value in proactive disclosure doesn't match other records, one of them is wrong.
- **Long contract periods with no competitive renewal** — a contract that has been "amended and extended" for years without re-competition.

---

## Threshold awareness (Canada — federal)

These thresholds trigger competition requirements. Contracts deliberately kept below them warrant scrutiny:

| Type | Competitive threshold |
|------|--------------------|
| Goods | $25,000 |
| Services | $25,000 |
| Construction | $100,000 |
| CETA/CFTA covered | $30,900 (goods) / $121,200 (services) |

Provincial thresholds vary. Municipal thresholds vary further.

---

## Terminology

| Term | Meaning |
|------|---------|
| **RFP** | Request for Proposals — evaluates both technical approach and price |
| **RFT / ITB** | Request for Tenders / Invitation to Bid — awarded on price alone |
| **Standing offer** | A pre-qualified arrangement allowing repeat purchases without full competition |
| **Supply arrangement** | A pre-qualification framework; doesn't guarantee any purchases |
| **Sole source** | Non-competitive award to a specific vendor |
| **ACAN** | Advance Contract Award Notice — Canada's mechanism to post-justify sole source |
| **PSPC** | Public Services and Procurement Canada — the central procurement agency |
| **Proactive disclosure** | Canada's requirement to publish contracts over $10K quarterly |
| **ATIP** | Access to Information and Privacy — Canada's FOI mechanism |
| **Amendment** | A modification to an existing contract |
| **Task authorization** | An order under a standing offer; each task is often treated as a separate contract |
| **Evaluation criteria** | The scoring system used to select a winner in a competitive process |
| **Fairness monitor** | An independent observer on large procurements — their reports are often obtainable via ATIP |

---

## Relationships to extract

1. **Company → Person**: Vendor principal, the specific public servant who signed the contract, the contracting officer
2. **Company → Company**: Subcontractors named in the contract or proposal (often disclosed in competitive bids)
3. **Person → Company**: Former public servants now working for vendors — note their former department and role
4. **Company → Transaction**: Every contract, amendment, and payment with value and date

---

## What investigators typically miss

1. **The evaluation report** — in competitive procurements, an evaluation report scores the bids. This is often obtainable via ATIP and shows why the winner won and by what margin.
2. **The losing bidders** — in open competitions, unsuccessful bids may be obtainable via ATIP or freedom of information. They provide context for whether the winner's price was competitive.
3. **Conflict of interest declarations** — evaluation committee members must declare conflicts. If a member had a relationship with the winning vendor, this is usually documented.
4. **Lobbying registry** — before a major contract award, check the federal and provincial lobbyist registries for registrations by the winning vendor or their lobbyists targeting the awarding department.
5. **The contract itself vs. the award notice** — the award notice is the public record; the actual contract may contain very different terms. Request the actual contract via ATIP.
6. **Subcontract flow-through** — a contract awarded to a large company that subcontracts most of the work to a small company owned by a related party. The public record shows the large company; the actual beneficiary is obscured.
7. **Crown corporation contracts** — Crown corporations are sometimes exempt from standard government procurement rules and proactive disclosure requirements. They warrant separate research.
