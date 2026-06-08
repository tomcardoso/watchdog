# Domain knowledge — Laws, regulations, and policy documents

This skill is loaded by `/ingest` when the document type is a statute, regulation, order-in-council, government policy directive, government white paper, consultation paper, or similar primary or secondary legislation. This skill covers the documents through which governments create legal obligations and policy frameworks — not the decisions applying them (see `court-documents`, `administrative-tribunals`, and others for adjudicative records).

Apply this knowledge in addition to the standard extraction process. It tells you what to look for, what terminology means, and what patterns are worth flagging.

---

## Document types covered

- Acts of parliament, congress, legislature, and national assembly (primary legislation)
- Statutory instruments, regulations, orders-in-council, and decrees (secondary legislation)
- By-laws and municipal ordinances
- Policy directives, ministerial guidelines, and government directives
- Government white papers and green papers
- Consultation papers and regulatory impact assessments
- Codes of conduct with statutory force
- Proclamations and royal assent notices
- In Canada: federal Acts; Statutory Orders and Regulations (SOR/); Governor-in-Council (GIC) orders; provincial acts and regulations; Orders in Council (OIC)
- In the US: federal statutes (Public Laws); Code of Federal Regulations (CFR); executive orders; state statutes and administrative codes
- In the UK: Acts of Parliament; Statutory Instruments (SI); Orders in Council; devolved legislation (Scottish Parliament, Senedd, Northern Ireland Assembly)
- In Australia: Commonwealth Acts; legislative instruments; state and territory legislation

---

## Always-present fields to extract

| Field | What to look for |
|-------|-----------------|
| **Title / short title** | The formal and short names of the legislation |
| **Jurisdiction** | Which level of government enacted it (federal, state/provincial, municipal) |
| **Enacting body** | The parliament, legislature, or authority that passed or issued it |
| **Date of royal assent / enactment** | When the legislation received formal approval |
| **Commencement date** | When the legislation takes legal effect (often different from enactment) |
| **Citation** | The formal citation used to reference it (e.g. RSC 1985, c C-44; 42 USC 1983; SI 2018/644) |
| **Responsible minister / department** | The government body responsible for administering the law |
| **Amendments** | Any amending acts or instruments included in the document |
| **Defined terms** | Key terms the legislation defines, often in a "Definitions" or "Interpretation" section |
| **Enabling power** | For regulations, the section of the parent act that authorizes them |

---

## Red flags — what to look for

### Hidden scope and exceptions

- **Exemptions carved out by regulation rather than statute** — regulations can be amended by cabinet without going through parliament, making exceptions to statutory rules much easier to add quietly. When a statutory protection has a regulation-based exemption, that exemption can be changed overnight.
- **Defined terms that narrow scope** — a law protecting "workers" that defines "worker" to exclude contractors, gig workers, or the self-employed does far less than it appears. Always check the definitions section.
- **"May" vs. "shall"** — discretionary language ("the minister may") creates a power without an obligation. Mandatory language ("the minister shall") creates an obligation. A law full of "may" may have no real enforcement teeth.

### Retroactivity and transitional provisions

- **Retroactive application** — legislation that takes effect before it was passed. This is rare but sometimes lawful; it is always newsworthy when it benefits a particular party.
- **Grandfather clauses** — provisions exempting existing projects, contracts, or entities from new requirements. Who benefits from the grandfather clause is often the story.
- **Sunset clauses** — provisions that automatically expire on a specified date. A law with a sunset clause that the government has repeatedly extended without amendment suggests reluctance to make the provision permanent.
- **Transitional provisions that gut the new law** — transitional rules that let regulated parties comply with the old standard for years while appearing to have adopted the new one.

### Regulatory capture and lobbying fingerprints

- **Drafting language identical to industry submissions** — compare the legislation to submissions made to the government during consultation. Word-for-word matching is a significant finding.
- **Regulations that have not been promulgated** — a statute may create powers that require regulations before they take effect. If those regulations have never been made, the power is dead. Tracking unimplemented statutory provisions is a distinct accountability beat.
- **Scope narrowed between introduced and passed versions** — track changes between the bill as introduced, as amended at committee, and as passed. Amendments that benefit specific industries are often not reported.

### Emergency and extraordinary powers

- **Time-limited emergency provisions that become permanent** — powers introduced as temporary emergency measures that were subsequently made permanent, sometimes without a separate legislative debate.
- **Broad regulation-making powers** — enabling provisions that allow the executive to make regulations on almost any subject matter without returning to parliament. These represent a transfer of legislative power to the executive.
- **Privative clauses** — provisions that attempt to prevent courts from reviewing decisions made under the legislation. Their scope has been significantly limited by constitutional decisions in many jurisdictions.

---

## Jurisdiction terminology

| Term | Jurisdiction | Meaning |
|------|-------------|---------|
| **Royal assent** | Canada/UK/Australia | Formal approval of legislation by the Crown; the final step before a law comes into force |
| **Commencement / in force** | Universal | The date the law takes legal effect; may be set by proclamation, a later date in the Act, or a regulation |
| **Order-in-council (OIC)** | Canada/UK | A regulation or order made by cabinet (the Governor-in-Council in Canada, the Privy Council in the UK) |
| **Statutory instrument (SI)** | Canada/UK | Secondary legislation made under powers granted by an Act |
| **Regulation (SOR/)** | Canada | Statutory Order and Regulation — the formal citation form for Canadian federal regulations |
| **Public Law (P.L.)** | US | The citation form for US federal statutes after enactment |
| **Code of Federal Regulations (CFR)** | US | The codification of permanent US federal regulatory rules |
| **Executive order** | US | A directive issued by the President with the force of law, without congressional approval |
| **By-law** | Canada/UK/Australia | Secondary legislation made by a municipal authority or corporation |
| **Enabling provision** | Universal | The section of an Act that authorizes a minister or body to make regulations |
| **Privative clause** | Universal | A provision attempting to exclude judicial review of decisions made under the legislation |
| **Sunset clause** | Universal | A provision specifying that the law or a part of it expires on a particular date |
| **Grandfather clause** | Universal | A provision exempting existing situations from a new requirement |
| **White paper** | Canada/UK/Australia | A government policy statement signalling its intention to legislate |
| **Green paper** | Canada/UK/Australia | A government consultation document inviting public comment before policy is settled |
| **Regulatory impact assessment (RIA)** | Universal | A government document estimating the costs and benefits of a proposed regulation |

---

## Relationships to extract from legislation

1. **Legislation → Responsible department/minister**: Who administers the law
2. **Regulation → Enabling statute**: The parent Act under which the regulation was made
3. **Amendment → Original legislation**: How and when the law was changed
4. **Legislation → Exempted entities**: Who is carved out of the law's requirements
5. **Legislation → Affected regulated class**: Who the law applies to (with attention to defined terms that narrow this class)

---

## What investigators typically miss

1. **The regulations that were never made** — statutes sometimes create powers that require regulations before they take effect, and those regulations may never have been drafted. An unproclaimed section of a statute is a power that exists on paper but does nothing.
2. **Comparison of bill as introduced vs. as passed** — legislative databases in most jurisdictions preserve every version of a bill. Tracking what was removed between first reading and royal assent reveals what the government retreated on, and often who pushed back.
3. **The regulatory impact assessment** — most modern legislation is accompanied by a regulatory impact assessment or statement. This document contains the government's own estimates of compliance costs and intended beneficiaries. Where the actual effects differ significantly from the RIA, that divergence is a story.
4. **Commencement orders that are delayed or never made** — the date of royal assent is not the date a law comes into force. Some provisions require a proclamation to bring them into force; those proclamations may be delayed indefinitely. Track which sections of a law are actually in force.
5. **Transitional provisions** — transitional provisions at the end of legislation tell you who gets to keep doing what under the old rules, and for how long. These are frequently buried and underreported but often represent the most significant negotiated accommodations.
6. **Consequential amendments** — most statutes amend other statutes as a consequence of their main provisions. These consequential amendments can expand or limit the scope of other laws in ways that are not obvious from the bill's title or purpose clause.
7. **Regulations made without consultation** — compare the regulations as gazetted against any prior consultation draft or proposed regulation. Changes made between the consultation draft and the final version that benefit specific regulated parties are a common lobbying story.
