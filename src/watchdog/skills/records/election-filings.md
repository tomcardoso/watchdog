---
description: a campaign finance disclosure, donor list, third-party advertising return, or similar electoral record
---
# Domain knowledge — Election filings

This skill is loaded by Watchdog when the document type is a campaign finance disclosure, donor list, third-party advertising return, or similar electoral record.

---

## Document types covered

- Campaign finance returns (national and sub-national elections authorities)
- Donor and contribution lists
- Third-party advertiser registration and expense returns
- Party and constituency association financial returns
- Leadership campaign returns
- Political party annual financial returns
- Inaugural committee filings
- In Canada: Elections Canada returns; provincial equivalent returns; electoral district association (EDA) returns
- In the US: FEC campaign finance disclosures; PAC and Super PAC filings; state-level campaign finance returns

---

## Fields to extract

| Field | What to look for |
|-------|-----------------|
| **Registrant name** | Legal name of the party, candidate, or third party |
| **Registration/filing number** | Assigned by the elections authority |
| **Reporting period** | The period covered by the return (election period, annual, quarterly) |
| **Election date** | The election this return relates to, if applicable |
| **Candidate or party name** | Who the return is filed for |
| **District / constituency** | Electoral district name and number |
| **Chief agent / treasurer** | The person legally responsible for the finances |
| **Total contributions received** | Gross amount raised |
| **Total expenses** | Gross amount spent |
| **Largest contributors** | Names, amounts, dates and any other information (e.g. addresses) of large donors |
| **Donor address** | Street address as filed — required in most disclosure regimes and useful for spotting clustering |
| **Donor employer / occupation** | Required on US FEC filings for individual contributions; in Canada, the postal code often substitutes as the closest available geographic/occupational proxy |
| **Contribution date** | The date of each individual contribution, not just the reporting period |
| **Aggregate contributions to date** | The running total from a single donor across the election cycle or reporting year — required once a donor crosses a threshold |
| **Third-party sponsor** | Name and address of any third party buying political advertising |

---

## Red flags — what to look for

### Contribution patterns

- **Contributions just below reporting thresholds** — amounts just below the disclosure threshold from multiple donors may indicate deliberate structuring to avoid disclosure. Threshold levels vary by jurisdiction and change over time, so apply this to individual contributions disclosed in the filing that sit at or just below the limit stated or known for that jurisdiction.
- **Multiple contributions from the same household** — spouses, adult children, and other household members contributing the maximum allowable, especially in the same period, may indicate co-ordinated giving.
- **Contributions from prohibited sources** — most jurisdictions ban corporate donations to candidates or parties; some ban union donations; some ban foreign donations. Any prohibited-category donor in a contribution list is a red flag.
- **Out-of-district donors making up a large share of a candidate's fundraising** — worth noting, especially if those donors cluster around a specific industry or employer.
- **Late-reported contributions** — amendments filed after the original return may indicate contributions that were initially concealed or misattributed.
- **Loans from individuals or companies** — loans to campaigns may be repaid from future fundraising, effectively laundering a contribution. Look for loan terms, lender identity, and repayment status.
- **Multiple donors listed at the same address** — several individually maxed-out contributions from one residential or business address may indicate a straw-donor scheme, where a company or individual reimburses others to contribute in their names. Not automatically improper — could be a household or shared office — but worth flagging as a pattern.
- **Employer clustering** — a run of donations at or near the maximum from employees of the same company, especially donors with similar or junior job titles, may suggest employer pressure or reimbursement rather than independent giving.
- **Coordinated round-number maximums near the legal limit** — several donors each giving the exact same round figure just under the contribution limit, especially within a short window, is a pattern consistent with coordinated giving and may be worth a closer look.

### Third-party advertising

- **Third party registered shortly before a blackout or restricted period** — groups registering just before the restricted period may be trying to front-load spending.
- **Third party sharing an address or director with a registered party or candidate** — co-ordination between third parties and candidates is typically illegal; this is a structural indicator.
- **Advertising expenses that don't match declared reach** — a very large declared spend with no publicly visible advertising.

### Expense patterns

- **Transfers between candidates and the party** — allowed within limits but worth tracking as a way of moving money around the system.
- **Large payments to a single vendor** — especially if that vendor is newly incorporated, shares an address with the campaign, or appears in multiple campaigns.
- **Polling or research expenses** — campaigns often use polling as a way to pay party insiders; the recipient of polling money is worth checking.
- **Non-monetary contributions (in-kind)** — goods or services provided at below-market or no cost. Often undervalued; compare to market rates.

---

## Jurisdiction terminology

### Canada

| Term | Meaning |
|------|---------|
| **Registered party** | A political party registered with Elections Canada or a provincial authority |
| **Electoral district association (EDA)** | The local fundraising and nomination arm of a party in a riding |
| **Chief agent** | The person legally responsible for a party's finances |
| **Official agent** | The person legally responsible for a candidate's election expenses |
| **Third party** | Any individual, group, or organization other than a candidate or party that spends money to influence an election |
| **Election expenses limit** | The maximum a candidate or party can spend during the election period |
| **Contribution limit** | The maximum an individual may give per year to a registered party — the federal figure is indexed annually and varies by province, so verify the current limit with Elections Canada or the provincial authority | **Reimbursement** | Parties and candidates meeting vote thresholds receive public reimbursement of a portion of expenses |
| **Blackout period** | Period near election day when third-party advertising is restricted |

### United States

| Term | Meaning |
|------|---------|
| **FEC** | Federal Election Commission — the US federal elections authority |
| **PAC** | Political Action Committee — a committee that raises money to elect or defeat candidates |
| **Super PAC** | An independent expenditure PAC that can raise unlimited funds but cannot co-ordinate with campaigns |
| **Dark money** | Spending by nonprofits (501(c)(4)s) that don't disclose donors |
| **In-kind contribution** | A non-cash contribution (goods, services, facilities) |
| **Bundler** | A person who collects contributions from others and delivers them to a campaign |
| **Electioneering communication** | A broadcast ad that mentions a candidate within 60 days of a general election (triggers FEC disclosure) |
| **Independent expenditure** | Spending to expressly advocate for or against a candidate, not co-ordinated with the campaign |

---

## Relationships to extract from election filings

1. **Person → Campaign**: Donor (with amount, date, aggregate-to-date, address, and employer/occupation where stated), Official Agent, Chief Agent
2. **Person → Candidate**: Donor (with the same contribution attributes) where the filing is candidate-specific rather than party-wide
3. **Person → Party/EDA**: Donor, Officer, Director
4. **Company → Campaign**: Vendor (with payment amount and purpose) — flag if corporate, as corporate donations are restricted in many jurisdictions
5. **Third party → Election**: Registered third party (with registration date, sponsor, and total spend)
6. **Campaign → Vendor**: Payment (with amount, date, and stated purpose)
7. **Campaign → Campaign**: Transfer (inter-candidate or candidate-to-party)

---

## What investigators typically miss

1. **Amendments and corrections** — the original return may be superseded by amendments filed months later. Always check for the most recent version; the differences between versions can be useful signals.
2. **The auditor's report** — larger campaigns and parties are required to have their returns audited. A qualified opinion or a note about incomplete records is significant.
3. **Unpaid claims** — expenses incurred but not yet paid at the time of filing; sometimes a way to defer costs to the next reporting period.
4. **The chief/official agent's own address** — sometimes the agent's address matches a vendor receiving large payments.
5. **Non-monetary contributions from prohibited donors** — a company providing free office space, printing, or IT services to a campaign may be making an illegal in-kind contribution in jurisdictions where corporate donations are banned. Look for in-kind contributions and check whether the donor is a prohibited category.
6. **Candidate's personal loans to their own campaign** — allowed in many jurisdictions but subject to limits and repayment rules; large self-loans that are never repaid are a financing mechanism worth examining.
7. **A third party with no prior public profile** — a newly formed organization spending large amounts on advertising is only suspicious once you know it has no history. That judgment requires checking the group against prior registrations or public records outside the filing itself.

---

## Sources and further reading

### Official and regulatory
- [Political Financing — Elections Canada](https://www.elections.ca/content.aspx?section=fin&lang=e&document=index) — Elections Canada's central hub for Canada's federal political financing rules, contribution limits, spending restrictions, and searchable databases of returns going back to 1993
- [Annual Limits on Contributions — Elections Canada](https://www.elections.ca/content.aspx?section=pol&dir=lim&document=lim2025&lang=e) — Current indexed contribution limits for registered parties and candidates under the Canada Elections Act
- [Campaign Finance Data — FEC](https://www.fec.gov/data/) — Federal Election Commission portal for US federal campaign finance disclosures; searchable by candidate, committee, donor, and expenditure
- [Introduction to Campaign Finance and Elections — FEC](https://www.fec.gov/introduction-campaign-finance/) — Plain-language overview of US campaign finance law, who can give and how, and how to research public data
- [Contributions in the Name of Another — FEC](https://www.fec.gov/help-candidates-and-committees/candidate-taking-receipts/contributions-name-another/) — Official FEC explainer on the straw-donor / conduit-contribution prohibition, the legal basis for scrutinizing same-address and employer-clustered donations

### Practitioner and public interest
- [OpenSecrets](https://www.opensecrets.org/) — Nonpartisan research group aggregating FEC data; tracks donor industries, dark money, PAC spending, and bundlers for every federal race
- [FollowTheMoney.org](https://www.followthemoney.org/) — National Institute on Money in Politics; the primary resource for US state-level campaign contributions and independent spending (federal data now hosted at OpenSecrets)
- [Political Finance Database — International IDEA](https://www.idea.int/data-tools/data/political-finance-database) — Comparative database of political finance regulations across 181 countries covering bans on private income, public funding, spending rules, and disclosure requirements; useful for cross-jurisdictional context

### Journalism resources
- [IRE Resource Center — Campaign Finance Tipsheets](https://www.ire.org/resource-center/tipsheets/?q=campaign+finance) — Tipsheets from Investigative Reporters and Editors on using FEC data, tracking dark money, and working with state campaign finance records

**Notes on unsourced claims:** The red flag on contribution structuring just below disclosure thresholds is a well-documented practice in enforcement literature but the specific thresholds vary by jurisdiction and change frequently; always verify current limits directly with the relevant elections authority. The employer-clustering and coordinated round-number-maximum patterns are common investigative heuristics, not themselves proof of a violation in isolation; treat them as editorial pattern-recognition pending corroboration.
