# Domain knowledge — Land registries and title systems

This skill is loaded by `/ingest` when the document type is a land registry extract, title search, cadastral record, RDPRM registration, or similar document from a property rights registration system.

This skill covers the registry/title system itself — how property rights are recorded, document types, and what anomalies mean. For market transaction analysis (who paid what, mortgage patterns, assessment values), see the `real-estate` skill. For Quebec-specific corporate registry records, see the `corporate-filings` skill.

Apply this knowledge in addition to the standard extraction process. It tells you what to look for, what terminology means, and what patterns are worth flagging.

---

## Document types covered

**Common law systems (Ontario, BC, UK, Australia, most US states):**
- Land title certificates and parcel register extracts
- Transfer instruments (deeds, grants)
- Mortgage / charge instruments
- Discharge instruments
- Caveats and cautions (notices of interest)
- Certificates of pending litigation / lis pendens
- Easements and rights of way
- Covenants and restrictions

**Civil law systems (Quebec, France, Louisiana, and others):**
- Land register (registre foncier) extracts
- Deeds of sale (actes de vente) — notarially certified
- Deeds of hypothec (actes d'hypothèque)
- Prior rank claims and legal hypothecs
- RDPRM registrations (movable hypothecs and floating charges)
- Declarations of co-ownership (syndicate declarations)

**Corporate property registries:**
- RDPRM (Quebec) — charges on movable property
- Personal Property Security Act (PPSA) registrations — Canada (common law provinces)
- UCC financing statements — US
- Companies House charges register — UK

---

## Always-present fields to extract

| Field | What to look for |
|-------|-----------------|
| **Property identifier** | The registry's unique parcel identifier (PIN in Ontario; lot number in Quebec; title number in UK/Australia; APN in US) |
| **Legal description** | The formal property description as registered |
| **Owner of record** | The registered title holder (may differ from beneficial owner) |
| **Instrument type** | What kind of document is registered (transfer, charge, discharge, caveat, etc.) |
| **Instrument date** | When the document was signed or executed |
| **Registration date** | When the instrument was registered — this is the legally operative date in most systems |
| **Registration number** | The registry's unique identifier for the instrument |
| **Grantor / débiteur** | The person transferring or encumbering the property |
| **Grantee / créancier** | The person receiving the property or the security |
| **Consideration / amount** | The price (for transfers) or principal amount (for mortgages/hypothecs) |
| **Rank** | For mortgages and charges: first, second, or lower rank (determines priority on default) |
| **Notary / solicitor** | The lawyer or notary who prepared the instrument (civil law: required; common law: usual) |

---

## Red flags — what to look for

### Title integrity

- **Gap in title chain** — an instrument refers to a prior instrument that is not in the register, or the chain of transfers does not connect. This may indicate a missing document, a fraud, or a registry error.
- **Rapid sequential transfers** — a property transferred multiple times in a short period, especially with increasing consideration, may indicate title fraud, a fraudulent flip scheme, or money laundering.
- **Transfer by power of attorney** — a transfer signed by someone acting under a power of attorney. The POA document may not be registered; verify that the authority existed and was valid at the time.
- **Transfer to a numbered company or trust** — obscures the beneficial owner. Cross-reference the corporate registry for directors.
- **Discharge of mortgage without corresponding payment** — in common law systems, a discharge can be registered without court oversight. An unexplained discharge may indicate a fraudulent release of security.

### Mortgages and charges

- **Stacked mortgages (second, third, or lower rank)** — multiple charges on the same property signal financial stress or complex financing. Note the rank, amount, and lender for each.
- **High-value mortgage relative to apparent property value** — may indicate an inflated appraisal or a fraud scheme.
- **Private individual as mortgagee** — an individual (not a financial institution) holding a first mortgage is unusual. Note their name and address.
- **Collateral or all-obligations mortgage** — a mortgage that secures all present and future debt, not a specific loan. Common for bank facilities but should be noted; the disclosed amount may understate actual exposure.
- **Mortgage to an offshore or shell entity** — a charge held by an entity in a secrecy jurisdiction is a red flag for beneficial ownership concealment.

### Caveats, cautions, and notices

- **Caveat or caution without explanation** — in Torrens title systems (Australia, parts of Canada), a caveat signals a third party claims an interest in the property. The caveator's identity and basis of claim are often significant.
- **Certificate of pending litigation (CPL) / lis pendens** — a notice that a lawsuit affecting the property is underway. The property cannot be transferred cleanly while the CPL is registered. Check the underlying court proceeding.
- **Constructive trust or resulting trust caveat** — a claim that someone has a beneficial interest in the property despite not being the registered owner. This is often filed in family law, estate, or business partnership disputes.

### Civil law systems (Quebec and others)

- **Hypothec registered without a corresponding sale** — a legal hypothec (arising by law, e.g. a construction lien) registered on a property without a deed of sale is worth examining.
- **Hypothec on universality of property** — a charge covering all of a company's present and future property. Note which lender holds this and the secured amount.
- **Prior rank (prior claim)** — certain claims rank above a conventional hypothec regardless of registration date (e.g. construction lien claims). Their presence affects the effective security of other creditors.
- **Radiation without discharge** — a hypothec cancelled without a matching payment or formal discharge may indicate an informal arrangement.

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

## Relationships to extract from land registry records

1. **Person / Company → Property**: Owner (registered title holder), previous owners, mortgagors, lien claimants
2. **Person / Company → Security interest**: Mortgagee/chargee/hypothecary creditor (with rank, amount, and date)
3. **Property → Instrument**: Each registered instrument (type, date, parties, amount)
4. **Property → CourtCase**: Any CPL, lis pendens, or judicial hypothec linking the property to litigation
5. **Notary / Solicitor → Transaction**: The legal professional who prepared the instrument

---

## What investigators typically miss

1. **The date of registration vs. the date of execution** — in most systems, priority is determined by registration date, not the date the document was signed. A mortgage signed before another but registered after it ranks behind it.
2. **The full chain of title** — a single instrument shows one transaction. An abstract or full title search shows every instrument ever registered. The pattern of ownership over time is often more revealing than the current state.
3. **PPSA / UCC / RDPRM for personal property** — immovable property is only part of the picture. Charges on equipment, vehicles, receivables, and inventory are registered in personal property security registries, not the land registry.
4. **The notary or conveyancer as a lead** — the legal professional who prepared an instrument is public information. In fraud investigations, the same notary or solicitor appearing across multiple suspicious transactions is significant.
5. **The civil law / common law distinction** — a company operating across both systems (e.g. a company incorporated in Ontario with properties in Quebec) will have instruments in both the land register and the RDPRM. Both must be searched.
6. **Caveat withdrawal timing** — in Torrens systems, a caveat lapses or is withdrawn after a period. The withdrawal of a caveat that was protecting an unregistered interest may leave that interest unprotected — and may indicate a settlement, payment, or dispute resolution.
