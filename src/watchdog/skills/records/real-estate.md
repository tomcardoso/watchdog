---
description: a real property record — title transfer or deed, mortgage/charge/hypothec instrument, discharge, lien, property assessment, land registry or parcel register extract, title search or abstract, caveat or certificate of pending litigation, or a personal property security registration (PPSA, UCC, RDPRM) — market transactions and the registry systems that record them
---
# Domain knowledge — Real estate and land registry records

Loaded by Watchdog when the document type is a title transfer, deed, mortgage or hypothec instrument, discharge, lien, property assessment, land registry or parcel register extract, title search, caveat, or a personal property security registration — any record of real property ownership or financing, or of the registry systems that record them. For Quebec-specific corporate registry records (REQ), see `corporate-filings`.

---

## Document types covered

**Market transactions:**
- Transfer of land / deeds of sale
- Mortgage / charge instruments
- Discharge of mortgage / charge
- Lien registrations (construction liens, tax liens, judgment liens)
- Property assessment notices
- Subdivision and severance documents

**Registry and title system records (common law: Ontario, BC, UK, Australia, most US states):**
- Land title certificates and parcel register extracts
- Title searches and abstracts of title
- Caveats and cautions (notices of interest)
- Certificates of pending litigation / lis pendens
- Easements and rights of way
- Covenants and restrictions

**Civil law systems (Quebec, France, Louisiana, and others):**
- Land register (registre foncier) extracts
- Deeds of sale (actes de vente) — notarially certified
- Deeds of hypothec (actes d'hypothèque)
- Prior rank claims and legal hypothecs
- Declarations of co-ownership (syndicate declarations)

**Personal property security registries:**
- RDPRM registrations (Quebec) — movable hypothecs and floating charges
- Personal Property Security Act (PPSA) registrations — Canada (common law provinces)
- UCC financing statements — US
- Companies House charges register — UK

**Jurisdiction-specific variants:**
- In Canada (Ontario): Transfers; Charges (mortgages); discharges of charge
- In the US: Grant deeds, warranty deeds, quitclaim deeds; deeds of trust; mechanic's liens; lis pendens

---

## Fields to extract

| Field | What to look for |
|-------|-----------------|
| **Property identifier** | The registry's unique parcel identifier (PIN in Ontario; lot number in Quebec; APN in the US; title number in UK/Australia) |
| **Legal description** | The formal property description as registered (lot, plan, cadastral reference) |
| **Municipal address** | Street address — may differ from legal description |
| **Owner of record** | The registered title holder (may differ from beneficial owner) |
| **Instrument type** | What kind of document is registered (transfer, charge, discharge, caveat, etc.) |
| **Grantor / transferor / seller** | The person transferring or encumbering the property |
| **Grantee / transferee / buyer** | The person receiving the property or the security |
| **Mortgagor / chargor / borrower** | Who is borrowing (giving the mortgage) |
| **Mortgagee / chargee / lender** | Who is lending (holding the mortgage) |
| **Consideration / amount** | The stated purchase price (for transfers) or principal amount (for mortgages/hypothecs/liens) |
| **Rank** | For mortgages and charges: first, second, or lower rank (determines priority on default) |
| **Document date** | When the instrument was signed or executed |
| **Registration date** | When the instrument was registered — the legally operative date in most systems |
| **Instrument / registration number** | The registry's unique identifier for the instrument |
| **Notary / solicitor** | The lawyer or notary who prepared the instrument (civil law: required; common law: usual) |
| **Assessment value** | Assessed value for tax purposes (from assessment notices) |

---

## Red flags

### Transfer patterns

- **Consideration of $1, $2, or nominal amount** — a nominal transfer price usually indicates a gift between related parties, an estate transfer, a corporate reorganization, or an attempt to obscure the real price. Always note these.
- **Transfer between related parties at below-market consideration** — may indicate a fraudulent preference (transferring assets to avoid creditors) or tax avoidance.
- **Rapid sequential transfers** — property that has changed hands three or more times in 12 months, especially with increasing consideration, may be involved in title fraud, mortgage fraud, or money laundering.
- **Transfer to a numbered company, shell entity, or trust** — obscures ultimate beneficial ownership. The corporate registry entry for the entity is a lead worth recording.
- **Transfer shortly before or after a court judgment or bankruptcy filing** — hallmark of a fraudulent transfer. Note the timing relative to any court cases in the vault.
- **Transfer by power of attorney** — a transfer signed by someone acting under a power of attorney. The POA document may not be registered; whether the authority existed and was valid at the time is a lead worth recording.

### Title integrity

- **Gap in title chain** — an instrument refers to a prior instrument that is not in the register, or the chain of transfers does not connect. This may indicate a missing document, a fraud, or a registry error.
- **Unexplained discharge in the title chain** — in common law systems, a discharge can be registered without court oversight. A discharge with no corresponding sale or refinancing visible in the chain may indicate a fraudulent release of security; record it as a lead.

### Mortgages and charges

- **Private individual as mortgagee** — an individual (not a financial institution) holding a mortgage, especially a first mortgage, is unusual. Note their name and address.
- **Stacked mortgages (second, third, or lower rank)** — multiple charges on the same property signal financial stress or complex financing. Note the rank, amount, and lender for each.
- **Mortgage amount far exceeding apparent property value** — may indicate an inflated appraisal or a fraud scheme, especially in combination with other red flags.
- **Collateral or all-obligations mortgage** — a mortgage that secures all present and future debt, not a specific loan. Common for bank facilities but should be noted; the disclosed amount may understate actual exposure.
- **Mortgage to an offshore or shell entity** — a charge held by an entity in a secrecy jurisdiction is a red flag for beneficial ownership concealment.
- **Discharge shortly before property transfer** — the mortgage was paid off and then the property was sold; record the sequence so it can be checked.

### Liens

- **Construction lien** — a contractor or supplier hasn't been paid. Indicates a payment dispute.
- **Writ of execution on title** — a judgment creditor has registered their judgment against the property owner's real estate.
- **Tax arrears lien** — property taxes are significantly overdue. Tax sales are a matter of public record in most jurisdictions.
- **Multiple liens from different creditors** — owner has serious financial problems.

### Caveats, cautions, and notices

- **Caveat or caution without explanation** — in Torrens title systems (Australia, parts of Canada), a caveat signals a third party claims an interest in the property. Record the caveator's identity and stated basis of claim — both are often significant.
- **Certificate of pending litigation (CPL) / lis pendens** — a notice that a lawsuit affecting the property is underway. The property cannot be transferred cleanly while the CPL is registered. The underlying court proceeding is a lead worth recording.
- **Constructive trust or resulting trust caveat** — a claim that someone has a beneficial interest in the property despite not being the registered owner. This is often filed in family law, estate, or business partnership disputes.

### Civil law systems (Quebec and others)

- **Hypothec registered without a corresponding sale** — a legal hypothec (arising by law, e.g. a construction lien) registered on a property without a deed of sale is worth examining.
- **Hypothec on universality of property** — a charge covering all of a company's present and future property. Note which lender holds this and the secured amount.
- **Prior rank (prior claim)** — certain claims rank above a conventional hypothec regardless of registration date (e.g. construction lien claims). Their presence affects the effective security of other creditors.
- **Radiation without discharge** — a hypothec cancelled without a matching payment or formal discharge may indicate an informal arrangement.

### Assessment anomalies

- **Assessed value significantly below comparable properties** — may indicate an error, a heritage designation, or an arrangement worth investigating.
- **Assessment appealed** — the owner challenged their assessment. The outcome is a lead worth recording.

---

## Common law vs. civil law terminology

| Concept | Common law (Ontario, BC, UK, Australia) | Civil law (Quebec, France) |
|---------|-----------------------------------------|---------------------------|
| **Property right** | Title | Droit réel / droit de propriété |
| **Registry system** | Land titles / Torrens (most jurisdictions) | Registre foncier / livre foncier |
| **Property identifier** | Title number / PIN / APN | Numéro de lot (cadastral) |
| **Transfer document** | Transfer / deed | Acte de vente (notarially certified) |
| **Security instrument** | Mortgage / charge | Hypothèque (conventionnelle, légale, ou judiciaire) |
| **Release of security** | Discharge | Radiation |
| **Notice of claim** | Caveat / caution / CPL | Préavis / inscription |
| **System of registration** | Title registration (rights are guaranteed by the state) | Publicity of rights (registration makes rights opposable to third parties) |
| **Ownership document** | Certificate of title | Acte notarié |
| **Registry professional** | Solicitor / conveyancer | Notaire (notary — mandatory for real estate in civil law jurisdictions) |
| **Lien for unpaid work** | Construction lien / mechanic's lien | Hypothèque légale de la construction |
| **Personal property charge** | PPSA registration (Canada) / UCC filing (US) | RDPRM registration (Quebec) |

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
| **APN** | US | Assessor's Parcel Number — the common US property identifier |
| **Deed of trust** | US | A security instrument used in many US states instead of a mortgage |
| **Lis pendens** | US / Latin | Notice of pending litigation registered against a property (equivalent to CPL in Canada) |
| **Land transfer tax / stamp duty land tax** | Universal | Tax on property transfers — calculated on consideration |
| **Beneficial owner** | Universal | The real economic owner, who may differ from the registered owner |

---

## Jurisdiction-specific notes

### Quebec (civil law)
The Quebec land register (Registre foncier) is searchable by cadastral lot number, not by civic address. The RDPRM (Registre des droits personnels et réels mobiliers) covers movable hypothecs and floating charges on personal property. Real estate transactions must be prepared and certified by a Quebec notary. The REQ (Registraire des entreprises du Québec) covers corporate filings separately.

### Ontario and common law Canada
Ontario uses a Torrens-based electronic land registry (POLARIS) with PIN-based parcel registers. Charges (mortgages) are registered electronically. The PPSA register covers security interests in personal property.

### UK
Companies House maintains a separate charges register for corporate security interests. Land Registry (England and Wales), Registers of Scotland, and Land Registry Northern Ireland each operate independently. Title numbers identify registered titles.

### Australia
Most Australian states use the Torrens title system. Caveats are the primary mechanism for protecting unregistered interests. PPSR (Personal Property Securities Register) covers personal property security nationwide.

### US
Real property recording is county-level (not federal or state). Deed types vary: warranty deeds (grantor guarantees title), quitclaim deeds (no guarantee), grant deeds (intermediate). UCC financing statements cover personal property security interests.

---

## Relationships to extract

1. **Person / Company → Property**: Owner (registered title holder), previous owners, mortgagors, lien claimants
2. **Person / Company → Security interest**: Mortgagee/chargee/hypothecary creditor (with rank, amount, and date)
3. **Person / Company → Address**: Every address in the instrument — parties' addresses often appear only in real estate records
4. **Property → Instrument**: Each registered instrument (type, date, parties, amount)
5. **Property → CourtCase**: Any CPL, lis pendens, judgment lien, or judicial hypothec linking the property to litigation
6. **Property → Transaction**: The transfer price, mortgage amount, or lien amount
7. **Notary / Solicitor → Transaction**: The legal professional who prepared the instrument

---

## What investigators typically miss

1. **The date of registration vs. the date of execution** — in most systems, priority is determined by registration date, not the date the document was signed. A mortgage signed before another but registered after it ranks behind it.
2. **The full chain of title** — a single instrument shows one transaction. An abstract or full title search shows every instrument ever registered. The pattern of ownership over time (who owned it, when, what mortgages) is often more revealing than the current state.
3. **PPSA / UCC / RDPRM for personal property** — immovable property is only part of the picture. Charges on equipment, vehicles, receivables, and inventory are registered in personal property security registries, not the land registry.
4. **The notary or conveyancer as a lead** — the legal professional who prepared an instrument is public information, and knows who they were acting for. In fraud investigations, the same notary or solicitor appearing across multiple suspicious transactions is significant.
5. **The civil law / common law distinction** — a company operating across both systems (e.g. a company incorporated in Ontario with properties in Quebec) will have instruments in both the land register and the RDPRM. Both must be searched.
6. **Caveat withdrawal timing** — in Torrens systems, a caveat lapses or is withdrawn after a period. The withdrawal of a caveat that was protecting an unregistered interest may leave that interest unprotected — and may indicate a settlement, payment, or dispute resolution.
7. **The transferee's address and residency declarations** — the address used by a buyer at purchase may be their only documented address at that point in time; capture it even if it looks like a lawyer's address. Statutory declarations and affidavits of residence attached to transfers in many jurisdictions are sworn testimony about where someone lives.
8. **Power of sale and vendor take-back mortgages** — a mortgagee selling the property under the mortgage because the owner defaulted is different from a regular sale. A seller loaning the buyer part of the purchase price (vendor take-back) is a relationship between buyer and seller worth noting.

---

## Sources and further reading

### Official and regulatory
- [FATF — Money Laundering and Terrorist Financing Through the Real Estate Sector (2007)](https://www.fatf-gafi.org/en/publications/Methodsandtrends/Moneylaunderingandterroristfinancingthroughtherealestatesector.html) — primary FATF typologies report covering rapid transfers, shell company purchases, inflated appraisals, and mortgage fraud
- [FATF — Risk-Based Approach Guidance for the Real Estate Sector (July 2022)](https://www.fatf-gafi.org/en/publications/Fatfrecommendations/Guidance-rba-real-estate-sector.html) — updated guidance for real estate supervisors and practitioners, including professional enablers, virtual assets, and cross-border transactions; includes typologies, case studies, and examples of good practice
- [FINTRAC — Money Laundering and Terrorist Financing Indicators: Real Estate](https://fintrac-canafe.canada.ca/guidance-directives/transaction-operation/indicators-indicateurs/real_mltf-eng) — Canadian indicators developed from three years of suspicious transaction report review; directly maps to the rapid-transfer and shell-company red flags in this skill
- [FINTRAC — Operational Brief: Money Laundering in Real Estate Financial Transactions](https://fintrac-canafe.canada.ca/intel/operation/real-eng) — case-based brief with concrete transaction patterns seen by Canadian authorities
- [FINTRAC — Real estate brokers, sales representatives, and developers](https://fintrac-canafe.canada.ca/re-ed/real-eng) — FINTRAC's official guidance on anti-money laundering obligations for real estate professionals in Canada, including client identification, suspicious transaction reporting, and record-keeping requirements
- [BC Land Owner Transparency Registry (LOTR) — LTSA](https://ltsa.ca/products-services/lotr/) — Canada's first public beneficial ownership registry for land; operated by BC's Land Title and Survey Authority; requires disclosure of individuals with indirect interests through corporations, trusts, and partnerships
- [FinCEN — Geographic Targeting Orders (GTOs)](https://www.fincen.gov/news/news-releases/fincen-targets-shell-companies-purchasing-luxury-properties-seven-major) — US Treasury's Financial Crimes Enforcement Network program requiring title insurance companies to identify the natural persons behind shell companies used in all-cash residential real estate purchases in targeted metropolitan areas

### Practitioner and public interest
- [Global Witness — On the House: How US Laws Let Corrupt Individuals Buy Real Estate](https://globalwitness.org/en/campaigns/corruption-and-money-laundering/on-the-house/) — undercover investigation showing how anonymous shell companies acquire US property
- [Global Witness — How anonymous companies help launder money in US real estate](https://www.globalwitness.org/en/campaigns/corruption-and-money-laundering/anonymous-companies-used-to-launder-money-in-us-real-estate/) — analysis of how the absence of beneficial ownership disclosure requirements in the US enables laundering through real estate; includes five case studies spanning drug trafficking, corruption, and fraud
- [Global Witness — £100bn of Property in England and Wales Is Secretly Owned](https://globalwitness.org/en/press-releases/100bn-of-property-in-england-and-wales-is-secretly-owned-estimates-show/) — UK-specific investigation into anonymous property ownership via shell companies

### Notes on unsourced claims
The Quebec-specific red flags (radiation without discharge, hypothec on universality, prior rank above a conventional hypothec) are accurate descriptions of civil law practice but lack a single authoritative English-language public source. The Chambre des notaires du Québec and the Barreau du Québec publish practitioner guidance, but much of it is members-only. Treat these items as editorially reviewed pending a Quebec-specific citation.
