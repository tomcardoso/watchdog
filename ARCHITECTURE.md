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
- **Skip exact duplicates before OCR (D27).** Before the worker pool, each file's sha256 is
  checked against the document registry (already ingested), the pending queue (already chewed this
  round), and the shas seen earlier in the same batch. A match is moved to `_INCOMING/_SKIPPED/`
  with a warning rather than re-OCR'd and re-queued — so re-dropping a file you already have costs
  one hash, not another OCR pass (and re-ingestion was already a no-op, see §5). Exact bytes only;
  a near-duplicate has a different sha and is handled by the MinHash check below.
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
releases the lock in a `finally`. Models, concurrency, and classification come from
`watchdog configure` (`classifier_model`, `extractor_model`, `finalizer_model`,
`extractor_effort`, `finalizer_effort`, `extract_concurrency`, `classify_pages`,
`default_skill`) or per-run flags.

A second, finer lock (`.watchdog/Registry/.write-lock`, `flock`) serializes the actual
registry/note writes so the concurrent document workers write safely.

---

## 5. Ingest (extraction)

**Code:** `pipeline/orchestrate.py` (the loop), `model_client.py` (the model adapter),
`pipeline/prompts.py` + `pipeline/schemas.py` (task prompt builders + JSON contracts;
the instruction prose lives in editable templates under `prompts/*.md` — see D28),
`pipeline/preflight.py`, `pipeline/postflight.py`, `pipeline/write_vault.py`.

`orchestrate.run` scans the queue and extracts documents concurrently, bounded by an
`asyncio.Semaphore(extract_concurrency)`. Per document (`_extract_document`):

1. **Pre-flight** (`preflight.run`, a function call) — packages the page text and the
   candidate existing entities matched by substring against the manifest (no ML), each
   carrying its current note summary + timeline/roles/contradictions digest (§8).
2. **Classify** — one cheap model call (`model_client.acomplete_json`, `classifier_model`,
   default haiku) over the document's first `classify_pages` pages + the generated
   in-memory skill index, returning the closest domain-skill filename (§6). Python reads that
   one skill and injects it into the extraction prompt. **Skipped entirely when a skill is
   pinned** for the run (`--skill` / `default_skill`) — that one skill is used for every
   document, saving a model call per doc on known-homogeneous batches.
3. **Extract** — one model call against the `EXTRACTION` schema. The model emits two layers
   (D26): a **fact layer** — `document.key_facts`, each a single material fact written once,
   carrying an optional `date` (when the fact *is* a datable occurrence) and an optional
   `entities` list (the ids the fact is about) plus an optional verbatim `quote`; and a **graph
   layer** — entities (deduped against the pre-flight candidates) with aliases, roles, and
   contradictions. It no longer restates the document as per-entity summaries, evidence
   fragments, or timeline events, nor pads `key_facts` to a fixed count — the full Docling text
   is retained in the morgue (§3, §12), so extraction indexes it rather than reproducing it.
   Schema validation + a same-model retry live in `model_client` (no automatic tier
   escalation — see D20); the orchestrator adds one post-flight repair retry.
4. **Post-flight** (`postflight.run`, a function call) — validates the JSON, applies
   `match_id` merges (remapping `key_facts.entities` tags onto canonical ids), **explodes** the
   unified key_facts into the per-entity `evidence_fragments` + `timeline_events` that the
   writers consume (`explode_key_facts`, D26), then calls `write_vault.run()`.

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
- **Why a dedicated model call wins.** The orchestrator sends a text excerpt + the skill
  index (built in memory from the global catalog, `skills_catalog.build_index()`) to a
  cheap haiku call that returns the skill filename; Python then reads that one skill (from
  the global catalog) and injects it into the extraction prompt. Accurate, cheap, and it
  keeps the extraction prompt lean (only the relevant skill, not the index). When the
  skill-based extractor self-classified, that work was turns inside the expensive
  extraction call (the #87 tax); a separate haiku call is cheaper.
- **Pinning.** `--skill` / `default_skill` skips this call entirely and uses one skill
  for the whole run (see §5, D21).
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
| `## Summary` | synthesized prose | model — provisional one-liner from the entity's top tagged fact, then bundled synthesis once 2+ mentions (D26) |
| `## Analysis` | tagged-fact claims → synthesized prose | deterministic claims (key_facts tagged to the entity, exploded per D26) for single-mention; model-synthesized prose once 2+ mentions |
| `## Contradictions` | cited callouts | deterministic — append-only, deduped, audit-managed |
| `## Timeline` | structured events | deterministic — the entity's dated tagged facts, merged, sorted by **event** date |
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
clobber an earlier, richer one. The gate is **project-wide recurrence** (D26): an entity
earns a synthesized summary once it appears in **2+ documents across the whole
investigation**, otherwise it stays a deterministic stub.

| Entity's project-wide reach | Treatment | Cost |
|---|---|---|
| In **1 document** total | deterministic stub — facts in `## Analysis`, relationships; **no Summary section** | free |
| In **2+ documents** total (`appears_in ≥ 2`) | **bundled synthesis** — short model-written Summary (1–3 paragraphs) | bounded |

- **Recurrence is the signal, counted across the project — not the batch.** `synthesis_bundle.build_bundle`
  gates on the registry entity's `appears_in` length, so a registering agent or a law firm that
  surfaces in a second document *in a later batch, years apart* is promoted the moment its
  `appears_in` crosses 2. Only entities *touched this run* (present in the fragment queue) are
  candidates — an untouched entity has nothing new to reconcile. (The `count` still written to
  `_queue.json` is now just the touched-set marker, no longer the gate.)
- **No summary for single-document entities, and no inline revision.** Under D26 the extractor emits
  no per-entity summary, so the old carryforward trick (pre-flight feeds the current `## Summary`
  back and extraction *revises* it) is gone. A one-document entity simply has no Summary section —
  its facts live in `## Analysis` and its connections in `## Relationships`, which is all an
  incidental actor needs. Summaries are only ever written by bundled synthesis, so a single new
  document can never silently overwrite an established one.
- **Association needs no special code.** An incidental entity tied to an important one — the
  paralegal who filed for a tracked party — is captured for free: it gets a stub note whose
  `## Relationships` records the link (and the reverse link lands on the tracked party's note). If
  it keeps reappearing, its own `appears_in` promotes it to synthesis.
- **No recency bias.** The synthesis prompt instructs the model to weight the full body of
  evidence: an entity established across many documents is *not* redefined by a new passing
  mention — a minor new reference is folded in without reshaping a settled account.
- **Gated synthesis mechanics.** As `write_vault` writes each entity, it appends a per-entity
  **fragment** (the entity's slice of the exploded extraction — its tagged-fact claims with any
  quotes, roles — plus document attribution) to `.watchdog/tmp/entity-fragments/<id>.md`.
  This is a *free byproduct* of data the extractor already produced. In `_post_ingest`,
  `build_bundle` selects the recurring entities and packs each one's fragments + current prose
  into one compact bundle; a single model call synthesizes them all; `synthesis_bundle.apply_bundle`
  bulk-writes the Summary/Analysis via the shared writer in `pipeline/finalize_entity.py`.
- **Why fragments are a byproduct, not extractor-written prose.** Having the extractor
  narrate per-entity notes would add token cost to the expensive parallel phase. The
  extraction JSON already contains everything a fragment needs.
- **Known limitation.** Synthesis reconciles this run's fragments with the entity note's *carried
  prose*, not a fresh re-read of every source document. The deep, on-demand `/watchdog-entity`
  pass (`pipeline/write_entity.py`, which also re-synthesizes the Timeline) remains the tool for a
  full rebuild of a central figure from all its sources.

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
the events being the document's **dated** `key_facts`, with each fact's `entities` tags
supplying the contributing entity ids (D26) — write-only and lock-free, since each filename
is unique. All merge/dedup and the briefing
then run in `_post_ingest` (model: `post_model`) after extraction:

- `timeline.collisions(vault)` promotes dates with no prior canonical to **canonical**
  `{date}.ndjson` and returns the collisions where a canonical already existed; the
  orchestrator sends each collision's events to one model call (`timeline-dedup`,
  preserving full event objects), writes the deduped set back, then calls
  `timeline.cmd_rebuild_timeline` to render `timeline.md`. If the dedup call fails it
  falls back to the union rather than losing events;
- builds a briefing prompt from the compact per-doc results — which now carry each
  document's `key_facts` (projected to fact + date, the briefing's source for figures and
  chronology) alongside near-dup alerts and contradiction flags — plus the per-document
  scratchpads, now slimmed to forward-looking leads only (D33); makes one model call
  (`briefing`), and `_write_briefing` writes the structured prose into `briefings/<ts>.md`,
  `hot.md`, and a `log.md` entry.

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
morgue/<entity>/<type>/…     original source files + a sibling <name>.md of the
                            Docling full text, filed by subject (D26)
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
- **Reasoning effort** (per-stage, default `high` ≡ the model default): `extractor_effort`
  and `finalizer_effort` (`low`/`medium`/`high`) tune how many thinking tokens each stage
  spends; thinking bills as output, so a lower effort is the per-run cost lever (D36).
  `model_client` maps them to each backend's native control (`output_config.effort` /
  `ClaudeAgentOptions.effort`) and drops them on Haiku-tier stages (classify; any Haiku
  model), which reject `effort`. Overridable per run via `--extractor-effort` /
  `--finalizer-effort`.
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
- **Domain (record) skills are global** (`watchdog.skills_catalog`, see D21): the
  package's `src/watchdog/skills/records/` plus the user's `~/.watchdog/skills/records/`
  (a user skill overrides a package skill of the same name). The ingest orchestrator
  reads them directly from there — nothing is copied into a vault — so they're always
  current with no refresh step. New skills are added by dropping a file (in the package,
  or the user dir). The classification index is **built in memory** from the catalog
  (`build_index()`), so it never drifts (supersedes D12); each skill's index line comes
  from its `description:` frontmatter, falling back to the first intro sentence.
  `watchdog show-skills` lists them / opens the GitHub folder; `--skill` / `default_skill`
  pin one (a catalog name or a file path).

---

## 14. Invariants and decision log

### Invariants (canonical)

These are the **governing rules of the pipeline** — the canonical statement of each principle. They are always true; violating one needs a *new, numbered decision* that supersedes the invariant, not just a code change. Read them first. The decision log below is the dated history of how each was established and refined; where a log entry established or operates within an invariant, *this* section is the authority on the principle and the entry records the specific change, rationale, and tradeoff.

- **I1 — Deterministic code writes; the model only reasons.** Anything derivable in Python (document identity, provenance, slugs, role targets, timeline fan-out) is stamped in code, never paid for in model output — and the model is not asked to restate as prose what it already emitted structurally. *History: D2, D18, D24–D26, D29–D31, D33, D34.*
- **I2 — Local-first preprocessing.** Source documents never leave the machine; chew costs no API tokens. *History: D1.*
- **I3 — Skills and prompt templates are global package resources** — read directly, never copied per-vault — and prompt templates live in their own directory so they never leak into the classifier index. *History: D21, D28.*
- **I4 — Configured model and effort only; no automatic escalation.** Each stage's model *and* its reasoning effort are explicit knobs with stable defaults; a failed call retries on the *same* model at the *same* effort — the pipeline never silently bumps either to recover. *History: D20, D36.*

### Decision log (summary)

The dated record of individual decisions, each operating within the invariants above. An entry is the authority on its *specific* change — the rationale weighed and the tradeoff accepted — not on the general principle (that's an invariant). *Append new decisions in ascending number order — newest at the bottom; when a decision establishes or revises an invariant, update the Invariants section too.*

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
| D12 | ~~Generate `records/_index.md` by scanning the per-vault records dir~~ **Superseded by D21.** | A static index file silently drifts from the skills it describes. Moot once the classify index is built in memory from the global catalog (D21) — no file to scan or keep fresh | — |
| D13 | Sectioned extraction for large documents (§5) | Documents over the token threshold don't fit one context; sequential page-range sections with a carried scratchpad + deterministic `merge-sections` preserve cross-section consistency | Large docs are extracted serially (slower); needs overlap + merge-dedup |
| D14 | ~~Timeline reconciliation + briefing in one finalize subagent~~ **Superseded by D18.** | Kept scratchpad prose and timeline NDJSON out of the model orchestrator's context. Moot under Python orchestration — `_post_ingest` reads them directly | — |
| D15 | Runaway guard + `ingest-abort` (§5) | A stuck subagent bails (`STATUS: failed`) instead of wedging the batch; clean bail leaves no partial vault writes, so the doc re-ingests cleanly | A failed doc must be manually moved back from `queue/_failed/` to retry |
| D16 | ~~Entity synthesis (§8) and finalize (§9) kept as separate subagents, not merged~~ **Superseded by D17.** | Kept the per-entity synthesis fan-out apart from the whole-batch finalize to avoid serializing entity work. Moot once the fan-out itself proved to be the cost problem and both merged into one post-ingest agent (D17) | — |
| D17 | Merge synthesis + finalize into one post-ingest subagent fed a Python-built bundle (§8, §9) | The per-entity fan-out launched one subagent per `count ≥ 2` entity (36 in a representative run), each paying startup + preamble cache-write to do a few hundred tokens of judgement — ~$5 of a $19 run. D16 feared merging would re-bloat context, but fragments are *compact digests* (D8), so one agent reading the whole bundle stays small; the cost win (one agent, one cached preamble) dominates the serialization cost. The bundle's `build_bundle`/`apply_bundle` survive into D18; only the surrounding subagent is gone | A very large batch could need bundle splitting by token budget (not yet implemented) |
| D18 | Python orchestrator; the model is called only for reasoning (#118 W3) | A Claude Code skill session *is* a model loop, so the orchestrator (and per-turn coordination) cost model tokens even for pure dispatch — the orchestrator alone ran $1–3+ of a representative batch, ~38–86% of pipeline spend was per-turn context re-send. Moving the loop to Python (`orchestrate.py`) calling the model only for classify/extract/synthesis/timeline-dedup/briefing removes that floor and lets each task route to a backend + tier (`model_client`, D-auth #119). Supersedes the subagent split (D3) and the off-orchestrator finalize (D14) | Extraction now runs the Claude Agent SDK in-process and parallel concurrency is bounded by subscription/API rate limits (`extract_concurrency` knob); the `claude-api` backend is unproven until a metered key is used |
| D19 | Force-section on whole-doc output overrun (§5) | Sectioning triggers on *input* size (`section_token_threshold`), but truncation is *output*-driven — a moderate-input, entity-dense doc overruns the model's output ceiling on the agent-SDK backend (which can't cap output), truncating the JSON and escalating the retry toward a pricier tier. On a multi-page doc whose whole-doc extraction is rejected, the orchestrator re-runs it through the sectioned path with a small forced budget (≥2 sections) to bound per-call output | Adds one (failed) whole-doc attempt before the fallback; single-page docs can't be split, so they still just fail |
| D20 | Configured model only — no automatic escalation; classifier model is its own knob | `model_client` originally bumped the tier up (haiku→sonnet→opus) on a JSON-validation failure. That makes ingest cost unpredictable and can silently spend opus money — the opposite of a budgeted pipeline. Now a failed call retries on the **same** configured model (the orchestrator's post-flight repair + D19 sectioning handle genuine failures), and the classify step gets its own `classifier_model` knob (default haiku) alongside `extractor_model`/`finalizer_model`, so each stage's model is explicit and stable | A doc that a stronger model would have salvaged now fails instead of auto-upgrading — the user opts into a stronger model deliberately via config |
| D21 | Record skills are global, not per-vault (`skills_catalog`) | Copying skills into every vault was a holdover from the Claude-Code-skill ingest, which needed them under `.claude/commands/` for discovery. The Python orchestrator (D18) just reads the file, so the copy was vestigial and created drift (each vault stale until `refresh-skills`). Skills now live in the package + `~/.watchdog/skills/records/` (user overrides) and are read directly; the classify index is built in memory (supersedes D12); per-skill `description:` frontmatter lets a user-added skill set its own index line. Pinning (`--skill`/`default_skill`, a name or path) and `watchdog show-skills` round it out. Pre-production, so vault-local copies are simply ignored (strictly global) rather than migrated | Loses per-vault skill freezing/customization; mitigated by `--skill PATH` for one-offs and by stamping the skill used into each document's note + registry (`record_skill`) for provenance. Custom skills in `~/.watchdog` aren't carried with a shared vault |
| D22 | Extractor emits structured **evidence fragments** instead of prose `analysis`; optional verbatim `quote` on fragments and `key_facts` (#107) **— the extractor no longer emits fragments directly (D26); they are reconstructed from tagged `key_facts`, but everything downstream (the structured-claims-feed-synthesis digest, the `## Analysis` render) is unchanged.** | The per-entity `analysis` prose was thrown away and rewritten by synthesis for every multi-mention entity (D7/D17), so the extractor did prose work twice. Replacing it with `{claim, page, confidence, reason}` fragments gives the synthesizer a clean, page-anchored, citable digest to compose from rather than re-prosing prose — and single-mention notes render the claims directly under `## Analysis`. Quotes are *captured* at extraction only when the wording is significant, and on entity fragments are *surfaced* into synthesized prose only in exceptional cases (the synthesis prompt gates this); on `key_facts` the quote renders verbatim in the document note (terminal, reader-facing — the strongest placement). This is a **quality/citability** change, not a cost trim (structured fragments cost slightly more output tokens than the prose they replace) — the cost trim is the separate #127 | More structured extractor output; a captured quote against garbled OCR isn't verified to appear on the page (that check is the deferred #106 evaluator); existing notes keep their old prose `## Analysis` until the entity is next extracted |
| D23 | Post-ingest is a re-runnable `finalize` step over per-run on-disk inputs (#135) | Post-ingest (synthesis + timeline + briefing) calls the model, so a rate limit can interrupt it *after* extraction has already written the vault — and `ingest_setup` wipes the fragment queue at the next ingest's start, so a re-ingest would silently drop the un-synthesized batch. Post-ingest is now `orchestrate.finalize`, fed inputs that persist in `tmp` (entity fragments + per-doc `result_*.json` + scratchpads); `watchdog ingest` calls it at its tail, and `watchdog finalize` re-runs it standalone to complete an interrupted batch. A clean pass clears the inputs (idempotent, and fixes a latent scratchpad-accumulation bug where briefings re-included prior runs); an interrupted pass leaves them for retry. A new `watchdog ingest` over a pending batch prompts to **merge** (keep the inputs via `wipe_pending=False` so both batches finalize together), **finalize** first, or **discard**; `watchdog status` flags a pending batch | No cross-run finalization queue beyond the merge prompt; synthesis re-runs are idempotent (bulk overwrite), so a partial finalize is safe to repeat |
| D24 | Roles are extracted by `target_id` only; `target_name`/`target_type` are re-inflated deterministically (#140) | Extraction output is ~95% of run cost and roles were the single largest field — and ~43% of the roles payload was the target's `target_name`/`target_type`, both *derivable from `target_id`* via the registry, re-typed by the model on every role (e.g. an 86-char case name repeated across a dozen roles). The extractor now emits roles by id; `write_vault._resolve_role_targets` fills name/type from this batch's entities + the registry right after id reconciliation, so every downstream consumer (note links, pre-flight context, synthesis digest) is unchanged. First concrete cut from #140 — moves derivable data out of paid model output into free deterministic code | A dangling `target_id` (no matching entity) falls back to the id as name + `Unknown` type; the win is only realized while extraction stays on a metered tier (Sonnet output is the cost) |
| D25 | `confidence` is omitted when `high`; absent ⇒ `high` (#140) **— field reworked into `basis` by D34; the omit-default mechanism survives, the 4-level scale does not.** | Measured across a 5-doc run, **99% of all confidence values were `high`** (411 rendered, 8 non-high). Requiring it on every fact/role/event/key-fact paid model output to restate the default ~99% of the time (~6% of extraction output). The extractor now emits `confidence` only for `medium`/`low`/`disputed`; the schema makes it optional and every consumer defaults absent → `high`, so notes render identically and the rare exception stands out instead of hiding in a sea of `high`. Same omit-defaults pattern extends to `page` (omit when no marker) and empty arrays | Defaulting absent → `high` is the *optimistic* direction — a forgotten mark silently reads as `high` — but empirically the model flagged all 8 non-high cases, so exposure is ~8-in-411. Whether `confidence` is well-calibrated at all (≈always `high`) is a separate quality question (#143) |
| D26 | Unified **fact primitive**: the model emits each material fact once on `document.key_facts`, tagged with `entities` + an optional `date`; postflight fans it back out (#140) | The extractor restated the same fact up to three times — as a `document.key_fact`, as each involved entity's `evidence_fragment`, and (if dated) as each entity's `timeline_event` — plus a per-entity `summary`, so a 5-page order emitted ~4× the source text in JSON (32K chars from 7.7K of Docling markdown). That redundancy was rational only because the Docling text was **discarded** after extraction, making the JSON the sole text record. D26 retains the full text in the morgue as a sibling `<name>.md` (deterministic, $0), then collapses the restatement: the model emits a fact once with `entities` tags (who it's about) and an optional `date` (when it occurred); `postflight.explode_key_facts` deterministically reconstructs the per-entity `evidence_fragments` and `timeline_events` the writers already consume, and `stage_timeline_events` reads the dated facts directly. Entities keep only the graph layer (identity, aliases, roles, contradictions); no per-entity summary/fragments/timeline. `key_facts` is materiality-driven with no fixed count. Stacks on D24/D25. Synthesis is now gated on **project-wide recurrence** (`appears_in ≥ 2`) rather than batch-local mention count, so an entity recurring across separate batches/years is promoted; single-document entities are deterministic stubs with no Summary section (§8) | A single-document entity gets no synthesized summary at all (by design — its facts sit in `## Analysis`), and a model only ever judges an entity from one document plus carried prose, never a fresh re-read of all sources (that's `/watchdog-entity`). A dated fact must be *tagged* to reach an entity's per-entity timeline (the global timeline still gets it untagged). Timeline recall now depends on the model attaching `date` to occurrence-facts rather than filling a dedicated field |
| D27 | Skip exact duplicates at chew, not just at ingest (#146) | Re-extraction was already a no-op — `preflight` skips a sha already in `documents.json` before any model call (§5). But the wasteful part remained upstream: a re-dropped file was still re-OCR'd, queued, then quietly skipped at ingest. Chew now hashes each file first and, if the sha is already ingested / already queued / a repeat within the batch, moves it to `_INCOMING/_SKIPPED/` with a warning instead of OCR'ing it. Chosen over a content-addressed *text cache*: skipping is simpler (a hash + set lookup, no persistent store or version-invalidation), covers the same cases, and is the correct intent — "I already have this document," not "re-OCR it cheaply." | Exact-bytes only — a near-duplicate (re-scan, different export) has a different sha and still chews (the MinHash flag handles that, §3). Files dropped *simultaneously* with identical bytes keep the first and skip the rest, which is right for true duplicates but would also skip a deliberately-reprocessed copy |
| D28 | Task-prompt prose lives in `prompts/*.md` templates, in a **separate directory** from record skills | `prompts._text` loads a template (cached) and `_render` substitutes `{{token}}` placeholders; the builders still own all data assembly and conditionals in Python (model copy out of code, logic in code). The load-bearing constraint is the separate directory: `skills_catalog` only ever scans `skills/records/`, so a prompt template never leaks into the classifier index or `watchdog show-skills`. Packaged like the skills `.md` (hatchling bundles non-`.py` files under `src/watchdog`) | Editing a template changes model behavior with no type-checking — prompts are plain-text copy to be reviewed like prose. Whitespace was naturalized for markdown, so the assembled prompt is semantically identical but not byte-identical to the old string literals |
| D29 | Stop round-tripping deterministic document fields through the model; stamp them in Python (#140 family) | The extractor was asked to echo back document **identity** (`sha256`, `filename`, `original_path`, `page_count`) — values the pipeline already holds from `preflight` — and to re-derive **provenance** (`source`, `obtained`) by parsing the `.yml` sidecar text it was handed. Both are deterministic: Python knows the identity, and the sidecar is structured YAML. `orchestrate._stamp_document` now sets all six on the extraction right before post-flight (`_sidecar_provenance` parses the sidecar with `yaml.safe_load`, coercing an auto-parsed date back to a string); the schema drops `sha256`/`filename` from `_DOCUMENT.required` (kept optional in `properties` so the stamped dict validates) and the extract/section prompts no longer instruct the model to produce them. The sidecar text still reaches the model as extraction context (its free-form `notes`). Same lever as D24/D25 — derivable data out of paid output — plus a correctness win: `write_vault` keys the vault write on `document.sha256`, so stamping removes a latent desync if the model ever mis-transcribed the 64-char hash. Also dropped the never-read `document_type` from the classify call's schema/prompt (it was discarded; the `document_type` the vault uses is the separate extraction-stage field) | Provenance parsing is now strict YAML rather than the model's lenient reading — a malformed sidecar yields no `source`/`obtained` (returns `{}` on `YAMLError` or a non-mapping) instead of a best-effort salvage; the documented `key: value` sidecar format is unaffected |
| D30 | `document_type` is deduped against a per-vault registry (match-or-coin), its slug is derived, and dead/duplicate type fields are dropped (#140 cleanup, stacks on D29) | An audit of the extract schema found three more issues with the document-type fields. (1) **Drift:** `document_type` is a free string the model coined per document with no guidance, so the same instrument came out worded differently across docs — fragmenting the `watchdog status` "Documents by type" tally. Fixed with the same dedup pattern the pipeline already uses for entities: `preflight` collects the distinct types already in `documents.json` (the registry is the vault itself — no new file), passes them as `KNOWN_DOCUMENT_TYPES`, and the prompt instructs reuse-verbatim-or-coin-new. (2) **Redundancy:** the model emitted both `document_type` (descriptive, e.g. "Annual Report") and `morgue_document_type` (the slug, e.g. "annual-report") — the same fact twice; `_stamp_document` now derives `morgue_document_type = slugify(document_type)` and it leaves `EXTRACTION.required`. (3) **Dead field:** `near_duplicate_of` was in the model schema but never prompted and always sourced by Python from the MinHash pass — removed from the model-facing document schema. `title`/`document_type`/`date_of_document` (schema-required or note-rendering, but previously undescribed in the whole-doc prompt) are now documented in `extract_instructions.md` | The known-types list is a `preflight` snapshot, so two genuinely-new types in the **same parallel batch** can still be coined inconsistently (neither is in `documents.json` yet) — the same race the entity layer reconciles post-merge; a type-reconciliation pass is deferred until it bites. The registry only shapes new docs going forward; types coined before this change aren't retroactively normalized |
| D31 | Timeline-dedup returns kept **indices**, not echoed event objects; and collapses only pure restatements, never divergent accounts | The same-date dedup step (post-ingest, only on cross-document date collisions) had the model echo back the full kept event objects — re-typing a 64-char `source_sha256`, `page`, `confidence`, and the event text per event, all of which Python already holds. It now returns `keep` (a list of indices); `orchestrate._select_kept` maps them to the **original** objects (deduped, order-preserving) and falls back to keeping **all** events on any unusable response (non-list / out-of-range / empty), so dedup can drop a redundant restatement but never silently wipe a date. The criterion was also tightened: only **pure restatements** (same facts, different words) collapse to the most precise wording; any event adding a material fact, detail, or distinct perspective is kept — including two sources describing the same day differently (e.g. opposing legal teams), which stay as separate rows each attributed to its own `source_sha256`. The model no longer rewrites event text (indices only), so kept events are preserved verbatim | "Merge with attribution" is realized as co-dated, separately-attributed rows, **not** a single fused event — the timeline event model carries one `source_sha256` per row, so a literal one-row merge citing multiple sources would need a schema change (multi-source events) and is deliberately not done; it would also blur the clean per-document provenance the vault relies on. Dedup correctness still rests on the model's same-occurrence judgment (unchanged risk from before), now biased toward keeping |
| D32 | Advisory page-coverage warning flags a likely skim | A classic LLM failure is not reading a long document to the end. Extraction can't *enforce* reading, but `key_facts` carry a `page`, so `orchestrate._coverage_warning` deterministically flags a probable skim: for a document of ≥ `_COVERAGE_MIN_PAGES` (8) pages, if no fact cites anything past roughly the first half (`_COVERAGE_TAIL_FRACTION`), it prints a yellow `⚠` after the `OK` line and logs a `WARN`. Pairs with the read-every-page instruction now in `extract_instructions.md` (the prompt nudges; this catches when the nudge failed). Purely advisory — never fails the extraction, since a document whose material genuinely sits up front trips it too. Real *enforcement* of coverage remains structural (large-PDF sectioning, the `chunk_size` knob — §6), not this heuristic | Heuristic with false positives (front-loaded-but-complete docs) and false negatives (a model that fabricates a single back-page fact passes); it is a review prompt, not a guarantee. A doc with no page anchors at all can't be assessed and is silently skipped |
| D33 | Briefing reads figures + chronology from `key_facts`; the scratchpad is slimmed to forward-looking leads (#150) | The per-document scratchpad — free-text notes the model wrote to feed the end-of-batch briefing — was ~19% of one doc's extraction output, and ~half of it (its "Key figures" and "Chronological" sections) hand-retyped facts the model had already emitted structurally in `key_facts`, paying to state the same facts twice as prose. The briefing couldn't simply drop those sections, because it **only read the scratchpads** and was never handed `key_facts`. So this is a swap, not a trim: `_compact_result` now carries a `key_facts` projection (`_briefing_facts` → fact + date only, dropping page/confidence/entities/quote as narrative noise), the briefing prompt draws figures and chronology from those structured facts, and the scratchpad/`observations` instructions are slimmed to *forward-looking* notes only (leads, open questions, cross-document threads) with figures/chronology/contradictions explicitly excluded. Stacks on D26 — same principle of not emitting prose that duplicates structured data. The compact result is also the standalone-`finalize` input (D23), so putting `key_facts` there keeps an interrupted batch's briefing fully reconstructable from disk | Briefing input gains the (deduped) `key_facts` and loses the scratchpads' restated figures — roughly a wash on tokens, a net output cut at extraction. "Leads only" is a softer instruction than a structured field, so thin or empty scratchpads are possible (an empty scratchpad just yields no notes file for that doc); contradictions still reach the briefing via the separate `contradiction_flags`, not the scratchpad |
| D34 | `confidence` (4-level) → `basis` (`stated`/`inferred`) — provenance, not graded certainty (#143) | An audit of the 4-level `confidence` field (D25) found ~99% of values were `high`, raising the question of whether the field carried signal. The diagnosis: the scale conflated two axes and overpromised on a third. (1) `high`/`medium`/`low` claimed to grade *inference depth* (none / one step / multi-step), but there's no evidence the model discriminates one-step from multi-step, and the journalist's action is identical for any inferred fact — *verify it*. (2) `disputed` is not a confidence level at all but a *cross-document conflict* signal, fully redundant with the `[!contradiction]` mechanism (and never emitted in the sample). So the field collapses to the one distinction that is both real and decision-relevant: did the document **state** this, or did the model **infer** it? The field is renamed `basis` with enum `stated`/`inferred`, keeping D25's omit-default (absent ⇒ `stated`, the overwhelming default — so the model emits `basis` only for the rare `inferred` exception). `disputed` leaves the field entirely (conflicts live only in `[!contradiction]`); the contradiction gate is retightened from "both sides high or medium" to "both sides stated". Renderers now show only the marked exception — `*(inferred)*` — across key_facts, per-entity claims, timeline events, and roles (fixing a D25-era inconsistency where three of four renderers printed `confidence: high` on every line, burying the signal the design meant to surface). A binary keyed on "is this literally on the page?" is also more honestly calibrated than a self-rated scale — a sharper, checkable question is harder to reflexively max out | Loses any ability to grade inference *depth* (a deep chain reads the same as a one-step guess) — judged not worth keeping, since page cite + quote let the reader gauge that and the reader's action doesn't change with depth. Pre-production, so no migration: notes written under the old scale keep their `confidence: <level>` text until the entity/document is next extracted. Absent ⇒ `stated` is still the optimistic direction — a forgotten mark reads as directly-stated — but the question "is it on the page?" is more concrete than "rate your confidence," which should reduce silent misses |
| D35 | Watch-word alerts are a deterministic post-ingest text scan, no model (#165) | A user wants to be alerted when a newly-ingested document mentions a tracked name/company/address/term. This is done entirely in deterministic Python — no model call — because the scannable material already exists for free: D26 retains each document's full text as a page-aligned morgue `<stem>.md` (with `<!-- PAGE N -->` markers). `pipeline/watchlist.scan` reads the vault-root `watchlist.md` (one term per line; `#` comments; literal terms matched case-insensitively on word boundaries via lookarounds so leading/trailing punctuation still matches; a `/.../`-wrapped line is a regex), greps this run's `ok` documents, and records term + document + page (resolved from the nearest preceding marker) + a bolded context snippet. Each hit is *annotated* — not gated — with the matching registry entity when `m.group(0)` resolves against `manifest.json` (name/aliases), so the text scan stays complete while gaining entity links where available. It runs as step 4 of `_post_ingest` (after the morgue text and registry both exist), writes `briefings/alerts-<date>.md` (dated, append-per-run, matching the `surface-<date>` convention) and prints a console summary; an empty/absent `watchlist.md` is a silent no-op. Chosen over (a) entity-only matching (recall gaps — misses terms the model never surfaced as an entity) and (b) a chew-time pre-scan (would predate the registry, losing annotation) | Matches only what survived chew/OCR into the morgue text, and a term not extracted as an entity gets no link (by design — completeness comes from the text layer). Scans only this run's documents; retro-scanning the existing vault after editing the list (a `watchdog watch scan` command) is deferred until wanted. Per-document matches for one term are capped at 50 to bound a too-broad regex |
| D36 | Per-stage **reasoning-effort** knobs as an output-token cost lever (#163) | The #140 validation run concluded the one remaining cost lever was *output-token volume per doc*, and thinking tokens **bill as output**. Neither backend set any thinking parameter, so the model thought at each backend's default (`high`). `extractor_effort` and `finalizer_effort` (`low`/`medium`/`high`, default `high`) are now config keys — mirroring the per-stage *model* knobs — resolved in `model_client` and mapped to each backend's native control: `output_config.effort` (claude-api, composed into the same dict as the structured-output `format`) and `ClaudeAgentOptions.effort` (claude-agent-sdk). `_resolve_effort` drops the knob on Haiku-tier models (which reject `effort` with a 400 — so the Haiku **classify** stage has no knob) and treats `high` as "no override" (it ≡ the model default), so an unconfigured pipeline sends nothing and behaves exactly as before. The lever lives in `model_client` (the backend-abstraction layer), not the orchestrator, because the available controls are provider-dependent (#125/#137): the config key is an abstract `low`/`medium`/`high` *intent* that other backends can map to their own reasoning-effort parameter (OpenAI `reasoning_effort`, etc.) or ignore. Operates within I4 — explicit knob, stable default, no auto-escalation | The deliverable per the cost-reduction thread is a measured A/B (default vs `extractor_effort=low`/`medium` on a fixed corpus via `scripts/analyze-session`); the knob ships structured and tested, but whether a lower effort actually cuts cost *without* degrading extraction quality is a numbers question to settle on a real run. `xhigh`/`max` (Opus-only) are deliberately not exposed — the lever is for spending *less*, and `low`/`medium`/`high` is the portable set across providers |
