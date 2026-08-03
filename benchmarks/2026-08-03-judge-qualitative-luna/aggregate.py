import json
import os

DIR = os.path.dirname(os.path.abspath(__file__))
mapping = json.load(open(os.path.join(DIR, "mapping.json")))

docs = [
    "annual-financial-report-19-20",
    "annual-financial-report-20-21",
    "first-report-monitor",
    "initial-order-2021-02-01",
    "pension-order-2021-03-17",
    "prefiling-report-monitor",
]

arms = ["gpt-luna-low", "gpt-luna-med", "gpt-luna-high"]
tally = {a: {"facts": {"verbatim": 0, "credited": 0, "ungrounded": 0},
             "must_not_miss": {"verbatim": 0, "credited": 0, "ungrounded": 0}} for a in arms}

detail_rows = []
per_doc_pct = {a: {} for a in arms}

for doc in docs:
    packet = json.load(open(os.path.join(DIR, f"packet-{doc}.json")))
    fact_ids = {it["id"] for it in packet["items"]["facts"]}
    mnm_ids = {it["id"] for it in packet["items"]["must_not_miss"]}

    judgment = json.load(open(os.path.join(DIR, f"judgment-{doc}.json")))
    label_to_arm = mapping[doc]

    doc_tally = {a: {"hit": 0, "total": 0} for a in arms}

    for item_id, per_label in judgment["judgments"].items():
        kind = "facts" if item_id in fact_ids else ("must_not_miss" if item_id in mnm_ids else None)
        if kind is None:
            print(f"WARNING: {doc}:{item_id} not found in packet items!")
            continue
        for label, verdict in per_label.items():
            arm = label_to_arm[label]
            tier = verdict["tier"]
            if tier not in ("verbatim", "credited", "ungrounded"):
                print(f"WARNING: unexpected tier '{tier}' at {doc}:{item_id}:{label}")
                continue
            tally[arm][kind][tier] += 1
            detail_rows.append((doc, item_id, kind, arm, tier, verdict.get("note", "")))
            doc_tally[arm]["total"] += 1
            if tier in ("verbatim", "credited"):
                doc_tally[arm]["hit"] += 1

    for a in arms:
        t = doc_tally[a]["total"]
        per_doc_pct[a][doc] = round(doc_tally[a]["hit"] / t * 100) if t else None

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

print()
print("Per-document hit % (facts+mnm combined):")
for doc in docs:
    row = "  ".join(f"{a}={per_doc_pct[a][doc]}%" for a in arms)
    print(f"  {doc:32} {row}")

with open(os.path.join(DIR, "summary.json"), "w") as f:
    json.dump({"tally": tally, "summary": summary, "per_doc_pct": per_doc_pct}, f, indent=2)

with open(os.path.join(DIR, "detail_rows.json"), "w") as f:
    json.dump(detail_rows, f, indent=2)

print(f"\nTotal item-arm judgments: {len(detail_rows)} (expect {140*3})")
