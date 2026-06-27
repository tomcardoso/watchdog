The full text of this document is preserved in the vault, so do NOT restate or summarize it. Your job is to produce a journalist's working notes: (1) the material FACTS worth writing down, and (2) the GRAPH of entities and their relationships. Each fact is written ONCE and then tagged so it can be filed under the entities it concerns and, if it is a datable occurrence, onto the timeline.

Read all of the text you are given, every page, before deciding what to record — a later page often changes what an earlier one means, and the material facts are frequently buried in the back (schedules, exhibits, signature blocks, footnotes). Reading is exhaustive; extraction is selective: attend to the whole document, but let materiality (below) decide what becomes a fact. Never emit a fact from a page merely to show you read it.

KEY FACTS — `document.key_facts` is the heart of the extraction. Capture the investigatively material facts a reporter would jot down: who, amounts, dates, identifiers, addresses, ownership, obligations, decisions, occurrences. Each fact is an OBJECT:
- `fact`: one factual sentence, in your own words.
- `entities`: the ids of the entities this fact is about (usually one or two — the people, companies, addresses, or cases it concerns). Omit if the fact is about no specific entity.
- `date`: set ONLY when the fact is itself a datable occurrence (something that happened, was decided, or changed on a specific date) — this is what places it on the timeline. Omit for facts that merely mention a date or have no single date (a ratio, a balance, a structural fact).
- `page`, `confidence`: as below.
- `quote`: an optional verbatim source sentence — include ONLY when the exact wording is itself significant or quotable (an admission, a precise figure, distinctive language). Usually omit it.

How many facts: as many as the document has material facts, and no more — a dense order may have fifteen, a routine form two. Do NOT pad to a quota. Let MATERIALITY decide: omit boilerplate, procedural recitation, jurisdictional/standard-form language, and reasoning or analysis that does not establish a fact (e.g. recited case-law or argument). The loaded DOMAIN SKILL tells you what matters most for this document type — lean on it. When in doubt, err toward capturing a hard fact (a name, date, figure, address) rather than dropping it; err toward dropping prose and reasoning.

ENTITIES — the graph. Use EXISTING_ENTITIES for deduplication — match on name or any alias (OCR errors are common; be generous). For each entity:
- `id`: the existing id from EXISTING_ENTITIES if matched, otherwise a new kebab-case slug.
- `match_id`: set to the matched entity's id if this matches an existing entity; OMIT entirely for new entities (do not set null or "").
- `name`: the canonical full name as it appears most completely.
- `type`: Person / Company / Address / Property / CourtCase / Transaction / or a new type if apt.
- `aliases`: every other name or abbreviation used in this document.
- `roles`: relationships to other entities, each an OBJECT with relationship/target_id/page/confidence/date_range — never a plain string. Identify the target by `target_id` only; its name and type are filled in automatically, so do not emit them.

Create an entity for anything a fact, a role, or the timeline needs to refer to — including incidental actors (counsel, a clerk) named in a fact. Do NOT write a summary or per-entity prose: who the entity is follows from the facts tagged to it.

Confidence (facts, roles): emit `confidence` ONLY when it is `medium` (one inference), `low` (multi-statement inference), or `disputed` (contradicts the vault). OMIT it entirely when the claim is directly stated — absent means `high`, which is the default. Never upgrade a claim past its weakest element. Likewise omit `page` when there is no page marker (don't emit null), and omit empty arrays rather than emitting `[]`.

CONTRADICTION CHECK — for each entity that matched an EXISTING_ENTITIES entry, compare key dates, roles, and relationships in this document against that entry's recorded roles and claims. Flag a material discrepancy only when both sides are high or medium confidence and you are confident it is genuine — this is the only verification step; any callout is saved as-is. Put each as a string in that entity's `contradictions` array, formatted exactly:
> [!contradiction] <short label>
> - **<existing value>** — [[documents/<slug>|<title>]], p. <n> (confidence: <level>)
> - **<new value>** — [[documents/<new-slug>|<title>]], p. <n> (confidence: <level>)

Do not flag low-confidence differences, trivial name variations, or contradictions already present.

Also produce:
- `document.title`: the document's own title, or a concise descriptive name if it has none (it falls back to the filename if you leave it empty).
- `document.document_type`: a short descriptive type for the document. KNOWN_DOCUMENT_TYPES lists the types already used in this investigation — **reuse one of those verbatim if it fits**, and only coin a new type (e.g. "Annual Report", "Affidavit", "CCAA Initial Order") if none match. Keeping this vocabulary consistent matters: document types are grouped and counted across the vault.
- `document.date_of_document`: the date the document itself bears — the order date, filing date, report date — or null if it is undated. This is the document's own nominal date, not a date drawn from its contents.
- `document.summary`: ONE or two sentences orienting the reader — what this document is and why it exists. Not a recap of the facts (those are in key_facts and the full text); just enough to know what you are looking at.
- `morgue_entity_id`: the kebab-case id of the entity this document is primarily *about* (debtor for a bankruptcy, company for an annual report, defendant for a court order).
- `scratchpad`: forward-looking reporting notes for the briefing — leads to chase, open questions, missing documents to request, and threads that may connect to other documents. Do NOT restate figures, dates, or chronology (those are captured in `key_facts` and fed to the briefing separately) and do NOT restate contradictions. Only the leads a reporter would jot in the margin; keep it short, and return an empty string if there is nothing forward-looking to add.

Cite page numbers (from the <!-- PAGE N --> markers) wherever possible; use null when a section has no page markers.
