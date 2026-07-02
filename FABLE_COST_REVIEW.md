# Watchdog cost & feature review (Fable, 2026-07-02)

**Scope:** token cost of the `chew`/`ingest` pipeline on a Claude Pro/Max subscription
(the `claude-agent-sdk` backend, the only one that works on subscription auth — §13, D37),
plus feature proposals. The interactive investigation layer is out of scope per the brief.

**What was read:** README.md, ARCHITECTURE.md (§1, §5–§9, §13, §15), the full DECISIONS.md
D-log (D1–D48), `model_client.py`, `pipeline/orchestrate.py`, `prompts.py`, `schemas.py`,
`preflight.py`, `postflight.py`, `section.py`, `merge.py`, `synthesis_bundle.py`,
`finalize_entity.py`, `timeline.py`, all six `prompts/*.md` templates, the record-skill
corpus sizes, `scripts/analyze-session`, and the test suite layout. API facts (caching
pricing, batch discount, model pricing) were verified against the current Anthropic docs,
not recalled.

**Baseline:** #140 validation (2026-06-25): ~$0.0248/page, ~$0.49/doc at API-equivalent
rates, with output tokens ~70% of dollar cost. No per-page *token* baseline exists yet —
producing one is itself recommendation A2.

---

## Measured input anatomy (the numbers everything below uses)

Per **whole-document extraction call** (`prompts.build_extract_prompt`, sent once per doc,
twice if the post-flight repair retry fires):

| Component | Size | Tokens (chars/4) | Varies per… |
|---|---|---|---|
| `prompts/extract_instructions.md` | 7,354 chars | ~1,840 | never |
| Investigation brief (`context.md`, whole file) | varies | typically 500–2,000 | per vault |
| Record skill (`skills/records/*.md`) | 8.4–15.2 KB, median 11.5 KB | ~2,900 | per document type |
| `EXISTING_ENTITIES` (preflight candidates, full digests) | unbounded | grows with vault | per doc × vault size |
| Known document types, sidecar | small | ~50–300 | per doc |
| Document text (Docling markdown + page markers) | ~500–800 tok/page for legal filings | — | per doc |
| EXTRACTION schema JSON, appended by `_agent_complete_async` (model_client.py:262) | 1,898 chars | ~475 | never |

So every extraction call on the subscription backend carries a **~5,200-token static
prefix** (instructions + skill + schema) plus the brief, before any document text. Per
**classification call** (one per doc unless pinned): `classify.md` (~50 tok) + the
in-memory skill index (7,413 chars ≈ 1,850 tok) + up to `classify_pages`=5 pages capped at
24,000 chars (≈6,000 tok) — i.e. up to ~8K tokens per doc on Haiku.

Worked example — a 200-page dump arriving as 20 × 10-page filings, homogeneous type,
unpinned: 20 × ~5.2K = **~104K tokens of byte-identical extraction prefix** plus
20 × ~4–8K = **~80–160K tokens of classification input**, against ~120–160K tokens of
actual document text. **Roughly half the input tokens of a routine batch are repeated
boilerplate or avoidable classification.** That is the headline finding; A1/A4/B1 attack
it directly.

---

## Verified API facts (checked 2026-07-02)

- **No prompt caching anywhere in this repo.** `grep -r cache_control src/ scripts/`
  returns nothing. `_api_complete_async` (model_client.py:277) sends a plain string user
  message with no cache breakpoints; the agent-SDK backend exposes no `cache_control`
  knob to us.
- Cache economics: writes 1.25× (5-min TTL) or 2× (1-h TTL); reads **0.1×**. Minimum
  cacheable prefix is model-dependent: **4,096 tokens on Opus 4.x and Haiku 4.5, 2,048 on
  Sonnet 4.6**. Consequence: the ~5.2K-token extraction prefix caches on Sonnet; the ~2K
  classify prefix on Haiku is **below the 4,096 minimum and will silently not cache**.
- **Message Batches API: 50% off all token usage**, supports structured outputs and
  prompt caching, up to 100K requests / 256 MB per batch, most complete within an hour
  (max 24 h). Requires a metered key — not available on subscription auth, as the brief
  assumed.
- Current pricing: Opus 4.8 **$5/$25** per MTok, Sonnet 4.6 $3/$15, Haiku 4.5 $1/$5.
  `model_client._PRICING` (lines 48–52) is **correct**. `scripts/analyze-session`
  `_COSTS` (lines 18–22) is **stale**: Opus at $15/$75 (old ≤4.5 pricing) and Haiku at
  $0.80/$4 (Haiku 3.5-era). The 2026-06-25 baseline ran Sonnet-dominant so the
  $0.0248/page figure is probably fine, but any haiku/opus session analyzed with this
  script is mis-priced. See A3.

---

## Part 1A — High-impact / low-effort

### A1. Wire prompt caching into the `claude-api` backend

**Files:** `model_client.py` (`_api_complete_async`), `pipeline/prompts.py`
(`build_extract_prompt`, `build_section_prompt`).

**Current:** the whole prompt is one string; no `cache_control`; every extraction call
pays full price for the ~5.2K-token static prefix.

**Proposed:** `build_extract_prompt` already orders content cache-friendly (instructions →
brief → skill → *then* per-doc data). Have the builders return a **list of content
blocks** instead of one string: block 1 = instructions + brief (constant per run), block 2
= skill text (constant per document type) with `cache_control: {"type": "ephemeral"}`,
block 3 = everything volatile (EXISTING_ENTITIES, sidecar, document text).
`_api_complete_async` passes the block list as the user message content. The system
prompt stays byte-identical, so one breakpoint after the skill block caches the whole
prefix. Same treatment for the section prompt (instructions + skill repeat per section)
and, at 5-min TTL, sequential sections of one large doc are near-guaranteed hits.

**Effect:** with N docs sharing a skill in a run, prefix cost drops from N× to
1.25× + (N−1)×0.1× ≈ **~87% off the prefix** for N=20. On the 20-doc example that is
~90K input tokens erased. The post-flight repair retry (orchestrate.py:240–256) also
becomes a cache hit on its resent prefix.

**Limits / tradeoff:** does **nothing on the subscription (`claude-agent-sdk`) path** —
the SDK doesn't expose breakpoints (that's B1's job). On Haiku (classify) the prefix is
under the 4,096-token minimum, so don't bother marking it. `ModelResult.usage` already
carries `cache_read_input_tokens` from the API; surface it in A2's report to verify hits
(a zero there means a silent invalidator). No invariant touched.

### A2. Persist per-task token usage — the pipeline currently throws its own telemetry away

**Files:** `model_client.py` (`ModelResult`), `pipeline/orchestrate.py`
(`_simple_extract`, `_extract_sectioned`, `_classify`, `_post_ingest`, `_compact_result`).

**Current:** every backend returns `usage` (input/output/cache token counts) in
`ModelResult`, and the orchestrator **discards it** — only `cost_usd` survives into
`result_<sha>.json` (orchestrate.py:206–220), and on the agent-SDK path even cost is
whatever the SDK reports. There is no way to answer "how many tokens did this ingest
spend, by stage?" without spelunking Claude Code session logs with a script whose pricing
table is stale (A3). The metric the user actually budgets — session tokens — is invisible.

**Proposed:** accumulate `{task, model, backend, input_tokens, output_tokens,
cache_read, cache_write, attempts}` per call; write a per-run
`.watchdog/Registry/usage-<ts>.json` (or extend `ingest.log`) and print a one-line
summary at the end of `watchdog ingest` (`N docs · X in / Y out tokens · $Z est`). This
is the **prerequisite for every measurement this review calls for** (B3's effort A/B,
B2's entity-digest sizing, C1–C3) and turns the D36 tradeoff note ("a numbers question to
settle on a real run") into something settleable natively.

**Tradeoff:** none of substance — one more small file per run. Deterministic, I1-clean.

### A3. Fix the stale pricing table in `scripts/analyze-session`

**File:** `scripts/analyze-session` lines 18–22. Opus 4.8 is $5/$25 (script says
$15/$75); Haiku 4.5 is $1/$5 (script says $0.80/$4). Sonnet is correct. Until fixed, any
before/after comparison involving haiku (classification-heavy sessions) or opus is wrong —
haiku sessions under-read by 20%, opus sessions over-read 3×. Align with
`model_client._PRICING`, or better, have the script import it so there is one table.

### A4. Kill redundant classification on homogeneous batches — nudge first, then opt-in auto-pin

**Files:** `pipeline/orchestrate.py` (`run`, `_extract_document`), `cmd/ingest.py`,
README config table.

**Current:** one Haiku call per document (up to ~8K input tokens each) unless `--skill` /
`default_skill` pins (§6, D21). For the routine case this tool targets — a dump of 200
pages of the *same* filing type — that's N−1 wasted calls, and on subscription each one
is also a CLI process spawn. The guidance to use `--skill` exists but nothing tells the
user their batch *was* homogeneous.

**Proposed, two steps:**
1. **Zero-risk nudge (do this regardless):** at end of run, if all classified docs got
   the same skill, print: `All 18 documents classified as court-documents — next time run
   watchdog ingest --skill court-documents to skip classification.` One `if` statement.
2. **Opt-in auto-pin** (`auto_pin_skill`, default off, config + `--auto-pin`): within a
   run, after the first K (say 4) classifications agree, pin that skill for the
   remainder and announce it (`· pinned court-documents after 4/4 agreement`). Off by
   default because it changes classification behavior mid-run; announced, so it is not a
   *silent* behavior change. It does not touch I4 (no model/effort escalation — it uses
   the model *less*), but it deserves a D-entry because a mis-pin loads the wrong skill
   for the tail of a mixed batch, exactly the failure mode issue #95 cared about. The
   nudge alone captures most of the value with zero risk; ship the auto-pin only if the
   nudge proves annoying.

**Effect:** on the 20-doc homogeneous example, eliminates ~19 Haiku calls (~80–150K input
tokens on subscription; only cents on metered, but this is a *subscription-tokens* lever
and a latency lever — classification serializes ahead of each extraction).

### A5. Deduplicate the sectioned-extraction carry-forward block

**File:** `pipeline/orchestrate.py` — `_carry_block` (lines 260–267) and
`_extract_sectioned` (line 287: `carry += _carry_block(r.parsed)`).

**Current:** the carry-forward is built by *string concatenation* of per-section blocks.
An entity that appears in every section of a 400-page filing (the debtor, the court) is
re-listed in **every** block, so section k's prompt carries up to k−1 duplicate entity
lines plus every prior section's full `observations` text verbatim. Growth is
quadratic-ish in section count, and it rides in *addition* to the instructions + skill +
EXISTING_ENTITIES already repeated per section.

**Proposed:** keep a dict keyed by entity id, rebuild the carry text fresh each section
(`id | name | type`, one line per unique entity), and carry only the most recent
section's observations (or a capped tail, e.g. last 1,000 chars) — earlier observations
are already preserved in `parts` for the final scratchpad. Deterministic, ~15 lines, directly
testable (there is already `tests/test_orchestrate.py`).

**Tradeoff:** later sections lose access to *early* sections' free-text observations.
Since observations are defined as forward-looking briefing leads (D33), not extraction
context, the loss is minimal; the entity list — the thing the prompt says to reuse — is
preserved exactly and gets *more* reliable (no duplicate-id noise).

### A6. Sectioned documents never see the investigation brief

**Files:** `pipeline/prompts.py` (`build_section_prompt` has no `brief` parameter),
`pipeline/orchestrate.py` (`_extract_sectioned` doesn't pass it).

**Current:** `build_extract_prompt` injects `context.md` ("orient extraction toward
this"); the section prompt doesn't. So precisely the *largest, most investigation-central
documents* are extracted without the journalist's stated intent. This is a quality
inconsistency, not a cost bug — I flag it here because fixing it adds input tokens, and
the right move is to add it **inside the A1 cacheable prefix** (instructions + brief
block) so it costs one cache write per run, not tokens per section. Cheap fix, same PR as
A1 ideally.

---

## Part 1B — High-impact / structural

### B1. Add a `claude-batch` backend: the real answer for bulk extraction economics

**Files:** `model_client.py` (new backend beside `claude-api`), `cmd/ingest.py`
(`_resolve_stage` already parses `backend:model`), docs.

**Current:** D37 built exactly the provider abstraction this needs, but the cheapest
Claude option — the Message Batches API — isn't wired. Verified: 50% off **all** token
usage, supports structured outputs (`output_config.format`) and prompt caching, results
within ~1 hour typically.

**Proposed:** a `claude-batch` backend usable as `watchdog configure extractor_model
claude-batch:sonnet` (metered key required — refuse with a clear error on subscription
mode, same pattern as the existing claude-api guard at model_client.py:406–409).
Implementation sketch: the orchestrator's per-doc concurrency model doesn't fit a batch
round-trip, so the clean integration is one level up — a batch *mode* for the extraction
phase: classify (or pin) everything first, submit all extraction requests as one batch
keyed by sha (`custom_id`), poll (`processing_status` until `ended`), then run
post-flight/write_vault per result as they're collected. Extraction is already
non-interactive and per-document independent, and `watchdog ingest` is already resumable
by design — a submitted-batch id persisted in `.watchdog/` makes even the poll
interruptible. Combined with A1 caching (stackable), bulk extraction lands at roughly
**0.5 × (0.1×-prefix + doc text)** — pennies per document, spending **zero subscription
tokens**.

**Tradeoff:** latency (minutes-to-an-hour, not seconds) — acceptable for a fire-and-forget
bulk phase, wrong for a 2-doc top-up, so it's a config path, not a default. Meaningful new
surface in `model_client` (submit/poll/collect + error mapping per `custom_id`). Fits I4
(explicit knob, no silent routing) and the D37 pattern exactly; needs a D-entry. This is
the recommendation I'd rank #1 for the user's actual problem statement ("a 200-page dump
burns a full session"): the hybrid — **batch-metered extraction, subscription reserved
for interactive investigation** — is precisely what D37's abstraction was built to allow.

### B2. Bound the pre-flight `EXISTING_ENTITIES` digest — the input that grows forever

**File:** `pipeline/preflight.py` (`run`, lines 84–104).

**Current:** every manifest entity whose name/alias substring-matches the document text is
shipped into the extraction prompt with its **full** current summary, its **entire**
`## Analysis` section (synthesized prose, can be many paragraphs for a hub entity), and
**all** of its timeline events and roles. Nothing is capped. In a mature vault, hub
entities (the court, the debtor, a law firm on every filing) match nearly every document,
so extraction input grows superlinearly with investigation age — the per-page token cost
quietly climbs over the life of a case. Short names make it worse: `"Lee"` substring-
matches unrelated text and drags a full digest in.

**Proposed:** cap the digest deterministically — e.g. per candidate: summary (already
short), analysis truncated to ~1,500 chars, timeline to the N most recent events (N≈15),
roles to N≈20; and cap total candidates (rank by name length descending — longer names
are higher-precision matches — or by match count). Make the caps config keys with
generous defaults so power users can widen them. **Measure first** via A2: log
`len(json.dumps(existing_entities))` per doc for a real mature vault before choosing
numbers.

**Tradeoff:** the digest exists so the extractor can dedup entities and verify
contradictions (D6: the extractor is the *sole* contradiction verifier — there is no
downstream pass). Truncating analysis/timeline can miss a contradiction against an old,
truncated claim. That's a real quality cost and why the caps must be generous and
measured, not aggressive. Operates within I1; needs a D-entry recording the recall
tradeoff.

### B3. Actually run the D36 effort A/B — the knob shipped, the measurement never happened

**Files:** none to change first; then possibly `cmd/setup.py` (default) or
`skills_catalog` (per-skill default).

**Current:** `extractor_effort`/`finalizer_effort` default to `high`, which
`_resolve_effort` treats as "send nothing" — i.e. **the cost lever D36 built has never
moved from its no-op position**, and D36's own tradeoff section names the A/B as the
undelivered deliverable. Thinking bills as output; output was ~70% of baseline cost. If
`medium` holds quality, it is plausibly the single largest *dollar* saving available —
bigger than everything in Part 1A combined — and it works on the subscription backend
(the agent SDK maps `effort` natively), unlike A1/B1.

**Proposed protocol:** fix a corpus (the 5-doc #140 validation set + one dense 50-page
filing), extract at `high` and `medium` into two scratch vaults, diff: key_fact count and
overlap, entity/role/contradiction sets, page-coverage warnings, plus a manual read of
disagreements. Cost per condition comes from A2. If `medium` holds: either flip the
config default (one line + docs + D-entry), or — the finer option — add an optional
`effort:` key to record-skill frontmatter (`skills_catalog` already parses frontmatter
for `description:`) so trivially-structured types (vehicle registrations, WHOIS) default
lower while dense types (bankruptcy, financial statements) stay high. Precedence: flag >
config > skill frontmatter > built-in. Per-type defaults fit I4 (explicit, stable,
inspectable via `show-skills`); D-entry either way.

### B4. Micro-batch small same-skill documents into one extraction call *(flagged: revises a §1 principle)*

**Files:** `pipeline/orchestrate.py`, `prompts.py`, `schemas.py`, `postflight.py`.

**Current:** a 2-page filing pays the same ~5.2K-token prefix + preamble as a 60-page one.
Dumps of many tiny documents (exhibit lists, one-page orders, registration printouts) are
the worst case: overhead exceeds content.

**Proposed:** when a run contains multiple docs of the same skill each under a small
threshold (~3K est tokens), extract up to K (~4) of them in **one** call — one prefix,
one schema, output keyed by sha. Post-flight splits per sha and proceeds unchanged.

**Why it's quarantined here:** §1 states "Each document's text lives only in its own
extraction call" — a design principle (not a numbered invariant, but load-bearing:
cross-document contamination of facts/citations is the risk the principle prevents, and
`write_vault` keys everything on one sha per extraction). Doing this requires a decision
entry explicitly narrowing that principle ("…except deliberate micro-batches of ≤K small
same-type documents"), schema changes, and a validation that facts never bleed across
shas (the per-fact `page` + per-doc keying makes bleed detectable). **Do A1/B1 first** —
caching + batch discount remove most of the same overhead without touching the
principle. Pursue B4 only if the many-tiny-docs profile dominates real usage on
subscription (where A1 is unavailable).

### B5. Honest opinion: is under-adoption of D37 routing the biggest available win?

**Partly — but not the way the question implies.** Three claims:

1. **The biggest *engineering* win is B1 + A1 on a metered key,** not switching model
   vendors. DeepSeek-chat is ~11×/13× cheaper than Sonnet per token, but batch-Sonnet
   with a cached prefix already gets extraction to the order of **~$0.005–0.01/page**, at
   which point extraction cost is simply not the problem anymore — and you keep the model
   the entire skill/prompt corpus was tuned against. For a precision-first investigative
   tool, D37's own caveat ("non-Claude backends are unproven on dense legal/financial
   extraction") still holds, and nothing has been built since to *prove* them — there is
   no golden-extraction eval harness (the deferred #106 evaluator). Routing extraction to
   DeepSeek today would be flying blind on the one stage where a silent quality
   regression (a missed contradiction, a dropped alias) is worst.
2. **The under-adopted routing that *is* free money:** the low-risk stages. Classification
   (Haiku, ~$0.008/doc metered) and timeline-dedup (indices in, indices out, D31) tolerate
   any competent model; running them on a metered `claude-api` key instead of the
   subscription costs cents per dump and removes per-doc CLI spawns from the
   subscription session. Recommend documenting this hybrid explicitly in README's Model
   backends section as *the* subscription-preservation recipe: `classifier_model
   claude-api:haiku`, `extractor_model claude-batch:sonnet` (after B1),
   `finalizer_model claude-api:haiku` — subscription only for `/watchdog-query` et al.
3. **The gate to going further (DeepSeek/OpenAI extraction) is measurement, not
   plumbing.** Build the B3 harness once; it then answers effort A/Bs *and* backend A/Bs.
   Until it exists, keep defaults on Claude — the D37 tradeoff was correctly weighed and
   nothing about it has changed.

---

## Part 1C — Speculative / measure first

### C1. Deterministic running-header/footer stripping from extraction input

Legal filings repeat a header/footer on every page (case number, firm name, pagination);
Docling preserves them, so a 200-page filing re-sends the same 1–2 lines ×200
(~2–6K tokens/doc). At chew or preflight, detect lines whose normalized form recurs on
>60% of pages and strip them from the *prompt* text only (morgue `<stem>.md` keeps the
full text — I1/D26 unaffected; the watch-word scan D35 reads the morgue, so no recall
loss there). Risk: a table row or a genuinely repeated material line (a running total)
misclassified as boilerplate. Measure the actual repeated-line share on real corpora
(one-off script over morgue texts) before building; if it's <3% of input, skip.

### C2. `classify_pages` default and excerpt cap

Default 5 pages (≤24K chars ≈ 6K tokens) per classify call is generous for picking 1 of
~35 skills; most document types are identifiable from page 1–2. Halving the default to 2–3
roughly halves classification input. Cheap to test with the B3 harness (classification
accuracy is directly checkable — it's a label). Pure config-default change; per-run flag
already exists.

### C3. Sectioning geometry

`section.py`: threshold 120K est tokens, budget 60K/section, overlap 4K. The overlap
duplicates ~7% of a sectioned doc's text; fine. Two things worth a look once A2 data
exists: (a) the whole-doc path near the threshold sends up to ~120K tokens into one call
whose output is capped at 16K only on `claude-api` (`_TASK_MAX_TOKENS`) — the agent SDK
can't cap output (D19), so dense 100K-token docs on subscription flirt with the
output-overrun → force-section fallback, which *pays for the failed whole-doc attempt
first* (orchestrate.py:386–388). If A2 shows fallbacks firing regularly, lowering the
threshold to ~80K is cheaper than the retry. (b) These three knobs are functional but
**hidden** — see Part 3.

### C4. Don't blind-bump `_MODEL_IDS` to Sonnet 5

`model_client._MODEL_IDS` pins `sonnet → claude-sonnet-4-6`. Sonnet 5 is out (same
sticker price, intro discount through 2026-08) but uses a **new tokenizer ~30% heavier**
— a straight swap raises token counts (and subscription session consumption) even at
equal dollars, and its min cacheable prefix differs. Treat any model bump as a B3-harness
run, not a version chore.

---

## Part 2 — Feature proposals

Each is grounded in a specific gap in the D-log or code; ordered by value-per-effort.

### F1. `watchdog reindex` — rebuild the search index with zero model calls

**Grounding:** D38 tradeoff: "Changing `embed_model` requires a full re-chew… there's no
reindex command yet." D43 moved embedding into `write_vault` *because* the contextual
prefix needs extraction outputs (title/type/entities) — but for an already-ingested vault
those outputs **already exist** in `documents.json`/`entities.json`, and the full text
exists as page-marked morgue `<stem>.md` files (D26). So a deterministic
`watchdog reindex [name]` can rebuild `.embeddings/` — passages with contextual prefixes,
notes, new `embed_model` or `rerank_model` — from disk alone, no OCR re-run, **no model
tokens**. Today the documented path (re-chew + re-ingest) re-pays full extraction for
data the vault already has, which is the single most expensive way imaginable to change
an embedding model. Files: new `cmd` + reuse of `pipeline/embed.py` and the D43 prefix
builder; also unlocks upgrading old vaults to the D43 hybrid path retroactively.

### F2. `watchdog merge-entities <keep-id> <merge-id>` — close the dedup loop the tooling already points at

**Grounding:** three shipped features *detect* duplicate entities and none can *fix* them:
the dashboard's "possible duplicates" view (D42), `/watchdog-health`'s near-duplicate
check, and D39's export tradeoff ("the same person under name variants appears as
separate nodes, which the export can't fix"). A deterministic merge — union aliases/roles/
`appears_in`/timeline refs, remap `target_id`s across the registry, concatenate Analysis
with provenance, redirect the losing note (or leave a stub link), update manifest — is
pure registry surgery, I1-side, and directly improves synthesis (fragments stop
splitting), leads, export, and search. This is the highest-leverage data-quality feature
available and it's blocked only by not existing.

### F3. Retro watch-word scan — `watchdog watchlist scan`

**Grounding:** D35's tradeoff explicitly defers it: "Scans only this run's documents;
retro-scanning the existing vault after editing the list… is deferred until wanted." A
journalist adds a name to `watchlist.md` *because it just became interesting* — the
existing corpus is exactly what they want swept. `watchlist.scan` already takes a results
list; feed it a synthetic all-documents list built from `documents.json` + morgue paths.
Small, deterministic, no model.

### F4. Surface `inferred` facts as a lead class

**Grounding:** D34 defines an inferred fact as "a lead to verify, not a finding," and D40
built the deterministic lead sweep — but none of its three signals include inferred
facts, so the pipeline's own "verify me" markers never reach the leads report. Add a
fourth registry-only signal to `pipeline/leads.py`: entities carrying `basis: inferred`
facts/roles, listed with their claims. Deterministic; consistent with D40's design of
feeding cheap pre-filters to the future model lint (#155 slice 2, which the D-log already
plans — this feeds it).

### F5. Per-ingest cost/usage reporting in `watchdog status` and `log.md`

The user-facing half of A2: once usage is persisted, show tokens/cost per ingest in
`watchdog status` and append to `log.md` ("Ingest — 18 files, 412K in / 96K out, ~$1.87").
For subscription users this is the only way to learn what fraction of a session a given
dump costs — today they find out by hitting the limit mid-batch. (Resumability already
softens that — this makes it predictable.)

---

## Part 3 — Other flags

1. **Stale cross-reference:** DECISIONS.md's header points at "Invariants in
   ARCHITECTURE.md **§14**" — they live in **§15** (§14 is web research). One-word fix;
   worth doing since both files are the canonical orientation path.
2. **Hidden, undocumented config keys:** `section_token_threshold`,
   `section_token_budget`, `section_overlap_tokens` are read from `config.json`
   (`pipeline/section.py:47–49`, and threshold is even echoed by `ingest_setup.py:104`)
   but are absent from `_CONFIGURE_KEYS` (`cmd/setup.py`) and the README table. Either
   register + document them (CLAUDE.md's own docs rule requires it) or make them
   constants; a half-hidden knob is the worst of both.
3. **Vestigial dead config in `model_client.py`:** `_TASK_TIERS` / `_TASK_BACKENDS`
   (lines 55–56) are empty dicts, never populated ("populated as tasks are defined,
   Workstream 3" — that workstream landed elsewhere). Harmless, but they imply a per-task
   routing layer that doesn't exist; delete or use.
4. **`pending_finalization` uses the pre-D26 gate:** `orchestrate.py:601–613` estimates
   pending entities via the fragment queue's `count >= 2`, but D26 moved the synthesis
   gate to registry `appears_in ≥ 2` (§8 notes `count` is now just a touched-set marker).
   Display-only, but the "N entities" shown for an interrupted batch can be wrong in both
   directions.
5. **Output-token discipline status (asked by the brief):** `key_facts` is deliberately
   unbounded ("materiality-driven, no fixed count," D26) and per-fact fields are already
   omit-default (D25/D34) — reasonable. The runaway guard (D15) and force-sectioning
   (D19) are **correctness fallbacks, not cost controls**: D19 *adds* cost (one paid
   failed attempt) when it fires. The only true output cap is claude-api's
   `max_tokens=16000` (`_TASK_MAX_TOKENS`); the subscription backend has none by SDK
   limitation — one more reason the bulk path belongs on claude-api/claude-batch (B1).
   The real remaining output lever is effort (B3).
6. **Test coverage of cost paths:** good bones — `test_model_client.py` (28 tests),
   `test_prompts.py`, `test_orchestrate.py`, `test_section.py` exist. Gaps to fill
   alongside the work above: no test pins the *size/stability* of the static prompt
   prefix (a regression that bloats `extract_instructions.md` or accidentally injects a
   timestamp — the classic silent cache invalidator — would pass CI today); nothing tests
   carry-forward growth (A5 should land with one); A1 needs a byte-identical-prefix test
   across two builder calls, which is exactly the property caching depends on.
7. **`_agent_complete_async` appends the JSON schema to every prompt** (~475 tokens/call,
   model_client.py:262) because the agent SDK lacks structured outputs. Unavoidable
   there, but it's another ~10K tokens per 20-doc run that the claude-api backend doesn't
   pay — one more small weight on the metered side of the scale.

---

## Suggested sequencing

1. **A2 + A3** (usage telemetry, fix the analyzer) — everything else needs the ruler.
2. **A4-nudge, A5, A6** — small, safe, immediate.
3. **A1** (caching) and **B1** (batch backend) — the structural cost floor for bulk
   ingest on a metered key; document the hybrid recipe (B5.2) in README.
4. **B3** (effort A/B harness) — then decide `medium` default and/or per-skill efforts;
   the same harness later gates any backend or model-generation change (C4, B5.3).
5. **B2** (entity-digest caps) once A2 shows how bad the growth is on a mature vault.
6. Features: **F1** and **F2** first — both unblock existing shipped surfaces.
