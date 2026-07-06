# Watchdog production review (Fable, 2026-07-05)

**Scope:** correctness, concurrency/locking, failure paths, cross-platform claims,
structural token-cost, runtime efficiency, dead code, tests, and architecture-vs-intent —
per `PROD_REVIEW_PROMPT.md`. **Security is out of scope** (see `SECURITY_REVIEW_PROMPT.md`);
one deferred note at the very end.

**Baseline:** reviewed at `main` @ `3957162`, clean tree. #200 and #216 confirmed merged.
Test suite: **953 passed, 0 failed, 0 skipped, 16.7s** via
`~/.local/pipx/venvs/watchdog-intel/bin/pytest`. The #200 capture tests did **not** skip —
Playwright + Chromium are installed in the venv, so all 17 `test_capture.py` tests actually
ran; the green baseline is honest. Every mutation made during the test audit was reverted
and the suite re-run green (`git diff` shows no source changes).

Findings marked **CONFIRMED** were traced end-to-end (and where practical reproduced);
**PLAUSIBLE** means the failing path is real in the code but I could not complete a repro.

---

## 1. Release blockers

### B1 — Raw timeline files are never consumed: every ingest re-litigates every historical date, and one failed dedup call permanently duplicates timeline events — CONFIRMED (reproduced)

**Where:** `src/watchdog/pipeline/timeline.py:104-132` (`collisions`),
`src/watchdog/pipeline/orchestrate.py:885-909` (`_post_ingest` step 2).
`grep -rn unlink` confirms nothing anywhere deletes a raw `{date}_{sha7}.ndjson` file
(only `abort.run` does, for *failed* docs).

**Current behavior:** `stage_timeline_events` writes one raw file per (date, doc).
`collisions()` promotes raw-only dates to a canonical `{date}.ndjson` — but **leaves the raw
files on disk**. Reproduced in a scratch vault:

```
run 1: collisions() promotes 2024-01-05_abc1234.ndjson → 2024-01-05.ndjson; raw file remains
run 2: collisions() reports 2024-01-05 as a collision — canonical + the same raw file,
       i.e. the identical event twice
```

**Why it's wrong, concretely:**
1. **Unbounded model-call growth (the #216 bug class):** every ingest after the first makes
   one `timeline-dedup` call per historical date that ever had a raw file — on a
   months-old investigation that's dozens-to-hundreds of calls per ingest that should be zero,
   each fed the canonical events plus stale raw duplicates.
2. **Permanent data corruption on a documented failure path:** when a dedup call fails,
   `_post_ingest` falls back to `kept = events` — which at that point is *canonical + raw =
   each event twice* — and writes that back to the canonical file. A rate limit during
   post-ingest is an expected, documented state (README:233); hit one during the collision
   loop and `timeline.md` gains duplicate rows that persist and can compound on later runs.
3. Even on the happy path, the model is asked each run to re-collapse byte-identical
   duplicates — dedup correctness for old dates now rests on the model forever.

**Fix:** delete (or move aside) each raw file once its events are merged into the canonical —
in `collisions()` for the promotion branch, and in `_post_ingest` after a *successful* dedup
write for the collision branch (leave raws in place when the dedup call fails, so nothing is
lost). Add a test asserting a second `collisions()` call returns `[]` and the raw file is gone
— no such test exists today (see §4).

**Tradeoff:** none of substance. The raws' only legitimate reader after promotion is
`abort.run`, which only handles docs that never reached post-flight — those docs' raws should
indeed survive until their doc succeeds, which the "delete only what was merged" rule preserves.
Worth a D-entry: "raw timeline files are consumed at promotion/dedup, not retained."

### B2 — Failed chunks of a large PDF are silently dropped: the document queues `OK` with missing page ranges and nothing ever surfaces it — CONFIRMED

**Where:** `src/watchdog/pipeline/preprocess.py:322-358` (`process_large_pdf`) records
`metadata["failed_chunks"]`; `grep -rn failed_chunks src/ tests/` shows **zero consumers** —
not chew's status line, not ingest, not `/watchdog-health`.

**Current behavior:** a >40-page PDF is chunked; a chunk that times out
(`chunk_timeout`=300s — plausible on a dense scanned chunk) or errors is skipped, the
remaining pages are assembled, and the file settles as green `OK` in chew. The queue JSON's
`page_count` is the *original* total, so even the `_coverage_warning` heuristic is fooled
into thinking the missing tail pages exist. The journalist gets entities and a briefing from
a document silently missing pages 41–80.

**Why it's a blocker:** README:61 promises "400+ page PDFs are split and processed in
parallel; **no truncation**", and trust/provenance is the product. A silent evidence gap in
an investigative tool is worse than a failed file — a failed file gets retried; a gap gets
published around.

**Fix (either is small):** (a) treat any `failed_chunks` as a chew failure — move the file
to `_INCOMING/_FAILED/` with the failed page ranges in the message (retry-friendly, matches
the existing failure vocabulary); or (b) queue it but propagate `failed_chunks` into the
queue JSON, print a yellow warning at chew *and* ingest, and stamp the gap into the document
note. (a) is safer and simpler. **Tradeoff:** (a) makes one bad chunk fail a 400-page file —
acceptable; partial evidence should be an explicit user choice, not a default.

---

## 2. Fix before calling it production

### P1 — The lead sweep's "unresolved contradictions" signal is dead code: the registry never contains a `contradictions` field — CONFIRMED

**Where:** `src/watchdog/pipeline/leads.py:78-81` reads `ent.get("contradictions")` from
`entities.json`; but `write_vault._new_entity` (write_vault.py:370-390) and `_merge_entity`
(:393-422) never write that key — contradictions live only in note bodies
(`## Contradictions`, write_vault.py:806-811). No other writer exists (grep-verified).

**Effect:** the third of the four advertised lead signals (README:240, GETTING_STARTED:230,
D40, D55) can never fire. `test_leads.py:45` masks this by fabricating registry entries
*with* a `contradictions` key — a shape production never produces (see §4, V1).

**Fix:** persist the per-entity contradiction callouts (or just a count) onto the registry
entry in `write_vault.run` when `incoming.get("contradictions")` is non-empty; or make the
sweep read the note body's `## Contradictions` section. Registry persistence is more in
keeping with the "registry-only sweep" design (D40). Needs a matching integration-shaped
test. **Tradeoff:** registry entries grow slightly; a count-only field loses the callout text
(the note keeps it).

### P2 — `watchdog ingest` finalizes on **sonnet** by default while `watchdog finalize` and every doc say **haiku** — CONFIRMED

**Where:** `cmd/ingest.py:204-205` resolves the finalizer stage via
`_resolve_stage(flag, config)` whose `default="sonnet"` (ingest.py:37); `cmd_finalize`
(ingest.py:434-435) passes `default="haiku"`; the config help (`cmd/setup.py:257-258`),
README:491, and INSTALL:196 all document haiku as the default.

**Effect:** on an unconfigured vault, the same pending batch is synthesized/briefed on
Sonnet if the ingest run completes normally, but on Haiku if it's finished by `watchdog
finalize` — inconsistent output quality and ~3× the documented post-ingest cost on the main
path. Violates the spirit of I4 ("explicit knobs with **stable defaults**").

**Fix:** one word — pass `default="haiku"` at ingest.py:205. Also update ARCHITECTURE §13,
which still says "configurable via `watchdog configure`, **default sonnet**" for both models
(see §3). Add a test asserting `cmd_ingest` and `cmd_finalize` resolve the same default
(mutation-provable).

### P3 — Unknown binary files are ingested as mojibake: the `UnicodeDecodeError` fallback is unreachable — CONFIRMED (verified empirically)

**Where:** `src/watchdog/pipeline/preprocess.py:515-518` — the unknown-suffix branch does
`process_direct_text` and falls back to Docling on `UnicodeDecodeError`; but
`process_direct_text` (:129) reads with `errors="replace"`, which **never raises** (verified:
a 1 KB binary blob decodes without exception). So any extension outside the two known sets —
`.doc`, `.xls`, `.zip`, `.webm` (which README:335 *promises* is supported — see §3) — is
decoded as replacement-character soup, has `char_count > 0`, settles as green `OK`, and is
then paid for at extraction as garbage text.

**Fix:** in the unknown-suffix branch only, read with `errors="strict"` (keep `replace` for
the known text suffixes), and let genuinely-binary files route to Docling or fail with a
clear "unsupported format" `ERR`. Also run `is_garbled` on the direct-text result so a
mostly-replacement-char "success" is at least flagged. **Tradeoff:** a text file in an exotic
encoding now takes the Docling path instead of a lossy direct read — strictly better.

### P4 — `watchdog status` counts `_INCOMING/_SKIPPED/` files as pending, sending the user in a loop — CONFIRMED (reproduced)

**Where:** `cmd/base.py:446-457` — `_count_incoming` excludes only `_FAILED`/`_failed`;
chew's `find_files` (preprocess_batch.py:65, `SKIP_DIRS`) excludes both `_FAILED` and
`_SKIPPED`. Reproduced in a scratch vault: after chew skips a duplicate into `_SKIPPED/`,
`watchdog status` says "1 file in _INCOMING/ — run watchdog chew" while `watchdog chew` says
"_INCOMING/ is empty". The count never clears without the user understanding internals —
exactly the golden-path dead end the review brief asks about. Same wrong count feeds
`watchdog list`'s "To chew" column and the bare-`watchdog` guided flow's messaging.

**Fix:** add `_SKIPPED`/`_skipped` to the exclusion in `_count_incoming` (one line), plus a
test. Consider a separate dim "N skipped files in `_INCOMING/_SKIPPED/`" status line so the
files aren't invisible either.

### P5 — Every new vault's `CLAUDE.md` instructs Claude to run the removed `/watchdog-ingest` skill — CONFIRMED

**Where:** `src/watchdog/templates/vault/CLAUDE.md:3` — "check `.watchdog/ingest-state.json`
— if it exists, files are queued and ready to extract; run `/watchdog-ingest` before doing
anything else." The skill no longer exists (`src/watchdog/skills/` has no
`watchdog-ingest.md`; D18 removed it). `ingest-state.json` exists for the whole duration of
any running ingest and survives a killed one, so an investigation session opened during or
after an interrupted ingest is *directed, as its first standing instruction, to run a
command that doesn't exist*.

**Fix:** rewrite the instruction ("if `.watchdog/queue/` has files, tell the user to run
`watchdog ingest` in their terminal") and refresh via `watchdog refresh-skills`… except
refresh-skills doesn't touch vault-root files (D42 tradeoff) — verify whether it updates
`.claude/`/CLAUDE.md and say so in the fix. This is one of a family of stale D18-era strings
on the product surface — the rest are inventoried in §3 (items D1–D8) — but this one changes
*Claude's* behavior, not just the user's reading, hence a fix-before item.

### P6 — Lock acquisition is check-then-write everywhere; chew takes no mutual exclusion at all — CONFIRMED (code-traced)

**Where:**
- Ingest run-lock: `pipeline/ingest_setup.py:48-60` checks `lock_file.exists()` then
  `write_text` (:99) — two `watchdog ingest` processes racing the window both proceed. The
  docstring at :40-43 explicitly claims `force_lock` gives mutual exclusion for concurrent
  batch collection (#214); the primitive doesn't deliver it. Also, a lock file whose
  `started_at` line is malformed is deleted regardless of age (:58-60).
- Finalize lock: `cmd/ingest.py:445-450` — same check-then-write.
- **Chew:** `preprocess_batch.py:309-314` writes `.chew-lock` **without checking whether one
  exists**. Two chews can run concurrently on one vault — `watchdog watch` plus a manual
  `watchdog chew` is a realistic collision (watch doesn't check locks either,
  `cmd/vault.py:787-833`). The failure shape is messy-but-mostly-recoverable (racing
  staging renames, double near-dup computes), but the lock file exists purely to block
  `rename`/`move` — it doesn't protect chew from chew.
- Registry `.write-lock` (`write_vault.py:299-310`): correct under `flock`… on POSIX. See P7.

**Why it matters at this tier and not as a blocker:** all writes inside one ingest are
serialized in-process (single asyncio thread), so the common case is safe; these races need
two concurrent invocations. But the tool *documents* concurrent-adjacent workflows (watch
mode; "re-run ingest later to collect the batch"), and the mutation test in §4 shows nothing
would catch a regression here.

**Fix:** use `os.open(lock, O_CREAT|O_EXCL)` (atomic on all platforms) for both run-locks,
with the existing staleness logic on the failure branch; make chew check-and-refuse (or
queue behind) an existing fresh `.chew-lock`. **Tradeoff:** none; ~15 lines total.

### P7 — Windows: the registry write-lock is a silent no-op, and README's claims need scoping — CONFIRMED (code-traced; untested platform)

**Where:** `write_vault.py:69-73` — `fcntl` import fails on Windows, `_HAS_FLOCK = False`,
and `_registry_lock` degrades to open-and-yield. README:642 claims "Vault writes are
file-locked … so the concurrent document workers serialize safely"; ARCHITECTURE §4 likewise.
On Windows that sentence is false — it's saved only by the D18 fact that document workers
share one process. Cross-*process* writers (a concurrent `merge-entities`, `reindex`, or a
second ingest given P6) have no serialization on Windows.

Related Windows facts checked mechanically:
- `loop.add_signal_handler(SIGINT)` raises `NotImplementedError` on the Proactor loop —
  caught (orchestrate.py:1150-1155), falls back to `cmd_ingest`'s `except KeyboardInterrupt`
  (ingest.py:339-345). Recoverable (resume works), but Ctrl+C can land mid-`write_vault`
  rather than the graceful "finish current writes" path — the message printed on macOS
  ("finishing current writes, then stopping") is not what Windows gets.
- Setup: `_detect_shell` reads `$SHELL` — unset on Windows, so tab completion is silently
  skipped; README:147 says setup enables completion "automatically" without a Windows caveat.
- INSTALL's Windows prerequisites omit Tesseract entirely while `setup` **hard-blocks** on it
  (`setup_cmd.py:27-33`) — a Windows user following INSTALL step 4 cannot complete step 6.
  (Also in §3.)
- ANSI escapes (colours + `LiveRegion` cursor codes, `cmd/live.py`) are emitted
  unconditionally — fine on Windows Terminal, garbage on legacy conhost.

**Fix:** per the brief, "fix it or fix the README." Minimum honest fix: implement the lock
via `msvcrt.locking` on Windows (small), make the Ctrl+C copy accurate, add Tesseract to
INSTALL's Windows section, and downgrade README "Requirements" to say Windows support is
untested/best-effort until someone runs the suite there. Testing has been macOS-only; the
README already admits this in Alpha limitations (:602) but the Requirements section (:124)
reads as a first-class promise.

### P8 — A failed document can leave partial vault writes behind; D15/abort's "no partial writes" claim is stale — PLAUSIBLE (path traced, no repro)

**Where:** `write_vault.run` writes entity notes, appends entity fragments, and updates the
embed/FTS indexes (steps 3–4, write_vault.py:774-853) *before* atomically persisting the
registries (step 5, :855-876). `postflight.run` catches any exception from it
(postflight.py:158-169) and returns errors; the orchestrator then either retries (repair) or
quarantines via `_fail` → `abort.run`. Two consequences:

1. **Failure after partial write:** an exception mid-`write_vault` (e.g. `OSError` on a note
   write, or a corrupt `entity-fragments/_queue.json` raising in `_record_entity_fragment`,
   which is *not* wrapped) leaves rewritten entity notes citing a `documents/<slug>` note that
   was never written, appended fragments, and updated search indexes — with the registries
   untouched. A retry heals it (full rewrite); choosing **discard** at the next ingest's
   pending prompt does not — phantom claims stay in notes and the search index.
   `abort.py`'s docstring (:10-16, "This never touches the vault registry… the source file
   stays in `_INCOMING/`") describes the pre-D18 world: the source is actually in
   `.watchdog/staging/<sha>/`, and post-flight *can* have half-run.
2. **Repair retry double-appends fragments:** if attempt 1 fails inside `write_vault` after
   some entities were written (fragments appended), the repair retry re-runs `write_vault`
   and `_record_entity_fragment` appends the same claims again — synthesis then sees
   duplicated evidence for those entities.

**Fix:** cheapest honest version — write fragments and index updates *after* the registries
persist (they're derived data), and truncate/replace rather than append a doc's fragment
contribution on rewrite (key the append by sha, or clear `<eid>.md` entries for this sha
first). Update abort.py's docstring either way. **Tradeoff:** reordering inside the lock is
low-risk; per-sha fragment keying is a small format change to a run-scoped temp file.

### P9 — Sectioned documents: later sections' `document.summary` is paid for and discarded; the biggest documents get a summary of section 1 only — CONFIRMED

**Where:** the section prompt explicitly asks later sections for
"document.key_facts + **document.summary** for this section only" (prompts.py:115-116;
`summary` is in the SECTION schema), but `merge.merge_extractions` keeps only the **first**
non-empty section's document dict (`merge.py:69`) — every later section's summary is paid
output thrown away, and the merged document note's `## Summary` describes only the first
~60K tokens of a 400-page filing.

**Fix:** either stop asking later sections for a summary (cheapest — cut the instruction and
schema field for non-first sections), or concatenate per-section summaries into the merged
summary. Given D26's "extraction indexes, doesn't restate" direction, dropping the ask is
the right move. Structural token-cost item as much as a quality one — this call-site detail
postdates `FABLE_COST_REVIEW.md` and was never assessed; #217's telemetry won't see it
because the waste is inside an otherwise-legitimate call.

### P10 — `watchdog watch` chews files that are still being copied — PLAUSIBLE

**Where:** `cmd/vault.py:809-830` — a 3s poll detects any new path and immediately chews it.
A multi-hundred-MB PDF copied in via Finder/network share is detected mid-copy: sha256 of a
partial file, OCR of truncated bytes → `ERR` (file then *renamed into `_FAILED/` mid-copy*)
or, worse, a truncated-but-valid PDF ingested as complete. Classic watcher gap.

**Fix:** before chewing, require the file's size to be stable across two polls (or mtime
older than one interval). ~6 lines. **Tradeoff:** adds one poll interval of latency.

### P11 — Model-emitted `morgue_entity_id` and `key_facts.date` are used raw in filesystem paths — PLAUSIBLE

`morgue_entity_id` (post-flight requires non-empty, postflight.py:54-55, but no shape check)
becomes a morgue path segment (`write_vault.py:717-721`), and each `key_facts.date` string
becomes a timeline **filename** (`timeline.py:97`). A model emitting `"Acme Corp"`,
`"2024/03"`, or a stray `..` produces broken morgue layout/wikilinks or a nested/failed
timeline write (the latter caught by post-flight's warning wrapper, so it's silent event
loss). The prompts instruct kebab-case ids and ISO dates and models comply in practice —
but nothing deterministic enforces it. **Fix:** `slugify()` the morgue id at
`_stamp_document` (same treatment `morgue_document_type` already gets) and validate
`date` against `^\d{4}(-\d{2}(-\d{2})?)?$` in post-flight, dropping/flagging nonconforming
dates. The *traversal* aspect of the same surface is deferred to the security review.

### P12 — Structural token-cost: the synthesis bundle is the one remaining input that grows without bound as an investigation ages — CONFIRMED (structural)

Inventory of all metered call sites (orchestrate.py routes everything through `_call_model`;
plus `batch_extract.submit`): `classify`, `extract`, `extract-section`, batch repair,
`entity-synthesis`, `timeline-dedup`, `timeline-precision`, `briefing`. Verified bounded per
batch **except**:

- `timeline-dedup` call *count* grows with investigation age — that's B1; fixing B1 restores
  the intended zero-calls-when-nothing-collides property.
- **`entity-synthesis`** (`synthesis_bundle.build_bundle`, synthesis_bundle.py:62-75) packs,
  for every touched recurring entity, its full `current_analysis` — an append-only,
  uncapped ledger. A hub entity touched by most documents ships its entire history into
  every batch's synthesis call; D17's tradeoff note ("a very large batch could need bundle
  splitting — not yet implemented") predates D26's append-only analysis and understates the
  growth. Same class as the pre-flight digest that D60/#216 bounded, and the per-candidate
  caps deferred to **#241** — recommend #241's telemetry-driven sizing explicitly include
  the synthesis bundle, not just pre-flight. No new unbounded inputs otherwise: pre-flight
  digest is D60-mitigated with #241 open (not re-reported), the brief/instructions/skill are
  constant, and the briefing input is batch-scoped.
- `timeline-precision` (D63) postdates `FABLE_COST_REVIEW.md` and was never assessed there:
  it is correctly gated (one call per month mixing precisions, usually zero) and already
  attributes usage via `detail=month` — nothing to fix; flagged so #217's measurement plan
  knows it exists.
- I1's "don't re-send what Python knows" holds at every site checked (indices-only dedup
  contracts, stamped identity/provenance, id-only roles). The one violation is P9.

---

## 3. Doc/code mismatch inventory (feeds #166)

Stale-`/watchdog-ingest` family (all CONFIRMED, all product surface):

- [ ] **D1** `templates/vault/CLAUDE.md:3` — directs Claude to run `/watchdog-ingest` (see P5).
- [ ] **D2** `cmd/base.py:601` — `--help` banner: "ingest — Set up extraction session and open in Claude Code".
- [ ] **D3** `cmd/vault.py:415` — `watchdog new` next steps: "Run watchdog ingest to set up extraction and open Claude Code".
- [ ] **D4** `cmd/vault.py:984` — `watchdog status`: "N files chewed and waiting for `/watchdog-ingest`".
- [ ] **D5** `cmd/vault.py:826` — `watchdog watch` macOS notification: "ready for /watchdog-ingest".
- [ ] **D6** `cmd/vault.py:943` — `status` on registry-less vault: "open this vault in Claude Code to begin ingesting".
- [ ] **D7** `INSTALL.md:156` — "Ingestion happens in two steps: chewing in your terminal, then **extraction in Claude Code**" (directly contradicted by :194 four lines later).
- [ ] **D8** `GETTING_STARTED.md:377` — Subsequent sessions step 3: "`watchdog ingest` — opens Claude Code with extraction pre-loaded".

Docs vs code:

- [ ] **D9** `README.md:335` — `.webm` listed as supported; not in `DOCLING_SUFFIXES` (preprocess.py:61-71) → currently ingests as mojibake (P3). Fix code or table.
- [ ] **D10** Supported-types table omissions the code *does* support: `.bmp`, `.webp`, `.aac`, `.ogg`, `.flac`, `.avi`, `.mov`, `.vtt`, `.pptx`, `.xml`, `.csv`, `.adoc`, `.tex`. Decide the promised set and align both directions.
- [ ] **D11** `README.md:491` + `INSTALL.md:196` + config help say finalizer default **haiku**; `watchdog ingest` actually defaults **sonnet** (P2). `GETTING_STARTED.md:175` says Sonnet for post-ingest — three docs, two answers, code has a third state.
- [ ] **D12** `ARCHITECTURE.md` §13 first bullet: "Models … default sonnet" for extractor *and* finalizer; and "classification runs on haiku" without naming its knob — rewrite to match the real per-stage defaults.
- [ ] **D13** Project `CLAUDE.md` (repo root): "ARCHITECTURE.md §14's Invariants (I1–I4)" — they're **§15, I1–I5** (the DECISIONS.md header was fixed post-cost-review; CLAUDE.md wasn't).
- [ ] **D14** `ARCHITECTURE.md` §12: `ingest-state.json  handoff from watchdog ingest to the skill` — no skill exists; the file is written and deleted but never read (see PR1).
- [ ] **D15** `README.md:642` / ARCHITECTURE §4: "vault writes are file-locked" — false on Windows (P7); scope the claim or fix the lock.
- [ ] **D16** `INSTALL.md` Step 4 (Windows) omits Tesseract; `watchdog setup` hard-blocks on it on Windows (setup_cmd.py:27-33).
- [ ] **D17** `INSTALL.md:113` — "It will ask two questions" — setup asks three (projects dir, capture browser, **auth mode** — `setup_auth_interactive`); the step-6 bullet list also omits the auth step.
- [ ] **D18** README vault-structure tree (:290-321) omits `_INCOMING/_SKIPPED/` (which GETTING_STARTED/INSTALL both reference), `queue/_failed/`, `timeline.md`, `.fulltext/`, `.watchdog/research/`, and Registry's `usage-*.json`/`batch-pending.json`. At minimum add `_SKIPPED` and `_failed` — both are user-visible recovery surfaces.
- [ ] **D19** `watchdog timeline` and `watchdog describe` appear in no doc (README command tables, GETTING_STARTED, INSTALL all silent; `timeline` is in the banner, `describe` isn't anywhere user-visible). Document or hide deliberately.
- [ ] **D20** `abort.py` module docstring (:1-17): "the source file stays in `_INCOMING/` (post-flight never moved it)" — sources live in `.watchdog/staging/<sha>/` at ingest time, and post-flight can have partially run (P8). Also "subagent" vocabulary here and in `preflight.py`/`write_vault.py`/`timeline.py` docstrings describes the pre-D18 architecture.
- [ ] **D21** `GETTING_STARTED.md:103` — "Near-duplicate detection is automatic… it will be flagged as a duplicate and skipped" conflates the sha-exact skip (D27, skipped) with MinHash near-dup (flag-only, never skipped). Split the sentence.
- [ ] **D22** `ingest_setup.py` module docstring + `main()` (:1-12, :115-128) — the whole "open Claude Code → /watchdog-ingest" flow, including a user-facing print. `main()` is unreachable from the CLI (not in `_PIPELINE_COMMANDS`/`_INTERNAL_CMDS`) — see PR2.
- [ ] **D23** `base.py:26-30` `_MODEL_IDS` duplicates `model_client._MODEL_IDS` with a *different* haiku id (`claude-haiku-4-5-20251001` vs `claude-haiku-4-5`) — two tables that can drift; one already has.
- [ ] **D24** `synthesis_bundle.py` docstring documents `watchdog build-synthesis-bundle` / `apply-syntheses` commands that are no longer registered, and a `count >= 2` gate that D26 replaced.

Verified-accurate claims worth recording as checked: chew makes no model/network calls
(I2 holds — the only chew-time network surface is nothing; even embedding moved to ingest per
D43); desktop notification is correctly macOS-gated (base.py:511); lazy-import discipline
holds (`watchdog about`/`--help` ≈ 0.2s cold — no docling/fastembed import on interactive
commands); resumability of extraction (queue-file lifecycle + `already_extracted` skip) works
as documented; `merge/finalize/discard` prompt matches D23's description; unlock's 30-minute
staleness matches INSTALL; the cost review's Part-3 flags #2 (hidden section knobs — now
registered + documented), #3 (dead `_TASK_TIERS`), and #4 (pre-D26 pending gate) were all
fixed; prompt-prefix byte-stability is now pinned by tests (test_prompts.py:91,123).

---

## 4. Test gaps and vacuous tests

Mutation protocol: break source, run suite, revert; final `git diff` clean and full suite
re-run green (953/953).

**Mutations that stayed GREEN (gaps proven, not guessed):**

- **G1 — The registry write-lock can be deleted entirely.** Removed the `flock` acquisition
  in `_registry_lock` → **full suite green**. No test exercises cross-process (or even
  simulated) lock contention, lock-file creation, or the Windows `_HAS_FLOCK=False` branch.
  Given P6/P7, at minimum add: a test that `_registry_lock` actually holds `LOCK_EX` (two
  processes via `multiprocessing`), and one pinning atomic run-lock acquisition once P6 lands.
- **G2 — The `kept or events` never-lose-events guard in `_select_kept`
  (orchestrate.py:857) can be deleted** → full suite green. The unusable-`groups` early
  return is tested; the "every group invalid → empty kept" path — the one that would wipe a
  date's canonical events — is not. One test with all-out-of-range indices.

**Mutations that went RED (behaviors genuinely pinned):** canonical promotion write
(timeline.py — 1 test), entity-timeline dedup (write_vault), ingest-lock staleness threshold
(ingest_setup — 3 tests), pre-flight alias floor, `explode_key_facts` fan-out (2 tests),
watchlist word boundaries. The load-bearing deterministic core is real, not decorative.

**Confirmed-vacuous / misleading tests:**

- **V1 — `tests/test_leads.py:45,91-94,140,186` (contradiction-signal tests).** They build
  registry entries with a `"contradictions"` key **that no production code ever writes**
  (P1), so they prove `find_leads` handles a shape that cannot occur — and their green
  status is precisely what let the dead feature ship. **Strengthen:** drive the fixture
  through `write_vault.run` with a contradiction-bearing extraction (or assert in a separate
  test that `write_vault` persists the field), so the contract between writer and sweep is
  what's tested.
- **V2 — `tests/test_near_dup.py:74-80` (`test_shingles_from_text_returns_set`,
  `_empty`).** Assert only `isinstance(result, set)` (the empty-input one asserts nothing
  else) — can't fail short of a crash. **Strengthen** (assert actual shingle content for a
  known input) or delete; the neighbouring jaccard/minhash tests carry the real weight.
- The other flagged weak-assert candidates (`test_watchlist.py:194`,
  `test_preprocess_batch.py:278`) turned out fine on inspection — the `is not None` is
  followed by substantive assertions.

**Named untested behaviors that matter (mutation framing — "what change would no test catch"):**

1. Raw-timeline-file lifecycle: nothing asserts raws are consumed / a second
   `collisions()` returns `[]` — the exact missing test for B1.
2. `failed_chunks` surfacing (B2): no test drops a chunk and asserts the file fails or the
   gap is reported.
3. Crash-recovery midpoints: no test kills between `write_vault` success and queue-file
   unlink (lingering "skipped" queue entry, PR4), or between note writes and registry
   persist (P8's partial-write state), then asserts resume heals.
4. `cmd_ingest`/`cmd_finalize` default parity (P2) — a one-line assert.
5. `_count_incoming` vs `find_files` exclusion parity (P4).
6. Unknown-binary chew path (P3): a `.bin` of random bytes should not queue as OK.
7. Windows branches: `_HAS_FLOCK=False` write path, `add_signal_handler`
   `NotImplementedError` fallback — at least import-level/branch tests, since CI is
   Linux/macOS.
8. `watch` mid-copy quiescence (P10) once fixed.

---

## 5. Post-release

- **PR1 — Delete `ingest-state.json` entirely.** Written by `ingest_setup.run`, deleted by
  `_release_lock` and setup's sweep, read by nothing (grep-verified). Its only remaining
  effect is the P5 template confusion. Goes with removing D14/D22.
- **PR2 — Dead entry points and vestiges:** `ingest_setup.main`; `synthesis_bundle.main` /
  `_main_build` / `_main_apply` (commands not registered); `preprocess.py --vault-path`
  (parsed, threaded from `preprocess_batch.py:214`, used by nothing since D43);
  `registry.cmd_validate_extraction` still validates per-entity `timeline_events`, a shape
  the model stopped emitting at D26; pre-D18 "subagent" docstrings (D20). Flagging, not
  fixing, per the brief.
- **PR3 — Empty `.watchdog/staging/<sha>/` dirs accumulate forever** — one per ingested
  document; `write_vault`'s rmdir loop (write_vault.py:906-913) only prunes under
  `_INCOMING/`. Cosmetic but unbounded; remove the emptied staging dir after the morgue move.
- **PR4 — A queue file for an `already_extracted` doc is never unlinked** (orchestrate.py:451-453
  returns "skipped" without cleanup) — only reachable after a crash in the narrow window
  before `_finish_extraction`'s unlink, but once there, every future ingest re-reports the
  phantom "already extracted — skipping" row forever. Unlink on the skipped path.
- **PR5 — Registry rewrite cost per document.** Each `write_vault.run` parses and re-serializes
  the *entire* `entities.json` + rewrites the full manifest (and reads each touched note file
  four times — notes/summary/analysis/contradictions each re-read it). At ~5K entities
  (~10 MB registry) a 200-doc batch does ~2 GB of JSON churn and re-embeds hub notes once
  per touching document. Nothing hurts at current scale; when it does, batch-scope the
  registry load (read once per run inside the lock, write per doc) and re-embed a note once
  per batch, not per touch. Same family: `preflight.run` re-parses
  entities/manifest/documents per document.
- **PR6 — Chew subprocess-per-file re-pays the docling import** (seconds) for every small
  file; a many-small-files dump spends more time importing than converting. Worker-process
  reuse (or a persistent pool) is the fix when it starts to hurt.
- **PR7 — `watchdog search` loads every passage vector on every query** — fine to roughly
  low-thousands of documents; consider a memory-mapped combined matrix beyond that.
- **PR8 — `setup` prompts crash non-interactively** — `_ask_projects_dir` and
  `_check_playwright` call `input()` without EOF handling (auth setup handles it properly).
  Piped/CI invocation gets a traceback instead of the auth-style graceful skip.
- **PR9 — Duplicate `_MODEL_IDS` tables** (D23) — fold `base.py`'s into `model_client`'s.
- **PR10 — `README.md:9` "Not yet battle-hardened"**: after the above land, revisit the alpha
  framing as part of #166 rather than piecemeal.

---

## Verdict — production readiness

**Not yet — but the distance is short and the shape is right.** The architecture is sound
and honestly documented: the deterministic/model split (I1) is real in the code, not
aspirational; the invariants are enforced where they matter; resumability, quarantine, and
the merge/finalize/discard flow all work as documented; the test suite is 953 genuinely
non-decorative tests (most mutations went red immediately). The golden path on macOS, happy
case, does what the README promises.

What blocks the "production-ready" claim is a small number of defects concentrated exactly
where this tool can least afford them — silent data integrity: **B1** (timeline duplication
on the documented rate-limit path, plus unbounded dedup calls as the vault ages) and **B2**
(silently missing page ranges in large documents, against an explicit "no truncation"
promise). Both are one-afternoon fixes with clear tests. Behind them sits a second tier of
trust bugs — the dead contradictions lead signal (P1), mojibake ingestion of unsupported
binaries including the README-promised `.webm` (P3), the `_SKIPPED` status loop (P4), and
the finalizer-default split (P2) — plus one systematic cleanup: the D18 migration left a
trail of "open Claude Code / run /watchdog-ingest" strings across the banner, `new`,
`status`, `watch`, the vault CLAUDE.md template, INSTALL, and GETTING_STARTED (P5, D1–D8)
that actively misdirects the non-technical journalist the tool is for.

**Shortest credible path:** (1) fix B1 + B2 with their named regression tests; (2) the
one-liners P2 and P4; (3) P1 and P3 with integration-shaped tests (and fix or delist
`.webm`); (4) one sweep PR for the stale-string family + template CLAUDE.md (P5, §3 D1–D8),
which also hands #166 its checklist; (5) atomic lock acquisition (P6) and either a Windows
lock or an honest README (P7). Everything else — P8–P12, the §5 list — can ride behind the
skills review (#68) and the documentation rewrite (#166) without endangering the production
claim.

---

*Deferred to the security review:* model-emitted path segments (`morgue_entity_id`,
timeline `date` filenames — P11's traversal face) and everything in
`SECURITY_REVIEW_PROMPT.md`'s own scope.
