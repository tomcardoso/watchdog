"""Deterministic anchor-based scorer: frozen corpus-v1 keys vs benchmark-arm vaults (#412).

Not a pytest module — a standalone benchmark tool (no ``test_`` prefix, so pytest never
collects it). Run it against one or more benchmark vaults, dig-only or fully finalized:

    ~/.local/pipx/venvs/watchdog-intel/bin/python benchmarks/score_arms.py \
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
First used for the Tier 0 checklist A/B recorded in #412. `score()` (#466) is the pure,
structured-data entry point `run_benchmark.py` builds its reports from; `main()` is unchanged
for manual command-line use.
"""
import glob
import json
import os
import re
import sys

import yaml


def _keys_glob(keys_dir=None):
    d = keys_dir if keys_dir is not None else os.path.join(os.path.dirname(os.path.abspath(__file__)), "keys")
    return sorted(glob.glob(os.path.join(d, "*.yaml")))


KEYS = _keys_glob()
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


def vault_extracted_shas(vault):
    """The sha256 of every document this vault actually staged an extraction for — the filename
    of each `.watchdog/extracted/<sha>.json` artifact, with no separate manifest to read (#559).
    A vault whose arm was rate-limited or Ctrl-C'd partway through, or that lost individual
    documents to per-document failures, simply has fewer of these files than a complete run — no
    flag to check, just count what's actually there."""
    return {os.path.splitext(os.path.basename(f))[0]
           for f in glob.glob(os.path.join(vault, ".watchdog", "extracted", "*.json"))}


def score_item(text, blob):
    anc = anchors_from(text)
    if not anc:
        return None, 0, 0  # unscorable numerically
    hits = sum(1 for a in anc if any(v in blob for v in variants(a)))
    return (hits / len(anc)) >= 0.5, hits, len(anc)


def score(vaults, keys_dir=None):
    """Pure, side-effect-free scorer (no printing). See module docstring for the caveats.

    A key item's document is skipped for a vault that never extracted it — a rate limit, a
    Ctrl-C, or a per-document failure can all leave a vault with fewer staged extractions than
    the corpus has documents (#559), and scoring those documents' key items as misses charges the
    arm for pages it never opened. Gated on `.watchdog/extracted/<sha>.json` presence (an exact
    match against the key's own `document.sha256`, no heuristic), not on any cancelled/rate_limited
    flag the caller might pass, so a per-document failure gets the same treatment as a hard stop.
    A key with no `document.sha256` (older or synthetic fixtures) is never filtered — nothing to
    match against, so it scores exactly as it always has.

    Returns:
        {"vaults": [<basename>, ...],
         "detail": [{"qid": "<key>:<id>",
                     "cells": {<vault-basename>: {"hit": bool|"not_extracted",
                                                   "hits": int, "total": int}}}, ...],
         "totals": {"facts": {<vault-basename>: {"hit": int, "of": int}},
                    "must_not_miss": {<vault-basename>: {"hit": int, "of": int}}},
         "unscorable": [qid, ...]}

    Whether an item carries a numeric anchor at all depends only on its own text, never on which
    vault is looking, so that check is made once per item, up front — an item with no anchor
    never enters `detail` and is reported only in `unscorable` ("no numeric anchor, needs hand
    check"). `hit` is the string `"not_extracted"` when *this particular vault* never staged the
    item's document — a vault-specific gap, kept out of `unscorable` even when it leaves every
    vault's cell `"not_extracted"`: the item still has an anchor, nothing was ever attempted on
    it, and reporting it as "no numeric anchor" would be a different, false claim.
    """
    key_files = KEYS if keys_dir is None else _keys_glob(keys_dir)
    keys = []
    for f in key_files:
        k = yaml.safe_load(open(f, encoding="utf-8"))
        keys.append((os.path.basename(f).replace(".yaml", ""), k))

    blobs = {v: vault_text(v) for v in vaults}
    extracted_shas = {v: vault_extracted_shas(v) for v in vaults}
    totals = {v: [0, 0] for v in vaults}    # facts hit/of, keyed by full vault path
    mnm_totals = {v: [0, 0] for v in vaults}
    unscorable = []
    detail = []
    for name, k in keys:
        doc_sha = (k.get("document") or {}).get("sha256")
        for kind, items, tot in (("F", k.get("facts") or [], totals),
                                 ("M", k.get("must_not_miss") or [], mnm_totals)):
            for it in items:
                text = it.get("fact") or it.get("item") or ""
                full_text = text + " " + (it.get("quote") or "")
                qid = f"{name}:{it.get('id')}"
                # Whether an item carries a numeric anchor depends only on its own text — never
                # on which vault is looking (`score_item` derives it the same way for every
                # vault) — so it is checked once, up front, rather than inferred from whether any
                # non-filtered vault happened to score it. That is what keeps a fully-filtered
                # item (every vault `"not_extracted"`) from being misclassified as "no numeric
                # anchor": it has one, nothing was ever attempted on it.
                if not anchors_from(full_text):
                    unscorable.append(qid)
                    continue
                cells = {}
                for v in vaults:
                    vb = os.path.basename(v)
                    if doc_sha and doc_sha not in extracted_shas[v]:
                        cells[vb] = {"hit": "not_extracted", "hits": 0, "total": 0}
                        continue
                    # `full_text` has an anchor (checked above), so `score_item` never returns
                    # `None` here.
                    hit, h, n = score_item(full_text, blobs[v])
                    tot[v][1] += 1
                    if hit:
                        tot[v][0] += 1
                    cells[vb] = {"hit": hit, "hits": h, "total": n}
                detail.append({"qid": qid, "cells": cells})

    return {
        "vaults": [os.path.basename(v) for v in vaults],
        "detail": detail,
        "totals": {
            "facts": {os.path.basename(v): {"hit": totals[v][0], "of": totals[v][1]} for v in vaults},
            "must_not_miss": {os.path.basename(v): {"hit": mnm_totals[v][0], "of": mnm_totals[v][1]}
                              for v in vaults},
        },
        "unscorable": unscorable,
    }


def main(vaults):
    result = score(vaults)
    qid_w = max(len(qid) for qid in [d["qid"] for d in result["detail"]] + result["unscorable"])
    header = f"{'key item':{qid_w}}" + "".join(f"{os.path.basename(v):>26}" for v in vaults)
    print(header)
    for d in result["detail"]:
        row = []
        for v in vaults:
            c = d["cells"][os.path.basename(v)]
            if c["hit"] == "not_extracted":
                row.append("not extracted")
            elif c["hit"] is None:
                row.append("   n/a")
            else:
                row.append(f"{'HIT' if c['hit'] else 'miss'} {c['hits']}/{c['total']}")
        print(f"{d['qid']:{qid_w}}" + "".join(f"{c:>26}" for c in row))
    print()
    for label, key in (("FACTS (numeric-scorable)", "facts"), ("MUST_NOT_MISS (numeric-scorable)", "must_not_miss")):
        print(label)
        for v in vaults:
            vb = os.path.basename(v)
            h, n = result["totals"][key][vb]["hit"], result["totals"][key][vb]["of"]
            pct = f"{h / n * 100:.0f}%" if n else "n/a"
            print(f"  {vb:26} {h:>3}/{n}  ({pct})")
    print(f"\nunscorable (no numeric anchor, needs hand check): {len(result['unscorable'])}")
    print("  " + ", ".join(result["unscorable"]))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: score_arms.py <vault> [<vault> ...]")
    main(sys.argv[1:])
