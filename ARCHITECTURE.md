# Watchdog — Architecture

This document records how the preprocessing and ingestion pipeline is built and,
more importantly, **why** — the architectural decisions and their tradeoffs. It is
the reference for understanding the system as a whole and for evaluating future
changes against the choices already made.

> **Keep this current.** Any change that alters the pipeline's structure, the
> division of labour between deterministic code and the model, the vault/registry
> layout, or one of the decisions logged below must update this file in the same
> change. See [CLAUDE.md](CLAUDE.md).

---

## 1. Design principles

These run through every decision below.

- **Local-first.** Preprocessing (OCR, layout, near-duplicate detection,
  classification inputs, the search index) runs entirely on the user's machine.
  Documents never leave it during chew. The only network calls are the Claude API
  during the ingest (extraction/synthesis) phase.
- **Deterministic code writes; the model decides.** Anything that can be done
  reliably and cheaply in Python — file writing, merging, sorting, deduplication,
  registry bookkeeping — is done in Python. The model is reserved for judgement:
  reading documents, extracting entities, classifying, synthesizing prose. This
  keeps token cost down and makes the vital bookkeeping reproducible and testable.
- **Cost-consciousness.** Token spend is treated as a budget. Work is pushed to the
  cheapest layer that can do it correctly, and model work is bounded (gated,
  fanned out, fed pre-digested inputs) rather than open-ended.
- **Flat orchestrator context.** Each document is extracted in an isolated subagent
  so document text, skill files, and extraction output never accumulate in the
  orchestrator's context. The orchestrator holds only compact state regardless of
  batch size. The same fan-out shape is reused for entity synthesis.
- **Parallel, with serialized writes.** Subagents run concurrently; all registry
  and note writes funnel through a single serialized, lock-guarded path so
  concurrency is safe without the model having to reason about it.

---

## 2. Pipeline overview

```
_INCOMING/ ──▶ chew ──▶ .watchdog/queue/<sha>.json ──▶ ingest ──▶ vault notes + registry
 (raw docs)   (local)        (extracted text)         (Claude)     (entities, documents,
                                                                     timeline, briefings)
```

Two human-invoked phases, with a clean handoff via the queue:

1. **Chew** (`watchdog chew`) — local, no model. OCR/layout extraction, large-PDF
   chunking, near-duplicate fingerprinting. Writes one queue JSON per document.
2. **Ingest** (`watchdog ingest` → `/watchdog-ingest`) — model-driven. A Claude
   orchestrator fans out per-document extractor subagents (large documents are
   extracted in sequential page-range sections), runs a gated entity-synthesis pass,
   then delegates timeline reconciliation and the briefing to a single finalize
   subagent. A runaway guard lets a stuck subagent bail cleanly without wedging the batch.

---

## 3. Chew (preprocessing)

**Code:** `pipeline/preprocess.py` (single file), `pipeline/preprocess_batch.py`
(batch orchestration), `pipeline/near_dup.py`.

- **Text/layout extraction.** Direct text where the PDF has it; otherwise Docling
  with OCR. Garbled-text detection can force OCR. Output is per-page markdown.
- **Large documents.** PDFs above a threshold are split into chunks (default 40
  pages, `chunk_size`), processed in parallel subprocesses, and reassembled in
  order with page numbers preserved.
- **Near-duplicate detection.** Each document's text is shingled into word 3-grams
  (`shingle_size`) and reduced to a MinHash signature; the candidate is compared
  against every prior document's signature by estimated Jaccard similarity. Matches
  at or above `dup_threshold` (default 0.85) are flagged for journalist review at
  ingest — never auto-discarded. The signature is stored in `documents.json` so
  future documents compare against it.
- **Output.** Per document: `.watchdog/queue/<sha256>.json` (filename, sha256,
  page count, per-page markdown, `near_dup`, MinHash signature). The original is
  moved to `.watchdog/staging/<sha256>/`.

Chew is fully local and writes no model-derived fields — `document_type` is left
`None` here (see §6).

---

## 4. Ingest setup & locking

**Code:** `pipeline/ingest_setup.py`, `cmd/ingest.py`.

`watchdog ingest` acquires a run lock (`.watchdog/Registry/.ingest-lock`, treated as
stale after 30 minutes), scans the queue, clears the previous run's entity-fragment
staging (§8), and writes `.watchdog/ingest-state.json` for the skill to read. The
`/watchdog-ingest` skill reads that state and **must** release the lock on every
exit path via `watchdog unlock` (which removes the lock, deletes the state file, and
cleans `wdg_*` temp files).

A second, finer lock (`.watchdog/Registry/.write-lock`, `flock`) serializes the
actual registry/note writes so parallel subagents can write safely.

---

## 5. Ingest (extraction)

**Skills:** `skills/watchdog-ingest.md` (orchestrator),
`skills/watchdog-ingest-subagent.md` (per-document extractor).
**Code:** `pipeline/preflight.py`, `pipeline/postflight.py`, `pipeline/write_vault.py`.

The orchestrator (model: `ORCHESTRATOR_MODEL`, configurable via `watchdog configure orchestrator_model` or `--orchestrator-model`) partitions the queue by
estimated size. **Normal** documents are processed in **batches of up to 5**, all
extractor subagents in a batch launched in parallel. **Large** documents (over
`section_token_threshold`) are extracted in sections instead (see "Large documents"
below). Each normal subagent:

1. **Pre-flight** (`watchdog pre-flight <sha>`) — packages everything the subagent
   needs: the page text path, and candidate existing entities matched by substring
   against the manifest (no ML), each carrying its current note summary (§8).
2. **Load domain skill** — identifies the document type from the full text and reads
   the single closest-matching skill from `.claude/commands/records/`, consulting
   `records/_index.md` (a generated one-line description per skill) when unsure.
   Falls back to `general-records.md` (see §6).
3. **Extract** — title, date, entities (with dedup against pre-flight candidates),
   roles, timeline events, key facts, per-entity summary and analysis,
   contradictions.
4. **Post-flight** (`watchdog post-flight --extraction …`) — validates the JSON,
   applies `match_id` merge decisions, reads the MinHash signature, and calls
   `write_vault.run()` which performs all vault writes.

`write_vault` is the single deterministic writer: it merges entities (reconciling
near-duplicate slugs coined by parallel subagents via the shared `entity_norm`
name+type normalization), writes entity and document notes, updates the four registry
files, appends timeline events, and moves the source file to the morgue — all inside
the write lock.

**Large documents — sectioned extraction.** Code: `pipeline/section.py`,
`pipeline/merge.py`; skill: `watchdog-ingest-section-subagent.md`. A document too big
for one context is split by `watchdog section-plan` into overlapping page-range
sections, extracted **one at a time in reading order** with a running scratchpad
carried forward, then combined by `watchdog merge-sections` into a single extraction
JSON that goes through the same post-flight / `write_vault` path. Section subagents
self-classify like normal ones (no pre-classification — see §6).

**Runaway guard & abort.** A subagent that cannot make progress returns `STATUS:
failed` instead of looping. The orchestrator then runs `watchdog ingest-abort <sha>`
(`pipeline/abort.py`), which clears the document's staging/section files and moves its
queue file to `.watchdog/queue/_failed/`, leaving the registry untouched. Because a
clean bail never wrote vault state, the document re-ingests cleanly once moved back.

---

## 6. Document classification

**Decision:** classification happens **at extraction time, performed by the
extractor subagent reading the document** — not by a separate pre-pass.

- **History.** An earlier design embedded the first N pages with a local fastembed
  model (`bge-small-en-v1.5`) and matched them against embeddings of the skill files
  to pre-assign a `document_type` at chew time.
- **Why it was removed (issue #95).** The comparison was register-mismatched — a
  document's text vs. *meta-text describing how to read that document type* — and
  with ~35 adjacent skills the cosine similarity was noisy (misclassifications
  including `null` and wrong categories). Worse, a confident-wrong classification
  was the *most* harmful outcome: the subagent would load the wrong domain skill and
  skip its own inference.
- **Why model-at-extraction wins.** The subagent already reads the entire document;
  having it pick the skill from descriptive filenames (plus the generated
  `records/_index.md` for adjacent cases) is both more accurate and nearly free,
  because the expensive work (reading the document) happens regardless. It also
  deleted an entire subsystem (the embedding cache, hashing, thresholds).
- **Tradeoff.** `document_type` is `null` in the queue between chew and ingest; it is
  populated by extraction. Accepted — nothing downstream needs it earlier.
- **Sections too.** Large documents are extracted in sections (§5). Section 1
  classifies from its pages (consulting `_index.md`, falling back to
  `general-records.md`) and records the chosen skill path as a `Domain skill:` line
  in the running scratchpad. Sections 2..N read that line and load the same skill
  rather than re-classifying — guaranteeing all sections of a document use the same
  domain skill. If the scratchpad line is somehow absent, later sections fall back to
  self-classifying.
- **Note.** The fastembed model is still used for the **search index** (§11); only
  the classifier was removed.

---

## 7. Entity notes: structured vs. synthesized

An entity note has two fundamentally different kinds of content, treated
differently:

| Section | Kind | Treatment |
|---|---|---|
| `## Summary` | synthesized prose | model — carryforward interim, then entity-synthesis subagent |
| `## Analysis` | synthesized prose | model — entity-synthesis subagent |
| `## Contradictions` | cited callouts | deterministic — append-only, deduped, audit-managed |
| `## Timeline` | structured events | deterministic — merged, sorted by **event** date |
| `## Relationships` | structured roles | deterministic — merged |
| `## Notes` | journalist annotations | never touched by the pipeline |

**Decision:** structured/relational content (Timeline, Relationships, Contradictions)
is accumulated deterministically; prose (Summary, Analysis) is synthesized by the
model.

- **Why the split.** Timeline and Relationships are facts with a natural key and
  order; mechanical merge/sort is correct and free. Prose is a cross-source judgement
  that only the model can do well.
- **Why Contradictions are their own cited section** (not folded into Analysis prose,
  not made chronological). They are verifiable claims with citations. The extractor
  subagent is the sole verifier — it confirms each one at extraction time, against the
  entity's prior contradictions and context supplied by pre-flight, so there is no
  later orchestrator removal pass. Sorting them by date would make them a worse
  Timeline keyed on the *document/provenance* date rather than the *event* date — the
  wrong axis. So they stay a discrete, append-only, deduped log that the prose
  synthesis never disturbs.

---

## 8. Entity synthesis: carryforward + gated synthesis

> **Two different post-extraction passes, don't confuse them.** This section is the
> **entity-synthesis subagent** (`watchdog-entity-synthesis-subagent.md`), which
> rewrites entity *prose*. §9's **finalize subagent**
> (`watchdog-ingest-finalize-subagent.md`) reconciles the *timeline* and writes the
> *briefing*. Separate skills, separate steps — see D16 for why they're kept separate
> rather than merged into one post-extraction agent.

Synthesizing an entity's prose across all its documents on every ingest would be
expensive and would re-bloat the orchestrator context. Synthesizing nothing (the old
behaviour) let a later document's summary clobber an earlier, richer one. The
solution is **two complementary mechanisms covering disjoint cases:**

| Case (this ingest) | Mechanism | Cost |
|---|---|---|
| Brand-new entity, 1 mention | plain write (extraction summary) | free |
| Pre-existing entity, 1 mention | **inline carryforward** | ~free, parallel |
| Any entity, **2+ mentions** | **gated entity-synthesis subagent** | bounded |

- **Inline carryforward.** Pre-flight carries each matched entity's current
  `## Summary` into the subagent's candidate list; the subagent *revises* it with the
  new document rather than writing a fresh single-document summary. Handles the common
  single-touch case in the extractor that's already running — no extra agent.
- **Gated synthesis.** As `write_vault` writes each entity, it appends a per-entity
  **fragment** (the entity's slice of the extraction JSON — summary, analysis, roles —
  plus document attribution) to `.watchdog/tmp/entity-fragments/<id>.md` and bumps a
  count in `_queue.json`. This is a *free byproduct* of data the extractor already
  produced — no extra extractor tokens. After extraction, the orchestrator (§3 of the
  ingest skill) reads `_queue.json`, selects entities with **count ≥ 2**, and fans out
  entity-synthesis subagents (batches of 5, model `ENTITY_SYNTHESIZER_MODEL`) that reconcile the fragments + current
  prose into a synthesized Summary and Analysis via `watchdog write-entity-synthesis`
  (`pipeline/finalize_entity.py`).
- **The gate is the cost control.** Most entities in a batch are single-mention and
  never hit synthesis. Cost scales with *contested* entities, not all entities.
- **Why fragments are a byproduct, not extractor-written prose.** Having the extractor
  narrate per-entity notes would add token cost to the expensive parallel phase. The
  extraction JSON already contains everything a fragment needs.
- **Why gate on "≥2 this ingest" specifically.** That is exactly the case inline
  carryforward can't handle (multiple simultaneous fragments → last-write-wins) and
  where reconciliation earns its cost.
- **Known limitation.** Fragments are run-scoped (cleared at ingest start). A
  pre-existing entity touched by a single new document is handled by carryforward, not
  the entity-synthesis subagent — its summary is revised incrementally, not re-synthesized from all
  history. The deep, on-demand `/watchdog-entity` pass (`pipeline/write_entity.py`,
  which also re-synthesizes the Timeline) remains the tool for a full rebuild of a
  central figure.

The entity-synthesis subagent writes **only** Summary and Analysis; Contradictions,
Timeline, Relationships, and Notes are preserved untouched.

---

## 9. Timeline reconciliation & briefing

**Code:** `pipeline/timeline.py`. **Skill:** `watchdog-ingest-finalize-subagent.md`.
**Files:** `.watchdog/timeline/`, `briefings/`.

Each extractor (or section) subagent writes its events to a **raw** per-document file
`{date}_{sha7}.ndjson` — write-only and lock-free, since each filename is unique. All
merge/dedup and the briefing are then deferred to a **single finalize subagent**
(model `FINALIZER_MODEL`) launched once after extraction, so scratchpad prose and
timeline NDJSON never enter the orchestrator's context. That subagent:

- runs `watchdog timeline-collisions` (promotes dates with no prior canonical to
  **canonical** `{date}.ndjson`; returns the collisions where a canonical already
  existed), semantically deduplicates each collision, and runs `watchdog
  rebuild-timeline` to render `timeline.md`;
- reads the per-document scratchpads and `RESULTS` / `NEARDUP_ALERTS` /
  `CONTRADICTION_FLAGS` handed to it, and writes the post-ingest briefing under
  `briefings/`.

The orchestrator only prints the returned briefing path — it never reads the
scratchpads, timeline files, or briefing itself.

---

## 10. Near-duplicate detection

See §3. MinHash-over-shingles, computed at chew time, surfaced as `near_dup` on the
queue JSON and flagged in the ingest briefing. Detection only — the journalist
decides whether near-duplicates are the same document.

---

## 11. Search index

**Code:** `pipeline/embed.py`. **Files:** `<vault>/.embeddings/`.

A local fastembed (`bge-small-en-v1.5`) semantic index, one `.npy` + `.json` per
document and per note. Re-ingesting a document or note overwrites only its own files.
This is the same model the removed classifier used, retained solely for search.

---

## 12. Vault & registry layout

**Vault (the investigation folder):**

```
entities/<type>/<id>.md     entity notes
documents/<slug>.md         document notes
morgue/<entity>/<type>/…     original source files, filed by subject
timeline.md                 rendered global timeline
briefings/<date>.md         per-ingest briefings
context.md / hot.md / log.md investigation context, hot cache, run log
.embeddings/                search index
.obsidian/graph.json        graph colours per entity type
.watchdog/                  pipeline state (below)
```

**`.watchdog/` (pipeline state):**

```
queue/<sha>.json            chewed documents awaiting ingest
staging/<sha>/              chewed originals
timeline/                   raw + canonical NDJSON event files
tmp/                        scratch (wdg_* temp, entity-fragments/)
Registry/
  entities.json             full entity records (roles, events, appears_in)
  documents.json            per-document metadata + MinHash signatures
  registry.json             counts + last-updated
  manifest.json             lightweight id→{name,type,aliases,note_path} lookup
  ingest.log                append-only ingest log
  .ingest-lock / .write-lock  run lock / write serialization
ingest-state.json           handoff from `watchdog ingest` to the skill
```

**Why a manifest separate from `entities.json`.** Pre-flight needs only a small
lookup (names/aliases → id/note_path) for candidate matching; reading the full
registry for every document would be wasteful. The manifest is the cheap index.

---

## 13. Models & skills

- **Models** (all configurable via `watchdog configure`, default sonnet): four separate
  model settings — `orchestrator_model` (ingest orchestrator), `extractor_model`
  (per-document, section, and large-document subagents), `entity_synthesizer_model`
  (entity synthesis subagents, §8), and `finalizer_model` (timeline + briefing, §9).
  Each can also be overridden for a single run via the matching `watchdog ingest` flag.
- **Subagent skills**: `watchdog-ingest-subagent.md` (per-document extraction),
  `watchdog-ingest-section-subagent.md` (one page-range section of a large document),
  `watchdog-entity-synthesis-subagent.md` (prose synthesis, §8), and
  `watchdog-ingest-finalize-subagent.md` (timeline + briefing, §9).
- **Domain skills** live in `src/watchdog/skills/records/` and are installed into a
  vault's `.claude/commands/records/` by `setup_cmd.install_skills`, which also
  generates `records/_index.md` (one-line description per skill, derived from each
  skill's intro). New skills are added by dropping a file in that directory — no code
  changes. The index is **generated, not checked in**, so it has a single source of
  truth and cannot drift from the skills (see D12). It is rebuilt by *scanning the
  records directory* — so skills a user adds directly to their vault are indexed too —
  on install, on `watchdog refresh-skills`, and at the start of every ingest
  (`ingest_setup.run` → `regenerate_records_index`). Regeneration is unconditional
  rather than mtime-gated: it's cheap and that way it self-heals on add, edit, and
  delete alike.

---

## 14. Decision log (summary)

| # | Decision | Rationale | Tradeoff |
|---|---|---|---|
| D1 | Local-first preprocessing | Source documents never leave the machine; no API cost for chew | Bound by local compute for OCR/layout |
| D2 | Deterministic code writes, model decides | Reproducible, testable, cheap bookkeeping; model reserved for judgement | More Python surface to maintain |
| D3 | Isolated extractor subagent per document | Flat orchestrator context regardless of batch size | Per-subagent startup overhead |
| D4 | Classification at extraction time, by the model (§6) | Accurate, near-free (doc already read), deleted the embedding subsystem | `document_type` null until extraction |
| D5 | Structured vs. synthesized note split (§7) | Mechanical merge is correct+free for facts; prose needs the model | Two write paths |
| D6 | Contradictions as a discrete cited section, verified at extraction (§7) | Verifiable claims; the extractor is the sole verifier (no orchestrator removal pass); chronological sort would be a worse timeline | Extra section + extraction field; a bad callout isn't caught downstream |
| D7 | Carryforward + gated entity-synthesis (§8) | Cost scales with contested entities, not all entities; fixes summary clobber and the within-batch race | Single-new-mention entities only revised, not fully re-synthesized |
| D8 | Fragments as a write-time byproduct (§8) | Synthesis input with zero extra extractor tokens | Run-scoped scratch state in tmp |
| D9 | Raw-then-canonical timeline files (§9) | Subagents write lock-free; merge deferred to one post-batch step | Two file generations on disk |
| D10 | MinHash near-dup, detect-only (§10) | Cheap local dedup signal; journalist decides | Approximate similarity |
| D11 | Separate lightweight manifest (§12) | Cheap candidate lookup in pre-flight | A second index to keep in sync (done in `write_vault`) |
| D12 | Generate `records/_index.md` by scanning the records dir (§13), don't check in a static index; rebuild unconditionally on install/refresh/ingest | Single source of truth — the skill's own intro; a static file silently drifts and breaks the "just drop a skill file" workflow. Scanning the dir (not the package) indexes user-added skills; unconditional rebuild is cheap and self-heals on add/edit/delete (no mtime edge cases) | Descriptor quality depends on parsing the intro prose; a dedicated frontmatter descriptor field is the upgrade path if that heuristic gets fragile |
| D13 | Sectioned extraction for large documents (§5) | Documents over the token threshold don't fit one context; sequential page-range sections with a carried scratchpad + deterministic `merge-sections` preserve cross-section consistency | Large docs are extracted serially (slower); needs overlap + merge-dedup |
| D14 | Timeline reconciliation + briefing in one finalize subagent, off the orchestrator (§9) | Keeps the orchestrator context flat — scratchpad prose and timeline NDJSON never enter it | An extra subagent round-trip; the orchestrator can't see briefing internals |
| D15 | Runaway guard + `ingest-abort` (§5) | A stuck subagent bails (`STATUS: failed`) instead of wedging the batch; clean bail leaves no partial vault writes, so the doc re-ingests cleanly | A failed doc must be manually moved back from `queue/_failed/` to retry |
| D16 | Entity synthesis (§8) and finalize (§9) kept as separate subagents, not merged | They differ on the axis the architecture optimizes: synthesis is per-entity parallel fan-out, finalize is a single whole-batch agent. Merging would serialize the entity work and re-concentrate every entity's fragments + all scratchpads + all timeline files into one context — the exact bloat the subagent split prevents. They also have independent failure domains and models. Briefing cohesion (reflecting synthesized entities) comes from ordering + passing the synthesized list to finalize, not from merging | Two post-extraction steps instead of one; the briefing doesn't see synthesized prose unless that list is explicitly passed |
