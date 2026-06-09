# Domain knowledge — Government reports

This skill is loaded by `/ingest` when the document type is a government-produced report, departmental evaluation, royal commission report, public inquiry report, task force report, advisory council report, or similar policy document produced by or for a government body.

This is distinct from audit reports (see `audit-reports` skill), which cover auditor general and value-for-money audits specifically.

Apply this knowledge in addition to the standard extraction process. It tells you what to look for, what terminology means, and what patterns are worth flagging.

---

## Document types covered

- Royal commission and public inquiry reports
- Task force and advisory council reports
- Departmental evaluation and program review reports
- Legislative or parliamentary committee reports
- Independent review panel reports
- Commissioner or ombudsman reports
- Coroner's review reports (systemic, not individual cases)
- Departmental results reports and annual plans
- Inspector general reports
- Truth and reconciliation commission reports
- In Canada: Royal Commission reports; Parliamentary committee reports; Senate committee reports; Departmental Results Reports (DRR); Truth and Reconciliation Commission reports and calls to action; National Inquiry into MMIWG reports
- In the US: Congressional committee reports; Presidential commission reports; IG reports tabled to Congress
- In the UK: Public inquiry reports (Grenfell, Infected Blood, Leveson, etc.); Parliamentary select committee reports

---

## Always-present fields to extract

| Field | What to look for |
|-------|-----------------|
| **Report title** | Full title as published |
| **Issuing body** | Which government body, commission, or committee produced it |
| **Author(s)** | Commissioner, chair, or lead author names |
| **Publication date** | When the report was released |
| **Date of mandate** | When the issuing body was established or the review was commissioned |
| **Scope** | What the report was asked to examine |
| **Recommendations** | Formal numbered recommendations |
| **Government response** | Whether a government response was issued, and its content |
| **Calls to action / calls for justice** | Numbered action items |
| **Implementation status** | Whether recommendations have been acted on |
| **Dissenting opinions** | Whether any commissioner or member dissented |

---

## Red flags — what to look for

### Mandate and independence

- **Narrow terms of reference** — a commission or task force whose mandate specifically excludes the most sensitive questions (e.g. "review implementation but not the decision to proceed") may have been designed to avoid accountability.
- **Commissioner with connections to the subject matter** — a review chair or panel member with prior professional or personal ties to the department, company, or individuals being reviewed.
- **Timeline compressed before an election** — a commission struck or a report rushed to publication before an election may be designed for political purposes rather than genuine accountability.
- **Expert advice ignored in the final report** — compare expert testimony or submissions to final conclusions. Findings that contradict the weight of expert evidence warrant scrutiny.

### Recommendations and follow-through

- **Recommendations that repeat prior recommendations** — the same recommendation appears in a prior report (sometimes from years or decades earlier). This shows the problem was identified and not addressed.
- **Government response that accepts recommendations "in principle"** — a response that agrees with the direction of a recommendation without committing to specific action or a timeline.
- **Recommendations with no corresponding action** — track the implementation of specific numbered recommendations over time. The gap between what was recommended and what was done is often the story.
- **Recommendations directed at a body that no longer exists** — a report addressing a program or structure that was subsequently reorganized may provide cover for inaction.

### Findings and evidence

- **Findings that attribute systemic failures to individual error** — a report that blames one person for a problem that appears structural may be shielding the institution.
- **Evidence heard in camera not reflected in the public report** — commissions and inquiries sometimes hear evidence in closed session; the public report may not reflect the full record.
- **Statistics presented without methodology** — a report that cites data (e.g. recidivism rates, cost estimates) without explaining the source or methodology cannot be properly evaluated.
- **Comparisons to peer jurisdictions** — reports often use international comparisons selectively. Check whether the comparison jurisdictions were chosen to support a predetermined conclusion.

### Public inquiries

- **Costs and duration** — a commission that runs significantly over budget or past its projected timeline may indicate scope expansion, institutional resistance to co-operation, or deliberate delay.
- **Witness co-operation** — an inquiry that reports on witness refusals, production order disputes, or claimed privileges over documents is documenting resistance from the subjects under review.
- **Concurrent criminal proceedings** — an inquiry is not a criminal proceeding; witnesses may be compelled to testify but evidence given at an inquiry cannot be used against them criminally in many jurisdictions. The intersection between an inquiry and a parallel police investigation is complex and worth noting.

---

## Jurisdiction terminology

| Term | Jurisdiction | Meaning |
|------|-------------|---------|
| **Royal commission** | Canada / UK / Australia | A formal public inquiry appointed by executive order, with broad powers to compel evidence |
| **Public inquiry** | Universal | A broader term covering formal review processes established under public inquiry legislation |
| **Terms of reference** | Universal | The mandate given to a commission or task force — defines what it can and cannot examine |
| **Order in Council** | Canada / UK | A Cabinet order — the instrument by which a royal commission or government appointment is made |
| **Treasury Board** | Canada | The Cabinet committee responsible for government spending and program management |
| **Departmental evaluation** | Canada | A mandatory periodic review of a program's relevance, effectiveness, and efficiency |
| **Departmental Results Report (DRR)** | Canada | An annual public accountability document showing how a department performed against its plans |
| **TRC** | Canada | Truth and Reconciliation Commission — the commission that examined the legacy of residential schools; its 94 calls to action remain a key accountability reference |
| **National Inquiry into MMIWG** | Canada | The National Inquiry into Missing and Murdered Indigenous Women and Girls — its final report contains 231 calls for justice |
| **Select committee** | UK / Australia | A parliamentary committee examining a specific issue or government department; equivalent to a standing committee |
| **Section 21 inquiry** | UK | A statutory public inquiry under the Inquiries Act 2005 |

---

## Relationships to extract from government reports

1. **Commission/Committee → Government**: Reporting relationship and tabling date
2. **Report → Recommendation**: Each formal recommendation (numbered, with text)
3. **Recommendation → Government response**: Accepted/rejected/in principle, with committed action
4. **Person → Commission**: Commissioner, chair, member, expert witness
5. **Report → Prior report**: Where a current report repeats findings from an earlier one (cite both)

---

## What investigators typically miss

1. **The minority report or dissenting opinion** — a dissenting commissioner often captures the most pointed critique of the institution or the majority's conclusions. Always read dissents in full.
2. **The submissions and exhibits** — royal commissions and parliamentary committees receive written submissions from governments, organizations, and individuals. These submissions are often public and contain admissions and evidence not reproduced in the final report.
3. **The list of witnesses** — who was called to testify (and who was not) is itself informative. Notable absences may indicate the commission chose not to pursue certain lines of inquiry.
4. **Progress tracking on prior calls to action** — civil society organizations, academics, and media organizations often independently track implementation of major reports' recommendations. These trackers are authoritative reference points.
5. **The government's tabling date vs. completion date** — governments sometimes sit on completed reports before tabling them publicly. The gap between when a report was completed and when it was released may be significant.
6. **The founding instrument** — the order in council, letters patent, or statutory instrument establishing a commission defines its mandate, composition, and powers. Any subsequent narrowing of scope (by the commission or the government) becomes apparent against the original mandate.

---

## Sources and further reading

### Official and regulatory
- [Parliamentary Budget Office — Canada](https://www.pbo-dpb.ca/en) — Independent officer of Parliament who provides non-partisan economic and fiscal analysis to senators and members of Parliament; publishes costing notes, budget analyses, and economic outlooks
- [Parliamentary Budget Office — Publications](https://www.pbo-dpb.ca/en/publications) — Searchable archive of all PBO reports, legislative costing notes, and economic analyses
- [Government of Canada Publications](https://publications.gc.ca/site/eng/home.html) — Central catalogue of federal government publications including royal commission reports, departmental evaluations, and advisory council reports, with over 500,000 digitized items
- [National Inquiry into Missing and Murdered Indigenous Women and Girls — Final Report](https://www.mmiwg-ffada.ca/final-report/) — The official repository of the MMIWG inquiry's final report and its 231 Calls for Justice
- [National Centre for Truth and Reconciliation — Reports](https://nctr.ca/records/reports/) — Archive of TRC commission reports, residential school records, and related government and healing foundation documents

### Practitioner and public interest
- [CRS Reports — Congressional Research Service](https://crsreports.congress.gov/) — Nonpartisan policy and legal analysis prepared by the Library of Congress for the US Congress; publicly available since 2018 and covering a wide range of domestic and foreign policy topics
- [CBC News — Beyond 94](https://www.cbc.ca/news/indigenous/beyond-94-truth-and-reconciliation-1.4574765) — CBC's ongoing tracker of implementation progress on the Truth and Reconciliation Commission's 94 Calls to Action, updated regularly with status assessments and reporting

### Journalism resources
- [House of Commons — Open Data](https://www.ourcommons.ca/en/open-data) — Machine-readable datasets from the House of Commons including committee evidence, Hansard transcripts, bill information, and member expenditure data
