# /query — Answer a question from the vault

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

1. **Registry/entities.json** — to find entities matching names in the question
2. **Entity notes** (`entities/<type>/<id>.md`) — for the specific entities identified
3. **Document notes** (`documents/*.md`) — for the source documents those entities appear in
4. **Briefings** (`briefings/*.md`) — for previous analysis that may be relevant

Use Bash `grep -r` across the vault if you need to find entities by alias or partial name:
```bash
grep -ri "<search term>" entities/ documents/ --include="*.md" -l
```

### 3. Compose the answer

**Structure:**
- State the answer directly in the first sentence
- Support every claim with a citation: entity name, document title, page number
- If the vault contains conflicting information, surface the conflict — don't silently pick one
- If the vault does not contain enough information to answer, say so explicitly — do not speculate

**Citation format:**
> John Doe is listed as Director of Shell Co Ltd (Annual Report 2023, p. 3 — confidence: high).

**If the answer requires combining information from multiple documents:**
> The address 123 Main St appears in two documents: the Shell Co corporate registration (p. 1) and the Smith Holdings annual report (p. 7). These documents have no other apparent connection.

### 4. Suggest follow-up

If the question reveals a gap — an entity that appears but has no document note, a relationship that's implied but not explicitly confirmed — note it briefly:
> "This relationship is inferred from two separate documents but no direct confirmation was found. Run `/surface` for a broader connection analysis."

---

## What not to do

- Do not speculate beyond what the documents support
- Do not cite documents not in this vault
- Do not merge entities that are not confirmed to be the same real-world entity
- Do not answer from general knowledge — only from vault content
