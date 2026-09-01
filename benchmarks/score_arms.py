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
the same fixed reference — it is not absolute recall. A key item is matched only against its
own document's extraction, not the whole vault (#591) — no citation provenance beyond that,
so within a document a match can still come from anywhere in it, not the specific span the key
cites. Only ~a third of key items carry a >=4-digit numeric anchor; most of the rest are
reported as unscorable for hand or judge scoring, and a handful whose only anchor is an
identifier (an LSO number, say) rather than a quantity fall back to a non-numeric name test
instead (#591) — see `score_item`.

Absolute recall figures from before #591 landed are not comparable to figures after it —
`FINDINGS.md` notes where a number was corrected. First used for the Tier 0 checklist A/B
recorded in #412. `score()` (#466) is the pure, structured-data entry point `run_benchmark.py`
builds its reports from; `main()` is unchanged for manual command-line use.
"""
import decimal
import glob
import json
import os
import re
import sys

import yaml

# Bumped whenever a change alters what counts as a hit, so figures produced under different
# versions are never ranked against each other as if they measured the same thing (#551). Version
# 1 was everything before this constant existed; version 2 is post-#591 — per-document matching
# (a key item is scored only against its own document's extraction, not the whole vault), the
# rounding-collision fix, and `millions_prose`.
SCORER_VERSION = 2


def _keys_glob(keys_dir=None):
    d = keys_dir if keys_dir is not None else os.path.join(os.path.dirname(os.path.abspath(__file__)), "keys")
    return sorted(glob.glob(os.path.join(d, "*.yaml")))


KEYS = _keys_glob()
NUM_RE = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?")


def quote_text(it):
    """A key item's supporting quote, flattened to one string.

    Either section may carry a single string or a list of verbatim spans (#573). A list is used
    wherever no contiguous span exists in the chew: a de-bundled `must_not_miss` claim supported
    by two adjacent spans, a sentence crossing a page break, or a table row the conversion
    scatters across columns so its label and figure must be quoted separately. Callers only ever
    scan this for numeric anchors, so joining is lossless for their purposes — nothing downstream
    needs the spans kept apart."""
    q = it.get("quote")
    if isinstance(q, (list, tuple)):
        return " ".join(str(x) for x in q)
    return q or ""


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


MIN_VARIANT_LEN = 4  # floor for name-candidate phrases (#591); numeric variants are guarded below


def variants(anchor):
    """Ways the same figure legitimately appears in extraction prose.

    The thousands-to-millions rounding is a legitimate restatement ($4,903 thousand really does
    appear as "$4.9 million", and per-document scoring — see `score()` — means a short "X.Y"
    token can now only match within the one document the item is actually about, not the whole
    corpus). What isn't legitimate is the *old* second rounding step's failure mode: when the
    two-decimal form rounds to a whole number ("78.00"), `.rstrip("0").rstrip(".")` used to strip
    the decimal point too, collapsing the anchor to a bare, undifferentiated integer ("78003" ->
    "78.00" -> "78." -> "78") that matches any substring in a 70-page corpus. Requiring the
    stripped form to still contain a "." keeps the meaningful one-decimal restatements ("4.9",
    "2.8") and drops only the ones that lost their fraction entirely (#591).
    """
    v = {anchor}
    if "." not in anchor and len(anchor) > 3:
        v.add(anchor[:-3] + "." + anchor[-3:])          # 66671 -> 66.671  (thousands as $M)
        n = int(anchor)
        v.add(f"{n / 1000:.1f}")                        # 66671 -> 66.7
        stripped = f"{n / 1000:.2f}".rstrip("0")
        if "." in stripped and not stripped.endswith("."):
            v.add(stripped)                             # 4903 -> 4.90 -> 4.9 (kept: has a digit)
                                                          # 78003 -> 78.00 -> 78. (dropped: bare)
        v |= millions_prose(anchor)                     # 5000000 -> "5 million" (whole-dollar $M)
    if "." in anchor:                                    # 66.671 -> 66671
        v.add(anchor.replace(".", ""))
        v.add(f"{float(anchor):.1f}")
    return v


def millions_prose(anchor):
    """"N million" prose forms for a whole-dollar anchor of at least $1,000,000.

    `variants()`'s thousands-to-millions conversion above only covers an anchor already
    denominated in thousands ($4,903 thousand -> "$4.9 million"). A plain-dollar anchor like
    "$5,000,000" needs a different conversion (divide by 1,000,000, not 1,000) to match the way
    extraction prose actually states it ("$5 million") — without this, that class of anchor can
    never match prose at all. Tom's ruling: the benchmark scores on numeric *value*, not on
    transcription format — "$5 million" is a hit against a $5,000,000 anchor because it is the
    same number, not because it happens to share digits.

    Every variant keeps the literal word "million" (or an "m" suffix, both seen in real
    extractions, e.g. "$85.9M") directly adjacent to the digits. That word is what keeps this
    safe: it cannot reintroduce the short bare-digit collisions #591 fixed, because a bare "5" is
    never produced on its own — the shortest possible output is "2 million" or "5.0m" (#591
    follow-up). Lowercase only: `variants()` is always matched against `norm()`-ed (lowercased)
    extraction text, so an uppercase "M" candidate would never match and is pointless to generate.
    """
    if "." in anchor:
        return set()
    n = int(anchor)
    if n < 1_000_000:
        return set()
    millions = decimal.Decimal(n) / decimal.Decimal(1_000_000)
    exact = millions.normalize()                        # 5000000 -> 5 ; 1250000 -> 1.25
    rounded = millions.quantize(decimal.Decimal("0.1"), rounding=decimal.ROUND_HALF_UP)
    # 1250000 -> 1.3 (a "sensibly rounded" one-decimal form, not banker's-rounded 1.2)
    # `:f` forces fixed-point: `Decimal.normalize()` rewrites a round ten into scientific
    # notation (Decimal("50") -> "5E+1"), so plain interpolation would emit "5E+1 million" for
    # $50,000,000 and never match anything. `rounded` needs no such guard — `quantize` keeps its
    # exponent — but round figures are exactly the ones prose states most often, so this is the
    # case that matters most.
    return {f"{exact:f} million", f"{rounded} million", f"{rounded}m"}


def norm(text):
    return re.sub(r"(\d),(\d)", r"\1\2", text.lower())   # strip thousands commas


IDENTIFIER_KEYWORD_RE = re.compile(r"\b(LSO|bar\s*(?:no\.?|number)|docket)\b", re.IGNORECASE)
NAME_RE = re.compile(r"[A-Z][A-Za-z.'-]*(?:\s+[A-Z][A-Za-z.'-]*)+")


def is_identifier_anchor(anchor, text):
    """True when `anchor` reads as a reference code (LSO/bar number, docket suffix) rather than
    a quantity — either a keyword like "LSO#" sits just before it, or the digits run straight
    into a trailing letter ("78003K") with no space, a suffix pattern a quantity never has (#591).
    """
    for m in re.finditer(re.escape(anchor), text):
        start, end = m.span()
        if IDENTIFIER_KEYWORD_RE.search(text[max(0, start - 15):start]):
            return True
        if end < len(text) and text[end].isalpha():
            return True
    return False


def name_candidates(text):
    """Multi-word capitalized phrases (candidate person/entity names) in `text` — the non-numeric
    fallback for an item whose only anchor is an identifier the extractor reasonably dropped
    (an LSO number) while still capturing the substance (the lawyer's name, correctly related)
    (#591)."""
    return [m.group() for m in NAME_RE.finditer(text) if len(m.group()) >= MIN_VARIANT_LEN]


def _strings(obj):
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        return " ".join(_strings(v) for v in obj.values())
    if isinstance(obj, list):
        return " ".join(_strings(v) for v in obj)
    return ""


def _extracted_files(vault):
    """The `extracted/*.json` artifacts for `vault`, checking both layouts a caller might pass:
    a live vault nests them under `.watchdog/`, while a kept run directory's `artifacts/<arm>/`
    holds `extracted/` directly (`bench_report.write_run`'s copy). The same dual check
    `qualitative/build_packets.py`'s `load_extraction` already uses, so the #551 index can score
    a run's archived artifacts — which outlive the live vault's next reset — the same way a live
    vault scores."""
    for sub in (("extracted",), (".watchdog", "extracted")):
        d = os.path.join(vault, *sub)
        if os.path.isdir(d):
            return glob.glob(os.path.join(d, "*.json"))
    return []


def vault_doc_texts(vault):
    """Per-document extraction text, keyed by the document's sha256 (the artifact's own
    filename) — so a key item can be matched only against the extraction for the document it
    belongs to, instead of any document in the vault (#591)."""
    out = {}
    for f in _extracted_files(vault):
        sha = os.path.splitext(os.path.basename(f))[0]
        try:
            out[sha] = norm(_strings(json.loads(open(f, encoding="utf-8").read())))
        except (OSError, json.JSONDecodeError):
            pass
    return out


def vault_text(vault):
    """Whole-vault extraction text, concatenated across every document. Used only as a fallback
    for a key item with no `document.sha256` to scope it to a single document (older or
    synthetic fixtures) — every other item is matched via `vault_doc_texts` instead (#591)."""
    return "\n".join(vault_doc_texts(vault).values())


def vault_extracted_shas(vault):
    """The sha256 of every document this vault actually staged an extraction for — the filename
    of each `extracted/<sha>.json` artifact (see `_extracted_files`), with no separate manifest
    to read (#559). A vault whose arm was rate-limited or Ctrl-C'd partway through, or that lost
    individual documents to per-document failures, simply has fewer of these files than a
    complete run — no flag to check, just count what's actually there."""
    return {os.path.splitext(os.path.basename(f))[0] for f in _extracted_files(vault)}


def score_item(text, blob):
    anc = anchors_from(text)
    if not anc:
        return None, 0, 0  # unscorable numerically
    hits = sum(1 for a in anc if any(v in blob for v in variants(a)))
    if hits == 0 and all(is_identifier_anchor(a, text) for a in anc):
        # Every anchor in this item is a reference code, not a quantity, and none of them
        # matched — don't fail the whole item on a number the extractor had no reason to
        # transcribe. Fall back to whether the item's substance (a name) was captured (#591).
        if any(cand.lower() in blob for cand in name_candidates(text)):
            return True, hits, len(anc)
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
    match against, so it scores exactly as it always has (and, having no sha to scope a match to,
    is matched against the whole-vault blob rather than one document's text — see `vault_text`).

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

    blobs = {v: vault_text(v) for v in vaults}          # fallback: keys with no document.sha256
    doc_texts = {v: vault_doc_texts(v) for v in vaults}  # per-document text, keyed by sha256
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
                full_text = text + " " + quote_text(it)
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
                    # A key item is matched only against its own document's extraction, not the
                    # whole vault (#591) — sibling documents can no longer cross-credit an anchor
                    # that never appeared in the document the item is actually about. Falls back
                    # to the whole-vault blob only for a key with no `document.sha256`.
                    doc_blob = doc_texts[v].get(doc_sha, "") if doc_sha else blobs[v]
                    # `full_text` has an anchor (checked above), so `score_item` never returns
                    # `None` here.
                    hit, h, n = score_item(full_text, doc_blob)
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
