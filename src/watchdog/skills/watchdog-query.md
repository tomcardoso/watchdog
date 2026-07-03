# /watchdog-query — Answer a question from the vault

Answer the journalist's question using only information in this vault.

The question is: **$ARGUMENTS**

---

## How to answer

### 1. Parse the question

Identify:
- What entities are referenced (people, companies, addresses)?
- What time period, if any?
- What kind of relationship or fact is being asked about?
- Is this a lookup ("who is the director of X?"), a comparison ("which companies share address Y?"), a timeline ("when did Z first appear?"), or an analysis ("what's unusual about this transaction?")?

### 2. Gather evidence

Read the relevant vault files. Prioritise in this order:

1. **Registry/manifest.json** — lightweight index of every entity: `id`, `name`, `type`, `aliases`, `note_path`. Read this first to find which entities are relevant to the question. Match on name and all aliases.
2. **Entity notes** — read only the specific notes identified in step 1 (use the `note_path` field, append `.md`). Each note has a `## Summary`, `## Timeline`, `## Analysis`, and `## Relationships`.
3. **timeline.md** — global chronological view across all entities; use this for "when did X happen?" or "what happened in year Y?" questions
4. **Document notes** (`documents/*.md`) — for the source documents those entities appear in
5. **Briefings** (`briefings/*.md`) — for previous analysis that may be relevant

If the manifest doesn't surface the right entity by name, fall back to grep:
```bash
grep -ri "<search term>" entities/ documents/ --include="*.md" -l
```

**Semantic lane — `watchdog search`.** The manifest + grep above are entity-anchored and limited to `entities/`/`documents/` notes: perfect for "what do we know about Acme Corp?" but blind to *conceptual* or *passage-level* questions ("which documents describe a shell-company structure?", "find passages about the rezoning vote"), to wording the documents phrase differently, and to raw source text that never got promoted into a note. For those — or when manifest + grep don't surface a confident answer — reach for hybrid search over the source corpus:

```bash
watchdog search "<the question, or its key concept>" --json
```

The JSON has a `passages` array, each with `filename`, `page`, `text`, and `score`, ranked by meaning **and** exact terms (so an exact token like a case number or dollar figure still lands). The `text` is the citable span: cite it as `(<filename>, p. <page>)`, and open the matching `documents/<...>.md` note if you need surrounding context. Use this lane to *find* the right documents, then confirm against the entity/document notes — read the synthesized digest first (it's why the vault exists); don't synthesize from raw passages alone when a note already covers it. The `notes` array (synthesized prose) is there too, but prefer reading those notes directly. There's also an `exact` array — every literal occurrence of the query term/phrase across the full raw corpus text *and* every generated note (not just `entities/`/`documents/`), each with `kind`, `title`, `path`, `page`, and `text`; unlike `passages`/`notes` it isn't scored, just exhaustive. It's a broader net than the step-2 grep fallback (that one misses source-document text entirely) — reach for it when you need to confirm a name or term appears *somewhere*, not just find the most relevant passage.

### 3. Compose the answer

**Structure:**
- State the answer directly in the first sentence
- Support every claim with a citation: entity name, document title, page number
- If the vault contains conflicting information, surface the conflict — don't silently pick one
- If the vault does not contain enough information to answer, say so explicitly — do not speculate

**Citation format:**
> John Doe is listed as Director of Shell Co Ltd (Annual Report 2023, p. 3).

**If the answer requires combining information from multiple documents:**
> The address 123 Main St appears in two documents: the Shell Co corporate registration (p. 1) and the Smith Holdings annual report (p. 7). These documents have no other apparent connection.

### 4. Persist substantive answers to `queries/`

Investigations compound when explorations are written down instead of vanishing into chat. After composing the answer, **file it to `queries/`** so the work accumulates as the investigation grows.

**When to persist:** any answer that synthesises across documents, surfaces a connection, resolves or raises a question, or analyses a pattern. **Skip** trivial single-fact lookups ("who is the director of X?", "what address is on document Y?") — a page for those is noise, not knowledge.

**How:**
- Slug the question into a short topic: `who-controls-shell-co`, `123-main-st-connections`.
- If a `queries/<slug>.md` already covers the same question, **update** it — sharpen the answer, add newly-relevant documents, refresh `last_updated` — rather than create a near-duplicate.
- Otherwise create `queries/<slug>.md`:

```yaml
---
id: <slug>
question: <the question as asked>
type: Query
entities:
  - "[[entities/<type>/<id>|Entity Name]]"
documents:
  - "[[documents/<slug>|Document Title]]"
created: <today>
last_updated: <today>
---

## Answer

<The composed answer, every claim cited inline as in step 3. Preserve the citations — this page must stand on its own months from now.>

## Open questions

<Any gap the question revealed: a missing document, an unconfirmed relationship, an ambiguous identity. One sentence each. Omit the section if there are none.>

## Notes

<!-- Journalist annotations — never overwritten. -->
```

Then tell the journalist where it went: `Filed to queries/<slug>.md`.

**Graduate to a thread.** If the answer establishes a genuine investigative angle — at least two entities connected by at least two documents — it has outgrown a query page. Say so, and run `/watchdog-wiki <angle>` to promote it to a `wiki/` thread that deepens over time. For a broader connection sweep, suggest `/watchdog-surface`.

---

## What not to do

- Do not speculate beyond what the documents support
- Do not cite documents not in this vault
- Do not merge entities that are not confirmed to be the same real-world entity
- Do not answer from general knowledge — only from vault content
