---
description: a charity information return, nonprofit tax filing, trust return, or similar tax filing by a nonprofit, charity, or trust; for the nonprofit's own financial statements (balance sheet, income statement), use `financial-statements`
---
# Domain knowledge — Tax documents

This skill is loaded by Watchdog when the document type is a charity information return, nonprofit tax filing, trust return, or similar tax filing by a nonprofit, charity, or trust.

---

## Document types covered

- Registered charity annual information returns
- Non-profit organization information returns
- Trust income tax returns (estates and trusts)
- Private foundation returns
- Tax-exempt organization annual returns
- State or provincial charity registration filings
- Revenue authority charity compliance audit records
- In Canada: T3010 (Registered Charity Information Return); T3 (Trust Income Tax Return); T1044 (Non-Profit Organization Information Return — *Note: unlike the T3010, the T1044 is not publicly available and only surfaces via leaks or discovery*); CRA charity compliance audit records
- In the US: Form 990 (Return of Organization Exempt from Income Tax); Form 990-PF (Private Foundation Return); Form 990-EZ; Form 990-N (e-Postcard — *strictly for gross receipts <$50k, contains almost no financial data*); state charity registration filings

---

## Fields to extract

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
- **Revenue declining while executive compensation increases** — where the return shows prior-year comparative figures, a decline in revenue alongside rising executive compensation is a sign that leadership is protecting its own pay while the charity shrinks. Where only a single year is present, log a lead to compare against prior returns.
- **Large cash or near-cash holdings** — charities are required to disburse a minimum amount annually (disbursement quota). Large accumulated reserves may indicate failure to meet the quota.

### Related-party and insider transactions

- **Non-arm's-length transactions** — charity information returns require disclosure of transactions between the charity and its directors, officers, or their relatives (rental of property, loans, contracts for services).
- **Director loans** — a charity lending money to a director or a director lending to the charity at a favourable rate.
- **Shared staff or facilities** — a charity sharing staff or premises with a for-profit company controlled by the same people.
- **Director also receiving compensation as an employee** — a director who is also a paid staff member occupies both a governance and an operational role. This is not automatically improper but the dual role and total compensation should be noted.
- **Gifts to organizations connected to directors** — a charity grants money to another charity or organization where the director of the first charity also sits on the board.

### Political activity

- **Political activity reporting** — rules vary heavily by jurisdiction. In the US, 501(c)(3)s are strictly prohibited from partisan activity and heavily limited on non-partisan lobbying. However, in Canada, charities may now devote up to 100% of resources to non-partisan "Public policy dialogue and development activities" (PPDDAs), so long as they further the charity's purpose. *Partisan* activity (supporting/opposing a candidate or party) remains universally prohibited for registered charities.
- **Return reporting little or no political activity** — a charity's annual return reporting zero or near-zero political activity is only half the picture; the flag is a mismatch with what the organisation actually does. Record the reported figure as stated. If the digest or the document set shows the charity running advocacy campaigns, that mismatch is a lead — don't assert concealment from the return alone.
- **501(c)(3) vs. 501(c)(4) status** — in the US, 501(c)(3) organizations are prohibited from partisan political activity; 501(c)(4) social welfare organizations may engage in more political activity. Organizations that straddle this line are worth examining.

### US-specific patterns (990)

- **Schedule L transactions** — record all business transactions with interested persons disclosed in Schedule L (loans, grants, and service contracts involving directors, officers, or key employees).
- **Part VII compensation** — the 990 requires disclosure of compensation for the highest-paid employees and all current officers.
---

## Jurisdiction terminology

### Canada

| Term | Meaning |
|------|---------|
| **T3010** | The annual information return all Canadian registered charities must file with CRA, which is public |
| **Charitable registration number** | A 15-character Business Number ending in RR followed by four digits |
| **Disbursement quota (DQ)** | The minimum amount a registered charity must spend annually. As of 2023, the rate is 3.5% on investment property up to $1 million, and 5% on property exceeding $1 million |
| **Qualified donee** | An organization that can receive gifts from a registered charity and issue tax receipts |
| **Qualifying disbursements** | Under new rules, charities can fund non-qualified donees (like foreign NGOs) without strict "direction and control," provided they meet accountability requirements (risk assessments, written agreements, monitoring) |
| **Revocation** | CRA can revoke charitable status for failure to file, failure to meet the DQ, operating outside charitable purposes, or partisan political activity violations |
| **T1044** | A simplified annual return for non-profit organizations that are not registered charities (not publicly accessible) |

### United States

| Term | Meaning |
|------|---------|
| **EIN** | Employer Identification Number — the US tax ID for organizations |
| **501(c)(3)** | US tax-exempt charitable organization; donations are tax-deductible |
| **501(c)(4)** | US social welfare organization; may engage in more political activity than a 501(c)(3); donations not deductible |
| **990** | Annual return filed by most tax-exempt organizations |
| **990-PF** | Return for private foundations — more detailed disclosure requirements |
| **Schedule O** | Supplemental information section of the Form 990; often where the organization explains unusual items — read it fully |
| **Schedule B** | The Form 990 schedule naming significant donors; not included in publicly filed 990s but received by the IRS. Following *Americans for Prosperity Foundation v. Bonta* (2021), state regulators can no longer compel charities to submit unredacted Schedule B lists |
| **Private foundation vs. public charity** | A private foundation is typically funded by a single donor or family; public charities receive broad public support. Private foundations have stricter rules |
| **Excise tax (4941)** | US tax on self-dealing transactions between a private foundation and its disqualified persons |
| **Form 4720** | Filed when a foundation or its managers engage in prohibited acts — a red flag document |
| **ProPublica Nonprofit Explorer** | Public database of 990 filings: nonprofitexplorer.propublica.org |

---

## Relationships to extract from tax documents

1. **Person → Organization**: Director, Officer, Highest-paid employee (with compensation)
2. **Organization → Organization**: Grants given (qualified donees, qualifying disbursements), transactions with related organizations
3. **Person → Organization (non-arm's-length)**: Director or officer receiving contracts, loans, or rental income from the charity
4. **Organization → Government**: Government grants received (with department and amount)

---

## What investigators typically miss

1. **The disbursement quota calculation** — if a Canadian charity is sitting on large reserves while spending minimally on programs, calculate whether it is meeting its DQ (3.5% on the first $1M of non-charitable assets; 5% on amounts above $1M). Failure to meet the DQ can trigger regulator scrutiny.
2. **Donor-Advised Funds (DAFs) as black boxes** — DAFs (like Fidelity Charitable or CanadaHelps) are the "dark matter" of modern philanthropy. When examining grants *to* or *from* major DAF sponsors, recognize that these entities legally obscure the true original source or final destination of the funds.
3. **Year-over-year comparison** — a single year of a charity return is much less useful than a five-year series. Trends in revenue, spending, compensation, and program delivery tell a story that a single return hides.
4. **The charity's stated purposes vs. actual activities** — compare the description of activities to the charitable purposes registered with the revenue authority. Scope creep outside stated purposes is a compliance issue.
5. **Gifts to foreign organizations** — while Canada no longer requires strict "direction and control" for funding foreign entities, charities must still establish "qualifying disbursements" with rigorous accountability frameworks (written agreements, risk assessments, monitoring). Careless international transfers still warrant high scrutiny.
6. **The auditor's report (if appended)** — larger charities attach audited financial statements. A qualified opinion, going concern note, or management letter is significant.
7. **Changes in fiscal year** — a charity that changes its fiscal year end mid-stream is worth examining; it can compress or extend a reporting period to obscure a difficult year.

---

## Sources and further reading

### Official and regulatory
- [CRA: Filing a Registered Charity Information Return (T3010)](https://www.canada.ca/en/revenue-agency/services/charities-giving/charities/operating-a-registered-charity/filing-t3010-charity-return.html) — Canada Revenue Agency guidance on the T3010 annual return requirement, including what must be disclosed and what is made public
- [CRA: How to Get Information About a Charity](https://www.canada.ca/en/revenue-agency/services/charities-giving/charities/information-about-a-charity.html) — The CRA's public-facing charity search, which surfaces T3010 data including financial figures and registration status for the last five fiscal years
- [IRS: About Form 990, Return of Organization Exempt from Income Tax](https://www.irs.gov/forms-pubs/about-form-990) — IRS overview of the Form 990 series, filing requirements by organization size, and links to the full form and instructions
- [IRS: Tax Exempt Organization Search](https://www.irs.gov/charities-non-profits/tax-exempt-organization-search) — The IRS database for searching exempt organization filings, status, and determination letters

### Practitioner and public interest
- [FATF: Best Practices on Combating the Abuse of Non-Profit Organisations](https://www.fatf-gafi.org/en/publications/Financialinclusionandnpoissues/Bpp-combating-abuse-npo.html) — The Financial Action Task Force's best practices paper on terrorist financing risks in the nonprofit sector and the risk-based measures countries and NPOs should apply (updated under Recommendation 8, October 2023)
- [FATF: Non-Profit Organisations Topic Page](https://www.fatf-gafi.org/en/topics/non-profit-organisations.html) — FATF's central resource on NPO vulnerabilities, linking to guidance, mutual evaluation findings, and Recommendation 8 implementation materials

### Journalism resources
- [ProPublica Nonprofit Explorer](https://projects.propublica.org/nonprofits/) — Free searchable database of millions of Form 990 filings; full-text search across electronically filed 990s, 990-PFs, and 990-EZs from 2011 to present; supports people search across officer and employee disclosures
