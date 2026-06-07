# Domain knowledge — Bankruptcy and insolvency records

Loaded by `/ingest` when the document type is a bankruptcy filing, proposal, creditor list, trustee report, receiving order, or similar insolvency record.

---

## Document types covered

- Voluntary bankruptcy assignments and petitions
- Involuntary bankruptcy orders (receiving orders, adjudications)
- Consumer and commercial proposals or voluntary arrangements
- Statements of affairs
- Proofs of claim and proofs of debt
- Dividend sheets
- Trustee's / administrator's reports and final accounts
- Receivership orders and receiver's reports
- Certificates or orders of discharge
- In Canada: Assignments in bankruptcy; BIA consumer and commercial proposals; CCAA proceedings; NOIs (Notices of Intention)
- In the US: Chapter 7 (liquidation), Chapter 11 (reorganization), and Chapter 13 (individual repayment plan) filings under the US Bankruptcy Code; PACER records
- In the UK: Individual voluntary arrangements (IVAs); company voluntary arrangements (CVAs); administration orders; liquidation proceedings
- In Australia: Part IX debt agreements; Part X personal insolvency agreements; voluntary administration

---

## Always-present fields to extract

| Field | What to look for |
|-------|-----------------|
| **Bankrupt's / debtor's name** | Full legal name of the individual or company |
| **Estate or case number** | Unique identifier assigned by the insolvency authority or court |
| **Trustee / administrator / liquidator** | The licensed professional administering the estate |
| **Date of bankruptcy / filing date** | When the insolvency was formally filed or ordered |
| **Date of discharge** | When the bankrupt was released from most debts |
| **Total assets** | From statement of affairs or schedules |
| **Total liabilities** | From statement of affairs or schedules |
| **Preferred / priority creditors** | Creditors with priority (tax authorities, employees) |
| **Secured creditors** | Creditors with collateral (banks, mortgagees) |
| **Unsecured creditors** | General creditors — ranked last |
| **Dividend rate** | Cents on the dollar paid to unsecured creditors |

---

## Red flags

### Pre-bankruptcy conduct

- **Transactions at undervalue** — assets transferred for less than fair market value before bankruptcy. If within certain look-back periods, the trustee can reverse these.
- **Fraudulent preferences** — paying certain creditors before others, or transferring assets to related parties, shortly before bankruptcy. Most insolvency statutes set look-back periods for these transactions.
- **Concealed assets** — assets not disclosed in the statement of affairs or bankruptcy schedules. A bankrupt's sworn statement omitting assets is perjury in virtually every jurisdiction.
- **Related party creditors near the top of the list** — a director's company claiming a large unsecured debt before other creditors.
- **Multiple bankruptcies** — the same individual filing insolvency proceedings more than once. Successive bankruptcies typically result in stricter conditions for discharge.

### Timing patterns

- **Bankruptcy filed shortly after a judgment** — the debtor is likely using bankruptcy to escape the judgment creditor.
- **Business bankruptcy followed closely by a new company in the same industry** — the "phoenix company" pattern: old company's debts left behind while the business continues under a new name.
- **Assets transferred to a spouse or family member in the years before bankruptcy** — common attempt to shield assets from creditors.

### Creditor list patterns

- **Unusual creditor names** — watch for the bankrupt's own companies, family members, or associates appearing as creditors. These claims may be inflated or fabricated.
- **Large gap between total assets and total liabilities** — a large shortfall, particularly if the bankrupt was recently apparently solvent, suggests either rapid dissipation of assets or fraudulent concealment.
- **Tax authority as a large creditor** — significant tax arrears indicate years of unpaid source deductions (payroll remittances) or corporate tax. Failure to remit source deductions is a personal liability of directors in many jurisdictions.

---

## Terminology

| Term | Jurisdiction | Meaning |
|------|-------------|---------|
| **LIT** | Canada | Licensed Insolvency Trustee — the regulated professional administering the estate |
| **BIA** | Canada | Bankruptcy and Insolvency Act — Canada's primary insolvency statute |
| **CCAA** | Canada | Companies' Creditors Arrangement Act — applies to large companies (>$5M in claims) |
| **NOI** | Canada | Notice of Intention to make a proposal — provides a stay of proceedings while a proposal is developed |
| **OSB** | Canada | Office of the Superintendent of Bankruptcy — federal regulator of insolvency in Canada |
| **Chapter 7** | US | US liquidation bankruptcy — assets sold, debts discharged |
| **Chapter 11** | US | US reorganization bankruptcy — business continues while a reorganization plan is developed |
| **Chapter 13** | US | US individual repayment plan bankruptcy |
| **PACER** | US | Public Access to Court Electronic Records — the US federal court filing database |
| **Administration** | UK | UK equivalent of CCAA / Chapter 11 — company continues while an administrator attempts rescue |
| **CVA / IVA** | UK | Company Voluntary Arrangement / Individual Voluntary Arrangement — UK equivalents of a commercial or consumer proposal |
| **Voluntary administration** | Australia | Australian equivalent of administration; a moratorium while a deed of company arrangement is negotiated |
| **Statement of affairs** | Universal | The debtor's sworn statement of assets and liabilities at the date of insolvency |
| **Proof of claim** | Universal | A creditor's formal claim against the estate |
| **Dividend** | Universal | Payment to creditors from estate proceeds — often cents on the dollar |
| **Discharge** | Universal | Release from insolvency — individual is freed from most pre-bankruptcy debts |
| **Preferential payment** | Universal | Payment to one creditor over others shortly before insolvency — can be reversed |
| **Undervalue transaction** | Universal | Transfer of assets for less than fair value — can be reversed |
| **Fraudulent conveyance** | Universal | Transfer intended to defraud creditors — can be reversed |
| **Secured claim** | Universal | Backed by collateral; paid before unsecured creditors |

---

## Relationships to extract

1. **Person / Company → Person** (Bankrupt): Trustee, legal counsel, principal creditors
2. **Person / Company → Transaction** (Bankrupt): All pre-bankruptcy transactions of note
3. **Company → CourtCase**: Any restructuring or receivership order; any litigation by or against the estate
4. **Person → Company**: Every company the bankrupt was director of — directors of an insolvent company can have personal liability for unremitted payroll deductions in many jurisdictions
5. **Creditor → Bankrupt**: Every significant creditor with their claim amount and type (secured/preferred/unsecured)

---

## What investigators typically miss

1. **The statement of affairs** — a sworn document listing all assets and liabilities. Compare this to what you know from other documents about the debtor's assets. Discrepancies are newsworthy.
2. **Inspector's or creditor committee reports** — in commercial insolvencies, creditors elect inspectors or a creditors' committee to oversee the trustee. Their meeting records are documented and can be very candid.
3. **The trustee's report on conduct** — the trustee's report to creditors on the bankrupt's conduct specifically addresses whether the debtor has been honest and co-operative, and can flag fraud for regulatory investigation.
4. **Proofs of claim from related parties** — always look at who is claiming against the estate and for how much. A director's holding company claiming a large amount as an unsecured creditor may be inflating the claim.
5. **The effect on employees** — employees are often preferred or priority creditors for unpaid wages, but their claims may be capped. Wage protection programs (where they exist) may cover some of this. The trustee's obligation to notify employees is often not fulfilled promptly.
6. **The sequence of registrations and deregistrations** — when was the company incorporated, when did financial distress start (look for tax liens, judgment liens), when was insolvency filed? The gap between these dates tells a story.
