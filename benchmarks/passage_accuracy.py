"""Measure how well `pipeline/passages.py` finds each fact's source passage (D270).

Ground truth is a vault whose facts carry quote locators: the locator names the sentence the fact
was drawn from. For each such fact the locator and quote are hidden, the matcher is run as it would
be for a fact with only a page number, and the passage it picks is judged correct when it contains
the locator's sentence opening on the right page.

    python benchmarks/passage_accuracy.py [VAULT] [--embed]

With no VAULT the fictional demo investigation is built into a temporary folder
(`watchdog.gui.demo`). `--embed` also scores the local embedding model as a second signal, when
fastembed and its model are available. Prints precision and recall at a range of thresholds.

Precision = correct / located; recall = correct / all facts with ground truth; "located" counts
facts given any passage at that threshold.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from watchdog.pipeline import passages
from watchdog.pipeline.quote_verify import _normalize

THRESHOLDS = [0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.8]


def cases(vault: Path) -> list[dict]:
    docs = json.loads((vault / ".watchdog/registry/documents.json").read_text(encoding="utf-8"))
    out = []
    for sha, rec in sorted(docs.items()):
        pages = passages.morgue_page_texts(vault, rec)
        try:
            ex = json.loads((vault / ".watchdog/extracted" / f"{sha}.json").read_text(encoding="utf-8"))
        except OSError:
            continue
        index = passages._DocIndex(pages)
        for f in ex["document"].get("key_facts", []):
            loc = (f.get("quote_locator") or "").strip()
            if not loc or f.get("quote_verified") is False:
                continue
            out.append({"doc": rec["filename"], "fact": f["fact"], "page": f.get("page"),
                        "locator": loc, "index": index, "pages": pages})
    return out


def correct(case: dict, passage: str | None, page: int | None) -> bool:
    return bool(passage) and page == case["page"] and _normalize(case["locator"]) in _normalize(passage)


def lexical(case_list: list[dict]) -> list[tuple[float, bool]]:
    """(score, correct-if-accepted) for each case, at the lowest threshold."""
    out = []
    for c in case_list:
        r = passages.find_passage(c["index"], {"fact": c["fact"], "page": c["page"]}, threshold=0.0)
        out.append((r["score"], correct(c, r["passage"], r["page"])))
    return out


def embedded(case_list: list[dict], mix: float) -> list[tuple[float, bool]] | None:
    try:
        from fastembed import TextEmbedding
    except ImportError:
        return None
    import numpy as np
    model = TextEmbedding("BAAI/bge-small-en-v1.5")
    out = []
    for c in case_list:
        rows = c["index"].pages.get(c["page"], [])
        if not rows:
            out.append((0.0, False))
            continue
        texts = [r[0] for r in rows]
        vecs = np.array(list(model.embed([c["fact"]] + texts)))
        vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
        cos = vecs[1:] @ vecs[0]
        raw = passages.tokens(c["fact"])
        ft = {t: c["index"].weight(t, s) for t, s in raw.items()}
        total = sum(ft.values()) or 1.0
        lex = np.array([sum(w for t, w in ft.items() if t in r[1]) / total for r in rows])
        score = mix * lex + (1 - mix) * cos
        best = int(np.argmax(score))
        out.append((float(score[best]), correct(c, texts[best], c["page"])))
    return out


def decoys(case_list: list[dict]) -> list[float]:
    """Each fact scored against the same-numbered page of a different document, where its
    support cannot be: any passage accepted there is a false location."""
    out = []
    for i, c in enumerate(case_list):
        other = next((d for d in case_list[i + 1:] + case_list[:i] if d["doc"] != c["doc"]), None)
        if other is None:
            continue
        pages = sorted(other["index"].pages)
        page = min(c["page"] or 1, pages[-1]) if pages else 1
        r = passages.find_passage(other["index"], {"fact": c["fact"], "page": page}, threshold=0.0)
        out.append(r["score"])
    return out


def table(label: str, results: list[tuple[float, bool]], n: int) -> None:
    print(f"\n{label}  ({n} facts with a known source sentence)")
    print("  threshold  located  correct  precision  recall")
    for t in THRESHOLDS:
        got = [ok for s, ok in results if s >= t]
        good = sum(got)
        prec = good / len(got) if got else 1.0
        print(f"  {t:9.2f}  {len(got):7d}  {good:7d}  {prec:9.1%}  {good / n:6.1%}")
    best = sum(ok for _, ok in results)
    print(f"  ranking only (best passage, no threshold): {best}/{n} = {best / n:.1%}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("vault", nargs="?")
    ap.add_argument("--embed", action="store_true", help="also try the embedding model as a second signal")
    ap.add_argument("--misses", action="store_true", help="print the facts the matcher got wrong")
    args = ap.parse_args(argv)
    if args.vault:
        vault = Path(args.vault)
    else:
        from watchdog.gui import demo
        tmp = Path(tempfile.mkdtemp())
        vault = demo.build(tmp / "demo")
    case_list = cases(vault)
    n = len(case_list)
    if not n:
        print("No facts with quote locators to measure against.", file=sys.stderr)
        return 1
    lex = lexical(case_list)
    table("Lexical", lex, n)
    fake = decoys(case_list)
    print(f"\nDecoys: each fact against a page of another document ({len(fake)} facts)")
    print("  threshold  falsely located")
    for t in THRESHOLDS:
        k = sum(1 for s in fake if s >= t)
        print(f"  {t:9.2f}  {k:4d}  {k / len(fake):6.1%}")
    if args.misses:
        for c, (s, ok) in zip(case_list, lex):
            if not ok:
                r = passages.find_passage(c["index"], {"fact": c["fact"], "page": c["page"]}, threshold=0.0)
                print(f"\n  MISS {c['doc']} p{c['page']} score={s}\n    fact: {c['fact']}\n    want: {c['locator']}\n    got:  {r['passage']}")
    if args.embed:
        for mix in (0.0, 0.5, 0.7):
            res = embedded(case_list, mix)
            if res is None:
                print("\nfastembed is not installed; skipped the embedding signal.")
                break
            table(f"Embedding, lexical weight {mix}", res, n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
