# Domain knowledge — Aircraft logs and aviation records

This skill is loaded by `/ingest` when the document type is an aircraft registration, flight log, airworthiness record, ADS-B flight track, safety investigation report, or similar aviation document.

Apply this knowledge in addition to the standard extraction process. It tells you what to look for, what terminology means, and what patterns are worth flagging.

---

## Document types covered

- Aircraft registration database entries (Transport Canada, FAA, EASA, CASA, CAAC, etc.)
- Aircraft maintenance records and technical logs
- ADS-B flight tracking data (FlightAware, FlightRadar24, ADS-B Exchange)
- National aviation safety investigation reports (TSB, NTSB, AAIB, ATSB, etc.)
- Aviation occurrence reporting system records
- Air operator certificate (AOC) records
- Airworthiness directives (ADs) and service bulletins
- Airport landing fee records and flight manifests (where available)
- Private aviation charter records
- Pilot certification records
- In Canada: CADORS records; Civil Aircraft Register; Transport Canada airworthiness records
- In the US: FAA aircraft registration records; FAA Airmen Inquiry records; NTSB accident reports; ASRS confidential reports

---

## Always-present fields to extract

| Field | What to look for |
|-------|-----------------|
| **Registration mark** | The national prefix and alphanumeric identifier (e.g. C-FXXX in Canada, N-number in the US, G- in UK, VH- in Australia) |
| **Aircraft type / model** | Manufacturer, model, and series |
| **Serial number** | The manufacturer's serial number — uniquely identifies the airframe |
| **Owner of record** | Legal registered owner (individual, company, or trust) |
| **Operator** | The entity operating the aircraft (may differ from owner) |
| **Certificate of airworthiness status** | Whether the aircraft is currently airworthy |
| **Date of manufacture** | Age of the airframe |
| **Engine type(s)** | Engine manufacturer, model, and count |
| **Flight date / route** | For flight tracking: departure and arrival airports and times |
| **Altitude and ground speed** | From ADS-B data |
| **Incident type** | Category of occurrence (accident, incident, serious incident) |
| **Injuries / fatalities** | For accidents: persons killed or injured |
| **Probable cause** | Investigative authority's finding of probable cause |

---

## Red flags — what to look for

### Ownership and registration

- **Aircraft registered to a numbered company or trust** — obfuscates the beneficial owner. Cross-reference the registered company in the corporate registry.
- **Registration address that doesn't match the operator** — an aircraft registered at a law firm or accountant's address, or a PO box.
- **Lien on aircraft title** — an aircraft with an unregistered or unpaid lien may be operating under financial stress.
- **Registration lapsed or suspended** — an aircraft being flown without a valid registration is operating illegally.
- **Foreign-registered aircraft used domestically** — an aircraft with another country's registration operated routinely in a given country may be doing so to avoid local oversight, or for tax purposes.
- **Aircraft registered in tax-advantaged jurisdictions** — some aircraft are registered in Delaware, Isle of Man, Cayman Islands, or similar jurisdictions for tax reasons while operating elsewhere.

### Flight patterns (ADS-B tracking)

- **Regular flights between a politician's home city and a location connected to a donor, lobbyist, or business associate** — the flight path is the connection.
- **Flights that land at private airports, airstrips, or FBOs** — private airports don't have the same public records as commercial airports; the flight track may show landings at locations not otherwise documented.
- **Emergency transponder codes visible in ADS-B data** — 7500 (hijack), 7600 (radio failure), 7700 (general emergency).
- **Disappearing from ADS-B coverage** — some aircraft operators block their ADS-B position from aggregators like FlightAware. ADS-B Exchange does not honor these blocks; it is the most complete public ADS-B source.
- **High-frequency flights to a single destination** — a private aircraft making repeated flights to a specific location (a resort, a corporate headquarters, a foreign city) establishes a relationship.
- **Flights at unusual hours** — late-night or early-morning flights by government or corporate aircraft may indicate urgency or a desire for reduced scrutiny.

### Maintenance and airworthiness

- **Outstanding airworthiness directives** — an AD that applies to the aircraft's type and has not been complied with is a safety violation.
- **Maintenance performed by an uncertified organization** — aircraft maintenance must be performed by an approved maintenance organization. Maintenance records referencing uncertified organizations are irregular.
- **Long gap in maintenance log entries** — an aircraft with no log entries for an extended period may have had maintenance performed off-record or may have been flown without required inspections.

### Incidents and accidents

- **Safety investigation reports** — national aviation safety authorities (TSB in Canada, NTSB in the US, AAIB in the UK, ATSB in Australia, BEA in France) assign investigators to significant aviation occurrences. Final reports include factual findings, safety analysis, and safety recommendations.
- **Probable cause findings involving maintenance or design** — a systemic cause (not just pilot error) may implicate the manufacturer, maintenance organization, or operator.
- **Operator's prior safety record** — occurrence reporting systems are public and searchable by operator. An operator with multiple prior incidents is a pattern story.

---

## Terminology

| Term | Meaning |
|------|---------|
| **ADS-B** | Automatic Dependent Surveillance–Broadcast — the transponder system that broadcasts an aircraft's GPS position; the basis of public flight tracking |
| **ADS-B Exchange** | A community-run ADS-B aggregator that does not honor operator blocking requests — the most complete public source |
| **FlightAware / FlightRadar24** | Commercial ADS-B aggregators that honor operator blocking |
| **AOC** | Air Operator Certificate — the certificate required to operate a commercial air service |
| **AMO** | Approved Maintenance Organization — an organization certified to perform aircraft maintenance |
| **AD** | Airworthiness Directive — a mandatory inspection or modification order issued by a regulatory authority |
| **FBO** | Fixed Base Operator — a commercial operation at an airport providing fuel, hangars, and services; often used by private aviation |
| **METAR / NOTAM** | Meteorological and aeronautical notices — the weather and airspace information available at the time of a flight (relevant in accident investigations) |
| **VFR / IFR** | Visual Flight Rules / Instrument Flight Rules — the operating conditions under which a flight is conducted |
| **Squawk code** | A four-digit transponder code that identifies an aircraft to ATC; 7700 signals an emergency |
| **C-registration** | Canadian civil aircraft registration prefix — C-F and C-G |
| **N-number** | US aircraft registration prefix — all US civil aircraft have an N-number |
| **CADORS** | Civil Aviation Daily Occurrence Reporting System — Transport Canada's public database of aviation incidents |
| **TSB** | Transportation Safety Board of Canada — investigates aviation, rail, marine, and pipeline accidents |
| **NTSB** | National Transportation Safety Board — the US equivalent |
| **AAIB** | Air Accidents Investigation Branch — UK aviation safety authority |
| **ATSB** | Australian Transport Safety Bureau |
| **FAA Registry** | The US database of all registered N-number aircraft — publicly searchable at registry.faa.gov |
| **Civil Aircraft Register (Canada)** | Transport Canada's database of all registered Canadian civil aircraft — publicly searchable at tc.canada.ca |

---

## Relationships to extract from aviation records

1. **Aircraft → Owner**: Registered owner (individual or company) with registration dates
2. **Aircraft → Operator**: Operating entity if different from owner
3. **Person → Aircraft**: Pilot certification records
4. **Aircraft → Flight**: Tracked flight (route, date, time, altitude)
5. **Aircraft → Incident**: Safety occurrence record (with date, location, and finding)
6. **Company → Fleet**: All aircraft registered to the same company

---

## What investigators typically miss

1. **The beneficial owner vs. the registered owner** — a trust, LLC, or numbered company holds the registration. The real owner is found by piercing the corporate structure. In the US, the FAA registry sometimes shows a trustee address (Wilmington, DE is a common giveaway for Delaware trust structures).
2. **ADS-B Exchange for unblocked tracking** — commercial flight trackers honor requests by operators to suppress their position data. ADS-B Exchange, run by volunteers, does not. It is the authoritative source for confirming a flight that was blocked on FlightAware.
3. **Historical flight data** — ADS-B Exchange and other databases retain months to years of historical position data. Subpoenaed government aircraft logs can be cross-checked against public ADS-B records.
4. **The maintenance logbook** — for an accident, the maintenance logbook is the most important document after the safety investigation report. It shows the history of the airframe and any prior issues.
5. **Lien and registration history** — the FAA Aircraft Registry includes lien filings and historical registration data. A chain of ownership through multiple shell companies is a red flag for financial irregularity or asset concealment.
6. **Correlation with other records** — an aircraft flight to a destination on a specific date, cross-referenced with a hotel record, calendar entry, or expense report for the same date, creates a corroborated fact. The flight track alone is evidence of opportunity.
