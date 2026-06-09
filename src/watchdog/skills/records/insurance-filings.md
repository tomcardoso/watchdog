# Domain knowledge — Insurance filings

This skill is loaded by `/ingest` when the document type is an insurance regulatory return, actuarial report, reinsurance treaty, market conduct review report, or similar insurance industry regulatory document.

Apply this knowledge in addition to the standard extraction process. It tells you what to look for, what terminology means, and what patterns are worth flagging.

---

## Document types covered

- Annual regulatory returns filed with the insurance regulator
- Supervisory letters and regulatory orders
- Actuarial valuation reports
- Reinsurance treaties and arrangements
- Insurance company prospectuses and offering memoranda
- Rate filing applications
- Market conduct review reports
- Insurance holding company filings
- Captive insurance company filings
- In Canada: OSFI annual regulatory returns; provincial rate filing applications (FSRA, AMF)
- In the US: NAIC (National Association of Insurance Commissioners) filings; state insurance department examination reports
- In the UK: Prudential Regulation Authority (PRA) and Financial Conduct Authority (FCA) returns
- Internationally: Lloyd's of London syndicate accounts; Bermuda Monetary Authority returns; EU Solvency II filings

---

## Always-present fields to extract

| Field | What to look for |
|-------|-----------------|
| **Insurer name** | Legal name of the insurance company |
| **Regulator identifier** | The regulator's institution number or code |
| **Lines of business** | Life, property and casualty, title, mortgage, specialty, etc. |
| **Reporting period** | Fiscal year or quarter covered |
| **Gross written premium** | Total premiums written before reinsurance |
| **Net written premium** | Premiums retained after ceding to reinsurers |
| **Claims incurred** | Total losses paid and reserved |
| **Loss ratio** | Claims incurred / earned premium — a core performance metric |
| **Expense ratio** | Underwriting expenses / earned premium |
| **Combined ratio** | Loss ratio + expense ratio — above 100% means underwriting loss |
| **Reinsurer names** | Who is providing reinsurance and on what terms |
| **Capital and surplus** | The insurer's financial cushion |
| **Capital adequacy ratio** | The regulatory capital adequacy measure |
| **Appointed actuary** | The actuary responsible for reserving opinions |

---

## Red flags — what to look for

### Financial stability

- **Capital adequacy ratio below supervisory target** — most regulators set a target ratio (e.g. OSFI's 150% MCT in Canada; 100% under Solvency II in Europe). Below the target, regulators may impose restrictions. Approaching the minimum indicates potential insolvency.
- **Reserve deficiency** — the appointed actuary must opine on whether reserves (amounts set aside for future claims) are adequate. A deficiency means the insurer has underestimated future claims payments.
- **Rapid premium growth without corresponding capital** — an insurer writing premiums far faster than it is building capital may be taking on risk it cannot back.
- **High combined ratio sustained over multiple years** — an insurer consistently spending more on claims and expenses than it collects in premiums is losing money on underwriting and relying on investment income to survive.
- **Investment portfolio concentrated in illiquid or high-risk assets** — an insurer with a large share of assets in private equity, real estate, or subordinated debt may have difficulty meeting claims in a stress scenario.

### Reinsurance

- **Concentration of reinsurance with a single reinsurer** — heavy reliance on one reinsurer creates counterparty risk. If the reinsurer fails or disputes coverage, the insurer is exposed.
- **Reinsurance with affiliated entities** — ceding risk to a related company (captive reinsurer, parent company) may not genuinely transfer risk. "Finite reinsurance" arrangements that transfer very little actual risk but improve reported financial ratios are a fraud risk.
- **Reinsurance recoverables exceeding surplus** — if the amount the insurer expects to collect from reinsurers exceeds the insurer's own capital, it is highly dependent on those collections materializing.
- **Unrated or offshore reinsurers** — an insurer ceding risk to reinsurers that are unrated or domiciled in opaque jurisdictions (Cayman Islands, Barbados, Bermuda without equivalent regulation) may be parking risk without genuine transfer.

### Market conduct

- **Denial rates significantly above industry average** — a high claims denial rate for a given line of business may indicate systematic underpayment of claims.
- **Complaints above industry average** — regulators in many jurisdictions publish complaint statistics by insurer. An insurer with a complaints ratio well above the industry average warrants scrutiny.
- **Rate filing applications with inadequate actuarial support** — a rate increase application that lacks the actuarial data to support the requested increase.

---

## Jurisdiction terminology

| Term | Jurisdiction | Meaning |
|------|-------------|---------|
| **OSFI** | Canada | Office of the Superintendent of Financial Institutions — the federal regulator for federally incorporated insurers, banks, and pension plans |
| **MCT** | Canada | Minimum Capital Test — OSFI's standard for P&C insurer capital adequacy |
| **LICAT** | Canada | Life Insurance Capital Adequacy Test — OSFI's capital standard for life insurers |
| **FSRA** | Canada (Ontario) | Financial Services Regulatory Authority of Ontario — regulates provincially incorporated insurers |
| **AMF** | Canada (Quebec) | Autorité des marchés financiers — Quebec's financial regulator |
| **ICA** | Canada | Insurance Companies Act — the federal statute governing federally incorporated insurers |
| **IBC** | Canada | Insurance Bureau of Canada — the industry association; publishes industry statistics |
| **NAIC** | US | National Association of Insurance Commissioners — coordinates state insurance regulation |
| **PRA** | UK | Prudential Regulation Authority — regulates large insurers and banks in the UK |
| **Solvency II** | EU | The EU regulatory framework for insurance capital and risk management |
| **SCR** | EU | Solvency Capital Requirement — the Solvency II capital adequacy metric |
| **Lloyd's syndicate** | UK / Global | A group of Lloyd's underwriters that collectively insure large or complex risks |
| **Appointed actuary** | Universal | An actuary appointed by the insurer's board who must sign an opinion on the adequacy of reserves — a statutory role |
| **P&C** | Universal | Property and Casualty — the line of insurance covering homes, cars, and commercial property |
| **Captive insurance** | Universal | An insurer owned by the entity it insures — often used for self-insurance within a corporate group |
| **Reciprocal exchange** | Canada / US | A non-corporate insurer owned by its policyholders |

---

## Relationships to extract from insurance filings

1. **Insurer → Regulator**: Regulatory filing relationship (with institution number and line of business)
2. **Insurer → Reinsurer**: Reinsurance cession (with type of reinsurance, ceded premium, and reinsurer name)
3. **Insurer → Actuary**: Appointed actuary (individual and firm)
4. **Insurer → Parent / affiliate**: Ownership structure (holding company, group relationships)
5. **Insurer → Market conduct action**: Regulatory order or supervisory letter

---

## What investigators typically miss

1. **The appointed actuary's report** — the actuary must opine on reserve adequacy, but also on any material uncertainty. A qualified or adverse actuary's opinion is a significant warning sign.
2. **Reinsurance recoverable aging** — older unpaid reinsurance recoverables may indicate disputes with reinsurers about coverage, which can become a solvency issue.
3. **Run-off operations** — an insurer that has stopped writing new business but still has long-tail liabilities (asbestos, environmental) may be in run-off for decades. The adequacy of run-off reserves is a persistent issue.
4. **Surplus notes** — a form of subordinated debt that counts as capital for regulatory purposes but is economically debt. A high proportion of capital in surplus notes suggests the insurer's true capital buffer is smaller than it appears.
5. **Investment income dependency** — an insurer with a combined ratio consistently above 100% that remains solvent is living on investment income. Periods of low returns or rising rates (which depress bond values) can expose this weakness.
6. **Regulator public supervisory disclosures** — many regulators publish institution-level data, including capital ratios and certain financial metrics. Comparing this public data to the insurer's own disclosures can reveal inconsistencies.

---

## Sources and further reading

### Official and regulatory
- [OSFI — Minimum Capital Test Guideline (2026)](https://www.osfi-bsif.gc.ca/en/guidance/guidance-library/minimum-capital-test-guideline-2026) — Office of the Superintendent of Financial Institutions' capital adequacy framework for Canadian property and casualty insurers; defines the 100% minimum and 150% supervisory target MCT ratios referenced in the red flags section
- [OSFI — Life Insurance Capital Adequacy Test Guideline (2025)](https://www.osfi-bsif.gc.ca/en/guidance/guidance-library/life-insurance-capital-adequacy-test-guideline-2025) — OSFI's equivalent capital standard for life insurers (LICAT); establishes the core and total ratio requirements
- [OSFI — Guidance library](https://www.osfi-bsif.gc.ca/en/guidance/guidance-library) — Full index of OSFI's supervisory guidelines, advisories, and letters covering all federally regulated financial institutions in Canada
- [NAIC — National Association of Insurance Commissioners](https://content.naic.org/) — The US standard-setting body for insurance regulation since 1871; coordinates state insurance oversight and provides the Financial Data Repository, IRIS financial ratio reports, and risk-based capital analysis tools used by all 50 state regulators
- [IAIS — Insurance Core Principles and ComFrame](https://www.iais.org/activities-topics/standard-setting/icps-and-comframe/) — The International Association of Insurance Supervisors' globally accepted framework for insurance supervision; the ICPs and ComFrame establish standards for capital adequacy, reserving, reinsurance oversight, and market conduct across more than 200 jurisdictions

### Practitioner and public interest
- [Insurance Bureau of Canada — Facts Book](https://www.ibc.ca/industry-resources/resources-data/facts-book) — IBC's annual statistical reference for the Canadian property and casualty insurance industry; includes industry-level loss ratios, combined ratios, premium volume, and claims data useful for benchmarking individual insurer performance
