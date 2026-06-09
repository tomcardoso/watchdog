# Domain knowledge — Municipal records

This skill is loaded by `/ingest` when the document type is a council agenda or minutes, development permit, variance application, zoning amendment, conflict-of-interest disclosure, or other municipal government record.

Apply this knowledge in addition to the standard extraction process. It tells you what to look for, what terminology means, and what patterns are worth flagging.

---

## Document types covered

- Council meeting agendas and minutes
- Planning and development committee decisions
- Site plan approvals and amendments
- Zoning amendment and variance decisions
- Official or local plan amendment applications
- Building permits
- Development permits and variances
- Conflict-of-interest declarations (municipal councillors)
- Integrity commissioner or ethics officer complaints and decisions
- Municipal lobbyist registry records
- Municipal auditor general reports
- Council expense reports and indemnity claims
- Property tax assessment records and appeals
- Municipal contracts and procurement records
- Ombudsman investigation reports
- In Canada (Ontario): Committee of Adjustment decisions; OPA/ZBA applications; LPAT/OLT appeals; MCIA declarations; MFIPPA requests
- In the US: City council minutes; planning commission decisions; board of zoning appeals decisions; city ethics commission records
- In the UK: Planning committee decisions; standards committee decisions; overview and scrutiny committee reports

---

## Always-present fields to extract

| Field | What to look for |
|-------|-----------------|
| **Meeting date** | Date of council or committee meeting |
| **Item number** | The agenda item reference |
| **Ward / district** | Which ward, district, or neighbourhood the matter relates to |
| **Applicant name** | Who is applying for a permit, variance, or approval |
| **Property address** | Civic address and legal description |
| **Application type** | Zoning amendment, variance, site plan, consent, etc. |
| **Recommendation** | Staff recommendation (approve, refuse, defer) |
| **Council vote** | How members voted and who moved/seconded |
| **Decision** | Final outcome (approved, refused, deferred, tabled) |
| **Conflict declared** | Whether any councillor declared a conflict |
| **Councillors absent** | Note absences that affected a vote outcome |

---

## Red flags — what to look for

### Development approvals

- **Variance applications for properties with recent ownership changes** — a property purchased, then immediately subject to a variance or upzoning application, suggests the buyer may have had advance knowledge the application would be approved.
- **Multiple variances for the same property or developer** — variances are supposed to be exceptional; a developer who routinely receives them may be receiving systematic accommodation.
- **Applications approved against staff recommendation** — staff rejection followed by council approval is unusual and worth examining, especially where the development is controversial or in a sensitive area.
- **Conditions of approval waived or not enforced** — a development approved with specific conditions (traffic study, community benefits agreement, affordable unit percentage) where the conditions were later waived or ignored.
- **Councillor recusal pattern** — a councillor who routinely declares conflicts on matters involving a specific developer, employer, or property may have a relationship worth documenting.
- **Applicant is a numbered company or shell entity** — development applications from numbered companies can obscure the real beneficial interest. Check the corporate registry for directors and officers.

### Conflicts of interest

- **Failure to declare** — most jurisdictions require councillors to disclose a pecuniary or financial interest before any council vote where they have one. Failure to declare is the offence; it can result in disqualification from office.
- **Declared conflict but failure to leave the room** — many conflict-of-interest laws require the councillor to leave the meeting, not just abstain from voting. Remaining in the room during deliberations may itself be a violation.
- **Indirect pecuniary interest** — an interest held through a spouse, parent, or dependent child. Councillors sometimes declare conflicts for direct interests but not indirect ones.
- **Pattern of declarations by the same councillor** — a councillor who repeatedly declares conflicts in a particular sector (real estate, construction, waste management) may have pervasive interests in that sector.

### Integrity commissioner complaints

- **Repeat complaints against the same councillor** — a pattern of complaints, even if individually dismissed, is worth noting.
- **Complaint dismissed on procedural grounds** — a complaint dismissed because it was filed late or outside the commissioner's jurisdiction, without a finding on the merits.
- **Complaint settled before a report** — some complaints are resolved informally before the commissioner issues a report. The existence of an informal resolution may not be publicly disclosed.

### Procurement and contracts

- **Sole-source contracts awarded without competitive bidding** — municipal procurement rules typically require competitive bids above specified thresholds. Sole-source awards above those thresholds require documented justification.
- **Contract splitting** — dividing a large contract into smaller pieces each below the tender threshold to avoid competitive bidding requirements.
- **Vendor relationship with a council member or senior official** — a contract awarded to a company with a personal or financial relationship to a decision-maker.

---

## Jurisdiction terminology

| Term | Jurisdiction | Meaning |
|------|-------------|---------|
| **OPA** | Canada (Ontario) | Official Plan Amendment — a change to the municipality's long-term land use plan |
| **ZBA** | Canada (Ontario) | Zoning Bylaw Amendment — a change to the rules governing what can be built on a specific property |
| **Minor variance** | Canada (Ontario) | A small exception to the zoning bylaw granted by the Committee of Adjustment |
| **Committee of Adjustment** | Canada (Ontario) | A quasi-judicial body that hears minor variance and consent applications |
| **OLT / LPAT** | Canada (Ontario) | Ontario Land Tribunal (formerly Local Planning Appeal Tribunal) — hears appeals of municipal planning decisions |
| **MCIA** | Canada (Ontario) | Municipal Conflict of Interest Act — governs when councillors must declare conflicts |
| **MFIPPA** | Canada (Ontario) | Municipal Freedom of Information and Protection of Privacy Act |
| **Integrity Commissioner** | Canada | An independent officer who investigates complaints about councillors' compliance with codes of conduct |
| **Ward** | Universal | A geographic division of a municipality for electoral purposes |
| **Board of Zoning Appeals (BZA)** | US | The US equivalent of the Committee of Adjustment — hears variance applications |
| **Planning commission** | US | The local body that reviews development applications; often advisory to the city council |
| **Standards committee** | UK | The committee that investigates complaints about elected member conduct |
| **Overview and scrutiny committee** | UK | A local government committee that reviews council policy and executive decisions |

---

## Relationships to extract from municipal records

1. **Person → Property**: Owner, applicant, agent (often a planning consultant or lawyer)
2. **Councillor → Vote**: Voted for/against/absent/conflict declared, on which item
3. **Company → Application**: Developer applying for approval
4. **Councillor → Conflict**: Declared conflict (direct or indirect) and the specific item
5. **Person → Complaint**: Integrity commissioner or ethics complaint (complainant and subject)
6. **Vendor → Contract**: Municipal contract (amount, type, procurement method)

---

## What investigators typically miss

1. **The planning consultant and lawyer** — development applications are almost always submitted by a planning consultant or lawyer on behalf of the real applicant. The consultant may appear on dozens of applications before the same committee; their relationship with municipal staff and members is worth examining.
2. **The agent of record vs. the beneficial owner** — the applicant on a development permit may be a numbered company or a trustee. The beneficial owner — the person who will profit from the approval — may be someone else entirely.
3. **Conditions attached to a bylaw** — a council may pass a zoning amendment "in principle" subject to conditions (a holding zone). If the conditions are later lifted without scrutiny, that is a second decision worth examining.
4. **The recused councillor's subsequent vote** — a councillor who declares a conflict on an amendment must also declare a conflict on any subsequent vote on the same matter (e.g. an appeal). Failing to carry the recusal through to related matters is a common violation.
5. **Council meeting audio and video** — most municipalities record council meetings; the audio or video may contain discussions not reflected in the formal minutes.
6. **Heritage designation and demolition permits** — a property under heritage designation that receives a demolition permit may require council approval. The sequence of decisions (heritage designation lifted, demolition permitted, development approved) can reveal political accommodation of a developer.

---

## Sources and further reading

### Official and regulatory

- [Municipal Act, 2001 — ontario.ca/laws](https://www.ontario.ca/laws/statute/01m25) — Ontario's primary statute governing municipalities; sets out councillor conflict-of-interest requirements, procurement rules, and bylaw powers
- [Community Charter, SBC 2003, c. 26 — bclaws.gov.bc.ca](https://www.bclaws.gov.bc.ca/civix/document/id/complete/statreg/03026_00) — BC legislation governing municipal powers, council accountability, and financial management

### Practitioner and public interest

- [AMCTO — Association of Municipal Managers, Clerks and Treasurers of Ontario](https://www.amcto.com) — Professional association for Ontario municipal administrators; publishes guidance on records management, conflict-of-interest procedures, and municipal law
- [OGP Local — Open Government Partnership](https://www.opengovpartnership.org/ogp-local/) — International initiative helping local governments apply open government principles including transparency and accountability
- [Transparency International — Local Government Knowledge Hub](https://knowledgehub.transparency.org/topics/local-government) — Research portal covering corruption risks in local governance, with case studies and anti-corruption resources from jurisdictions worldwide
