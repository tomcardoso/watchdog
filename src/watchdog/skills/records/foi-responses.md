# Domain knowledge — Freedom of information responses

This skill is loaded by `/ingest` when the document type is a freedom of information or access to information response package, severance log, exemption index, or related disclosure record.

Apply this knowledge in addition to the standard extraction process. It tells you what to look for, what terminology means, and what patterns are worth flagging.

---

## Document types covered

- Access to information / freedom of information response packages
- Exemption indexes and severance (redaction) logs
- Informal disclosure packages
- Proactive or routine disclosure releases
- Complaint and investigation decisions (information commissioner, ombudsman, or equivalent oversight body)
- Requests-in-progress logs and disclosure summaries
- In Canada: Federal ATI (Access to Information) response packages; provincial FOI packages; municipal MFIPPA responses (Ontario)
- In the US: FOIA (Freedom of Information Act) response packages; state-level public records responses
- In the UK: Freedom of Information Act response packages
- In Australia: FOI Act response packages (federal and state)

---

## Always-present fields to extract

| Field | What to look for |
|-------|-----------------|
| **Institution** | Which government body produced the records |
| **Request number** | The unique identifier assigned to the request |
| **Request date** | When the request was filed |
| **Response date** | When the institution responded |
| **Request description** | What was asked for — the exact wording matters |
| **Number of pages released** | Total pages disclosed (full or partial) |
| **Number of pages withheld** | Total pages not disclosed |
| **Exemptions claimed** | Which statutory exemptions were invoked to withhold information |
| **Requester** | Who filed the request |
| **Decision-maker** | The access coordinator or analyst who signed the response |
| **Extension claimed** | Whether and why the institution extended the statutory deadline |

---

## Red flags — what to look for

### Volume and timing

- **High severance rate** — if more than 30–40% of pages are withheld, look at which exemptions are claimed. A blanket use of cabinet confidence, third-party information, or similar exemptions on records that appear routine is worth challenging.
- **Time taken far beyond the statutory limit** — statutory deadlines vary (30 days federally in Canada and Australia; 20 working days in the UK; 20 business days under US federal FOIA). A response that arrives six to eighteen months late suggests either a backlog or deliberate delay on a politically sensitive file.
- **Response timed to a political event** — institutions sometimes release sensitive records on a Friday afternoon, during a busy news cycle, or just before a legislative recess. Check the response date against the calendar.
- **Substantively identical requests returned with different severance levels** — if the same records were released to a different requester with less redaction, note the discrepancy.

### Exemptions and redactions

- **Cabinet or executive privilege claimed on operational records** — absolute exemptions for cabinet deliberations are appropriate for genuine policy decisions; their use on documents that appear to be operational may indicate over-claiming. Note: in some jurisdictions, this exemption cannot be reviewed by the oversight body.
- **Third-party / commercial confidentiality over-claimed** — often overclaimed by institutions afraid of complaints from third parties. Check whether the third party was given the opportunity to object, and whether the information is genuinely commercial.
- **"Not responsive" designation** — records within the date range declared not relevant to the request. This can be legitimate or can be used to exclude inconvenient documents.
- **Withheld by full page vs. redacted within a page** — a page withheld in full under a single exemption may be hiding less than a page with surgical redactions of specific names or figures.
- **Consecutive page numbers with a gap** — e.g. pages 1–17, then 19–45. Page 18 was withheld or severed. Note the gap and the exemption claimed.
- **Blank pages and separators** — institutions sometimes include blank pages as separators that inflate the page count. Actual content page count may be significantly lower.

### Document content

- **Email threads with missing participants** — a chain that begins mid-conversation, or where sender and recipient fields are redacted but the body is not, suggests the correspondent is significant.
- **Dates and times redacted** — unusual; may indicate the timing itself is sensitive (e.g., a decision made the day before a public announcement).
- **Subject lines redacted but body released** — the subject may reveal what a series of emails is about even when the body is partially redacted.
- **"See attached" with no attachment** — an email referencing an attachment where none was released. This is a gap to follow up on.
- **Coordinated messaging or talking points** — communications that appear to have been drafted centrally and distributed to multiple officials are often found in FOI packages; they reveal the government's communications strategy.

---

## Jurisdiction terminology

| Term | Jurisdiction | Meaning |
|------|-------------|---------|
| **ATI / ATIP** | Canada | Access to Information (and Privacy) — the federal access regime under the Access to Information Act |
| **FOI** | General | Freedom of Information — used in provincial, state, and international contexts |
| **FIPPA / MFIPPA** | Canada (Ontario) | Freedom of Information and Protection of Privacy Act / Municipal equivalent |
| **FOIA** | US | Freedom of Information Act — the US federal access statute |
| **FoIA** | UK | Freedom of Information Act 2000 — the UK access statute |
| **Severance** | Canada | Redaction of portions of a document before release (called "redaction" in most other jurisdictions) |
| **Exemption** | Universal | A statutory ground for withholding information |
| **Exclusion** | Canada | Information entirely outside the scope of the Act (e.g., Cabinet confidences) — unlike exemptions, exclusions cannot be reviewed by the Information Commissioner |
| **Section 21 / Third-party information** | Canada | Exemption for commercial information belonging to a third party |
| **Section 69 / Cabinet confidence** | Canada | Absolute exemption for Cabinet deliberations — not reviewable by the Information Commissioner |
| **Public interest test** | UK / Australia | Many exemptions in these jurisdictions require balancing the public interest in disclosure against the interest in withholding |
| **Proactive disclosure** | Universal | Routine publication of certain government records without a specific request |
| **Information Commissioner** | Canada / UK / Ireland | The independent oversight officer who reviews refusals, delays, and inadequate responses |

---

## Relationships to extract from FOI responses

1. **Person → Institution**: Requester (with request number and date), Decision-maker (access coordinator)
2. **Document → Exemption**: Specific exemptions claimed on specific pages or document types
3. **Institution → Institution**: Inter-departmental communications (reveals which agencies were consulted)
4. **Person → Person**: Email correspondents (even when names are redacted, roles and titles often remain)
5. **Institution → Event**: Response timing relative to political or policy events

---

## What investigators typically miss

1. **The covering letter** — the formal response letter lists every exemption claimed and gives page counts. Read it in full before reading the records; it is a map of what is missing.
2. **Metadata in document headers and footers** — even when body text is severed, headers and footers often retain document titles, dates, classification levels, and author names.
3. **The request description itself** — the institution's paraphrase of your request (in the covering letter or the disclosure summary) sometimes reveals how narrowly they interpreted it. A narrower interpretation means fewer records.
4. **Attachments to emails** — emails are often released; their attachments are often not. Note every instance of a referenced attachment that is absent.
5. **Proactive disclosure cross-reference** — contracts, hospitality, and travel released proactively may overlap with or contradict records released in a FOI package. Compare them.
6. **Consultation records** — many FOI packages contain records of the institution consulting with other departments or the central government before making decisions. These show who was involved in the decision to withhold.
7. **The online disclosure database** — many jurisdictions maintain public logs of completed requests. Comparing the database summary to the actual release can reveal inconsistencies or missing records.

---

## Sources and further reading

### Official and regulatory
- [Access to Information Manual — Treasury Board of Canada Secretariat](https://www.canada.ca/en/treasury-board-secretariat/services/access-information-privacy/access-information/access-information-manual.html) — The federal government's operational guide for institutions handling access to information requests under the Access to Information Act
- [Access to Information Act: Plain Language Guide to Exemptions and Exclusions](https://www.canada.ca/en/treasury-board-secretariat/services/access-information-privacy/aia-plain-language-guide.html) — Treasury Board Secretariat guide explaining what each exemption and exclusion means in plain language
- [Office of the Information Commissioner of Canada](https://www.oic-ci.gc.ca/en) — The federal oversight body that investigates complaints about access to information requests made to federal institutions; publishes decisions and annual reports
- [FOIA.gov](https://www.foia.gov/) — The US government's central portal for the Freedom of Information Act, including agency contacts, request status tracking, and annual statistics

### Practitioner and public interest
- [Global Right to Information Rating (RTI Rating)](https://www.rti-rating.org/) — Comparative scoring of national access to information laws by the Centre for Law and Democracy and Access Info Europe; useful for benchmarking a jurisdiction's legal framework
- [Access Info Europe](https://www.access-info.org/) — European human rights organisation that advocates for and monitors access to information laws; publishes comparative research and the Legal Leaks toolkit for journalists

### Journalism resources
- [MuckRock](https://www.muckrock.com/) — Nonprofit news and transparency platform that files, tracks, and publishes public records requests in the US; searchable database of past requests and responses
