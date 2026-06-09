# Domain knowledge — Lobbying records

This skill is loaded by `/ingest` when the document type is a lobbyist registration, communication report, client-registrant relationship filing, or similar lobbying disclosure.

Apply this knowledge in addition to the standard extraction process. It tells you what to look for, what terminology means, and what patterns are worth flagging.

---

## Document types covered

- Lobbyist registrations (federal, provincial, US federal and state, UK, EU, and other jurisdictions)
- Communication reports (periodic activity logs)
- Client-registrant relationship filings
- Senior official or designated public office holder meeting records
- In-house lobbyist returns
- Consultant lobbyist filings
- Deregistration notices
- In Canada: Federal Registry of Lobbyists registrations; communication reports; provincial lobbyist registries
- In the US: Lobbying Disclosure Act (LDA) registrations (LD-1) and quarterly reports (LD-2)
- In the UK: UK Register of Consultant Lobbyists
- In the EU: EU Transparency Register

---

## Always-present fields to extract

| Field | What to look for |
|-------|-----------------|
| **Registrant name** | The individual or firm doing the lobbying |
| **Registration number** | Assigned by the lobbying registry |
| **Client name** | The organization paying for the lobbying (may differ from registrant) |
| **Registration date** | When the registration was filed |
| **Effective date** | When the lobbying activity began |
| **Subject matter** | What the lobbyist is lobbying about — the specific legislation, regulation, policy, or contract |
| **Government institutions targeted** | Which departments, agencies, or bodies are being lobbied |
| **Public office holders contacted** | Specific officials who were contacted |
| **Communication dates** | When contacts occurred |
| **Lobbying fees / expenses** | Amount paid (disclosure requirements vary by jurisdiction) |
| **Previous public offices held** | Whether the lobbyist is a former government official |

---

## Red flags — what to look for

### Revolving door

- **Former cabinet minister or senior official lobbying their former department** — most jurisdictions impose a cooling-off period for former ministers and senior officials. In Canada: two years for former ministers (five years for designated public office holders). In the US: two years for members of Congress, one year for senior executive branch officials. A registration filed shortly after leaving office warrants scrutiny.
- **Former political staff lobbying on behalf of a client in the same policy area they worked on** — cooling-off periods for ministerial or political staff vary; in Canada it is one year at the federal level.
- **Lobbyist who was previously a senior official in the department they are now lobbying** — even if legally compliant, the relationship is worth examining.
- **Public office holder who met with a lobbyist later joins the lobbyist's firm or client** — the inverse of the revolving door: the official who was lobbied ends up working for the lobbying interest.

### Registration patterns

- **Late registration** — lobbying must be registered within a set period of the first communication (10 days in Canada; 45 days in the US). A registration filed after a known meeting is a potential violation.
- **Retroactive registration** — a registration with an effective date months before the filing date.
- **Registration filed just before a government decision** — suggests the lobbying may have started before registration, or that the lobbyist knew a decision was imminent.
- **Broad, vague subject matter descriptions** — a subject matter defined as "economic development" or "government relations" covers almost anything. Compare to the specific meetings reported.
- **Multiple clients in the same industry with overlapping subject matter** — a lobbying firm working for several companies in the same sector on the same regulatory file may have undisclosed conflicts.

### Meeting and communication patterns

- **High volume of meetings with a single official** — especially if that official later makes a decision favourable to the client.
- **Meetings with political staff rather than public servants** — in many jurisdictions, communications with ministers' political staff trigger registration requirements. These are often not disclosed because the lobbyist doesn't realize they should be.
- **Gaps in reporting** — months where no communications are reported, followed by a burst of activity, may indicate underreporting.
- **Oral communications not reported as written** — the most sensitive discussions are often oral and thus harder to trace; compare oral vs. written communication volumes.

### Financial patterns (US LDA)

- **Lobbying income just below the disclosure threshold** — amounts just below the threshold reported by multiple clients may indicate deliberate underreporting.
- **Large lobbying fees combined with no reported contacts** — money spent with no meetings recorded is unusual.
- **Significant increase in lobbying spend around a regulatory or legislative event** — a spike in reported income aligned with a known policy decision.

---

## Jurisdiction terminology

### Canada

| Term | Meaning |
|------|---------|
| **Consultant lobbyist** | A person paid to lobby on behalf of a client; must register if they communicate with a designated public office holder |
| **In-house lobbyist** | An employee who lobbies as a significant part of their duties; the organization registers |
| **Designated Public Office Holder (DPOH)** | Senior officials whose communications with lobbyists must be reported: ministers, ministerial staff, deputy ministers, heads of Crown corporations |
| **Five-year prohibition** | Former designated public office holders cannot lobby federally for five years after leaving office |
| **Office of the Commissioner of Lobbying (OCL)** | The federal body that administers the Lobbying Act and investigates violations |
| **Registry of Lobbyists** | The public federal database: lobbyistregistrar.gc.ca |

### United States

| Term | Meaning |
|------|---------|
| **LDA** | Lobbying Disclosure Act — the US federal lobbying disclosure law |
| **LD-1** | Registration form filed within 45 days of first lobbying contact |
| **LD-2** | Quarterly activity report — income/expenses and specific issues lobbied |
| **LD-203** | Semi-annual report of political contributions |
| **Covered official** | A member of Congress, Congressional staff, or senior executive branch official |
| **Revolving door** | The movement of individuals between government positions and the private lobbying industry |
| **Grassroots lobbying** | Efforts to influence public opinion to pressure officials — generally not required to be disclosed under LDA |

### UK and EU

| Term | Meaning |
|------|---------|
| **UK Register of Consultant Lobbyists** | The UK statutory register — covers consultant lobbyists communicating with ministers and permanent secretaries |
| **EU Transparency Register** | The joint register of the European Parliament and Commission — not fully mandatory but increasingly required for meetings with senior officials |

---

## Relationships to extract from lobbying records

1. **Person → Organization**: Lobbyist (employed by or engaged by), Former employer (government body or political party)
2. **Person → Official**: Communication (with date, subject matter, and type)
3. **Organization → Government body**: Lobbying relationship (with subject matter and registration period)
4. **Organization → Organization**: Client–registrant relationship (who is paying whom to lobby)
5. **Person → Person**: Former official now lobbying former colleagues

---

## What investigators typically miss

1. **Deregistrations** — a lobbyist who stops a registration abruptly is worth checking: did a government decision go their client's way, or did they get caught lobbying without registering?
2. **The "subject matter" field across time** — compare the stated subject matter to known government decisions during the same period. If the government awarded a contract or changed a regulation consistent with what the lobbyist was pushing, that alignment is the story.
3. **Lobbyists who are also political donors** — lobbying registrations and election finance records are two separate databases; cross-referencing them shows who has both financial and direct access to politicians.
4. **Former officials who appear as references or technical contacts** — sometimes a former official doesn't register as a lobbyist but appears as a "subject matter expert" or "technical consultant" on filings.
5. **The gap between registration and first reported meeting** — if meetings start occurring the same week as registration, the communications may have preceded the registration.
6. **Corporate structure of the client** — the "client" listed may be a subsidiary; the ultimate beneficial interest may be a foreign corporation or state-owned enterprise that would be politically sensitive.

---

## Sources and further reading

### Official and regulatory
- [Office of the Commissioner of Lobbying of Canada](https://lobbycanada.gc.ca/en/) — The federal body that administers Canada's Lobbying Act, enforces the Lobbyists' Code of Conduct, and maintains the public Registry of Lobbyists
- [Registry of Lobbyists — Advanced Search](https://lobbycanada.gc.ca/app/secure/ocl/lrs/do/advSrch) — Searchable database of all federal registrations and monthly communication reports; supports filtering by client, lobbyist, subject matter, government institution, and date
- [Lobbying Disclosure Act (LDA) Reports — LDA.gov](https://lda.senate.gov/system/public/) — US Senate Office of Public Records database of LD-1 registrations, LD-2 quarterly activity reports, and LD-203 political contribution disclosures (note: the legacy lda.senate.gov domain is transitioning to lda.gov after June 2026)
- [How to Register and Report — Office of the Commissioner of Lobbying](https://lobbycanada.gc.ca/en/registration-and-compliance/how-to-register-and-report-your-lobbying-activities/) — Canada's official guidance on registration timelines, what triggers disclosure, and which officials count as designated public office holders

### Practitioner and public interest
- [OpenSecrets — Federal Lobbying Data](https://www.opensecrets.org/federal-lobbying) — Aggregated LDA data searchable by client, firm, and issue; tracks total lobbying spend by industry and organization for every year since 1998
- [GIJN Guide to Investigating Foreign Lobbying](https://gijn.org/resource/guide-investigating-foreign-lobbying/) — Global Investigative Journalism Network guide to tracing foreign lobbying relationships, including how to use FARA filings in the US and equivalent registers in other jurisdictions

### Journalism resources
- [IRE Resource Center](https://www.ire.org/resources/) — Tipsheets and guides from Investigative Reporters and Editors covering lobbying records, revolving-door research, and cross-referencing lobby registrations with campaign finance data

**Notes on unsourced claims:** Cooling-off period durations cited in the red flags section (two years for Canadian ministers; five years for designated public office holders; two years for US members of Congress; one year for senior executive branch officials) reflect the law as of 2024 and are subject to legislative change. Always verify current periods with the relevant registry or ethics body before publishing.
