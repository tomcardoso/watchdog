---
description: a grant application, research ethics board decision, conflict-of-interest disclosure, retraction notice, research agreement, or similar academic or scientific accountability document
---
# Domain knowledge — Academic and research documents

This skill is loaded by Watchdog when the document type is a grant application, research ethics board decision, research proposal, study, dissertation, conflict-of-interest disclosure, retraction notice, research agreement, or similar academic or scientific document.

---

## Document types covered

- Grant applications and funding decisions (national research councils, foundations, industry sponsors)
- Research ethics board (REB/IRB/HREC) approvals and decisions
- Research proposal
- Dissertation, study or academic journal article
- Conflict-of-interest disclosure forms
- Industry-sponsored research agreements
- Technology transfer agreements and licensing deals
- Research misconduct investigation reports
- Retraction notices and corrigenda
- Grant accountability and progress reports
- University financial statements and endowment reports
- Academic discipline and investigation letters
- In Canada: Tri-Agency (NSERC, SSHRC, CIHR) applications; Canada Research Chair nominations; CFI applications
- In the US: NIH and NSF grant applications (available via FOIA)
- In the UK: UKRI, Wellcome Trust, and Medical Research Council grants
- In Australia: ARC and NHMRC grant applications

---

## Fields to extract

| Field | What to look for |
|-------|-----------------|
| **Principal investigator (PI)** | The lead researcher on the grant or study |
| **Co-investigators** | Other named researchers |
| **Institution** | The university or research institute hosting the grant |
| **Funding agency** | The granting body (government council, foundation, industry sponsor) |
| **Grant title** | The stated research objective |
| **Grant amount** | Total funding requested or awarded |
| **Grant period** | Start and end dates of the funding |
| **Application reference number** | The agency's identifier |
| **Decision** | Funded, not funded, deferred, conditionally funded |
| **Industry partners** | Companies providing funding, in-kind contributions, or access |
| **COI disclosure** | Disclosed conflicts of interest (financial, personal, professional) |
| **Ethics file number** | For ethics approvals: the ethics board's identifier |
| **Ethics decision** | Approved, approved with conditions, refused |

---

## Red flags — what to look for

### Conflicts of interest

- **Financial tie between a researcher and the subject of their research** — a COI disclosure, acknowledgements section, or grant application naming a company the researcher holds equity in, consults for, or sits on the board of, where that company's products are the subject of the research. The stated tie is the extractable fact. Whether it was disclosed in the right place — named in the acknowledgements but absent from the grant's COI form, or the reverse — is a cross-reference a human confirms; log that gap as a lead rather than asserting non-disclosure.
- **Industry sponsor with editorial control** — a research agreement that gives the industry funder the right to review findings before publication, delay publication, or veto publication of negative results.
- **Grant from a foundation funded by the industry** — some industry interests fund apparently independent foundations that in turn fund academic research, and the distance between the original funder and the researcher may obscure the conflict. Note any grant routed through a third-party foundation and log a lead to trace the ultimate funding source.
- **Researcher employed or consulting for regulator and industry simultaneously** — a researcher who provides expert advice to a regulatory body while simultaneously receiving industry funding in the same field.
- **Institutional conflicts of interest** — the university itself has a financial stake in the research outcome through equity, royalties, or a commercial partnership. This is separate from the individual researcher's conflict.

### Research misconduct and integrity

- **Retraction notices** — a retracted paper is not just a scientific error; it may indicate fabrication, falsification, or plagiarism. Note the reason given for retraction and whether the authors agreed.
- **Corrigendum (correction)** — a correction to a published paper. Multiple corrections to the same paper, or corrections involving data or conclusions, are more significant than typographical corrections.
- **Duplicated data or image manipulation** — note any corrections, retractions, or data- or image-integrity concerns stated in the document itself, and log a lead to check the paper against PubPeer and Retraction Watch.
- **Authorship changes in the record** — authorship disputes rarely play out publicly, but they leave documentary traces: a corrigendum that adds or removes an author, a retraction an author declined to sign, or a formal *letter of disassociation* (a separate document in which a researcher publicly disowns a paper bearing their name). Record the change and who initiated it; the bare fact that a paper carried a contested author is usually resolved before publication and rarely documented — the change to the record is the reportable event.
- **Prior misconduct finding surfacing in the record** — a document that references a researcher's earlier misconduct finding: a debarment, a retraction for cause, or an institutional integrity ruling. Record the finding as stated. If the entity digest already carries such a finding and a new application or bio makes no mention of it, that gap is a lead for a human to check — not something the extractor should assert as concealment.
- **Deviation from the approved protocol** — research, social science especially, seldom unfolds exactly as the grant application or ethics protocol described. Note deviations from the approved ethics protocol or pre-registered plan that the authors themselves describe or acknowledge in the methods or limitations — a methods or limitations section describing a procedure the approved protocol did not cover, or outcomes changed from those pre-registered.

### Grant accountability

- **Progress report showing a stalled or off-track project** — a progress or final report that acknowledges missed milestones, unspent or reallocated funds, a no-cost extension request, or objectives quietly dropped. These are stated in the report itself. (Whether required reports are missing altogether is an absence the extractor can't see — a human checks that against the agency's filing record; see *What investigators typically miss*.)
- **Grant funds used for purposes other than stated** — a use-of-funds report showing expenditures inconsistent with the approved budget and research plan.
- **Grant-funded IP moving to a company the researcher controls** — you will rarely see the improper transfer itself; look for the documentary signals inside these records instead. A COI disclosure naming a startup the PI founded or holds equity in; a progress or accountability report describing commercialization of grant-funded work; or a technology-transfer or licensing agreement whose terms move publicly funded IP to a private company on soft terms. The tension between the grant's public-interest purpose and the private benefit, visible where these documents overlap, may be worth a closer look.

---

## Jurisdiction terminology

| Term | Jurisdiction | Meaning |
|------|-------------|---------|
| **NSERC / SSHRC / CIHR** | Canada | The three federal granting councils (natural sciences, social sciences, health research) — collectively called "Tri-Agency" |
| **CRC** | Canada | Canada Research Chair — a federally funded research position at a university |
| **CFI** | Canada | Canada Foundation for Innovation — funds research infrastructure |
| **REB** | Canada | Research Ethics Board — the institutional committee that reviews research involving human participants |
| **TCPS2** | Canada | Tri-Council Policy Statement on Ethical Conduct for Research Involving Humans |
| **NIH / NSF** | US | National Institutes of Health / National Science Foundation — the major US federal funding agencies |
| **IRB** | US | Institutional Review Board — the US equivalent of a REB |
| **UKRI** | UK | UK Research and Innovation — the umbrella body for UK research councils |
| **ARC / NHMRC** | Australia | Australian Research Council / National Health and Medical Research Council |
| **Technology transfer office (TTO)** | Universal | The university office that commercializes research findings (licensing, startup formation) |
| **Arm's length** | Universal | An industry partner or funder without a direct financial relationship to the researcher or institution |
| **Knowledge mobilization** | Universal | The process of translating research findings into practice |

---

## Relationships to extract from academic records

1. **Person → Institution**: Researcher affiliated with university or institute
2. **Person → Grant**: PI or co-investigator on a funded grant (with amount and agency)
3. **Person → Paper**: Author or co-author (with any subsequent authorship change noted)
4. **Person → Company**: Consulting relationship, equity holding, board membership (conflict disclosure)
5. **Company → Grant**: Industry co-funder or in-kind contributor (with type and value of contribution)
6. **Institution → Company**: Technology transfer, licensing, or equity arrangement
7. **Paper → Retraction**: Retracted publication (with date, journal, and stated reason)

---

## What investigators typically miss

1. **The acknowledgements section of published papers** — researchers are typically required to acknowledge all funders. The acknowledgements in peer-reviewed papers may reveal industry funding.
2. **Partnership grant categories** — many funding agencies have grant categories that explicitly involve industry co-funding. The identity of the industry partner and the terms of the partnership are public in the grant abstracts database.
3. **The ethics approval does not mean the research was ethical** — an ethics board approval means the board reviewed and approved the protocol. It does not guarantee that the research was conducted as described, that data was accurately reported, or that findings were not selectively published.
4. **Technology transfer agreements** — when a university licenses research findings to a company or takes equity in a startup formed around research, the terms of those agreements may be accessible through access to information. The university's financial interest in a company affects its independence from that company.
5. **Conflicts in systematic reviews and clinical guidelines** — researchers with industry ties who participate in systematic reviews or clinical guideline panels can shape the evidence base used to make healthcare decisions. Check the COI disclosures in published guidelines.
6. **Retraction Watch and PubPeer** — these community watchdog sites flag papers with potential integrity issues before formal retraction. A paper flagged on PubPeer for data manipulation is newsworthy even before the journal acts.
7. **Missing annual or final reports** — agencies require periodic progress and final reports, but a gap only shows in the funder's filing record, which no single document reveals. A grant that appears in the awards database with no corresponding reports, or a final report years overdue, is worth checking directly with the agency.

---

## Sources and further reading

### Official and regulatory

- [US Office of Research Integrity — ori.hhs.gov](https://ori.hhs.gov) — US federal agency that oversees allegations of research misconduct in government-funded research and publishes findings and sanctions
- [Tri-Agency Framework: Responsible Conduct of Research (2021) — rcr.ethics.gc.ca](https://rcr.ethics.gc.ca/eng/framework-cadre-2021.html) — Canadian federal policy governing research integrity across NSERC, SSHRC, and CIHR grants; defines fabrication, falsification, and plagiarism and the institutional response process
- [ClinicalTrials.gov](https://clinicaltrials.gov) — US National Library of Medicine registry of over 500,000 clinical studies; use to check whether a study was registered, when, and whether results were reported as required

### Practitioner and public interest

- [COPE — Committee on Publication Ethics](https://publicationethics.org) — Global membership body for journal editors and publishers; sets standards for handling misconduct, authorship disputes, and editorial conflicts of interest
- [Retraction Watch](https://retractionwatch.com) — Independent publication that tracks scientific retractions and research misconduct findings; searchable database of retracted papers
- [ORCID](https://orcid.org) — Persistent identifier system for researchers; use to find a researcher's publication history, institutional affiliations, and funding across institutions and name variants
- [AllTrials](https://www.alltrials.net) — Campaign for the registration and full reporting of clinical trials; background on outcome-switching and selective outcome reporting
