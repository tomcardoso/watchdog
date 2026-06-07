# /watchdog-context — Seed context.md from prior stories and reporter notes

Read background material in `_CONTEXT/`, conduct a short structured interview, and write a polished `context.md` that tells every other Watchdog skill what this investigation is about.

Run this once when starting a new investigation, or again any time your understanding of the story has shifted significantly.

---

## 0. Privacy warning

Before reading any files, display this warning and wait for explicit confirmation:

> ⚠️ **Privacy warning**
>
> Everything in `_CONTEXT/` will be read by an AI. **Do not include:**
> - Source names or contact details
> - Confidential communications or unpublished tips
> - Leaked or legally sensitive documents
> - Any information that could identify a confidential source
>
> Published stories, your own research notes, and publicly available background are fine.
>
> Type **yes** to continue, or stop now and remove any sensitive files first.

If the response is anything other than an explicit confirmation, stop immediately.

---

## 1. Discover and preprocess files

List all files in `_CONTEXT/`:

```bash
find _CONTEXT/ -type f -not -name ".*" -not -name "*.yml"
```

If the folder is empty or missing, skip to step 3 — you'll work from the interview alone.

For each file, run:

```bash
watchdog preprocess "<file_path>"
```

Collect the extracted text. Do not load all files into context at once — read them one at a time and build a running mental summary. You do not need the full text after processing; keep only the key facts and themes.

---

## 2. Synthesize the material

After reading all files, build an internal picture of:

- **The story** — what pattern, wrongdoing, or question does the prior coverage establish or suggest?
- **Known entities** — people, companies, addresses, or institutions already named as relevant
- **Established facts** — what has already been reported or confirmed?
- **Open questions** — what did prior coverage raise but not answer?
- **Missing documents** — what records are referenced or implied but absent from the vault?
- **Dead ends** — anything the reporter already tried that didn't pan out?

If there is an existing `context.md`, read it now. Treat it as prior state — your goal is to update and enrich it, not replace it wholesale.

---

## 3. Structured interview

Ask the following questions in a **single batch** — do not ask them one at a time. Generate the first 4–6 questions from the material (make them specific to what you read), then add any of the generic questions below that aren't already answered by the files. Aim for 6–10 questions total.

**Material-generated questions (examples of what to ask — adapt to what you found):**
- "Your stories name [Entity A] and [Entity B] as connected — is that relationship the core thread you're pursuing, or context?"
- "Your [year] story mentions [gap or unanswered question] — is that still an open question in this investigation?"
- "I see references to [document type] — do you have those records, or are they something you're still looking for?"

**Generic questions to include if not covered by material:**
- What is the central question or wrongdoing you're investigating?
- Who are the 2–3 most important entities (people or companies) in this story?
- What would a definitive finding look like — what would you need to prove?
- Are there any angles you've already ruled out or dead ends to avoid?
- What documents or records are you most actively looking for?

The reporter can answer fully, answer briefly, skip any question, or say "make your best guess." Do not ask follow-up questions — take what you get and move on.

---

## 4. Draft context.md

Write a draft using the structure below. Populate every section from the synthesized material and the interview answers. Where the reporter skipped a question or the material was silent, leave the section with a short placeholder rather than omitting it.

```markdown
# {Investigation name} — context

## What I'm investigating

{One to three paragraphs. What is the story? What pattern, question, or wrongdoing is being pursued?
What has prior coverage established, and what remains unproven?}

## Key questions I'm trying to answer

- {Question 1}
- {Question 2}
- ...

## Entities I already know are relevant

{For each entity: name, type, and one sentence on why they matter.}

- **{Name}** ({type}) — {why relevant}
- ...

## Documents I'm expecting or looking for

- {Document type or specific record, and why it matters}
- ...

## What prior coverage established

{Brief summary of what has already been reported and confirmed. Cite specific stories or sources
if the reporter provided them. This section prevents re-investigating things already in print.}

## What I don't yet understand

{Gaps, contradictions, or unresolved questions from the prior coverage and the reporter's answers.}

## Dead ends and ruled-out angles

{Approaches already tried that didn't pan out. Prevents Watchdog from re-surfacing things
already investigated and dismissed.}
```

---

## 5. Show draft and confirm

Print the full draft and ask:

> Here's the draft `context.md`. Does this look right?
> - Type **yes** to write it
> - Type any corrections and I'll revise before writing
> - Type **skip** to discard without writing

If the reporter provides corrections, revise the draft and show it again. Repeat until they confirm or skip. Do not write the file until you have explicit approval.

---

## 6. Write context.md

Write the approved draft to `context.md` at the vault root. This overwrites any existing `context.md`.

Print:
```
context.md written. Run /watchdog-ingest to begin processing records.
```

If the reporter skipped, print:
```
Skipped — context.md not written.
```

---

## Guidelines

- **Never create entity notes or registry entries** — `_CONTEXT/` files are background, not evidence. Nothing from this skill touches `.watchdog/Registry/`.
- **Don't cite `_CONTEXT/` files as sources** — they are the reporter's prior knowledge, not the vault's evidence base.
- **Questions must be specific** — generic questions produce generic answers. Generate questions from what you actually read.
- **Respect skipped questions** — if the reporter doesn't answer, make a reasonable inference or leave a placeholder. Don't repeat the question.
