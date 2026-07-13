---
description: a balance sheet, income statement, auditor's report, management discussion and analysis (MD&A), or an annual, interim, or quarterly report built around financial statements — from a private or public company (10-K, 10-Q), charity, hospital, university, or other organization; for event-driven securities filings (insider reports, material change reports, proxies, prospectuses, AIFs) use `regulatory-filings`, for corporate registry records use `corporate-filings`
---
# Domain knowledge — Financial statements

Loaded by Watchdog when the document type is a balance sheet, income statement, auditor's report, management discussion and analysis (MD&A), or an annual, interim, or quarterly report built around financial statements — whatever the organization: private or public company, charity, hospital, or university.

For corporate registry records (registrations, director filings, shareholder registers), see `corporate-filings`; for event-driven securities filings around the statements (material change reports, insider trading reports, proxies, prospectuses, annual information forms), see `regulatory-filings`.

---

## Document types covered

- Annual financial statements (audited)
- Annual reports (narrative plus audited statements) — private and public companies, charities, hospitals, universities
- Interim / quarterly financial statements (reviewed or unaudited)
- Auditor's reports and management letters
- Management discussion and analysis (MD&A)
- 10-K and 10-Q filings — US public companies
- Financial statements filed in bankruptcy proceedings
- Non-profit / charity financial statements

---

## Fields to extract

| Field | What to look for |
|-------|-----------------|
| **Reporting entity** | The company or organization the statements relate to |
| **Fiscal year end** | The period the statements cover |
| **Auditor** | Name of the auditing firm and engagement partner |
| **Audit opinion type** | Unqualified (clean), qualified, adverse, or disclaimer of opinion |
| **Total revenue** | For income statement |
| **EBITDA / Adjusted EBITDA** | Earnings Before Interest, Taxes, Depreciation, and Amortization (and any non-GAAP management adjustments) |
| **Net income / (loss)** | Profit or loss for the period |
| **Total assets** | From balance sheet |
| **Total liabilities** | From balance sheet |
| **Total equity** | Assets minus liabilities |
| **Cash and equivalents** | Liquidity indicator |
| **Deferred revenue** | Unearned revenue representing payments received before goods or services are delivered |
| **Long-term debt** | Significant borrowings |
| **Executive indebtedness** | Outstanding loans or credit lines extended to directors or officers |
| **Accounting standard** | IFRS, ASPE, US GAAP, or other |

---

## Red flags

### Audit opinion red flags

- **Going concern note** — auditor is uncertain whether the entity can continue as a going concern for the next 12 months. Look for the phrase "material uncertainty related to going concern" or "substantial doubt about the entity's ability to continue." This is often buried in Note 1 or the auditor's report.
- **Qualified opinion** — auditor disagrees with a specific accounting treatment. The qualification describes what and why. Always extract the full text.
- **Adverse opinion** — financial statements are materially misstated. Rare and significant.
- **Disclaimer of opinion** — auditor could not obtain sufficient evidence. May indicate the company withheld information.
- **Emphasis of matter paragraph** — the auditor draws attention to something without qualifying. Read every emphasis paragraph fully.
- **Change in auditor** — note the name of the previous auditor and when they were replaced. An auditor change concurrent with a financial restatement is highly significant.

### Income statement red flags

- **Accounting-to-Tax Income Disconnect** — a large or widening gap between reported pre-tax accounting income and the income implied by current tax expense can signal aggressive book-tax differences worth a closer forensic look. The two figures do not reconcile exactly (timing differences, credits, and other adjustments break the equivalence), so treat a persistent gap as a lead to log, not a precise measure.
- **EBITDA vs. CFO Divergence** — a widening gap between EBITDA and cash flow from operations (CFO) can arise from capitalizing operating-type costs, aggressive revenue recognition, or working-capital drains. Note the gap and whatever drivers the statements disclose.
- **Revenue declining while administrative expenses increase** — the business is shrinking but overhead isn't.
- **Revenue concentrated in one or few customers** — disclosed in notes as a customer concentration risk. Loss of one customer could be devastating.
- **Revenue recognition policy** — how and when does the company recognize revenue? Aggressive recognition (recognizing revenue before it's earned) is a common fraud mechanism.
- **Non-recurring items appearing every year** — "one-time" charges that appear repeatedly are not one-time.
- **Goodwill impairment** — the company wrote down the value of an acquisition. Often signals the acquisition failed.
- **COGS vs. Adjusted Metric Shifting** — compare GAAP cost of goods sold against any non-GAAP adjusted figures shown in the document; note items excluded from the adjusted measure (e.g., depletion, content rights, or other core inventory costs) and their magnitude, which can make the business look artificially cheap.

### Balance sheet red flags

- **Receivables growing faster than revenue** — may indicate the company is recognizing revenue before customers pay, or that customers aren't paying.
- **Inventory growing faster than cost of goods sold** — possible overvaluation of inventory, or a business that can't sell what it makes.
- **Deferred Revenue Drawdown** — a steadily decreasing Deferred-to-Revenue ratio, indicating the company is surviving on a declining backlog rather than fresh sales.
- **Aggressive Asset Mix ("Soft Assets")** — capital-light or questionable assets (prepaids, deferred preproduction costs, long-term receivables, deferred marketing costs) growing to comprise a high percentage (e.g., ~40%) of total assets.
- **Related party receivables** — amounts owed by related parties (directors, officers, affiliated companies). These may never be collected.
- **Negative equity** — liabilities exceed assets. The company is technically insolvent.
- **Unusual intangible assets** — large values assigned to internally generated intangibles may be inflated.

### Cash flow red flags

- **Profitable but cash-flow negative** — a company reporting profit but burning cash. Accrual accounting allows "profit" without cash.
- **Large differences between net income and operating cash flow** — the gap shows how much of "profit" is not backed by cash.
- **Financing cash flows masking operating weakness** — a company that only generates cash by borrowing or selling shares is not self-sustaining.

### Annual report red flags

When the document is a full annual report rather than standalone statements, the narrative wrapper deserves the same scrutiny as the numbers:

**Narrative-versus-numbers divergence** — an upbeat chairman's or CEO's letter paired with deteriorating statements may signal spin worth probing; note what the letter chooses not to mention (a discontinued segment, an impairment, a departed executive the numbers reveal).

**Auditor changes** — a change of auditor, especially mid-year, after a qualified opinion, or from a large firm to a much smaller one, can precede restatements or disputes; the reason given (or not given) for the change is worth recording.

**Risk-factor drift** — when the document itself reproduces prior-year risk disclosures, compare them year over year: risks newly added or quietly dropped can mark what management has started or stopped worrying about, and a new, oddly specific risk factor is sometimes the first public trace of an unannounced problem. If only the current year's risks are present, note any oddly specific new risk and log a lead to compare against the prior year's report.

**Non-GAAP emphasis** — heavy reliance on adjusted or non-GAAP measures whose gap to the audited figures widens year over year, or a changed definition of an "adjusted" metric between years, may be an important signal of financial distress of obfuscation.

**Incentives Tied to Non-GAAP Targets** — executive bonuses, options, or performance shares explicitly benchmarked against non-GAAP metrics (like Adjusted EBITDA) instead of standard GAAP Net Income.

**Insider & Creative Concentration** — major shareholders concurrently serving as senior executives and creative directors, leading to opaque corporate structures, inactive subsidiaries, or unvouched relationships with limited partnerships.

**Certified statements** — for public companies, the CEO and CFO personally certify the statements (SOX 302/906 in the US and equivalents elsewhere). Note who certified; a later restatement makes those certifications significant.

### Related party transactions

IFRS, US GAAP, ASPE, and virtually all other accounting standards require disclosure of transactions with related parties (directors, officers, controlling shareholders, affiliated entities). These disclosures are in the notes, often late in the document, and are frequently the most newsworthy content:

- Consulting fees paid to a company controlled by a director
- Loans to or from officers
- Rent paid to a landlord who is also a shareholder
- Purchases from or sales to affiliated companies at non-market prices

Extract every related party transaction. Note the party, the nature of the transaction, and the dollar amount.

---

## Terminology

| Term | Meaning |
|------|---------|
| **IFRS** | International Financial Reporting Standards — used by public companies in Canada, the EU, UK, Australia, and over 140 other jurisdictions |
| **ASPE** | Accounting Standards for Private Enterprises — used by most Canadian private companies |
| **US GAAP** | United States Generally Accepted Accounting Principles — used by US public companies and many US private companies |
| **Consolidated statements** | Financials that include subsidiaries as if the group were one entity |
| **Equity method** | Accounting for investments where the investor has significant influence but not control |
| **Fair value** | The price that would be received in an orderly market transaction |
| **Impairment** | A write-down of an asset's carrying value to reflect a decline in value |
| **Deferred revenue** | Cash received but not yet earned — a liability |
| **Deferred tax** | A timing difference between accounting income and taxable income |
| **Contingent liability** | A potential obligation dependent on a future event (e.g. ongoing litigation) |
| **Subsequent event** | An event after the balance sheet date that may affect the financial statements |
| **Material** | Significant enough to affect the decision-making of a reasonable investor |
| **Restatement** | A correction to previously issued financial statements |

---

## Relationships to extract

1. **Company → Person**: Auditor engagement partner, CFO (who signs off), board audit committee members
2. **Company → Company**: Auditing firm, subsidiaries and affiliates (from consolidation scope), related parties
3. **Company → Intermediary / VIE**: Map connections between the primary company and third-parties, specialized distribution channels, limited partnerships, or off-balance-sheet entities (Variable Interest Entities) mentioned in the footnotes
4. **Person / Company → Transaction**: All related party transactions with amounts
5. **Company → Company (related party)**: Every related party entity named in the notes

---

## What investigators typically miss

1. **Note 1** — accounting policies. This is where the company describes how it counts revenue, values inventory, and measures everything else. Aggressive policies buried here can explain otherwise inexplicable results.
2. **The segment information note** — if the company has multiple business lines or geographies, segment disclosures show which parts are profitable and which are not. Losing segments are sometimes hidden in aggregation.
3. **Commitments and contingencies note** — future obligations and potential liabilities. Lease commitments, purchase obligations, and pending litigation. The total of all future commitments can dwarf what appears on the balance sheet.
4. **Share-based compensation note** — how much are executives being paid in stock options and restricted shares? This doesn't always appear prominently in the income statement.
5. **The five-year summary** — many annual reports include a multi-year financial summary. This makes trend analysis easy and is often overlooked.
6. **Management's report on internal controls** — if the company discloses material weaknesses in internal controls, it means they don't have adequate processes to catch errors or fraud.
7. **Changes in accounting policies** — when a company changes how it accounts for something, the effect is disclosed. A change that flatters results should raise questions.

---

## Sources and further reading

### Official and regulatory
- [IFRS Accounting Standards Navigator — IFRS Foundation](https://www.ifrs.org/issued-standards/list-of-standards/) — Searchable index of all current IFRS Accounting Standards; required disclosure frameworks for public companies in Canada, the EU, UK, Australia, and over 140 other jurisdictions
- [IAS 1 Presentation of Financial Statements — IFRS Foundation](https://www.ifrs.org/issued-standards/list-of-standards/ias-1-presentation-of-financial-statements/) — The standard that sets overall requirements for financial statement presentation, including going concern disclosure; going concern requirements were moved to IAS 8 under IFRS 18 (effective 2027)
- [IAS 8 Accounting Policies, Changes in Accounting Estimates and Errors — IFRS Foundation](https://www.ifrs.org/issued-standards/list-of-standards/ias-8-accounting-policies-changes-in-accounting-estimates-and-errors/) — Governs how accounting policy changes must be disclosed and applied; a policy change that flatters results must be explained here
- [PCAOB Auditing Standards](https://pcaobus.org/oversight/standards/auditing-standards) — The Public Company Accounting Oversight Board's full catalogue of auditing standards for US public company audits, including AS 18 on related parties
- [SEC EDGAR Full Text Search](https://www.sec.gov/edgar/search/) — Free full-text search of all electronic SEC filings since 2001, including 10-K and 10-Q filings; search for company names, financial terms, or disclosure language

### Practitioner and public interest
- [CPA Canada: Understanding Reports on Financial Statements](https://www.cpacanada.ca/business-and-accounting-resources/audit-and-assurance/canadian-auditing-standards-cas/publications/understanding-reports-on-financial-statements) — Plain-language guide explaining the differences between audit, review, and compilation engagements and what each auditor's report actually means
- [IFRS Foundation: Going Concern — A Focus on Disclosure (updated May 2025)](https://www.ifrs.org/content/dam/ifrs/supporting-implementation/educational-materials/going-concern-2025.pdf) — Educational material on going concern assessment and disclosure requirements under IFRS, updated to reflect IFRS 18

### Journalism resources
- [The Investigative Journalist's Guide to Company Accounts — Centre for Investigative Journalism](https://tcij.org/handbooks/the-investigative-journalists-guide-to-company-accounts/) — Forensic accountant Raj Bairoliya's handbook covering balance sheets, income statements, cash flow, auditor's reports, and red flags for journalists who do not work with accounts daily
