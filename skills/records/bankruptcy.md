# Domain knowledge — Bankruptcy and insolvency records

Loaded by `/ingest` when the document type is a bankruptcy filing, proposal, creditor list, trustee report, receiving order, or similar insolvency record.

---

## Document types covered

- Assignments in bankruptcy (voluntary)
- Receiving orders (involuntary bankruptcy)
- Consumer and commercial proposals
- Notices of intention to make a proposal
- Statements of affairs
- Proofs of claim and proofs of debt
- Dividend sheets
- Trustee's reports and final accounts
- Court orders under CCAA (Companies' Creditors Arrangement Act)
- Receivership orders and receiver's reports
- Certificates of discharge

---

## Always-present fields to extract

| Field | What to look for |
|-------|-----------------|
| **Bankrupt's name** | Full legal name of the individual or company |
| **Estate number** | Unique identifier assigned by the Office of the Superintendent of Bankruptcy |
| **Trustee** | Licensed Insolvency Trustee (LIT) administering the estate |
| **Date of bankruptcy / date of assignment** | When the bankruptcy was formally filed |
| **Date of discharge** | When the bankrupt was released from most debts |
| **Total assets** | From statement of affairs |
| **Total liabilities** | From statement of affairs |
| **Preferred creditors** | Creditors with priority (CRA, employees) |
| **Secured creditors** | Creditors with collateral (usually banks and mortgagees) |
| **Unsecured creditors** | General creditors — ranked last |
| **Dividend rate** | Cents on the dollar paid to unsecured creditors |

---

## Red flags

### Pre-bankruptcy conduct

- **Transactions at undervalue** — assets transferred for less than fair market value before bankruptcy. If within certain time limits, the trustee can reverse these.
- **Fraudulent preferences** — paying certain creditors before others, or transferring assets to related parties, shortly before bankruptcy. The Bankruptcy and Insolvency Act sets out look-back periods.
- **Concealed assets** — assets not disclosed in the statement of affairs. A bankrupt's sworn statement omitting assets is perjury.
- **Related party creditors near the top of the list** — a director's company claiming a large unsecured debt before other creditors.
- **Multiple bankruptcies** — the same individual filing bankruptcy more than once. In Canada, a second bankruptcy results in stricter conditions for discharge.

### Timing patterns

- **Bankruptcy filed shortly after a judgment** — the debtor is likely using bankruptcy to escape the judgment creditor.
- **Business bankruptcy followed closely by a new company in the same industry** — the "phoenix company" pattern: old company's debts left behind while the business continues under a new name.
- **Assets transferred to a spouse or family member in the years before bankruptcy** — common attempt to shield assets from creditors.

### Creditor list patterns

- **Unusual creditor names** — watch for the bankrupt's own companies, family members, or associates appearing as creditors. These claims may be inflated or fabricated.
- **Large gap between total assets and total liabilities** — a large shortfall, particularly if the bankrupt was recently apparently solvent, suggests either rapid dissipation of assets or fraudulent concealment.
- **CRA as a large creditor** — significant tax arrears indicate years of unpaid source deductions (employee CPP, EI, income tax), HST, or corporate tax. Failure to remit source deductions is a personal liability of directors.

---

## Terminology

| Term | Meaning |
|------|---------|
| **LIT** | Licensed Insolvency Trustee — the regulated professional administering the estate |
| **BIA** | Bankruptcy and Insolvency Act — Canada's primary insolvency statute |
| **CCAA** | Companies' Creditors Arrangement Act — applies to large companies (>$5M in claims) |
| **Statement of affairs** | The bankrupt's sworn statement of assets and liabilities at the date of bankruptcy |
| **Proof of claim** | A creditor's formal claim against the estate |
| **Dividend** | Payment to creditors from estate proceeds — often cents on the dollar |
| **Discharge** | Release from bankruptcy — individual is freed from most pre-bankruptcy debts |
| **Absolute discharge** | Immediate discharge with no conditions |
| **Conditional discharge** | Discharge subject to conditions (usually paying additional money to the estate) |
| **Suspended discharge** | Discharge delayed — usually for misconduct or second bankruptcy |
| **Mediation** | Required in consumer bankruptcies where the bankrupt opposes creditor claims |
| **Preferential payment** | Payment to one creditor over others shortly before bankruptcy — can be reversed |
| **Undervalue transaction** | Transfer of assets for less than fair value — can be reversed |
| **Fraudulent conveyance** | Transfer intended to defraud creditors — can be reversed |
| **Secured claim** | Backed by collateral; paid before unsecured creditors |
| **Administration charge** | The trustee's fee, paid from the estate before creditors |
| **OSB** | Office of the Superintendent of Bankruptcy — federal regulator of insolvency |
| **FICO** | Fraud Investigation and Compliance Office — the enforcement arm of OSB |

---

## Relationships to extract

1. **Person / Company → Person** (Bankrupt): Trustee, legal counsel, principal creditors
2. **Person / Company → Transaction** (Bankrupt): All pre-bankruptcy transactions of note
3. **Company → CourtCase**: Any CCAA or receivership order; any litigation by or against the estate
4. **Person → Company**: Every company the bankrupt was director of — directors of an insolvent company can have personal liability for unremitted source deductions
5. **Creditor → Bankrupt**: Every significant creditor with their claim amount and type (secured/preferred/unsecured)

---

## What investigators typically miss

1. **The statement of affairs** — a sworn document listing all assets and liabilities. Compare this to what you know from other documents about the bankrupt's assets. Discrepancies are newsworthy.
2. **Inspector's reports** — in commercial bankruptcies, creditors elect inspectors to oversee the trustee. Inspector meetings are documented and can be very candid.
3. **Section 170 report** — the trustee's report to creditors on the bankrupt's conduct. It specifically addresses whether the bankrupt has been honest and cooperative, and can flag fraud for OSB investigation.
4. **Proofs of claim from related parties** — always look at who is claiming against the estate and for how much. A director's holding company claiming $500K as an unsecured creditor may be inflating the claim.
5. **The employer's insolvency effect on employees** — employees are preferred creditors for up to 6 months' wages. But the Wage Earner Protection Program (WEPP) covers some of this. The trustee's obligation to notify employees is often not fulfilled promptly.
6. **The sequence of registrations and deregistrations** — when was the company incorporated, when did financial distress start (look for CRA liens, judgment liens), when was bankruptcy filed? The gap between these dates tells a story.
