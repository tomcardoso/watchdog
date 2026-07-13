---
description: a corporate registration, director filing, shareholder register, or beneficial-ownership record from a corporate registry; for securities disclosures use `regulatory-filings`, for annual reports or financial statements use `financial-statements`
---
# Domain knowledge — Corporate filings

This skill is loaded by Watchdog when the document type is a corporate registration, director filing, or similar corporate registry record.

For standalone financial statements or MD&A, see `financial-statements`; for securities-regulator disclosure documents (annual reports, material change reports, insider trading reports, prospectuses), see `regulatory-filings`.

---

## Document types covered

- Corporate registrations and certificates of incorporation
- Director / officer filings (appointments, resignations, changes)
- Shareholder registers
- Corporate search results (national and provincial/state registries)
- Beneficial ownership declarations
- Agent for service of process filings
- Certificates of dissolution, amalgamation, or continuance
- In Canada: provincial and territorial registries; federal registry (Corporations Canada); extra-provincial registration filings
- In the US: Secretary of State filings (Delaware, Nevada, California, etc.); statements of information / biennial reports; registered-agent records; FinCEN Beneficial Ownership Information (BOI) reports
- In the UK: Companies House
- In Australia: ASIC business name and company registers

---

## Fields to extract

| Field | What to look for |
|-------|-----------------|
| **Registered name** | Exact legal name, including punctuation (Ltd., Inc., Corp., LP, GmbH, SAS, etc.) |
| **Former names** | Former legal names for the business, if any |
| **Trade names** | Other names under which that business may operate, if any |
| **Corporate structure** | Names of businesses in which the company holds shares or for which it is a subsidiary |
| **Registration number** | The regulator's unique identifier for the company |
| **Jurisdiction** | Where the company is legally registered, not where it operates |
| **Registered address** | The official address on file — may differ from operational address |
| **Date of incorporation** | When the company was formed |
| **Company status** | Active, dissolved, amalgamated, struck off, dormant |
| **Directors** | Full legal name, address, date of appointment |
| **Officers** | President, Secretary, CFO, etc. — often different from directors |
| **Registered agent / agent for service** | The person or firm authorized to receive legal documents |
| **Share structure** | Classes of shares, authorized and issued counts |
| **Fiscal year end** | Often December 31, but not always |
| **Extra-provincial / foreign qualification** | Whether the company is registered to do business outside its home jurisdiction, and where |

---

## Red flags — what to look for

### Director and officer patterns

- **Director with no address, or a PO box as address** — may indicate a nominee director (a person who lends their name to a company but has no real involvement). Common in shell company structures.
- **Same person as director of multiple companies** — record each directorship and flag the count when the same person appears as director of three or more companies. Whether those companies span unrelated industries or different jurisdictions is a judgment for a human to make on the recorded list.
- **Director appointed and resigned within 12 months** — rapid turnover can indicate a company being set up and wound down quickly.
- **Director change near a significant event** — note director changes with their dates, and log a lead to correlate the timing with any major transaction, filing, or regulatory action, checking the entity digest where such an event is already recorded.
- **Director whose address matches the company's registered address** — can indicate the director's address is fictitious.
- **Name variations** — most corporate registries do not verify the information they receive, so (intentionally or unintentionally) misspelled or variable names are common.

### Address patterns

- **Multiple companies at the same address** — record the shared address and note whether it is a registered-agent or law-firm address versus a residential, mailbox, or unmarked one. A law firm serving as registered agent is normal; flag a residential or unmarked shared address for a lead, especially where a single individual appears to act as a nominee across the entities.
- **Registered address in a jurisdiction with no apparent business connection** — e.g. a company claiming local registration but all directors and operations are elsewhere.
- **Registered address that appears implausible** — a malformed street number or an address that otherwise looks implausible on its face; log a lead to verify it exists.

### Share structure

- **Bearer shares** — shares not registered to a specific owner, making it impossible to trace beneficial ownership. Prohibited in some jurisdictions; a red flag where still permitted.
- **Voting rights disproportionate to ownership** — a person holding 1% of shares but 51% of votes. This is a control mechanism worth noting.
- **Shares issued to another company** — the parent company might itself be in a jurisdiction with minimal disclosure requirements.
- **Upstream and downstream businesses** — many businesses, especially large or international ones, have complex corporate structures that include holding companies, subsidiaries, etc. These relationships are worth noting.
- **Beneficial ownership** — corporate registries around the world have begun to require that beneficial owners be disclosed as an anti-money laundering measure. Take note of any beneficial owners, particularly if they do not overlap with directors or officers.

### Amalgamations and continuances

- **Amalgamation / merger** — two or more companies merging into one. The predecessor companies cease to exist. This can be used to obscure a company's history.
- **Continuance / redomiciliation** — a company moving its legal domicile to a different jurisdiction. Watch for continuances into less transparent jurisdictions.
- **Name change** — a company changing its name. Prior names are aliases and should be recorded. A series of name changes may indicate an attempt to distance from a reputation.

### Other patterns

- **Missing or backdated filings** — businesses will sometimes backdate an address change, director addition or removal, etc. This is always notable when it happens, particularly if the backdating stretches back months or years.

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

### United States

| Term | Meaning |
|------|---------|
| **Registered agent** | The person or company authorized to receive legal documents on the company's behalf |
| **Articles of organization** | The founding document for an LLC (equivalent to Articles of Incorporation for a corporation) |
| **Operating agreement** | LLC internal governance document — often not publicly filed |
| **EIN** | Employer Identification Number — US equivalent of a Business Number |
| **Statement of information** | Periodic filing required by some states (e.g. California) confirming officers, directors, and registered agent are current |
| **FinCEN BOI** | Beneficial Ownership Information — Under the March 2025 Interim Final Rule, all U.S. domestic reporting companies and U.S. persons are fully exempt. The rule applies strictly to foreign-formed entities registered to do business in the U.S., and even those entities do not need to report any beneficial owners who are U.S. citizens or residents

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

1. **Person → Company**: Director, Officer (with title), Shareholder (with share percentage if stated), Registered Agent (where an individual), Signing Officer
2. **Company → Company**: Registered agent (where a firm), Parent/subsidiary (if shares held by another company), Amalgamation predecessor/successor
3. **Company → Address**: Registered address, Principal place of business (if different), Previous registered address (if shown)
4. **Person → Address**: Director's stated address (extract even if it matches company address — that match is itself notable)

---

## What investigators typically miss

1. **The gap between the event date and the filing date** — a director resignation or address change dated months before it was actually filed with the registry. A large gap can mean the company continued operating with outdated public information, deliberately or not.
2. **The same registered agent across unrelated companies** — a commercial registered-agent or law-firm address shared across hundreds of unrelated companies is normal; the same individual acting as registered agent for a handful of otherwise-unconnected companies is more likely to be worth a second look.
3. **Dissolution timing relative to litigation or a regulatory action** — a company dissolved shortly after being named in a lawsuit, labour complaint, or environmental order can be an attempt to make a judgment uncollectible.
4. **Prior names and prior addresses** — registries usually retain a history of past filings; a company's current name is not always the name under which it did the thing you are investigating.
5. **The full filing history, not just the current-status snapshot** — many registries let you pull every document ever filed, not just the current printout. That history often has annual returns and director changes the summary page doesn't show.
6. **Officers who are not listed as directors** — a company's real decision-maker is sometimes an officer (president, CFO) rather than a director; searching for directors alone can miss this person.
7. **Extra-provincial or foreign qualification filings in a second jurisdiction** — a company doing sustained business outside its home jurisdiction is usually required to register there too; that second filing can reveal directors, addresses, or a registered agent not shown in the home-jurisdiction record.
8. **The registry's own currency date** — most registries stamp results with a "current as of" date; treat that date as the freshness bound on everything in the printout, not the date of the underlying filing.

---

## Sources and further reading

### Official and regulatory
- [FATF — The Misuse of Corporate Vehicles Including Trusts and Company Service Providers (2006)](https://www.fatf-gafi.org/en/publications/Methodsandtrends/Themisuseofcorporatevehiclesincludingtrustandcompanyserviceproviders.html) — canonical typologies report on nominee directors, shell companies, and bearer shares
- [FATF — Guidance on Beneficial Ownership of Legal Persons (2023)](https://www.fatf-gafi.org/content/dam/fatf-gafi/guidance/Guidance-Beneficial-Ownership-Legal-Persons.pdf) — current FATF standards for transparency of legal persons
- [FATF — Concealment of Beneficial Ownership (2018)](https://www.fatf-gafi.org/en/publications/methodsandtrends/documents/concealment-beneficial-ownership.html) — Egmont Group joint report on techniques used to hide ownership
- [FinCEN — Beneficial Ownership Information Reporting](https://www.fincen.gov/boi) — US BOI reporting rules; as of March 2025, the requirement applies only to foreign entities registered in the US, not domestic companies
- [Corporations Canada — Federal Corporate Registry](https://ised-isde.canada.ca/site/corporations-canada/en/corporations-canada) — search tool for federally incorporated Canadian entities (CBCA)
- [NASS — Business Registration](https://www.nass.org/business-services/state-business-filing-links) — the National Association of Secretaries of State's directory of links to every US state's business entity search and registered-agent filing portal

### Practitioner and public interest
- [Open Ownership — Principles for Effective Beneficial Ownership Disclosure](https://www.openownership.org/en/principles/) — nine-principle framework for evaluating the quality of a jurisdiction's beneficial ownership regime
- [Global Witness — Anonymous Company Owners](https://www.globalwitness.org/en/campaigns/corruption-and-money-laundering/anonymous-company-owners/) — investigations and reports on shell company abuse across multiple jurisdictions
- [OpenCorporates](https://opencorporates.com/) — the largest open database of company registration data, aggregating from official registries across 140+ jurisdictions

### Notes on unsourced claims
The claims that amalgamations and series of name changes are used to obscure corporate history are well-established practitioner knowledge but are not cited in a single canonical public document. Treat them as editorial observations pending a specific citation.
