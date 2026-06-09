# Domain knowledge — Academic and research documents

This skill is loaded by `/ingest` when the document type is a grant application, research ethics board decision, conflict-of-interest disclosure, retraction notice, research agreement, or similar academic or scientific accountability document.

Apply this knowledge in addition to the standard extraction process. It tells you what to look for, what terminology means, and what patterns are worth flagging.

---

## Document types covered

- Grant applications and funding decisions (national research councils, foundations, industry sponsors)
- Research ethics board (REB/IRB/HREC) approvals and decisions
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

## Always-present fields to extract

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

- **Undisclosed financial relationship with a sponsor** — a researcher who holds equity in, receives consulting fees from, or sits on the board of a company whose products are the subject of their research, without disclosing this in the grant application or publication.
- **Industry sponsor with editorial control** — a research agreement that gives the industry funder the right to review findings before publication, delay publication, or veto publication of negative results.
- **Grant from a foundation funded by the industry** — some industry interests fund apparently independent foundations that in turn fund academic research. The distance between the original funder and the researcher may obscure the conflict.
- **Researcher employed or consulting for regulator and industry simultaneously** — a researcher who provides expert advice to a regulatory body while simultaneously receiving industry funding in the same field.
- **Institutional conflicts of interest** — the university itself has a financial stake in the research outcome through equity, royalties, or a commercial partnership. This is separate from the individual researcher's conflict.

### Research misconduct and integrity

- **Retraction notices** — a retracted paper is not just a scientific error; it may indicate fabrication, falsification, or plagiarism. Note the reason given for retraction (which is often vague) and whether the authors agreed.
- **Corrigendum (correction)** — a correction to a published paper. Multiple corrections to the same paper, or corrections involving data or conclusions, are more significant than typographical corrections.
- **Duplicated data or image manipulation** — reported by watchdog sites like Retraction Watch or Pubpeer. Cross-reference any retraction with these sources.
- **Authorship disputes** — a researcher named as an author who disputes authorship, or a researcher who should have been named but was not, may indicate attribution manipulation.
- **Prior misconduct findings not disclosed** — a researcher with a prior misconduct finding at another institution who did not disclose it when applying for a grant or position at a new institution.

### Grant accountability

- **Progress reports not filed** — agencies typically require annual progress reports. Missing reports may indicate the project stalled or the PI avoided accountability.
- **Grant funds used for purposes other than stated** — a use-of-funds report showing expenditures inconsistent with the approved budget and research plan.
- **Transfer of grant to a company** — a researcher transferring grant-funded IP or activities to a startup they control without proper disclosure or technology transfer agreement.

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
3. **Person → Company**: Consulting relationship, equity holding, board membership (conflict disclosure)
4. **Company → Grant**: Industry co-funder or in-kind contributor (with type and value of contribution)
5. **Institution → Company**: Technology transfer, licensing, or equity arrangement
6. **Paper → Retraction**: Retracted publication (with date, journal, and stated reason)

---

## What investigators typically miss

1. **The acknowledgements section of published papers** — researchers are required to acknowledge all funders. The acknowledgements in peer-reviewed papers often reveal industry funding not disclosed in the grant application.
2. **Partnership grant categories** — many funding agencies have grant categories that explicitly involve industry co-funding. The identity of the industry partner and the terms of the partnership are public in the grant abstracts database.
3. **The ethics approval does not mean the research was ethical** — an ethics board approval means the board reviewed and approved the protocol. It does not guarantee that the research was conducted as described, that data was accurately reported, or that findings were not selectively published.
4. **Technology transfer agreements** — when a university licenses research findings to a company or takes equity in a startup formed around research, the terms of those agreements may be accessible through access to information. The university's financial interest in a company affects its independence from that company.
5. **Conflicts in systematic reviews and clinical guidelines** — researchers with industry ties who participate in systematic reviews or clinical guideline panels can shape the evidence base used to make healthcare decisions. Check the COI disclosures in published guidelines.
6. **Retraction Watch and PubPeer** — these community watchdog sites flag papers with potential integrity issues before formal retraction. A paper flagged on PubPeer for data manipulation is newsworthy even before the journal acts.

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
