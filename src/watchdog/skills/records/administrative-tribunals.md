# Domain knowledge — Administrative tribunal records

This skill is loaded by `/ingest` when the document type is a decision, order, or ruling from a quasi-judicial administrative tribunal that is not a court of law, not a labour or employment arbitration (covered by `labour-arbitration`), not an immigration body (covered by `immigration-refugee`), and not a professional licensing panel (covered by `professional-licensing` or `healthcare-licensing`). Administrative tribunals are government-created bodies with statutory powers to hear disputes and make binding decisions.

Apply this knowledge in addition to the standard extraction process. It tells you what to look for, what terminology means, and what patterns are worth flagging.

---

## Document types covered

- Human rights tribunal decisions (discrimination in housing, services, facilities — not employment, which is `labour-arbitration`)
- Environmental and energy review board decisions and panel reports
- Competition tribunal decisions and consent agreements
- Expropriation boards and compensation decisions
- Privacy commissioner investigation reports and orders
- Information commissioner orders (access to information appeals)
- Liquor, cannabis, and gaming authority decisions
- Financial services regulatory tribunal decisions (securities, banking, insurance regulators sitting quasi-judicially)
- Agricultural review boards and supply management tribunal decisions
- Transportation authority decisions (rail, road — aviation is `aircraft-logs`)
- Utility and rate regulation board decisions
- In Canada: Canadian Human Rights Tribunal; Canadian Radio-television and Telecommunications Commission (CRTC); National Energy Board / Canada Energy Regulator; Competition Tribunal; Office of the Privacy Commissioner orders; provincial human rights tribunals; Ontario Energy Board
- In the US: Federal Communications Commission (FCC) adjudications; Federal Energy Regulatory Commission (FERC) orders; state public utilities commissions; state human rights agencies
- In the UK: Competition and Markets Authority (CMA) decisions; Information Commissioner's Office (ICO) enforcement notices; First-tier Tribunal (General Regulatory Chamber); Upper Tribunal

---

## Always-present fields to extract

| Field | What to look for |
|-------|-----------------|
| **Tribunal name** | The full legal name of the body |
| **Case / file number** | The reference number assigned to the matter |
| **Complainant / applicant** | Name and any identifying role (individual, corporation, government body) |
| **Respondent** | Who the complaint is against |
| **Decision date** | When the decision or order was issued |
| **Decision-maker(s)** | Name(s) and title(s) of the adjudicator(s) or panel members |
| **Enabling statute** | The legislation under which the tribunal derives its jurisdiction |
| **Outcome** | Complaint upheld/dismissed; order granted/refused; application allowed/denied |
| **Remedy or order** | What the tribunal directed the respondent to do |
| **Compliance deadline** | When the order must be implemented |
| **Costs** | Whether costs were awarded and to whom |

---

## Red flags — what to look for

### Jurisdictional issues

- **Challenges to jurisdiction** — a respondent arguing the tribunal has no authority to hear the case often signals they believe the underlying complaint has merit. Jurisdictional challenges that succeed can bury a meritorious complaint in delay.
- **Statute cited vs. substance of complaint** — the enabling statute the complainant cites shapes what remedies are available. Where a complainant picks a narrower statutory vehicle than available, ask whether the more powerful route was deliberately avoided.
- **Prior complaints dismissed for procedural reasons** — a complainant who filed the same or similar complaint and had it dismissed for timeliness or jurisdictional reasons without a hearing on the merits.

### Competition and regulatory

- **Consent agreements** — when a regulator settles with a company rather than proceeding to a full hearing, the consent agreement often contains admissions of fact that would not appear in a litigated case. These are frequently underreported.
- **Undertakings in lieu of divestiture** — in merger reviews, an undertaking (behavioural remedy) instead of a structural one (forced sale) is a weaker outcome that often signals regulatory capture or inadequate enforcement.
- **Market definition disputes** — in competition matters, how the market is defined largely determines whether anticompetitive conduct is found. A narrow market definition that excludes obvious substitutes benefits the respondent.

### Human rights

- **Systemic findings** — a human rights decision finding systemic discrimination (affecting a class of people, not just the individual complainant) carries far more significance than an individual finding. The named complainant may be one of hundreds.
- **Comparator selection** — human rights complaints require identifying a comparator group. Who was chosen and who was not is analytically significant and often contested.
- **Remedies not enforced** — a human rights order that the respondent failed to comply with, and whether the complainant had the resources to pursue enforcement.

### Privacy and information

- **Orders ignored** — privacy and access-to-information orders issued by commissioners are sometimes ignored, requiring the complainant to go to court for enforcement. A pattern of non-compliance by a particular organization is newsworthy.
- **Findings of systemic failure** — a commissioner finding that an organization had no breach response plan, inadequate data retention policies, or systemic privacy failures.
- **Third-party disclosure** — access-to-information decisions where a third party (usually a company) objected to the disclosure of records about its activities and lost.

### Utility and rate regulation

- **Cross-subsidization findings** — a utility regulator finding that one class of ratepayers is subsidizing another is often politically significant and affects different communities differently.
- **Deferral accounts** — utilities sometimes defer costs to later rate applications. Accumulated deferral accounts represent future rate increases that are not visible in the current rate.

---

## Jurisdiction terminology

| Term | Jurisdiction | Meaning |
|------|-------------|---------|
| **Tribunal** | Universal | A quasi-judicial body with statutory authority to resolve disputes and make binding orders |
| **Adjudicator** | Universal | The decision-maker in a tribunal hearing |
| **Panel** | Universal | Multiple adjudicators hearing a matter together |
| **Complainant** | Universal | The person or organization that filed the complaint |
| **Respondent** | Universal | The person or organization against whom the complaint is made |
| **Mediation/conciliation** | Universal | An informal dispute resolution step before a formal hearing |
| **Summary dismissal** | Universal | Dismissal of a complaint without a full hearing, often on procedural grounds |
| **Systemic remedy** | Universal | An order requiring organization-wide change rather than just relief for the individual complainant |
| **Consent agreement** | Universal | A negotiated settlement with the regulator, often containing admissions of fact |
| **CRTC** | Canada | Canadian Radio-television and Telecommunications Commission |
| **CER** | Canada | Canada Energy Regulator (formerly National Energy Board) |
| **CHRT** | Canada | Canadian Human Rights Tribunal |
| **CMA** | UK | Competition and Markets Authority |
| **ICO** | UK | Information Commissioner's Office |
| **FCC** | US | Federal Communications Commission |
| **FERC** | US | Federal Energy Regulatory Commission |

---

## Relationships to extract from administrative tribunal records

1. **Complainant → Respondent**: Nature of the dispute (discrimination, anticompetitive conduct, regulatory violation)
2. **Tribunal → Enabling statute**: The legal source of the tribunal's jurisdiction
3. **Respondent → Remedy**: What the respondent was ordered to do or pay
4. **Decision-maker → Organization**: Prior appointments or affiliations (relevant to conflict-of-interest analysis)
5. **Company → Prior tribunal orders**: Pattern of findings or consent agreements across multiple proceedings

---

## What investigators typically miss

1. **The minority dissent** — where a panel is split, the dissenting reasons often identify weaknesses in the majority's analysis and signal what arguments were closest to prevailing.
2. **Consent agreements are not victories** — a consent agreement between a regulator and a company is often framed as an enforcement success, but the agreed-upon facts may represent only a fraction of what investigators found. The investigation report behind a consent agreement is a separate document and often obtainable by FOI.
3. **Intervenor lists** — many tribunals allow third parties to intervene. Who intervened and on which side reveals the industry and advocacy landscape around the issue.
4. **Costs awards against complainants** — in some tribunals, an unsuccessful complainant can be ordered to pay the respondent's costs. This has a chilling effect; a pattern of cost awards in a particular tribunal is a systemic story.
5. **Compliance monitoring** — an order to do something does not mean it was done. Regulators often have a compliance monitoring or follow-up process; the compliance record is as important as the order itself.
6. **The complaint that triggered the proceeding** — the formal decision rarely reproduces the original complaint in full. The original filing, available separately, may contain more specific factual allegations than what appears in the decision.
7. **Reconsideration and appeal decisions** — a significant percentage of tribunal decisions are reconsidered or appealed. The decision you have may have been overturned or modified by a subsequent proceeding.
