# Domain knowledge — Legislature transcripts

This skill is loaded by `/ingest` when the document type is a Hansard transcript, committee transcript, parliamentary debate record, legislative assembly record, or similar verbatim record of legislative proceedings.

Apply this knowledge in addition to the standard extraction process. It tells you what to look for, what terminology means, and what patterns are worth flagging.

---

## Document types covered

- Verbatim transcripts of parliamentary or legislative debates
- Committee or select committee hearing transcripts
- Question period or question time transcripts
- Budget debate and estimates transcripts
- Bill readings and debate transcripts
- Senate or upper house debate transcripts
- In Canada: House of Commons Hansard; Senate of Canada debates; provincial and territorial legislative assembly Hansards; Standing committee and special committee transcripts
- In the US: Congressional Record; Congressional hearing transcripts (House and Senate committees)
- In the UK: UK Hansard (House of Commons and Lords); select committee transcripts
- In Australia: Hansard (House of Representatives and Senate); Senate Estimates transcripts

---

## Always-present fields to extract

| Field | What to look for |
|-------|-----------------|
| **Date** | Date of the sitting |
| **House / chamber** | The chamber or committee (lower house, upper house, specific committee) |
| **Volume and number** | The Hansard or official record volume and issue number (for citation) |
| **Speaker name** | Every named speaker in the relevant passage |
| **Speaker role** | Party, constituency/province, and role (minister, opposition, backbencher, presiding officer) |
| **Subject / topic** | The debate topic, question, or bill under discussion |
| **Bill reference** | Bill number and title if applicable |
| **Committee name** | For committee transcripts: the full committee name |
| **Witnesses** | For committee hearings: names, organizations, and roles of witnesses |
| **Motions** | Any formal motions moved, seconded, and voted on |
| **Vote record** | How members voted, where a recorded division was called |

---

## Red flags — what to look for

### Question period / question time

- **Evasive answers** — a minister who answers a question about Topic A with a prepared statement about Topic B has deflected, not answered. Note the question asked and the answer given.
- **Shifting explanations across multiple days** — a minister who gives one explanation on Monday and a different explanation on Thursday. Parliamentary records allow precise comparison.
- **Claims made in the chamber that contradict documents** — a minister who states that no such directive was issued, when a freedom of information release shows it was. The parliamentary statement is the accountability record.
- **Opposition questions that telegraph a coming story** — parties often use question time to put a minister on the record before a story breaks. If you have a document, check whether it was raised in the chamber and what the minister said.

### Committee testimony

- **Witness testimony that contradicts departmental position** — a departmental official who testifies before a committee in a way that contradicts the minister's public statements.
- **Redacted documents tabled in committee** — documents tabled in a committee proceeding are part of the parliamentary record even if redacted. The existence of the redaction is itself informative.
- **Committee reports with dissenting or supplementary opinions** — like royal commission dissents, these often contain the sharpest analysis.
- **Witnesses who refuse or are prevented from answering** — a witness who says "I'd have to get back to the committee on that" or is instructed by counsel not to answer. Track whether the promised follow-up actually arrived.
- **In camera (private) committee sessions** — committees sometimes go in camera to discuss sensitive matters. The public record shows a gap; the reason given for going in camera is itself informative.

### Votes and divisions

- **Recorded division results** — who voted for and against a bill or motion. Party-line votes are routine; deviations from the party line are significant.
- **Bills passed without debate** — a bill passed at all stages without substantive debate or committee study may have been rushed through with limited scrutiny.

### Bill and debate content

- **Assurances given in debate** — a minister who states in debate that a provision means something specific; courts sometimes look to legislative debates to resolve statutory ambiguities.
- **Amendments rejected** — an amendment moved in committee that was voted down; the record of what was proposed and rejected is relevant when the enacted version is later found to have a gap.
- **Omnibus bills** — large bills that combine many unrelated measures limit committee scrutiny. Note how many distinct policy areas a bill covers and how much debate time was allocated.

---

## Jurisdiction terminology

| Term | Jurisdiction | Meaning |
|------|-------------|---------|
| **Hansard** | UK / Canada / Australia | The verbatim official record of parliamentary proceedings — named after the original printer of UK Parliament's debates |
| **Question Period (QP)** | Canada | The 45-minute daily oral question period in the House of Commons and most provincial legislatures |
| **Question Time** | UK / Australia | The equivalent of Question Period in the UK House of Commons and Australian Parliament |
| **Standing committee** | Universal | A permanent committee organized by subject area (Finance, Justice, Health, etc.) |
| **Special committee** | Universal | A temporary committee struck for a specific purpose |
| **Select committee** | UK / Australia | A parliamentary committee of inquiry — equivalent to a standing or special committee in Canada |
| **Recorded division** | Universal | A formal vote where individual members' votes are recorded |
| **Order Paper** | Universal | The daily agenda of business before the chamber |
| **Estimates** | Canada / UK / Australia | The government's spending plans — reviewed by committee to hold departments accountable |
| **Private member's bill** | Universal | A bill introduced by a backbench legislator, not by the government |
| **Royal assent** | Canada / UK / Australia | The head of state's or governor general's signature that enacts a bill into law |
| **Prorogation** | Canada / UK / Australia | Ending a parliamentary session — kills all bills that have not received royal assent |
| **Congressional Record** | US | The official record of proceedings and debates in the US House and Senate |
| **Markup** | US | The committee process of amending and approving a bill before it goes to the full chamber |

---

## Relationships to extract from legislative transcripts

1. **Person → Statement**: Who said what, in which chamber or committee, on what date (with Hansard or official record citation)
2. **Person → Vote**: How each member voted on each recorded division
3. **Person → Witness/Committee**: A witness who appeared before a committee (with organization and date)
4. **Statement → Document**: A statement in question time or committee that references a specific document or fact
5. **Bill → Stage**: Each reading, committee referral, amendment, and final vote

---

## What investigators typically miss

1. **The official citation** — every statement in Hansard is precisely locatable by date, page, and column. Always record the full citation so that statements can be independently verified.
2. **Committee evidence vs. main chamber debates** — committee transcripts are published separately from main chamber Hansard. Committee evidence, including witness testimony and document tabling, is often more detailed and more candid than question time exchanges.
3. **The written question and answer process** — members can submit written questions to the government in many parliamentary systems; the answers are published in the official record and often contain more detailed statistics and admissions than oral answers. These are easy to miss because they appear at the end of a day's proceedings.
4. **Order Paper notices** — notices of questions, motions, and bills placed on the Order Paper but not yet reached may signal what an opposition party knows or intends to raise.
5. **Unedited vs. official transcripts** — some legislatures publish a draft or "Blues" transcript before the corrected official version. Comparing them can reveal changes made after the sitting.
6. **Statements made during debate that conflict with later departmental action** — a minister who commits to a specific course of action during debate and then takes a different course. The parliamentary record is the proof of the original commitment.

---

## Sources and further reading

### Official and regulatory
- [House of Commons Debates (Hansard) — ourcommons.ca](https://www.ourcommons.ca/documentviewer/en/house/latest/hansard) — The official verbatim record of House of Commons proceedings; the authoritative source for citations of what was said in the chamber
- [House of Commons — Open Data](https://www.ourcommons.ca/en/open-data) — Machine-readable XML and JSON datasets of Hansard transcripts, committee evidence, voting records, and bill information, available for bulk download and analysis
- [Hansard — UK Parliament](https://hansard.parliament.uk/) — The official report of all UK Parliamentary debates for both the House of Commons and House of Lords, searchable back to the early nineteenth century
- [LEGISinfo — Parliament of Canada](https://www.parl.ca/legisinfo) — Canada's official bill tracking system covering all bills from introduction through royal assent, including links to recorded divisions and parliamentary debate for each stage
- [Congress.gov — Library of Congress](https://www.congress.gov/) — US federal legislative portal providing the Congressional Record, committee hearing transcripts, and roll call votes

### Practitioner and public interest
- [OpenParliament.ca](https://openparliament.ca/) — Independent, non-governmental search tool for Canadian House of Commons Hansard transcripts back to 1994; provides topic summaries, MP profiles, and bill tracking in a more accessible interface than the official record
- [GovTrack.us](https://www.govtrack.us/) — Independent tracker of US congressional activity including voting records, bill progress, and member statistics; operated without government or party affiliation since 2004
