You have no private reasoning channel — this model produces its answer in one visible pass, with
no separate space to think before committing to output. So work the extraction in three stages,
written into `document.plan` BEFORE you fill `document.key_facts`, in this order:

1. PLAN. In one or two sentences: what kind of document this is, which DOMAIN SKILL fields you
   expect to find, and whether a KNOWN_DOCUMENT_TYPES entry already fits. `entities` comes before
   `document` in your output, so you have already committed the entity roster and its ids by the
   time you write this — when key_facts tags an entity, use one of those exact ids, never a new
   or revised one.
2. EVIDENCE TRIAGE. Note, page by page or section by section, which passages clear the
   materiality bar above (KEY FACTS) and which are boilerplate you're setting aside — a phrase
   each is enough, not full sentences.
3. CONSISTENCY PASS. Before you write key_facts, check the document against itself: any date,
   figure, or name that looks inconsistent with the rest (TRANSCRIBE, DON'T CORRECT); any
   change-of-state to preserve as a change, not just an end-state (PRESERVE CHANGES); any name
   recurring in a different capacity elsewhere (the two-hats paragraph); any table row whose
   label and figure look conversion-mangled (CONVERSION ARTIFACTS). Note what you found, even if
   "none."

`document.plan` is scratch space — nothing downstream reads it. Keep every stage terse (a list of
short notes, not prose); its only job is to give you room to work the problem before you have to
commit to key_facts, not to duplicate what key_facts will already say.
