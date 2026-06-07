# Domain knowledge — Financial statements

Loaded by `/ingest` when the document type is a balance sheet, income statement, auditor's report, management discussion and analysis (MD&A), or similar financial disclosure.

---

## Document types covered

- Annual financial statements (audited)
- Interim / quarterly financial statements (reviewed or unaudited)
- Auditor's reports and management letters
- Management discussion and analysis (MD&A)
- Annual information forms (AIF) — Canadian public companies
- 10-K and 10-Q filings — US public companies
- Prospectuses and offering memoranda
- Financial statements filed in bankruptcy proceedings
- Non-profit / charity financial statements

---

## Always-present fields to extract

| Field | What to look for |
|-------|-----------------|
| **Reporting entity** | The company or organization the statements relate to |
| **Fiscal year end** | The period the statements cover |
| **Auditor** | Name of the auditing firm and engagement partner |
| **Audit opinion type** | Unqualified (clean), qualified, adverse, or disclaimer of opinion |
| **Total revenue** | For income statement |
| **Net income / (loss)** | Profit or loss for the period |
| **Total assets** | From balance sheet |
| **Total liabilities** | From balance sheet |
| **Total equity** | Assets minus liabilities |
| **Cash and equivalents** | Liquidity indicator |
| **Long-term debt** | Significant borrowings |
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

- **Revenue declining while administrative expenses increase** — the business is shrinking but overhead isn't.
- **Revenue concentrated in one or few customers** — disclosed in notes as a customer concentration risk. Loss of one customer could be devastating.
- **Revenue recognition policy** — how and when does the company recognize revenue? Aggressive recognition (recognizing revenue before it's earned) is a common fraud mechanism.
- **Non-recurring items appearing every year** — "one-time" charges that appear repeatedly are not one-time.
- **Goodwill impairment** — the company wrote down the value of an acquisition. Often signals the acquisition failed.

### Balance sheet red flags

- **Receivables growing faster than revenue** — may indicate the company is recognizing revenue before customers pay, or that customers aren't paying.
- **Inventory growing faster than cost of goods sold** — possible overvaluation of inventory, or a business that can't sell what it makes.
- **Related party receivables** — amounts owed by related parties (directors, officers, affiliated companies). These may never be collected.
- **Negative equity** — liabilities exceed assets. The company is technically insolvent.
- **Unusual intangible assets** — large values assigned to internally generated intangibles may be inflated.

### Cash flow red flags

- **Profitable but cash-flow negative** — a company reporting profit but burning cash. Accrual accounting allows "profit" without cash.
- **Large differences between net income and operating cash flow** — the gap shows how much of "profit" is not backed by cash.
- **Financing cash flows masking operating weakness** — a company that only generates cash by borrowing or selling shares is not self-sustaining.

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
3. **Person / Company → Transaction**: All related party transactions with amounts
4. **Company → Company (related party)**: Every related party entity named in the notes

---

## What investigators typically miss

1. **Note 1** — accounting policies. This is where the company describes how it counts revenue, values inventory, and measures everything else. Aggressive policies buried here can explain otherwise inexplicable results.
2. **The segment information note** — if the company has multiple business lines or geographies, segment disclosures show which parts are profitable and which are not. Losing segments are sometimes hidden in aggregation.
3. **Commitments and contingencies note** — future obligations and potential liabilities. Lease commitments, purchase obligations, and pending litigation. The total of all future commitments can dwarf what appears on the balance sheet.
4. **Share-based compensation note** — how much are executives being paid in stock options and restricted shares? This doesn't always appear prominently in the income statement.
5. **The five-year summary** — many annual reports include a multi-year financial summary. This makes trend analysis easy and is often overlooked.
6. **Management's report on internal controls** — if the company discloses material weaknesses in internal controls, it means they don't have adequate processes to catch errors or fraud.
7. **Changes in accounting policies** — when a company changes how it accounts for something, the effect is disclosed. A change that flatters results should raise questions.
