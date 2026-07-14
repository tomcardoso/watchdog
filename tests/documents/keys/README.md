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

## The freeze

Once reviewed, hash the keys the way the corpus is hashed and do not touch them again:

```
cd tests/documents/keys && shasum -a 256 *.yaml > keys-v1.sha256
```

A key that drifts between conditions invalidates every comparison made with it.
