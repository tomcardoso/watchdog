# Domain knowledge — Election filings

This skill is loaded by `/ingest` when the document type is a campaign finance disclosure, donor list, third-party advertising return, or similar electoral record.

Apply this knowledge in addition to the standard extraction process. It tells you what to look for, what terminology means, and what patterns are worth flagging.

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

## Always-present fields to extract

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
| **Largest contributors** | Names, amounts, and dates of top donors |
| **Third-party sponsor** | Name and address of any third party buying political advertising |

---

## Red flags — what to look for

### Contribution patterns

- **Contributions just below reporting thresholds** — amounts just below the disclosure threshold from multiple donors may indicate deliberate structuring to avoid disclosure. Threshold levels vary by jurisdiction.
- **Multiple contributions from the same household** — spouses, adult children, and other household members contributing the maximum allowable, especially in the same period, may indicate co-ordinated giving.
- **Contributions from prohibited sources** — most jurisdictions ban corporate donations to candidates or parties; some ban union donations; some ban foreign donations. Any prohibited-category donor in a contribution list is a red flag.
- **Out-of-district donors making up a large share of a candidate's fundraising** — worth noting, especially if those donors cluster around a specific industry or employer.
- **Late-reported contributions** — amendments filed after the original return may indicate contributions that were initially concealed or misattributed.
- **Loans from individuals or companies** — loans to campaigns may be repaid from future fundraising, effectively laundering a contribution. Look for loan terms, lender identity, and repayment status.

### Third-party advertising

- **Third party registered shortly before a blackout or restricted period** — groups registering just before the restricted period may be trying to front-load spending.
- **Third party with no prior public profile** — a newly formed organization with no history spending large amounts on advertising.
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
| **Contribution limit** | In Canada: $1,725/year per person per registered party (indexed annually); varies by province |
| **Reimbursement** | Parties and candidates meeting vote thresholds receive public reimbursement of a portion of expenses |
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

1. **Person → Campaign**: Donor (with amount and date), Official Agent, Chief Agent
2. **Person → Party/EDA**: Donor, Officer, Director
3. **Company → Campaign**: Vendor (with payment amount and purpose) — flag if corporate, as corporate donations are restricted in many jurisdictions
4. **Third party → Election**: Registered third party (with registration date, sponsor, and total spend)
5. **Campaign → Vendor**: Payment (with amount, date, and stated purpose)
6. **Campaign → Campaign**: Transfer (inter-candidate or candidate-to-party)

---

## What investigators typically miss

1. **Amendments and corrections** — the original return is often superseded by amendments filed months later. Always check for the most recent version; the differences between versions can be the story.
2. **The auditor's report** — larger campaigns and parties are required to have their returns audited. A qualified opinion or a note about incomplete records is significant.
3. **Unpaid claims** — expenses incurred but not yet paid at the time of filing; sometimes a way to defer costs to the next reporting period.
4. **The chief/official agent's own address** — sometimes the agent's address matches a vendor receiving large payments.
5. **Non-monetary contributions from prohibited donors** — a company providing free office space, printing, or IT services to a campaign may be making an illegal in-kind contribution in jurisdictions where corporate donations are banned. Look for in-kind contributions and check whether the donor is a prohibited category.
6. **Candidate's personal loans to their own campaign** — allowed in many jurisdictions but subject to limits and repayment rules; large self-loans that are never repaid are a financing mechanism worth examining.
