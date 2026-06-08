# Domain knowledge — Vehicle and vessel registration records

This skill is loaded by `/ingest` when the document type is a motor vehicle registration, certificate of title, vehicle lien record, vessel registration, or related transport registration document. For aircraft, use `aircraft-logs` instead, which covers aircraft registration, airworthiness, and flight tracking.

Apply this knowledge in addition to the standard extraction process. It tells you what to look for, what terminology means, and what patterns are worth flagging.

---

## Document types covered

- Motor vehicle registration certificates and title documents
- Vehicle title transfer records
- Lien and encumbrance registrations against vehicles (PPSA / UCC filings)
- Fleet registration records (government, corporate, not-for-profit)
- Off-road vehicle and snowmobile registration records
- Recreational vehicle and trailer registration records
- Vessel / watercraft registration and licensing records
- Vehicle licence plate records (where accessible)
- Commercial vehicle permits and oversize/overweight permits
- Dealer registration and fleet records
- In Canada: provincial vehicle registration (ServiceOntario, ICBC, SAAQ, etc.); Transport Canada vessel registration; provincial personal property security registries (PPSA) for vehicle liens
- In the US: State DMV title and registration records; NHTSA vehicle identification number (VIN) data; Coast Guard vessel documentation; state UCC lien filings
- In the UK: Driver and Vehicle Licensing Agency (DVLA) records; Maritime and Coastguard Agency (MCA) vessel registration
- In Australia: State/territory road transport authority registrations; Australian Maritime Safety Authority (AMSA) vessel registration

---

## Always-present fields to extract

| Field | What to look for |
|-------|-----------------|
| **Registered owner** | The person or entity in whose name the vehicle is registered |
| **Vehicle identification number (VIN)** | The unique 17-character identifier for motor vehicles |
| **Make, model, year** | The vehicle's manufacturer, model designation, and model year |
| **Licence plate / registration number** | The plate issued and its jurisdiction |
| **Registration date** | When the current registration was issued |
| **Expiry date** | When registration expires |
| **Address of registered owner** | The address on file at the time of registration |
| **Lienholder** | Any lender or financier with a security interest in the vehicle |
| **Transfer date** | When ownership changed hands |
| **Purchase price** | Where disclosed on the title transfer |
| **Vessel registration number** | For watercraft, the hull identification number and registration number |

---

## Red flags — what to look for

### Ownership and shell structures

- **Corporate or trust registration** — a vehicle registered to a numbered company, trust, or holding entity rather than an individual. This is common with expensive vehicles, yachts, and commercial fleets and can obscure beneficial ownership.
- **Mismatch between driver and registered owner** — where traffic stops or incident records show a vehicle consistently operated by someone other than the registered owner.
- **Rapid transfers between related parties** — a vehicle transferred multiple times among entities that share directors, addresses, or beneficial owners may indicate artificial transactions, asset hiding, or circular ownership arrangements.
- **Registration address of record** — the address registered on the title may differ from where the owner actually resides. Using a different jurisdiction's registration can sometimes reduce insurance costs or registration fees (common in border regions).

### Liens and financial claims

- **Undisclosed liens** — a lien registered against a vehicle that the buyer was not aware of; in most jurisdictions, a lien against the vehicle survives a sale unless discharged. Purchasing a vehicle without checking the lien registry is a common fraud vector.
- **Lien holder is an unusual entity** — a private company or individual (rather than a bank or finance company) holding a lien against a vehicle may indicate a private loan arrangement worth examining.
- **Liens registered shortly before or after a transfer** — a lien registered just before a sale, or a lien that appears to have been paid off and re-registered, may indicate financial manipulation.

### Fleet and commercial vehicles

- **Government fleet vehicles** — government fleet registrations can reveal the size, cost, and composition of public-sector vehicle fleets. Disproportionate use of luxury vehicles, unusual fleet growth, or vehicles assigned to specific individuals are worth examining.
- **Nonprofit and charity vehicles** — vehicles registered to charities or nonprofits used for purposes inconsistent with the organization's stated mission.
- **Commercial vehicle permit patterns** — repeated oversize or overweight permits issued to a specific carrier may indicate regulatory accommodation or enforcement gaps.

### Vessel registration

- **Flag of convenience** — a vessel registered in a jurisdiction (Panama, Marshall Islands, Liberia) primarily to avoid regulatory requirements of the owner's home country. This is a common mechanism in environmental and labour violations at sea.
- **Beneficial ownership vs. registered owner** — commercial vessels are often registered to a special purpose company. The registered owner is rarely the ultimate beneficial owner.
- **Change of flag and name** — a vessel with a history of flag changes, name changes, and ownership transfers (particularly following an incident) is a significant red flag for regulatory evasion.

---

## Jurisdiction terminology

| Term | Jurisdiction | Meaning |
|------|-------------|---------|
| **VIN** | Universal | Vehicle Identification Number — the 17-character unique identifier stamped on motor vehicles manufactured after 1981 |
| **Title** | US/Canada | The legal document establishing ownership of a vehicle |
| **Certificate of registration** | Canada/UK/Australia | The document issued by the transport authority confirming registration |
| **PPSA** | Canada | Personal Property Security Act — the provincial legislation governing security interests in personal property including vehicles |
| **UCC** | US | Uniform Commercial Code — the US framework governing security interests in personal property |
| **DVLA** | UK | Driver and Vehicle Licensing Agency — maintains vehicle registration records in Great Britain |
| **DMV** | US | Department of Motor Vehicles (varies by state — may be called BMV, RMV, etc.) |
| **Hull identification number (HIN)** | Universal | The unique identifier for a boat hull, equivalent to a VIN |
| **Flag state** | Universal | The country under whose laws a vessel is registered and whose regulations it must follow |
| **Flag of convenience** | Universal | Registration of a vessel in a foreign country to benefit from lower regulatory standards or taxes |
| **Coast Guard documentation** | US | Federal vessel registration for vessels over 5 net tons used in commerce or offshore waters |
| **Liens** | Universal | A creditor's legal claim against property as security for a debt |
| **Registered keeper** | UK | The person or organization responsible for the vehicle in the DVLA record (may differ from legal owner) |

---

## Relationships to extract from vehicle and vessel records

1. **Person/entity → Vehicle**: Ownership, registration, and transfer history
2. **Vehicle → Lienholder**: Who has a security interest and when
3. **Vehicle → Prior owners**: Chain of title with dates and prices where available
4. **Vessel → Flag state**: Jurisdiction of registration
5. **Corporate owner → Beneficial owner**: Where the registered owner is a company, who ultimately controls it

---

## What investigators typically miss

1. **Lien registry searches** — a title search alone does not reveal all claims on a vehicle. In most jurisdictions, a separate search of the personal property security registry (PPSA in Canada, UCC in the US) is needed to find all registered security interests.
2. **Historical registration data** — current registration records show current ownership. Historical registration data — who owned the vehicle in prior years — is often available by FOI from transport authorities and can document patterns of ownership transfer.
3. **The registered address history** — where a vehicle was registered over time tracks the movements and addresses of its owners in a way that is often impossible to obtain from other sources.
4. **Fleet size as a financial indicator** — the number of vehicles registered to a company at a point in time is a proxy for the scale of its operations. Changes in fleet size before and after regulatory action or financial distress are meaningful.
5. **Cross-matching vehicle records with corporate registrations** — a vehicle registered to a numbered company whose directors are identifiable through corporate filings links vehicle records to beneficial ownership. This cross-match is rarely done.
6. **Insurance write-offs and salvage titles** — a vehicle with a salvage or rebuilt title has been written off by an insurer; where it appears with a clean title subsequently, the title has been washed. Title washing is fraud and is common in vehicle trafficking.
7. **Vessel AIS and port records** — a vessel's registration documents state where it is registered; AIS tracking data (where available) and port arrival records show where it actually goes. Discrepancies between stated itineraries and actual movements are often the story.
