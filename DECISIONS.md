# Watchdog — Decision log

The dated record of architectural decisions, each operating within the **Invariants** in [ARCHITECTURE.md §14](ARCHITECTURE.md#14-invariants). An entry is the authority on its *specific* change — the rationale weighed and the tradeoff accepted — not on the general principle (that lives in the Invariants). For the current structure of the system, read ARCHITECTURE.md; read here only when you need the *why* behind a specific past decision.

**Appending:** add the next `### D<n>` at the **end** (ascending, newest last). Keep entries concise — a few sentences of rationale, then the tradeoff. A decision earns an entry only if it forecloses a future option or would read as a bug without the rationale; pure refactors belong in the commit message. When a decision establishes or revises an invariant, update the Invariants section in ARCHITECTURE.md in the same change.

---

### D1 — Local-first preprocessing

Source documents never leave the machine; no API cost for chew

**Tradeoff:** Bound by local compute for OCR/layout

### D2 — Deterministic code writes, model decides

Reproducible, testable, cheap bookkeeping; model reserved for judgement

**Tradeoff:** More Python surface to maintain

### D3 — ~~Isolated extractor subagent per document~~ **Superseded by D18.**

Kept the (model) orchestrator's context flat by extracting each doc in a throwaway subagent. Moot once the orchestrator became Python (no model context to keep flat)

**Tradeoff:** —

### D4 — Classification at extraction time, by the model (§6)

Accurate, near-free (doc already read), deleted the embedding subsystem

**Tradeoff:** `document_type` null until extraction

### D5 — Structured vs. synthesized note split (§7)

Mechanical merge is correct+free for facts; prose needs the model

**Tradeoff:** Two write paths

### D6 — Contradictions as a discrete cited section, verified at extraction (§7)

Verifiable claims; the extractor is the sole verifier (no orchestrator removal pass); chronological sort would be a worse timeline

**Tradeoff:** Extra section + extraction field; a bad callout isn't caught downstream

### D7 — Carryforward + gated entity-synthesis (§8)

Cost scales with contested entities, not all entities; fixes summary clobber and the within-batch race

**Tradeoff:** Single-new-mention entities only revised, not fully re-synthesized

### D8 — Fragments as a write-time byproduct (§8)

Synthesis input with zero extra extractor tokens

**Tradeoff:** Run-scoped scratch state in tmp

### D9 — Raw-then-canonical timeline files (§9)

Subagents write lock-free; merge deferred to one post-batch step

**Tradeoff:** Two file generations on disk

### D10 — MinHash near-dup, detect-only (§10)

Cheap local dedup signal; journalist decides

**Tradeoff:** Approximate similarity

### D11 — Separate lightweight manifest (§12)

Cheap candidate lookup in pre-flight

**Tradeoff:** A second index to keep in sync (done in `write_vault`)

### D12 — ~~Generate `records/_index.md` by scanning the per-vault records dir~~ **Superseded by D21.**

A static index file silently drifts from the skills it describes. Moot once the classify index is built in memory from the global catalog (D21) — no file to scan or keep fresh

**Tradeoff:** —

### D13 — Sectioned extraction for large documents (§5)

Documents over the token threshold don't fit one context; sequential page-range sections with a carried scratchpad + deterministic `merge-sections` preserve cross-section consistency

**Tradeoff:** Large docs are extracted serially (slower); needs overlap + merge-dedup

### D14 — ~~Timeline reconciliation + briefing in one finalize subagent~~ **Superseded by D18.**

Kept scratchpad prose and timeline NDJSON out of the model orchestrator's context. Moot under Python orchestration — `_post_ingest` reads them directly

**Tradeoff:** —

### D15 — Runaway guard + `ingest-abort` (§5)

A stuck subagent bails (`STATUS: failed`) instead of wedging the batch; clean bail leaves no partial vault writes, so the doc re-ingests cleanly

**Tradeoff:** A failed doc must be manually moved back from `queue/_failed/` to retry

### D16 — ~~Entity synthesis (§8) and finalize (§9) kept as separate subagents, not merged~~ **Superseded by D17.**

Kept the per-entity synthesis fan-out apart from the whole-batch finalize to avoid serializing entity work. Moot once the fan-out itself proved to be the cost problem and both merged into one post-ingest agent (D17)

**Tradeoff:** —

### D17 — Merge synthesis + finalize into one post-ingest subagent fed a Python-built bundle (§8, §9)

The per-entity fan-out launched one subagent per `count ≥ 2` entity (36 in a representative run), each paying startup + preamble cache-write to do a few hundred tokens of judgement — ~$5 of a $19 run. D16 feared merging would re-bloat context, but fragments are *compact digests* (D8), so one agent reading the whole bundle stays small; the cost win (one agent, one cached preamble) dominates the serialization cost. The bundle's `build_bundle`/`apply_bundle` survive into D18; only the surrounding subagent is gone

**Tradeoff:** A very large batch could need bundle splitting by token budget (not yet implemented)

### D18 — Python orchestrator; the model is called only for reasoning (#118 W3)

A Claude Code skill session *is* a model loop, so the orchestrator (and per-turn coordination) cost model tokens even for pure dispatch — the orchestrator alone ran $1–3+ of a representative batch, ~38–86% of pipeline spend was per-turn context re-send. Moving the loop to Python (`orchestrate.py`) calling the model only for classify/extract/synthesis/timeline-dedup/briefing removes that floor and lets each task route to a backend + tier (`model_client`, D-auth #119). Supersedes the subagent split (D3) and the off-orchestrator finalize (D14)

**Tradeoff:** Extraction now runs the Claude Agent SDK in-process and parallel concurrency is bounded by subscription/API rate limits (`extract_concurrency` knob); the `claude-api` backend is unproven until a metered key is used

### D19 — Force-section on whole-doc output overrun (§5)

Sectioning triggers on *input* size (`section_token_threshold`), but truncation is *output*-driven — a moderate-input, entity-dense doc overruns the model's output ceiling on the agent-SDK backend (which can't cap output), truncating the JSON and escalating the retry toward a pricier tier. On a multi-page doc whose whole-doc extraction is rejected, the orchestrator re-runs it through the sectioned path with a small forced budget (≥2 sections) to bound per-call output

**Tradeoff:** Adds one (failed) whole-doc attempt before the fallback; single-page docs can't be split, so they still just fail

### D20 — Configured model only — no automatic escalation; classifier model is its own knob

`model_client` originally bumped the tier up (haiku→sonnet→opus) on a JSON-validation failure. That makes ingest cost unpredictable and can silently spend opus money — the opposite of a budgeted pipeline. Now a failed call retries on the **same** configured model (the orchestrator's post-flight repair + D19 sectioning handle genuine failures), and the classify step gets its own `classifier_model` knob (default haiku) alongside `extractor_model`/`finalizer_model`, so each stage's model is explicit and stable

**Tradeoff:** A doc that a stronger model would have salvaged now fails instead of auto-upgrading — the user opts into a stronger model deliberately via config

### D21 — Record skills are global, not per-vault (`skills_catalog`)

Copying skills into every vault was a holdover from the Claude-Code-skill ingest, which needed them under `.claude/commands/` for discovery. The Python orchestrator (D18) just reads the file, so the copy was vestigial and created drift (each vault stale until `refresh-skills`). Skills now live in the package + `~/.watchdog/skills/records/` (user overrides) and are read directly; the classify index is built in memory (supersedes D12); per-skill `description:` frontmatter lets a user-added skill set its own index line. Pinning (`--skill`/`default_skill`, a name or path) and `watchdog show-skills` round it out. Pre-production, so vault-local copies are simply ignored (strictly global) rather than migrated

**Tradeoff:** Loses per-vault skill freezing/customization; mitigated by `--skill PATH` for one-offs and by stamping the skill used into each document's note + registry (`record_skill`) for provenance. Custom skills in `~/.watchdog` aren't carried with a shared vault

### D22 — Extractor emits structured **evidence fragments** instead of prose `analysis`; optional verbatim `quote` on fragments and `key_facts` (#107) **— the extractor no longer emits fragments directly (D26); they are reconstructed from tagged `key_facts`, but everything downstream (the structured-claims-feed-synthesis digest, the `## Analysis` render) is unchanged.**

The per-entity `analysis` prose was thrown away and rewritten by synthesis for every multi-mention entity (D7/D17), so the extractor did prose work twice. Replacing it with `{claim, page, confidence, reason}` fragments gives the synthesizer a clean, page-anchored, citable digest to compose from rather than re-prosing prose — and single-mention notes render the claims directly under `## Analysis`. Quotes are *captured* at extraction only when the wording is significant, and on entity fragments are *surfaced* into synthesized prose only in exceptional cases (the synthesis prompt gates this); on `key_facts` the quote renders verbatim in the document note (terminal, reader-facing — the strongest placement). This is a **quality/citability** change, not a cost trim (structured fragments cost slightly more output tokens than the prose they replace) — the cost trim is the separate #127

**Tradeoff:** More structured extractor output; a captured quote against garbled OCR isn't verified to appear on the page (that check is the deferred #106 evaluator); existing notes keep their old prose `## Analysis` until the entity is next extracted

### D23 — Post-ingest is a re-runnable `finalize` step over per-run on-disk inputs (#135)

Post-ingest (synthesis + timeline + briefing) calls the model, so a rate limit can interrupt it *after* extraction has already written the vault — and `ingest_setup` wipes the fragment queue at the next ingest's start, so a re-ingest would silently drop the un-synthesized batch. Post-ingest is now `orchestrate.finalize`, fed inputs that persist in `tmp` (entity fragments + per-doc `result_*.json` + scratchpads); `watchdog ingest` calls it at its tail, and `watchdog finalize` re-runs it standalone to complete an interrupted batch. A clean pass clears the inputs (idempotent, and fixes a latent scratchpad-accumulation bug where briefings re-included prior runs); an interrupted pass leaves them for retry. A new `watchdog ingest` over a pending batch prompts to **merge** (keep the inputs via `wipe_pending=False` so both batches finalize together), **finalize** first, or **discard**; `watchdog status` flags a pending batch

**Tradeoff:** No cross-run finalization queue beyond the merge prompt; synthesis re-runs are idempotent (bulk overwrite), so a partial finalize is safe to repeat

### D24 — Roles are extracted by `target_id` only; `target_name`/`target_type` are re-inflated deterministically (#140)

Extraction output is ~95% of run cost and roles were the single largest field — and ~43% of the roles payload was the target's `target_name`/`target_type`, both *derivable from `target_id`* via the registry, re-typed by the model on every role (e.g. an 86-char case name repeated across a dozen roles). The extractor now emits roles by id; `write_vault._resolve_role_targets` fills name/type from this batch's entities + the registry right after id reconciliation, so every downstream consumer (note links, pre-flight context, synthesis digest) is unchanged. First concrete cut from #140 — moves derivable data out of paid model output into free deterministic code

**Tradeoff:** A dangling `target_id` (no matching entity) falls back to the id as name + `Unknown` type; the win is only realized while extraction stays on a metered tier (Sonnet output is the cost)

### D25 — `confidence` is omitted when `high`; absent ⇒ `high` (#140) **— field reworked into `basis` by D34; the omit-default mechanism survives, the 4-level scale does not.**

Measured across a 5-doc run, **99% of all confidence values were `high`** (411 rendered, 8 non-high). Requiring it on every fact/role/event/key-fact paid model output to restate the default ~99% of the time (~6% of extraction output). The extractor now emits `confidence` only for `medium`/`low`/`disputed`; the schema makes it optional and every consumer defaults absent → `high`, so notes render identically and the rare exception stands out instead of hiding in a sea of `high`. Same omit-defaults pattern extends to `page` (omit when no marker) and empty arrays

**Tradeoff:** Defaulting absent → `high` is the *optimistic* direction — a forgotten mark silently reads as `high` — but empirically the model flagged all 8 non-high cases, so exposure is ~8-in-411. Whether `confidence` is well-calibrated at all (≈always `high`) is a separate quality question (#143)

### D26 — Unified **fact primitive**: the model emits each material fact once on `document.key_facts`, tagged with `entities` + an optional `date`; postflight fans it back out (#140)

The extractor restated the same fact up to three times — as a `document.key_fact`, as each involved entity's `evidence_fragment`, and (if dated) as each entity's `timeline_event` — plus a per-entity `summary`, so a 5-page order emitted ~4× the source text in JSON (32K chars from 7.7K of Docling markdown). That redundancy was rational only because the Docling text was **discarded** after extraction, making the JSON the sole text record. D26 retains the full text in the morgue as a sibling `<name>.md` (deterministic, $0), then collapses the restatement: the model emits a fact once with `entities` tags (who it's about) and an optional `date` (when it occurred); `postflight.explode_key_facts` deterministically reconstructs the per-entity `evidence_fragments` and `timeline_events` the writers already consume, and `stage_timeline_events` reads the dated facts directly. Entities keep only the graph layer (identity, aliases, roles, contradictions); no per-entity summary/fragments/timeline. `key_facts` is materiality-driven with no fixed count. Stacks on D24/D25. Synthesis is now gated on **project-wide recurrence** (`appears_in ≥ 2`) rather than batch-local mention count, so an entity recurring across separate batches/years is promoted; single-document entities are deterministic stubs with no Summary section (§8)

**Tradeoff:** A single-document entity gets no synthesized summary at all (by design — its facts sit in `## Analysis`), and a model only ever judges an entity from one document plus carried prose, never a fresh re-read of all sources (that's `/watchdog-entity`). A dated fact must be *tagged* to reach an entity's per-entity timeline (the global timeline still gets it untagged). Timeline recall now depends on the model attaching `date` to occurrence-facts rather than filling a dedicated field

### D27 — Skip exact duplicates at chew, not just at ingest (#146)

Re-extraction was already a no-op — `preflight` skips a sha already in `documents.json` before any model call (§5). But the wasteful part remained upstream: a re-dropped file was still re-OCR'd, queued, then quietly skipped at ingest. Chew now hashes each file first and, if the sha is already ingested / already queued / a repeat within the batch, moves it to `_INCOMING/_SKIPPED/` with a warning instead of OCR'ing it. Chosen over a content-addressed *text cache*: skipping is simpler (a hash + set lookup, no persistent store or version-invalidation), covers the same cases, and is the correct intent — "I already have this document," not "re-OCR it cheaply."

**Tradeoff:** Exact-bytes only — a near-duplicate (re-scan, different export) has a different sha and still chews (the MinHash flag handles that, §3). Files dropped *simultaneously* with identical bytes keep the first and skip the rest, which is right for true duplicates but would also skip a deliberately-reprocessed copy

### D28 — Task-prompt prose lives in `prompts/*.md` templates, in a **separate directory** from record skills

`prompts._text` loads a template (cached) and `_render` substitutes `{{token}}` placeholders; the builders still own all data assembly and conditionals in Python (model copy out of code, logic in code). The load-bearing constraint is the separate directory: `skills_catalog` only ever scans `skills/records/`, so a prompt template never leaks into the classifier index or `watchdog show-skills`. Packaged like the skills `.md` (hatchling bundles non-`.py` files under `src/watchdog`)

**Tradeoff:** Editing a template changes model behavior with no type-checking — prompts are plain-text copy to be reviewed like prose. Whitespace was naturalized for markdown, so the assembled prompt is semantically identical but not byte-identical to the old string literals

### D29 — Stop round-tripping deterministic document fields through the model; stamp them in Python (#140 family)

The extractor was asked to echo back document **identity** (`sha256`, `filename`, `original_path`, `page_count`) — values the pipeline already holds from `preflight` — and to re-derive **provenance** (`source`, `obtained`) by parsing the `.yml` sidecar text it was handed. Both are deterministic: Python knows the identity, and the sidecar is structured YAML. `orchestrate._stamp_document` now sets all six on the extraction right before post-flight (`_sidecar_provenance` parses the sidecar with `yaml.safe_load`, coercing an auto-parsed date back to a string); the schema drops `sha256`/`filename` from `_DOCUMENT.required` (kept optional in `properties` so the stamped dict validates) and the extract/section prompts no longer instruct the model to produce them. The sidecar text still reaches the model as extraction context (its free-form `notes`). Same lever as D24/D25 — derivable data out of paid output — plus a correctness win: `write_vault` keys the vault write on `document.sha256`, so stamping removes a latent desync if the model ever mis-transcribed the 64-char hash. Also dropped the never-read `document_type` from the classify call's schema/prompt (it was discarded; the `document_type` the vault uses is the separate extraction-stage field)

**Tradeoff:** Provenance parsing is now strict YAML rather than the model's lenient reading — a malformed sidecar yields no `source`/`obtained` (returns `{}` on `YAMLError` or a non-mapping) instead of a best-effort salvage; the documented `key: value` sidecar format is unaffected

### D30 — `document_type` is deduped against a per-vault registry (match-or-coin), its slug is derived, and dead/duplicate type fields are dropped (#140 cleanup, stacks on D29)

An audit of the extract schema found three more issues with the document-type fields. (1) **Drift:** `document_type` is a free string the model coined per document with no guidance, so the same instrument came out worded differently across docs — fragmenting the `watchdog status` "Documents by type" tally. Fixed with the same dedup pattern the pipeline already uses for entities: `preflight` collects the distinct types already in `documents.json` (the registry is the vault itself — no new file), passes them as `KNOWN_DOCUMENT_TYPES`, and the prompt instructs reuse-verbatim-or-coin-new. (2) **Redundancy:** the model emitted both `document_type` (descriptive, e.g. "Annual Report") and `morgue_document_type` (the slug, e.g. "annual-report") — the same fact twice; `_stamp_document` now derives `morgue_document_type = slugify(document_type)` and it leaves `EXTRACTION.required`. (3) **Dead field:** `near_duplicate_of` was in the model schema but never prompted and always sourced by Python from the MinHash pass — removed from the model-facing document schema. `title`/`document_type`/`date_of_document` (schema-required or note-rendering, but previously undescribed in the whole-doc prompt) are now documented in `extract_instructions.md`

**Tradeoff:** The known-types list is a `preflight` snapshot, so two genuinely-new types in the **same parallel batch** can still be coined inconsistently (neither is in `documents.json` yet) — the same race the entity layer reconciles post-merge; a type-reconciliation pass is deferred until it bites. The registry only shapes new docs going forward; types coined before this change aren't retroactively normalized

### D31 — Timeline-dedup returns kept **indices**, not echoed event objects; and collapses only pure restatements, never divergent accounts

The same-date dedup step (post-ingest, only on cross-document date collisions) had the model echo back the full kept event objects — re-typing a 64-char `source_sha256`, `page`, `confidence`, and the event text per event, all of which Python already holds. It now returns `keep` (a list of indices); `orchestrate._select_kept` maps them to the **original** objects (deduped, order-preserving) and falls back to keeping **all** events on any unusable response (non-list / out-of-range / empty), so dedup can drop a redundant restatement but never silently wipe a date. The criterion was also tightened: only **pure restatements** (same facts, different words) collapse to the most precise wording; any event adding a material fact, detail, or distinct perspective is kept — including two sources describing the same day differently (e.g. opposing legal teams), which stay as separate rows each attributed to its own `source_sha256`. The model no longer rewrites event text (indices only), so kept events are preserved verbatim

**Tradeoff:** "Merge with attribution" is realized as co-dated, separately-attributed rows, **not** a single fused event — the timeline event model carries one `source_sha256` per row, so a literal one-row merge citing multiple sources would need a schema change (multi-source events) and is deliberately not done; it would also blur the clean per-document provenance the vault relies on. Dedup correctness still rests on the model's same-occurrence judgment (unchanged risk from before), now biased toward keeping

### D32 — Advisory page-coverage warning flags a likely skim

A classic LLM failure is not reading a long document to the end. Extraction can't *enforce* reading, but `key_facts` carry a `page`, so `orchestrate._coverage_warning` deterministically flags a probable skim: for a document of ≥ `_COVERAGE_MIN_PAGES` (8) pages, if no fact cites anything past roughly the first half (`_COVERAGE_TAIL_FRACTION`), it prints a yellow `⚠` after the `OK` line and logs a `WARN`. Pairs with the read-every-page instruction now in `extract_instructions.md` (the prompt nudges; this catches when the nudge failed). Purely advisory — never fails the extraction, since a document whose material genuinely sits up front trips it too. Real *enforcement* of coverage remains structural (large-PDF sectioning, the `chunk_size` knob — §6), not this heuristic

**Tradeoff:** Heuristic with false positives (front-loaded-but-complete docs) and false negatives (a model that fabricates a single back-page fact passes); it is a review prompt, not a guarantee. A doc with no page anchors at all can't be assessed and is silently skipped

### D33 — Briefing reads figures + chronology from `key_facts`; the scratchpad is slimmed to forward-looking leads (#150)

The per-document scratchpad — free-text notes the model wrote to feed the end-of-batch briefing — was ~19% of one doc's extraction output, and ~half of it (its "Key figures" and "Chronological" sections) hand-retyped facts the model had already emitted structurally in `key_facts`, paying to state the same facts twice as prose. The briefing couldn't simply drop those sections, because it **only read the scratchpads** and was never handed `key_facts`. So this is a swap, not a trim: `_compact_result` now carries a `key_facts` projection (`_briefing_facts` → fact + date only, dropping page/confidence/entities/quote as narrative noise), the briefing prompt draws figures and chronology from those structured facts, and the scratchpad/`observations` instructions are slimmed to *forward-looking* notes only (leads, open questions, cross-document threads) with figures/chronology/contradictions explicitly excluded. Stacks on D26 — same principle of not emitting prose that duplicates structured data. The compact result is also the standalone-`finalize` input (D23), so putting `key_facts` there keeps an interrupted batch's briefing fully reconstructable from disk

**Tradeoff:** Briefing input gains the (deduped) `key_facts` and loses the scratchpads' restated figures — roughly a wash on tokens, a net output cut at extraction. "Leads only" is a softer instruction than a structured field, so thin or empty scratchpads are possible (an empty scratchpad just yields no notes file for that doc); contradictions still reach the briefing via the separate `contradiction_flags`, not the scratchpad

### D34 — `confidence` (4-level) → `basis` (`stated`/`inferred`) — provenance, not graded certainty (#143)

An audit of the 4-level `confidence` field (D25) found ~99% of values were `high`, raising the question of whether the field carried signal. The diagnosis: the scale conflated two axes and overpromised on a third. (1) `high`/`medium`/`low` claimed to grade *inference depth* (none / one step / multi-step), but there's no evidence the model discriminates one-step from multi-step, and the journalist's action is identical for any inferred fact — *verify it*. (2) `disputed` is not a confidence level at all but a *cross-document conflict* signal, fully redundant with the `[!contradiction]` mechanism (and never emitted in the sample). So the field collapses to the one distinction that is both real and decision-relevant: did the document **state** this, or did the model **infer** it? The field is renamed `basis` with enum `stated`/`inferred`, keeping D25's omit-default (absent ⇒ `stated`, the overwhelming default — so the model emits `basis` only for the rare `inferred` exception). `disputed` leaves the field entirely (conflicts live only in `[!contradiction]`); the contradiction gate is retightened from "both sides high or medium" to "both sides stated". Renderers now show only the marked exception — `*(inferred)*` — across key_facts, per-entity claims, timeline events, and roles (fixing a D25-era inconsistency where three of four renderers printed `confidence: high` on every line, burying the signal the design meant to surface). A binary keyed on "is this literally on the page?" is also more honestly calibrated than a self-rated scale — a sharper, checkable question is harder to reflexively max out

**Tradeoff:** Loses any ability to grade inference *depth* (a deep chain reads the same as a one-step guess) — judged not worth keeping, since page cite + quote let the reader gauge that and the reader's action doesn't change with depth. Pre-production, so no migration: notes written under the old scale keep their `confidence: <level>` text until the entity/document is next extracted. Absent ⇒ `stated` is still the optimistic direction — a forgotten mark reads as directly-stated — but the question "is it on the page?" is more concrete than "rate your confidence," which should reduce silent misses

### D35 — Watch-word alerts are a deterministic post-ingest text scan, no model (#165)

A user wants to be alerted when a newly-ingested document mentions a tracked name/company/address/term. This is done entirely in deterministic Python — no model call — because the scannable material already exists for free: D26 retains each document's full text as a page-aligned morgue `<stem>.md` (with `<!-- PAGE N -->` markers). `pipeline/watchlist.scan` reads the vault-root `watchlist.md` (one term per line; `#` comments; literal terms matched case-insensitively on word boundaries via lookarounds so leading/trailing punctuation still matches; a `/.../`-wrapped line is a regex), greps this run's `ok` documents, and records term + document + page (resolved from the nearest preceding marker) + a bolded context snippet. Each hit is *annotated* — not gated — with the matching registry entity when `m.group(0)` resolves against `manifest.json` (name/aliases), so the text scan stays complete while gaining entity links where available. It runs as step 4 of `_post_ingest` (after the morgue text and registry both exist), writes `briefings/alerts-<date>.md` (dated, append-per-run, matching the `surface-<date>` convention) and prints a console summary; an empty/absent `watchlist.md` is a silent no-op. Chosen over (a) entity-only matching (recall gaps — misses terms the model never surfaced as an entity) and (b) a chew-time pre-scan (would predate the registry, losing annotation)

**Tradeoff:** Matches only what survived chew/OCR into the morgue text, and a term not extracted as an entity gets no link (by design — completeness comes from the text layer). Scans only this run's documents; retro-scanning the existing vault after editing the list (a `watchdog watch scan` command) is deferred until wanted. Per-document matches for one term are capped at 50 to bound a too-broad regex

### D36 — Per-stage **reasoning-effort** knobs as an output-token cost lever (#163)

The #140 validation run concluded the one remaining cost lever was *output-token volume per doc*, and thinking tokens **bill as output**. Neither backend set any thinking parameter, so the model thought at each backend's default (`high`). `extractor_effort` and `finalizer_effort` (`low`/`medium`/`high`, default `high`) are now config keys — mirroring the per-stage *model* knobs — resolved in `model_client` and mapped to each backend's native control: `output_config.effort` (claude-api, composed into the same dict as the structured-output `format`) and `ClaudeAgentOptions.effort` (claude-agent-sdk). `_resolve_effort` drops the knob on Haiku-tier models (which reject `effort` with a 400 — so the Haiku **classify** stage has no knob) and treats `high` as "no override" (it ≡ the model default), so an unconfigured pipeline sends nothing and behaves exactly as before. The lever lives in `model_client` (the backend-abstraction layer), not the orchestrator, because the available controls are provider-dependent (#125/#137): the config key is an abstract `low`/`medium`/`high` *intent* that other backends can map to their own reasoning-effort parameter (OpenAI `reasoning_effort`, etc.) or ignore. Operates within I4 — explicit knob, stable default, no auto-escalation

**Tradeoff:** The deliverable per the cost-reduction thread is a measured A/B (default vs `extractor_effort=low`/`medium` on a fixed corpus via `scripts/analyze-session`); the knob ships structured and tested, but whether a lower effort actually cuts cost *without* degrading extraction quality is a numbers question to settle on a real run. `xhigh`/`max` (Opus-only) are deliberately not exposed — the lever is for spending *less*, and `low`/`medium`/`high` is the portable set across providers

### D37 — OpenAI-compatible backends (OpenAI, DeepSeek) behind a provider abstraction (#125)

Output tokens are ~88% of extraction cost and non-Claude providers are dramatically cheaper there, so the pipeline needs to route to them. D36 had already moved the *effort* knob to an abstract `low`/`medium`/`high` intent precisely so other providers could slot in; this realizes that. **Abstraction:** (1) a per-provider **effort policy** (`_EFFORT_POLICY`) translates the intent to each provider's native control — Claude `output_config.effort` with `high`≡default and Haiku-rejects, OpenAI `reasoning_effort` on reasoning models only (and `high` is *not* a no-op there), DeepSeek nothing (the reasoner thinks by default; no portable knob) — so the shared `acomplete_json` path never hard-codes one provider's semantics; (2) `_resolve_backend_auth` resolves the key per backend — Claude backends keep the subscription/api-key mode, others read their own stored key (`watchdog auth set openai|deepseek`) independent of the Claude mode, so a user with only an OpenAI/DeepSeek key can run those backends; (3) a single `_openai_complete_async` (httpx Chat Completions, no new SDK dep) serves both providers, selected by base URL via `functools.partial`. Structured output uses portable JSON-object mode + schema-in-prompt + the existing validate/retry shell (full `json_schema` mode isn't universal). `cmd/auth.py` already had a `_PROVIDERS` table, so OpenAI/DeepSeek are additive there. **User-facing selection:** each stage's existing model knob (`extractor_model`/`finalizer_model`/`classifier_model`, config or `--*-model` flag) accepts a `backend:model` form — `claude-api:opus`, `openai:gpt-5-mini`, `deepseek:deepseek-chat` — parsed by `ingest._resolve_stage` into `(backend, model)` and threaded through `orchestrate.run` to each stage's `acomplete_json`; a bare tier (`sonnet`/`opus`/`haiku`) keeps the stage on Claude routed by auth mode. One knob carries both halves so a stage can't be half-configured. Watchdog stays Claude-by-default; other providers are opt-in per stage. **Deferred:** a Claude fallback on invalid non-Claude output, and full pricing fidelity

**Tradeoff:** Quality on dense legal/financial extraction is unproven (the issue's caveat), so the defaults stay on Claude — a non-Claude backend is used only when the user selects it, and there's no automatic fallback if it returns junk after retries (it just fails the doc, like any backend). Pricing is approximate and output-only (DeepSeek's cache-hit input discount isn't modelled; OpenAI rates aren't populated, so cost reports None there). `max_tokens` (not OpenAI's newer `max_completion_tokens`) is sent, so OpenAI *reasoning* models would reject it — fine for DeepSeek and OpenAI chat models. The reasoning-model detection for `reasoning_effort` is a substring heuristic (`gpt-5`/`o1`/`o3`/`o4`/`reasoner`)

### D38 — Corpus search indexes overlapping sub-page **passage windows**, not whole pages; corpus and notes are separate streams; bge query prefix + `+`/`-` arithmetic + advisory threshold (#138)

The per-page index (one vector per page) was too coarse for concept-to-passage search — a page averages many topics into one vector and dilutes a short query. Borrowing Semantra's windowing, `add_document` now splits each page into overlapping word windows (`_WINDOW_SIZE`=128, `_WINDOW_OVERLAP`=16) and stores one vector per window tagged with its page; windows never cross a page boundary, so each passage keeps an exact page cite and the matched window *is* the citable span (no separate highlighting pass). Three smaller fixes ride along: (1) **bge asymmetric retrieval** — short queries are embedded with the model's instruction prefix (`_QUERY_PREFIX`), passages without one, a free recall gain the old symmetric path left on the table; (2) **`+`/`-` associative queries** — `_parse_query` splits a query into positive/negative phrases (a sign counts only after whitespace, so `anti-bribery` stays whole) and the query vector is `Σpos − Σneg`; (3) **separate corpus/notes streams** — `search(scope=)` ranks source passages apart from synthesized notes so prose never dilutes source ranking, surfaced as two sections in `watchdog search`, with `min_score`/`--threshold` to drop weak hits. The model stays `bge-small-en-v1.5` by default but is now an `embed_model` knob: it already beats Semantra's mpnet on retrieval, the real lever was granularity not model size, and the genuinely-stronger 2025 models (Qwen3-Embedding, EmbeddingGemma) aren't in fastembed yet — the knob is the forward door for when they are. Indexing stays deterministic and on-machine (I2)

**Tradeoff:** Word-count windows approximate true tokenization (fastembed has no exposed tokenizer split), so window sizes are roughly, not exactly, 128 tokens. Pre-production, no migration: the old per-page `docs/` vectors are simply re-written as passages on the next chew (and the legacy monolithic-index migration was dropped — its per-page target no longer exists). Cosine still has no universal cutoff, so the threshold is advisory and corpus-tuned, and a `+`/`-` query shifts the score scale lower (judge those by ranking). Changing `embed_model` requires a full re-chew — vectors from two models aren't comparable, and there's no reindex command yet. A window never spans a page break, so a concept split across the page boundary is only captured within each page's own windows

### D39 — `watchdog export` emits the entity graph as Neo4j-import CSV / Cypher, fully deterministically from `entities.json` (#69)

The relationship graph is already structured in the registry (`roles` per entity), so external network analysis (Neo4j, Gephi, NetworkX) needs only a read-and-emit step — no model, no markdown parsing, on the deterministic side of I1. Two non-obvious filters keep the output correct: (1) **only stated-direction roles are emitted** — `write_vault._add_reverse_role` stores a mirrored `is_reverse: true` copy of every relationship so the entity notes can render both sides, but emitting both would double every edge; the export drops reverse roles and keeps the original direction; (2) **edges to never-profiled entities are skipped** — a `target_id` can point at an entity that was named but never got its own registry record, and `neo4j-admin import` rejects an edge whose endpoint isn't in `nodes.csv`. Columns track the current schema, not the issue's 2026-06-10 sketch: per-entity `confidence` no longer exists (D25/#143 replaced it with `basis`), so nodes carry `doc_count` (= `len(appears_in)`) and edges carry `basis`/`date_range`/`source_page`

**Tradeoff:** Graph quality is bounded by ingest-time entity dedup — the same person under name variants that weren't resolved appears as separate nodes, which the export can't fix (an existing vault limitation, not an export bug). The Cypher path uppercases/sanitizes relationship strings into rel-type tokens and backtick-quotes labels, so two source relationships that differ only in punctuation collapse to one type

### D40 — Deterministic whole-vault **lead sweep** (`pipeline/leads.py`): named-but-unprofiled, recurring-isolated, unresolved-contradiction — surfaced post-ingest and via `watchdog leads` (#155, slice 1)

Karpathy's "LLM Wiki" lint surfaces *what's missing* across the whole corpus; the cheapest, highest-value slice of that is pure graph analysis needing no model, so it lives on the deterministic side of I1 alongside the watch-word scan (D-watchlist/#165). Three registry-only signals: (1) **named but never profiled** — a `role.target_id` absent from `entities.json`; because every *extracted* entity becomes a registry record (so document `key_facts`/`entities_extracted` tags can never dangle), the *only* place a name appears without a record is a relationship target the extractor named but didn't profile — making this a precise, low-noise lead rather than a fuzzy coverage heuristic; (2) **recurring but isolated** — `appears_in ≥ 3` with empty `roles`, the registry-only proxy for thin coverage that avoids reading note bodies and gates out the single-mention long tail; (3) **unresolved contradictions** — the per-ingest contradiction flags already on the entity, just *listed* standing. `leads.scan` runs in `_post_ingest` after the watch-word scan, snapshotting `briefings/leads-<date>.md` (overwrite, not append — current-state, not an event log) and printing a `⚠`-line; `watchdog leads` re-runs it on demand and a bare `watchdog` with nothing pending nudges the count. The vault resolver shared with `export` was promoted to `cmd/base._resolve_vault`

**Tradeoff:** Scope is deliberately the *deterministic* slice only: the model-driven whole-vault pieces — a cross-document contradiction re-check (beyond per-batch) and stale/superseded-claim detection — are a separate follow-up (#155 slice 2) that consumes these signals as cheap pre-filters. The "named but never profiled" name falls back to the bare `target_id` slug when no entity ever supplied a `target_name`, so a lead can read as a slug rather than a display name. The isolated threshold (`_ISOLATED_MIN_DOCS = 3`) is a fixed heuristic, not yet a config knob

### D41 — Activate `queries/` as the compounding-exploration log: `/watchdog-query` auto-persists substantive answers, which graduate to `wiki/` threads (#155, slice 3)

Karpathy's sharpest strategic point is that valuable query outputs should be *filed back* so explorations compound instead of re-deriving from scratch each session. The vault already shipped an inert `queries/` directory and `wiki/` threads; slice 3 wires the loop with no new Python — pure skill/template prose, the cheapest #155 slice. `/watchdog-query` now writes synthesised, citation-preserving answers to `queries/<slug>.md` (create or update, never a near-duplicate) and promotes a finding that reaches the wiki threshold (≥2 entities tied by ≥2 documents) to a `wiki/` thread; a standing rule in the vault `CLAUDE.md` makes the reflex session-wide, not only on an explicit `/watchdog-query`. `Write`/`Edit(queries/**)` and `Write`/`Edit(wiki/**)` were added to `_VAULT_PERMISSIONS` so the habit doesn't prompt (wiki writes were previously unauthorised — an existing gap closed here). The `queries/` log and `wiki/` threads form the two-tier compounding substrate (transient explorations vs matured angles)

**Tradeoff:** Persistence is auto with a *substantive-vs-trivial* filter the model judges (skip one-line lookups), not a deterministic gate, so a borderline answer may or may not get a page. Being skill prose, the behaviour can't be unit-tested like the deterministic slices — correctness is a prompt-adherence question, not a pytest. Existing vaults need `watchdog refresh-skills` (for the updated skill + CLAUDE.md) and the new permission lines before the loop runs without prompts. The model-driven whole-vault lint (slice 2) and image-as-evidence (slice 4) remain separate follow-ups

### D42 — Native Obsidian Bases dashboard, replacing Dataview (#184)

The vault dashboard — the live tables of most-mentioned entities, recent documents, people, companies, and possible duplicates — moved from Dataview queries embedded in `index.md` to a native Obsidian **Bases** file (`dashboard.base`); `index.md` is now a thin landing page that links to it. Bases is a *core* Obsidian feature (1.9+), so the dashboard renders with no community-plugin install and no "restricted mode" step — removing the only onboarding friction the dashboard carried, and dropping a third-party dependency. The scaffold writes `dashboard.base` deterministically at `watchdog new`, alongside `graph.json`/`app.json`, from the entity/document note frontmatter the pipeline already stamps (I1/I2: no model, no network). Recurrence (an entity's `appears_in` count) is surfaced as a sortable **column** rather than a declarative sort, because Bases has no documented `sort:` key — its tables sort interactively on column click. Two review-oriented views were added that Dataview lacked: single-source entities (`appears_in.length == 1`) and possible duplicates.

**Tradeoff:** loses Dataview's declarative auto-sort — the user clicks a column header to sort, rather than the view opening pre-sorted — and the dashboard now requires Obsidian 1.9+ (Bases predates that only as beta). In exchange the dashboard works out of the box with zero plugin setup. Existing vaults keep their old Dataview `index.md` until re-scaffolded; `watchdog refresh-skills` does not touch vault root files, so migrating an existing vault is a manual drop-in of `dashboard.base` (or living with the Dataview version, which still works if the plugin is installed). Contradiction-status and other note-*body* signals can't be surfaced (Bases queries frontmatter only); those stay with `/watchdog-health` (#188).
