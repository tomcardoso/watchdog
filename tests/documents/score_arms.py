"""Deterministic anchor-based scorer: frozen corpus-v1 keys vs benchmark-arm vaults (#412).

Not a pytest module — a standalone benchmark tool (no ``test_`` prefix, so pytest never
collects it). Run it against one or more benchmark vaults, dig-only or fully finalized:

    ~/.local/pipx/venvs/watchdog-intel/bin/python tests/documents/score_arms.py \
        ~/Investigations/bench-ex-sonnet-med ~/Investigations/bench-t0-sonnet-med ...

For each key item (facts F*, must_not_miss M*), it pulls numeric anchors from the key text,
generates formatting variants (comma-grouped, thousands-as-millions decimal, rounded $M),
and checks them against `.watchdog/extracted/*.json` — the raw per-document extraction
artifacts `watchdog dig` stages before any finalizer call. Scoring against these instead of
the committed vault notes means `watchdog bark` never needs to run for an extractor-only
arm, and the finalizer's reconciliation/synthesis pass can't dilute the measurement — the
artifacts persist after `bark` too, so this reads the same regardless of whether a vault has
been finalized.

Caveats (same spirit as the #215 verbatim tier, see keys/README.md): this RANKS arms against
the same fixed reference — it is not absolute recall. Scoring is blob-level (no citation
provenance), so sibling documents can cross-credit; only ~a third of key items carry a
>=4-digit numeric anchor and the rest are reported as unscorable for hand or judge scoring.
First used for the Tier 0 checklist A/B recorded in #412.
"""
import glob
import json
import os
import re
import sys

import yaml

KEYS = sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "keys", "*.yaml")))
NUM_RE = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?")


def anchors_from(text):
    """Distinctive numeric anchors: digit-strings with >=4 digits, or >=3 with a decimal."""
    out = []
    for m in NUM_RE.finditer(text):
        tok = m.group().lstrip("$").rstrip("%")
        digits = tok.replace(",", "")
        raw = digits.replace(".", "")
        if len(raw) >= 4 and not re.fullmatch(r"(19|20)\d\d", digits):
            out.append(digits)
    return sorted(set(out), key=len, reverse=True)


def variants(anchor):
    """Ways the same figure legitimately appears in extraction prose."""
    v = {anchor}
    if "." not in anchor and len(anchor) > 3:
        v.add(anchor[:-3] + "." + anchor[-3:])          # 66671 -> 66.671  (thousands as $M)
        n = int(anchor)
        v.add(f"{n / 1000:.1f}")                        # 66671 -> 66.7
        v.add(f"{n / 1000:.2f}".rstrip("0").rstrip("."))
    if "." in anchor:                                    # 66.671 -> 66671
        v.add(anchor.replace(".", ""))
        v.add(f"{float(anchor):.1f}")
    return v


def norm(text):
    return re.sub(r"(\d),(\d)", r"\1\2", text.lower())   # strip thousands commas


def _strings(obj):
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        return " ".join(_strings(v) for v in obj.values())
    if isinstance(obj, list):
        return " ".join(_strings(v) for v in obj)
    return ""


def vault_text(vault):
    parts = []
    for f in glob.glob(os.path.join(vault, ".watchdog", "extracted", "*.json")):
        try:
            parts.append(_strings(json.loads(open(f, encoding="utf-8").read())))
        except (OSError, json.JSONDecodeError):
            pass
    return norm("\n".join(parts))


def score_item(text, blob):
    anc = anchors_from(text)
    if not anc:
        return None, 0, 0  # unscorable numerically
    hits = sum(1 for a in anc if any(v in blob for v in variants(a)))
    return (hits / len(anc)) >= 0.5, hits, len(anc)


def main(vaults):
    keys = []
    for f in KEYS:
        k = yaml.safe_load(open(f, encoding="utf-8"))
        keys.append((os.path.basename(f).replace(".yaml", ""), k))

    qid_w = max(len(f"{name}:{it.get('id')}")
                for name, k in keys
                for it in (k.get("facts") or []) + (k.get("must_not_miss") or []))
    header = f"{'key item':{qid_w}}" + "".join(f"{os.path.basename(v):>26}" for v in vaults)
    print(header)
    blobs = {v: vault_text(v) for v in vaults}
    totals = {v: [0, 0] for v in vaults}    # facts hit/of
    mnm_totals = {v: [0, 0] for v in vaults}
    unscorable = []
    detail = []
    for name, k in keys:
        for kind, items, tot in (("F", k.get("facts") or [], totals),
                                 ("M", k.get("must_not_miss") or [], mnm_totals)):
            for it in items:
                text = it.get("fact") or it.get("item") or ""
                qid = f"{name}:{it.get('id')}"
                row = []
                any_scorable = False
                for v in vaults:
                    hit, h, n = score_item(text + " " + (it.get("quote") or ""), blobs[v])
                    if hit is None:
                        row.append("   n/a")
                        continue
                    any_scorable = True
                    tot[v][1] += 1
                    if hit:
                        tot[v][0] += 1
                    row.append(f"{'HIT' if hit else 'miss'} {h}/{n}")
                if not any_scorable:
                    unscorable.append(qid)
                else:
                    detail.append((qid, row))

    for qid, row in detail:
        print(f"{qid:{qid_w}}" + "".join(f"{c:>26}" for c in row))
    print()
    for label, tot in (("FACTS (numeric-scorable)", totals), ("MUST_NOT_MISS (numeric-scorable)", mnm_totals)):
        print(label)
        for v in vaults:
            h, n = tot[v]
            print(f"  {os.path.basename(v):26} {h:>3}/{n}  ({h / n * 100:.0f}%)")
    print(f"\nunscorable (no numeric anchor, needs hand check): {len(unscorable)}")
    print("  " + ", ".join(unscorable))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: score_arms.py <vault> [<vault> ...]")
    main(sys.argv[1:])
