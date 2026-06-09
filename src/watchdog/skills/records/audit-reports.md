# Domain knowledge — Audit reports

This skill is loaded by `/ingest` when the document type is an auditor general report, value-for-money audit, performance audit, internal audit report, public accounts, inspector general report, or similar public accountability document.

Apply this knowledge in addition to the standard extraction process. It tells you what to look for, what terminology means, and what patterns are worth flagging.

---

## Document types covered

- Supreme audit institution reports (auditor general, comptroller general, national audit office, court of auditors)
- Provincial, state, or regional auditor general reports
- Municipal auditor general reports
- Value-for-money / performance audits
- Financial audits and public accounts
- Internal audit reports (government departments)
- Crown corporation or state enterprise audit committee reports
- Inspector general reports
- Parliamentary or legislative budget office reports
- Special examination reports
- In Canada: Auditor General of Canada reports; Commissioner of the Environment and Sustainable Development (CESD) reports; Parliamentary Budget Officer (PBO) reports
- In the US: Government Accountability Office (GAO) reports; federal Inspector General reports
- In the UK: National Audit Office (NAO) reports
- In Australia: Australian National Audit Office (ANAO) reports

---

## Always-present fields to extract

| Field | What to look for |
|-------|-----------------|
| **Audit entity** | The department, program, or organization that was audited |
| **Audit type** | Financial audit, performance/VFM audit, special examination |
| **Audit period** | The period covered by the audit |
| **Report date / tabling date** | When the report was released to Parliament/Legislature/Council |
| **Audit objective** | What the audit set out to examine |
| **Findings** | Key findings, numbered or lettered as in the report |
| **Recommendations** | Formal recommendations, numbered as in the report |
| **Management response** | The audited organization's response to each recommendation |
| **Agreed / disagreed** | Whether management agreed with each recommendation |
| **Implementation date committed** | When the organization committed to implementing each recommendation |
| **Previous recommendations outstanding** | Recommendations from prior audits not yet implemented |

---

## Red flags — what to look for

### Finding severity and management response

- **Disagreement with a recommendation** — management that disagrees with an audit finding is unusual. Note the specific recommendation and the stated reason for disagreement; these often reveal institutional defensiveness.
- **Vague implementation commitments** — a response that commits to "review the matter" or "develop a framework" without a specific action and deadline is often a non-commitment.
- **"Partially agree" responses** — management agrees with part of a recommendation but not the most consequential part; this allows them to claim action was taken while avoiding the core issue.
- **Repeat findings** — the most important audit signal: a finding that was made in a prior audit, was supposedly addressed, and has recurred. This shows the systemic problem was not actually fixed.
- **Prior recommendations still outstanding** — most audit reports include a section on prior recommendations. A recommendation that was outstanding in the previous audit and still outstanding now is a significant finding, even if not highlighted as such.

### Financial integrity

- **Qualified audit opinion** — an auditor's opinion that is not "clean" (unmodified). The qualifications are listed and each one represents a specific financial statement problem.
- **Emphasis of matter** — a note the auditor adds to draw attention to a matter (e.g. a going concern) without qualifying the opinion. Often overlooked but significant.
- **Non-compliance with authorities** — a finding that the organization spent money in a manner not authorized by Parliament, the legislature, or the governing statute. This is a constitutional or legal issue, not just an accounting one.
- **Transfers to third parties without controls** — grants or contribution agreements where the auditor found insufficient oversight of how the funds were spent by recipients.
- **Write-offs and forgiveness of amounts receivable** — money owed to the government that was written off. The amounts and the justification are significant.

### Program performance

- **Program objectives not achieved** — the core value-for-money finding: a program spending significant public money that cannot demonstrate it achieved its stated goals.
- **Performance measurement gaps** — the auditor found the program had no means to measure whether it was achieving results. This is both a finding in itself and a reason why accountability is impossible.
- **Conflict between program delivery and departmental objectives** — where the audit finds the administering agency's priorities were inconsistent with the program's purpose.
- **Outsourcing and contractor management** — audits frequently find that government departments awarded contracts to external consultants without competitive process, without clear deliverables, or without monitoring whether deliverables were met.

---

## Jurisdiction terminology

| Term | Jurisdiction | Meaning |
|------|-------------|---------|
| **OAG / AG** | Canada | Office of the Auditor General / Auditor General — the independent officer of Parliament who audits government finances and programs |
| **VFM audit** | Universal | Value-for-money audit (also called performance audit) — examines whether a program achieved its objectives efficiently and effectively |
| **Special examination** | Canada | A periodic audit of a federal Crown corporation — required every 10 years |
| **Public accounts** | Universal | The government's annual financial statements, tabled in the legislature |
| **CESD** | Canada | Commissioner of the Environment and Sustainable Development — an officer within the OAG |
| **PBO** | Canada | Parliamentary Budget Officer — independent officer who analyzes the financial impact of government proposals |
| **GAO** | US | Government Accountability Office — the US supreme audit institution |
| **IG** | US | Inspector General — each major US federal agency has an IG with audit and investigative powers |
| **NAO** | UK | National Audit Office — the UK supreme audit institution |
| **ANAO** | Australia | Australian National Audit Office |
| **Court of Auditors** | EU / France / many others | The equivalent of an auditor general in many civil law countries |
| **Contribution agreement** | Canada | A funding agreement between the government and a recipient (NGO, municipality, company) — subject to audit |

---

## Relationships to extract from audit reports

1. **Audit office → Department/program**: Audit relationship (with finding types and dates)
2. **Finding → Recommendation**: Each finding maps to one or more formal recommendations
3. **Recommendation → Management response**: Agreed/disagreed, committed action, and deadline
4. **Department → Prior recommendations**: Outstanding recommendations from earlier audits
5. **Program → Third party**: Grant recipients or contractors who were themselves subject to audit findings

---

## What investigators typically miss

1. **The prior-audit follow-up section** — audit reports typically include a section reviewing the status of prior recommendations. This section is often more newsworthy than the new findings, because it shows what governments promised and didn't do.
2. **Management responses are negotiated** — the "management response" published alongside audit findings is the result of a formal back-and-forth between the audit office and the audited entity. The fact that it is in the report does not mean it is candid or complete.
3. **The scope limitation note** — an auditor who was unable to obtain certain records may note a scope limitation. This tells you something was not examined that probably should have been.
4. **Recommendation numbering across years** — recommendations are numbered by chapter and paragraph. Cross-referencing recommendation numbers across multiple years of the same chapter can track whether a specific problem was fixed.
5. **The chapter title vs. the actual finding** — audit chapters often have bland titles ("Management of Federal Programs") that obscure significant findings. Read the findings and recommendations before concluding the chapter is routine.
6. **The departmental action plan** — after a performance audit, departments are often required to table an action plan. This document, separate from the management response in the audit, often makes specific commitments with timelines. Following up on these commitments is an ongoing accountability story.

---

## Sources and further reading

### Official and regulatory
- [Office of the Auditor General of Canada (OAG)](https://www.oag-bvg.gc.ca/internet/english/admin_e_41.html) — The independent officer of Parliament responsible for auditing federal government finances and programs; all tabled reports are publicly available on the OAG site
- [OAG Reports to Parliament](https://www.oag-bvg.gc.ca/internet/English/parl_lp_e_856.html) — Index of all Auditor General reports tabled in Parliament, including performance audits, special examinations, and public accounts
- [US Government Accountability Office (GAO)](https://www.gao.gov/) — The US Congress's independent audit and evaluation agency; publishes reports and findings on federal programs at gao.gov
- [GAO Yellow Book: Government Auditing Standards](https://www.gao.gov/yellowbook) — The preeminent standards for government auditing (GAGAS); sets requirements for financial audits, attestation engagements, and performance audits of government entities and grant recipients
- [INTOSAI: International Organization of Supreme Audit Institutions](https://www.intosai.org/index.html) — The umbrella body for 196 national supreme audit institutions; publishes the International Standards of Supreme Audit Institutions (ISSAIs)
- [INTOSAI Audit Standards (ISSAIs)](https://www.intosai.org/focus-areas/audit-standards.html) — The INTOSAI Framework of Professional Pronouncements, including founding principles, ISSAIs for public sector auditing, and implementation guidance
- [Oversight.gov — US Inspector General Reports](https://www.oversight.gov/) — Centralised database of all public reports from US federal Offices of Inspector General; searchable by agency, report type, and topic

### Practitioner and public interest
- [Canadian Audit and Accountability Foundation (CAAF)](https://www.caaf-fcar.ca/en/performance-audit/15-performance-audit) — Canada's premier research and education foundation for public sector performance audit; publishes practice guides and methodology resources for performance auditors
