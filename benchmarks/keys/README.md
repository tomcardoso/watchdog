# Answer keys — corpus-v1

One YAML per corpus document. These are the fixed reference every condition in the #361 / #215
benchmark is scored against — see `../BENCHMARKING.md` for how many runs that is and what each one
varies. **Frozen at `keys-v4.sha256`.** Re-freezing after any revision (see "The freeze" below) is
Step 0 of `BENCHMARKING.md` — the one thing to do before anything else in that guide.

## What these are, and what they are not

A key is a **ranking device between conditions**, not a gold standard. Every decision rule in #361
has the form "does Haiku hold ≥95% of *Sonnet's* recall" — which needs a fixed, consistent
reference applied identically to every arm, not a true gold key.

**Never quote a recall number from these keys as an absolute measure of Watchdog's accuracy.**
"87% fact recall" only ever means "Haiku retained 87% of what Sonnet retained, against a common
reference." That limitation is recorded in #361 and is not negotiable.

## How they were drafted

- **From the source PDFs.** Never from a pipeline extraction, never from chewed text. A key built
  from the pipeline's own output inherits the pipeline's blind spots, so the extractor is never
  scored on anything the pipeline already drops.
- **The prompt that does the drafting is [`drafting-prompt.md`](drafting-prompt.md).** Use it when
  adding a document to the corpus or widening an existing key, so every key is drafted to the same
  standard — a key drafted to a different standard than its neighbours makes cross-document
  comparisons meaningless.
- **The scanned Initial Order was read visually, not OCR'd first** — same reason, one stage earlier.
- **`must_not_miss` for the dense 70-page report was built against a mechanical inventory** — all 27
  note headings enumerated programmatically with page numbers, so a buried item cannot be absent
  from the key merely because the drafting model overlooked it. This is the fix for the
  *correlation* risk #361 identifies (see "The mistake worth recording" in that issue).
- **Every page of every document was read for `keys-v4`** (#625). Before it, the keys cited 88 of
  the corpus's 209 pages — a regression on the other 121 could not be detected, because nothing
  was keyed there. v4 cites 158, and no document sits below 68%.

**One imbalance survives v4, and it is worth knowing before quoting a per-document number.** The
keys are not drafted at a uniform density: measured against each document's word count, the
2019-20 annual report gets roughly twice its share of the key and the 2020-21 report — the largest
document in the corpus at 42% of its words — gets about half. That is an artifact of how much each
document's drafting pass proposed, not a judgment that one matters more. The fix is a further pass
on the 2020-21 report rather than cuts elsewhere; until then, read aggregate recall as weighted
toward the smaller annual report.

## Schema

| Field | Purpose |
|---|---|
| `document` | File, sha256, pages, text layer, type, role in the benchmark |
| `entities` | Name, type, `aliases`, role, page |
| `relationships` | subject / predicate / object / page |
| `facts` | Material facts, each with `page` and a **verbatim `quote`**. Count is set by the document, not a quota — 23 in the Pension Order, 121 in the 2019-20 annual report |
| `contradictions` | Cross-document conflicts, with both sides quoted. **An empty list is meaningful** — it means an invented contradiction scores as a false positive |
| `must_not_miss` | Buried items, **scored separately**. This is where cheap conditions degrade first. One claim per entry, each with a **verbatim `quote`** or `basis: inferred` (#573) |

`quote` and `aliases` exist to support the **three grounding tiers** (verbatim / credited
normalization / ungrounded) rather than exact match. "LU" for "Laurentian University of Sudbury" is
a credited normalization, not a miss.

**Page cites and quote spans.** The page field is spelled `page` for a single page and `pages` for
a list; both are accepted everywhere. A `quote` in either section may be one string or a **list of
spans**, and a list is required wherever the document has no contiguous run of text to quote:

- the sentence crosses a page break
- the conversion scatters a table row across columns, so the row label and its figure have to be
  quoted separately (`["Total Receipts", "29,514"]`)
- the conversion mangles the page badly enough that only fragments survive

**In the entries added by the #625 pass, a span list is the norm rather than the exception** — 246
of the 296 carry more than one, because `drafting-prompt.md` asks for discrete verbatim spans and
forbids eliding across them. So a list there is not by itself a signal that the page is damaged;
read the entry's `location` for that. In the pre-#625 entries a list still means what it says below.

In all three, `fact`/`item` carries the truth and the spans carry what the conversion renders —
the M1.1 precedent in `initial-order-2021-02-01.yaml`. **Every quote in every key is machine-checked
to appear on its cited page in the frozen chew**; a quote that cannot be located there is a defect
in the key, not evidence about the pipeline.

### `must_not_miss` anchoring (#573)

Every entry now carries either a `quote` — one or more spans copied verbatim from the document's
**OCR text layer**, not retyped from the PDF — or `basis: inferred` with a `why_inferred`
explaining what the claim needs beyond stated text (combining facts from different parts of the
document, arithmetic, noticing an absence, reading a visual feature). Three rules keep this
honest:

- **One independently checkable claim per entry.** Entries that bundled several were split into
  `M6.1`, `M6.2`, … — dotted ids so a judgment recorded against `M6` in an archived pass can
  still be traced to its descendants. Bundling was the larger defect: an extraction holding two
  of three claims used to land on a judge's discretion, which depressed this column across every
  pass before 2026-08-09.
- **`inferred` is scored separately from quoted, never dropped.** An inferred item is a real
  thing a reporter must not miss; it just cannot be graded as recall of stated text.
- **`quote` mirrors the OCR; `item` carries the truth.** Where the text layer is corrupt these
  diverge on purpose — see the comment above `M1.1` in `initial-order-2021-02-01.yaml`, where
  `item` has the true `LSO# 69993I` and `quote` has the OCR's `699931`. Do not reconcile them.
  Faithfully extracting the wrong value is correct extraction (see the errors note below), and
  the divergence is the only durable record that the text layer is damaged there.

## Scoring notes that are easy to get wrong

- **An OCR-caused miss is still a miss** — this benchmark measures the pipeline end to end, not
  the extraction stage in isolation. That is the point of drafting the keys from the source PDFs
  and of reading the scanned Initial Order visually: the extractor is scored on what the pipeline
  drops, because a page the pipeline mangles is a page the reporter does not get. Do not discount
  an item because it sits behind a scanning artifact. This costs nothing in *ranking* terms
  either — every arm clones one chewed master vault (`ensure_master_vault` in
  `../run_benchmark.py`), so the OCR text layer is byte-identical across arms and cannot move one
  arm relative to another.
- **The Initial Order's `ocr_hazards` block is gone as of `keys-v3`** (#579, D194). It predicted
  where OCR would bite; the predictions have now been checked against the frozen chew, so it has
  nothing left to tell a reader. The prediction that mattered — that page 17's 90° rotation would
  yield "nothing or gibberish", losing the applicant's counsel entirely — **did not happen**: the
  chew auto-rotates the backsheet and renders the firm, the address, all four lawyers and their
  LSO numbers. The prediction that held was character-level damage on that page, and that is
  recorded where it is actionable, in the `item`/`quote` divergence on `M1.1`–`M1.5`.
- **The scored contradiction is C1** in `annual-financial-report-19-20.yaml` ↔
  `prefiling-report-monitor.yaml`. Neither document cites the other.
- **The $52.8M endowment figure is NOT a contradiction** — documents 1 and 2 agree on it. Flagging
  it is a false positive.
- **Two source documents contain errors**, recorded deliberately: both Monitor's reports date the
  Haché affidavit to "January 30, 2020" (the Initial Order shows it is 2021), and the First Report
  prints the court file number without leading zeros. Faithfully extracting the wrong value is
  *correct* extraction. Neither is a miss — and noticing the date discrepancy is itself a scored
  item, `prefiling-report-monitor:M2`, so the rule is already carried by the keys rather than
  resting on a reader remembering this note.

## The run protocol these keys assume

One setting is load-bearing: the **skill pin**. Concurrency and ingest order used to be too — that
changed with #381, and how many passes the corpus needs used to be too — that changed with D120.
The history matters for reading this benchmark, so both are recorded below.

### Concurrency and order no longer matter (#381 / D118)

Earlier drafts of this file mandated `--concurrency 1` and a fixed ingest order. That was because
`preflight.run()` used to snapshot the entity registry *inside* each document's extraction, so
entity resolution and contradiction detection depended on which documents had already landed — a
correctness property riding on a throughput knob (the bug that became #381).

**As of #381, extraction carries no vault state.** It is a pure function of the document, its
skill, the brief, and its sidecar. Entity resolution and contradiction detection moved to the
finalizer's **reconciliation pass** (`reconcile.py`), which runs once in post-ingest after every
document has landed and reads the complete per-entity claim ledger. So:

- **Extraction output is independent of `--concurrency` and ingest order.** Run at the default
  concurrency; there is no reason to serialize.
- **Contradictions are caught in reconciliation, not extraction** — which is concurrency-immune and
  order-immune by construction, and annotates *both* sides of a conflict.

This has a direct bearing on what the benchmark measures — see "What each arm measures" below.

### One pass now — each document's sidecar pins its own skill (D120)

The corpus needs two skills (see `expected_skill` in each key), and `--skill` pins one skill for
the whole run — so before D120 a single pinned run would have extracted two of the six under the
wrong skill, and several `must_not_miss` items in the FY20-21 report are exactly what
`financial-statements` primes for and `bankruptcy` does not. That used to mean draining the queue
twice, once per skill.

Each corpus PDF now ships with a `.yml` sidecar of the same name (e.g.
`Annual-Financial-Report-20-21.pdf.yml`) carrying its correct `skill:` value — resolved
deterministically in Python, never sent through the model, so it costs nothing and can't be
misclassified. Copy every document into `_INCOMING/` (sidecars travel with them), `chew`, then a
single `watchdog ingest` with no `--skill` flag at all correctly classifies all six:

| Skill | Documents |
|---|---|
| `bankruptcy` | Pre-Filing Report · Initial Order · First Report · Pension Order |
| `financial-statements` | Annual Financial Report 2019-20 · 2020-21 |

Consequence to expect: the finalizer and briefing now run **once** per vault, over all six
documents at once — which is strictly better for scoring than the old two-pass protocol, since the
cross-skill contradiction (C1, spanning a `bankruptcy` document and a `financial-statements` one)
and the entity-duplicate count no longer depend on which pass ran first or on an intermediate,
partial-corpus reconciliation state.

## What each arm measures (post-#381)

The split of labour across models changed, so the attribution of each metric changed with it:

- **Material-fact recall and `must_not_miss`** — the **extractor** model (`--extractor-model`).
  Extraction is now pure per-document reading, which is exactly what these keys score.
- **Entity resolution (duplicate count) and contradiction detection (C1, C2)** — the **finalizer**
  model (`--finalizer-model`), because reconciliation runs there. #361 weights entity/relationship
  quality highest for the DeepSeek decision, so for that arm the finalizer model matters as much as
  the extractor — do not hold it fixed at the Haiku default without recording that choice.
- **Contradictions are scored from the `[!contradiction]` callouts** written into entity notes
  (label, both values, both document slugs, both page numbers) — not the briefing's "flagged"
  count. Check the quoted values and pages against C1/C2.

## The freeze

**Before hashing, check the quotes** — a key quote that isn't where the key says it is reads as
evidence about the pipeline rather than as the key defect it is, and freezing makes it permanent:

```
python3 benchmarks/verify_keys.py --pages benchmarks/runs/<run>/pages
```

It exits non-zero on any problem. `benchmarks/runs/` is gitignored, so this needs a local chewed
run of the corpus; the check is on the frozen chew, not the PDFs, because the chew is what an
extraction is scored against.

Then hash the keys the way the corpus is hashed and do not touch them again:

```
cd benchmarks/keys && shasum -a 256 *.yaml > keys-v<n>.sha256
```

A key that drifts between conditions invalidates every comparison made with it.

**When a revision is unavoidable, cut a new version rather than re-hashing the old one.** The
previous `.sha256` stays in the tree as the pin for figures already published against it, and
`FINDINGS.md` says which version produced which numbers. Silently re-freezing would leave every
archived comparison claiming to be scored against a key that no longer exists.

| Version | Frozen | Covers |
|---|---|---|
| `keys-v1.sha256` | before the first sweep | every figure in `FINDINGS.md` up to and including 2026-08-08 |
| `keys-v2.sha256` | 2026-08-09 | `must_not_miss` anchoring and de-bundling (#573) — 77 entries became 131 |
| `keys-v3.sha256` | 2026-08-11 | `ocr_hazards` removed from the Initial Order and its dangling `why` references rewritten (#579). **Ids and item text are unchanged from v2** — this bump is advisory prose only |
| `keys-v4.sha256` | 2026-08-18 | The page-by-page pass (#625) — 257 entries became 553. 14 wrong page cites corrected and 6 unlocatable quotes rewritten as spans; `verify_keys.py` added and now gates the freeze. **No id drift** — every v4 entry is either a v3 entry under its original id or a new one appended after the highest existing id. No surviving entry's `fact`/`item` text changed; what changed is `page` on 17 of them and `quote` on 10, neither of which a judge is shown |

Each version's key files are archived beside its manifest (`v1/`, `v2/`) so an archived figure can
be read against the exact keys that produced it.

**Archived judgments do not survive a key version bump, and must not be made to.** De-bundling
changed ids (`M6` became `M6.1`/`M6.2`/`M6.3`), so a judgment recorded against `M6` no longer
matches anything; `aggregate.py` counts each unmatched keyed item as a miss, which turns an
archived pass into a meaningless number rather than a wrong one. Do not remap old verdicts onto
split ids — a `credited` on a bundled item says nothing about which of its claims was captured,
so distributing it would be inventing data. A pass scored under v1 stays reported under v1; a
new comparison needs new judging.

That warning is about **id drift**, which v2 → v3 does not have — ids and item text are identical,
so a v2 judgment still matches its item. One caveat if you compare across that bump anyway: a
`must_not_miss` entry's `why` is shown to the judge by `../qualitative/build_packets.py`, and the
v2 text for `M1.1`–`M1.5` asserted a rotation failure that did not occur. Judgments on those five
may have been graded more leniently than the v3 text invites.
