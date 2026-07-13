---
description: a statute, regulation, order-in-council, bill, government policy directive, or similar primary or secondary legislation — the instruments through which governments create legal obligations. For policy prose (white papers, consultation papers, regulatory impact assessments) use `government-reports`; for the decisions applying the law use `court-documents`, `administrative-tribunals`, and others
---
# Domain knowledge — Laws, regulations, and legislative instruments

This skill is loaded by Watchdog when the document type is a statute, regulation, order-in-council, bill, government policy directive, or similar primary or secondary legislation — the instruments through which governments create legal obligations. Policy prose about legislation (white papers, green papers, consultation papers, regulatory impact assessments) is covered by `government-reports`; the decisions applying the law are covered by `court-documents`, `administrative-tribunals`, and others.

---

## Document types covered

- Acts of parliament, congress, legislature, and national assembly (primary legislation)
- Bills at any stage — as introduced, as amended in committee, as passed
- Statutory instruments, regulations, orders-in-council, and decrees (secondary legislation)
- By-laws and municipal ordinances (the enacted text — the council application and decision records around a by-law are covered by `municipal-records`)
- Policy directives, ministerial guidelines, and government directives
- Codes of conduct with statutory force
- Proclamations and royal assent notices
- In Canada: federal Acts; Statutory Orders and Regulations (SOR/); Governor-in-Council (GIC) orders; provincial acts and regulations; Orders in Council (OIC)
- In the US: federal statutes (Public Laws); Code of Federal Regulations (CFR); executive orders; state statutes and administrative codes (each state legislates and codifies independently)
- In the UK: Acts of Parliament; Statutory Instruments (SI); Orders in Council; devolved legislation (Scottish Parliament, Senedd, Northern Ireland Assembly)
- In Australia: Commonwealth Acts; legislative instruments; state and territory legislation
- In other Westminster-derived systems (New Zealand, India, Ireland, and others): similarly structured Acts, statutory instruments, and gazettes under jurisdiction-specific names

---

## Fields to extract

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
| **Bill number and stage** | For bills: the bill number and the stage of the text (as introduced, as amended in committee, as passed) |

---

## Red flags — what to look for

### Hidden scope and exceptions

- **Exemptions carved out by regulation rather than statute** — regulations can be amended by cabinet without going through parliament, making exceptions to statutory rules much easier to add quietly. When a statutory protection has a regulation-based exemption, that exemption can be changed overnight.
- **Defined terms that narrow scope** — a law protecting "workers" that defines "worker" to exclude contractors, gig workers, or the self-employed does far less than it appears. Always check the definitions section.
- **"May" vs. "shall"** — discretionary language ("the minister may") creates a power without an obligation. Mandatory language ("the minister shall") creates an obligation. A law full of "may" may have no real enforcement teeth.

### Retroactivity and transitional provisions

- **Retroactive application** — legislation that takes effect before it was passed. This is rare but sometimes lawful; it is always newsworthy when it benefits a particular party.
- **Grandfather clauses** — provisions exempting existing projects, contracts, or entities from new requirements. Take note of who benefits from a grandfather clause.
- **Sunset clauses** — provisions that automatically expire on a specified date. A law with a sunset clause that the government has repeatedly extended without amendment suggests reluctance to make the provision permanent.
- **Transitional provisions that gut the new law** — transitional rules that let regulated parties comply with the old standard for years while appearing to have adopted the new one.

### Regulatory capture and lobbying fingerprints

- **Drafting language identical to industry submissions** — note any distinctive or unusually specific drafting language; log a lead to compare it against submissions made to the government during consultation, since word-for-word matching is a significant finding.
- **Regulations that have not been promulgated** — note sections that delegate detail to regulations (visible as phrases like "as prescribed by regulation"); a statute may create powers that require regulations before they take effect, so log a lead to check whether those regulations were ever made. Tracking unimplemented statutory provisions is a distinct accountability beat.
- **Scope narrowed between introduced and passed versions** — note the bill's stage and version; log a lead to compare the text against the bill as introduced, as amended at committee, and as passed, since amendments that benefit specific industries are often not reported.

### Emergency and extraordinary powers

- **Time-limited emergency provisions that become permanent** — note any stated sunset or emergency provisions and their expiry dates; powers introduced as temporary emergency measures are sometimes made permanent without a separate legislative debate, so log a lead to track whether they were renewed or made permanent.
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
| **Markup** | US | The committee process of amending and approving a bill before it goes to the full chamber |
| **By-law** | Canada/UK/Australia | Secondary legislation made by a municipal authority or corporation |
| **Enabling provision** | Universal | The section of an Act that authorizes a minister or body to make regulations |
| **Privative clause** | Universal | A provision attempting to exclude judicial review of decisions made under the legislation |
| **Sunset clause** | Universal | A provision specifying that the law or a part of it expires on a particular date |
| **Grandfather clause** | Universal | A provision exempting existing situations from a new requirement |

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
3. **The regulatory impact assessment** — most modern legislation is accompanied by a regulatory impact assessment or statement. This document contains the government's own estimates of compliance costs and intended beneficiaries. Where the actual effects differ significantly from the RIA, that divergence is worth noting.
4. **Commencement orders that are delayed or never made** — the date of royal assent is not the date a law comes into force. Some provisions require a proclamation to bring them into force; those proclamations may be delayed indefinitely. Track which sections of a law are actually in force.
5. **Transitional provisions** — transitional provisions at the end of legislation tell you who gets to keep doing what under the old rules, and for how long. These are frequently buried and underreported but often represent the most significant negotiated accommodations.
6. **Consequential amendments** — most statutes amend other statutes as a consequence of their main provisions. These consequential amendments can expand or limit the scope of other laws in ways that are not obvious from the bill's title or purpose clause.
7. **Regulations made without consultation** — compare the regulations as gazetted against any prior consultation draft or proposed regulation. Changes made between the consultation draft and the final version that benefit specific regulated parties may point to lobbying influence.

---

## Sources and further reading

### Official and regulatory
- [Justice Laws Website — Canada](https://laws-lois.justice.gc.ca/eng/) — The official consolidated source for all federal Acts and regulations of Canada, maintained by the Department of Justice, with both current and historical versions in English and French
- [LEGISinfo — Parliament of Canada](https://www.parl.ca/legisinfo) — The Parliament of Canada's official bill tracking system, jointly operated by the Senate, House of Commons, and Library of Parliament; covers all bills from first reading through royal assent back to 1994
- [Canada Gazette](https://gazette.gc.ca/accueil-home-eng.html) — The official newspaper of the Government of Canada, publishing proposed regulations (Part I) and enacted regulations and statutory instruments (Part II); essential for tracking the regulatory process
- [Congress.gov — Library of Congress](https://www.congress.gov/) — The official US government portal for federal legislation, including bill text, committee reports, the Congressional Record, and roll call votes from the 93rd Congress onward
- [CRS Reports — Congressional Research Service](https://crsreports.congress.gov/) — Nonpartisan analysis prepared for members of Congress explaining the background, provisions, and policy context of pending and enacted legislation
- [UK Legislation — National Archives](https://www.legislation.gov.uk/) — The official source for UK primary and secondary legislation, including Acts of Parliament, statutory instruments, and devolved legislation from the Scottish Parliament, Senedd, and Northern Ireland Assembly
- [EUR-Lex — European Union Law](https://eur-lex.europa.eu/homepage.html) — The official EU law database, providing free access to treaties, regulations, directives, and the Official Journal of the European Union in all 24 EU official languages

### Practitioner and public interest
- [GovTrack.us](https://www.govtrack.us/) — Independent, non-governmental tracker of US congressional legislation, voting records, and member activity; operated by Civic Impulse LLC with no government or party affiliation, running since 2004
- [Open States (Plural Policy)](https://openstates.org/) — Free tracker aggregating bill text, votes, and legislator information for all 50 US state legislatures, complementing the federal-only focus of Congress.gov
