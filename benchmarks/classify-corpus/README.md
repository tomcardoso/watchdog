# Classifier-model sweep corpus — not yet populated

Empty until someone builds it. `benchmark.yaml`'s `classifier_sweep` section needs a
type-diverse document set to *rank* classifier models against — `corpus/` (corpus-v1) only spans
two record skills (`bankruptcy`, `financial-statements`), which is enough to smoke-test the
default classifier but not enough to tell classifier models apart. Until this directory has
content, `run_benchmark.py` skips the classifier-sweep stage with a one-line notice; the
extractor sweep, finalizer sweep, and classifier smoke test are unaffected.

## What to add

A handful of real documents, each landing on a different skill from
`src/watchdog/skills/records/` beyond the two corpus-v1 already covers — for example a
corporate filing (`corporate-filings`), a real-estate document (`real-estate`), a government
report (`government-reports`), and a news clipping (`news-clippings`). Public, real documents,
same sourcing standard as corpus-v1 — no synthetic/fabricated fixtures.

Alongside the documents:

- `expected.yaml` — a flat mapping of filename to the correct skill, e.g.:

  ```yaml
  "some-corporate-filing.pdf": corporate-filings
  "some-real-estate-doc.pdf": real-estate
  ```

- A frozen `classify-corpus-v1.sha256` (`shasum -a 256 *.pdf > classify-corpus-v1.sha256`, run
  from inside this directory), the same freeze discipline `corpus/corpus-v1.sha256` uses —
  `run_benchmark.py` verifies it before any classifier-sweep arm runs, exactly like the main
  corpus.

No `.yml` sidecars — classification must actually run for this stage to mean anything (a pinned
sidecar skips classification entirely, per D120).
