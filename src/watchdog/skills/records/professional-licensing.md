# Domain knowledge — Professional licensing and discipline records

This skill is loaded by `/ingest` when the document type is a discipline decision, conduct review, fitness-to-practise order, licence revocation or suspension, or similar regulatory action from a professional licensing body for non-healthcare professions. For healthcare professions (medicine, nursing, pharmacy, dentistry), use `healthcare-licensing` instead.

Apply this knowledge in addition to the standard extraction process. It tells you what to look for, what terminology means, and what patterns are worth flagging.

---

## Document types covered

- Law society discipline decisions and conduct reviews (lawyers, paralegals, notaries)
- Engineering and geoscience regulatory body decisions
- Certified public accountant / chartered professional accountant discipline decisions
- Real estate council and board discipline decisions
- Financial adviser and planner licensing body decisions (securities dealers, investment advisers, mortgage brokers)
- Architectural and landscape architectural regulatory decisions
- Land surveying and appraisal body decisions
- Veterinary licensing body decisions
- Social work and psychology licensing body decisions
- Teacher certification and conduct decisions
- Building trades licensing and discipline decisions (electricians, plumbers, contractors)
- In Canada: Law societies (provincial); Engineers and Geoscientists (provincial); CPA Canada provincial bodies; Real Estate Council of Ontario (RECO); Investment Industry Regulatory Organization of Canada (IIROC) / CIRO decisions; Mutual Fund Dealers Association (MFDA) decisions
- In the US: State bar association discipline decisions; FINRA (Financial Industry Regulatory Authority) enforcement decisions; state engineering boards; state CPA boards
- In the UK: Solicitors Regulation Authority (SRA) decisions; Financial Conduct Authority (FCA) enforcement notices; Institute of Chartered Accountants in England and Wales (ICAEW) decisions

---

## Always-present fields to extract

| Field | What to look for |
|-------|-----------------|
| **Practitioner name** | Full name and any professional designations |
| **Licence number or registration** | The professional's licence or registration number |
| **Regulatory body** | The full name of the licensing body |
| **Date of decision** | When the discipline panel issued its decision |
| **Allegations** | The specific conduct alleged and the rules or standards cited |
| **Findings** | Which allegations were proven or admitted |
| **Sanction** | The penalty imposed: reprimand, fine, suspension, revocation, conditions |
| **Duration of suspension** | If suspended, for how long |
| **Reinstatement conditions** | What the practitioner must do to return to practice |
| **Client / complainant** | Whether a client was harmed and how |
| **Financial restitution ordered** | Amounts the practitioner must repay to clients |
| **Costs awarded** | Costs the practitioner must pay to the regulatory body |

---

## Red flags — what to look for

### Trust account and financial misconduct

- **Misappropriation of client funds** — the most serious finding in any regulated profession involving client money. In law, misappropriation of trust funds typically results in disbarment. In finance, it is securities fraud.
- **Commingling funds** — mixing client money with personal or business funds violates trust accounting rules in law, real estate, and financial advising. It may not involve theft but is a serious practice failure.
- **Churning** — in investment advising, churning means trading excessively in a client's account to generate commissions. Look for trading frequency relative to the client's stated risk tolerance and investment objectives.
- **Unauthorized trading** — executing trades without client consent. Often paired with churning findings.

### Competence and conduct

- **Pattern of client complaints** — a practitioner with multiple complaints dismissed individually may still represent a systemic problem. Regulatory decisions rarely reference the full complaint history in detail; FOI or registry requests can fill the gap.
- **Failure to communicate** — in law and financial advising, failure to keep clients informed is a standalone finding. It often accompanies more serious misconduct as a process failure.
- **Practising while suspended** — continuing to practise after a licence suspension is a criminal matter in many jurisdictions, not just a regulatory one.
- **Undisclosed conflicts of interest** — acting for parties on both sides of a transaction, or recommending investments in which the adviser has an undisclosed financial interest.

### Supervision failures

- **Principal or supervising lawyer/adviser held responsible** — where a junior practitioner engaged in misconduct, whether the supervising professional was also disciplined reveals whether the regulatory body holds firms and supervisors accountable.
- **Firm-level findings** — some regulatory bodies can find the firm, not just the individual, in violation. A firm-level finding suggests systemic failures beyond one bad actor.

### Consent resolutions and negotiated outcomes

- **Facts admitted vs. contested** — many discipline proceedings end in a consent resolution where the practitioner admits certain facts. These admissions are often narrower than what was alleged and may exclude the most serious conduct.
- **Sanction below the recommended range** — a panel imposing a sanction below what the regulatory body sought may signal that the agreed statement of facts omitted context that would have supported a harsher outcome.
- **Reinstatement after serious misconduct** — a practitioner reinstated after disbarment or licence revocation. What conditions were imposed and how was compliance monitored?

---

## Jurisdiction terminology

| Term | Jurisdiction | Meaning |
|------|-------------|---------|
| **Law society** | Canada/UK | The self-regulatory body for lawyers in a province or jurisdiction |
| **Bar association** | US | The regulatory body for lawyers (state bars have licensing authority; national bar associations are voluntary) |
| **Disbarment** | US | Permanent removal of a lawyer's licence to practise |
| **Striking off** | UK | Permanent removal of a solicitor's name from the roll |
| **Bencher** | Canada/UK | An elected or appointed member of the governing board of a law society |
| **CPA** | Canada/US | Certified Public Accountant (US) / Chartered Professional Accountant (Canada) — the regulated accounting designation |
| **CIRO** | Canada | Canadian Investment Regulatory Organization — merged IIROC and MFDA in 2023; regulates investment dealers and mutual fund dealers |
| **FINRA** | US | Financial Industry Regulatory Authority — regulates broker-dealers and investment advisers |
| **FCA** | UK | Financial Conduct Authority — regulates financial services firms and some individual practitioners |
| **SRA** | UK | Solicitors Regulation Authority — regulates solicitors in England and Wales |
| **Trust account** | Universal | A segregated account holding client money, separate from the practitioner's own funds |
| **Reprimand** | Universal | A formal public finding of misconduct without suspension or revocation |
| **Undertaking** | Universal | A written commitment by the practitioner to the regulatory body |
| **Fitness to practise** | Universal | Whether a practitioner is able to perform professional duties safely and competently |

---

## Relationships to extract from professional licensing records

1. **Practitioner → Regulatory body**: The jurisdiction and body that took the action
2. **Practitioner → Employer/firm**: Where the practitioner was working at the time of the conduct
3. **Practitioner → Client(s)**: Nature of the harm and financial amounts involved
4. **Regulatory body → Sanction**: The specific penalty and conditions
5. **Supervising professional → Junior practitioner**: Accountability chain where supervision failures are alleged

---

## What investigators typically miss

1. **The public registry** — most regulatory bodies maintain a public registry of all licensed practitioners, including past discipline. The discipline decision you have is a single document; the registry entry shows the complete discipline history, including matters that predate the current proceeding.
2. **The complaints process behind the decision** — discipline decisions describe the outcome, not the investigation. The original complaint, investigation report, and any charges are separate documents. Many regulatory bodies will disclose these on request.
3. **Conditions on a licence** — a practitioner may have conditions imposed (e.g., cannot handle trust funds, must be supervised) that are not highlighted in the decision headline. Conditions represent ongoing risk and are the most important thing to check when assessing whether a practitioner is safe to use.
4. **Parallel criminal proceedings** — serious financial misconduct in a regulated profession often also constitutes fraud or theft. Whether criminal charges were laid — or notably, were not laid — is a separate story.
5. **The client restitution fund** — law societies, real estate councils, and some other regulatory bodies maintain client restitution funds that compensate victims of practitioner theft. Claims to these funds, which are often public, reveal the scale of client harm.
6. **Corporate structure of the practice** — professional misconduct often involves a firm, not just an individual. Who else worked at the firm, who owned it, and whether the firm is still operating under a different name are questions the decision itself may not answer.
7. **Reciprocal discipline** — when a practitioner is disciplined in one jurisdiction, other jurisdictions where they are licensed typically initiate their own discipline proceedings on the same facts. A practitioner disciplined in one province may remain licensed and active in another.

---

## Sources and further reading

### Official and regulatory
- [Law Society of Ontario — Current Regulatory Actions](https://lso.ca/protecting-the-public/current-regulatory-actions) — gateway to active LSO discipline and regulatory proceedings; full Tribunal decisions available through the Law Society Tribunal portal and CanLII
- [FINRA BrokerCheck](https://brokercheck.finra.org/) — free US public database of broker-dealer and investment adviser registration, employment history, regulatory actions, arbitrations, and complaints; covers all FINRA-registered individuals and firms
- [CIRO Enforcement — Canadian Investment Regulatory Organization](https://www.ciro.ca/rules-and-enforcement/enforcement) — searchable database of active and concluded CIRO enforcement proceedings against Canadian investment dealers and mutual fund dealers; full panel decisions available on CanLII
- [Professional Engineers Ontario — Discipline Decisions Gazette](https://www.peo.on.ca/public-protection/discipline/complaints-decisions-gazette) — Ontario engineering discipline findings and orders published under the Professional Engineers Act; covers decisions from 1997 onward
- [RECO Discipline and Appeals Decisions](https://registrantsearch.reco.on.ca/DisciplineAppeals) — Ontario real estate council discipline committee decisions involving breaches of REBBA and TRESA; decisions retained for at least 60 months

### Journalism resources
- [GIJN: Researching Corporations and Their Owners](https://gijn.org/resource/researching-corporations-and-their-owners/) — GIJN guide covering corporate registries, securities databases, and ownership research tools across multiple jurisdictions; useful for tracing firm-level misconduct beyond individual discipline decisions
