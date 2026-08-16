# Verifier precision grading — run `2026-08-09-1523`, arm `gpt-luna-low-verify`

The hand grading of all 220 facts the verification pass added in that run: the measurement behind
#589, D199, and the fitted `_CONTAINMENT_SUPPRESS` in `pipeline/verify.py`. Result: 41% material,
58% trivial, one unsupported. See `benchmarks/FINDINGS.md` for the reading.

These are checked in, unlike the judgment files directly under `benchmarks/qualitative/` (which
`.gitignore` excludes). Those are transient output of a judge workflow that gets re-run; this is a
settled dataset a shipped constant was fitted to, and refitting that constant after a verifier
prompt change means grading a new population and comparing it against this one.

The packets these grade are **not** here — each carries the document's full page text and they
rebuild deterministically, no model calls involved:

```
python benchmarks/verifier_precision.py build <path-to>/benchmarks/runs/2026-08-09-1523 \
    --arm gpt-luna-low-verify --out <dir>
cp judgment-*.json <dir>/
python benchmarks/verifier_precision.py aggregate <dir>
```

One judge, unblinded, no second reader — the limits are recorded in D199's tradeoff paragraph.
