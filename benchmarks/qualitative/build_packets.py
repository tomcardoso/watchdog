"""Build blinded per-document judging packets for the #361/#215 qualitative judge pass.

For each corpus document, finds the key items (facts + must_not_miss) that have NO
numeric anchor (score_arms.py already can't score these — see its `anchors_from`), then
writes a packet file containing those items plus each arm's extracted content, blinded
as X/Y/Z with a per-document random mapping. The mapping is written to a judge-only file
(mapping.json) that is NOT given to the judges.

    build_packets.py --arms sonnet-4.6-high,sonnet-4.6-med,gpt-mini-low

Arms are ids from benchmark.yaml's extractor_sweep; their vaults are resolved the same way
run_benchmark.py resolves them. See RUNBOOK.md step 6 for the full procedure.
"""
import argparse
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from score_arms import anchors_from  # noqa: E402

import yaml

# Configuration comes from the command line and benchmark.yaml, never from edits to this file:
# the point of keeping the protocol tracked is that the next pass runs the identical procedure,
# and a pass whose arm list lives in a Python literal drifts every time someone re-points it.
#
#     build_packets.py --arms sonnet-4.6-high,sonnet-4.6-med,gpt-mini-low [--out DIR]
#
# Vault paths are resolved the same way run_benchmark.py resolves them (benchmark.yaml's
# vault_root + the sweep's vault_prefix), so the arms named here are the arm ids from that file.
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BENCH = os.path.join(REPO, "benchmarks")
KEYS_DIR = os.path.join(BENCH, "keys")


def _parse_args():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--arms", required=True,
                    help="comma-separated extractor_sweep arm ids to judge (blinded as X/Y/Z)")
    ap.add_argument("--config", default=os.path.join(BENCH, "benchmark.yaml"))
    ap.add_argument("--out", default=os.path.dirname(os.path.abspath(__file__)),
                    help="where packets and mapping.json are written (default: beside this script)")
    return ap.parse_args()


args = _parse_args()
config = yaml.safe_load(open(args.config, encoding="utf-8"))
_root = config.get("vault_root")
VAULT_ROOT = (os.path.join(BENCH, ".vaults") if not _root
              else _root if os.path.isabs(_root) else os.path.join(BENCH, _root))
_prefix = config["extractor_sweep"]["vault_prefix"]
_arm_ids = [a.strip() for a in args.arms.split(",") if a.strip()]
_known = {a["id"] for a in config["extractor_sweep"]["arms"]}
_unknown = [a for a in _arm_ids if a not in _known]
if _unknown:
    sys.exit(f"Unknown arm id(s): {', '.join(_unknown)}")
VAULTS = {a: os.path.join(VAULT_ROOT, f"{_prefix}-{a}") for a in _arm_ids}
_missing = [v for v in VAULTS.values() if not os.path.isdir(v)]
if _missing:
    sys.exit("No vault for: " + ", ".join(_missing) + "\nRun those arms first.")
OUT_DIR = args.out
os.makedirs(OUT_DIR, exist_ok=True)

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
    labels = [chr(ord("X") + i) if i < 3 else f"A{i}" for i in range(len(arm_names))]
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
