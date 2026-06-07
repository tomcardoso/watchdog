# Domain knowledge — Real estate records

Loaded by `/ingest` when the document type is a title transfer, deed, mortgage instrument, lien, property assessment, or similar real property record.

---

## Document types covered

- Transfer of land (deeds)
- Charge / mortgage instruments
- Discharge of charge
- Certificate of pending litigation (CPL) on title
- Lien registrations (construction liens, tax liens, judgment liens)
- Property assessment notices
- Land registry abstracts of title
- Parcel registers (Ontario's electronic land registry)
- Subdivision and severance documents
- Easements and rights of way

---

## Always-present fields to extract

| Field | What to look for |
|-------|-----------------|
| **PIN (Property Identification Number)** | Ontario's unique 9-digit property identifier — links every instrument on the same parcel |
| **Legal description** | Lot, Plan, Concession — the formal property description |
| **Municipal address** | Street address — may differ from legal description |
| **Grantor / transferor** | Who is selling or transferring |
| **Grantee / transferee** | Who is buying or receiving |
| **Mortgagor / chargor** | Who is borrowing (giving the mortgage) |
| **Mortgagee / chargee** | Who is lending (holding the mortgage) |
| **Consideration** | The stated purchase price or loan amount |
| **Registration date** | When the instrument was registered at the land registry |
| **Document date** | When the document was signed — may differ from registration date |
| **Instrument number** | Unique identifier in the land registry |
| **Assessment value** | Assessed value for tax purposes (from assessment notices) |

---

## Red flags

### Transfer patterns

- **Consideration of $1 or $2** — a nominal transfer price usually indicates a gift between related parties, an estate transfer, a corporate reorganization, or an attempt to obscure the real price. Always note these.
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
- **Tax arrears lien** — property taxes are significantly overdue. Municipal tax sales are a matter of public record.
- **Multiple liens from different creditors** — owner has serious financial problems.

### Assessment anomalies

- **Assessed value significantly below comparable properties** — may indicate an error, a heritage designation, or an arrangement worth investigating.
- **Assessment appealed** — the owner challenged their assessment. Win or loss?

---

## Terminology

| Term | Meaning |
|------|---------|
| **Fee simple** | Full ownership — the most complete form of property ownership |
| **Leasehold** | Ownership of the right to use the property for a term, not the land itself |
| **Easement** | The right to use part of another person's land for a specific purpose |
| **Right of way** | A specific type of easement allowing passage across land |
| **Encumbrance** | Any claim, lien, or charge against a property |
| **Charge** | Ontario's term for a mortgage |
| **Discharge** | Removal of a mortgage or lien from title when paid off |
| **Transfer** | Ontario's term for a deed (sale of land) |
| **Abutting** | Adjacent or sharing a boundary |
| **LRO (Land Registry Office)** | Ontario's land titles system |
| **GEO** | Ontario's property identifier system |
| **MPAC** | Municipal Property Assessment Corporation — Ontario's assessment body |
| **ARN (Assessment Roll Number)** | Unique identifier in MPAC's system |
| **Land transfer tax** | Provincial tax on property transfers — calculated on consideration |
| **Beneficial owner** | The real economic owner, who may differ from the registered owner |

---

## Relationships to extract

1. **Person / Company → Property**: Owner (current), Previous owner, Mortgagor, Lien claimant
2. **Person / Company → Address**: Every address in the instrument — parties' addresses often appear only in real estate records
3. **Property → CourtCase**: Any CPL or judgment lien connecting a property to a court proceeding
4. **Property → Transaction**: The transfer price, mortgage amount, or lien amount

---

## What investigators typically miss

1. **The transferee's address** — the address used by a buyer at purchase may be their only documented address at that point in time. Capture it even if it looks like a lawyer's address.
2. **The solicitor's name on the document** — the lawyer who handled the transfer knows who they were acting for. In fraud investigations, the lawyer's firm is often significant.
3. **Historical title** — an abstract of title shows every instrument ever registered against a property. The pattern over time (who owned it, when, what mortgages) is often more revealing than the current state.
4. **Affidavit of residence and citizenship** — attached to Ontario transfers; the transferee declares their residency and citizenship for land transfer tax purposes. This is sworn testimony about where someone lives.
5. **Power of sale** — a mortgagee selling the property under the mortgage. Indicates the owner defaulted. Different from a regular sale.
6. **Vendor take-back mortgage** — the seller loans the buyer part of the purchase price. This is a relationship between buyer and seller worth noting.
