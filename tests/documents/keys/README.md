# Answer keys — corpus-v1

One YAML per corpus document. These are the fixed reference the four benchmark conditions
(#361, #215) are scored against. **Not yet frozen — pending Tom's review.**

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

Two settings are load-bearing. Getting either wrong makes the results uninterpretable, silently.

### `--concurrency 1` — not optional

`preflight.run()` is called *inside* `_extract_document`, so each document snapshots the entity
registry **at the moment its own extraction starts**. At the default `--concurrency 5`, the first
five documents extract in parallel and every one of them takes its digest snapshot before any of
the others has written a single entity. **They cannot see each other.**

A contradiction can only fire when the second document of a pair is read *after* the first has
landed in the registry. So at default concurrency, whether the scored contradiction is catchable
at all is decided by which parallel wave each document happens to fall into — and if it lands
badly, every condition scores zero on contradictions and the result reads as "no model catches
these" when the pipeline never gave any of them the chance.

Sequential extraction costs wall-clock time, not tokens.

### Two passes, because `--skill` pins ONE skill for the whole run

The corpus needs two skills (see `expected_skill` in each key). `--skill` applies one skill to
every queued document, so a single pinned run would extract two of the six under the wrong skill —
and several `must_not_miss` items in the FY20-21 report are exactly what `financial-statements`
primes for and `bankruptcy` does not. Use `chew --file` to control what is queued, then drain the
queue twice:

```
# Pass 1 — the four insolvency documents, in chronological order
watchdog ingest --skill bankruptcy --concurrency 1 [--extractor-model … --extractor-effort …]

# Pass 2 — the two annual reports
watchdog ingest --skill financial-statements --concurrency 1 [same model flags]
```

### Ingest order (fixed across all conditions)

| # | Skill | Document |
|---|---|---|
| 1 | `bankruptcy` | Pre-Filing Report of the Proposed Monitor |
| 2 | `bankruptcy` | CCAA Initial Order |
| 3 | `bankruptcy` | First Report of the Monitor |
| 4 | `bankruptcy` | Pension Order |
| 5 | `financial-statements` | Annual Financial Report 2019-20 |
| 6 | `financial-statements` | Annual Financial Report 2020-21 |

The order is chosen, not incidental:

- **FY2019-20 is fifth**, so its digest already holds the Pre-Filing Report's "LU is insolvent…
  will not have sufficient funding / liquidity to meet payroll in February." Both sides of the
  scored contradiction are then explicitly available — one on the page, one in the digest — which
  is what the contradiction machinery needs to fire.
- **FY2020-21 is last**, so it sees FY2019-20 and the restatement contradictions (C2) become
  catchable too.

Consequence to expect, not trip over: two ingest passes means the finalizer runs twice, so each
vault gets two briefings. Identical across all conditions, so it does not distort the comparison.

## The freeze

Once reviewed, hash the keys the way the corpus is hashed and do not touch them again:

```
cd tests/documents/keys && shasum -a 256 *.yaml > keys-v1.sha256
```

A key that drifts between conditions invalidates every comparison made with it.
