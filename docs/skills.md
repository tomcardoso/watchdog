# Skills

Watchdog ships with domain knowledge for 33 document types — corporate filings, court documents, land registries, campaign finance returns, and more. This page explains what a skill is, how documents get matched to one, the full catalogue, and how to add your own. Read it if you want to know what Watchdog actually knows about your documents.

## What a skill is

A skill is a plain markdown file that encodes what an experienced investigative journalist knows about one document type: what fields are always present, what patterns are anomalous, what terminology means, and what investigators typically miss. It is the difference between a first-year reporter reading a bankruptcy filing and a twenty-year veteran reading the same pages — the veteran notices the related-party transfer buried in the creditor list.

When Watchdog extracts a document, it loads the matching skill first. The model enters the document already primed with the right red flags, so a sole-source justification or a director change three weeks before a contract award gets flagged rather than passed over.

## How a document gets its skill

Each document's type is identified at ingest time by a quick classification step: a small, cheap model reads the document's opening pages and picks the closest-matching skill from the catalogue. Documents that match no specific skill fall back to [general records](../src/watchdog/skills/records/general-records.md), a universal framework for orienting yourself in any unfamiliar record. You can skip classification entirely by pinning a skill — see [reading and pinning skills](#reading-and-pinning-skills) below.

## The catalogue

Skills are jurisdiction-agnostic by default: universal principles come first, with specific jurisdictions (Canada, US, UK, Australia, EU) treated as examples, not defaults.

### Financial and corporate

| Skill | Covers |
|-------|--------|
| [`corporate-filings`](../src/watchdog/skills/records/corporate-filings.md) | Registrations, director filings, shareholder registers, beneficial ownership |
| [`financial-statements`](../src/watchdog/skills/records/financial-statements.md) | Audited statements, annual reports (10-K/10-Q), MD&A, auditor opinions, related-party disclosures |
| [`regulatory-filings`](../src/watchdog/skills/records/regulatory-filings.md) | Securities disclosures, insider trading reports, SEDAR+/EDGAR filings |
| [`bankruptcy`](../src/watchdog/skills/records/bankruptcy.md) | Bankruptcy filings, creditor lists, trustee reports, restructuring proceedings |
| [`insurance-filings`](../src/watchdog/skills/records/insurance-filings.md) | Regulatory returns, actuarial reports, reinsurance treaties, market conduct reviews |
| [`tax-documents`](../src/watchdog/skills/records/tax-documents.md) | Charity information returns (T3010, Form 990), nonprofit filings, trust returns |

### Legal and regulatory

| Skill | Covers |
|-------|--------|
| [`court-documents`](../src/watchdog/skills/records/court-documents.md) | Civil claims, affidavits, judgments, orders, injunctions |
| [`criminal-proceedings`](../src/watchdog/skills/records/criminal-proceedings.md) | Charging documents, bail decisions, trial decisions, sentencing, forfeiture orders |
| [`administrative-tribunals`](../src/watchdog/skills/records/administrative-tribunals.md) | Quasi-judicial bodies: housing and services human rights, competition, privacy, utility regulation |
| [`labour-arbitration`](../src/watchdog/skills/records/labour-arbitration.md) | Grievance awards, labour board decisions, unfair labour practices, collective agreements |
| [`immigration-refugee`](../src/watchdog/skills/records/immigration-refugee.md) | Asylum decisions, detention reviews, deportation orders, judicial reviews |
| [`healthcare-licensing`](../src/watchdog/skills/records/healthcare-licensing.md) | Discipline decisions, fitness to practise, facility inspections (medicine, nursing, pharmacy) |
| [`professional-licensing`](../src/watchdog/skills/records/professional-licensing.md) | Discipline decisions for lawyers, accountants, engineers, financial advisers, real estate agents |
| [`legislation`](../src/watchdog/skills/records/legislation.md) | Statutes, regulations, orders-in-council, bills, policy directives |

### Government and public records

| Skill | Covers |
|-------|--------|
| [`government-contracts`](../src/watchdog/skills/records/government-contracts.md) | The full procurement lifecycle: RFPs, bids, sole-source justifications, awards, amendments, vendor performance |
| [`audit-reports`](../src/watchdog/skills/records/audit-reports.md) | Auditor general reports, performance audits, inspector general reports |
| [`government-reports`](../src/watchdog/skills/records/government-reports.md) | Royal commissions, public inquiries, committee reports, white papers, consultations |
| [`foi-responses`](../src/watchdog/skills/records/foi-responses.md) | FOI/ATI response packages, exemption indexes, redaction logs |
| [`legislature-transcripts`](../src/watchdog/skills/records/legislature-transcripts.md) | Hansard, committee transcripts, question period, congressional hearings |
| [`lobbying-records`](../src/watchdog/skills/records/lobbying-records.md) | Lobbyist registrations, communication reports, revolving door disclosures |
| [`election-filings`](../src/watchdog/skills/records/election-filings.md) | Campaign finance returns, donor lists, third-party advertising disclosures |
| [`municipal-records`](../src/watchdog/skills/records/municipal-records.md) | Council minutes, zoning decisions, conflict-of-interest declarations |
| [`police-records`](../src/watchdog/skills/records/police-records.md) | Occurrence reports, use-of-force records, public complaint decisions, coroner's inquests |
| [`corrections-records`](../src/watchdog/skills/records/corrections-records.md) | Parole board decisions, probation orders, prison inspection reports, correctional oversight |
| [`environmental-filings`](../src/watchdog/skills/records/environmental-filings.md) | Pollutant release inventories, environmental assessments, compliance orders |

### Property

| Skill | Covers |
|-------|--------|
| [`real-estate`](../src/watchdog/skills/records/real-estate.md) | Title transfers, mortgages, liens, assessments, land registry and title systems — common law and civil law; caveats, PPSA/RDPRM charges |
| [`vehicle-registrations`](../src/watchdog/skills/records/vehicle-registrations.md) | Motor vehicle and vessel registrations, title transfers, liens, fleet records |

### Specialized

| Skill | Covers |
|-------|--------|
| [`academic-research`](../src/watchdog/skills/records/academic-research.md) | Grant applications, ethics decisions, conflict-of-interest disclosures, retraction notices |
| [`aircraft-logs`](../src/watchdog/skills/records/aircraft-logs.md) | Aircraft registrations, ADS-B flight tracks, safety investigation reports |
| [`dns-whois`](../src/watchdog/skills/records/dns-whois.md) | WHOIS records, DNS data, IP allocation, SSL certificate transparency logs |
| [`news-clippings`](../src/watchdog/skills/records/news-clippings.md) | News articles, press releases, wire stories, corrections, retractions |
| [`audio-video`](../src/watchdog/skills/records/audio-video.md) | YouTube transcripts, podcast transcripts, earnings calls, press conference recordings |
| [`websites-html`](../src/watchdog/skills/records/websites-html.md) | Any HTML file or downloaded website page — both its presentation content and its underlying markup (tracking IDs, hidden elements, outbound links) |

## Reading and pinning skills

`watchdog show-skills` lists every skill in the catalogue and opens the skills folder on GitHub; `watchdog show-skills <name>` prints one skill in full so you can see exactly what Watchdog will look for.

If a vault is always one document type — 400 pages of the same filing, say — you can skip per-document classification by pinning a skill: `watchdog dig --skill <name>` for one run (see [Commands](commands.md)), or set `default_skill` to make it permanent (see [Configuration](configuration.md)).

If a batch mixes document types instead, pin each document individually by adding a `skill:` field to its `.yml` sidecar (see [Vault](vault.md#sidecar-files)) — it overrides both `--skill` and `default_skill` for that one document and skips classification for it, so one `dig` run can correctly handle several document types at once.

## Custom skills

You can add your own skills in `~/.watchdog/skills/records/` — plain markdown, no code required. A custom skill overrides a built-in one of the same name, so you can also adapt an existing skill to your beat by copying and editing it.

Start from the template at [`_template.md`](../src/watchdog/skills/records/_template.md), which lays out the standard structure. The one authoring principle that matters most: lead with patterns that apply anywhere, and treat specific jurisdictions as examples rather than the frame. If you want to contribute a skill back to the project, the full authoring guide is in [CLAUDE.md](../CLAUDE.md).

---

**Where next:** [Investigating](investigating.md) to put the extracted knowledge to work, or [Configuration](configuration.md) for the classification and skill settings.
