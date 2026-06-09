# Domain knowledge — Corporate filings

This skill is loaded by `/ingest` when the document type is an annual report, corporate registration, director filing, or similar corporate record.

Apply this knowledge in addition to the standard extraction process. It tells you what to look for, what terminology means, and what patterns are worth flagging.

---

## Document types covered

- Annual reports (public and private companies)
- Corporate registrations and certificates of incorporation
- Director / officer filings (appointments, resignations, changes)
- Shareholder registers
- Corporate search results (national and provincial/state registries)
- Beneficial ownership declarations
- Agent for service of process filings
- In Canada: Ontario, BC, Alberta provincial registries; federal CBCA registry; SEDAR/SEDAR+ for public companies
- In the US: State corporate registries (Delaware, Nevada, etc.); SEC EDGAR for public companies
- In the UK: Companies House
- In Australia: ASIC business name and company registers

---

## Always-present fields to extract

These fields appear in virtually every corporate filing. Extract them even when the document doesn't highlight them prominently:

| Field | What to look for |
|-------|-----------------|
| **Registered name** | Exact legal name, including punctuation (Ltd., Inc., Corp., LP, GmbH, SAS, etc.) |
| **Registration number** | The regulator's unique identifier for the company |
| **Jurisdiction** | Where the company is legally registered, not where it operates |
| **Registered address** | The official address on file — may differ from operational address |
| **Date of incorporation** | When the company was formed |
| **Company status** | Active, dissolved, amalgamated, struck off, dormant |
| **Directors** | Full legal name, address, date of appointment |
| **Officers** | President, Secretary, CFO, etc. — often different from directors |
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

- **Multiple companies at the same address** — especially if it's a residential address, a mailbox service, or a law firm. Normal for a law firm serving as registered agent; significant if the companies otherwise appear unrelated.
- **Registered address in a jurisdiction with no apparent business connection** — e.g. a company claiming local registration but all directors and operations are elsewhere.
- **Registered address that doesn't exist** — a non-existent street number or a demolished building.

### Share structure

- **Bearer shares** — shares not registered to a specific owner, making it impossible to trace beneficial ownership. Prohibited in most major jurisdictions; a red flag where still permitted.
- **Voting rights disproportionate to ownership** — a person holding 1% of shares but 51% of votes. This is a control mechanism worth noting.
- **Shares issued to another company** — the parent company might itself be in a jurisdiction with minimal disclosure requirements.

### Financial red flags (in annual reports)

- **Going concern qualification** — auditor notes uncertainty about whether the company can continue operating. Look for the phrase "going concern" in the auditor's report.
- **Revenue declining year-over-year while debt increases** — the opposite of what a healthy company looks like.
- **Related party transactions** — transactions between the company and its directors, officers, or their family members. Required to be disclosed under most reporting standards; a red flag when large.
- **Auditor change** — a change in auditor mid-year or in the same year as a significant financial event.
- **"Subsequent events" disclosures** — events after the balance sheet date that materially affect the company. These are buried at the end of notes and frequently overlooked.

### Amalgamations and continuances

- **Amalgamation / merger** — two or more companies merging into one. The predecessor companies cease to exist. This can be used to obscure a company's history.
- **Continuance / redomiciliation** — a company moving its legal domicile to a different jurisdiction. Watch for continuances into less transparent jurisdictions.
- **Name change** — a company changing its name. Prior names are aliases and should be recorded. A series of name changes may indicate an attempt to distance from a reputation.

---

## Jurisdiction terminology

### Canada

| Term | Meaning |
|------|---------|
| **Extra-provincial registration** | A company incorporated elsewhere that has registered to do business in this province |
| **NUANS** | Canada's name search system — a NUANS report shows other companies with similar names |
| **Articles of incorporation** | The founding document — sets out the company's share structure and purpose |
| **Notice of directors** | A filed document listing current directors |
| **Annual return** | Annual filing confirming the company is still active and updating director/address info |
| **SEDAR / SEDAR+** | System for Electronic Document Analysis and Retrieval — Canada's equivalent of EDGAR for public companies |

### United States

| Term | Meaning |
|------|---------|
| **Registered agent** | The person or company authorized to receive legal documents on the company's behalf |
| **Annual report vs. 10-K** | Annual report is the glossy document for shareholders; 10-K is the detailed SEC filing with full financial disclosure |
| **Articles of organization** | The founding document for an LLC (equivalent to Articles of Incorporation for a corporation) |
| **Operating agreement** | LLC internal governance document — often not publicly filed |
| **EIN** | Employer Identification Number — US equivalent of a Business Number |
| **FinCEN BOI** | Beneficial Ownership Information — as of March 2025, the reporting requirement was removed for US domestic companies and applies only to foreign entities registered in the US |

### UK and others

| Term | Meaning |
|------|---------|
| **Companies House** | The UK corporate registry — publicly searchable for all UK registered companies |
| **Persons of Significant Control (PSC)** | UK requirement to disclose individuals who control more than 25% of shares or votes |
| **GmbH** | Gesellschaft mit beschränkter Haftung — German limited liability company |
| **SAS / SARL** | Société par actions simplifiée / Société à responsabilité limitée — French company forms |
| **ASIC** | Australian Securities and Investments Commission — the Australian corporate regulator |

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
3. **The "related party" note** — required disclosure under most accounting standards; often contains the most useful information about who benefits from the company's operations.
4. **The signing block** — who signed the filing and in what capacity. If a director signs an annual report but is also a named defendant in a litigation disclosed in the same report, that's notable.
5. **Date of the annual general meeting** — companies are required to hold an AGM annually. If the AGM date is missing or the filing is very late, it may indicate the company is not being actively maintained.
6. **The auditor's address** — a major accounting firm (Deloitte, KPMG, EY, PwC, BDO, Grant Thornton) has different implications than an unknown sole practitioner with the same address as the company.

---

## Sources and further reading

### Official and regulatory
- [FATF — The Misuse of Corporate Vehicles Including Trusts and Company Service Providers (2006)](https://www.fatf-gafi.org/en/publications/Methodsandtrends/Themisuseofcorporatevehiclesincludingtrustandcompanyserviceproviders.html) — canonical typologies report on nominee directors, shell companies, and bearer shares
- [FATF — Guidance on Beneficial Ownership of Legal Persons (2023)](https://www.fatf-gafi.org/content/dam/fatf-gafi/guidance/Guidance-Beneficial-Ownership-Legal-Persons.pdf) — current FATF standards for transparency of legal persons
- [FATF — Concealment of Beneficial Ownership (2018)](https://www.fatf-gafi.org/en/publications/methodsandtrends/documents/concealment-beneficial-ownership.html) — Egmont Group joint report on techniques used to hide ownership
- [FinCEN — Beneficial Ownership Information Reporting](https://www.fincen.gov/boi) — US BOI reporting rules; as of March 2025, the requirement applies only to foreign entities registered in the US, not domestic companies
- [IAS 24 — Related Party Disclosures (IFRS)](https://www.ifrs.org/issued-standards/list-of-standards/ias-24-related-party-disclosures/) — the accounting standard requiring disclosure of related party transactions
- [IAS 1 — Presentation of Financial Statements (IFRS)](https://www.ifrs.org/issued-standards/list-of-standards/ias-1-presentation-of-financial-statements/) — the accounting standard requiring going concern disclosure
- [Corporations Canada — Federal Corporate Registry](https://ised-isde.canada.ca/site/corporations-canada/en/corporations-canada) — search tool for federally incorporated Canadian entities (CBCA)
- [SEDAR+ — Canadian Public Company Filings](https://www.sedarplus.ca/home/) — securities filings for Canadian reporting issuers

### Practitioner and public interest
- [Open Ownership — Principles for Effective Beneficial Ownership Disclosure](https://www.openownership.org/en/principles/) — nine-principle framework for evaluating the quality of a jurisdiction's beneficial ownership regime
- [Global Witness — Anonymous Company Owners](https://www.globalwitness.org/en/campaigns/corruption-and-money-laundering/anonymous-company-owners/) — investigations and reports on shell company abuse across multiple jurisdictions

### Notes on unsourced claims
The claims that amalgamations and series of name changes are used to obscure corporate history are well-established practitioner knowledge but are not cited in a single canonical public document. Treat them as editorial observations pending a specific citation.
