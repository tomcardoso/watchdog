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

In the app and the docs the three stages are called pre-processing (`chew`), processing (`dig`) and post-processing (`bark`), and the whole flow is "adding documents"; code, commands, settings keys and RPC names keep the original words (D275).

```
incoming/ ─▶ chew ─▶ .watchdog/queue/<sha>.json ─▶ dig ─▶ .watchdog/extracted/<sha>.json ─▶ bark ─▶ vault
 (raw docs)  (local)        (page text)          (extract)     (staged extraction)        (commit + finalize)
```

1. **Chew** (`watchdog chew`) — local, no model. Text/OCR, large-PDF chunking, near-duplicate
   fingerprinting. One queue JSON per document.
2. **Dig** (`watchdog dig`) — the orchestrator (`pipeline/orchestrate.py`) classifies and extracts
   each queued document concurrently and stages the validated result. Touches no vault state.
3. **Bark** (`watchdog bark`) — resolves the staged batch (exact-name fold, entity reconciliation),
   commits it to the vault in one serial pass, then runs post-ingest: contradictions, entity
   synthesis, timeline dedup, the briefing.

`watchdog add` runs all three in one go (D251), and bare `watchdog` inside a vault is a home screen
that offers it; `watchdog ingest` (deprecated, D138) runs dig and bark together. A failed document is set aside in `queue/_failed/` without sinking the batch.

---

## 3. Chew (preprocessing)

**Code:** `pipeline/preprocess.py`, `pipeline/preprocess_batch.py`, `pipeline/near_dup.py`,
`pipeline/sidecar.py`, `pipeline/file_metadata.py`.

- **Text.** Direct text where the file has it; Docling with OCR otherwise. Output is per-page
  markdown.
- **Recordings (D273, `pipeline/transcribe.py`).** Audio and video are transcribed locally by
  faster-whisper (int8 on the CPU), decoded by PyAV in streamed 20-minute windows cut at a quiet
  point. A page is a fixed five-minute block numbered by time (a silent block has no page), and
  each line starts with `[hh:mm:ss]`; lines break at sentence ends, so a timestamp never splits a
  sentence for quote checking. `metadata.source_type` is `transcript` and `metadata.media` is a
  versioned block (`format`): kind, duration, each page's time range, detected language, and the
  model and decode settings that produced it. The model is fetched on demand (once per drop, before
  the workers start), recordings transcribe one at a time, and the per-file timeout scales with
  length. The subprocess reports progress on stderr, forwarded by `preprocess_one`.
- **Page-scoped OCR (D189, D192).** Every PDF page is scored by three signals (character ratio,
  word shape, font CMap; two must agree). Pages without a usable text layer are force-OCR'd; the
  rest keep theirs. Pages are grouped by verdict into at most two classes, split at `chunk_size`
  (default 40), converted in parallel subprocesses and spliced back by page number.
- **Exact duplicates are skipped before OCR (D27)** — sha256 against the registry, the queue and
  the current batch. A match goes to `incoming/skipped/`.
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
Models, efforts, concurrency and classification come from `watchdog settings` or per-run flags;
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
   another page (D112, D200); stamps a source passage on every fact (`pipeline/passages.py`, D270:
   the resolved quote, else the best-matching sentence on the cited page found by code, else
   `unlocated`; no model call); explodes `key_facts` into per-entity fragments and timeline events
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

**Failure.** A document whose extraction or post-flight fails is logged to `processing.log` and moved
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

**Code:** `pipeline/entity_facts.py`, `pipeline/entity_notes.py` (D280).

An entity note is a view, rendered whole from stored data every time it is written; nothing in it
except the journalist's Notes is the only copy of anything.

| Section | Source | Written by |
|---|---|---|
| `## Summary` | registry entry's `synthesis` (summary, analysis, model, date, facts shown/total, citation map, citation counts) | synthesis (§8) or a `/watchdog-entity` session; citations rendered as checked links (§8.6); no byline (D284), never a fact |
| `## Facts` | every `key_facts` entry in the committed documents' stored extractions whose tags resolve to the entity | deterministic render: date order (fact date, else document date), document and page link, flags, quote or matched passage, reporter's mark, `^f-<hash>` block id; past 40 facts grouped by document (5 each plus every marked fact; the 40 most recent documents in full, earlier ones one line each) |
| `## Earlier claims` | registry `legacy_claims` | carried once from a pre-D280 note for documents with no stored extraction |
| `## Contradictions` | registry ledger, minus handled callouts | reconciliation (§8.5) |
| `## Relationships` | registry roles | deterministic merge |
| `## Notes` | the note on disk | the journalist; never touched |

A fact's tag is resolved to a current entity by `FactIndex.resolve`: the tag itself when that
entity lists the document, else the merge log's chain from a merged-away id to a record that lists
it, ignoring undone merges. The commit pass renders the notes of every entity a flush touched,
once, before the registries persist (`RegistryBatch.render_notes`). A reporter's mark re-renders
the fact's entities' notes when the registry lock is free, else queues them in
`registry/notes-stale.json` for the next flush. `entity_notes.rebuild` (the Maintenance job "Rebuild
notes") rewrites every entity and document note from the registries and stored extractions with no
model call; rebuilding the demo after deleting every note gives identical files. A vault whose
`registry.json` lacks `entity_note_format: 2` is rebuilt once at its next commit, carrying older
notes' AI prose into `synthesis` (`by: carried`). Timeline events live in the registry and
`timeline.md`; the note's chronological Facts list replaces its old Timeline section.

---

## 8. Entity synthesis

**Code:** `pipeline/synthesis_bundle.py`, `pipeline/finalize_entity.py`.

An entity earns a synthesized summary once its `appears_in` reaches 2 documents, counted across the
whole vault (D26). Only entities named in this batch are candidates; the batch is the run's
`result_*.json` set, so a resumed run still re-synthesizes it (D129). An entity whose `synthesis`
is marked `stale` (written before a merge into it, or an undo of one) is a candidate at every run
until rewritten, even with one document left, and its note and app page carry an "Out of date"
warning until then (`entity_notes.STALE_NOTICES`, D285). `build_bundle` gives the model
the entity's **facts** (D280), never its earlier prose: one line each with a short citation
(`[f:` + the shortest unique prefix of the D271 hash), date, document, page, warnings, the
reporter's Verified mark and `new` for this batch's facts. Disputed facts are included, labelled `disputed by the reporter`, and counted.
An entity past `FACT_MAX` (120) facts or `FACT_BUDGET_CHARS` gets this batch's facts, then verified
ones, then the most recent, and a `selection` line says how many it sees. Calls hold at most 25
entities (D238). The model returns a summary and an optional analysis, asked to cite fact ids where
a sentence rests on a fact (uncited framing is allowed). `apply_bundle` stores them in the
registry's `synthesis` with the model id, counts and the short-to-full citation map, then renders
the note, linking each citation to its fact (§8.6). An entity the model omits keeps
its synthesis. `/watchdog-entity` (`write_entity.py`) stores a session's summary the same way
(`by: session`).

---

## 8.5. Reconciliation

**Code:** `pipeline/reconcile.py`, `pipeline/chunking.py`, `pipeline/merge_entities.py`.

One reconcile call (split into context-bounded chunks when large, D237) does two jobs that need
every document's claims side by side:

- **Entity resolution, by confidence tier (D279).** `pipeline/identity.py` classifies every
  candidate pair from what the extractions record (names and aliases, roles, facts, and a
  type-gated harvest of labelled identifiers from facts about one entity): **high** (same
  identifier; a person's same full name plus a shared role, employer or street address; a
  non-person's same, specific name) merges in code; **medium** (a person's same full name alone;
  a short or generic name; reordered or similar names) goes to the model with both sides' facts and
  roles; **low** (initialled or partial person names) is never merged and becomes a "possible
  same" item; a conflicting identifier, different people's names or a pair the reporter dismissed
  is no pair. The fold (`_batch_exact_fold`, D127) folds only high matches, and gives a same-slug
  match that is not high a fresh slug (`john-smith-2`). `build_bundle` blocks the rest: same
  canonical type, name-token Jaccard ≥ 0.5 (subsets score 1.0), plus a surname index for initialled
  names, at least one side touched this run, capped at 2,000 pairs, then routes each by tier.
  `apply_merges` applies rule and model merges by what is already committed (D128): a staged-only
  loser is an id rewrite, two committed sides get `merge_entities.run`, a committed side always
  survives, and no chain of merges joins a dismissed pair. Merges apply **before** the commit
  pass; merged survivors get one contradiction-only follow-up call over their joined record.
  Every merge and candidate is staged on an extraction's `identity` block and written to
  `registry/merges.json` at commit (§12).
- **Contradiction detection.** For each entity in 2+ documents, `_FactLedger` reads its facts from
  the stored and staged extractions (D280), never a note: `new_facts` (this batch's documents) and
  `stored_facts` (earlier ones), each grouped under a `[[documents/<slug>|<title>]]` heading, each
  fact with its short id, page, warnings and source passage (quote or matched passage, cut to 240
  characters). Disputed facts are included, labelled; `legacy_claims` join the stored side. The model compares
  new against stored and new against new; a merged survivor's follow-up call treats every fact as
  new. An oversized entity loses its oldest stored facts, never the new ones. `apply_contradictions`
  files each conflict through `contradiction.run`, which validates both document slugs (D81).
  Applied **after** commit, because it needs the committed documents registry. Basis does not gate
  a contradiction (D214).

- **Re-check (D287, `pipeline/recheck.py`).** Stored facts are never compared with each other by a
  run, so the app offers an on-demand re-check of one entity or every recurring one. It sends the
  entity's facts as `new_facts` with `stored_facts` empty, through the same prompt and schema; one
  too large for a call is cut in date order into half-call blocks, each block sent as new against
  every later block as stored, the last alone, so every pair is seen once (an entity needing more
  than 45 calls is left out and said so). Small entities share a call. Findings are dropped when
  they cite the same two facts (or, lacking fact links, the same two pages) as a recorded or
  handled contradiction or an earlier finding, then filed by `contradiction.run`. It runs as a job
  under the processing lock, records its calls (`task: reconcile`) in a usage file and one history
  version; the app shows `contradictions.estimate` (calls, tokens, list-price cost on the
  configured model) and asks first.

A reconcile failure defers the whole batch: nothing commits, and the next `bark` retries (I7). A
contradiction failure after commit only leaves those callouts for a later run.

---

## 8.6. Fact citations

**Code:** `pipeline/citations.py` (D283).

A citation is a wikilink to a fact's line in its document note, `[[documents/<slug>#^f-<hash>|p. 4]]`
(the block id is `citations.block_id` of the D271 id; every document note's fact lines carry one).
Code makes and checks every one; no model ever writes a link a reader sees unchecked.

- **Model prose with short refs** (synthesis, the briefing): the model is shown `[f:3a9c]` refs and
  cites them; `render_short` replaces each run of refs with ` (p. 2; p. 4)`, each a link, through
  the stored map (`synthesis.fact_refs`, `.watchdog/briefings/<ts>.json`) and a lookup of the
  current fact — for a summary, only the entity's own current facts. An unknown ref or a fact that
  no longer exists is dropped (the sentence stays) and counted (`synthesis.citations`, the briefing's
  sidecar). A link to a Disputed fact is labelled `, disputed`. Summaries re-render on every note
  write, so their links follow marks and re-processing; a briefing is rendered once, when written.
- **Links already in text** (a `/watchdog-entity` summary; contradiction sides): `annotate_prose`
  drops a dangling link and labels a disputed one; `annotate_callouts` falls back to a plain
  document link and appends ` · *disputed*`. `resolutions._callout_text` ignores fact fragments and
  that label, so a callout's id is unchanged by either.
- **Contradictions**: reconcile may return `a_fact`/`b_fact` (the short refs it was shown);
  `contradiction.fact_block` resolves each only within the named document, by unique prefix.
- **Pages a session writes** (`queries/`, `wiki/`, research memos, also briefings):
  never rewritten. `check_text`/`check_vault` resolve every link and report found, not found and
  disputed; the app calls `vault.citations` for each page it renders and shows a dangling one as
  "source not found", `watchdog check-citations` and Maintenance → Check citations list them all.
  `watchdog search --json` gives each passage and corpus hit the facts on its page with their
  `cite` link, so a session cites what it found.

Uncited sentences are never flagged: AI-written text may frame and connect what the cited facts
say (the owner's call).

---

## 9. Finalize (`bark`)

**Code:** `orchestrate.finalize`, `orchestrate._post_ingest`, `pipeline/timeline.py`,
`pipeline/requests.py`, `pipeline/leads.py`, `pipeline/watchlist.py`, `pipeline/resolutions.py`.

Order: **tiered fold → reconciliation merges → commit → post-ingest.**

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

- **Contradictions** (§8.5), then **entity synthesis** (§8). Before the commit pass, a vault whose
  notes predate D280 is rebuilt once (§7).
- **Timeline.** Each document stages raw `{date}_{sha7}.ndjson` files; `timeline.collisions`
  promotes uncontested dates and returns dates with events from several documents (including
  several from one batch, D240). Each colliding date gets one dedup call (five dates at a time, at
  most 200 events per call, D248); `_select_kept` keeps the originals and unions entity ids onto
  each survivor (D59). A failed call leaves the date untouched for the next run (D65). Months
  mixing month- and day-precision events get a precision pass that can only fold a coarse event
  into a day (D63). `cmd_rebuild_timeline` is the single renderer of `timeline.md`.
- **Briefing.** One call over compact per-document results (key facts, entity names, near-dup
  alerts, contradiction flags) and the scratchpads, condensed in steps when the batch is large
  (D238). The key facts are read from the stored extractions as citable lines
  (`_briefing_cited_facts`: a short ref unique across the batch, date, page, warnings and the
  reporter's mark, Disputed labelled, D283). `_write_briefing` renders the model's `[f:…]`
  citations as links (§8.6) and writes `briefings/<ts>.md` (a counter suffix on collision; its
  emerging patterns, open questions and one-line status included), a `log.md` entry and
  `.watchdog/briefings/<ts>.json` (the ref map, citation counts and the status line the Overview
  shows as its headline). `--skip-briefing` skips the call and those files (D134). It no longer
  writes `hot.md` (D285): see the session primer, §12.
- **Leads, watch-list alerts and document requests.** Model-free sweeps write dated briefing files;
  `resolutions.json` holds acknowledgments so handled items don't resurface (D68).
  `watchdog review` (`cmd/review.py`) steps through the open ones, plus near-duplicate documents
  (`documents.json` `near_duplicate_of`, stamped at extraction from chew's MinHash match), and
  writes to the same store (D252). Document
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
incoming/                    drop zone for chew; incoming/failed/ and incoming/skipped/ are set aside (D266)
context/                     background material for the context interview (D266)
entities/<type>/<id>.md      entity notes; <type> is the closed vocabulary (D105)
documents/<slug>.md          document notes (slug gains -<sha6> when another document owns it, D241); each fact line ends in its ^f- block id, the target of every fact citation (D283)
morgue/<entity>/<type>/…     originals + a <name>.md full-text sibling (D26); same -<sha6> rule
timeline.md                  rendered global timeline
briefings/                   briefings, leads, alerts, research memos
requests.md                  open document requests
verification.md              generated list of the facts a reporter has marked (D271); never hand-edited
merges.md                    generated record of every entity merge and open "possible same" pair (D279)
context.md / log.md          investigation context, run log (an older vault's hot.md is left in place, unread, D285)
index.md / dashboard.base    landing page and Obsidian Bases dashboard (D42)
queries/ wiki/               session-written findings and threads
.embeddings/ .fulltext/      search indexes
.claude/                     Claude Code settings and /watchdog-* commands (D245)
.watchdog/
  queue/<sha>.json           chewed documents; removed after commit
  staging/<sha>/             chewed originals
  extracted/<sha>.json       staged, validated extractions; kept, and the source every entity note is rendered from (D280)
  timeline/                  raw and canonical NDJSON events
  tmp/                       per-run scratch (result_<sha>.json, notes_<sha>.md, checkpoints)
  briefings/<ts>.json        a briefing's short-ref → D271 id map, citation counts (D283) and status line (D285)
  research/                  research worklist (§14)
  backups/<ts>-<op>/         pre-mutation snapshots (merge-entities, undo-merge, a fresh run's wipe of leftovers)
  history/                   version history of generated and app-edited files (D286): objects/ (zlib blobs by SHA-256), log.jsonl, index.json, .lock
  processing-state.json      present while a run is in progress
  .preprocessing-lock        held while files are pre-processed
  registry/
    entities.json documents.json registry.json manifest.json
    resolutions.json requests.json batch-pending.json
    verification.json        the reporter's marks on facts (D271); source of truth for verification.md
    merges.json              the merge log and "possible same" candidates (D279); source of merges.md
    notes-stale.json         entities (and `doc:<sha>` document notes, D285) whose notes a mark could not refresh, rendered by the next commit flush (D280)
    processing.log           per-document START/OK/WARN/FAILED lines
    usage/usage-<ts>.json    per-call token/cost/latency records (D50, D86, D132)
    .processing-lock .write-lock .verification-lock
```

**Passages and the verification ledger (D270, D271).** Each fact in `.watchdog/extracted/<sha>.json`
carries `passage`, `passage_page`, `passage_method` and `passage_score`, and the document records
`passages_version`; they are stamped in post-flight, so they reach the vault only through the
finalize commit (I7). Documents committed earlier have none and are not backfilled. Facts have no
stored id: `pipeline/verification.py` derives one from the document's SHA-256, the fact's page and
its normalized text. `registry/verification.json` (`schema_version`) holds the marks, each with a
snapshot of the fact it was made on and a history; it has its own lock, `.verification-lock`, so a
mark never waits on a commit pass, and every write regenerates `verification.md` (`format_version`)
at the vault root. The ledger never changes a fact, and the app writes it through `verification.mark`,
the function `watchdog verify-fact` calls (I10).

**Recordings in the registry (D273).** A recording's `documents.json` entry carries the `media`
block from pre-processing (stamped on the document at extraction): readers use the fields they
know and ignore the rest, whatever its `format`.

**Working files (D276).** The locks, run state and run log are named for the app's stages and built
through `vault_paths.py` helpers; the same migration renames an older vault's `.chew-lock`,
`.ingest-lock`, `ingest-state.json` and `ingest.log`.

**Folder names (D266).** `vault_paths.py` is the one place the `incoming/` and `context/` names live, with
helpers for every path under them. `migrate_folder_names` renames an older vault's `_INCOMING/` and
`_CONTEXT/` (never deleting a file) and runs at the start of each command that touches them and when the
app opens a vault (`require_vault`).

Every model call is also recorded in `~/.watchdog/telemetry.db` (D193): vault path and name,
filename, model, tokens, cost. Off with `telemetry false`; `delete --purge` removes a vault's rows
(D247).

**Session boundaries (D245).** A vault's Claude Code settings pre-authorize writes only to the
session's own pages (`queries/`, `wiki/`, `briefings/`, `context.md`, `.watchdog/tmp/`,
`.watchdog/research/`) and a few deterministic commands. `refresh-skills` updates commands,
permissions, the prompt hook and dashboard views in existing vaults.

**Session primer (D285).** The vault's SessionStart hook (matcher `startup|resume|compact`) runs
`watchdog session-primer`, an internal command that prints `cmd/primer.build(vault)`: the questions
in `context.md`, counts and verification progress, the most-mentioned entities with note links,
open contradictions, leads, document requests, possible same entities and disputed facts (with
citation links), the last three briefings with their status lines, and how to cite. It is read from
the registry, the ledger, the merge log and the requests ledger with no model call, built fresh for
every session, and budgeted (`BUDGET_CHARS`, 6,000 characters; lists shrink from five items to
none until it fits). It never fails a session's start. The app shows the same text under Briefings →
Current state (`vault.sessionPrimer`). An older vault's hook, which `cat`ed `hot.md`, is rewritten by
the D266 migration on first use (`vault_paths.retire_hot_md_hook`), which also refreshes its
`.claude/CLAUDE.md`; `hot.md` itself is left on disk and is no longer read, indexed or
citation-checked.

**Version history (D286).** `pipeline/history.py` versions every tracked file: Markdown outside
`morgue/`, `incoming/`, `context/` and hidden folders, plus `entities`, `documents`, `merges`,
`verification`, `resolutions` and `requests` `.json`. Blobs are content-addressed and compressed;
`log.jsonl` (`schema_version` per line) is append-only and the source of truth; `index.json`
(per path its versions and last-seen size and mtime, per version its cause and log offset) is
rebuilt from it when missing or behind. Each write point wraps itself in
`history.recording(vault, cause, scope)` under its own lock: finalize (one version per run, the
documents as cause), `merge_entities.run`, `merge_undo.undo`, `entity_notes.rebuild`,
`verification.mark`, the app's `saveNotes`/`writeFile`/Review resolutions, each Ask Claude turn,
and restores. Entering records changes made since the last version (cause `found`); recordings
nest, so a merge inside a run is part of the run's version. While a run holds the vault, a small
edit records only its own files. Restore writes a page back whole, an entity or document note's
Notes section only, and nothing else (generated files and registries are view-only). App-only
(I10).

**Merging entities (D54).** `watchdog merge-entities <keep> <merge>` unions the losing entity onto
the survivor, remaps every registry and timeline reference, writes a redirect stub, and snapshots
what it changes. The merged id's facts reach the survivor through the merge log (§7). It keeps one
`synthesis`, marked `stale: merge`, and suggests `/watchdog-entity` when both had one.

**The merge log (D279).** `registry/merges.json` (`schema_version` 1) records every merge — keep
and merged records, tier, `decided_by` (`rule`, `model` with its id, or `reporter` with the
`reporter_name`), rule, reason, evidence per occurrence (documents, D271 fact ids, matching
identifier, shared attributes), run id and timestamps — and every "possible same" candidate
(`same:<hash>` ids; "Not the same" is a resolution). A recurring decision adds an occurrence, not
an entry. It is written only in the finalize commit (`RegistryBatch.flush`, before the registries,
idempotently) or by `merge_entities.run`, under the registry lock, and regenerates `merges.md`. Each
entry's `undo` keeps the merged record's extracted id, documents and fact ids, its registry
snapshot and the backup path; the staged extraction keeps each moved entity's `extracted_id` and
each fact's `extracted_entities`. Since D280, `undo.version` 2 adds `changes`: per document, the
entity records, facts (with their tags then) and roles that carried the merged id just before the
fold (`merge_log.carried_items`); a recurring decision accumulates them. A same-document section
fold (`merge.py`, D282) is logged with rule `same-document-section` and `undo.available: false`.

**Undo merge (D281).** `pipeline/merge_undo.py` (the app's job `jobs.undoMerge`) re-tags the
recorded items to the split record (the merged id when free), marks the entry `undone`, rebuilds
both registry entries' documents, aliases, roles and timeline events from the extractions,
re-points and restores third records' roles, retags timeline NDJSON from the facts, marks the pair
"Not the same", and renders the notes. Contradictions, the journalist's Notes and the AI summary
stay with the survivor. `merge_undo.check` refuses, with a reason, pre-D280 entries, section folds,
undone entries, a survivor merged away later, a merged id now taken, or a reprocessed document.

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
| entity-synthesis | per 25 recurring entities touched this run | each entity's facts with short citation refs, warnings and marks (not its earlier prose, D280) | timeline, relationships, contradictions |
| timeline-dedup | per colliding date (≤200 events per call) | event text and page | other dates; entity histories |
| timeline-precision | per month mixing precisions | that month's events | other months |
| request-dedup | when a run adds a request (≤200 per call) | open request type, wording, likely source | everything else |
| briefing | once per run | brief, compact per-document results with citable fact lines, alerts, contradiction flags, scratchpads | raw text; full notes |

---

## 14. Web research

**Code:** `pipeline/research.py`, `pipeline/capture.py`, `cmd/research.py`.
**Skill:** `skills/watchdog-research.md`.

`watchdog research` opens a Claude Code session on `/watchdog-research`, which proposes a mission,
researches in rounds and appends kept sources to `.watchdog/research/queue.tsv`. When the session
ends, Python downloads the queued URLs into `incoming/` (after a confirm); chew and dig remain the
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
- **`watchdog research fetch`** downloads a given list of links through the same path, without a session.
- Round summaries go to `briefings/research-<date>.md`, never `context.md`. Research bounds are
  advisory; only the egress checks are enforced. Web tools are granted by the skill's own
  `allowed-tools`, not the vault-wide permissions.

See D45–D48.

---

## 14.5. Desktop app

**Code:** `gui/` (Electron main, preload, React renderer), `watchdog/gui/` (server, `api/*.py`,
`jobs.py`, `chat.py`, `demo.py`), `watchdog/progress.py`. **Contract:** `gui/API.md`.

- **One program, two front ends (D265).** The app starts `python -m watchdog.gui.server` and speaks
  line-delimited JSON-RPC over its stdio. Reads run in-process; `sys.stdout` is pointed at stderr so
  a library `print()` can't corrupt the protocol.
- **Mutations are CLI commands.** `jobs.start` (long runs: `add`, `chew`, `dig`, `bark`, `reindex`,
  `merge-entities`…) and `action.run` (quick ones: `projects rename`, `unlock`…) run
  `python -m watchdog <args>` in the vault with stdin closed, `NO_COLOR=1` and
  `WATCHDOG_PROGRESS=1`. `progress.emit` then writes prefixed JSON lines (chew files, per-document
  extraction states, finalize stages) that the server turns into `job.progress` events; without the
  variable it writes nothing. The few in-process writes go through the CLI's own library functions
  (`resolutions`, `_coerce_value`/`_persist`, auth state, a note's `## Notes` body), plus the
  app-only version history (D286).
- **The public-records gate** runs in the app after chew and before the model call, from
  `ingest.preflight` (count, models, `auto_approve` verdict, the warning text), then `add
  --skip-warning`.
- **Claude sessions** (`chat.*`) use the Claude Agent SDK with `cwd` set to the vault and project
  settings loaded, so the vault's permissions and `/watchdog-*` commands apply; transcripts are kept
  under `~/.watchdog/gui/chats/`.
- **Files.** The renderer reads vault files only through `wdfile://`, which the main process limits
  to registered vault folders, and which answers byte-range requests (206) so a recording can
  seek. Thumbnails are cached in the app's user-data folder. A recording opens in a player above
  its transcript; its citations read as time ranges (D273).
- **Engine (D267).** The app installs its own Python: a bundled `uv` creates a Python 3.12
  environment under the app's user-data folder (`engine/`) and installs the bundled wheel of the
  same version, then downloads the local models (`gui/src/main/engine.ts`,
  `watchdog/gui/engine_setup.py`). It installs in two phases (D272): phase 1 (Python, the wheel,
  the light libraries from `engine_setup core-requirements`) is what first-run setup waits for;
  phase 2 (the rest of one `uv pip compile` lock, then the models) runs in the background and
  resumes at launch. Until it finishes the sidecar runs with `WATCHDOG_ENGINE_PENDING=1` and
  `jobs.start`/`action.run` refuse document-adding commands (`engine_not_ready`);
  `engine.setReady` lifts it without a restart. `engine.json` is versioned (`schema`, finished
  `phases`); an unreadable or foreign record rebuilds the environment. `WATCHDOG_PYTHON` or a user's choice overrides it; in
  development the repository's source goes first on `PYTHONPATH`. The terminal commands remain the
  app's mutation path but are retired from user-facing documentation. The transcription model is
  an on-demand step (`engine_setup.ON_DEMAND`): never in the first-run download, fetched by the
  first recording or by `setup.downloadModel`, a job running `engine_setup models --only
  transcription` (D273).
- **Folder access (D268).** `~/.watchdog/access.json` lists the folders the user has allowed;
  only the main process writes it (`gui/src/main/access.ts`). The backend runs with
  `WATCHDOG_ENFORCE_ACCESS=1`, under which `watchdog/access.py`'s audit hook refuses writes outside
  allowed folders and Watchdog's own exempt locations, in the sidecar and every CLI subprocess; the
  sidecar answers `not_granted` for a vault outside the list, and app-run Claude sessions are denied
  edits outside their vault. Their shell commands run in Claude Code's sandbox on macOS and are
  limited to the vault's pre-approved `watchdog` commands elsewhere (D274).
- **Version history (D286).** Entity, document, briefing and page views have a History panel
  (`history.file`, `history.diff`, `history.restore`), Activity has the investigation's versions
  (`history.versions`), and Settings → Version history shows the store's size and clears it
  (`history.clear`). These call `pipeline/history` in-process; there is no CLI command.
- **Re-check contradictions (D287).** An entity page's Re-check and Maintenance's whole-investigation
  card call `contradictions.estimate`, then `jobs.recheckContradictions`, which runs `python -m
  watchdog.pipeline.recheck` as a job (engine-gated, refused while a run holds the vault). No CLI
  command.
- **Release and updates (D269).** `electron-builder.config.cjs` and `publish.yml`'s `app` job
  build signed (when configured) installers from the version tag; `gui/src/main/updater.ts` offers
  updates from GitHub Releases. See `gui/DISTRIBUTION.md`.

---

## 15. Invariants

The governing rules. Changing one needs a new numbered decision that supersedes it. Mechanically
checkable parts are guarded by named tests in `tests/test_invariants.py`; prompt-relied parts are
noted as such.

- **I1 — Deterministic code writes; the model only reasons.** Anything derivable in Python
  (identity, provenance, slugs, role targets, timeline fan-out) is stamped in code, and the model
  is not asked to restate as prose what it emitted structurally. Exception: `document.summary`, a
  bounded digest grounded in `key_facts` — a prompt instruction, not a checked postcondition.
  *History: D2, D18, D24–D26, D29–D31, D33, D34, D75, D77, D78, D170, D270.*
- **I2 — Local-first preprocessing.** Source documents never leave the machine during chew, and
  chew costs no API tokens. This bounds source-document egress; web research is allowed (§14).
  *History: D1, D45.*
- **I3 — Skills and prompt templates are global package resources**, read directly and never
  copied per vault; prompt templates never appear in the classifier index. *History: D21, D28.*
- **I4 — Configured model and effort only; no automatic escalation.** A failed call retries on
  the same model at the same effort. Continuing a truncated response is not escalation. The
  verification pass's model is fixed to `extractor_model` (it reads that model's cache); its effort
  is configurable. *History: D20, D36, D104, D137, D172, D181, D221, D222.*
- **I5 — Research output re-enters through `incoming/`,** never as a direct vault write.
  *History: D45, D46.*
- **I6 — Anything parsed out of a source document is untrusted input.** XML goes through
  `defusedxml`, never the stdlib parsers; metadata is allowlisted and length-capped; a failing
  reader yields `{}`; document text in a note is defanged. A command a vault's session runs without a
  prompt reaches only that vault: under `CLAUDECODE`, `search` and `write-entity` refuse other
  investigations and files outside it, and the vault denies its sessions `~/.watchdog`.
  *History: D78, D110, D154, D241, D257.*
- **I7 — The vault mutates only at the finalize commit.** Extraction stages
  `.watchdog/extracted/<sha>.json` and touches no committed state. Every vault write happens in the
  serial, sha-sorted commit pass, after the pre-commit fold and merges — so a reconcile failure
  leaves the batch wholly uncommitted, and `dig` leaves the vault untouched by construction.
  Investigation sessions don't hand-edit pipeline-owned files; they change pipeline state only
  through deterministic commands that take the registry lock. *History: D126–D129, D245, D258.*
- **I8 — Transcribe source values as printed.** Dates, figures, file numbers and names are
  extracted as they appear, even when they look wrong; an inconsistency is noted in the fact, not
  corrected. A prompt instruction with no ground truth to check against. (Correcting a fact's
  `page` citation, D177, is not covered: a citation is checkable.) *History: D167, D177.*
- **I9 — Styling is a terminal affordance.** `--json` output, and any output off a real terminal,
  carries no escape bytes; diagnostics go to stderr in every mode. *History: D174.*
- **I10 — The app adds no pipeline behaviour.** Every vault mutation the desktop app makes runs the
  CLI command that makes it in the terminal, or the library function that command calls; the app
  keeps the CLI's gates (the public-records acknowledgement, confirmations before irreversible
  operations). An app-only feature with no CLI command (version history's restore and clear, D286)
  may change the vault through a library function directly, under the same folder access (D268),
  the operation's locks and I7's commit discipline. The command line is expected to be removed
  eventually; new features need not gain a command. *History: D265, D271, D286.*
- **I11 — Under the app, Watchdog writes only where the user has allowed it.** With
  `WATCHDOG_ENFORCE_ACCESS=1`, file changes under the home folder or mounted volumes outside an
  allowed folder or an exempt location are refused, and only the app's main process ever writes
  the allowed list. Guarded by `tests/test_access.py` and `tests/test_gui_access.py`. *History:
  D268, D274.*
- **I12 — Entity merges are tiered and recorded.** Two entity records are joined only on a
  high-confidence rule, a model judgement of a medium-confidence pair, or a reporter's merge; a
  person is never merged on a name alone, and initialled or partial names are never merged
  automatically. Every merge is written to `registry/merges.json` with who decided and the
  evidence, including a fold inside one document read in sections, and a pair the reporter marked
  "Not the same" (or whose merge was undone) is never merged automatically.
  *History: D127, D279, D281, D282.*
- **I13 — Entity notes are views of stored facts; model prose never replaces a fact.** An entity
  note's facts are rendered by code from the stored extractions on every write, and the note can be
  rebuilt from stored data with no model call. Model prose is stored separately (the registry's
  `synthesis`), shown under the note's Summary heading (no byline since D284), and is never an input to
  synthesis or to the contradiction check, which read facts. *History: D26, D118, D280, D284.* Guarded by
  `tests/test_invariants.py::test_I13_…` and
  `tests/test_gui_demo.py::test_deleted_notes_rebuild_identically_with_no_model`.
- **I14 — A rendered citation always resolves to a stored fact.** Wherever Watchdog shows or writes
  a fact citation as a link — an entity summary, a briefing, a contradiction side, a page
  the app renders — code has resolved it to a fact that exists now; one that does not resolve is
  dropped (generated text) or shown as "source not found" (a page a session wrote, never rewritten).
  A short ref resolves only through the map stored with the text that cited it, and a contradiction
  side only within its named document. Uncited sentences are allowed and never flagged; a Disputed
  fact is cited and labelled, never hidden. *History: D271, D280, D283.* Guarded by
  `tests/test_citations.py` and `tests/test_gui_demo.py::test_demo_summaries_cite_facts_and_every_citation_resolves`.
- **I15 — A disputed fact is labelled wherever a fact is shown, and never dropped.** A fact the
  reporter marked Disputed stays in every list, note, timeline, export, search result and session
  primer that would hold it, carrying the label "disputed"; no step filters it out. The synthesis,
  briefing and contradiction-check inputs include it, labelled (the same-name comparison of D279
  sends facts unlabelled).
  A mark counts only while it matches the fact's words and page (D271). *History: D280, D283,
  D285.* Guarded by `tests/test_gui_demo.py::test_a_disputed_fact_is_shown_labelled_on_every_surface`.
