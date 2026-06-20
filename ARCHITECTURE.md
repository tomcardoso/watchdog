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
- **Model only for reasoning.** A Python orchestrator (`pipeline/orchestrate.py`) runs the
  ingest loop and calls the model only for judgement — classify, extract, synthesize,
  dedup timeline collisions, brief. Dispatch, file I/O, pre/post-flight, registry writes,
  and the synthesis bundle are deterministic Python. Each document's text lives only in
  its own extraction call, never in a long-lived context.
- **Parallel, with serialized writes.** Documents are extracted concurrently
  (semaphore-bounded); all registry and note writes funnel through a single serialized,
  lock-guarded path (`write_vault`), so concurrency is safe without the model reasoning
  about it.

---

## 2. Pipeline overview

```
_INCOMING/ ──▶ chew ──▶ .watchdog/queue/<sha>.json ──▶ ingest ──▶ vault notes + registry
 (raw docs)   (local)        (extracted text)        (Python; model    (entities, documents,
                                                       for reasoning)    timeline, briefings)
```

Two human-invoked phases, with a clean handoff via the queue:

1. **Chew** (`watchdog chew`) — local, no model. OCR/layout extraction, large-PDF
   chunking, near-duplicate fingerprinting. Writes one queue JSON per document.
2. **Ingest** (`watchdog ingest`) — a **Python orchestrator** (`pipeline/orchestrate.py`)
   that runs the whole pipeline in-process and calls the model (via `model_client`) **only
   for the reasoning steps**: classify, extract, synthesize entity prose, dedup colliding
   timeline events, write the briefing. Everything mechanical — dispatch, pre/post-flight,
   registry writes, timeline staging, the synthesis bundle, near-dup — is deterministic
   Python. Documents are extracted concurrently (semaphore-bounded); a failed document is
   logged and set aside (`_failed/`) without sinking the batch.

> **Earlier design (through #117):** ingest launched the `/watchdog-ingest` Claude Code
> skill, which orchestrated extractor subagents and a post-ingest subagent. #118 (W3)
> replaced that skill-as-orchestrator with the Python orchestrator above so the model
> stops paying reasoning tokens to act as a control-flow engine — see D18.

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

`watchdog ingest` resolves auth (`auth.resolve_auth`; errors to `watchdog setup` if
unconfigured), acquires a run lock (`.watchdog/Registry/.ingest-lock`, stale after 30
minutes), scans the queue, and clears the previous run's entity-fragment staging (§8).
It then runs the Python orchestrator in-process (`asyncio.run(orchestrate.run(...))`) and
releases the lock in a `finally`. Models and concurrency come from `watchdog configure`
(`extractor_model`, `finalizer_model`, `extract_concurrency`) or per-run flags.

A second, finer lock (`.watchdog/Registry/.write-lock`, `flock`) serializes the actual
registry/note writes so the concurrent document workers write safely.

---

## 5. Ingest (extraction)

**Code:** `pipeline/orchestrate.py` (the loop), `model_client.py` (the model adapter),
`pipeline/prompts.py` + `pipeline/schemas.py` (task prompts + JSON contracts),
`pipeline/preflight.py`, `pipeline/postflight.py`, `pipeline/write_vault.py`.

`orchestrate.run` scans the queue and extracts documents concurrently, bounded by an
`asyncio.Semaphore(extract_concurrency)`. Per document (`_extract_document`):

1. **Pre-flight** (`preflight.run`, a function call) — packages the page text and the
   candidate existing entities matched by substring against the manifest (no ML), each
   carrying its current note summary + timeline/roles/contradictions digest (§8).
2. **Classify** — one cheap model call (`model_client.acomplete_json`, haiku) over a text
   excerpt + the generated `records/_index.md`, returning the closest domain-skill
   filename (§6). Python reads that one skill and injects it into the extraction prompt.
3. **Extract** — one model call against the `EXTRACTION` schema: title, date, entities
   (deduped against the pre-flight candidates), roles, timeline events, key facts,
   per-entity summary/analysis, contradictions, morgue fields, and a briefing scratchpad.
   Schema validation + a tier-escalating retry live in `model_client`; the orchestrator
   adds one post-flight repair retry.
4. **Post-flight** (`postflight.run`, a function call) — validates the JSON, applies
   `match_id` merges, and calls `write_vault.run()`.

`write_vault` is the single deterministic writer: it merges entities (reconciling
near-duplicate slugs coined by concurrent workers via the shared `entity_norm`
name+type normalization), writes entity and document notes, updates the registry files,
stages timeline events, and moves the source file to the morgue — all inside the write
lock.

**Large documents — sectioned extraction.** Code: `pipeline/section.py`,
`pipeline/merge.py`. A document over `section_token_threshold` is split by `section.run`
into overlapping page-range sections, extracted **one at a time in reading order** with a
carry-forward block (entities-so-far + observations) in each section's prompt, then
combined by `merge.merge_extractions` into a single extraction JSON that goes through the
same post-flight / `write_vault` path.

**Output-overrun fallback.** Sectioning is gated on *input* size, but a moderate-input,
entity-dense document can overrun the model's *output* ceiling — the agent-SDK backend
can't cap output tokens, so the JSON truncates and post-flight rejects it. When
whole-document extraction fails on a **multi-page** document, the orchestrator
force-sections it (`section.run(force_budget=…)`, capped at half the doc so it yields ≥2
sections) and retries on the sectioned path — which bounds per-call output — before giving
up. See D19.

**Failure handling.** The model adapter raises if it can't get schema-valid JSON; a doc
whose extraction or post-flight fails (after the output-overrun fallback, for multi-page
docs) is logged to `.watchdog/Registry/ingest.log` and
cleaned via `abort.run` (`pipeline/abort.py`) — staging/section temp removed, queue file
moved to `.watchdog/queue/_failed/`, registry untouched. One bad document never sinks the
batch; move the queue file back from `_failed/` to retry.

---

## 6. Document classification

**Decision:** classification is a **dedicated, cheap model call the orchestrator makes
before extraction** (`_classify`, on the haiku tier) — not an embedding pre-pass, and no
longer folded into the extractor.

- **History.** An earlier design embedded the first N pages with a local fastembed
  model (`bge-small-en-v1.5`) and matched them against embeddings of the skill files
  to pre-assign a `document_type` at chew time.
- **Why it was removed (issue #95).** The comparison was register-mismatched — a
  document's text vs. *meta-text describing how to read that document type* — and
  with ~35 adjacent skills the cosine similarity was noisy. Worse, a confident-wrong
  classification was the *most* harmful outcome: it loaded the wrong domain skill.
- **Why a dedicated model call wins.** The orchestrator sends a text excerpt + the
  generated `records/_index.md` to a cheap haiku call that returns the skill filename;
  Python then reads that one skill and injects it into the extraction prompt. Accurate,
  cheap, and it keeps the extraction prompt lean (only the relevant skill, not the
  index). When the skill-based extractor self-classified, that work was turns inside the
  expensive extraction call (the #87 tax); a separate haiku call is cheaper.
- **Tradeoff.** `document_type` is `null` in the queue between chew and ingest; it is
  populated at ingest. Accepted — nothing downstream needs it earlier.
- **Sections.** A sectioned document (§5) is classified once, on its full-text excerpt,
  before sectioning; every section's prompt carries the same domain skill.
- **Note.** The fastembed model is still used for the **search index** (§11); only
  the classifier was removed.

---

## 7. Entity notes: structured vs. synthesized

An entity note has two fundamentally different kinds of content, treated
differently:

| Section | Kind | Treatment |
|---|---|---|
| `## Summary` | synthesized prose | model — carryforward interim, then bundled synthesis |
| `## Analysis` | synthesized prose | model — bundled synthesis |
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

> **Post-ingest runs in the Python orchestrator.** Entity prose synthesis (this section)
> and timeline reconciliation + briefing (§9) happen in `orchestrate._post_ingest`, each a
> single deterministic-Python step wrapped around one model call. They were once a
> per-entity synthesis fan-out (D16), then one bundled post-ingest subagent (D17), and now
> plain function calls in the orchestrator (D18) — the progression that drove post-ingest
> cost down.

Synthesizing an entity's prose across all its documents on every ingest would be
expensive. Synthesizing nothing (the old behaviour) let a later document's summary
clobber an earlier, richer one. The solution is **two complementary mechanisms covering
disjoint cases:**

| Case (this ingest) | Mechanism | Cost |
|---|---|---|
| Brand-new entity, 1 mention | plain write (extraction summary) | free |
| Pre-existing entity, 1 mention | **inline carryforward** | ~free, parallel |
| Any entity, **2+ mentions** | **bundled synthesis in `_post_ingest`** | bounded |

- **Inline carryforward.** Pre-flight carries each matched entity's current
  `## Summary` into the extraction prompt's candidate list; extraction *revises* it with
  the new document rather than writing a fresh single-document summary. Handles the common
  single-touch case in the extraction call that's already running — no extra call.
- **Gated synthesis.** As `write_vault` writes each entity, it appends a per-entity
  **fragment** (the entity's slice of the extraction JSON — summary, analysis, roles —
  plus document attribution) to `.watchdog/tmp/entity-fragments/<id>.md` and bumps a
  count in `_queue.json`. This is a *free byproduct* of data the extractor already
  produced. In `_post_ingest`, `synthesis_bundle.build_bundle` selects entities with
  **count ≥ 2** and packs each one's fragments + current prose into one compact bundle;
  a single model call synthesizes them all; `synthesis_bundle.apply_bundle` bulk-writes
  the Summary/Analysis via the shared writer in `pipeline/finalize_entity.py`.
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
  bundled synthesis — its summary is revised incrementally, not re-synthesized from all
  history. The deep, on-demand `/watchdog-entity` pass (`pipeline/write_entity.py`,
  which also re-synthesizes the Timeline) remains the tool for a full rebuild of a
  central figure.

Bundled synthesis writes **only** Summary and Analysis; Contradictions,
Timeline, Relationships, and Notes are preserved untouched. `apply_bundle` skips
any entity the model omits or returns with an empty summary, so its carried-forward
prose stays in place.

---

## 9. Timeline reconciliation & briefing

**Code:** `pipeline/orchestrate.py` (`_post_ingest`), `pipeline/timeline.py`.
**Files:** `.watchdog/timeline/`, `briefings/`, `hot.md`, `log.md`.

Each document's extraction stages its events to **raw** per-document files
`{date}_{sha7}.ndjson` (`timeline.stage_timeline_events`, called from post-flight) —
write-only and lock-free, since each filename is unique. All merge/dedup and the briefing
then run in `_post_ingest` (model: `post_model`) after extraction:

- `timeline.collisions(vault)` promotes dates with no prior canonical to **canonical**
  `{date}.ndjson` and returns the collisions where a canonical already existed; the
  orchestrator sends each collision's events to one model call (`timeline-dedup`,
  preserving full event objects), writes the deduped set back, then calls
  `timeline.cmd_rebuild_timeline` to render `timeline.md`. If the dedup call fails it
  falls back to the union rather than losing events;
- builds a briefing prompt from the per-document scratchpads + the compact per-doc
  results (near-dup alerts, contradiction flags), makes one model call (`briefing`), and
  `_write_briefing` writes the structured prose into `briefings/<ts>.md`, `hot.md`, and a
  `log.md` entry.

`watchdog ingest` prints the per-document summary; the briefing/hot/log files are the
durable record a fresh session reads.

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

- **Models** (configurable via `watchdog configure`, default sonnet): `extractor_model`
  (extraction, whole-doc + section) and `finalizer_model` (post-ingest synthesis +
  timeline + briefing, §8–§9); classification runs on haiku. `extract_concurrency`
  (default 5) bounds parallel extraction. Each is overridable per run via the matching
  `watchdog ingest` flag (`--extractor-model` / `--finalizer-model` / `--concurrency`).
- **Model client** (`model_client.py`): the orchestrator's single entry to the model.
  Routes each task to a backend — `claude-agent-sdk` (subscription login or API key — the
  only backend that works on a subscription) or `claude-api` (raw Messages + structured
  outputs) — by auth mode and per-task policy, validates the JSON, escalates the tier on
  failure, and reports cost/latency. Auth (subscription vs API key) is resolved by
  `cmd/auth.py` (see #119).
- **Claude Code skills** (in-vault, run interactively — *not* part of ingest):
  `watchdog-context`, `watchdog-entity`, `watchdog-query`, `watchdog-surface`,
  `watchdog-wiki`, `watchdog-health`. Ingest is the Python orchestrator (§5); it uses no
  Claude Code skill.
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
| D3 | ~~Isolated extractor subagent per document~~ **Superseded by D18.** | Kept the (model) orchestrator's context flat by extracting each doc in a throwaway subagent. Moot once the orchestrator became Python (no model context to keep flat) | — |
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
| D14 | ~~Timeline reconciliation + briefing in one finalize subagent~~ **Superseded by D18.** | Kept scratchpad prose and timeline NDJSON out of the model orchestrator's context. Moot under Python orchestration — `_post_ingest` reads them directly | — |
| D15 | Runaway guard + `ingest-abort` (§5) | A stuck subagent bails (`STATUS: failed`) instead of wedging the batch; clean bail leaves no partial vault writes, so the doc re-ingests cleanly | A failed doc must be manually moved back from `queue/_failed/` to retry |
| D16 | ~~Entity synthesis (§8) and finalize (§9) kept as separate subagents, not merged~~ **Superseded by D17.** | Original rationale: synthesis was a per-entity parallel fan-out, finalize a single whole-batch agent; merging would serialize entity work and re-concentrate fragments + scratchpads + timeline into one context. This held while synthesis was a fan-out — but the fan-out itself proved to be the cost problem (see D17) | — |
| D17 | Merge synthesis + finalize into one post-ingest subagent fed a Python-built bundle (§8, §9) | The per-entity fan-out launched one subagent per `count ≥ 2` entity (36 in a representative run), each paying startup + preamble cache-write to do a few hundred tokens of judgement — ~$5 of a $19 run. D16 feared merging would re-bloat context, but fragments are *compact digests* (D8), so one agent reading the whole bundle stays small; the cost win (one agent, one cached preamble) dominates the serialization cost. The bundle's `build_bundle`/`apply_bundle` survive into D18; only the surrounding subagent is gone | A very large batch could need bundle splitting by token budget (not yet implemented) |
| D19 | Force-section on whole-doc output overrun (§5) | Sectioning triggers on *input* size (`section_token_threshold`), but truncation is *output*-driven — a moderate-input, entity-dense doc overruns the model's output ceiling on the agent-SDK backend (which can't cap output), truncating the JSON and escalating the retry toward a pricier tier. On a multi-page doc whose whole-doc extraction is rejected, the orchestrator re-runs it through the sectioned path with a small forced budget (≥2 sections) to bound per-call output | Adds one (failed) whole-doc attempt before the fallback; single-page docs can't be split, so they still just fail |
| D18 | Python orchestrator; the model is called only for reasoning (#118 W3) | A Claude Code skill session *is* a model loop, so the orchestrator (and per-turn coordination) cost model tokens even for pure dispatch — the orchestrator alone ran $1–3+ of a representative batch, ~38–86% of pipeline spend was per-turn context re-send. Moving the loop to Python (`orchestrate.py`) calling the model only for classify/extract/synthesis/timeline-dedup/briefing removes that floor and lets each task route to a backend + tier (`model_client`, D-auth #119). Supersedes the subagent split (D3) and the off-orchestrator finalize (D14) | Extraction now runs the Claude Agent SDK in-process and parallel concurrency is bounded by subscription/API rate limits (`extract_concurrency` knob); the `claude-api` backend is unproven until a metered key is used |
