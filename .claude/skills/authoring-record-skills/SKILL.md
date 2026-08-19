---
name: authoring-record-skills
description: How to write or extend a Watchdog record (domain) skill — where skill files live, the required section-by-section structure, red-flag authoring rules for the single-pass extractor, and the questions to ask the user before starting. Use whenever creating or editing a file under src/watchdog/skills/records/.
---

# Adding new record skills

## Where skills live

Record (domain) skill files live in `src/watchdog/skills/records/`. They are plain markdown and **global** — the ingest orchestrator reads them directly from the package via `watchdog.skills_catalog` (they are no longer copied into each vault). No code changes are needed when adding one. A user can add their own skills in `~/.watchdog/skills/records/`, which the catalog merges in (a user skill overrides a package skill of the same name). See DECISIONS D21.

## Standard structure

A blank template is at `src/watchdog/skills/records/_template.md` — copy it as the starting point for any new skill. Files starting with `_` are excluded from the catalog.

Every skill file should follow this structure in order:

0. **Frontmatter** — a YAML block with a `description:` line. This one line is the skill's entry in the classifier index, so make it a precise summary of the document types covered. (If omitted, the index falls back to the first sentence of the intro.)
1. **Intro paragraph** — one or two sentences explaining when this skill is loaded by Watchdog. Name the document types that trigger it, and note any sibling skills that own adjacent document types. This scope sentence is the only framing a skill carries — extraction-behaviour instructions (apply-in-addition, non-exhaustive field lists, no-external-lookups) live once in `src/watchdog/prompts/extract_instructions.md`, not in skill files.
2. **Document types covered** — a bulleted list of the specific document types the skill applies to. This is the one section where it is acceptable to list jurisdiction-specific document names (since those are the literal names of the documents). Group by jurisdiction if there are many.
3. **Fields to extract table** — a two-column table (`Field` | `What to look for`) listing fields expected in most documents of this type, or fields that are high-value whenever present. The table goes directly under the heading with no preamble — the extraction prompt already tells the model to extract listed fields even when not prominent and to treat the list as a floor, not a ceiling.
4. **Red flags section** — the most important section. Use sub-headings to group related red flags. Each red flag should be a bolded label followed by a sentence or two explaining what to look for and why it matters. Write for pattern recognition, not just field extraction. The only reader of this section is the extractor — one model pass over a single document plus a digest of entities already in the vault, with no web, databases, or prior knowledge. Every red flag must reduce to one of three moves: **capture a stated pattern** the model can see in the document, **compare against the entity digest** where both sides are stated (a contradiction needs two explicit claims — it cannot fire on silence), or **log a lead** for a human to check. A flag that requires knowing who someone is, proving an absence ("undisclosed", "not filed", "failed to"), or looking something up is not one the extractor can act on — move that insight to *What investigators typically miss* (item 7).

   These four failure modes recur in almost every first draft — name and convert each rather than delete it:
   - **Counting or trend across documents** ("repeated across contracts", "a pattern of complaints", "sustained over several years", "compare across decisions"). The extractor sees one document, so it cannot count or spot a trend. Narrow the flag to what a single document shows (a filing that itself tabulates several years or entries *is* fair game), reframe it as a **digest comparison** that fires only when the other instance is already a vault entity, or move the pattern to *What investigators typically miss*.
   - **External reputation or benchmark knowledge** ("a known bulletproof host", "above the industry average", "a tax-advantaged jurisdiction", "an unrated reinsurer"). The model holds no such list. Capture the stated value and **log a lead** to benchmark or verify it.
   - **Editorial or reliability judgement** ("this source is less reliable", "a reputable outlet", "treat with caution"). The extractor records facts, it does not grade them. Rewrite as an extraction action — *record* the anonymous source's stated characterisation, *note* that a claim rests on a single source — and move any residual judgement to *What investigators typically miss*.
   - **Vague thresholds** ("recent", "shortly after", "a significant event"). Give a concrete window ("within 12 months") or tie the flag to a date stated in the document.

   The default salvage is almost never deletion: keep the insight and end the flag with the move it actually supports — "record X; log a lead to check Y." That preserves the veteran's instinct while staying inside what one pass can do.
5. **Terminology table(s)** — one or more two-column tables (`Term` | `Meaning`) covering jargon a journalist would encounter. If the terminology varies significantly by jurisdiction, use a three-column table (`Term` | `Jurisdiction` | `Meaning`) or separate tables per jurisdiction.
6. **Relationships to extract** — a numbered list of entity relationships the skill should produce (e.g. `Person → Company: Director`). Use the `→` notation.
7. **What investigators typically miss** — a numbered list of the specific things experienced journalists often overlook when reading this document type — as many as the type genuinely has, not a fixed count. Be concrete and specific.
8. **Sources and further reading** — three subsections: **Official and regulatory** (government agencies, regulators, FATF, OECD, accounting standards bodies), **Practitioner and public interest** (law firm guides, NGO reports, public interest organizations), and **Journalism resources** (publicly accessible tipsheets, press freedom organizations). Omit a subsection entirely if there is nothing worth citing. End with a **Notes on unsourced claims** paragraph for any red flag claims that could not be traced to a specific source — these are flagged for editorial review, not silently included as fact. Every claim in the red flags section should be traceable to at least one source in this section.

## Authoring principles

- **Jurisdiction-agnostic by default.** Lead with principles and patterns that apply anywhere. Specific jurisdictions are examples, not the default frame. A journalist in Brazil or Germany should find the skill useful.
- **Jurisdiction-specific terminology tables are valuable** — but position them clearly as jurisdiction guides, not as the primary content. The always-present fields and red flags sections must be universal.
- **The red flags section is the most important.** This is where the skill earns its value. Think about what a twenty-year veteran investigative journalist would notice that a first-year reporter would miss.
- **Write red flags for the extractor; deeper context for the human.** The red flags section is read by a single-pass model with no outside knowledge, so each flag must be something it can see in the document or check against the entity digest where both sides are stated. Insights that require recognising a person, proving a negative, or consulting an outside source belong in *What investigators typically miss*, which only the journalist reads.
- **Write for a smart investigative journalist, not a specialist.** Assume the reader knows how journalism works but may not know the specific document type deeply. Explain jargon; don't assume it.
- **Be specific.** "Look for unusual transactions" is useless. "A property transferred three or more times in 12 months may be involved in title fraud, mortgage fraud, or money laundering" is useful.

## Before writing a new skill, ask the user

1. What document type are you working with? (Get a sample if possible.)
2. What jurisdiction(s) are most common for your work? (This shapes the terminology table.)
3. Are there existing skills that overlap? (Check `src/watchdog/skills/records/` first — some document types are covered from a related angle by an existing skill.)

If the new skill would overlap significantly with an existing one, consider extending the existing skill rather than creating a new file.
