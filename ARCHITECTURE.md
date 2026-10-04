# Watchdog — Architecture

How the pipeline is built, and the rules that govern it (§15). This file describes the current
state; the dated rationale for each decision is in [DECISIONS.md](DECISIONS.md), cited here as
`Dn`. Keep it current: a change to the pipeline's structure, the code/model split, the
vault/registry layout or an invariant updates this file in the same change (see
[CLAUDE.md](CLAUDE.md)).

---

## 1. Design principles

- **Local-first.** Chew (text extraction, OCR, near-duplicate detection) and the search indexes
  run on the user's machine and cost no API tokens. Network calls are the configured model
  provider's API during `dig`/`bark`, plus the opt-in web research, capture and Wayback features
  (§14).
- **Deterministic code writes; the model decides.** File writing, merging, sorting, dedup and
  registry bookkeeping are Python. The model is used only for judgement: classifying, extracting,
  reconciling, synthesizing prose, writing the briefing.
- **Cost is a budget.** Work goes to the cheapest layer that can do it correctly; model work is
  gated, bounded and fed pre-digested input.
- **Parallel extraction, serial writes.** Documents extract concurrently; every vault write
  happens in one serial commit pass (I7).
- **Two runtimes.** The document pipeline (`chew`, `dig`, `bark`) is a terminal program whose
  single-shot, schema-bound calls go through a provider-agnostic `model_client`, so each stage can
  run on Claude, OpenAI, DeepSeek, Gemini, OpenRouter or a local model (D37, D94, D139). The
  investigation layer (`/watchdog-query`, `-surface`, `-wiki`, `-entity`, `-context`, `-health`,
  `-research`) runs inside Claude Code as interactive sessions and is not portable: Claude Code is
  a hard requirement. "Make it backend-portable" applies to pipeline steps only (D18).

---

## 2. Pipeline overview

```
_INCOMING/ ─▶ chew ─▶ .watchdog/queue/<sha>.json ─▶ dig ─▶ .watchdog/extracted/<sha>.json ─▶ bark ─▶ vault
 (raw docs)  (local)        (page text)          (extract)     (staged extraction)        (commit + finalize)
```

1. **Chew** (`watchdog chew`) — local, no model. Text/OCR, large-PDF chunking, near-duplicate
   fingerprinting. One queue JSON per document.
2. **Dig** (`watchdog dig`) — the orchestrator (`pipeline/orchestrate.py`) classifies and extracts
   each queued document concurrently and stages the validated result. Touches no vault state.
3. **Bark** (`watchdog bark`) — resolves the staged batch (exact-name fold, entity reconciliation),
   commits it to the vault in one serial pass, then runs post-ingest: contradictions, entity
   synthesis, timeline dedup, the briefing.

Bare `watchdog` walks through all three; `watchdog ingest` (deprecated, D138) runs dig and bark
together. A failed document is set aside in `queue/_failed/` without sinking the batch.

---

## 3. Chew (preprocessing)

**Code:** `pipeline/preprocess.py`, `pipeline/preprocess_batch.py`, `pipeline/near_dup.py`,
`pipeline/sidecar.py`, `pipeline/file_metadata.py`.

- **Text.** Direct text where the file has it; Docling with OCR otherwise. Output is per-page
  markdown.
- **Page-scoped OCR (D189, D192).** Every PDF page is scored by three signals (character ratio,
  word shape, font CMap; two must agree). Pages without a usable text layer are force-OCR'd; the
  rest keep theirs. Pages are grouped by verdict into at most two classes, split at `chunk_size`
  (default 40), converted in parallel subprocesses and spliced back by page number.
- **Exact duplicates are skipped before OCR (D27)** — sha256 against the registry, the queue and
  the current batch. A match goes to `_INCOMING/_SKIPPED/`.
- **Near-duplicates (D10).** Word shingles (`shingle_size`) reduced to a MinHash signature,
  compared against committed, queued and same-batch documents in one vectorized index
  (`NearDupIndex`). Matches at or above `dup_threshold` (0.85) are flagged, never discarded. The
  signature is stored in `documents.json`.
- **Sidecars (D121).** A document's `.yml` sidecar is read only here: filtered to an allowlist,
  values length-capped, embedded in the queue JSON, and the file deleted. Every later reader takes
  it from the queue JSON.
- **Embedded file metadata.** PDF DocumentInfo, Office properties, EXIF and container tags are
  read from the original file, normalized to a small allowlist and capped at 200 characters
  (untrusted input, I6). Stored as `file_metadata`, separate from `metadata` (how the pipeline
  read the file).
- **Output.** `.watchdog/queue/<sha>.json` (filename, page count, per-page markdown, near-dup
  result, signature, sidecar, metadata); the original moves to `.watchdog/staging/<sha>/`.

---

## 4. Run setup, locking and estimates

**Code:** `pipeline/ingest_setup.py`, `pipeline/locks.py`, `cmd/ingest.py`.

A run resolves auth, takes the run lock, scans the queue, and runs `orchestrate.run` in-process.
Models, efforts, concurrency and classification come from `watchdog configure` or per-run flags;
their defaults live in `watchdog/defaults.py`.

- **Locks (D66, D69).** The ingest, finalize and chew locks are created with `O_CREAT|O_EXCL`. A
  lock older than 30 minutes (`STALE_SECONDS`) is taken over; one with an unreadable timestamp is
  left for `watchdog unlock`. A running holder re-stamps its lock every five minutes from a daemon
  thread, so a long run never looks stale. A finer `.write-lock` (`flock`/`msvcrt`) serializes
  registry writes.
- **Estimates (D72, D135, D143).** `--estimate` multiplies the queue's token estimate (chars/4,
  calibrated against this vault's past runs) by this vault's recent $/token, as a range. `bark
  --estimate` prices the staged batch from standalone-finalize history. `--estimate-all` prices
  every catalogue model at list rates, scaled by each model's `tokenizer_ratio`. Subscription auth
  gets token counts, never dollars.
- **Rate limits (D71, D184).** A `RateLimitError` stops the batch cleanly; unfinished documents
  stay queued and the next `dig` resumes. `--wait` sleeps until the reported reset and loops,
  refreshing the lock. The stop message reports the run's observed tokens/min and the provider's
  last reported remaining/limit.
- **Admission control (D185).** A document is held before dispatch when recent tokens/min, plus
  every in-flight document's reservation (`_run.admission_reserved`), plus its own estimate would
  exceed the budget (`extract_token_budget`, else the provider's reported limit, else ungated).
  Reservations are set before the check because every document is dispatched in the same
  event-loop tick. A waiting document is force-admitted after a cap. Not engaged on
  `claude-agent-sdk`, which reports no limit.
- **Auth failures (D242).** A provider auth or billing error stops the run with exit code 1
  instead of quarantining every document.

---

## 5. Extraction (`dig`)

**Code:** `pipeline/orchestrate.py`, `model_client.py`, `model_catalog.py` + `model_catalog.yaml`
(D142), `pipeline/prompts.py` + `prompts/*.md` (D28), `pipeline/schemas.py`,
`pipeline/preflight.py`, `pipeline/postflight.py`.

`run()` reads each queued document's pre-flight once to size it (page text is dropped and re-read
at extraction, and the registry is parsed once for the queue, D248), then extracts documents
concurrently under `extract_concurrency`. Run state lives in one `_RunState` object. Per document:

1. **Pre-flight** — page text, processing facts, and `known_document_types`. It reads no entity
   state: extraction is a pure function of the document (D118).
2. **Classify** — one cheap call over the first `classify_pages` pages, the sidecar and the
   in-memory skill index, returning a record skill (§6). Skipped when a skill is pinned. An unknown
   answer falls back to `general-records`.
3. **Extract** — one call against `EXTRACTION`. Two layers (D26): a fact layer
   (`document.key_facts`, each fact written once, with optional `date`, `entities` and a short
   `quote_locator`, D170) and a graph layer (the entities this document names, with aliases and
   roles). No vault matching and no contradictions — those need the whole batch (§8.5). Optional
   `document_requests` name obtainable documents the text cites (D111). Embedded file metadata is
   rendered as a data block. A model without a private reasoning channel also gets a scratch
   `document.plan` field, filled before `key_facts` (D208).
4. **Verify** (optional, `verify_extraction`/`--verify`, D172) — a second, cheap call over the
   same text asking what material fact is missing. On `claude-api` it re-reads the extraction
   call's prompt cache, so it runs on `extractor_model` at `verifier_effort`. Candidates are merged
   in code (`verify.merge_candidates`): sanitized, and suppressed when they near-duplicate an
   existing fact anywhere in the document (D199). Survivors are ordinary facts tagged
   `added_by: "verify"` in the staged JSON. Not available on batch backends.
5. **Post-flight** — validates the JSON; resolves each `quote_locator` to the full sentence on the
   cited page, correcting `page` when the locator resolves uniquely elsewhere (D75, D177); checks
   each fact's figures against the page text and annotates figures found nowhere or only on
   another page (D112, D200); explodes `key_facts` into per-entity fragments and timeline events
   (D26); flags a file-metadata creation date that postdates the document's own date (skipped for
   OCR'd scans); stages raw timeline NDJSON; and writes the validated extraction to
   `.watchdog/extracted/<sha>.json` (D126). That artifact is kept as an audit record.

**Two "already done" questions (D126).** `already_staged` (an artifact exists: skip
classify/extract) and `already_extracted` (the sha is committed: nothing to do).

**`--force` (D131).** `dig --force` re-extracts staged documents, overwriting the artifact. To
regenerate a committed document, `watchdog ingest --force <doc>` re-chews it from the morgue,
re-extracts it, lists the notes it will replace and confirms (default Cancel) before passing it to
finalize as `force_shas`.

**Large documents (D89, D196, D202).** A document over `section_token_threshold` is split into
overlapping page-range sections, packed greedily by per-page token estimates, extracted one at a
time with a carry-forward block (entity map plus the previous section's observations), and merged
(`merge.merge_extractions`). Threshold and budget default to 0.6 and 0.3 of the extractor's context
window, clamped below any long-context pricing tier (`long_context_threshold`, in real tokens),
then divided by the model's `tokenizer_ratio` (catalogue value, or the vault's own calibration,
D180, D190, D198). Each section's result is checkpointed (D157) so a retry replays finished
sections. A sectioned document's `document.summary` is composed afterwards by one small
extractor-tier call over the merged facts (`_compose_digest`), with a deterministic fallback;
whole documents write it inline.

**Output caps and truncation (D19, D104, D197).** The wire `max_tokens` is one per-model envelope
(`_wire_max_tokens`: the catalogue's `max_output_tokens` under 10% headroom). A max-token stop is
read from the provider's stop reason and never accepted. `claude-api` and DeepSeek continue a
truncated response by prefilling it (same model, same effort — not escalation, I4). A rejected
whole-document extraction is force-sectioned and retried; the worst case is a loud quarantine.

**Prompt caching (D51, D181, D195).** Prompts are built as content blocks: stable instructions,
then the skill (with the cache breakpoint), then per-document data. `claude-api` sends the blocks
as-is; GPT-5.6+ on OpenAI gets explicit cache breakpoints; other OpenAI models get a
`prompt_cache_key`. Everything else receives flattened text.

**Candidate harvest (D123, D223).** Before the prompt is built, `pipeline/harvest.py` collects
money, figures, percentages, dates and court file numbers by regex, plus names from the local
GLiNER model, into a per-page checklist the prompt includes.

**Failure.** A document whose extraction or post-flight fails is logged to `ingest.log` and moved
to `queue/_failed/` by `abort.run`, keeping section checkpoints so a retry resumes.
`watchdog requeue` puts failed documents back.

---

## 6. Classification

A dedicated cheap call before extraction (`classifier_model`, default haiku) picks one record
skill from the in-memory index (`skills_catalog.build_index()`); Python injects that skill into the
extraction prompt.

- **Pinning.** `--skill`/`default_skill` skip classification for the run. A sidecar's `skill:` line
  pins one document and wins over a run-wide pin (D120); it resolves by catalogue name only (D241).
- **Provenance-aware (D84).** The classifier sees the sidecar as data, bounded by a schema that only
  admits a skill name.
- **Universal red flags (D114)** live in `extract_instructions.md`; a skill adds type-specific ones.
- A sectioned document is classified once, before sectioning.

---

## 7. Entity notes

| Section | Kind | Written by |
|---|---|---|
| `## Summary` | prose | bundled synthesis, once an entity is in 2+ documents (§8) |
| `## Analysis` | tagged-fact claims, then prose | deterministic claims; synthesized prose once 2+ documents |
| `## Contradictions` | cited callouts | reconciliation (§8.5); append-only, deduped |
| `## Timeline` | dated events | deterministic, sorted by event date |
| `## Relationships` | roles | deterministic merge |
| `## Notes` | journalist annotations | never touched |

Structured content is merged deterministically; prose is synthesized. Contradictions stay a
discrete cited log rather than prose or a timeline (sorting them by document date would be the
wrong axis).

---

## 8. Entity synthesis

**Code:** `pipeline/synthesis_bundle.py`, `pipeline/finalize_entity.py`.

An entity earns a synthesized Summary once its `appears_in` reaches 2 documents, counted across the
whole vault (D26). Only entities named in this batch are candidates; the batch is the run's
`result_*.json` set, so a resumed run still re-synthesizes it (D129). `build_bundle` rebuilds each
entity's per-document fragments from the staged extractions, and the model rewrites Summary and
Analysis in calls of at most 25 entities (D238). Synthesis never touches Contradictions, Timeline,
Relationships or Notes; an entity the model omits keeps its prose. The prompt weights the whole
body of evidence, so one passing mention doesn't redefine an established entity.
`/watchdog-entity` (`write_entity.py`) is the on-demand full rebuild from every source.

---

## 8.5. Reconciliation

**Code:** `pipeline/reconcile.py`, `pipeline/chunking.py`, `pipeline/merge_entities.py`.

One reconcile call (split into context-bounded chunks when large, D237) does two jobs that need
every document's claims side by side:

- **Entity resolution.** The exact-name fold (`_batch_exact_fold`, D127) has already merged exact
  normalized-name duplicates in the staged batch. `candidate_pairs` blocks the rest: same canonical
  type, name-token Jaccard ≥ 0.5 (subsets score 1.0), at least one side touched this run, found
  through an inverted index on rare tokens and capped at 2,000 pairs. The model confirms or rejects
  each pair; `apply_merges` applies them by what is already committed (D128): a staged-only loser
  is an id rewrite, two committed sides get `merge_entities.run`, and a committed side always
  survives. Merges apply **before** the commit pass.
- **Contradiction detection.** For each entity in 2+ documents, the model compares its
  source-attributed claims and returns structured conflicts. `apply_contradictions` files each
  through `contradiction.run`, which validates both document slugs (D81). Applied **after** commit,
  because it needs the committed documents registry. Basis does not gate a contradiction (D214).

A reconcile failure defers the whole batch: nothing commits, and the next `bark` retries (I7). A
contradiction failure after commit only leaves those callouts for a later run.

---

## 9. Finalize (`bark`)

**Code:** `orchestrate.finalize`, `orchestrate._post_ingest`, `pipeline/timeline.py`,
`pipeline/requests.py`, `pipeline/leads.py`, `pipeline/watchlist.py`, `pipeline/resolutions.py`.

Order: **exact-name fold → reconciliation merges → commit → post-ingest.**

**Commit pass (`_commit_pending`, D126, D239).** Every staged extraction not yet in
`documents.json` (plus any `force_shas`) is replayed through `write_vault.run` in sorted sha order,
so the vault doesn't depend on completion order. `write_vault` canonicalizes entity types to a
closed six-value vocabulary (D105), merges entities, writes entity and document notes, defangs
model- and document-supplied text so it can't forge a wikilink (D241), moves the source to the
morgue, and updates the registries. Registries are held in memory for the pass
(`RegistryBatch`) and persisted atomically — after every document for passes up to 50, every 50
otherwise; a document's queue file is removed only after the flush that recorded it. A
mid-document failure rolls that document back in memory. Search indexes are written after the
registry, keyed for idempotent replay (D67).

**Post-ingest:**

- **Contradictions** (§8.5), then **entity synthesis** (§8).
- **Timeline.** Each document stages raw `{date}_{sha7}.ndjson` files; `timeline.collisions`
  promotes uncontested dates and returns dates with events from several documents (including
  several from one batch, D240). Each colliding date gets one dedup call (five dates at a time, at
  most 200 events per call, D248); `_select_kept` keeps the originals and unions entity ids onto
  each survivor (D59). A failed call leaves the date untouched for the next run (D65). Months
  mixing month- and day-precision events get a precision pass that can only fold a coarse event
  into a day (D63). `cmd_rebuild_timeline` is the single renderer of `timeline.md`.
- **Briefing.** One call over compact per-document results (key facts, entity names, near-dup
  alerts, contradiction flags) and the scratchpads, condensed in steps when the batch is large
  (D238). `_write_briefing` writes `briefings/<ts>.md` (a counter suffix on collision), `hot.md`
  and a `log.md` entry. `--skip-briefing` skips the call and those three files (D134).
- **Leads, watch-list alerts and document requests.** Model-free sweeps write dated briefing files;
  `resolutions.json` holds acknowledgments so handled items don't resurface (D68). Document
  requests are content-keyed in `requests.json`; a dedup call (at most 200 per call) folds
  paraphrases, and `requests.md` is re-rendered (D111, D159).
- `watchdog contradiction-add` lets a journalist promote a candidate contradiction found by
  `/watchdog-surface`, through the same deterministic writer (D82).

A batch is always finalized, never discarded (D242); `bark` with nothing pending reports so.

---

## 10. Near-duplicate detection

See §3. Detection only: the journalist decides whether two documents are the same. The dashboard
lists them as "Possible duplicate documents".

---

## 11. Search

**Code:** `pipeline/embed.py`, `pipeline/fulltext.py`, `cmd/reindex.py`.

- **Semantic index (D38, D43).** Local fastembed vectors (`embed_model`) for overlapping word
  windows of each page — each window is a citable passage — plus one per note. Built at commit,
  with a contextual prefix (title, type, entities) prepended before embedding. Index files are
  keyed by document sha (D241).
- **Hybrid ranking.** Corpus passages: dense cosine and BM25 fused by reciprocal rank, then
  reranked by a local cross-encoder (`rerank_model`; `none` or `--no-rerank` disables it). Notes:
  cosine only, shown as a separate section. A derived cache (`.embeddings/_cache/`, numpy only,
  fingerprinted on file names/sizes/mtimes) makes repeat queries fast.
- **Full-text lane (D57).** SQLite FTS5 over morgue pages and every generated note: every exact
  occurrence, unscored. `--batch` checks a term list (manifest names plus full text, no semantic
  lane); `--everywhere` runs the same lanes across every registered vault (D72). A failed
  full-text lookup is reported as "not checked", never as no hits.
- **`watchdog reindex` (D53)** rebuilds both indexes from the registry and morgue text, with no OCR
  or model calls — the way to change `embed_model`.

---

## 12. Vault & registry layout

```
entities/<type>/<id>.md      entity notes; <type> is the closed vocabulary (D105)
documents/<slug>.md          document notes (slug gains -<sha6> when another document owns it, D241)
morgue/<entity>/<type>/…     originals + a <name>.md full-text sibling (D26); same -<sha6> rule
timeline.md                  rendered global timeline
briefings/                   briefings, leads, alerts, research memos
requests.md                  open document requests
context.md / hot.md / log.md investigation context, session cache, run log
index.md / dashboard.base    landing page and Obsidian Bases dashboard (D42)
queries/ wiki/               session-written findings and threads
.embeddings/ .fulltext/      search indexes
.claude/                     Claude Code settings and /watchdog-* commands (D245)
.watchdog/
  queue/<sha>.json           chewed documents; removed after commit
  staging/<sha>/             chewed originals
  extracted/<sha>.json       staged, validated extractions (kept as an audit record)
  timeline/                  raw and canonical NDJSON events
  tmp/                       per-run scratch (result_<sha>.json, notes_<sha>.md, checkpoints)
  research/                  research worklist (§14)
  backups/<ts>-<op>/         pre-mutation snapshots (merge-entities, a fresh run's wipe of leftovers)
  ingest-state.json          present while a run is in progress
  registry/
    entities.json documents.json registry.json manifest.json
    resolutions.json requests.json batch-pending.json
    ingest.log               per-document START/OK/WARN/FAILED lines
    usage/usage-<ts>.json    per-call token/cost/latency records (D50, D86, D132)
    .ingest-lock .write-lock
```

Every model call is also recorded in `~/.watchdog/telemetry.db` (D193): vault path and name,
filename, model, tokens, cost. Off with `telemetry false`; `delete --purge` removes a vault's rows
(D247).

**Session boundaries (D245).** A vault's Claude Code settings pre-authorize writes only to the
session's own pages (`queries/`, `wiki/`, `briefings/`, `context.md`, `.watchdog/tmp/`,
`.watchdog/research/`) and a few deterministic commands. `refresh-skills` updates commands,
permissions, the prompt hook and dashboard views in existing vaults.

**Merging entities (D54).** `watchdog merge-entities <keep> <merge>` unions the losing entity onto
the survivor, remaps every registry and timeline reference, writes a redirect stub, and snapshots
what it changes. It keeps one Summary and suggests `/watchdog-entity` when both had one.

---

## 13. Models, backends and skills

- **Stages.** `classifier_model` (haiku), `extractor_model` (sonnet), `finalizer_model` (haiku) —
  the finalizer split into reconciliation, synthesis, timeline and briefing overrides (D137).
  Efforts: `classifier_effort` (low), `extractor_effort` (medium, D140), `finalizer_effort` (high),
  `verifier_effort` (low). Defaults are in `watchdog/defaults.py`; per-run flags override. GPT-5.6
  Luna is the documented recommendation for extraction (D221, D222).
- **Effort and thinking (D36, D161, D206).** A requested effort is checked against the catalogue's
  per-model `effort_levels` and mapped to each provider's control; an unsupported level fails
  loudly. Models flagged `thinking: true` get Anthropic's `thinking` parameter explicitly.
- **Backends.** `claude-agent-sdk` (the only subscription path; tools disabled, D145),
  `claude-api` (streaming Messages with structured output), and the OpenAI-compatible `openai`,
  `deepseek`, `gemini`, `local` and `openrouter`, registered in one `_BACKEND_META` table. Gemini
  and OpenAI get wire-enforced JSON schemas (OpenAI's derived to strict form, D151); the others get
  JSON mode plus the schema in the prompt (D98). `local`/`openrouter` take a configured base URL;
  `local` takes an optional `local_context_window` (D139). A failed call retries on the same model
  and effort (I4). Auth and billing failures raise `ProviderAuthError` (D242).
- **Catalogue (D142, D217, D249, D250).** `model_catalog.yaml` single-sources ids, pricing (including
  time-of-day, optionally weekday-only, `price_periods`), context windows, output caps, tokenizer
  ratios and capability flags. A retired provider id lives on its replacement's `legacy_ids`. A
  vendor-deprecated model is removed; a merely legacy one stays.
- **Batch backends (D52, D144, D169).** `claude-batch` and `openai-batch` submit the whole queue to
  the provider's Batch API at half price; `dig` submits and exits, and a later `dig` collects
  (`batch-pending.json`, one batch per vault). Each document resolves its own skill first.
  Sectioned documents fall back to the provider's live backend.
- **Setup (D95, D235).** How Claude Code signs in and which provider handles ingestion are asked
  separately.
- **Record skills (D21).** Global: the package's `skills/records/` plus `~/.watchdog/skills/records/`
  (user skills override by name). Read directly, never copied into a vault.
- **Claude Code commands.** `/watchdog-context`, `-entity`, `-query`, `-surface`, `-wiki`, `-health`,
  `-research`, copied into each vault by `new`/`refresh-skills`. `/watchdog-query` can call
  `watchdog search --json` as a semantic lane (D44).

**Data sent per call.** Chew makes none of these calls. A stage on the `local` backend sends the
same content to the user's own model server; every other backend sends it to that provider.

| Call | Runs | Sent | Withheld |
|---|---|---|---|
| classify | once per document, unless a skill is pinned | first `classify_pages` pages, skill index, sidecar | the rest of the document; vault data |
| extract | once per document, or per section | page/section text, skill, brief, sidecar, known document types, file metadata | all vault entity state (D118) |
| verify | once per document or section, when enabled | the extraction prompt plus its facts | vault data |
| digest | once per sectioned document | filename, title, type, page count, merged facts, skill, brief, sidecar | raw text |
| reconcile | once per run (chunked when large) | candidate pairs; each recurring entity's attributed claims and roles | raw text |
| entity-synthesis | per 25 recurring entities touched this run | each entity's current prose and fact fragments | timeline, relationships, contradictions |
| timeline-dedup | per colliding date (≤200 events per call) | event text and page | other dates; entity histories |
| timeline-precision | per month mixing precisions | that month's events | other months |
| request-dedup | when a run adds a request (≤200 per call) | open request type, wording, likely source | everything else |
| briefing | once per run | brief, compact per-document results, alerts, contradiction flags, scratchpads | raw text; full notes |

---

## 14. Web research

**Code:** `pipeline/research.py`, `pipeline/capture.py`, `cmd/research.py`.
**Skill:** `skills/watchdog-research.md`.

`watchdog research` opens a Claude Code session on `/watchdog-research`, which proposes a mission,
researches in rounds and appends kept sources to `.watchdog/research/queue.tsv`. When the session
ends, Python downloads the queued URLs into `_INCOMING/` (after a confirm); chew and dig remain the
journalist's step (I5).

- **The skill curates; Python fetches.** `research.py` validates each URL before connecting
  (http/https, no private or loopback addresses, re-checked on every redirect), caps the body at
  20 MiB, sanitizes HTML, and writes the file with a `.yml` sidecar carrying provenance
  (`retrieved_by`, `source_type`, `relevance`).
- **Rendered capture (D61).** With the optional `[web]` extra, HTML is rendered in headless
  Chromium with the same address check on every subresource, then saved as one self-contained file
  with scripts stripped, assets inlined and a `default-src 'none'` CSP. Without it, the plain fetch
  is sanitized with `nh3`.
- **Durability.** The worklist survives a crashed session; `watchdog`, `chew` and `status` warn when
  URLs are queued but not downloaded, and `research-fetch` finishes the download. Failed rows are
  retained. `research-seen` returns already-captured URLs so a recurring investigation doesn't
  re-fetch them.
- **Wayback (optional).** With `wayback_save` and keys set, each source is also submitted to Save
  Page Now; failures never block a download.
- **`watchdog fetch`** downloads a given list of links through the same path, without a session.
- Round summaries go to `briefings/research-<date>.md`, never `context.md`. Research bounds are
  advisory; only the egress checks are enforced. Web tools are granted by the skill's own
  `allowed-tools`, not the vault-wide permissions.

See D45–D48.

---

## 15. Invariants

The governing rules. Changing one needs a new numbered decision that supersedes it. Mechanically
checkable parts are guarded by named tests in `tests/test_invariants.py`; prompt-relied parts are
noted as such.

- **I1 — Deterministic code writes; the model only reasons.** Anything derivable in Python
  (identity, provenance, slugs, role targets, timeline fan-out) is stamped in code, and the model
  is not asked to restate as prose what it emitted structurally. Exception: `document.summary`, a
  bounded digest grounded in `key_facts` — a prompt instruction, not a checked postcondition.
  *History: D2, D18, D24–D26, D29–D31, D33, D34, D75, D77, D78, D170.*
- **I2 — Local-first preprocessing.** Source documents never leave the machine during chew, and
  chew costs no API tokens. This bounds source-document egress; web research is allowed (§14).
  *History: D1, D45.*
- **I3 — Skills and prompt templates are global package resources**, read directly and never
  copied per vault; prompt templates never appear in the classifier index. *History: D21, D28.*
- **I4 — Configured model and effort only; no automatic escalation.** A failed call retries on
  the same model at the same effort. Continuing a truncated response is not escalation. The
  verification pass's model is fixed to `extractor_model` (it reads that model's cache); its effort
  is configurable. *History: D20, D36, D104, D137, D172, D181, D221, D222.*
- **I5 — Research output re-enters through `_INCOMING/`,** never as a direct vault write.
  *History: D45, D46.*
- **I6 — Anything parsed out of a source document is untrusted input.** XML goes through
  `defusedxml`, never the stdlib parsers; metadata is allowlisted and length-capped; a failing
  reader yields `{}`; document text in a note is defanged. *History: D78, D110, D154, D241.*
- **I7 — The vault mutates only at the finalize commit.** Extraction stages
  `.watchdog/extracted/<sha>.json` and touches no committed state. Every vault write happens in the
  serial, sha-sorted commit pass, after the pre-commit fold and merges — so a reconcile failure
  leaves the batch wholly uncommitted, and `dig` leaves the vault untouched by construction.
  Investigation sessions don't hand-edit pipeline-owned files; they change pipeline state only
  through deterministic commands that take the registry lock. *History: D126–D129, D245.*
- **I8 — Transcribe source values as printed.** Dates, figures, file numbers and names are
  extracted as they appear, even when they look wrong; an inconsistency is noted in the fact, not
  corrected. A prompt instruction with no ground truth to check against. (Correcting a fact's
  `page` citation, D177, is not covered: a citation is checkable.) *History: D167, D177.*
- **I9 — Styling is a terminal affordance.** `--json` output, and any output off a real terminal,
  carries no escape bytes; diagnostics go to stderr in every mode. *History: D174.*
