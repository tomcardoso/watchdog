"""Build blinded per-document judging packets for the #509 gpt-luna effort sweep.

Mirrors benchmarks/2026-07-29-judge-qualitative/build_packets.py but swaps the three
arms under comparison to the newly-run gpt-luna-low/med/high, reading vault data
read-only from the main checkout (these vaults are gitignored and only exist there).
"""
import json
import os
import random
import sys

REPO = "/Users/tcardoso/Dropbox/code/Personal/watchdog"
sys.path.insert(0, os.path.join(REPO, "benchmarks"))
from score_arms import anchors_from  # noqa: E402

import yaml

KEYS_DIR = os.path.join(REPO, "benchmarks/keys")
VAULTS = {
    "gpt-luna-low": os.path.join(REPO, "benchmarks/.vaults/bench-ex-gpt-luna-low"),
    "gpt-luna-med": os.path.join(REPO, "benchmarks/.vaults/bench-ex-gpt-luna-med"),
    "gpt-luna-high": os.path.join(REPO, "benchmarks/.vaults/bench-ex-gpt-luna-high"),
}
OUT_DIR = "/Users/tcardoso/.claude/jobs/780926a4/tmp/judge-luna-509"

random.seed(20260803)


def unscorable(items, text_key):
    out = []
    for it in items:
        text = (it.get(text_key) or "") + " " + (it.get("quote") or "")
        if not anchors_from(text):
            out.append(it)
    return out


def load_extraction(vault_dir, sha256):
    path = os.path.join(vault_dir, ".watchdog", "extracted", f"{sha256}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def extraction_summary(doc):
    d = doc.get("document", {})
    return {
        "summary": d.get("summary"),
        "key_facts": d.get("key_facts", []),
        "coverage_gap": d.get("coverage_gap"),
        "entities": [e.get("name") for e in doc.get("entities", [])],
    }


mapping_all = {}

for key_file in sorted(os.listdir(KEYS_DIR)):
    if not key_file.endswith(".yaml"):
        continue
    doc_name = key_file[:-5]
    key = yaml.safe_load(open(os.path.join(KEYS_DIR, key_file), encoding="utf-8"))
    sha256 = key["document"]["sha256"]

    unscored_facts = unscorable(key.get("facts") or [], "fact")
    unscored_mnm = unscorable(key.get("must_not_miss") or [], "item")

    if not unscored_facts and not unscored_mnm:
        continue

    arm_names = list(VAULTS.keys())
    shuffled = arm_names[:]
    random.shuffle(shuffled)
    labels = ["X", "Y", "Z"]
    label_to_arm = dict(zip(labels, shuffled))
    mapping_all[doc_name] = label_to_arm

    extractions = {}
    for label, arm in label_to_arm.items():
        doc = load_extraction(VAULTS[arm], sha256)
        extractions[label] = extraction_summary(doc) if doc else None

    packet = {
        "document": doc_name,
        "document_file": key["document"]["file"],
        "items": {
            "facts": [
                {"id": it["id"], "text": it["fact"], "quote": it.get("quote"), "page": it.get("page")}
                for it in unscored_facts
            ],
            "must_not_miss": [
                {"id": it["id"], "text": it["item"], "page": it.get("page"), "why": it.get("why")}
                for it in unscored_mnm
            ],
        },
        "arms": extractions,
    }

    out_path = os.path.join(OUT_DIR, f"packet-{doc_name}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(packet, f, indent=2, ensure_ascii=False)
    print(f"{doc_name}: {len(unscored_facts)} facts + {len(unscored_mnm)} must_not_miss -> {out_path}")

with open(os.path.join(OUT_DIR, "mapping.json"), "w", encoding="utf-8") as f:
    json.dump(mapping_all, f, indent=2)
print("\nmapping written (judge-eyes-only):", os.path.join(OUT_DIR, "mapping.json"))
