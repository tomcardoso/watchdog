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

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from score_arms import anchors_from  # noqa: E402

import yaml

# Derived from this file's own location, not hardcoded: the whole point of keeping this script
# tracked is that the next pass runs the same protocol, and a path into one person's home
# directory means nobody else (and no agent on a fresh checkout) can run it at all.
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KEYS_DIR = os.path.join(REPO, "benchmarks/keys")
# Arms under judgment. Edit for the pass being run — these are the three #361/#215 candidates.
_VAULT_ROOT = os.path.join(REPO, "benchmarks/.vaults")
VAULTS = {
    "sonnet-4.6-high": os.path.join(_VAULT_ROOT, "bench-ex-sonnet-4.6-high"),
    "sonnet-4.6-med": os.path.join(_VAULT_ROOT, "bench-ex-sonnet-4.6-med"),
    "gpt-mini-low": os.path.join(_VAULT_ROOT, "bench-ex-gpt-mini-low"),
}
# Defaults beside this script (gitignored — see .gitignore); override with argv[1].
OUT_DIR = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))

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
