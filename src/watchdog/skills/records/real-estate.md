# Domain knowledge — Real estate records

Loaded by `/ingest` when the document type is a title transfer, deed, mortgage instrument, lien, property assessment, or similar real property market transaction record.

This skill covers market transactions (who bought what, for how much, under what financing). For the registry/title system itself — how property rights are recorded, document types across common law and civil law systems — see the `land-registries` skill.

---

## Document types covered

- Transfer of land / deeds of sale
- Mortgage / charge instruments
- Discharge of mortgage / charge
- Lien registrations (construction liens, tax liens, judgment liens)
- Property assessment notices
- Land registry abstracts of title
- Subdivision and severance documents
- Easements and rights of way
- In Canada (Ontario): Parcel registers; Transfers; Charges (mortgages); CPLs (certificates of pending litigation on title)
- In Canada (Quebec): Actes de vente; actes d'hypothèque — see also `land-registries` skill
- In the US: Grant deeds, warranty deeds, quitclaim deeds; deeds of trust; mechanic's liens; lis pendens

---

## Always-present fields to extract

| Field | What to look for |
|-------|-----------------|
| **Property identifier** | The unique registry identifier (PIN in Ontario; lot number in Quebec; APN in the US; title number in UK/Australia) |
| **Legal description** | The formal property description (lot, plan, address, cadastral reference) |
| **Municipal address** | Street address — may differ from legal description |
| **Grantor / transferor / seller** | Who is selling or transferring |
| **Grantee / transferee / buyer** | Who is buying or receiving |
| **Mortgagor / chargor / borrower** | Who is borrowing (giving the mortgage) |
| **Mortgagee / chargee / lender** | Who is lending (holding the mortgage) |
| **Consideration** | The stated purchase price or loan amount |
| **Registration date** | When the instrument was registered at the land registry |
| **Document date** | When the document was signed — may differ from registration date |
| **Instrument number** | Unique identifier in the land registry |
| **Assessment value** | Assessed value for tax purposes (from assessment notices) |

---

## Red flags

### Transfer patterns

- **Consideration of $1, $2, or nominal amount** — a nominal transfer price usually indicates a gift between related parties, an estate transfer, a corporate reorganization, or an attempt to obscure the real price. Always note these.
- **Transfer between related parties at below-market consideration** — may indicate a fraudulent preference (transferring assets to avoid creditors) or tax avoidance.
- **Multiple rapid transfers of the same property** — property that has changed hands three or more times in 12 months may be involved in title fraud, mortgage fraud, or money laundering.
- **Transfer to a numbered company or shell entity** — obscures ultimate beneficial ownership.
- **Transfer shortly before or after a court judgment or bankruptcy filing** — hallmark of a fraudulent transfer. Note the timing relative to any court cases in the vault.

### Mortgage patterns

- **Private lender as mortgagee** — an individual holding a mortgage is more unusual than a bank. Note the lender's name and address.
- **Second or third mortgages** — multiple charges on a property signal financial stress or sophisticated financing arrangements.
- **Mortgage amount far exceeding apparent property value** — possible fraud, especially in combination with other red flags.
- **Discharge shortly before property transfer** — the mortgage was paid off and then the property was sold; verify the sequence is legitimate.
- **Collateral mortgage** — a mortgage securing all present and future debts to the lender, not a specific loan. Common with banks but should be noted.

### Lien patterns

- **Construction lien** — a contractor or supplier hasn't been paid. Indicates a payment dispute.
- **Writ of execution on title** — a judgment creditor has registered their judgment against the property owner's real estate.
- **Tax arrears lien** — property taxes are significantly overdue. Tax sales are a matter of public record in most jurisdictions.
- **Multiple liens from different creditors** — owner has serious financial problems.

### Assessment anomalies

- **Assessed value significantly below comparable properties** — may indicate an error, a heritage designation, or an arrangement worth investigating.
- **Assessment appealed** — the owner challenged their assessment. Win or loss?

---

## Terminology

| Term | Jurisdiction | Meaning |
|------|-------------|---------|
| **Fee simple** | Common law | Full ownership — the most complete form of property ownership |
| **Leasehold** | Universal | Ownership of the right to use the property for a term, not the land itself |
| **Easement** | Universal | The right to use part of another person's land for a specific purpose |
| **Encumbrance** | Universal | Any claim, lien, or charge against a property |
| **Charge** | Canada (Ontario) | Ontario's term for a mortgage |
| **Transfer** | Canada (Ontario) | Ontario's term for a deed (sale of land) |
| **PIN** | Canada (Ontario) | Property Identification Number — Ontario's 9-digit unique property identifier |
| **LRO** | Canada (Ontario) | Land Registry Office — Ontario's land titles system |
| **MPAC** | Canada (Ontario) | Municipal Property Assessment Corporation — Ontario's assessment body |
| **ARN** | Canada (Ontario) | Assessment Roll Number — unique identifier in MPAC's system |
| **Hypothèque / acte d'hypothèque** | Quebec / France | The civil law equivalent of a mortgage |
| **APN** | US | Assessor's Parcel Number — the common US property identifier |
| **Deed of trust** | US | A security instrument used in many US states instead of a mortgage |
| **Mechanic's lien / construction lien** | US / Canada | A lien filed by a contractor or supplier for unpaid work |
| **Lis pendens** | US / Latin | Notice of pending litigation registered against a property (equivalent to CPL in Canada) |
| **Title number** | UK / Australia | The unique identifier for a registered title in the land registry |
| **Land transfer tax / stamp duty land tax** | Universal | Tax on property transfers — calculated on consideration |
| **Beneficial owner** | Universal | The real economic owner, who may differ from the registered owner |

---

## Relationships to extract

1. **Person / Company → Property**: Owner (current), Previous owner, Mortgagor, Lien claimant
2. **Person / Company → Address**: Every address in the instrument — parties' addresses often appear only in real estate records
3. **Property → CourtCase**: Any CPL, lis pendens, or judgment lien connecting a property to a court proceeding
4. **Property → Transaction**: The transfer price, mortgage amount, or lien amount

---

## What investigators typically miss

1. **The transferee's address** — the address used by a buyer at purchase may be their only documented address at that point in time. Capture it even if it looks like a lawyer's address.
2. **The solicitor's name on the document** — the lawyer who handled the transfer knows who they were acting for. In fraud investigations, the lawyer's firm is often significant.
3. **Historical title** — an abstract of title shows every instrument ever registered against a property. The pattern over time (who owned it, when, what mortgages) is often more revealing than the current state.
4. **Statutory declarations and affidavits of residence** — attached to transfers in many jurisdictions; the transferee declares residency and citizenship for tax purposes. This is sworn testimony about where someone lives.
5. **Power of sale** — a mortgagee selling the property under the mortgage because the owner defaulted. Different from a regular sale.
6. **Vendor take-back mortgage** — the seller loans the buyer part of the purchase price. This is a relationship between buyer and seller worth noting.
