---
description: a pollutant release inventory report, environmental assessment, spill record, inspection report, compliance order, or similar environmental regulatory document
---
# Domain knowledge — Environmental filings

This skill is loaded by Watchdog when the document type is a pollutant release inventory report, environmental assessment, spill record, inspection report, compliance order, or similar environmental regulatory document.

---

## Document types covered

- Pollutant release and transfer register (PRTR) reports — national inventories of industrial emissions
- Environmental assessment (EA) / environmental impact assessment (EIA) applications and decisions
- Spill notifications and occurrence reports
- Compliance and enforcement orders
- Administrative monetary penalties (AMPs) and fines
- Inspection reports and audit findings
- Environmental protection orders
- Remediation and cleanup orders
- Environmental monitoring reports
- Operating permits and environmental compliance approvals
- In Canada: National Pollutant Release Inventory (NPRI) reports; Impact Assessment Act (IAA) decisions; provincial environmental assessment decisions; Environmental Compliance Approvals (ECAs)
- In the US: EPA Toxic Release Inventory (TRI) reports; NEPA environmental impact statements (EIS)
- In the EU: E-PRTR (European Pollutant Release and Transfer Register); Industrial Emissions Portal Regulation (IEPR); Environmental Impact Assessment Directive decisions
- In Australia: National Pollutant Inventory (NPI) reports; state EPA compliance records

---

## Fields to extract

| Field | What to look for |
|-------|-----------------|
| **Facility or site name** | Legal name and common name of the facility |
| **Operator / owner** | Company responsible for the facility |
| **Location** | Address, geographic coordinates, nearest community |
| **Reporting year or period** | The period covered by the report |
| **Pollutants / substances** | Specific chemicals or substances released or stored |
| **Quantities released** | Amount released to air, water, and land (in tonnes or kg) |
| **Disposal method** | How waste or emissions are managed |
| **Permit or approval number** | The regulatory authorization under which the facility operates |
| **Exceedances** | Whether the facility exceeded any permit limits |
| **Enforcement action type** | Type of order or penalty issued |
| **Penalty amount** | Dollar value of any fine or administrative penalty |
| **Compliance deadline** | When the facility must achieve compliance |

---

## Red flags — what to look for

### Emissions and releases

- **Year-over-year increase in releases** — a facility releasing significantly more of a regulated substance than in prior years warrants explanation.
- **Releases near sensitive receptors** — a facility releasing substances to air or water near schools, hospitals, residential areas, Indigenous or marginalized communities, or source water intakes.
- **Priority substance used but not in the release report** — if the document identifies use of a carcinogen or priority substance above threshold quantities, note whether that substance also appears in the facility's PRTR/TRI report for the same year; if it cannot be confirmed from the document, log a lead to check the national register.
- **Carcinogen or endocrine disruptor releases** — note any substances the document itself names as carcinogens, endocrine disruptors, or priority substances; log a lead to research the health classification of the other substances released.
- **Stack emissions vs. fugitive emissions** — stack emissions (from a defined point) are more readily captured and controlled; fugitive emissions (leaks, evaporation) are harder to measure and often underreported.

### Spills and incidents

- **Spill to a watercourse or source water area** — a spill reaching surface water, groundwater, or a municipal water supply is a higher-severity event.
- **Spill notification delay** — facilities are required to report spills immediately (or within a defined timeframe). A spill discovered in an inspection that was not self-reported is a compliance failure.
- **Repeat spills at the same facility** — if this document (an inspection report or summary) itself lists multiple spill events at the facility, capture them as a pattern rather than a one-off incident.
- **Volume underestimation** — initial spill reports may understate the volume released; compare early notification records to final reports.

### Environmental assessments

- **Project scope near an assessment threshold** — capture the stated project scope and any thresholds that trigger an environmental assessment. Note where the project appears split into sub-projects each falling below a threshold, or otherwise sized just under a listed trigger (record the observation; do not infer intent).
- **Baseline data gaps** — an EIA that lacks baseline data on species at risk, wetlands, or groundwater before construction is a methodological weakness that may lead to missed impacts.
- **Public participation period shortened** — a comment period significantly shorter than the standard for a given jurisdiction may limit meaningful public participation.
- **Cumulative effects not assessed** — an EIA that considers only the direct effects of the proposed project, without assessing cumulative effects with other existing or planned projects in the same area, is incomplete under most legislative frameworks.

### Compliance and enforcement

- **Penalty below the maximum** — if the document states the statutory maximum penalty, capture it alongside the penalty actually imposed and note the gap; if the maximum is not stated, log a lead to check it against the governing statute.
- **Penalty paid vs. penalty outstanding** — some penalties are appealed or go unpaid. Check whether a penalty was actually collected.
- **Company with multiple facilities in non-compliance** — if the document covers a multi-facility operator, note the compliance status at each site it names.

---

## Jurisdiction terminology

| Term | Jurisdiction | Meaning |
|------|-------------|---------|
| **NPRI** | Canada | National Pollutant Release Inventory — Canada's annual public inventory of pollutant releases from industrial facilities |
| **TRI** | US | Toxic Release Inventory — the US equivalent of the NPRI, administered by the EPA |
| **E-PRTR** | EU | European Pollutant Release and Transfer Register |
| **NPI** | Australia | National Pollutant Inventory |
| **PRTR** | Universal | Pollutant Release and Transfer Register — the generic term for national emissions inventories |
| **IAA / IAAC** | Canada | Impact Assessment Act / Impact Assessment Agency of Canada — the federal EA regime since 2019 |
| **CEAA** | Canada | Canadian Environmental Assessment Act — the predecessor federal EA legislation |
| **ECA** | Canada (Ontario) | Environmental Compliance Approval — a permit authorizing a facility to operate with specific emissions conditions |
| **AMP** | Universal | Administrative Monetary Penalty — a fine issued without court proceedings |
| **EIS** | US | Environmental Impact Statement — the detailed document required under the US National Environmental Policy Act (NEPA) |
| **CEPA** | Canada | Canadian Environmental Protection Act — federal law governing toxic substances and pollution prevention |
| **Duty to consult** | Canada / Australia / others | The state's constitutional or legal obligation to consult Indigenous peoples when decisions may adversely affect their rights |
| **Species at risk** | Universal | Species listed under national endangered species legislation — a project affecting listed species or their critical habitat requires consultation and mitigation |

---

## Relationships to extract from environmental filings

1. **Company → Facility**: Operator/owner of the site
2. **Facility → Substance**: Releases (with quantity, medium — air/water/land — and year)
3. **Facility → Regulator**: Compliance orders, penalties, approvals (with permit numbers and conditions)
4. **Facility → Community**: Nearest community, proximity to sensitive receptors
5. **Project → Assessment decision**: EA decision (approved/rejected/referred to review panel) with conditions

---

## What investigators typically miss

1. **The national pollutant database** — PRTR/NPRI/TRI data is available online and can be searched by facility, company, substance, and location. The database allows year-over-year comparison; the individual report is only one year of a longer story.
2. **Approved vs. actual emissions** — a facility's permit sets limits; the PRTR shows what was actually released. A facility may be below permit limits but still releasing large absolute quantities.
3. **Spill records in provincial or state registries** — most jurisdictions maintain public spill notification databases. These are often more granular than annual PRTR data.
4. **EA conditions and their monitoring** — an approved project comes with conditions. Whether those conditions are being monitored and enforced — and whether violations have occurred — requires a separate access request or database search.
5. **Voluntary reduction commitments** — a facility may have made voluntary commitments to reduce emissions as part of a settlement or an EA condition. Compare stated commitments to actual PRTR data.
6. **Corporate ownership chain** — environmental liability follows the land and the operator. A company that sold a contaminated facility to a subsidiary before declaring bankruptcy may have left the cleanup obligation with a shell. Check the ownership history.
7. **Compliance history that spans multiple documents** — a single order or report shows one moment. A facility that has received multiple compliance orders or penalties over years without achieving compliance, a spill history that only emerges across separate notifications, or a corporate parent whose non-compliance is spread across several facilities are all patterns visible only when the individual documents are assembled and compared.

---

## Sources and further reading

### Official and regulatory
- [National Pollutant Release Inventory data search (Canada)](https://pollution-waste.canada.ca/national-release-inventory/) — searchable database of pollutant releases to air, water, and land from Canadian industrial facilities, going back to 1993; filterable by facility, substance, province, and sector
- [About the NPRI — Canada.ca](https://www.canada.ca/en/environment-climate-change/services/national-pollutant-release-inventory/about-national-pollutant-release-inventory.html) — overview of Canada's national pollutant inventory program, reporting thresholds, and covered substances
- [EPA Enforcement and Compliance History Online (ECHO)](https://echo.epa.gov/) — US database covering compliance and enforcement records for more than 1.5 million regulated facilities under the Clean Air Act, Clean Water Act, RCRA, and Safe Drinking Water Act
- [EPA Toxics Release Inventory (TRI) Program](https://www.epa.gov/toxics-release-inventory-tri-program) — US equivalent of the NPRI; annual reports from industrial and federal facilities on toxic chemical releases; searchable back to 1987
- [ECCC Environmental Enforcement Notifications — Canada.ca](https://www.canada.ca/en/environment-climate-change/services/environmental-enforcement/notifications.html) — database of successful federal environmental prosecutions in Canada, including fines issued under CEPA and the Fisheries Act

### Practitioner and public interest
- [GIJN: Environmental Investigative Reporting — Resources and Methods](https://gijn.org/stories/environmental-investigative-reporting-resources-methods/) — GIJN guide covering data sources, tools, and methods for environmental investigations, including pollutant databases and remote sensing resources
- [UNEP: Compliance Mechanisms Under Selected Multilateral Environmental Agreements](https://www.unep.org/resources/report/compliance-mechanisms-under-selected-multilateral-environmental-agreements) — UNEP comparative analysis of compliance frameworks across 19 major global environmental treaties; useful context for cross-border investigations
