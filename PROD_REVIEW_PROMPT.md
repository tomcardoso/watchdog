You are reviewing the codebase at the current working directory — a CLI tool called
`watchdog` (package name `watchdog-intel`) that OCRs and extracts structured intelligence
(entities, timelines, contradictions, briefings) from investigative-journalism document
dumps into an Obsidian vault. It is currently alpha; this review is the first of four
gates before it's declared production-ready (the others, in order: human review of the
domain skills per issue #68, a full documentation rewrite per issue #166, then GitHub
Pages docs/promo — none of those are your job, but the review should feed them).

## Read, in this order, before forming any opinion

1. `README.md`, `GETTING_STARTED.md`, `INSTALL.md` — these are the tool's *promises*.
   Treat every claim in them (supported platforms, resumability, supported file types,
   the local-first "documents never leave your machine during chew" boundary as a
   *correctness* check — not a security audit) as an assertion to verify against the code,
   not as background. (Security posture proper is out of scope — see the note below and
   `SECURITY_REVIEW_PROMPT.md`.)
2. `ARCHITECTURE.md` — the current-state design, especially §1 (design principles) and
   §15 (Invariants I1–I5, the governing rules). Any finding that amounts to "the code
   violates an invariant" is automatically high priority; any recommendation that would
   *change* an invariant must say so explicitly and argue for superseding it.
3. `DECISIONS.md` — the full D-log (numbered through D60 at this writing; read to the
   end, newest-last). **Do not re-propose something already decided and shipped.** If
   you think a past decision should be revisited, cite the D-number and explain why the
   original tradeoff no longer holds.
4. `FABLE_COST_REVIEW.md` at the repo root — a prior review focused on *model-token*
   cost. Do not re-report its findings; you may reference them. Token cost is out of
   scope for you except where a cost issue is also a correctness issue. Runtime
   efficiency (CPU, I/O, latency, scaling) is **in** scope — see below.
5. `gh issue list --limit 100` — a handful of issues are open and will be addressed
   soon. Don't re-report anything already tracked; if a finding relates to an open
   issue, cite the issue number instead. Before starting, confirm you are on latest
   `main` with a clean tree — in particular #200 (rendered web capture) and #216
   (bounded pre-flight digest) are expected to have merged; if they haven't, stop and
   say so rather than reviewing code about to be replaced.
6. Then the code: everything under `src/watchdog/` (pipeline, cmd, cli.py,
   model_client.py, auth, skills_catalog, setup), `tests/`, `pyproject.toml`,
   `.github/workflows/`. The *content* of `src/watchdog/skills/records/*.md` is
   explicitly out of scope (issue #68 covers it — a human domain review); the
   *machinery* that loads/indexes/pins them is in scope.

## The two questions

**1. Is it correct?** The mechanical layer:

- **Bugs.** Real ones, with a concrete failure scenario (inputs/state → wrong behavior).
  Distinguish CONFIRMED (you traced the path and are sure) from PLAUSIBLE (needs a repro
  you couldn't complete). No speculative "this could theoretically…" findings.
- **Concurrency and locking.** This pipeline is deliberately parallel with serialized
  writes: asyncio semaphore over document workers, `flock` on
  `.watchdog/Registry/.write-lock`, an ingest run-lock with 30-min staleness, parallel
  chew workers and chunk subprocesses, Ctrl+C/SIGINT handling mid-batch, and a resumable
  queue. Audit the whole surface: TOCTOU windows, lock staleness races, what happens if
  the process dies *inside* `write_vault`, whether the entity-id reconciliation for
  concurrent workers actually closes the race it claims to (D18, §5), whether `finalize`
  re-runs are as idempotent as D23 asserts.
- **Failure paths.** Interrupted chew, rate-limit mid-extraction, rate-limit mid-
  finalize, a doc failing after partial vault writes, `requeue`, the `_failed/`
  quarantine, `merge/finalize/discard` prompts for pending batches. Trace each to the
  actual code; the docs promise clean resume — verify it.
- **Cross-platform claims.** README claims macOS, Linux, *and Windows*; testing has been
  macOS-only. Check the claims mechanically: `fcntl`/`flock` availability on Windows,
  signal-handler registration, path handling, shell-profile writes in setup, anything
  POSIX-only on a promised platform. If Windows support is not real, the finding is
  "fix it or fix the README", not silently either.
- **Security posture — OUT OF SCOPE for this review.** The entire security audit (egress
  SSRF guard, #200 rendered-capture containment, TLS scope, snapshot phone-home,
  wikilink/frontmatter injection defang, path traversal, secrets handling) has been split
  into a separate, standalone prompt: **`SECURITY_REVIEW_PROMPT.md`** at the repo root, to
  be run in its own session. **Do not audit security here, and do not report security
  findings in `FABLE_PROD_REVIEW.md`** — if you notice something security-relevant, note it
  in one line at the very end under "Deferred to the security review" and move on. This
  review covers correctness, concurrency/locking, failure paths, cross-platform claims,
  structural token-cost, runtime efficiency, dead code, tests, and the architectural
  "is it right?" questions — everything except security.
- **Structural token-cost audit.** Not a cost re-review (`FABLE_COST_REVIEW.md` did
  that; per-token efficiency questions are parked in #217/#215/#93 pending telemetry)
  — but the codebase has grown metered call sites since that review was written, and
  the *structural* properties are checkable from code. Inventory every model call
  site and verify: each prompt input is bounded (nothing grows without limit as the
  investigation ages — the class of bug #216 fixed), no per-document call that should
  be per-batch, and no call re-sends what deterministic code already knows (I1's cost
  face). Flag any call site `FABLE_COST_REVIEW.md` predates and never assessed, so
  the #217 measurement plan knows where to point its telemetry.
- **Runtime efficiency and scaling.** Distinct from token cost (which
  `FABLE_COST_REVIEW.md` owns): find hot-path waste and anything that degrades
  superlinearly as an investigation ages — hundreds of documents, thousands of entities,
  months of accumulation. Look for: large registry JSON re-parsed per document instead
  of once per batch, O(n²) loops over entities/documents, embedding-index rebuild policy
  (rebuild-the-world vs incremental), redundant file reads inside worker loops,
  subprocess spawn overhead in chew. Also CLI startup latency: interactive commands
  (`watchdog`, `status`, `new`) shouldn't pay import time for docling/fastembed/
  playwright — check lazy-import discipline across `cli.py` and `cmd/`. Findings need
  the same rigor as bugs: the concrete path, why it degrades, at what scale it starts to
  hurt.
- **Dead code and vestiges.** The D-log shows heavy evolution (skill-orchestrator →
  Python orchestrator, embedding classifier removed, Dataview → Bases). Find the
  leftovers: unreachable branches, `main()` entry points for commands that no longer
  exist, comments describing superseded behavior, `pyproject.toml` dependencies nothing
  imports anymore. Flag, don't fix.
- **Tests.** The suite is in `tests/` and runs via
  `~/.local/pipx/venvs/watchdog-intel/bin/pytest` (never `pipx run pytest`). Judge
  coverage by the mutation-testing standard: for each load-bearing behavior, would
  breaking the source turn a test red? Name the specific untested behaviors that matter
  (crash-recovery paths, lock contention, the id-reconciliation race, platform
  branches), not a coverage percentage. Run the suite once to establish it's green
  before trusting it as a baseline. The #200 capture end-to-end tests skip when the
  Playwright browser isn't installed — a skip is acceptable, but report it so the
  baseline is honest about what actually ran.
- **Vacuous tests.** Audit the *existing* tests for ones that pass without proving
  anything — these are worse than missing tests because they manufacture false
  confidence. Look for the known shapes: assertions that restate the test's own setup
  (build a dict, assert the dict); tests that exercise a mock/monkeypatched stub and
  then assert on the stub's behavior rather than the code under test; assertions too
  weak to fail (`assert result`, `assert isinstance(x, dict)`, checking only that no
  exception was raised on a path that can't raise); tests that never actually invoke
  the module they're named after; fixtures so heavily patched that the real code path
  (locking, file I/O, the model-client boundary) is bypassed entirely. **Verify by
  actual mutation, not by reading:** for a sample of suspicious tests, break the
  corresponding source behavior (invert a condition, drop a write, skip the lock) and
  confirm the test goes red — a test that stays green under mutation is CONFIRMED
  vacuous. Report each with the file:line, what it appears to test, what it actually
  proves, and whether to strengthen or delete it. Restore all mutations afterward and
  re-run the suite green before finishing (`git diff` must show no source changes from
  this exercise).

**2. Is it right?** The architectural layer — given the stated intent (a non-technical
investigative journalist drops public records in a folder and gets a trustworthy,
citable knowledge vault), does the system as built serve that intent?

- Walk the golden path as the intended user: `pipx install` → `setup` → `new` → drop
  files → `chew` → `ingest` → open Obsidian → ask questions. Where does a
  non-technical user hit a wall, a confusing error, a silent failure, or a state they
  can't recover from without understanding internals? Error messages are product
  surface here — audit `sys.exit` strings and warnings for actionability.
- Trust and provenance are the product. Anywhere the pipeline could silently present
  model output as more certain than it is (basis/inferred handling, contradiction
  rendering, citation links that could 404, morgue page anchors) is an architectural
  finding, not a nit.
- Judge the seams: is anything load-bearing but fragile (an invariant maintained only
  by convention, a registry/note consistency assumption with no checker, a format
  parsed by string-matching in two places that could drift)? `/watchdog-health` exists —
  does it actually check the things most likely to break?
- Where the architecture and the code have drifted apart, say which one is right.

## Discipline

- Every finding: file:line, current behavior, why it's wrong (concrete scenario),
  proposed fix, and the tradeoff — written so a follow-up session with no memory of
  this one can act on it directly. Recommendations that warrant a DECISIONS.md entry
  should be phrased so they translate into one.
- Verify before asserting. "I checked X and found Y" beats confident recall. You may
  run read-only commands freely, run the test suite, and exercise the CLI against
  scratch vaults in a temp directory — never against real investigation vaults, and
  never anything that makes network calls to model providers.
- No style nits, no defensive-coding-for-impossible-scenarios suggestions, no
  refactors of working code (see CLAUDE.md — simplicity rules are project law). Every
  recommendation should trace to correctness, the user's trust, or production
  readiness.

## Output

Write a single markdown file, `FABLE_PROD_REVIEW.md`, at the repo root, prioritized as:

1. **Release blockers** — ship-stoppers: data loss, corruption, broken promises on the
   golden path. (Security is out of scope — see `SECURITY_REVIEW_PROMPT.md`.)
2. **Fix before calling it production** — real bugs and gaps that aren't blockers.
3. **Doc/code mismatch inventory** — every place README/GETTING_STARTED/INSTALL or
   ARCHITECTURE disagree with the code, as a checklist. This section feeds the #166
   documentation rewrite directly, so make it exhaustive even when items are trivial.
4. **Test gaps and vacuous tests** — named untested behaviors (mutation-testing
   framing), plus the inventory of tests confirmed or suspected vacuous, each marked
   strengthen-or-delete.
5. **Post-release** — dead code, cleanups, efficiency wins that don't yet hurt at
   current scale, architectural suggestions with no urgency.

Efficiency findings are not their own section — slot each by severity like any other
finding (a scaling cliff on the golden path is a 2, not a 5). Security is out of scope
entirely (see `SECURITY_REVIEW_PROMPT.md`).

End with a one-page summary: your overall verdict on production readiness and the
shortest credible path to it.
