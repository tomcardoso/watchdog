"""Union-of-N recall curve for a self-consistency run (#562 follow-up).

Not a pytest module — a standalone benchmark tool, same posture as score_arms.py. Given the arm
vaults (or archived `artifacts/<arm>` directories) of N identical arms, it scores the union of
the first 1, 2, ... N of them and prints the `must_not_miss` recall curve:

    ~/.local/pipx/venvs/watchdog-intel/bin/python benchmarks/union_recall.py \
        benchmarks/runs/<run>/artifacts/bench-sc-luna-low-r*

(Add `PYTHONPATH=<checkout>/src` if you are running from a worktree and want *its* copy of
`pipeline/verify`'s dedup thresholds rather than the pipx-installed package's.)

The curve is the measurement. A steep rise means each sample misses independently and union-of-N
is a real recall lever; a flat curve means the misses are correlated — the same salience
judgment failing every time — and no amount of resampling will fix them.

Why no new scoring logic: `score_arms.score()` matches numeric anchors against the concatenated
text of every `.watchdog/extracted/*.json` in a vault, so the union of N arms is literally a
directory holding all N arms' artifacts. Arm 1's files keep their bare `<sha>.json` names (which
is what `vault_extracted_shas` reads to decide whether a document was attempted at all); later
arms' copies are suffixed so they add text without colliding.
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import score_arms  # noqa: E402


def _extracted_dir(path):
    """Accept either a real vault (`<v>/.watchdog/extracted`) or an archived `artifacts/<arm>`
    directory (`<a>/extracted`), so a run can be re-scored straight out of benchmarks/runs/."""
    for candidate in (os.path.join(path, ".watchdog", "extracted"),
                      os.path.join(path, "extracted"), path):
        if os.path.isdir(candidate):
            return candidate
    sys.exit(f"Error: no extracted/ directory under {path}")


def build_union(arm_dirs, dest):
    """A vault whose `.watchdog/extracted` holds every arm's artifacts."""
    ex = os.path.join(dest, ".watchdog", "extracted")
    os.makedirs(ex, exist_ok=True)
    for i, arm in enumerate(arm_dirs):
        src = _extracted_dir(arm)
        for f in sorted(os.listdir(src)):
            if not f.endswith(".json"):
                continue
            # Arm 0 keeps the bare <sha>.json name — that is what marks the document as attempted.
            name = f if i == 0 else f"{f[:-5]}-r{i + 1}.json"
            shutil.copyfile(os.path.join(src, f), os.path.join(ex, name))
    return dest


def main(arm_dirs):
    tmp = tempfile.mkdtemp()
    unions = []
    for n in range(1, len(arm_dirs) + 1):
        unions.append(build_union(arm_dirs[:n], os.path.join(tmp, f"union-{n:02d}")))

    res = score_arms.score(unions)
    mnm = res["totals"]["must_not_miss"]

    print("\n=== must_not_miss recall, union of the first N samples ===")
    print(f"{'N':>3} {'hit':>6} {'of':>6} {'recall':>9} {'marginal':>10}")
    prev = None
    for n, v in enumerate(unions, 1):
        t = mnm[os.path.basename(v)]
        r = t["hit"] / t["of"] if t["of"] else 0.0
        marginal = "" if prev is None else f"{r - prev:+.1%}"
        print(f"{n:>3} {t['hit']:6} {t['of']:6} {r:9.1%} {marginal:>10}")
        prev = r

    # Per-item, so a stubborn zero (e.g. annual-financial-report-19-20:M9) is visible rather than
    # averaged away — that single item is the whole reason the subset was chosen.
    print("\n=== per-item: recovered at which N? ===")
    first, last = os.path.basename(unions[0]), os.path.basename(unions[-1])
    for d in res["detail"]:
        if ":M" not in d["qid"]:
            continue
        cells = d["cells"]
        if cells.get(first, {}).get("hit") == "not_extracted":
            continue
        got = next((n for n, v in enumerate(unions, 1)
                    if cells.get(os.path.basename(v), {}).get("hit") is True), None)
        if got == 1:
            continue    # already found by a single pass — no headroom, nothing to learn
        verdict = f"recovered at N={got}" if got else "NEVER recovered"
        print(f"  {d['qid']:46} {verdict}")
    if res["unscorable"]:
        print(f"\n({len(res['unscorable'])} key items have no numeric anchor and are unscorable "
              f"here — hand or judge scoring only; see score_arms.py's caveats.)")
    print(f"\n(scored against {last}'s union; blob-level matching, so sibling documents in the "
          f"same vault can cross-credit — same caveat as score_arms.py.)")

    distinct_fact_curve(arm_dirs)


def distinct_fact_curve(arm_dirs):
    """How many *distinct* facts do N samples produce between them?

    This, not the recall curve above, is the measurement with headroom. Only ~24 must_not_miss
    items carry a numeric anchor and ~23 of them are already hit by a single pass, so the frozen
    key can barely move — it would print a flat line whether or not the samples differ, which is
    the wrong reason to conclude anything. Counting distinct facts has no ceiling.

    Two facts count as one using `pipeline/verify._is_restatement` — the pipeline's own
    near-duplicate test (Jaccard 0.75 / containment 0.9), so "distinct" here means exactly what
    it would mean if these facts were merged by an actual ingest. Facts are pooled per document,
    never across documents.

    Read it as a gate, not a verdict: a flat curve is decisive (the samples say the same things,
    so there is nothing for a union to harvest and ensembling is dead). A rising curve is
    necessary but not sufficient — the extra facts might be noise, and only precision scoring
    (benchmarks/verifier_precision.py's judge packets) can say which.
    """
    from watchdog.pipeline import verify

    print("\n=== distinct facts, union of the first N samples (verify._is_restatement dedup) ===")
    print(f"{'N':>3} {'distinct':>9} {'vs N=1':>8} {'new':>6}")
    accepted = {}       # sha -> [token-set, ...]
    base = None
    for n, arm in enumerate(arm_dirs, 1):
        src = _extracted_dir(arm)
        added = 0
        for f in sorted(os.listdir(src)):
            if not f.endswith(".json"):
                continue
            import json
            with open(os.path.join(src, f), encoding="utf-8") as fh:
                doc = json.load(fh)
            sha = f[:-5]
            seen = accepted.setdefault(sha, [])
            for fact in (doc.get("document") or {}).get("key_facts") or []:
                toks = verify._tokens(fact.get("fact") or "")
                if not toks or any(verify._is_restatement(toks, e) for e in seen):
                    continue
                seen.append(toks)
                added += 1
        total = sum(len(v) for v in accepted.values())
        base = base or total
        print(f"{n:>3} {total:9} {total / base:7.2f}x {added:6}")



if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit("usage: union_recall.py <arm-dir> <arm-dir> [<arm-dir> ...]   (2 or more)")
    main(sys.argv[1:])
