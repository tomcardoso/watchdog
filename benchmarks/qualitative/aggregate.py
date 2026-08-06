import glob
import json
import os
import sys

import yaml

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from score_arms import anchors_from  # noqa: E402

# The denominator comes from the ANSWER KEYS, not from whatever a past pass happened to package
# or a judge happened to return. A key item that exists and was never graded is a miss, not an
# item that never existed — counting only what came back silently shrinks the denominator and
# inflates every percentage, which is how the 2026-07-29 table ended up reporting 70/44 against
# the tool's own 87/53 (see FINDINGS.md). Reading the keys also means adding documents or key
# items raises the denominator for the next pass automatically, with nothing to remember.
DIR = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KEYS_DIR = os.path.join(REPO, "benchmarks/keys")

mapping = json.load(open(os.path.join(DIR, "mapping.json")))

# Scope is the documents this pass actually judged (it may be a subset); within them, every
# unscorable key item counts, graded or not.
docs = sorted(os.path.basename(p)[len("packet-"):-len(".json")]
              for p in glob.glob(os.path.join(DIR, "packet-*.json")))
if not docs:
    sys.exit(f"No packet-*.json in {DIR} — run build_packets.py first.")

arms = sorted({arm for per_doc in mapping.values() for arm in per_doc.values()})


def _unscorable_ids(items, text_key):
    """Key items with no numeric anchor — the ones score_arms.py cannot grade, which is exactly
    the slice this pass exists to cover. Mirrors build_packets.unscorable."""
    return [it["id"] for it in items
            if not anchors_from((it.get(text_key) or "") + " " + (it.get("quote") or ""))]


keyed = {}          # doc -> {"facts": [id, ...], "must_not_miss": [id, ...]}
for doc in docs:
    key_path = os.path.join(KEYS_DIR, f"{doc}.yaml")
    if not os.path.exists(key_path):
        sys.exit(f"No answer key for judged document {doc!r} ({key_path})")
    key = yaml.safe_load(open(key_path, encoding="utf-8"))
    keyed[doc] = {
        "facts": _unscorable_ids(key.get("facts") or [], "fact"),
        "must_not_miss": _unscorable_ids(key.get("must_not_miss") or [], "item"),
    }

# tally[arm]["facts"|"must_not_miss"][tier] = count
tally = {a: {"facts": {"verbatim": 0, "credited": 0, "ungrounded": 0},
             "must_not_miss": {"verbatim": 0, "credited": 0, "ungrounded": 0}} for a in arms}

# also load packets to know which ids are facts vs must_not_miss
detail_rows = []

ungraded = []       # (doc, kind, item_id) present in the keys but absent from the judgment

for doc in docs:
    judgment = json.load(open(os.path.join(DIR, f"judgment-{doc}.json")))["judgments"]
    label_to_arm = mapping[doc]

    for kind in ("facts", "must_not_miss"):
        for item_id in keyed[doc][kind]:
            per_label = judgment.get(item_id)
            if per_label is None:
                # Keyed but never graded. Counted against every arm — an item nobody was scored
                # on is a miss, not an item that never existed.
                ungraded.append((doc, kind, item_id))
                for arm in arms:
                    tally[arm][kind]["ungrounded"] += 1
                    detail_rows.append((doc, item_id, kind, arm, "ungrounded", "not graded"))
                continue
            for label, verdict in per_label.items():
                arm = label_to_arm[label]
                tier = verdict["tier"]
                if tier not in ("verbatim", "credited", "ungrounded"):
                    print(f"WARNING: unexpected tier '{tier}' at {doc}:{item_id}:{label}")
                    continue
                tally[arm][kind][tier] += 1
                detail_rows.append((doc, item_id, kind, arm, tier, verdict.get("note", "")))

    # A judgment for something not in the keys means the packet and the keys have diverged.
    stray = set(judgment) - set(keyed[doc]["facts"]) - set(keyed[doc]["must_not_miss"])
    for item_id in sorted(stray):
        print(f"WARNING: {doc}:{item_id} judged but not an unscorable key item — ignored")

if ungraded:
    print(f"NOTE: {len(ungraded)} keyed item(s) had no judgment and counted as "
          f"misses for every arm:")
    for doc, kind, item_id in ungraded:
        print(f"  {doc}:{item_id} ({kind})")
    print()

print(f"{'arm':14} {'facts hit/total':>18} {'facts %':>9}   {'mnm hit/total':>16} {'mnm %':>7}")
summary = {}
for arm in arms:
    f = tally[arm]["facts"]
    m = tally[arm]["must_not_miss"]
    f_total = sum(f.values())
    m_total = sum(m.values())
    f_hit = f["verbatim"] + f["credited"]
    m_hit = m["verbatim"] + m["credited"]
    f_pct = f_hit / f_total * 100 if f_total else 0
    m_pct = m_hit / m_total * 100 if m_total else 0
    summary[arm] = {
        "facts": {**f, "hit": f_hit, "total": f_total, "pct": round(f_pct)},
        "must_not_miss": {**m, "hit": m_hit, "total": m_total, "pct": round(m_pct)},
    }
    print(f"{arm:14} {f_hit:>8}/{f_total:<8} {f_pct:>7.0f}%   {m_hit:>7}/{m_total:<7} {m_pct:>6.0f}%")

print()
print("Tier breakdown (verbatim / credited / ungrounded):")
for arm in arms:
    f = tally[arm]["facts"]
    m = tally[arm]["must_not_miss"]
    print(f"  {arm:14} facts: {f['verbatim']:>3}v {f['credited']:>3}c {f['ungrounded']:>3}u   "
          f"mnm: {m['verbatim']:>3}v {m['credited']:>3}c {m['ungrounded']:>3}u")

with open(os.path.join(DIR, "summary.json"), "w") as f:
    json.dump({"tally": tally, "summary": summary}, f, indent=2)

with open(os.path.join(DIR, "detail_rows.json"), "w") as f:
    json.dump(detail_rows, f, indent=2)

print(f"\nTotal item-arm judgments: {len(detail_rows)} (expect {140*3})")

# sonnet-high sanity check on initial-order document specifically
print("\n--- sonnet-high on initial-order-2021-02-01 (verify empty-extraction finding) ---")
soh_rows = [r for r in detail_rows if r[0] == "initial-order-2021-02-01" and r[3] == "sonnet-high"]
tiers = [r[4] for r in soh_rows]
print(f"  {len(soh_rows)} items, tiers: {dict((t, tiers.count(t)) for t in set(tiers))}")
