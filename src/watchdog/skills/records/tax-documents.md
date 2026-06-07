# Domain knowledge — Tax documents

This skill is loaded by `/ingest` when the document type is a charity information return, nonprofit tax filing, trust return, or similar tax filing by a nonprofit, charity, or trust.

Apply this knowledge in addition to the standard extraction process. It tells you what to look for, what terminology means, and what patterns are worth flagging.

---

## Document types covered

- Registered charity annual information returns
- Non-profit organization information returns
- Trust income tax returns (estates and trusts)
- Private foundation returns
- Tax-exempt organization annual returns
- State or provincial charity registration filings
- Revenue authority charity compliance audit records
- In Canada: T3010 (Registered Charity Information Return); T3 (Trust Income Tax Return); T1044 (Non-Profit Organization Information Return); CRA charity compliance audit records
- In the US: Form 990 (Return of Organization Exempt from Income Tax); Form 990-PF (Private Foundation Return); Form 990-EZ; Form 990-N (e-Postcard); state charity registration filings

---

## Always-present fields to extract

| Field | What to look for |
|-------|-----------------|
| **Organization name** | Legal registered name |
| **Registration number** | Revenue authority charity number or US EIN |
| **Fiscal year end** | Date the return covers through |
| **Charitable purposes** | The stated objects of the charity |
| **Revenue — total** | All income for the year |
| **Revenue — gifts / donations** | Amount received from the public |
| **Revenue — government grants** | Grants from federal, provincial/state, or municipal governments |
| **Revenue — other** | Investment income, sales, program fees |
| **Expenditures — total** | All spending for the year |
| **Expenditures — charitable programs** | Spending on the charity's stated purposes |
| **Expenditures — management/admin** | Overhead |
| **Expenditures — fundraising** | Cost of raising funds |
| **Compensation — highest paid employees** | Names and total compensation of the top-paid employees |
| **Directors and officers** | Names, roles, and whether they are arm's length from the charity |
| **Gifts to qualified donees** | Transfers to other charities or government bodies |
| **Accumulated property** | Assets held by the charity |

---

## Red flags — what to look for

### Revenue and expenditure patterns

- **High fundraising ratio** — if fundraising costs exceed 35% of fundraising revenue, that is worth flagging. Some charities spend more on raising money than on their programs.
- **Low program spending ratio** — a charity spending less than 60% of expenditures on charitable programs (as opposed to admin and fundraising) is below what most regulators and watchdogs consider healthy.
- **Revenue concentrated in a single source** — a charity that receives 80%+ of its income from a single donor or government grant is highly dependent and potentially controlled by that source.
- **Revenue declining year-over-year while executive compensation increases** — a sign that leadership is protecting its own pay while the charity shrinks.
- **Large cash or near-cash holdings** — charities are required to disburse a minimum amount annually (disbursement quota). Large accumulated reserves may indicate failure to meet the quota.

### Related-party and insider transactions

- **Non-arm's-length transactions** — charity information returns require disclosure of transactions between the charity and its directors, officers, or their relatives (rental of property, loans, contracts for services).
- **Director loans** — a charity lending money to a director or a director lending to the charity at a favourable rate.
- **Shared staff or facilities** — a charity sharing staff or premises with a for-profit company controlled by the same people.
- **Director also receiving compensation as an employee** — a director who is also a paid staff member occupies both a governance and an operational role. This is not automatically improper but the dual role and total compensation should be noted.
- **Gifts to organizations connected to directors** — a charity grants money to another charity or organization where the director of the first charity also sits on the board.

### Political activity

- **Political activity reporting** — most jurisdictions limit or prohibit partisan political activity by registered charities; non-partisan advocacy is typically capped. A charity that reports significant political expenditure or whose programs appear primarily advocacy-driven is at risk of losing charitable status.
- **Undisclosed political activity** — a charity that publicly engages in political advocacy but reports zero or near-zero political activity on its annual return.
- **501(c)(3) vs. 501(c)(4) status** — in the US, 501(c)(3) organizations are prohibited from partisan political activity; 501(c)(4) social welfare organizations may engage in more political activity. Organizations that straddle this line are worth examining.

### US-specific patterns (990)

- **Schedule L transactions** — business transactions with interested persons (loans, grants, business relationships with directors, officers, or key employees).
- **Schedule O** — supplemental information; often where the organization explains unusual items. Read it fully.
- **Part VII compensation** — the 990 requires disclosure of compensation for the highest-paid employees and all current officers.
- **Schedule B donor disclosure** — 990s filed publicly do not include Schedule B (which names significant donors), but the IRS receives it. Some state regulators require it.

---

## Jurisdiction terminology

### Canada

| Term | Meaning |
|------|---------|
| **T3010** | The annual information return all Canadian registered charities must file with CRA |
| **Charitable registration number** | A 15-character Business Number ending in RR followed by four digits |
| **Disbursement quota (DQ)** | The minimum amount a registered charity must spend on charitable activities or gifts to qualified donees each year |
| **Qualified donee** | An organization that can receive gifts from a registered charity and issue tax receipts |
| **Direction and control** | A registered charity giving funds to a non-qualified donee must maintain direction and control over how those funds are used |
| **Revocation** | CRA can revoke charitable status for failure to file, failure to meet the DQ, operating outside charitable purposes, or political activity violations |
| **T1044** | A simplified annual return for non-profit organizations that are not registered charities |

### United States

| Term | Meaning |
|------|---------|
| **EIN** | Employer Identification Number — the US tax ID for organizations |
| **501(c)(3)** | US tax-exempt charitable organization; donations are tax-deductible |
| **501(c)(4)** | US social welfare organization; may engage in more political activity than a 501(c)(3); donations not deductible |
| **990** | Annual return filed by most tax-exempt organizations |
| **990-PF** | Return for private foundations — more detailed disclosure requirements |
| **Private foundation vs. public charity** | A private foundation is typically funded by a single donor or family; public charities receive broad public support. Private foundations have stricter rules |
| **Excise tax (4941)** | US tax on self-dealing transactions between a private foundation and its disqualified persons |
| **Form 4720** | Filed when a foundation or its managers engage in prohibited acts — a red flag document |
| **ProPublica Nonprofit Explorer** | Public database of 990 filings: nonprofitexplorer.propublica.org |

---

## Relationships to extract from tax documents

1. **Person → Organization**: Director, Officer, Highest-paid employee (with compensation)
2. **Organization → Organization**: Grants given (qualified donees), transactions with related organizations
3. **Person → Organization (non-arm's-length)**: Director or officer receiving contracts, loans, or rental income from the charity
4. **Organization → Government**: Government grants received (with department and amount)

---

## What investigators typically miss

1. **The disbursement quota calculation** — if the charity is sitting on large reserves while spending minimally on programs, calculate whether it is meeting its DQ. A failure to meet the DQ can trigger regulator scrutiny.
2. **Year-over-year comparison** — a single year of a charity return is much less useful than a five-year series. Trends in revenue, spending, compensation, and program delivery tell a story that a single return hides.
3. **The charity's stated purposes vs. actual activities** — compare the description of activities to the charitable purposes registered with the revenue authority. Scope creep outside stated purposes is a compliance issue.
4. **Gifts to foreign organizations** — a charity cannot simply write a cheque to a foreign organization in most jurisdictions; it must maintain direction and control over how those funds are used. Large gifts to foreign entities warrant scrutiny.
5. **The auditor's report (if appended)** — larger charities attach audited financial statements. A qualified opinion, going concern note, or management letter is significant.
6. **Changes in fiscal year** — a charity that changes its fiscal year end mid-stream is worth examining; it can compress or extend a reporting period to obscure a difficult year.
