# Domain knowledge — News stories and clippings

This skill is loaded by `/ingest` when the document type is a news article, press clipping, wire story, press release, or similar published or distributed text.

Apply this knowledge in addition to the standard extraction process. It tells you what to look for, what terminology means, and what patterns are worth flagging.

---

## Document types covered

- Newspaper articles (print and online)
- Wire service stories (CP, AP, Reuters, AFP)
- Magazine features and investigative stories
- TV and radio transcript excerpts
- Press releases (government, corporate, NGO)
- Media advisories and backgrounders
- Op-eds and columns (attributed)
- Retracted or corrected stories
- Translated foreign press articles

---

## Always-present fields to extract

| Field | What to look for |
|-------|-----------------|
| **Headline** | Exact headline as published |
| **Publication / outlet** | Name of the news organization |
| **Author(s) / byline** | Reporter or columnist name(s) |
| **Publication date** | Date published or broadcast |
| **Edition / URL** | Print edition section or web URL |
| **Wire service credit** | If redistributed: original wire and dateline |
| **Key claims** | Factual assertions central to the story |
| **Named sources** | Every named individual quoted or cited |
| **Anonymous sources** | Description of unnamed sources (e.g. "a senior official who was not authorized to speak") |
| **Documents cited** | Any documents the story references or is based on |
| **Corrections or retractions** | Whether the story has been corrected or retracted since publication |

---

## Red flags — what to look for

### Source assessment

- **Anonymous sources without clear description** — a story based on an unnamed "source" with no description of their role, access, or motivation is less reliable than one with a specific characterization.
- **Single-source claims** — major factual assertions supported by only one source (named or unnamed) are more vulnerable to error or manipulation than multi-sourced claims.
- **Sources with an apparent interest in the outcome** — a source who benefits from the story's publication may be using the reporter. Note the relationship between the source and the claim.
- **On-background government sources** — government officials who brief reporters on background ("a spokesperson for the minister, not authorized to speak publicly") are often providing self-serving characterizations. Weight accordingly.

### Story reliability

- **Press releases quoted without attribution or scrutiny** — a story that reproduces the main claims of a press release without independent verification is essentially PR.
- **Corrections and updates** — a story that has been corrected may have contained a significant factual error. Note the nature of the correction and whether it affects the story's central claims.
- **Retractions** — a fully retracted story should not be used as a source of fact. Note that the story existed and was later retracted.
- **Publication date relative to events** — a story that predates a key event provides the state of knowledge at that time; one published after may benefit from hindsight or additional sources.
- **Outlet reputation for the subject area** — a tabloid reporting on financial crime and a financial investigative outlet reporting on the same topic have different reliability profiles. Note the outlet type.

### Claims and context

- **Characterizations vs. facts** — distinguish between what a source said (direct quote), what a reporter characterizes (paraphrase), and what is independently established. All three appear in news stories but carry different evidentiary weight.
- **Government/company denials** — a denial in a news story is not proof of innocence, but its terms matter. A denial that says "we have never done X" is narrower than one that says "X is completely false."
- **Translations** — a translated foreign-language article may have introduced errors or lost nuance. Note when a story is a translation and from which language.

---

## How to use news clippings in an investigation

A news clipping is not primary evidence. It is:

1. **A lead** — it points toward documents, sources, and events worth pursuing directly.
2. **A timeline anchor** — a published story establishes that certain facts were known at a certain date. This is significant for what came before and after.
3. **A prior representation** — a quote from a company official in a news story is a prior representation that can be compared to later statements or documents.
4. **A source of named parties** — the individuals, companies, and addresses named in the story are extraction targets even if the story itself is not the primary source.

Never treat a news story as establishing a fact for which the story itself is the only source. Always ask: what primary document or firsthand source is this story based on, and can that be obtained directly?

---

## Terminology

| Term | Meaning |
|------|---------|
| **Byline** | The reporter's name as it appears in the published story |
| **Dateline** | The city and date at the start of a wire story, indicating where it was written |
| **Wire service** | A news agency that distributes stories to subscribing outlets (CP, AP, Reuters, AFP) |
| **On the record** | A source who can be identified by name |
| **On background** | A source who can be described by role but not identified by name |
| **Deep background** | A source whose information can be used but who cannot be identified or described |
| **Not for attribution** | Information that can be used but not attributed to the source |
| **Embargo** | A restriction on publishing until a specified time — common for government reports and corporate announcements |
| **Kill** | A decision by the publication not to run a story |
| **Correction** | A published acknowledgment that a specific fact in an earlier story was wrong |
| **Retraction** | A full withdrawal of a story because it was substantially wrong or could not be substantiated |
| **Right of reply** | The practice of giving subjects of a story the opportunity to comment before publication |

---

## Relationships to extract from news clippings

1. **Person → Outlet**: Reporter byline at publication
2. **Person → Claim**: What a named source said (with quote and date)
3. **Person → Document**: Document cited in the story (even if you don't have it)
4. **Story → Correction/Retraction**: Any subsequent correction or retraction
5. **Story → Event**: The event or document the story is based on (as the lead for obtaining primary records)

---

## What investigators typically miss

1. **The documents underlying the story** — if a story says "according to documents obtained by [outlet]," seek those documents directly. They may be available through ATI, court records, or the original source.
2. **Prior coverage of the same subject** — a current story is rarely the first. Searching for prior coverage of the same company, individual, or issue often reveals an earlier record of problems.
3. **The reporter as a source** — the reporter who wrote a story may have documents, sources, and context not in the published article. In an investigation, speaking to journalists who have already covered a subject is a research step.
4. **What the story doesn't say** — a press release or corporate announcement article often omits information that would complicate the narrative. The absence of certain facts (e.g., a company's regulatory history) is a gap, not a confirmation of a clean record.
5. **The original vs. the updated version** — online news stories are frequently updated after initial publication. The original version and the current version may differ significantly; the Internet Archive (Wayback Machine) preserves versions at specific crawl times.
6. **Foreign-language coverage** — a person or company that appears clean in English-language coverage may have significant coverage in their home-country press. Always search in the relevant language if there is a foreign connection.
