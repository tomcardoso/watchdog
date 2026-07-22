# Watchdog — Architecture

This document records how the preprocessing and ingestion pipeline is built and,
more importantly, **why** — the architectural decisions and their tradeoffs. It is
the reference for understanding the system as a whole and for evaluating future
changes against the choices already made.

> **Keep this current.** Any change that alters the pipeline's structure, the
> division of labour between deterministic code and the model, the vault/registry
> layout, or one of the **Invariants** (§15) must update this file in the same change;
> the dated rationale for a specific decision is appended to [DECISIONS.md](DECISIONS.md).
> See [CLAUDE.md](CLAUDE.md).

---

## 1. Design principles

These run through every decision below.

- **Local-first.** Preprocessing (OCR, layout, near-duplicate detection,
  classification inputs) runs entirely on the user's machine. Documents never leave it
  during chew. The search index — embeddings, BM25, and the cross-encoder reranker — is
  also fully local (built at ingest, see §11), so it costs no API tokens either. The only
  network calls are the Claude API during the ingest (extraction/synthesis) phase.
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
- **Two runtimes, one boundary — Claude Code is required.** Watchdog runs in two places, and
  the line between them is a governing constraint. The **document pipeline** (`watchdog chew` /
  `ingest`) is a terminal program whose bounded reasoning calls go through a provider-agnostic
  `model_client`: Claude by default, but offloadable to OpenAI/DeepSeek/Gemini per stage (D37,
  D94) because a single-shot, schema-bound extraction call tolerates a cheaper model. The **investigation**
  (`/watchdog-query`, `-surface`, `-wiki`, `-context`, `-health`, `-research`) runs *inside Claude
  Code* as agentic, multi-turn, user-in-the-loop sessions — and is deliberately **not** offloadable:
  Claude Code is a hard requirement, and these stay on Claude. The split tracks capability, not
  preference — open-ended exploration that asks the user questions and follows links across the
  vault is where model and harness quality are hardest to substitute. The practical rule this
  sets: "make it backend-portable" applies only to pipeline steps; collapsing an interactive
  command into the single-shot pipeline pattern to gain portability would forfeit the iteration
  that makes it useful (see D18 for the one move that *was* worth it — ingest, which is batch,
  not interactive).

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
   for the reasoning steps**: classify, extract, reconcile entities + contradictions,
   synthesize entity prose, dedup colliding timeline events, write the briefing. Everything
   mechanical — dispatch, pre/post-flight,
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
- **Sidecar filtering (`pipeline/sidecar.py`, D121).** A document's `.yml` sidecar, if
  present, is read here — the only place it's ever read off disk — filtered to an allowlist
  of known keys (`source`, `obtained`, `notes`, `skill`, plus the fields `pipeline/research.py`
  writes), each value length-capped, and the clean result embedded as the queue JSON's
  `sidecar` field; the original `.yml` is then deleted. Every later reader (classify, extract,
  the per-document skill pin, the morgue write) takes the sidecar from there, never from
  `_INCOMING/` again — the source file and its provenance travel as one unit from this point
  on. If chew instead fails or finds no text, the sidecar moves with the source into
  `_INCOMING/_FAILED/` or `_INCOMING/_SKIPPED/` unfiltered, since nothing has consumed it yet.
- **Embedded file metadata (`pipeline/file_metadata.py`, #369).** Alongside the text,
  `preprocess.main()` captures the file's own embedded metadata — PDF DocumentInfo,
  Office core properties, image EXIF, audio/video container tags via `ffprobe` (gated on
  its presence, `{}` when absent) — always read from the **original** source path, never a
  Ghostscript-cleaned or chunk temp file (Ghostscript strips DocumentInfo). Normalized to a
  small flat allowlist (`author`, `created`, `modified`, `producer`, `gps`, …), every value
  coerced to `str` and capped at 200 characters — the same untrusted-content posture already
  applied to the `.yml` sidecar, since a malicious XMP/EXIF payload could otherwise inject
  text into the extraction prompt. Written to the queue file as a top-level `file_metadata`
  key, a sibling of (never nested inside) the `metadata` key above: `metadata` is
  *processing* facts the pipeline asserts about how the file was read; `file_metadata` is
  *file-intrinsic* claims the file makes about itself, forgeable and often
  machine-generated. Never raises — a corrupt file or unsupported suffix yields `{}`.

Chew is fully local and writes no model-derived fields — `document_type` is left
`None` here (see §6).

---

## 4. Ingest setup & locking

**Code:** `pipeline/ingest_setup.py`, `cmd/ingest.py`.

`watchdog ingest` resolves auth (`auth.resolve_auth`; errors to `watchdog setup` if
unconfigured), acquires a run lock (`.watchdog/registry/.ingest-lock`, stale after 30
minutes), scans the queue, and clears any leftover post-ingest inputs from a prior run
(per-doc results and scratchpads — §8).
It then runs the Python orchestrator in-process (`asyncio.run(orchestrate.run(...))`) and
releases the lock in a `finally`. Models, concurrency, and classification come from
`watchdog configure` (`classifier_model`, `extractor_model`, `finalizer_model`,
`extractor_effort`, `finalizer_effort`, `extract_concurrency`, `classify_pages`,
`default_skill`) or per-run flags.

**Pre-flight cost estimate (D72).** Before the confirm prompt, `ingest_setup.cost_estimate`
multiplies the queue's own `est_tokens` (already computed per file by `scan_queue` for the
sectioning threshold, and calibrated — D135 — against this vault's own extraction history before
the multiply) by this vault's $/token ratio from its last 3 `usage-<ts>.json` runs (D50,
D86), presented as a range (min/max across those runs) rather than one averaged figure. Subscription
auth (`claude-agent-sdk`) never gets a dollar figure — there's no real billing to project, only a
session-limit fraction token counts can't estimate honestly — but it still gets the calibrated
token count. `watchdog ingest --estimate` and `watchdog extract --estimate` print the same estimate
and exit before the lock is touched.

**Tokens-in calibration (D135).** The queue's `est_tokens` is a flat chars/4 heuristic
(`section.est_tokens_from_pages`) with no awareness of prompt overhead or a document's actual
density. Every successful extraction now carries its own naive estimate (`_compact_result`'s
`est_input_tokens`) through to the run's usage totals alongside the real `input_tokens` the model
reported, so `ingest_setup._tokens_calibration` can derive an actual/estimated ratio from a
vault's own last 3 extraction runs and scale future `--estimate` output by it — the raw heuristic
only when there's no such history yet. Deliberately scoped to the *displayed* estimate, not to
`section.py`'s sectioning threshold: recalibrating what actually gets sent to the model on a
still-thin, self-reported history would be a pipeline-behaviour change dressed up as a display fix.

**`watchdog finalize --estimate` (D135).** Prices the batch already staged in `.watchdog/tmp/`
(`result_<sha>.json` + `notes_<sha>.md`, chars/4) rather than a queue — `ingest_setup.
finalize_cost_estimate`. Its $/token ratio can't reuse `cost_estimate`'s own history: a `run()`
ingest's finalize tail shares its single usage file with extraction, which would badly misprice a
lone finalize. Only usage-`<ts>.json` files where every call falls in `orchestrate.FINALIZE_TASKS`
qualify — true only for a *standalone* `watchdog finalize`, which #403 phase 4's staged-corpus
read (D129) is what makes estimable at all: before that, the finalizer's inputs were smeared
across the retired `entity-fragments/` mechanism, not sitting in one place to measure.

**Lock acquisition is atomic** (`pipeline/locks.py`, D66). All three run locks — the ingest
lock, the shared finalize lock, and chew's `.watchdog/.chew-lock` — are taken with
`os.open(O_CREAT|O_EXCL)`, so two concurrent invocations can't both win (the old
check-then-write left a race window). A lock provably older than 30 minutes is taken over; one
whose `started_at` is missing or unparseable is left in place for `watchdog unlock` rather than
deleted regardless of age.

A second, finer lock (`.watchdog/registry/.write-lock`) serializes the actual registry/note
writes so the concurrent document workers write safely. Uses `flock` on macOS/Linux
(blocks indefinitely) and `msvcrt.locking` on Windows (bounded retries, ~10s, then raises)
— see D69.

**Rate-limit stop-and-resume, and `--wait` (D71).** A `model_client.RateLimitError` (session-wide,
not a per-document failure — see §5) stops the batch cleanly: in-flight documents finish or are
cancelled, their queue files are left in place, and `orchestrate.run` returns without raising.
Re-running `watchdog ingest` picks up exactly where it left off (the queue re-scan plus the
`already_extracted` registry check are enough — no extra resume state is needed). `--wait` makes
that re-run automatic: `cmd_ingest` loops on `orchestrate.run`, and on a rate-limited summary
sleeps until `RateLimitError.resets_at` (plus a buffer; a fixed fallback when the backend didn't
report one — only `claude-agent-sdk` does) before looping again, until the queue drains and
finalize completes. The sleep is chunked under the 30-minute staleness window, refreshing the held
lock's `started_at` after each chunk, so a wait that outlasts it doesn't make a live run look
abandoned. Opt-in only — without the flag, a rate limit stops the batch exactly as before. The
loop's only exit condition is a rate limit *during extraction*; with `watchdog extract` (§5)
finalize never runs at all, so `--wait` simply stops once the queue drains, same as normal.

---

## 5. Ingest (extraction)

**Code:** `pipeline/orchestrate.py` (the loop), `model_client.py` (the model adapter),
`pipeline/prompts.py` + `pipeline/schemas.py` (task prompt builders + JSON contracts;
the instruction prose lives in editable templates under `prompts/*.md` — see D28),
`pipeline/preflight.py`, `pipeline/postflight.py`, `pipeline/write_vault.py`.

`orchestrate.run` scans the queue and extracts documents concurrently, bounded by an
`asyncio.Semaphore(extract_concurrency)`. Per document (`_extract_document`):

1. **Pre-flight** (`preflight.run`, a function call) — packages the page text and the document's
   processing facts. **It reads no vault entity state (D118):** extraction is a pure function of
   the document, so pre-flight no longer snapshots the entity registry or builds candidate/timeline
   digests. Its one registry read is `known_document_types` (§6), an order-insensitive set of
   type strings the extractor may reuse. Entity resolution and the contradiction check that
   digest used to feed both moved to the finalizer (§8.5).
2. **Classify** — one cheap model call (`model_client.acomplete_json`, `classifier_model`,
   default haiku) over the document's first `classify_pages` pages, the document's `.yml`
   provenance sidecar when present, and the generated in-memory skill index, returning the
   closest domain-skill filename (§6). Python reads that one skill and injects it into the
   extraction prompt. **Skipped entirely when a skill is pinned** for the run (`--skill` /
   `default_skill`) — that one skill is used for every
   document, saving a model call per doc on known-homogeneous batches.
3. **Extract** — one model call against the `EXTRACTION` schema. The model emits two layers
   (D26): a **fact layer** — `document.key_facts`, each a single material fact written once,
   carrying an optional `date` (when the fact *is* a datable occurrence) and an optional
   `entities` list (the ids the fact is about) plus an optional verbatim `quote`; and a **graph
   layer** — the entities *this document* names, with aliases and roles. It carries **no
   `match_id` and no contradictions** (D118): resolving entities against the vault and comparing
   claims across documents both need a whole-vault view extraction doesn't have, so both moved to
   the finalizer (§8.5). It no longer restates the document as per-entity summaries, evidence
   fragments, or timeline events, nor pads `key_facts` to a fixed count — the full Docling text
   is retained in the morgue (§3, §12), so extraction indexes it rather than reproducing it. An
   optional `document_requests` array names concrete, obtainable artifacts the document refers
   to (a cited transcript, an enabling regulation) — omitted when there are none; see D111.
   Schema validation + a same-model retry live in `model_client` (no automatic tier
   escalation — see D20); the orchestrator adds one post-flight repair retry. When chew
   captured embedded file metadata (§3, #369), `prompts.build_extract_prompt`/
   `build_section_prompt` render it as a volatile `FILE_METADATA` block — data, not
   instructions, stating the trust caveat (forgeable, often machine-generated) plus the
   `ocr_used`/`source_type` processing facts, so the model can judge whether a creation date
   plausibly describes the original or just a scan/template.
4. **Post-flight** (`postflight.run`, a function call) — validates the JSON, **explodes** the
   unified key_facts into the per-entity `evidence_fragments` + `timeline_events` that the
   writers consume (`explode_key_facts`, D26), **verifies** each `key_facts[].quote` against
   the cited page's text from the chew-time queue descriptor (`quote_verify.verify_quotes`,
   D75), **checks each stated fact's numeric figures** against the cited page's text
   (`figure_verify.verify_figures`, D112), **flags a file-metadata date mismatch** —
   `document.file_metadata.created` postdating `date_of_document` by a year or more
   (`file_metadata.check_date_mismatch`, #369), deterministic and suppressed whenever
   `ocr_used` is true, since a scan's creation date describes the scan, not the original —
   stages raw timeline NDJSON (`timeline.stage_timeline_events`, into `.watchdog/timeline/`,
   not the vault proper — a pure function of this extraction, needing no committed registry
   state) — and, in place of writing to the vault directly, **stages the validated extraction**
   as a durable artifact at `.watchdog/extracted/<sha>.json` (#403 phase 1, D126). The artifact is
   never cleaned up on success — it doubles as an audit record and is what makes a document's
   extraction durable and reusable rather than transient pipeline state. **`write_vault.run()`
   is not called here any more** — see the commit pass below.

**Commit pass (`orchestrate._commit_pending`, #403 phase 1, D126).** At the top of
`orchestrate.finalize` — before any post-ingest model call, and covering all three ways finalize
runs (the tail of `watchdog ingest`, a standalone `watchdog finalize`, and a resumed run after a
rate-limit stop) — every `.watchdog/extracted/<sha>.json` whose sha is not yet a key in
`registry/documents.json` is committed by replaying the unmodified `write_vault.run()` over it, in
**sorted sha order**. Sorting is what makes this deterministic (§7 of the phase 1 spec; see
`tests/test_golden_vault.py`): under concurrent extraction, documents still race to produce their
staged artifact, but the write that used to leak completion order into `appears_in`/fragment
ordering no longer happens at extraction time at all — every ordering-sensitive write now happens
serially, in a fixed order, at commit. The queue file survives until this point (moved out of
`_finish_extraction`, #403 phase 1): `write_vault._write_morgue_markdown` and the corpus indexer
both still need to read it, and it is unlinked immediately after a successful commit.

`write_vault` is the single deterministic writer: it collapses each
entity's model-invented `type` onto a closed six-value vocabulary (`entity_type.canonical_type`,
D105) — so a drifting near-synonym (`company` vs `financialinstitution`) can't fork one real-world
entity across two folders — then merges each document's entities into the registry, writes entity
and document notes, updates the registry files, and moves the source file to the morgue — all
inside the write lock. The cross-worker slug reconciliation it used to do per-document (near-duplicate
ids coined by concurrent workers) now runs once up front, over the whole staged batch, in the
pre-commit fold below (#403 phase 2, D127); `write_vault` replays already-folded entities. The
**registry persist is the commit point**: the registries are written last, atomically
(temp-then-rename), and the rebuilt-from-source search indexes (embed/FTS) are (re)written *after*
that commit, keyed for idempotent replay (upsert by note_path), so a repair retry after a mid-write
crash converges instead of doubling (D67). Registry merges are themselves idempotent (sha-guarded),
and the entity note's `## Analysis` block is keyed by the source document and replaced, not appended.

**Finalize is a pre-commit resolution pipeline (#403 phases 2–4).** The commit pass does not run in
isolation: `orchestrate.finalize` resolves the batch *before* it writes, so `write_vault` commits
already-settled state exactly once. The order is **exact-name fold → entity-merge reconciliation →
commit → post-ingest**:

1. **Exact-name fold** (`_batch_exact_fold`, §5 above, D127) — deterministic, no model call.
2. **Entity-merge reconciliation** (`_reconcile_pre_commit` → `reconcile.apply_merges`, §8.5, D128) —
   the one model call that resolves token-variant duplicates. It runs over the staged batch, so a
   same-batch duplicate is folded as a cheap staged-id rewrite rather than post-commit note surgery.
3. **Commit** (`_commit_pending`) — the write, described above.
4. **Post-ingest** (`_post_ingest`, §8/§8.5/§9) — contradictions (which need the committed documents
   registry to validate slugs), gated entity synthesis, timeline reconciliation, the briefing.

Because reconciliation precedes the commit, a reconcile model failure **defers the whole batch**:
nothing commits, every staged artifact stays pending, and a later `watchdog finalize` retries the
sequence from the fold. This keeps the batch atomic — committing half-reconciled state would strand
duplicates a retry could never revisit (D128, I7).

**Two independent "already done" questions (D126).** `preflight.run` answers both, deliberately
not overloaded onto one flag: `already_staged` (the extraction artifact exists — skip the
classify/extract call entirely, sha-only, whatever model/effort/skill produced it) and
`already_extracted` (the sha is in `registry/documents.json` — nothing left to do at all, its
pre-existing meaning). A document can be staged without being committed (between extraction and
the next finalize); it cannot be committed without being staged.

**`--force` bypasses both checks for a full re-ingest, not just extract-only (D131, #424).**
`orchestrate._extract_document`/`_finish_batch_item`/`_submit_batch` skip the `already_staged`/
`already_extracted` short-circuit when `force=True`, overwriting the staged artifact under
whatever model/effort/skill this run is using. `watchdog extract --force` stops there — nothing
committed is touched, so no confirmation is needed. `watchdog ingest --force` goes further:
`cmd_ingest` runs extraction with finalize held off (the same skip-finalize path `extract` uses),
then — only if any force-re-extracted document is already a key in `registry/documents.json` —
warns which vault notes are about to be replaced and confirms, defaulting to **Cancel** (unlike
the routine ingest confirm, this replaces existing work). On confirm, those shas are passed to
`orchestrate.finalize` as `force_shas`, which `_pending_commits` folds into the commit-pass set
even though they are already committed, so `_commit_extracted` replays `write_vault.run` over them
again — the same replace-not-append note write a repair retry of an already-committed document
already relied on (D126), just deliberately triggered instead of accidental. On cancel, the
re-staged extraction is left pending for a later plain `watchdog finalize`.

**Re-queueing an already-committed document from the morgue (D131).** `watchdog ingest --force
<document>` names one or more committed documents (a sha256, an unambiguous sha256 prefix, or a
filename) to re-extract, not just whatever the queue already holds. A committed document's
original does not survive at `.watchdog/staging/<sha>/` — the commit pass moves it out of staging
into the morgue and prunes the emptied staging directory — so its durable, sha-stable location is
`registry/documents.json[sha]["morgue_path"]`. `cmd/ingest._resolve_force_selectors` resolves each
selector against that registry (a no-match selector is a clear error, never a silent no-op), and
`_requeue_forced_selectors` re-chews the morgue file through the real chew pipeline
(`preprocess_batch.run_ingest`), bypassing `_filter_already_seen`'s dedup check and excluding the
document from its own near-duplicate comparison — both bypasses scoped to that one sha, both
otherwise-legitimate checks that would misfire only because this document is being deliberately
re-processed rather than seen for the first time. The re-chew leaves the file in `staging/<sha>/`,
same as any freshly-chewed document, so the recommit that follows moves it back to the morgue
exactly as it would for a document ingested for the first time. `watchdog extract --force` takes
no document names — a re-queued-then-extracted document would sit staged with no plain `watchdog
finalize` able to recommit it (its sha is already a registry key, and finalize's own
`_pending_commits` excludes those without `force_shas`), so the selector is deliberately `ingest`-only.

**Large documents — sectioned extraction.** Code: `pipeline/section.py`,
`pipeline/merge.py`. A document over `section_token_threshold` is split by `section.run`
into overlapping page-range sections, extracted **one at a time in reading order** with a
carry-forward block in each section's prompt, then combined by `merge.merge_extractions`
into a single extraction JSON that goes through the same post-flight / `write_vault` path.
The threshold and per-section budget are **provider-aware** (D89, #321): rather than fixed
numbers they default to fractions (0.6 / 0.3) of the extraction model's context window
(`model_client.context_window` — Claude 200K, DeepSeek V4 1M, Gemini 2.5 1M, etc.), so a large-window model
reads far more of a document per call before sectioning. A 200K Claude window reproduces the
historical 120K/60K defaults exactly; the two config keys default to the `auto` sentinel and
accept an explicit `section_token_threshold`/`section_token_budget` integer as an advanced
escape hatch (a pinned integer does not rescale when the extraction model changes).
The input-window default is **additionally capped by the output ceiling** for a backend that
enforces a fixed `max_tokens` and can't paginate past it (openai, gemini — D104, #343): the
safe output budget is converted back to an input-token cap
(`model_client.output_ceiling_for_sectioning` × a density ratio) so a whole-document call's
expected *output* stays under the ceiling. Backends that paginate their output (claude-api,
deepseek) or have no ceiling (claude-agent-sdk) return `None` and keep the pure input-window
default.
The carry-forward is a deduplicated entity-id → name/type map accumulated across every
section seen so far (rebuilt fresh each section, one line per entity, not a running
concatenation) plus only the immediately preceding section's `observations` text; and,
like whole-document extraction, it includes the investigation brief (D49).

**Whole-document digest (`document.summary`, #279).** No section call ever sees the whole
document, so no section emits `document.summary` any more. Immediately after
`merge.merge_extractions` (and before `_stamp_document`), one small model call
(`orchestrate._compose_digest`, on the **extractor tier** — the same `extract_model`/backend that
read the sections, not the finalizer tier) composes the digest from the merged `key_facts` plus
the same context a whole-document extractor is handed short of the raw text itself — filename,
title, document_type, page_count, the domain skill, the investigation brief, and the sidecar
(the merged `key_facts` stand in for the text). A failed or empty response falls back to
`_stitch_digest`, a deterministic orientation line plus the first few facts as plain
sentences — degraded but valid, never worth a retry loop. Non-sectioned documents compose
the same field **inline**, in the single whole-document extraction call (rewritten field
spec in `extract_instructions.md`) — zero extra model calls, full-text grounding. Both paths
thus write `document.summary` at the extractor tier; they differ only in grounding — full text
inline vs. the merged `key_facts` post-merge — because no single call can hold a sectioned doc.

**Prompt caching (`claude-api` only).** `build_extract_prompt`/`build_section_prompt` return a
list of Anthropic content blocks instead of one string: a stable block (instructions + brief,
constant for the whole run), a skill block (constant per document type, carrying the
`cache_control` breakpoint), then a volatile block (per-document data, never cached). Every
extraction call sharing a skill within a run re-pays only the 0.1× cache-read rate for the
stable+skill prefix instead of full price. Only `_api_complete_async` (the metered-key
backend) understands blocks; `claude-agent-sdk` and the OpenAI-compatible backends flatten
them to plain text (`model_client._flatten_prompt`) since neither exposes a cache knob to us
(D51). `cache_read_input_tokens` is surfaced in the usage telemetry (§12) to verify hits.

**Candidate harvest (Tier 0, #361/D123).** Benchmark hand-scoring found extraction misses
"buried" facts — a lone sentence after a table, a table row, a one-line disclosure — even on
strong models, almost always anchored by a money figure, date, percentage, proper noun, or
court file number. Before the prompt is built, `pipeline/harvest.py` regex-harvests
deterministic candidates (money, bare table figures, percentages, dates including French
month names, court file numbers) from the same page-marked text, plus person/org/location
names from the optional local GLiNER model (`harvest_entities`, import-guarded — a missing
package or any model failure degrades to no entity candidates, never an ingest failure).
Candidates are deduped per page, capped at 3 page-occurrences each (running headers/footers),
and capped at 80 per page by kind priority, then rendered into a compact per-page checklist
(`format_checklist`) that `build_extract_prompt`/`build_section_prompt` inject as a volatile
block, turning recall ("notice it") into verification ("here it is — is it material?").

**Output truncation — never accept a partial extraction (#343).** Sectioning is gated on
*input* size, but a moderate-input, entity-dense document can overrun the model's *output*
`max_tokens` ceiling and truncate the JSON mid-object. Three layers guarantee no truncated
extraction is ever accepted. **(1) Detection (universal).** `acomplete_json` reads the
backend's `finish_reason`/`stop_reason` authoritatively — a max-token cut (`length`,
`max_tokens`) is never inferred from a parse failure — so a truncated-but-parseable response
is rejected rather than silently stored. **(2) Pagination (recovery).** For backends that can
continue a partial response by prefilling it as the assistant turn — claude-api (assistant
prefill) and deepseek (chat-prefix-completion beta) — `_complete_with_pagination` re-issues the
call with the partial output prefilled and concatenates the halves until a natural stop (guarded
by `_MAX_CONTINUATIONS`). Continuation is **not** escalation (I4): same model, same effort, just
finishing one response; the closing brace makes completeness true by construction. **(3)
Proactive sizing.** openai and gemini return a *new* message rather than continuing, so they
can't paginate — instead the output-ceiling-aware sectioning threshold (above) sizes the first
call to fit, and the agent-SDK backend has no client-enforced cap at all. **Reactive backstop.**
If any whole-document extraction is still rejected (truncation or otherwise), the orchestrator
force-sections the document (`section.run(force_budget=…)`, capped at half so it yields ≥2
sections) and retries on the bounded-output sectioned path — now for single-page documents too
(their text splits into character windows), not just multi-page ones. Worst case is a loud
quarantine, never silent data loss. See D104, D19.

**Failure handling.** The model adapter raises if it can't get schema-valid JSON; a doc
whose extraction or post-flight fails (after the output-overrun fallback, for multi-page
docs) is logged to `.watchdog/registry/ingest.log` and
cleaned via `abort.run` (`pipeline/abort.py`) — staging/section temp removed, queue file
moved to `.watchdog/queue/_failed/`, registry untouched. One bad document never sinks the
batch; move the queue file back from `_failed/` to retry.

**Extract-only (`watchdog extract`, #384/#425).** `orchestrate.run(..., skip_finalize=True)`
returns as soon as extraction finishes, at the single point (shared by the concurrent
per-document loop and the claude-batch collect path, §5) that would otherwise call `finalize` —
post-ingest (§8, §8.5, §9) never runs, and nothing is cleared, so `has_pending_finalization(vault)`
stays True on exactly what a normal interrupted run would leave behind. The CLI exposes this as
its own command, `watchdog extract` (`cmd_extract` forces `no_finalize` and delegates to
`cmd_ingest`), rather than a flag on `ingest`. That lets a later `watchdog finalize
--finalizer-model <model>` run against a fixed extraction, including against several copies of the
vault to compare finalizer candidates without re-paying extraction's cost each time. Re-running
`finalize` idempotently over a batch it has already completed is explicitly out of scope; run it
once per vault (or vault copy).

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
  for the whole run (see §5, D21). A document's own `.yml` sidecar can also carry a
  `skill:` field pinning *that document alone* — read deterministically in Python
  (`_sidecar_skill`, parsing the queue JSON's already-filtered `sidecar` text, D121), never
  sent through the model, so it never crosses the data/instruction line the provenance fields
  below hold (D120). It takes priority over a run-wide pin, so one ingest queue can mix skills
  without a second `chew`/`ingest` pass per skill. Falls back to classification (with a
  warning) if the named skill isn't in the catalog and isn't a file path.
- **Provenance-aware.** The classifier also sees the document's sidecar (source + collection
  note), filtered to an allowlist at chew time (D121) and carried in the queue JSON, when
  present — so a document whose type is ambiguous from its text alone — a bare form, a scanned
  table — can be routed by where it came from, not text alone. The sidecar is context, not a
  command: the classify prompt marks it as data and the constrained schema (a skill filename)
  bounds the blast radius; the document text governs on disagreement. See D84.
- **Universal red flags live in `extract_instructions.md`, not the matched skill (D114).**
  Type-agnostic patterns — document integrity, what's missing, backdating/timeline
  anomalies, self-reported-vs-verified — apply to every record, so they sit in the
  always-loaded extraction instructions rather than being restated per skill or reachable
  only through the `general-records` fallback. A matched skill adds its type-specific flags
  *on top of* that standing set; `general-records` keeps only its orient-yourself framing
  for genuinely unknown documents and points to the standing list rather than duplicating it.
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
  not made chronological). They are verifiable claims with citations. The finalizer's
  reconciliation pass (§8.5, D118) is the sole detector — it compares each recurring
  entity's claims across documents once, after all extraction, since a conflict needs
  both claims in view; nothing removes a callout afterward. Sorting them by date would make them a worse
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
  `appears_in` crosses 2. Only entities *touched this run* are candidates — an untouched entity has
  nothing new to reconcile. "Touched this run" is read from the batch's staged extractions: the
  entities named across the current batch's `.watchdog/extracted/<sha>.json` files (#403 phase 4,
  D129), the shas taken from the run's `result_*.json` set.
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
- **Gated synthesis mechanics.** In `_post_ingest` (after the commit pass), `build_bundle(vault, shas)`
  reads the batch's staged extractions (`.watchdog/extracted/<sha>.json`) and, for each entity, rebuilds
  a compact **fragment** on demand — the entity's slice of the exploded extraction (its tagged-fact
  claims with any quotes, its roles) plus document attribution, one block per document it appears in.
  The fragment is a pure function of data the extractor already produced, so it is derived here rather
  than stored: `write_vault` no longer maintains the per-entity fragment files it once did (#403 phase 4,
  D129). `build_bundle` selects the recurring entities, packs each one's fragments + current prose into
  one compact bundle; a single model call synthesizes them all; `synthesis_bundle.apply_bundle` bulk-writes
  the Summary/Analysis via the shared writer in `pipeline/finalize_entity.py`.
- **Batch scope comes from the result set, not the pending-commit set.** `_post_ingest` passes
  `build_bundle` the shas of the run's `result_*.json` files, deliberately not `_pending_commits`. On a
  resume after a synthesis rate-limit the documents are already committed (so `_pending_commits` is empty),
  but their `result_*.json` persist, and a re-run must still re-synthesize that batch (D129).
- **Why the fragment is derived, not extractor-written prose.** Having the extractor narrate per-entity
  notes would add token cost to the expensive parallel phase. The extraction JSON already contains
  everything a fragment needs, so synthesis reconstructs it for free at finalize.
- **Known limitation.** Synthesis reconciles this batch's staged claims with the entity note's *carried
  prose*, not a fresh re-read of every source document. The deep, on-demand `/watchdog-entity`
  pass (`pipeline/write_entity.py`, which also re-synthesizes the Timeline) remains the tool for a
  full rebuild of a central figure from all its sources.

Bundled synthesis writes **only** Summary and Analysis; Contradictions,
Timeline, Relationships, and Notes are preserved untouched. `apply_bundle` skips
any entity the model omits or returns with an empty summary, so its carried-forward
prose stays in place.

---

## 8.5. Reconciliation: entity resolution & contradiction detection

**Code:** `pipeline/reconcile.py`. The merge half is driven from `orchestrate._reconcile_pre_commit`
(before the commit pass); the contradiction half is applied from `_post_ingest` (after it). Both come
from **one** model call (`post_model`).

The two jobs stateless extraction (§5, D118) can't do, because both need every document's claims
side by side: **entity resolution** (the same real-world thing extracted under two ids) and
**contradiction detection** (two documents disagreeing about one entity). One reconcile call answers
both; where each is *applied* differs by what it needs (#403 phase 3, D128). **Entity merges apply
before the commit pass**, over the staged batch — so a same-batch duplicate is folded while it is
still staged JSON, a cheap id rewrite instead of post-commit note surgery, and the merge that would
change what synthesis summarizes has already happened. **Contradictions apply after commit**, because
`contradiction.run` validates both document slugs against `registry/documents.json`, which only holds
this batch's documents once committed. Being post-extraction, the call is concurrency-immune,
order-immune, and near-constant in cost (one call per ingest).

- **Entity resolution is two-tier.** The exact-name fold (`_batch_exact_fold`, §5, D127) already collapses
  *exact* normalized-name duplicates across the staged batch before reconciliation runs. This pass handles
  the *token-variant* judgement calls that fold deliberately won't auto-merge ("Laurentian University"
  vs "…of Sudbury"). `reconcile.build_bundle(vault, shas)` reconstructs the candidate input from the staged
  batch and the registry (no longer from written notes or a fragment queue). To keep it from being an
  every-entity-against-every-other call that eventually won't fit a context window, `reconcile.candidate_pairs`
  **blocks** the field deterministically: a pair is sent only if it shares a canonical type, scores ≥
  `_JACCARD_MIN` (0.5) on some pair of its known names (a strict token-subset scores 1.0, as do identical
  token sets — a word-order or stopword variant the exact-name pass can't fold, e.g. "Cardoso, Tom" vs
  "Tom Cardoso"), and involves at least one entity touched this run — ranked by overlap and capped at
  `_MAX_PAIRS` (200). The model confirms or rejects each pair by index and names the surviving id;
  `reconcile.apply_merges` applies each merge by a **taxonomy of what is already committed** (D128): a
  batch-only loser is a staged-id rewrite (no stub, backup, or provenance — it never existed as committed
  state); two committed sides get the full `merge_entities.run` surgery (§10); and when exactly one side is
  committed, the committed side always survives. Merges chain, following a just-merged id to its survivor,
  and the flattened remap is carried forward so a contradiction naming a folded-away id still lands on the
  survivor. The bundle's size (entity/pair counts and KB) is reported both live and in `watchdog usage`'s
  per-call detail, so a future cap or chunking decision comes from real bundle sizes rather than a
  guess; if it ever does outgrow a context window, it can be split at entity/pair boundaries into
  several calls with no quality loss, since contradiction detection only compares claims within one
  entity and each candidate pair is judged independently.
- **Contradiction detection** reads each recurring entity's (`appears_in ≥ 2`, the D26 gate) `##
  Analysis` claim ledger — already source-attributed by document — and the model returns structured
  `{entity_id, label, a_value/a_doc/a_page, b_value/b_doc/b_page}` items. `reconcile.apply_contradictions`
  files each through `contradiction.run` (D81), the same deterministic writer the manual `watchdog
  contradiction` command uses, which validates both document slugs — so a hallucinated reference is a
  skipped item and a warning, never a fabricated citation. The flags feed the briefing's
  "Contradictions flagged" count (§9).

I1 holds: the model answers only *which* pairs are the same and *which* claims conflict; every
write is deterministic code. Failure recovery, though, is no longer symmetric between the two halves
(#403 phase 3, D128). Because merges now apply *before* the commit pass, a reconcile model failure
(rate limit) **defers the whole batch**: nothing commits, every staged artifact stays pending, and a
re-run of `watchdog finalize` retries fold → reconcile → commit — committing half-reconciled state
would strand duplicates a later retry could never revisit (I7). The contradiction half, applied
post-commit, stays enrichment-only: the documents are already saved, so a failure there just leaves
the callouts unwritten for a later `finalize` to add.

---

## 9. Timeline reconciliation & briefing

**Code:** `pipeline/orchestrate.py` (`_post_ingest`), `pipeline/timeline.py`.
**Files:** `.watchdog/timeline/`, `briefings/`, `hot.md`, `log.md`.

Each document's extraction stages its events to **raw** per-document files
`{date}_{sha7}.ndjson` (`timeline.stage_timeline_events`, called from post-flight) — the events
being the document's **dated** `key_facts` (D26) — write-only and lock-free, since each filename
is unique. Each record carries `source_sha256`, `page`, and the fact's `entity_ids`, so the
rendered timeline can attribute every event to its source document and the entities it concerns (D59). This global timeline is still separate from an entity's own `## Timeline` section (§7):
the entity registry's `timeline_events` is populated independently, straight off the same
`key_facts`, by post-flight, and its per-entity dedup stays mechanical (D58). All merge/dedup and
the briefing then run in `_post_ingest` (model: `post_model`) after extraction:

- `timeline.collisions(vault)` promotes dates with no prior canonical to **canonical**
  `{date}.ndjson` (deleting the raws it just merged) and returns the collisions where a canonical
  already existed; the orchestrator sends each collision's events to one model call
  (`timeline-dedup`), which returns `groups` (each survivor + the pure-restatement indices that
  fold into it). `_select_kept` applies the decision — keeping the authoritative originals and
  **unioning each group's `entity_ids`** onto the survivor, so an event's entity attribution
  survives a cross-document collapse regardless of which restatement won (D59). On a **successful**
  dedup it writes the deduped set back to the canonical and consumes the collision's raws; on a
  **failed** dedup call it leaves the canonical and its raws untouched so the next ingest retries
  cleanly — never writing the canonical+raw union back, which would bake in duplicate rows that
  compound on every later run (D65). It then calls `timeline.cmd_rebuild_timeline` to render
  `timeline.md`;

- **Cross-precision reconciliation (D63).** Date-keyed buckets never compare a month-precision
  event (`2026-03`) against the specific day it restates (`2026-03-12`). After the exact-date dedup,
  `timeline.month_precision_groups` finds each month holding **both** precisions and one
  `timeline-precision` model call per such month matches each coarse event to the day it restates;
  `timeline.apply_precision_matches` drops the matched coarse event, keeps the precise date, and
  unions its `entity_ids` onto the survivor. Gated on a month mixing precisions, so most ingests make
  zero extra calls. The pass can only remove a coarse restatement — never a precise event — so it
  cannot collapse two distinct days; bare-year (`YYYY`) events are left unreconciled by design;

**One renderer (D59).** `timeline.cmd_rebuild_timeline` is the *single* code path that writes
`timeline.md` — reading the cross-document-deduped canonical NDJSON and resolving `source_sha256`
→ document link (+ `page`) and `entity_ids` → entity links, year-grouped. Every command that
touches the vault routes through it: `_post_ingest` (batch ingest), `watchdog merge-entities`,
`write_entity`, and the standalone `watchdog timeline`. `write_vault` no longer renders the global
timeline (it has no deduped data mid-batch), so the file's shape no longer depends on which
command last ran. `merge-entities` additionally remaps the losing entity id → survivor inside the
NDJSON records (`_remap_timeline_ndjson`), keeping the timeline's entity links correct after a
merge — deterministic, no model call, parallel to its registry surgery (§I1);
- builds a briefing prompt from the compact per-doc results — which now carry each
  document's `key_facts` (projected to fact + date, the briefing's source for figures and
  chronology) alongside near-dup alerts and contradiction flags — plus the per-document
  scratchpads, now slimmed to forward-looking leads only (D33); makes one model call
  (`briefing`), and `_write_briefing` writes the structured prose into `briefings/<ts>.md`,
  `hot.md`, and a `log.md` entry — first resolving any item that's an exact match against the
  registry manifest from an entity id to its display name, since not every backend reliably
  avoids echoing ids in prose (D107).

`watchdog ingest` prints the per-document summary; the briefing/hot/log files are the
durable record a fresh session reads.

**`--skip-briefing` (D134, #410).** A flag on `ingest` and `finalize`, not a separate command —
plumbed as `skip_briefing` through `orchestrate.run`/`orchestrate.finalize` → `_post_ingest`,
which skips only the `briefing` model call; reconciliation, synthesis, and the timeline steps
above still run. Since `_write_briefing` is what writes `hot.md` and the run's `log.md` entry,
both are skipped along with `briefings/<ts>.md` — the leads/watchlist/requests sweeps below don't
depend on it and still run. Recorded as `briefing_skipped` (not `briefing_error`), so the caller
doesn't treat an intentional skip as a failed run needing a later `watchdog finalize`. The bare
`watchdog` guided walk (`cmd_guided`) takes the same flag at the top level of the CLI parser —
it rides along on the `args` Namespace `_offer_ingest` passes straight into `cmd_ingest`, so it
needs no separate plumbing of its own.

**Deterministic sweeps + resolution overlay (D68).** Two model-free, whole-vault passes run
alongside the briefing and are also available on demand: the lead sweep (`pipeline/leads.py`,
`watchdog leads`) reads the entity registry for named-but-unprofiled / isolated / contradiction /
inferred signals, and the watch-word scan (`pipeline/watchlist.py`, `watchdog watchlist`) greps
the morgue full text for `watchlist.md` terms. Both write dated `briefings/` files. Because they
regenerate from scratch every run they used to re-surface handled items; `pipeline/resolutions.py`
is the shared acknowledgment overlay that fixes that. Its `.watchdog/registry/resolutions.json`
keys acknowledged items on stable ids (`lead:<signal>:<id>`, `contradiction:<callout-hash>`,
`alert:<sha7>:<term-hash>`); the report generators (and the entity-note writer, for contradiction
callouts) drop resolved ids from the active list. The store is populated by `watchdog resolve`,
by `- [x]` checkbox sync from the briefing files (`<!--wid:<id>-->` markers), and undone by
`watchdog unresolve`; `merge-entities` remaps lead ids onto the survivor (§I1, D54).

**Document requests (`pipeline/requests.py`, D111).** A **document request** is a concrete
artifact to acquire — a document type, the specific thing, why it matters, and often where to
get it — distinct from a lead's open-ended thread. The model emits `document_requests` on
`EXTRACTION`/`SECTION` (moved out of `scratchpad`/`observations`, never duplicated); Python
stamps each into `.watchdog/registry/requests.json` with an id (`request:<sha7>:<hash>`) and
provenance (§I1), inside `write_vault`'s registry lock. `write_requests` renders the still-open
entries to the vault-root `requests.md` (overwrite, current-state, same `<!--wid:...-->`
checkbox convention as leads/alerts) and `sync_from_briefings` reads it alongside the briefing
files. Resolution is manual only — no fuzzy auto-close — and a resolved or rendered request is
never re-fed into any later model prompt.

**Promoting a surface-found contradiction (`watchdog contradiction-add`, D82, D83).**
`/watchdog-surface` reports cross-document contradictions as labelled *candidates* rather than
writing callouts into pipeline-owned entity notes (D81). When the journalist explicitly confirms
promotion, `/watchdog-surface` invokes the deterministic command (`cmd/contradiction.py` →
`pipeline/contradiction.py`) which writes the callout — in the exact `[!contradiction]` shape
extraction emits — into the entity's `## Contradictions` ledger and re-renders the note through
`build_entity_note`, applying the same resolved-contradiction overlay the ingest writer does. So
the promoted callout is content-keyed like any pipeline-emitted one and `watchdog`
`resolve`/`unresolve` act on it unchanged. It validates the entity id and both document slugs, is
a no-op if the callout is already present, and makes no model call — the journalist stays the gate
(explicit confirmation), the pipeline stays the sole writer (§I1, §I5). The command is internal
and hidden from top-level `watchdog -h`.

---

## 10. Near-duplicate detection

See §3. MinHash-over-shingles, computed at chew time, surfaced as `near_dup` on the
queue JSON and flagged in the ingest briefing. Detection only — the journalist
decides whether near-duplicates are the same document.

---

## 11. Search index

**Code:** `pipeline/embed.py`. **Files:** `<vault>/.embeddings/`.

A local fastembed semantic index (`embed_model`, default `bge-small-en-v1.5`), one
`.npy` + `.json` per document and per note. Re-ingesting a document or note overwrites
only its own files. Indexing is deterministic and entirely on-machine — no API, no
metered call (I2).

**Built at ingest, not chew (D43).** Notes are embedded by `write_vault` as they're
written; corpus passages are embedded by `write_vault` too, right after the document note,
so the index is wholly an ingest product. Embedding moved off chew because each passage is
stored with a **contextual prefix** — the document's title, type, and the entities it names
— and those only exist after extraction. The prefix is prepended to the window before
embedding and kept alongside it for the sparse leg, anchoring a passage that lacks the
document's who/what to its document (Anthropic contextual-retrieval); the stored/cited
`text` stays the clean window.

**Passages, not pages (D38).** Each page is split into overlapping word *windows*
(`_WINDOW_SIZE` words, `_WINDOW_OVERLAP` shared with the neighbour) and one vector is
stored per window, tagged with its page. A whole page averages many topics into one
vector and dilutes a short query; a window is a passage-level unit, and the matched
window *is* the citable span (no separate highlighting step). Windows never cross a page
boundary, so every passage carries an exact page citation.

**Two streams, queried separately.** Corpus passages (what a *source* says) and notes
(what we *concluded*) live side by side but are ranked independently: `search(...,
scope=)` selects `corpus` / `notes` / `all`, and `watchdog search` shows them as two
sections so synthesized prose never dilutes source-passage ranking.

**Hybrid corpus retrieval (D43).** The corpus stream is ranked by a three-stage pipeline:
a dense cosine ranking and a sparse **BM25** ranking are fused with reciprocal-rank fusion
(`_RRF_K`), then the fused candidate pool (`_RERANK_POOL`) is reordered by a local
**cross-encoder reranker** (`rerank_model`, default `bge-reranker-base`; warmed by
`watchdog setup`, else downloaded on first search; disable with `rerank_model = none` or
`--no-rerank`). BM25 recovers the exact tokens
embeddings blur — case numbers, dollar amounts, statute cites, names — and the reranker is
the single biggest precision lever. BM25 is computed in-memory from the loaded passages (no
persisted sparse index); if the reranker can't load, search degrades to the fusion order.
The notes stream stays pure cosine.

**Query handling.** Short queries are embedded with the bge instruction prefix
(asymmetric retrieval — passages get no prefix); a query supports Semantra-style
`+`/`-` arithmetic (`_parse_query` → sum of positive minus negative phrase vectors);
`min_score` (CLI `--threshold`) drops weak hits on the **cosine** score (each result's
`score` stays the cosine even when fusion + rerank set the order). Cosine has no universal
cutoff — a strong conceptual match sits ≈ 0.5–0.65 for the default model, so the threshold
is advisory and corpus-tuned, not a fixed gate.

**Rebuilding without re-ingest (`watchdog reindex`, D53).** Code: `cmd/reindex.py`. Since
`documents.json`/`entities.json` already hold everything the D43 contextual prefix needs,
and the morgue `<stem>.md` sibling (D26) holds the full page-marked text, the index can be
rebuilt from disk alone — no OCR, no model call. `reindex` wipes `.embeddings/` and replays
`embed.add_document`/`add_note` for every registry entry, reconstructing pages from the
morgue text's `<!-- PAGE N -->` markers and each document's mentioned-entities list from
`appears_in`. This is the documented way to change `embed_model` after ingest — its vectors
are persisted, so switching models means every one is stale. `rerank_model` needs no
reindex: the cross-encoder only runs inside `search` at query time (`_get_reranker`) and
nothing about it is persisted, so a change takes effect on the next `watchdog search`. A
pre-D26 document with no morgue text on disk is skipped (its note still reindexes) rather
than failing the whole run.

**Full-text (exact-term) lane, complementary to the above (D57, issue #109).** Code:
`pipeline/fulltext.py`. **Files:** `<vault>/.fulltext/index.db`. A local SQLite FTS5 index
(`unicode61` tokenizer, no stemming) over the same raw source text (morgue pages) plus every
generated note the pipeline writes — entity, document, timeline, briefing, hot cache, and
run log. Where the embedding index above answers "what's most relevant," this answers
"every place this exact term or phrase appears" — the recall lane for a name, case number,
or other token that never got promoted into a synthesized note. Query syntax is
deliberately not raw FTS5 MATCH grammar: a quoted substring is a phrase match, bare words
are ANDed, and every token is escaped (`build_match`) so punctuation in a name (O'Brien,
AT&T) can't be misread as query syntax. One row per corpus page (carrying its page number
and morgue path, so a hit links straight to `morgue_path#page=N`) and one row per note
(keyed by note path), each replaced — not duplicated — on re-indexing via delete-then-insert.
`watchdog search` runs this as a third, unscored "Exact matches" section alongside the
existing corpus/notes sections, reusing the same snippet-windowing and term-highlighting the
semantic sections already use rather than FTS5's own `snippet()`. Built at ingest (the same
call sites that call `embed.add_document`/`add_note` also call the `fulltext` equivalents,
best-effort — a failure warns but never fails the ingest run) and rebuilt in full by
`watchdog reindex` alongside `.embeddings/`.

**Batch search (D57, issue #110): `watchdog search --batch <file>`.** Reads one term per
line (blank lines and `#`-comments skipped) and reports hits per term instead of ranking a
single query — the "does any of these N names from a leaked roster/sanctions list/donor
list appear anywhere" workflow. Combines two lanes per term: manifest name/alias substring
matches (the existing `manifest.json` lookup) and full-text hits (`fulltext.search`).
Deliberately skips the semantic/embedding lane — a batch is routinely hundreds of terms, and
embedding + cross-encoder rerank per term doesn't scale the way an in-process SQLite query
does. A flag on `search`, not a new command: one command a journalist needs to remember,
with `--batch` switching it from ranking a query to reporting per-term hits.

**Cross-vault search (D72, issue #272): `watchdog search --everywhere`.** A deliberately
small stepping stone toward a global entity registry (#67) — "have I seen this name in
*any* of my vaults?" answered today over existing per-vault indexes, with no shared
registry and no cross-vault entity resolution. Drops the single-project scope and instead
iterates every registered, non-archived project in `projects.json`, running the same
manifest-substring and full-text lanes as `--batch` (semantic/rerank skipped for the same
scaling reason: N vaults × embedding + rerank doesn't scale the way in-process SQLite
queries do) per vault, then reports hits grouped by investigation name. Composes with
`--batch` (a term list checked across every vault, not just one). A vault whose registered
path is missing or not a Watchdog vault (`_check_project_health`) is skipped rather than
failing the whole scan — the same tolerance `watchdog doctor` already applies.

---

## 12. Vault & registry layout

**Vault (the investigation folder):**

```
entities/<type>/<id>.md     entity notes (<type> ∈ the closed vocabulary, D105)
documents/<slug>.md         document notes
morgue/<entity>/<type>/…     original source files + a sibling <name>.md of the
                            Docling full text, filed by subject (D26)
timeline.md                 rendered global timeline
briefings/<date>.md         per-ingest briefings
requests.md                 open document requests — documents to go and get (D111)
context.md / hot.md / log.md investigation context, hot cache, run log
index.md / dashboard.base    landing page + native Obsidian Bases dashboard (D42)
.embeddings/                semantic search index
.fulltext/index.db          full-text (exact-term) search index (D57)
.obsidian/graph.json        graph colours per entity type
.watchdog/                  pipeline state (below)
```

**`.watchdog/` (pipeline state):**

```
queue/<sha>.json            chewed documents awaiting ingest — survives until the commit pass
                            (#403 phase 1), not deleted at extraction time
staging/<sha>/              chewed originals
extracted/<sha>.json        durable, validated extraction output (#403 phase 1, D126) — staged by
                            post-flight, committed to the vault by finalize's commit pass, never
                            cleaned up on success (doubles as an audit record)
timeline/                   raw + canonical NDJSON event files
tmp/                        scratch (wdg_* temp; per-run post-ingest inputs: result_<sha>.json,
                            notes_<sha>.md, synthesis-result.json — cleared on a clean finalize)
registry/
  entities.json             full entity records (roles, events, appears_in)
  documents.json            per-document metadata + MinHash signatures + embedded
                            file_metadata (author/producer/created/…, #369)
  registry.json             counts + last-updated
  manifest.json             lightweight id→{name,type,aliases,note_path} lookup
  resolutions.json          acknowledged leads/alerts/contradictions/requests overlay (D68, D111)
  requests.json             document-request ledger — id/provenance stamped by Python (D111)
  ingest.log                append-only ingest log (START/OK/WARN/FAILED per doc, D102)
  usage/usage-<ts>.json     per-run model-call token/cost/latency telemetry (D50, D86, D102);
                            `totals.est_input_tokens` (D135), present only when the run actually
                            extracted documents, is the naive chars/4 estimate for them — the
                            input tokens-in calibration compares against `totals.input_tokens`
  usage/usage-<ts>.partial.jsonl  in-progress run's calls, one JSON line per completed call —
                            folded into a real usage-<ts>.json and removed at the *next* run's
                            start if the run that wrote it never reached a clean exit (D132)
  batch-pending.json        pending claude-batch extraction state (D52)
  .ingest-lock / .write-lock  run lock / write serialization
backups/<ts>-<operation>/   pre-mutation snapshots for irreversible operations (D71)
ingest-state.json           present while a run is in progress; stale ⇒ interrupted ingest, resume with `watchdog ingest`
```

**Why a manifest separate from `entities.json`.** Pre-flight needs only a small
lookup (names/aliases → id/note_path) for candidate matching; reading the full
registry for every document would be wasteful. The manifest is the cheap index.

**Merging duplicate entities (`watchdog merge-entities <keep-id> <merge-id>`, D54).**
Code: `pipeline/merge_entities.py`, `cmd/merge_entities.py`. Ingest-time reconciliation
(`_reconcile_entity_ids`, above) only catches slug drift *coined within one batch*; the
same real-world entity extracted under two ids across separate ingests — the gap D39's
Neo4j-export tradeoff note and the dashboard's "Possible duplicates"/"Single-source
entities to review" views could only ever flag, never fix — needs a manual, deterministic
merge. `merge()` unions `aliases`/`appears_in`/`roles`/`timeline_events` onto the
surviving id and remaps every `role.target_id` **across the whole registry** that named
the losing id (not just the two entities involved), dropping any role that would end up
self-referential; `run()` additionally concatenates the losing note's `## Analysis` into
the survivor's with dated provenance, redirects the losing note to a short stub linking
to the survivor, regenerates any third-party entity note whose own Relationships section
just changed, rebuilds the manifest and global timeline, and does a best-effort reindex
of every note it touched. Must be run from inside the vault it mutates (no model calls,
no project-name lookup needed). The merge keeps only one prose `## Summary` (the survivor's
if it has one, else the loser's), so when both entities carried one the losing account is
dropped; `run()` returns a `summary_dropped` flag and the CLI nudges a `/watchdog-entity
<keep-id>` refresh to re-synthesize the Summary from all merged sources (#313). The
merged-away entity's stale corpus/notes search-index
entries are cleaned up by a subsequent `watchdog reindex` (D53), not by this command
itself — a full rebuild is the existing, already-documented way to drop vectors for
anything no longer in the registry.

**Pre-mutation snapshots (`pipeline/backup.py`, D71).** `merge-entities`, ingest's
`discard` choice (§4), and `delete --purge` all mutate or delete the registry with no
undo. `snapshot(vault, operation, paths)` copies whichever of `paths` currently exist
into `.watchdog/backups/<ts>-<operation>/`, preserving each path's position relative to
the vault, before the caller's own writes/deletes happen — a no-op (no directory
created) when nothing in `paths` exists yet, so an ordinary run that never touches the
irreversible branch leaves nothing behind. Backups are pruned to the 5 most recent
(name-sorted, since the timestamp prefix makes lexical order chronological). Each
call site backs up only what it's about to destroy: `merge-entities` snapshots
`entities.json`, `manifest.json`, both entity notes, and any third-party note about to
be regenerated; ingest's discard snapshots `result_*.json` and
`notes_*.md`; `delete --purge` snapshots the registry files only (backing up the whole
vault would defeat the purpose of purge) — and since that snapshot lives inside the
vault being deleted, it is a hedge against a partial failure, not a way to undo a
completed purge, and the CLI hint says so.

---

## 13. Models & skills

- **Models** (configurable via `watchdog configure`): `extractor_model` (default sonnet;
  extraction, whole-doc + section) and `finalizer_model` (default haiku; post-ingest
  synthesis + timeline + briefing, §8–§9); classification runs on haiku. `extract_concurrency`
  (default 5) bounds parallel extraction. Each is overridable per run via the matching
  `watchdog ingest` flag (`--extractor-model` / `--finalizer-model` / `--concurrency`).
  `finalizer_model` is itself an aggregate over four `_call_model` task names — `reconcile`,
  `entity-synthesis`, `timeline-dedup`/`timeline-precision`, and `briefing` — each independently
  overridable via `finalizer_reconciliation_model`/`finalizer_synthesis_model`/
  `finalizer_timeline_model`/`finalizer_briefing_model` (config keys, or the matching
  `--finalizer-<stage>-model` flag; D137/#433). `orchestrate.finalize`'s `finalizer_overrides`
  dict carries the resolved `<stage>_model`/`<stage>_backend` pairs; an absent key falls back to
  `post_model`/`post_backend` (`dict.get`'s own default), so a stage deliberately resolved to
  `None` backend — "route by auth mode" — is never confused with an unset override.
- **Reasoning effort** (per-stage, default `high` ≡ the model default): `extractor_effort`
  and `finalizer_effort` (`low`/`medium`/`high`) tune how many thinking tokens each stage
  spends; thinking bills as output, so a lower effort is the per-run cost lever (D36).
  `model_client` maps them to each backend's native control (`output_config.effort` /
  `ClaudeAgentOptions.effort`) and drops them on Haiku-tier stages (classify; any Haiku
  model), which reject `effort`. Overridable per run via `--extractor-effort` /
  `--finalizer-effort`.
- **Model client** (`model_client.py`): the orchestrator's single entry to the model.
  Routes each task to a backend — `claude-agent-sdk` (subscription login or API key — the
  only backend that works on a subscription), `claude-api` (raw Messages + structured
  outputs), or the OpenAI-compatible `openai`/`deepseek`/`gemini`/`local`/`openrouter` backends
  (Chat Completions over httpx, one provider each via base URL; D37, D94, D138) — by auth mode
  and per-task policy, validates the JSON, retries on the same model on failure, and reports
  cost/latency. Structured-output enforcement differs by provider on the OpenAI-compatible path:
  Gemini gets a real `json_schema` response format (its schema engine tolerates the same
  omit-optional-fields `schemas.py` design unmodified), while OpenAI, DeepSeek, local, and
  OpenRouter stay on portable `json_object` + schema-in-prompt (D98).
  **Provider
  abstraction:** the abstract `effort` intent is mapped to each provider's native control by
  a per-provider policy (`_EFFORT_POLICY`: Claude `output_config.effort`, OpenAI
  `reasoning_effort` on reasoning models only, Gemini `reasoning_effort` unconditionally (every
  current model accepts it, D94), DeepSeek none — its thinking mode is a separate
  on/off carried in the model id via a `-thinking` suffix, default off, D88; local and OpenRouter
  none either — an arbitrary self-hosted or OpenRouter-routed model has no capability table to
  consult, so the safe default never sends a control a given model might reject, D138), and
  `_resolve_backend_auth` resolves the key per backend — Claude backends via the
  subscription/api-key mode, others via their own stored key (set interactively via `watchdog
  auth`) independent of the Claude mode — plus, for `local`/`openrouter`, a **user-supplied base
  URL** (`watchdog configure local_base_url`/`openrouter_base_url`, or their env-var overrides)
  rather than the fixed URL the other three OpenAI-compatible backends bind at import time, and
  no API key requirement for `local` specifically (most self-hosted runners don't check for one,
  D138). A self-hosted model's id carries no vendor namespace to infer a context window from the
  way hosted models' do, so `context_window` takes an explicit `backend` and, for `local`, consults
  a `local_context_window` config override before falling back to a conservative default (D138)
  rather than guessing from the substring table. Auth is resolved by `cmd/auth.py` (see #119, D37,
  D93, D138). **Setup philosophy (D95):**
  Claude Code is required — it runs the interactive investigation skills below and is the
  ingestion default — but ingestion specifically can be routed to a cheaper metered provider
  instead, since it is the token-heavy stage. `watchdog setup`'s auth step surfaces this
  choice directly (offering to walk through picking a provider and a model for each of the
  three ingest stages when the user is on a subscription), and `watchdog auth`'s status
  display is split into a "Claude Code" section and an "Ingestion" section (showing, per
  stage, which provider it resolves to and whether that provider is ready) rather than
  favouring Claude's own settings.
- **Claude Code skills** (in-vault, run interactively — *not* part of ingest):
  `watchdog-context`, `watchdog-entity`, `watchdog-query`, `watchdog-surface`,
  `watchdog-wiki`, `watchdog-health`, `watchdog-research` (§14). Ingest is the Python
  orchestrator (§5); it uses no Claude Code skill. `watchdog-query` reads the manifest/notes
  first but can shell out to `watchdog search --json` as a **semantic lane** for
  conceptual/passage-level questions (§11, D44). It also narrows by **facet** (entity type,
  document type, date range) before reading notes, driven entirely off metadata already
  captured at ingest — manifest `type`, document-note `document_type`/`date_of_document`
  frontmatter, and `timeline.md`'s year grouping — no new index (#111).
- **`claude-batch` — bulk extraction via the Message Batches API** (`pipeline/batch_extract.py`,
  D52): a fundamentally different flow from the other backends — submit-many/poll/collect over
  minutes-to-24h rather than one call per document — so it isn't in `model_client._ABACKENDS`
  and is never dispatched through `acomplete_json`; `orchestrate._run_batch` handles it
  entirely, called from `run` instead of the concurrent per-document loop. It requires a
  pinned skill (classification isn't batchable) and `api-key` auth (a metered key; not
  available on subscription). Documents needing **sectioned** extraction fall back to
  `claude-api` — a section's carry-forward depends on the previous section's result, so it
  can't be an independent batch request. `watchdog ingest` submits and exits rather than
  blocking; state persists to `.watchdog/registry/batch-pending.json` (one batch in flight
  per vault, mirroring `has_pending_finalization`'s precedent), and a *later* `watchdog
  ingest` invocation checks it — collecting and writing to the vault if `ended`, or reporting
  progress and exiting if still processing. 50% off every token, stacking with the A1 prompt
  caching above (batch requests use the 1-hour cache TTL, since a batch routinely outlives
  the default 5-minute window).
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

**Data sent per call.** Every ingest model call is enumerated below — what it sends to
the cloud, what it deliberately withholds, and how often it fires. This is the concrete
backing for I2 (local-first preprocessing): chew makes none of these calls, and
everything below runs only during `watchdog ingest`. The one exception to "the cloud": a
stage routed to the `local` backend (D138) sends this same "Sent to the model" content to a
model server on the operator's own machine or network instead — nothing in that stage's
column leaves the building. Every other backend, Claude included, sends it to that
provider's servers.

| Call | Runs | Sent to the model | Withheld |
|---|---|---|---|
| **classify** (§6) | once per document; skipped entirely if a skill is pinned (run-wide `--skill`/`default_skill`, or that document's own sidecar `skill:` field) | first `classify_pages` pages of extracted text, the in-memory skill-catalog index, the `.yml` provenance sidecar if present | the rest of the document; all entity/registry data |
| **extract** — whole-doc or per-section (§5) | once per document, or once per section for a document over the sectioning threshold | the page/section text, the matched domain skill, the investigation brief (`context.md`), the `.yml` sidecar, known document types | **all vault entity state** — extraction is a pure function of the document (D118); original-file metadata (EXIF, PDF author fields — stripped at chew, §3) |
| **digest** (§5) | once per sectioned document, after merge — whole-doc extraction composes its digest inline instead, with no extra call | filename, title, document_type, page_count, the merged `key_facts` (not the raw text), the domain skill, brief, sidecar | the document's raw text |
| **reconcile** (§8.5) | once per run, if any entity was touched | deterministically-blocked candidate duplicate pairs (same type, overlapping names), and each recurring entity's source-attributed `## Analysis` claim ledger + roles digest | raw document text; entities with no plausible duplicate and no cross-document claims |
| **entity-synthesis** (§8) | once per run, batched, only for entities appearing in 2+ documents vault-wide | per qualifying entity: its current `## Summary`/`## Analysis` prose plus every accumulated fact fragment tagged to it across all its documents | timeline, relationships, contradictions — deterministic, never seen by a model |
| **timeline-dedup** (§9) | once per colliding date (0+ per run) | the event text and page for every event sharing that date | entities' full histories; unrelated dates |
| **timeline-precision** (§9) | once per month mixing month- and day-precision dates | that month's coarse and precise event text + page | other months; entity histories |
| **briefing** (§9) | once per run, over the whole batch | the investigation brief, compact per-document results (type, date, entity counts, key facts), near-dup alerts, contradiction flags, every document's scratchpad notes | raw document text; full entity notes |

---

## 14. Web research mode (investigation layer)

**Code:** `pipeline/research.py` (the egress gate + the durable worklist store), `cmd/research.py`
(`watchdog research` launcher + post-flight download; `watchdog fetch` bulk downloader; the internal
`research-fetch` recovery and `research-seen` re-fetch-avoidance commands). **Skill:**
`skills/watchdog-research.md`.

A bounded, agentic web-research session that **queues findings for `_INCOMING/`** so they flow
through the normal `chew → ingest` pipeline — it never writes vault notes directly. `watchdog
research` explains the mode, takes a question (or prompts for one), and opens Claude Code on
`/watchdog-research` (launched via `subprocess`, not `execvp`, so control returns afterward). The
skill seeds from the vault's open state (`manifest.json`, `watchdog leads`, health gaps,
`context.md`), proposes a mission, confirms scope + an effort tier (quick / standard / deep), then
researches in rounds — reading the web selectively to follow leads, checking in between rounds —
recording each kept source in a **links file** (`.watchdog/research/queue.tsv`: `url ⇥ title ⇥
source_type ⇥ relevance`). When the session ends, `watchdog research` downloads the queued sources
(after a confirm) and stops at `_INCOMING/`, leaving `chew`/`ingest` to the journalist.

- **The skill curates URLs; Python downloads them.** Splitting *curation* (model) from *fetching*
  (deterministic Python) is the core of the design. The skill never downloads a source — it only
  reads the web (WebSearch/WebFetch) and appends to the links file. The download is a deterministic
  **post-flight** of `watchdog research`: `pipeline/research.py` validates each URL *before* the
  network call (http/https only; host must not resolve to private/loopback/link-local space — the
  SSRF guard, re-checked on every redirect hop), fetches with a body-size cap (20 MiB), strips
  `<script>`/`<iframe>`, defangs wikilink/frontmatter-delimiter injection in sidecar values, and
  writes the source + `.yml` sidecar. It runs in the terminal (ungated), never folded into `chew`
  (which stays local-first/no-network, I2).
- **Faithful artifact + existing provenance plumbing.** A finding is downloaded as the original
  artifact (HTML → Docling, or any Docling-supported type by Content-Type), not a model-summarized
  capture. Provenance rides the existing `.yml` sidecar (§5, §12): `source`/`obtained` are stamped
  deterministically at ingest, and `retrieved_by: research-mode`, a `source_type` reliability tag,
  and per-doc `relevance` reach the extractor as notes and travel to the morgue.
- **Two-tier HTML capture (#200).** After the urllib fetch above (kept for type-detection and
  provenance), an HTML deposit gets a second, richer capture: `pipeline/capture.py` renders the
  page in headless Chromium — so static styling/images *and* client-rendered SPAs are captured as
  they actually appear, not as the empty shell a `<script>`-stripped fetch would save. The SSRF
  guard is re-applied to **every subresource request** the rendered page makes (`context.route`
  interception, not just the top-level URL; service workers are blocked and WebSockets mocked,
  since neither passes through the route handler), and the saved snapshot is a single self-contained
  `.html`: every `script`/`iframe`/event-handler/`javascript:` surface is stripped from the live
  DOM, images/fonts/stylesheets are inlined as data URIs (or neutered to `data:,` when uncaptured),
  and a `default-src 'none'` CSP meta tag is pinned as the first `<head>` child so the file can't
  phone home even if opened directly. Playwright is an **optional dependency**
  (`watchdog-intel[web]`, plus a one-time `playwright install chromium`): when it isn't installed or
  a render fails for any reason, `deposit_one` falls back to the plain fetch, sanitized by `nh3`
  (replacing the old two-tag regex). The sidecar's `capture: rendered|plain` field records which
  path a deposit took.
- **Per-doc rationale → sidecar; batch rationale → memo.** The connective tissue of a research round
  (what gap it targeted, what was pulled, what's still open) is written to a
  `briefings/research-<date>.md` memo as forward-looking leads — **never** `context.md`, which is the
  human-anchored orientation layer and must not be machine-seeded with unverified web inference.
- **Durability — a URL mirrors a PDF's stages (#196).** The worklist is the one URL-specific piece of
  state, so it lives under `.watchdog/research/` (durable), not `.watchdog/tmp/` (which `setup` sweeps):
  a session that crashes before its post-flight never loses what it queued, and **bare `watchdog`,
  `watchdog chew`, and `watchdog status` all warn** when URLs are queued-but-not-downloaded. Past that
  point a URL is tracked exactly as a pending PDF is — by filesystem presence: a downloaded URL is an
  `_INCOMING/` file, an ingested one is a `documents.json` entry with `source`. A download pass consumes
  the worklist, retaining only rows that *failed* to fetch (at-least-once, so a transient failure is
  never silently dropped); there is **no separate "done" ledger**. `watchdog research` offers to
  download a leftover queue on its next run, and `research-fetch` re-pulls on demand; downloads are
  idempotent (deposit names are a stable hash of the URL).
- **Re-fetch avoidance (#196).** So a recurring investigation doesn't re-pull sources it already has,
  the skill runs `watchdog research-seen` at seed time and skips any URL it returns — unless the
  journalist asks to re-check a source. The "seen" set is **derived, not stored**: the union of every
  `documents.json` `source` (ingested) and every in-flight `_INCOMING/**.yml` `source` (downloaded, not
  yet ingested) — mirroring how `chew` dedups against the registry (D27).
- **Optional Wayback archiving (#201).** When `wayback_save` is on and archive.org S3 keys are set
  (both via `watchdog configure`), the download step also submits each source to the Wayback Machine's
  Save Page Now and records the snapshot URL in the sidecar's `archived:` field — a citable public
  copy that outlives the original. Off by default, gated on the keys, and strictly best-effort:
  `save_to_wayback` catches every error and returns `None`, so archiving never blocks or fails a
  local deposit (which remains the source of record for ingest).
- **`watchdog fetch` — the non-agentic front door (#197).** When you already have a list of links,
  `watchdog fetch <url… | file>` downloads them straight into `_INCOMING/` with no research session.
  It is a thin CLI wrapper over the same `deposit_many` egress path (validation, size cap,
  sanitization, `.yml` sidecar, optional Wayback) — deliberately decoupled from the fetching internals
  so the downloader can be swapped without touching the command. Unlike the research post-flight it
  does not touch the durable worklist; its input is the explicit list you hand it.
- **Bounds are advisory.** `research_max_rounds` / `research_max_fetches` (and the effort tiers) are
  a budget the interactive skill self-limits to; only the egress hygiene is hard-enforced in Python.
- **Web access is scoped to the skill.** `WebSearch` and `WebFetch` (the skill's only outbound
  reach, for *reading*) are pre-approved only by the `watchdog-research` skill's `allowed-tools`
  frontmatter — granted while the skill is active, *not* added to the vault-wide `_VAULT_PERMISSIONS`.
  Archival downloading is a terminal post-flight, never granted to the skill — so a vault of sensitive
  source material carries no standing outbound-fetch permission.

See D45, D46, D47, D48, D61.

---

## 15. Invariants

These are the **governing rules of the pipeline** — the canonical statement of each principle. They are always true; violating one needs a *new, numbered decision* that supersedes the invariant, not just a code change. Read them first. The dated history of how each was established and refined lives in [DECISIONS.md](DECISIONS.md); where a decision operates within an invariant, *this* section is the authority on the principle and the decision entry records the specific change, rationale, and tradeoff.

Mechanically-checkable postconditions are guarded by named tests in `tests/test_invariants.py` (#349), one per invariant (or checkable part). Parts flagged below as prompt-relied — e.g. I1's summary grounding — are deliberately unguarded, since there's nothing mechanical for a test to assert.

- **I1 — Deterministic code writes; the model only reasons.** Anything derivable in Python (document identity, provenance, slugs, role targets, timeline fan-out) is stamped in code, never paid for in model output — and the model is not asked to restate as prose what it already emitted structurally. Carve-out: `document.summary` (#279) is a bounded, deliberate exception — a whole-document digest synthesized from `key_facts`, capped at three paragraphs and grounded (every claim in it must also exist in `key_facts`). That grounding is a **prompt instruction, not a verified postcondition** — unlike quote verification, no code checks the digest against `key_facts`, so a hallucinated claim would not be caught. *History: D2, D18, D24–D26, D29–D31, D33, D34, D75, D77, D78.*
- **I2 — Local-first preprocessing.** The *source documents you were given* never leave the machine, and chew costs no API tokens. This is a boundary on **source-doc egress**, not a vow of web abstinence — the investigation layer runs as agentic Claude Code sessions and web research (§14, I5) is allowed, since anything on the open web is already public. *History: D1, D45.*
- **I3 — Skills and prompt templates are global package resources** — read directly, never copied per-vault — and prompt templates live in their own directory so they never leak into the classifier index. *History: D21, D28.*
- **I4 — Configured model and effort only; no automatic escalation.** Each stage's model *and* its reasoning effort are explicit knobs with stable defaults; a failed call retries on the *same* model at the *same* effort — the pipeline never silently bumps either to recover. Response pagination (D104) is not an exception: continuing a truncated response prefills the same model's partial output at the same effort to finish one reply, changing neither knob. `finalizer_model` is itself an aggregate over four sub-stages (reconciliation, synthesis, timeline, briefing) each independently overridable (D137) — the "stage" this invariant guards is the finest-grained one actually configured, aggregate or per-stage. *History: D20, D36, D104, D137.*
- **I5 — Research output re-enters through `_INCOMING/`, never as a direct vault write.** Web research deposits findings as documents that flow through `chew → ingest`, keeping the deterministic pipeline the single writer (dedup, provenance, registry bookkeeping). Per-doc rationale rides the `.yml` sidecar; batch rationale goes to a `briefings/research-<date>.md` memo — never `context.md`. *History: D45, D46.*
- **I6 — Anything parsed out of a source document is untrusted input.** Source documents are adversarial by assumption — they arrive from leaks, FOI releases, and hostile parties. Content read *from* a document (its embedded metadata, its XML parts) is data to be defanged before use — never a directive, and never a payload the parser can be exploded by: XML goes through `defusedxml`, never the stdlib `xml.etree` (which expands internal entities, making a crafted `.docx` a billion-laughs bomb); metadata values are allowlisted and length-capped before they can reach a prompt; a reader that fails degrades to an empty dict rather than taking chew down. *History: D110.*
- **I7 — The vault mutates only at the finalize commit.** Extraction is a pure function of a document: it stages a durable `.watchdog/extracted/<sha>.json` and touches no committed vault state. Every write to that state — entity and document notes, the morgue, the registries, the timeline and search indexes — happens in one serial, sha-sorted commit pass at the top of `finalize` (`_commit_pending` replaying `write_vault.run`), after the pre-commit resolution steps (exact-name fold, entity-merge reconciliation) have run over the staged batch. Nothing before that pass writes a vault path, which is what makes the batch atomic: a reconcile failure leaves it wholly uncommitted and retriable, and `--no-finalize` leaves the vault untouched by construction, not by convention. *History: D126, D127, D128, D129.*

### Decision log

The dated, numbered history of specific decisions (D1, D2, …) — the rationale weighed and the tradeoff accepted for each — lives in its own file, [DECISIONS.md](DECISIONS.md), to keep this document focused on the current structure. Read it when you need the *why* behind a past decision; append new decisions there (ascending order, newest last).

