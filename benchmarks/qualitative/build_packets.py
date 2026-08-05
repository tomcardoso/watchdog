"""Build blinded per-document judging packets for the #361/#215 qualitative judge pass.

For each corpus document, finds the key items (facts + must_not_miss) that have NO
numeric anchor (score_arms.py already can't score these — see its `anchors_from`), then
writes a packet file containing those items plus the three arms' extracted content,
blinded as X/Y/Z with a per-document random mapping. The mapping is written to a
judge-only file (mapping.json) that is NOT given to the scoring subagents.
"""
import json
import os
import random
import sys

sys.path.insert(0, "benchmarks")
from score_arms import anchors_from  # noqa: E402

import yaml

REPO = "/Users/tcardoso/Dropbox/code/Personal/watchdog"
KEYS_DIR = os.path.join(REPO, "benchmarks/keys")
VAULTS = {
    "sonnet-high": os.path.join(REPO, "benchmarks/.vaults/bench-ex-sonnet-high"),
    "sonnet-med": os.path.join(REPO, "benchmarks/.vaults/bench-ex-sonnet-med"),
    "gpt-mini": os.path.join(REPO, "benchmarks/.vaults/bench-ex-gpt-mini"),
}
OUT_DIR = "/private/tmp/claude-502/-Users-tcardoso-Dropbox-code-Personal-watchdog/6237d463-bda5-4ca0-abb0-38603ec5971f/scratchpad/judge"

random.seed(20260729)


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
    """Pull the fields relevant to qualitative (non-numeric) judging."""
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
