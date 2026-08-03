# Model-response fixtures (#352)

A curated, checked-in library of real model responses that hit a specific condition — truncation,
malformed JSON, schema drift, pagination continuation — so `test_model_client.py` can pin the
deterministic code around the model (`section`, `acomplete_json`, `_complete_with_pagination`,
`_merge_usage`, `output_ceiling_for_sectioning`) against real backend behaviour, not hand-authored
guesses at what a truncated or malformed response looks like.

## Where these come from

`benchmarks/run_benchmark.py` auto-captures every truncation/malformed-JSON/schema-drift/
continuation event during a real (non-`--estimate-only`) run to a local, gitignored directory:
`benchmarks/.fixture-capture/`. That directory is a scratch heap — every condition fired by every
arm of every run, undifferentiated. This directory is the small, curated subset actually promoted
from it.

Auto-capture is scoped to the benchmark harness specifically because `corpus-v1` (the benchmark
corpus) is public court filings, not a real investigation vault — see `fixture_capture.py`'s module
docstring and DECISIONS.md D164. Never copy a fixture in here from anywhere other than a benchmark
run against `corpus-v1`.

## Promoting a capture

1. Run a benchmark (`benchmarks/run_benchmark.py`, any stage). If any of the conditions below fire,
   a JSON file lands in `benchmarks/.fixture-capture/`.
2. Look through the new files. If one is a good example of a condition not already represented
   here for that backend, copy it into a `<condition>/` subdirectory here, named
   `<backend>-<model_id>.json`.
3. Add one line to the table below.
4. Nothing needs scrubbing (public corpus), but keep an eye out for anything that looks like an
   API key or path leaking into the response text — reject the fixture rather than editing around
   it if so.

## Conditions

- **truncation** — a response cut off at the provider's max-token ceiling (`finish_reason`/
  `stop_reason` in `{length, max_tokens, MAX_TOKENS}`).
- **malformed_json** — a response that failed `_extract_json` after fence/brace-matching recovery.
- **schema_drift** — a response whose parsed JSON had a key `_prune_unknown` had to remove.
- **continuation** — one round of a prefill-continuation pair (`claude-api`/`deepseek` only) —
  the prefix that was resent plus the continuation text that came back.

## Inventory

| Condition | Backend | Model | Added |
|---|---|---|---|
| _(none yet — this library grows as benchmark runs surface real examples, see #352)_ | | | |
