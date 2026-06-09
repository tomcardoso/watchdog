# Domain knowledge — Labour and arbitration records

This skill is loaded by `/ingest` when the document type is a grievance arbitration award, labour board decision, collective agreement, interest arbitration award, unfair labour practice ruling, or similar labour relations document.

Apply this knowledge in addition to the standard extraction process. It tells you what to look for, what terminology means, and what patterns are worth flagging.

---

## Document types covered

- Grievance arbitration awards
- Interest arbitration awards
- Expedited arbitration decisions
- Labour board / industrial relations commission decisions
- Union certification applications and decisions
- Unfair labour practice (ULP) complaints and decisions
- First contract arbitration decisions
- Essential services agreements and designations
- Collective agreements (contracts)
- Human rights tribunal decisions (employment)
- Pay equity hearings and orders
- In Canada: Ontario Labour Relations Board (OLRB) decisions; Canada Industrial Relations Board (CIRB) decisions; Employment Insurance umpire decisions
- In the US: National Labor Relations Board (NLRB) decisions; American Arbitration Association (AAA) awards
- In the UK: Employment Tribunal decisions; Advisory, Conciliation and Arbitration Service (ACAS) arbitration awards
- In Australia: Fair Work Commission decisions and arbitration awards

---

## Always-present fields to extract

| Field | What to look for |
|-------|-----------------|
| **Employer** | Legal name of the employer |
| **Union** | Name of the union and local number |
| **Arbitrator(s) / board member(s)** | Who decided the matter |
| **Decision date** | When the award or decision was issued |
| **Grievance / file number** | The reference number |
| **Issue / subject** | What the grievance or dispute is about |
| **Decision / outcome** | Grievance allowed, dismissed, resolved; certification granted/denied; ULP found/not found |
| **Remedy** | What the arbitrator or board ordered |
| **Retroactive period** | How far back any remedy applies |
| **Bargaining unit description** | The scope of the employee group covered |
| **Collective agreement articles cited** | Which provisions were at issue |

---

## Red flags — what to look for

### Grievance arbitration

- **Discipline or discharge grievances** — arbitration of discipline or termination cases often involves detailed findings about workplace misconduct. The arbitrator's factual findings are credibility-tested accounts of what occurred.
- **Serial grievances by the same employee against the same employer** — a pattern of grievances filed by the same individual may indicate a targeted workplace conflict or a systemic management problem.
- **Discipline grievances involving safety** — a worker disciplined for refusing unsafe work (a protected right under occupational health and safety legislation in most jurisdictions) is a significant matter.
- **Settlement that reinstates a worker dismissed for cause** — a settlement where a worker dismissed for serious misconduct is reinstated may indicate the employer's case was weak or the process was flawed.
- **Arbitration of human rights-related issues** — a grievance involving discrimination, harassment, or accommodation often contains detailed factual findings about the workplace. The human rights dimension is worth flagging even when the grievance is framed as a contract dispute.

### Labour board proceedings

- **Unfair labour practice findings against an employer during an organizing campaign** — employers are prohibited from interfering with employees' rights to organize in most jurisdictions. A ULP finding during a certification campaign is significant.
- **Card-based vs. vote-based certification** — in card-based jurisdictions a union can be certified without a vote if it demonstrates majority support through signed membership cards. Evidence of employer interference with the card process is a ULP.
- **Decertification applications** — a union may be decertified if the majority of workers no longer want it. The timing of a decertification application relative to a contract expiry or labour dispute can reveal whether it was employer-assisted.
- **Raiding** — one union attempting to displace another during a permitted raiding period. The outcome of raiding applications reveals which unions have worker support.

### Collective agreements

- **Management rights clause** — the scope of the management rights clause determines what the employer can do unilaterally versus what must be bargained. A very broad management rights clause in a first contract may have been imposed through interest arbitration.
- **Contracting out clause** — whether the employer is prohibited from contracting out bargaining unit work, and under what conditions. Contracting out disputes are a major source of arbitration.
- **Workplace safety provisions** — joint health and safety committee rights, inspection rights, and the right to refuse unsafe work as specified in the agreement.
- **Discipline and discharge provisions** — the standard of "just cause" for discipline is usually implied or stated. Note whether the agreement specifies progressive discipline requirements.

---

## Jurisdiction terminology

| Term | Jurisdiction | Meaning |
|------|-------------|---------|
| **Grievance** | Universal | A formal complaint that the collective agreement has been violated |
| **Arbitration** | Universal | The binding process for resolving grievances; the arbitrator's award is final and enforceable |
| **Interest arbitration** | Universal | Arbitration to set the terms of a new collective agreement when the parties cannot reach one through bargaining |
| **OLRB** | Canada (Ontario) | Ontario Labour Relations Board |
| **CIRB** | Canada | Canada Industrial Relations Board — the federal labour board |
| **NLRB** | US | National Labor Relations Board — the US federal labour board |
| **Fair Work Commission** | Australia | The national workplace relations tribunal |
| **Employment Tribunal** | UK | The UK tribunal that hears employment disputes |
| **ACAS** | UK | Advisory, Conciliation and Arbitration Service — provides dispute resolution services |
| **Certification** | Universal | The formal recognition of a union as the bargaining agent for a group of employees |
| **Bargaining unit** | Universal | The defined group of employees covered by a collective agreement |
| **Just cause** | Universal | The standard for discipline and discharge in most collective agreements |
| **Seniority** | Universal | A worker's standing based on length of service |
| **ULP** | Universal | Unfair labour practice — an employer or union action that violates the labour relations statute |
| **Duty of fair representation (DFR)** | Universal | The union's legal obligation to represent all bargaining unit members fairly |
| **Essential services** | Universal | Services that must be maintained during a strike; workers in essential services may be designated |
| **Canada Labour Code** | Canada | The federal statute governing labour relations in federally regulated industries (banking, telecom, airlines, interprovincial transport) |

---

## Relationships to extract from labour records

1. **Union → Employer**: Bargaining relationship (certified unit, collective agreement dates)
2. **Union → Grievor**: Who filed the grievance (may be the union on behalf of a member, or the union itself)
3. **Arbitrator → Award**: Who decided the case and the outcome
4. **Employer → Remedy**: What the employer was ordered to do (reinstatement, back pay, cease and desist)
5. **Labour board → Union/Employer**: Certification, decertification, or ULP decision

---

## What investigators typically miss

1. **The factual findings in a discharge arbitration** — an arbitration award on a dismissal for cause contains detailed, credibility-tested findings about what happened. These findings are public and often contain significant information about workplace misconduct.
2. **The remedy as a signal** — an arbitrator who reinstates a worker with full back pay versus one who reinstates with a reduced remedy versus one who upholds the dismissal are sending very different signals about the employer's conduct.
3. **Employment tribunal / EI appeal decisions** — in many jurisdictions, decisions on employment insurance or unemployment benefit claims contain detailed accounts of how claimants were treated by employers at termination, including whether departures were voluntary or involuntary.
4. **Pattern of discipline at the same workplace** — a series of grievances all from the same workplace, involving similar discipline issues, may reveal a systemic management problem or retaliatory environment.
5. **The duty of fair representation complaint** — a DFR complaint against a union may reveal that the union failed to properly represent a member, often in a discharge arbitration. These are decided by the labour board and are public.
6. **Pay equity hearings** — pay equity is a proactive obligation in several jurisdictions. Pay equity complaints and orders reveal systemic wage discrimination and can involve large retroactive liabilities.

---

## Sources and further reading

### Official and regulatory

- [CanLII — Canadian Legal Information Institute](https://www.canlii.org/) — Free searchable database of Canadian court and tribunal decisions, including labour board and arbitration decisions from all provinces and federally. Published within two business days of issuance.
- [Canada Industrial Relations Board — Decisions](https://cirb-ccri.gc.ca/en/decisions/decisions) — Official CIRB decision database covering Canada Labour Code (Parts I, II, III) decisions since 1999, Status of the Artist Act decisions since 2013, and occupational health and safety tribunal decisions.
- [Ontario Labour Relations Board — Decisions](https://www.olrb.gov.on.ca/Decisions-EN.asp) — OLRB decisions are published via CanLII; this page explains access and directs to the Ontario Workplace Tribunals Library for decisions back to 1944.
- [BC Labour Relations Board — Decisions](https://www.lrb.bc.ca/decisions) — Searchable database of LRB decisions from 1990 to present, with weekly email notification available; also provides guidance on accessing arbitration awards separately.
- [National Labor Relations Board — Cases and Decisions](https://www.nlrb.gov/cases-decisions) — US federal labour board decisions, including ALJ rulings, Board decisions, and weekly summaries; also provides case search and regional election decisions.
- [Fair Work Commission — Major Cases](https://www.fwc.gov.au/hearings-decisions/major-cases) — Australian national workplace tribunal decisions including annual wage reviews, enterprise agreement disputes, and arbitration awards; organized by case type.
- [ACAS — Arbitration](https://www.acas.org.uk/arbitration) — UK Advisory, Conciliation and Arbitration Service description of collective and individual arbitration schemes, including scope (unfair dismissal, flexible working) and how awards are issued.

### Practitioner and public interest

- [ILO NATLEX — Database of National Labour Legislation](https://www.ilo.org/resource/natlex-database-national-labour-social-security-and-related-human-rights) — ILO database of national labour, social security, and human rights legislation from over 190 countries; useful for comparing the statutory framework behind labour boards and arbitration systems across jurisdictions.

### Journalism resources

- [CanLII — Federal Labour Decisions (Canada)](https://www.canlii.org/en/ca/) — Entry point for CIRB, Canadian Human Rights Tribunal employment decisions, and Pay Equity Hearings Tribunal decisions at the federal level.

### Notes on unsourced claims

The claim that pay equity orders can carry retroactive liability to 1988 in Ontario is supported by multiple employment law firm analyses citing Pay Equity Hearings Tribunal decisions, though the tribunal's own decisions are the primary source and are accessible via CanLII. The observation that decertification timing relative to contract expiry can reveal employer assistance reflects established labour board jurisprudence rather than a single citable study.
