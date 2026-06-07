# Domain knowledge — Corporate filings

This skill is loaded by `/ingest` when the document type is an annual report, corporate registration, director filing, or similar corporate record.

Apply this knowledge in addition to the standard extraction process. It tells you what to look for, what terminology means, and what patterns are worth flagging.

---

## Document types covered

- Annual reports (public and private companies)
- Corporate registrations and certificates of incorporation
- Director / officer filings (appointments, resignations, changes)
- Shareholder registers
- Corporate search results (Ontario, BC, Alberta; SEC EDGAR; Companies House UK)
- Beneficial ownership declarations
- Agent for service of process filings

---

## Always-present fields to extract

These fields appear in virtually every corporate filing. Extract them even when the document doesn't highlight them prominently:

| Field | What to look for |
|-------|-----------------|
| **Registered name** | Exact legal name, including punctuation (Ltd., Inc., Corp., LP) |
| **Registration number** | Ontario: 7 digits. BC: 7 digits prefixed BC. Federal (Canada): 9 digits. US: varies by state. |
| **Jurisdiction** | Where the company is legally registered, not where it operates |
| **Registered address** | The official address on file — may differ from operational address |
| **Date of incorporation** | When the company was formed |
| **Company status** | Active, dissolved, amalgamated, extra-provincial |
| **Directors** | Full legal name, address, date of appointment |
| **Officers** | President, Secretary, CFO etc. — often different from directors |
| **Registered agent / agent for service** | The person or firm authorized to receive legal documents |
| **Share structure** | Classes of shares, authorized and issued counts |
| **Fiscal year end** | Usually December 31 but not always |

---

## Red flags — what to look for

### Director and officer patterns

- **Director with no address, or a PO box as address** — may indicate a nominee director (a person who lends their name to a company but has no real involvement). Common in shell company structures.
- **Same person as director of 3+ companies** — especially if those companies are in unrelated industries or different jurisdictions.
- **Director appointed and resigned within 12 months** — rapid turnover can indicate a company being set up and wound down quickly.
- **Director change near a significant event** — a change right before or after a large transaction, dissolution, or court filing.
- **Director whose address matches the company's registered address** — can indicate the director's address is fictitious.

### Address patterns

- **Multiple companies at the same address** — especially if it's a residential address, a UPS Store, or a law firm. This is normal for a law firm serving as registered agent; it's significant if the companies otherwise appear unrelated.
- **Registered address in a jurisdiction with no apparent business connection** — e.g. a company claiming Ontario registration but all directors and operations are elsewhere.
- **Registered address that doesn't exist** — a non-existent street number or a demolished building.

### Share structure

- **Bearer shares** — shares not registered to a specific owner, making it impossible to trace beneficial ownership. Illegal in most Canadian and UK jurisdictions; a red flag in jurisdictions that still permit them.
- **Voting rights disproportionate to ownership** — a person holding 1% of shares but 51% of votes. This is a control mechanism worth noting.
- **Shares issued to another company** — the parent company might itself be in a jurisdiction with minimal disclosure requirements.

### Financial red flags (in annual reports)

- **Going concern qualification** — auditor notes uncertainty about whether the company can continue operating. Look for the phrase "going concern" in the auditor's report.
- **Revenue declining year-over-year while debt increases** — the opposite of what a healthy company looks like.
- **Related party transactions** — transactions between the company and its directors, officers, or their family members. Required to be disclosed in Canadian/US reporting; a red flag when large.
- **Auditor change** — a change in auditor mid-year or in the same year as a significant financial event.
- **Unusual accounting periods** — a fiscal year that doesn't align with the calendar year, especially one changed recently.
- **"Subsequent events" disclosures** — events after the balance sheet date that materially affect the company. These are buried at the end of notes and frequently overlooked.

### Amalgamations and continuances

- **Amalgamation** — two or more companies merging into one. The predecessor companies cease to exist. This can be used to obscure a company's history.
- **Continuance** — a company moving its legal domicile to a different jurisdiction. Watch for continuances into less transparent jurisdictions.
- **Name change** — a company changing its name. Prior names are aliases and should be recorded. A series of name changes may indicate an attempt to distance from a reputation.

---

## Canadian-specific terminology

| Term | Meaning |
|------|---------|
| **Extra-provincial registration** | A company incorporated elsewhere that has registered to do business in this province |
| **NUANS** | Canada's name search system — a NUANS report shows other companies with similar names |
| **Articles of incorporation** | The founding document — sets out the company's share structure and purpose |
| **Notice of directors** | A filed document listing current directors (Ontario: Form 1 under the Business Corporations Act) |
| **Annual return** | Annual filing confirming the company is still active and updating director/address info |
| **Ontario Business Number** | 9-digit number assigned by CRA; the first 9 digits of the BN15 used for tax remittances |
| **SEDAR / SEDAR+** | System for Electronic Document Analysis and Retrieval — Canada's equivalent of EDGAR for public companies |

## US-specific terminology

| Term | Meaning |
|------|---------|
| **Registered agent** | The person or company authorized to receive legal documents on the company's behalf |
| **Annual report vs. 10-K** | Annual report is the glossy document for shareholders; 10-K is the detailed SEC filing with full financial disclosure |
| **Articles of organization** | The founding document for an LLC (equivalent to Articles of Incorporation for a corporation) |
| **Operating agreement** | LLC internal governance document — often not publicly filed |
| **EIN** | Employer Identification Number — US equivalent of a Business Number |
| **Beneficial ownership** | Under FinCEN rules (effective 2024), companies with fewer than 20 employees must report beneficial owners to FinCEN |

---

## Relationships to extract from corporate filings

Beyond the standard entity extraction, specifically look for and record:

1. **Person → Company**: Director, Officer (with title), Shareholder (with share percentage if stated), Registered Agent, Signing Officer
2. **Company → Address**: Registered address, Principal place of business (if different), Previous registered address (if shown)
3. **Company → Company**: Parent/subsidiary (if shares held by another company), Amalgamation predecessor/successor
4. **Person → Address**: Director's stated address (extract even if it matches company address — that match is itself notable)

---

## What investigators typically miss

1. **The notes to financial statements** — the main financial tables are usually clean; the real information is buried in the notes. Read them fully.
2. **Previous auditor opinions** — compare the current auditor's opinion to prior years. A shift from clean to qualified is significant.
3. **The "related party" note** — required disclosure in Canadian/US accounting; often contains the most useful information about who benefits from the company's operations.
4. **The signing block** — who signed the filing and in what capacity. If a director signs an annual report but is also a named defendant in a litigation disclosed in the same report, that's notable.
5. **Date of the annual general meeting** — companies are required to hold an AGM annually. If the AGM date is missing or the filing is very late, it may indicate the company is not being actively maintained.
6. **The auditor's address** — a major accounting firm (Deloitte, KPMG, EY, PWC, BDO, Grant Thornton) has different implications than an unknown sole practitioner with the same address as the company.
