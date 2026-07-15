# Answer keys — corpus-v1

One YAML per corpus document. These are the fixed reference every condition in the #361 / #215
benchmark is scored against — see `../BENCHMARKING.md` for how many runs that is and what each one
varies. **Drafted and reviewed; not yet frozen.** Freezing (see "The freeze" below) is Step 0 of
`BENCHMARKING.md` — the one thing to do before anything else in that guide.

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
- **The scanned Initial Order was read visually, not OCR'd first** — same reason, one stage earlier.
- **`must_not_miss` for the dense 70-page report was built against a mechanical inventory** — all 27
  note headings enumerated programmatically with page numbers, so a buried item cannot be absent
  from the key merely because the drafting model overlooked it. This is the fix for the
  *correlation* risk #361 identifies (see "The mistake worth recording" in that issue).

## Schema

| Field | Purpose |
|---|---|
| `document` | File, sha256, pages, text layer, type, role in the benchmark |
| `entities` | Name, type, `aliases`, role, page |
| `relationships` | subject / predicate / object / page |
| `facts` | 19–22 material facts, each with `page` and a **verbatim `quote`** |
| `contradictions` | Cross-document conflicts, with both sides quoted. **An empty list is meaningful** — it means an invented contradiction scores as a false positive |
| `must_not_miss` | Buried items, **scored separately**. This is where cheap conditions degrade first |
| `ocr_hazards` | Initial Order only — scanned-page features OCR can mangle |

`quote` and `aliases` exist to support the **three grounding tiers** (verbatim / credited
normalization / ungrounded) rather than exact match. "LU" for "Laurentian University of Sudbury" is
a credited normalization, not a miss.

## Scoring notes that are easy to get wrong

- **`ocr_hazards` are checked FIRST** on the Initial Order. Page 17 (the backsheet) is rotated 90°
  and is the only place the applicant's counsel is named. A condition that returns no counsel has
  probably hit a rotation failure, not a reasoning failure — look at the chew output before
  charging it to the extractor.
- **The scored contradiction is C1** in `annual-financial-report-19-20.yaml` ↔
  `prefiling-report-monitor.yaml`. Neither document cites the other.
- **The $52.8M endowment figure is NOT a contradiction** — documents 1 and 2 agree on it. Flagging
  it is a false positive.
- **Two source documents contain errors**, recorded deliberately: both Monitor's reports date the
  Haché affidavit to "January 30, 2020" (the Initial Order shows it is 2021), and the First Report
  prints the court file number without leading zeros. Faithfully extracting the wrong value is
  *correct* extraction. Neither is a miss.

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

Once reviewed, hash the keys the way the corpus is hashed and do not touch them again:

```
cd tests/documents/keys && shasum -a 256 *.yaml > keys-v1.sha256
```

A key that drifts between conditions invalidates every comparison made with it.
