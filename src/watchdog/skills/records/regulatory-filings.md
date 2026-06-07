# Domain knowledge — Regulatory filings

This skill is loaded by `/ingest` when the document type is a securities disclosure, insider trading report, continuous disclosure document, prospectus, or similar filing with a securities regulator.

Apply this knowledge in addition to the standard extraction process. It tells you what to look for, what terminology means, and what patterns are worth flagging.

---

## Document types covered

- Annual reports and annual information forms
- Management discussion and analysis (MD&A)
- Material change reports and current reports (material events)
- Insider trading reports
- Early warning / large shareholder disclosure filings
- Prospectuses (final and preliminary)
- Shelf prospectuses and supplements
- Business acquisition reports
- Technical reports (mining and resource sector)
- In Canada: Annual Information Forms (AIF); Material Change Reports (MCR); SEDI insider trading reports; Early Warning Reports (EWR); NI 43-101 technical reports; SEDAR+ filings generally
- In the US: SEC Form 10-K (annual); 10-Q (quarterly); 8-K (material events); Form 4 (insider transactions); Forms 13D/G (5%+ shareholder); EDGAR filings generally
- In the UK: Annual reports filed with Companies House; Regulatory News Service (RNS) disclosures; FCA filings
- In Australia: ASX continuous disclosure announcements; ASIC filings

---

## Always-present fields to extract

| Field | What to look for |
|-------|-----------------|
| **Issuer name** | Legal name of the company filing |
| **Exchange and ticker** | Where the company is listed |
| **Regulator identifier** | SEDAR+ profile number, SEC CIK, or equivalent |
| **Filing date** | When the document was filed |
| **Period covered** | The fiscal period the filing relates to |
| **Auditor** | Name of the auditing firm |
| **Certifying officers** | CEO and CFO who sign certifications |
| **Insider name** | For insider reports: the insider filing the report |
| **Transaction date** | When the trade occurred |
| **Securities traded** | Type and number of securities |
| **Transaction price** | Price per security |
| **Post-transaction holdings** | Total holdings after the transaction |

---

## Red flags — what to look for

### Insider trading patterns

- **Large insider sales before a negative disclosure** — insiders selling significant holdings shortly before a material change report disclosing bad news is the classic insider trading pattern. Compare insider transaction dates to subsequent disclosure dates.
- **Cluster of insider sales** — multiple insiders selling in the same short window, even in small amounts, can signal that people close to the company know something.
- **Insider purchases followed by a positive announcement** — the inverse: insiders buying before a positive announcement. While not automatically illegal (insiders may buy for many legitimate reasons), the timing is relevant.
- **Late insider filings** — insiders must file within a set deadline after a trade (5 calendar days in Canada under NI 55-104; 2 business days in the US under Form 4). Chronically late filers may be concealing the timing of trades relative to material information.
- **Transactions in derivatives (options, warrants)** — insider option exercises and sales are disclosed; option grants are also disclosed. Large option grants to executives shortly before positive announcements are worth examining.

### Continuous disclosure anomalies

- **Material change report filed without a press release** — in some jurisdictions this is permissible (confidential filing) but unusual and worth questioning.
- **Restated financial statements** — a company that restates previously filed financials has corrected material errors. The reason for the restatement and its magnitude are important.
- **Auditor resignation** — an auditor that resigns mid-year rather than completing an audit may have disagreed with management over accounting treatment.
- **CEO or CFO departure shortly after a certification period** — executives certify that financial statements are accurate; a departure shortly after signing a certification, without explanation, is a potential red flag.
- **Going concern qualification** — see also `financial-statements` skill. In a regulatory filing context, a going concern note triggers additional scrutiny from securities regulators.
- **Change in fiscal year** — may indicate an attempt to delay disclosure of a difficult quarter.

### Mining and resource sector (NI 43-101 / JORC / PERC)

- **Qualified person credentials** — a technical report must be prepared or supervised by a "qualified person" under the applicable standard. Check that the QP is actually registered as a professional in the relevant jurisdiction.
- **Historical estimates included without disclaimer** — using historical resource estimates without the required disclaimer is a common disclosure violation.
- **Resource vs. reserve** — the distinction between a mineral resource (in situ tonnage and grade) and a mineral reserve (economically extractable) is fundamental. Companies sometimes blur this distinction in their marketing materials.
- **Preliminary economic assessment vs. feasibility study** — a conceptual study requires a much lower level of confidence than a prefeasibility or feasibility study. Investor-facing materials that treat a conceptual study as equivalent to a feasibility study are misleading.

### Large shareholder disclosure

- **Purpose of acquisition disclosure** — large shareholder filings must state the acquirer's intention (passive investment, or intent to influence management). Changes in stated purpose between successive filings are significant.
- **Group filings** — multiple parties acting jointly must file as a group. Failure to aggregate holdings when parties are acting in concert is a regulatory violation.
- **Threshold creep** — an acquirer who crosses the initial disclosure threshold, then slowly increases holdings, may be attempting a creeping takeover without triggering a formal bid.

---

## Jurisdiction terminology

### Canada

| Term | Meaning |
|------|---------|
| **SEDAR+** | System for Electronic Document Analysis and Retrieval Plus — the Canadian securities filing database |
| **SEDI** | System for Electronic Disclosure by Insiders — the Canadian insider trading database |
| **NI 51-102** | National Instrument 51-102 — continuous disclosure obligations for reporting issuers |
| **NI 55-104** | National Instrument 55-104 — insider reporting requirements |
| **NI 43-101** | National Instrument 43-101 — standards for disclosure of mineral projects |
| **AIF** | Annual Information Form — a detailed annual disclosure document |
| **Early warning report** | Required when a person acquires 10%+ of a public company's voting securities |
| **Material change** | A change that would reasonably be expected to have a significant effect on the market price of securities |
| **CSA** | Canadian Securities Administrators — the umbrella organization of provincial and territorial securities regulators |
| **OSC / BCSC / AMF** | Ontario Securities Commission / BC Securities Commission / Autorité des marchés financiers |

### United States

| Term | Meaning |
|------|---------|
| **EDGAR** | Electronic Data Gathering, Analysis, and Retrieval — the SEC's public filing database |
| **CIK** | Central Index Key — the SEC's unique identifier for each filer |
| **10-K** | Annual report filed with the SEC |
| **10-Q** | Quarterly report (Q1, Q2, Q3; Q4 is covered by the 10-K) |
| **8-K** | Current report filed for material events (officer changes, auditor changes, material contracts) |
| **Form 4** | Insider transaction report — must be filed within 2 business days of a trade |
| **13D** | Filed when a person acquires more than 5% of a company with intent to influence it |
| **13G** | Filed when a person acquires more than 5% as a passive investor |
| **Reg FD** | Regulation Fair Disclosure — prohibits selective disclosure of material non-public information |
| **SOX certifications** | Sarbanes-Oxley Section 302 and 906 certifications — CEO and CFO personally certify financial statement accuracy |

---

## Relationships to extract from regulatory filings

1. **Person → Company**: Insider (with role: director, officer, 10%+ holder), Auditor (firm and individual engagement partner)
2. **Person → Transaction**: Insider trade (date, type, quantity, price, post-transaction holdings)
3. **Company → Exchange**: Listed securities (exchange, ticker, class of security)
4. **Company → Auditor**: Auditing relationship (with any change noted)
5. **Company → Company**: Subsidiaries, parent company, significant shareholders

---

## What investigators typically miss

1. **The CEO/CFO certifications** — under most securities regimes, executives personally certify the accuracy of financial statements. If the company later restates, those certifications become important.
2. **The risk factor section** — companies are required to disclose known risks. A risk factor that materializes into actual losses (and was disclosed in prior filings) shows the company knew the risk; one that materializes without prior disclosure suggests the company may have known and not disclosed.
3. **Insider filing timing vs. announcement date** — the most important analysis for insider trading stories is the gap between the transaction date and the date of a subsequent public announcement. Compare transaction dates to material change reports and press releases.
4. **Director and officer change disclosures** — the reasons given for executive departures ("to pursue other opportunities") are boilerplate. Look for what was happening operationally or financially at the company at the same time.
5. **The management information circular (proxy circular)** — the annual proxy contains compensation details, related-party transactions, and director independence assessments. For stories about executive pay or governance, this is the primary document.
6. **Cross-border dual-listed companies** — a company listed on exchanges in multiple jurisdictions files in multiple systems; the disclosures may differ slightly. Comparing both filings can reveal inconsistencies.
