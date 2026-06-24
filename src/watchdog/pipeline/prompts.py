"""Prompt builders for the orchestrator's model tasks (#118).

Instructions ported from the retired skills (watchdog-ingest-subagent.md,
watchdog-ingest-section-subagent.md, watchdog-post-ingest-subagent.md). model_client
prepends a generic JSON-only system prompt and appends the schema; these builders supply
the task-specific instructions + data as the `prompt`.
"""

import json


def build_classify_prompt(doc_excerpt: str, index_text: str) -> str:
    return (
        "Identify this document's type and choose the single closest-matching domain skill.\n\n"
        "Return the skill's filename (e.g. \"corporate-filings.md\"). If nothing clearly matches, "
        "use \"general-records.md\". Also return a short descriptive `document_type` "
        "(e.g. \"Annual Report\", \"Affidavit\", \"Title Transfer\").\n\n"
        f"Available skills (one line each):\n{index_text}\n\n"
        f"Document excerpt:\n{doc_excerpt}"
    )


_EXTRACT_INSTRUCTIONS = """\
The full text of this document is preserved in the vault, so do NOT restate or summarize it. \
Your job is to produce a journalist's working notes: (1) the material FACTS worth writing down, \
and (2) the GRAPH of entities and their relationships. Each fact is written ONCE and then tagged \
so it can be filed under the entities it concerns and, if it is a datable occurrence, onto the \
timeline.

KEY FACTS — `document.key_facts` is the heart of the extraction. Capture the investigatively \
material facts a reporter would jot down: who, amounts, dates, identifiers, addresses, ownership, \
obligations, decisions, occurrences. Each fact is an OBJECT:
- `fact`: one factual sentence, in your own words.
- `entities`: the ids of the entities this fact is about (usually one or two — the people, \
companies, addresses, or cases it concerns). Omit if the fact is about no specific entity.
- `date`: set ONLY when the fact is itself a datable occurrence (something that happened, was \
decided, or changed on a specific date) — this is what places it on the timeline. Omit for facts \
that merely mention a date or have no single date (a ratio, a balance, a structural fact).
- `page`, `confidence`: as below.
- `quote`: an optional verbatim source sentence — include ONLY when the exact wording is itself \
significant or quotable (an admission, a precise figure, distinctive language). Usually omit it.

How many facts: as many as the document has material facts, and no more — a dense order may have \
fifteen, a routine form two. Do NOT pad to a quota. Let MATERIALITY decide: omit boilerplate, \
procedural recitation, jurisdictional/standard-form language, and reasoning or analysis that does \
not establish a fact (e.g. recited case-law or argument). The loaded DOMAIN SKILL tells you what \
matters most for this document type — lean on it. When in doubt, err toward capturing a hard fact \
(a name, date, figure, address) rather than dropping it; err toward dropping prose and reasoning.

ENTITIES — the graph. Use EXISTING_ENTITIES for deduplication — match on name or any alias (OCR \
errors are common; be generous). For each entity:
- `id`: the existing id from EXISTING_ENTITIES if matched, otherwise a new kebab-case slug.
- `match_id`: set to the matched entity's id if this matches an existing entity; OMIT entirely \
for new entities (do not set null or "").
- `name`: the canonical full name as it appears most completely.
- `type`: Person / Company / Address / Property / CourtCase / Transaction / or a new type if apt.
- `aliases`: every other name or abbreviation used in this document.
- `roles`: relationships to other entities, each an OBJECT with relationship/target_id/page/\
confidence/date_range — never a plain string. Identify the target by `target_id` only; its name \
and type are filled in automatically, so do not emit them.
Create an entity for anything a fact, a role, or the timeline needs to refer to — including \
incidental actors (counsel, a clerk) named in a fact. Do NOT write a summary or per-entity prose: \
who the entity is follows from the facts tagged to it.

Confidence (facts, roles): emit `confidence` ONLY when it is `medium` (one inference), `low` \
(multi-statement inference), or `disputed` (contradicts the vault). OMIT it entirely when the \
claim is directly stated — absent means `high`, which is the default. Never upgrade a claim past \
its weakest element. Likewise omit `page` when there is no page marker (don't emit null), and omit \
empty arrays rather than emitting `[]`.

CONTRADICTION CHECK — for each entity that matched an EXISTING_ENTITIES entry, compare key dates, \
roles, and relationships in this document against that entry's recorded roles and claims. Flag a \
material discrepancy only when both sides are high or medium confidence and you are confident it \
is genuine — this is the only verification step; any callout is saved as-is. Put each as a string \
in that entity's `contradictions` array, formatted exactly:
> [!contradiction] <short label>
> - **<existing value>** — [[documents/<slug>|<title>]], p. <n> (confidence: <level>)
> - **<new value>** — [[documents/<new-slug>|<title>]], p. <n> (confidence: <level>)
Do not flag low-confidence differences, trivial name variations, or contradictions already present.

Also produce:
- `document.summary`: ONE or two sentences orienting the reader — what this document is and why it \
exists. Not a recap of the facts (those are in key_facts and the full text); just enough to know \
what you are looking at.
- `morgue_entity_id`: the kebab-case id of the entity this document is primarily *about* (debtor \
for a bankruptcy, company for an annual report, defendant for a court order).
- `morgue_document_type`: a type slug like annual-report, court-order, bankruptcy-filing.
- `scratchpad`: tight, high-signal markdown notes for the briefing — Key figures, Leads, \
Contradictions, Chronological note. Only what a reporter would jot down; omit empty sections.

Cite page numbers (from the <!-- PAGE N --> markers) wherever possible; use null when a section \
has no page markers."""


def build_extract_prompt(*, pages_text: str, existing_entities: list, skill_text: str,
                         sidecar: str | None, sha256: str, filename: str,
                         original_path: str | None, page_count: int,
                         brief: str | None) -> str:
    parts = [_EXTRACT_INSTRUCTIONS, ""]
    parts.append(f"Set document.sha256 = {sha256!r}, document.filename = {filename!r}, "
                 f"document.original_path = {original_path!r}, document.page_count = {page_count}.")
    if brief:
        parts.append(f"\nINVESTIGATION BRIEF (orient extraction toward this):\n{brief}")
    parts.append(f"\nDOMAIN SKILL ({'matched' if skill_text else 'none'}):\n{skill_text or '(none)'}")
    parts.append(f"\nEXISTING_ENTITIES (for dedup + contradiction check):\n"
                 f"{json.dumps(existing_entities, ensure_ascii=False)}")
    if sidecar:
        parts.append(f"\nSIDECAR (source/obtained metadata):\n{sidecar}")
    parts.append(f"\nDOCUMENT TEXT:\n{pages_text}")
    return "\n".join(parts)


def build_section_prompt(*, pages_text: str, existing_entities: list, skill_text: str,
                         carry_forward: str, section_label: str, is_first: bool,
                         sha256: str, filename: str, original_path: str | None,
                         page_count: int) -> str:
    parts = [
        f"Extract ONE page-range section ({section_label}) of a large document. Same rules as a "
        "full extraction (entities, confidence, roles-as-objects, contradiction callouts), but "
        "scoped to this section's pages. Overlapping content may repeat from the adjacent section "
        "— that is expected; the merge deduplicates. Do NOT set match_id (ids are canonical).",
        "",
        _EXTRACT_INSTRUCTIONS,
        "",
    ]
    if is_first:
        parts.append("This is SECTION 1: fill document metadata (title, document_type, "
                     "date_of_document, page_count, sha256, filename, original_path) and the "
                     "morgue_entity_id / morgue_document_type fields.")
        parts.append(f"Set document.sha256 = {sha256!r}, document.filename = {filename!r}, "
                     f"document.original_path = {original_path!r}, document.page_count = {page_count}.")
    else:
        parts.append("This is a LATER section: omit document metadata and morgue fields; supply "
                     "entities + document.key_facts + document.summary for this section only.")
    parts.append("Put salient, high-signal notes for the briefing in `observations`.")
    if carry_forward:
        parts.append(f"\nCARRY-FORWARD (entities/observations from earlier sections — reuse these "
                     f"ids):\n{carry_forward}")
    parts.append(f"\nDOMAIN SKILL:\n{skill_text or '(none)'}")
    parts.append(f"\nEXISTING_ENTITIES:\n{json.dumps(existing_entities, ensure_ascii=False)}")
    parts.append(f"\nSECTION TEXT:\n{pages_text}")
    return "\n".join(parts)


def build_synthesis_prompt(bundle: dict) -> str:
    return (
        "Synthesize Summary and Analysis prose for each entity below. Each appears in two or more "
        "documents across the investigation, so reconcile all of its fragments with its carried "
        "prose into a single coherent account — do not concatenate per-document sentences, and do "
        "not lose specific detail (titles, figures, relationships) any source established. Keep the "
        "`summary` SHORT: one to three paragraphs, scaled to the entity's complexity — a paragraph "
        "for a simple recurring actor, up to three for a central figure with a tangled history. "
        "Weight the full body of evidence: an entity established across many documents is not "
        "redefined by a new passing mention — fold a minor new reference in without letting it "
        "reshape an account the prior sources already settled. Where sources genuinely conflict, "
        "prefer the higher-confidence one and note the uncertainty; do not invent a resolution. "
        "`analysis` is the investigative narrative (patterns, significance, open threads) — empty "
        "string if nothing beyond the summary. NEVER include [!contradiction] callouts; "
        "contradictions live in their own section.\n\n"
        "The fragments contain structured claims, some carrying a verbatim `quote`. Compose the "
        "prose from the claims in your own words; weave a verbatim quote into the prose ONLY in "
        "exceptional cases where the quote itself is unusually strong or important — by default, "
        "do not quote.\n\n"
        f"Entities:\n{json.dumps(bundle.get('entities', []), ensure_ascii=False)}"
    )


def build_briefing_prompt(*, brief: str | None, results: list, scratchpads: list,
                          neardup_alerts: list, contradiction_flags: list) -> str:
    return (
        "Write the post-ingest briefing for this batch. Draw narrative detail from the scratchpads; "
        "use the result blocks for machine-readable metadata. Provide: a one-sentence "
        "investigation_status; what_was_ingested (one line per file); new_entities; connections to "
        "existing vault entities (what the connection is and why it matters); actionable leads "
        "(open questions, contacts, missing documents, FOI ideas — orient toward the brief if "
        "given); anomalies (shared addresses, unexpected roles, disproportionate transactions, "
        "highly-connected entities with no documented relationships); emerging_patterns; "
        "open_questions.\n\n"
        f"INVESTIGATION BRIEF:\n{brief or '(none)'}\n\n"
        f"RESULTS:\n{json.dumps(results, ensure_ascii=False)}\n\n"
        f"NEAR-DUP ALERTS:\n{json.dumps(neardup_alerts, ensure_ascii=False)}\n\n"
        f"CONTRADICTION FLAGS:\n{json.dumps(contradiction_flags, ensure_ascii=False)}\n\n"
        f"SCRATCHPADS:\n" + "\n\n---\n\n".join(scratchpads)
    )


def build_timeline_dedup_prompt(date: str, events: list[dict]) -> str:
    return (
        f"These timeline events are all dated {date}. Some may describe the same real-world occurrence "
        "in different words. Return the deduplicated list of FULL event objects — remove semantic "
        "duplicates (keep the more precise wording) and preserve every genuinely distinct event. "
        "Keep each kept object's other fields (page, confidence, source_sha256) intact.\n\n"
        f"Events:\n{json.dumps(events, ensure_ascii=False)}"
    )
