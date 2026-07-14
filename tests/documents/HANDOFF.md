# Handoff — pick up the #361 / #215 measurement pass

Paste the block below into a fresh session, from the repo root.

---

We're running the extraction benchmark that gates #361 (shipped Claude default + DeepSeek
validation), #215 (reasoning-effort default), and several issues riding along on the same runs.
**corpus-v1 is already frozen — do not re-select or re-freeze documents.**

## Read these first, in this order

1. `tests/documents/CORPUS-v1.md` — the frozen corpus: which six documents, why each one, the
   contradiction pair being scored, and what's deliberately excluded. Start here.
2. The runbook artifact: <https://claude.ai/code/artifact/ef5f5a01-bc42-49d5-9b91-12a6c3383bce>
   — the seven steps, whose job each one is, and what it costs. **Steps 1 and 2 are done.**
3. GitHub issue #361 (`gh issue view 361`) — the protocol and the decision rules. Note the
   2026-07-13 revision: **answer keys are model-drafted, not hand-written.**

## Where things stand

- **Step 1 (choose documents) — DONE.** Six Laurentian CCAA documents, 209 pages, born-digital and
  scanned mixed, all public.
- **Step 2 (freeze) — DONE.** `tests/documents/corpus-v1.sha256`; provenance in `CORPUS-v1.md`.
  Committed on branch `benchmark-corpus-v1` (not yet merged — open a PR for it, or fold it into the
  first real PR). Verify any time with:
  `cd tests/documents && shasum -a 256 -c corpus-v1.sha256`
- **Steps 3–7 — TO DO.** Detailed below.

## Step 3 — build the answer keys

One YAML per document in `tests/documents/keys/`, drafted **from the source PDFs — never from a
pipeline extraction** — and **frozen before any condition runs**. Schema and rationale are in
`CORPUS-v1.md` and #361.

Three rules that are load-bearing, not stylistic:

- **`must_not_miss` for `Annual-Financial-Report-20-21.pdf` (the dense 70-page document) gets a
  dedicated adversarial pass, drafted twice and unioned.** Its *only* job is hunting buried items:
  walk the schedules, footnotes, signature blocks, and appendices, and enumerate what is buried
  there. Run it on **two model families — Opus and Gemini — and union the two lists.** Do not draft
  it with Opus alone: the risk is *correlation*, not capability. Anything Opus misses is absent from
  the key, and Opus's blind spots correlate with Sonnet's and Haiku's, so Haiku would "pass" on a
  schedule nobody wrote down.
- **Use the Auditor General's special report as the oracle** when drafting the keys — it is the
  page-cited account of what was actually true. Keep it OUT of the corpus.
  <https://www.auditor.on.ca/en/content/specialreports/specialreports/LaurentianUniversity_EN.pdf>
- **Then hand the drafted keys to Tom to review** (~20 min) before freezing. He is the quality gate,
  not the author: a frozen key with a bad fact in it corrupts all four conditions at once and nothing
  downstream will catch it. Do not freeze the keys until he has read them.

The contradiction that must appear in the keys is documented in full, with quotes and page numbers,
in `CORPUS-v1.md`. Don't re-derive it.

## Step 4 — four vaults, one condition each

Fresh vault per condition — **never reuse a vault**, carried-over entities contaminate everything
downstream of pre-flight. Same documents, same order, `--skill` pinned so classification variance
doesn't pollute an extraction comparison.

| Vault | Condition | Flags |
|-------|-----------|-------|
| bench-a | Baseline (and #215's `high` arm) | `--extractor-model sonnet --extractor-effort high` |
| bench-b | #215: medium effort | `--extractor-model sonnet --extractor-effort medium` |
| bench-c | #361: Haiku as shipped default? | `--extractor-model haiku --extractor-effort high` |
| bench-d | #361: DeepSeek | `--extractor-model deepseek:<model>` |

## Step 5 — the runs ⚠️

**Do not run these without Tom's explicit go-ahead.** They cost real money and share his
subscription session window. Roughly **$10–15 total** across the four conditions (209 pages;
the artifact's "$6–9" predates the final corpus and is low — correct it if you touch the page).

`watchdog ingest --estimate` gives a free cost preview first. After each run, capture before
touching the next vault — usage, briefings, timeline, hot.md, and a Registry snapshot — and **keep
the terminal output**: page-coverage warnings and per-document digest-size lines print only there
and are not recoverable afterwards.

## Steps 6–7 — score and decide

Judge model must **not** be a model under test (self-preference bias), and blind it regardless:
strip which condition produced which output, randomize order. Credit matches in three tiers —
verbatim / credited normalization / ungrounded — not exact match.

**Which judge, per arm — this matters and is easy to get wrong:**

- **Sonnet vs Haiku, and the #215 effort A/B:** an **Opus** judge is fine. Both arms are the same
  family, so any family preference lands on both equally and cancels out of the comparison.
- **DeepSeek vs Claude:** it does **not** cancel. An Anthropic judge scoring Anthropic output against
  a competitor's is asymmetric and would flatter Claude on precisely the question Tom most needs to
  get right (DeepSeek is his intended personal ingest path). **Cross-check this arm with a
  non-Anthropic judge (Gemini)** and compare the two verdicts. If they agree, the result stands.
  **If they disagree, that disagreement is the finding.**

**Tom reads the first scorecard by hand.** That is why #362 (the scoring script) is deliberately not
built yet; automating before reading one scorecard automates the wrong thing.

Decision rules are in #361 and were written down before the numbers exist, on purpose. Apply them as
written.

## Working agreements for this repo

- **Always work in a git worktree** — Tom runs concurrent sessions on the same checkout.
  In a worktree, run pytest as `PYTHONPATH=<worktree>/src ~/.local/pipx/venvs/watchdog-intel/bin/pytest`
  or you'll silently test the main checkout.
- **Never `git checkout --` to undo an experiment** if you have uncommitted work — it restores from
  HEAD and eats it. Commit first, or copy the file aside.
- Definition of done, docs rules, and the `DECISIONS.md` convention are in `CLAUDE.md`. Read it.
- `git push` over SSH currently fails (no identities in the agent). Push with
  `git -c credential.helper='!gh auth git-credential' push -u https://github.com/tomcardoso/watchdog.git <branch>`
- Tom is an investigative reporter, not a developer by trade. Explain in plain terms, and don't hand
  him plans whose critical path is hours of his manual labour — he'll (rightly) refuse them.
