"""Every page of a corpus read by each OCR engine, scored against the page's own text (#818).

Not a pytest module: a standalone benchmark tool, macOS only (Apple Vision). Run it with the app's
own engine Python and this checkout's pipeline:

    PYTHONPATH=src ~/Library/Application\\ Support/Watchdog/engine/venv/bin/python \\
        benchmarks/ocr_engine_compare.py run  [--corpus DIR] [--out DIR] [--engines a,b]
    ... benchmarks/ocr_engine_compare.py score [--out DIR]

`run` reads every page of every PDF once per engine, each engine in its own process so one
engine's settings cannot reach another's, and once without OCR for the page's text layer:

  text          the page's own text layer, no OCR (the reference for a born-digital page)
  vision        Apple Vision as shipped
  vision-nocorr Apple Vision with language correction off (`vision_ocr_compare.patch_ocrmac`)
  rapidocr      RapidOCR, `auto`'s engine outside macOS (D373)
  tesseract     Tesseract, through its command-line program (D379)
  easyocr       EasyOCR, Docling's default languages (not part of a default run: it is not
                shipped, so put an EasyOCR install on PYTHONPATH and name it in --engines)

Each page is copied to a one-page PDF and converted by `preprocess.process_with_docling` with OCR
forced, so it renders at the scale Watchdog picks for it (D364) and goes through the same layout
and reading order as the reference. A page is one conversion, so pages cannot be misfiled into one
another here (#816 is pre-processing, not the engine). Results are kept page by page under
`--out`, and a re-run reads only what is missing.

`score` compares each engine with the text layer on every page that has a sound one (a text layer
unlike the printed page is set aside, `text_layer_matches_page`), then each engine with the
person-settled reference for the scanned pages, `ocr-reference/corpus-v1-scanned.json`, which
`ocr_review_lines.py` builds the review for. Results: `2026-11-02-ocr-engine-results.md`.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENGINES = ["text", "vision", "vision-nocorr", "rapidocr", "tesseract"]
OCR_ENGINES = ENGINES[1:] + ["easyocr"]


# ── Reading ──────────────────────────────────────────────────────────────────────────────────

def _set_engine(engine: str) -> None:
    from watchdog.pipeline import preprocess
    if engine == "text":
        # Watchdog's converter always OCRs a page's pictures; the reference must hold no engine's
        # reading, so OCR is switched off outright.
        build = preprocess.build_converter

        def text_only(force_ocr, **kw):
            from docling.datamodel.base_models import InputFormat
            conv = build(force_ocr, **kw)
            conv.format_to_options[InputFormat.PDF].pipeline_options.do_ocr = False
            return conv
        preprocess.build_converter = text_only
    elif engine in ("vision", "vision-nocorr"):
        preprocess._config_force("ocr_engine", "apple_vision")
        if engine == "vision-nocorr":
            sys.path.insert(0, str(HERE))
            import vision_ocr_compare
            vision_ocr_compare.patch_ocrmac(False, None)
    elif engine == "rapidocr":
        preprocess._config_force("ocr_engine", "rapidocr")
    elif engine == "easyocr":
        preprocess._config_force("ocr_engine", "easyocr")
    elif engine == "tesseract":
        preprocess._config_force("ocr_engine", "tesseract")

        def cli_opts(force_ocr, *, required=False):
            from docling.datamodel.pipeline_options import TesseractCliOcrOptions
            return TesseractCliOcrOptions(force_full_page_ocr=force_ocr, lang=["eng"])
        preprocess._make_tesseract_opts = cli_opts


def read_document(pdf: Path, engine: str, out: Path) -> None:
    """Every page of `pdf` read by `engine`, written to `out` as it goes."""
    import pypdf
    from watchdog.pipeline import preprocess
    _set_engine(engine)
    done = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {"pages": {}}
    done["filename"] = pdf.name
    reader = pypdf.PdfReader(str(pdf))
    with tempfile.TemporaryDirectory() as tmp:
        for n in range(1, len(reader.pages) + 1):
            if str(n) in done["pages"]:
                continue
            writer = pypdf.PdfWriter()
            writer.add_page(reader.pages[n - 1])
            page_pdf = Path(tmp) / f"page-{n}.pdf"
            with open(page_pdf, "wb") as f:
                writer.write(f)
            started = time.monotonic()
            if engine == "text":
                result = preprocess.process_with_docling(page_pdf, force_ocr=False, detect=False)
            else:
                result = preprocess.process_with_docling(page_pdf, force_ocr=True)
            seconds = time.monotonic() - started
            if "error" in result:
                done["pages"][str(n)] = {"error": result["error"], "seconds": seconds}
            else:
                done["pages"][str(n)] = {
                    "text": "\n".join(p.get("markdown", "") for p in result["pages"]),
                    "seconds": seconds,
                    "ocr_engine": (result.get("metadata") or {}).get("ocr_engine"),
                }
            out.write_text(json.dumps(done, indent=1), encoding="utf-8")
            print(f"  {engine} {pdf.name} p{n} {seconds:.1f}s", file=sys.stderr, flush=True)


def run(corpus: Path, out: Path, engines: list[str]) -> None:
    pdfs = sorted(p for p in corpus.iterdir() if p.suffix.lower() == ".pdf")
    for engine in engines:
        for pdf in pdfs:
            dest = out / engine / f"{pdf.stem}.json"
            dest.parent.mkdir(parents=True, exist_ok=True)
            # One process per engine and document: patches stay in it.
            subprocess.run([sys.executable, __file__, "_read", str(pdf), engine, str(dest)],
                           check=True)


# ── Scoring ──────────────────────────────────────────────────────────────────────────────────

_QUOTES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "′": "'", "“": '"', "”": '"', "„": '"', "″": '"'})
_MD = re.compile(r"[#*|_`>\\]|<!--.*?-->|\[image\]|^-{3,}$", re.M)


def words(text: str) -> list[str]:
    """Markdown marks dropped, then whitespace-separated words with surrounding punctuation
    stripped. Case is kept: an identifier's case is part of it."""
    # Curly and straight quotes are one character here: neither engines nor a person typing the
    # reference choose between them reliably, and the choice changes nothing a reporter searches.
    text = text.translate(_QUOTES)
    text = _MD.sub(" ", text.replace("&amp;", "&"))
    out = []
    for w in text.split():
        w = w.strip(".,;:!?()[]{}\"'“”‘’")
        if w and not re.fullmatch(r"[-–—•·]+", w):
            out.append(w)
    return out


def align(ref: list[str], hyp: list[str]) -> tuple[int, list[tuple[str, int, int]]]:
    """Word edit distance (case-insensitive), and the operations: ("=", i, j), ("s", i, j),
    ("d", i, -1) for a reference word missing, ("i", -1, j) for an extra one."""
    a = [w.lower() for w in ref]
    b = [w.lower() for w in hyp]
    n, m = len(a), len(b)
    prev = list(range(m + 1))
    back = [[0] * (m + 1) for _ in range(n + 1)]
    for j in range(1, m + 1):
        back[0][j] = 2
    for i in range(1, n + 1):
        cur = [i] + [0] * m
        back[i][0] = 1
        ai = a[i - 1]
        for j in range(1, m + 1):
            best, op = prev[j - 1] + (ai != b[j - 1]), 0
            if prev[j] + 1 < best:
                best, op = prev[j] + 1, 1
            if cur[j - 1] + 1 < best:
                best, op = cur[j - 1] + 1, 2
            cur[j], back[i][j] = best, op
        prev = cur
    ops, i, j = [], n, m
    while i or j:
        op = back[i][j]
        if op == 0:
            ops.append(("=" if a[i - 1] == b[j - 1] else "s", i - 1, j - 1))
            i, j = i - 1, j - 1
        elif op == 1:
            ops.append(("d", i - 1, -1))
            i -= 1
        else:
            ops.append(("i", -1, j - 1))
            j -= 1
    return prev[m], ops[::-1]


_ID = re.compile(r"\d|@")


def identifiers(ws: list[str]) -> Counter:
    """Words a reporter searches for and cites that a language model can "correct": anything of
    three characters or more holding a digit (amounts, dates, file, licence and postal numbers) or
    an @ (email). Shorter ones are mostly page and paragraph numbers."""
    return Counter(w for w in ws if len(w) >= 3 and _ID.search(w))


def id_hits(ref_ids: Counter, hyp_text: str) -> Counter:
    """How many of each identifier a reading holds exactly, spacing aside (`11,2021` holds
    `2021`; `699931` does not hold `69993I`)."""
    flat = re.sub(r"\s+", "", hyp_text)
    return Counter({w: min(c, flat.count(w)) for w, c in ref_ids.items()})


DROP_RUN = 8   # this many reference words in a row missing from a reading is a dropped passage


def dropped_runs(ops) -> list[int]:
    runs, cur = [], 0
    for op, _, _ in ops:
        if op == "d":
            cur += 1
        elif op != "i":
            if cur >= DROP_RUN:
                runs.append(cur)
            cur = 0
    if cur >= DROP_RUN:
        runs.append(cur)
    return runs


def sound_text_layer(text: str) -> bool:
    """A page whose text layer can stand as the reference: one Watchdog would not OCR."""
    return len(words(text)) >= 20


def wer(ref: list[str], hyp: list[str]) -> float:
    return align(ref, hyp)[0] / max(len(ref), 1)


def text_layer_matches_page(ref: list[str], voter_words: dict[str, list[str]]) -> tuple[bool, float, float]:
    """Whether a text layer shows what is printed. Some PDFs carry text that is not drawn (a
    hidden duplicate of a paragraph, an earlier revision): every engine then reads the page alike
    and unlike its text layer. Such a page is set aside when the engine closest to the text layer
    is still more than 10% from it and over three times as far as the engines are from each other.
    Returns (matches, best engine's distance from the text layer, the engines' mutual distance)."""
    best = min(wer(ref, w) for w in voter_words.values())
    names = list(voter_words)
    pairs = [wer(voter_words[a], voter_words[b]) for i, a in enumerate(names) for b in names[i + 1:]]
    mutual = sum(pairs) / len(pairs)
    return not (best > 0.10 and best > 3 * mutual), best, mutual


def score(out: Path) -> dict:
    ref_docs = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted((out / "text").glob("*.json"))}
    readings = {e: {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in (out / e).glob("*.json")}
                for e in OCR_ENGINES if (out / e).is_dir()}
    totals = {e: Counter() for e in readings}
    per_doc: dict = {}
    examples: dict = {e: [] for e in readings}
    scanned, set_aside = [], []
    for stem, ref_doc in ref_docs.items():
        for n, ref_page in sorted(ref_doc["pages"].items(), key=lambda kv: int(kv[0])):
            ref_text = ref_page.get("text") or ""
            if not sound_text_layer(ref_text):
                scanned.append((stem, int(n)))
                continue
            ref = words(ref_text)
            voter_words = {}
            for e in VOTERS:
                page = (readings.get(e, {}).get(stem) or {}).get("pages", {}).get(n) or {}
                voter_words[e] = words(page.get("text") or "")
            ok, best, mutual = text_layer_matches_page(ref, voter_words)
            if not ok:
                set_aside.append({"doc": stem, "page": int(n), "best": round(best, 3), "mutual": round(mutual, 3)})
                continue
            ref_ids = identifiers(ref)
            for e, docs in readings.items():
                page = (docs.get(stem) or {}).get("pages", {}).get(n)
                if not page or "text" not in page:
                    continue
                hyp = words(page["text"])
                dist, ops = align(ref, hyp)
                hits = id_hits(ref_ids, " ".join(hyp))
                found = sum(hits.values())
                runs = dropped_runs(ops)
                t = totals[e]
                t["pages"] += 1
                t["ref_words"] += len(ref)
                t["errors"] += dist
                t["lost"] += sum(1 for op, _, _ in ops if op in ("s", "d"))
                t["ids"] += sum(ref_ids.values())
                t["id_hits"] += found
                t["dropped_passages"] += len(runs)
                t["dropped_words"] += sum(runs)
                t["ms"] += round(page["seconds"] * 1000)
                d = per_doc.setdefault(stem, {}).setdefault(e, Counter())
                d["ref_words"] += len(ref)
                d["errors"] += dist
                d["ids"] += sum(ref_ids.values())
                d["id_hits"] += found
                for w, c in ref_ids.items():
                    if hits[w] < c and len(examples[e]) < 400:
                        examples[e].append({"doc": stem, "page": int(n), "expected": w})
    return {"totals": totals, "per_doc": per_doc, "missed_identifiers": examples, "scanned": scanned,
            "set_aside": set_aside}


REFERENCE = HERE / "ocr-reference" / "corpus-v1-scanned.json"
SHORT_LINE = 4   # a reference line of fewer words is not counted as a dropped passage


def score_scanned(out: Path, reference: Path = REFERENCE) -> dict:
    """Each engine on the scanned pages, against the person-settled reference (#818).

    The reference is a set of printed lines, not running text: engines order a page's lines
    differently, and order is not what is measured. So a page is scored as a bag of words: `lost`
    is the share of the reference's words a reading lacks (misread or missed), identifiers are
    matched exactly as on born-digital pages, and a reference line of four words or more of which
    a reading holds under half is a dropped passage. Words a reading adds are not counted."""
    ref = json.loads(Path(reference).read_text(encoding="utf-8"))["pages"]
    readings = {e: {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in (out / e).glob("*.json")}
                for e in OCR_ENGINES if (out / e).is_dir()}
    totals = {e: Counter() for e in readings}
    missed: dict = {e: [] for e in readings}
    for stem, pages in ref.items():
        for n, lines in pages.items():
            ref_words = [w for ln in lines for w in words(ln["text"])]
            # An identifier broken at a line end ("81000-2-" / "175") is joined in the page text,
            # its hyphen dropped, by Docling for every engine alike, so it is no engine's error.
            ref_ids = Counter({w: c for w, c in identifiers(ref_words).items() if not w.endswith("-")})
            for e, docs in readings.items():
                page = (docs.get(stem) or {}).get("pages", {}).get(n)
                if not page or "text" not in page:
                    continue
                hyp_words = words(page["text"])
                bag = Counter(w.lower() for w in hyp_words)
                need = Counter(w.lower() for w in ref_words)
                lost = sum((need - bag).values())
                hits = id_hits(ref_ids, " ".join(hyp_words))
                dropped = 0
                for ln in lines:
                    lw = [w.lower() for w in words(ln["text"])]
                    if len(lw) >= SHORT_LINE and sum(1 for w in lw if bag[w]) < len(lw) / 2:
                        dropped += 1
                t = totals[e]
                t["pages"] += 1
                t["ref_words"] += len(ref_words)
                t["lost"] += lost
                t["ids"] += sum(ref_ids.values())
                t["id_hits"] += sum(hits.values())
                t["dropped_passages"] += dropped
                t["ms"] += round(page["seconds"] * 1000)
                for w, c in ref_ids.items():
                    if hits[w] < c:
                        missed[e].append({"doc": stem, "page": int(n), "expected": w})
    return {"totals": totals, "missed_identifiers": missed}


def report_scanned(result: dict) -> str:
    lines = ["Scanned pages, each engine against the person-settled reference", "",
             f"{'engine':<15}{'pages':>6}{'words':>8}{'lost':>8}{'ids':>7}{'ids exact':>11}"
             f"{'dropped':>9}{'s/page':>8}"]
    for e, t in result["totals"].items():
        if t["pages"]:
            lines.append(f"{e:<15}{t['pages']:>6}{t['ref_words']:>8}{t['lost'] / t['ref_words']:>8.2%}"
                         f"{t['ids']:>7}{t['id_hits'] / max(t['ids'], 1):>11.2%}"
                         f"{t['dropped_passages']:>9}{t['ms'] / t['pages'] / 1000:>8.1f}")
    return "\n".join(lines)


# Engines that read independently of one another. Vision without its correction is Vision again,
# so it would vote twice with Vision for any misreading the correction does not cause.
VOTERS = ["vision", "rapidocr", "tesseract"]
CONTEXT = 6   # settled words shown either side of a disputed spot


def consensus(readings: dict[str, list[str]]) -> list[dict]:
    """A scanned page's reference, built where every voter agrees. Each voter is aligned with the
    first; the page becomes a list of slots, each the first voter's word or a gap after it, holding
    every voter's reading there. A slot all voters read alike is `settled`; the rest are disputed,
    for a person to settle from the page image."""
    pivot_name, *others = VOTERS
    pivot = readings[pivot_name]
    # slot 2k is the gap before pivot word k, slot 2k+1 is pivot word k
    slots = [{pivot_name: [] if s % 2 == 0 else [pivot[s // 2]]} for s in range(2 * len(pivot) + 1)]
    for name in others:
        hyp = readings[name]
        for slot in slots:
            slot[name] = []
        _, ops = align(pivot, hyp)
        consumed = 0   # pivot words passed so far; an extra word sits in the gap before the next
        for op, i, j in ops:
            if op in ("=", "s"):
                slots[2 * i + 1][name].append(hyp[j])
            elif op == "i":
                slots[2 * consumed][name].append(hyp[j])
            if i >= 0:
                consumed = i + 1
    out = []
    for slot in slots:
        texts = {name: " ".join(slot[name]) for name in VOTERS}
        if not any(texts.values()):
            continue
        out.append({"readings": texts, "settled": len(set(texts.values())) == 1})
    return out


def disputes(out: Path) -> list[dict]:
    """Every disputed spot on every scanned page, with settled words around it for context."""
    ref_docs = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted((out / "text").glob("*.json"))}
    items = []
    for stem, ref_doc in ref_docs.items():
        for n, ref_page in sorted(ref_doc["pages"].items(), key=lambda kv: int(kv[0])):
            if sound_text_layer(ref_page.get("text") or ""):
                continue
            readings = {}
            for e in VOTERS:
                page = json.loads((out / e / f"{stem}.json").read_text(encoding="utf-8"))["pages"].get(n) or {}
                readings[e] = words(page.get("text") or "")
            slots = consensus(readings)
            for k, slot in enumerate(slots):
                if slot["settled"]:
                    continue
                before = [s["readings"][VOTERS[0]] for s in slots[max(0, k - CONTEXT):k] if s["settled"]]
                after = [s["readings"][VOTERS[0]] for s in slots[k + 1:k + 1 + CONTEXT] if s["settled"]]
                items.append({"doc": stem, "filename": ref_doc.get("filename"), "page": int(n),
                              "slot": k, "before": " ".join(before), "after": " ".join(after),
                              "readings": slot["readings"]})
    return items


def report(result: dict) -> str:
    lines = ["Born-digital pages, each engine's OCR against the page's own text layer", "",
             f"{'engine':<15}{'pages':>6}{'words':>8}{'WER':>8}{'lost':>8}{'ids':>7}{'ids exact':>11}"
             f"{'dropped':>9}{'s/page':>8}"]
    for e, t in result["totals"].items():
        if not t["pages"]:
            continue
        lines.append(f"{e:<15}{t['pages']:>6}{t['ref_words']:>8}{t['errors'] / t['ref_words']:>8.2%}"
                     f"{t['lost'] / t['ref_words']:>8.2%}"
                     f"{t['ids']:>7}{t['id_hits'] / max(t['ids'], 1):>11.2%}"
                     f"{t['dropped_passages']:>9}{t['ms'] / t['pages'] / 1000:>8.1f}")
    lines += ["", "Per document (WER / identifiers exact):"]
    for stem, engines in result["per_doc"].items():
        cells = "  ".join(f"{e} {c['errors'] / max(c['ref_words'], 1):.1%}/{c['id_hits'] / max(c['ids'], 1):.0%}"
                          for e, c in engines.items())
        lines.append(f"  {stem[:40]:<40} {cells}")
    lines += ["", "WER counts every difference; lost counts only the page's own words misread or "
              "missed, so text read from inside an image is not held against an engine.",
              "", f"Set aside, text layer unlike the printed page: {len(result['set_aside'])} pages"]
    lines += [f"  {a['doc']} p{a['page']}: nearest engine {a['best']:.0%} from it, engines "
              f"{a['mutual']:.0%} from each other" for a in result["set_aside"]]
    lines += ["", f"Scanned pages (no sound text layer, for the human-settled reference): "
              f"{len(result['scanned'])}"]
    by_doc = Counter(s for s, _ in result["scanned"])
    lines += [f"  {s}: {c} pages" for s, c in by_doc.items()]
    return "\n".join(lines)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "_read":
        read_document(Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4]))
        return
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--corpus", type=Path, default=HERE / "corpora" / "extract")
    r.add_argument("--out", type=Path, default=HERE / "runs" / "ocr-compare")
    r.add_argument("--engines", default=",".join(ENGINES))
    s = sub.add_parser("score")
    s.add_argument("--out", type=Path, default=HERE / "runs" / "ocr-compare")
    args = ap.parse_args()
    if args.cmd == "run":
        if sys.platform != "darwin":
            sys.exit("Apple Vision runs only on macOS.")
        engines = [e.strip() for e in args.engines.split(",") if e.strip()]
        unknown = set(engines) - set(ENGINES) - set(OCR_ENGINES)
        if unknown:
            sys.exit(f"unknown engine(s): {', '.join(sorted(unknown))}")
        run(args.corpus, args.out, engines)
    else:
        result = score(args.out)
        print(report(result))
        (args.out / "score.json").write_text(json.dumps(result, indent=1, default=dict), encoding="utf-8")
        if REFERENCE.exists():
            scanned = score_scanned(args.out)
            print("\n" + report_scanned(scanned))
            (args.out / "score-scanned.json").write_text(json.dumps(scanned, indent=1, default=dict),
                                                         encoding="utf-8")


if __name__ == "__main__":
    main()
