"""The review that makes the scanned-page reference for `ocr_engine_compare.py` (#818).

On a scanned page there is no text layer to score against, so a person settles it, but only where
the engines disagree. Each of the three independent engines (Apple Vision, RapidOCR, Tesseract;
`ocr_engine_compare.VOTERS`) reads every scanned page again keeping its line boxes; lines are
matched across engines by where they sit on the page (`group`), not by reading order, which
engines differ on. A line all three read alike is settled; every other line becomes a review item
with an enlarged crop of exactly that line. The answers, with the settled lines, are the reference
(`ocr-reference/corpus-v1-scanned.json`).

Run after `ocr_engine_compare.py run`, with the app's engine Python:

    PYTHONPATH=src <engine python> benchmarks/ocr_review_lines.py <repo> <out dir> [all|build]

`all` reads the line boxes and builds; `build` rebuilds from boxes already read. It writes
`lines.json` (the items), `settled.json` and `crops/`, `pages/` images into the out dir; the page
that shows them for review is not kept in the repository.
"""
import hashlib
import json
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

REPO = Path(sys.argv[1])
OUT = Path(sys.argv[2])
STAGE = sys.argv[3] if len(sys.argv) > 3 else "all"
sys.path.insert(0, str(REPO / "benchmarks"))
import ocr_engine_compare as o  # noqa: E402

RUNS = REPO / "benchmarks" / "runs" / "ocr-compare"
CORPUS = REPO / "benchmarks" / "corpora" / "extract"
VOTERS = o.VOTERS
POS = OUT / "positions"


def scanned_pages():
    out = []
    for f in sorted((RUNS / "text").glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        for n, p in d["pages"].items():
            if not o.sound_text_layer(p.get("text") or ""):
                out.append((f.stem, int(n)))
    return sorted(out)


def pdf_for(stem):
    p = CORPUS / f"{stem}.pdf"
    return p if p.exists() else next(CORPUS.glob(f"{stem}.*"))


def read_positions(engine):
    """Child process: every scanned page read by one engine, keeping its line boxes."""
    import pypdf
    sys.path.insert(0, str(REPO / "src"))
    o._set_engine(engine)
    from watchdog.pipeline import preprocess
    POS.mkdir(parents=True, exist_ok=True)
    out = {}
    with tempfile.TemporaryDirectory() as tmp:
        for stem, n in scanned_pages():
            reader = pypdf.PdfReader(str(pdf_for(stem)))
            w = pypdf.PdfWriter()
            w.add_page(reader.pages[n - 1])
            one = Path(tmp) / "p.pdf"
            with open(one, "wb") as f:
                w.write(f)
            res = preprocess.process_with_docling(one, force_ocr=True)
            tp = (res.get("text_positions") or {}).get("pages", {}).get("1") or {}
            out[f"{stem}|{n}"] = tp
            print(engine, stem[:25], n, len(tp.get("lines", [])), file=sys.stderr, flush=True)
    (POS / f"{engine}.json").write_text(json.dumps(out), encoding="utf-8")


PAGE_VERTICAL = False   # set per page in build(): a sideways page's lines run up the page


def vertical(box):
    """Whether text runs up the page. Decided for the whole page, from the shape of most of its
    line boxes: one box's shape misleads (a lone character is taller than wide, a short rotated
    word nearly square)."""
    return PAGE_VERTICAL


def page_is_vertical(lines):
    tall = sum(1 for _, (left, top, right, bottom), _ in lines if (bottom - top) > 1.5 * (right - left))
    return tall > len(lines) / 2


def same_line(a, b, same_engine=False):
    """Two engine lines are one physical line when they overlap across the reading direction by
    half the thinner one, and along it either overlap (two engines' boxes for the same words) or,
    for one engine's pieces of a line, sit a word-gap apart. A logo or seal beside the text
    overlaps it across but not along, so it stays apart."""
    (al, at, ar, ab), (bl, bt, br, bb) = a, b
    if vertical(a) != vertical(b):
        return False
    if vertical(a):
        across = min(ar, br) - max(al, bl)
        thin = min(ar - al, br - bl)
        along = min(ab, bb) - max(at, bt)
    else:
        across = min(ab, bb) - max(at, bt)
        thin = min(ab - at, bb - bt)
        along = min(ar, br) - max(al, bl)
    thick = max(ar - al, br - bl) if vertical(a) else max(ab - at, bb - bt)
    # a word-gap only between one engine's pieces of like height: a logo box is much taller
    gap = -thin if same_engine and thick <= 1.5 * thin else 0.0
    return thin > 0 and across >= 0.5 * thin and along > gap


def stacked(a, b):
    """Two boxes on different physical lines: they overlap across the reading direction by less
    than half the thinner one, however close or far apart they sit along it."""
    (al, at, ar, ab), (bl, bt, br, bb) = a, b
    if vertical(a) and vertical(b):
        across, thin = min(ar, br) - max(al, bl), min(ar - al, br - bl)
    else:
        across, thin = min(ab, bb) - max(at, bt), min(ab - at, bb - bt)
    return across < 0.5 * thin


def group(lines):
    """Union-find over (engine, box, text) lines. Two groups join only if no engine would then
    hold two stacked lines in one group: a tall box (a seal over several printed lines) would
    otherwise chain those lines into one."""
    parent = list(range(len(lines)))
    members = {i: [i] for i in range(len(lines))}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def compatible(gi, gj):
        for a in members[gi]:
            for b in members[gj]:
                if lines[a][0] == lines[b][0] and stacked(lines[a][1], lines[b][1]):
                    return False
        return True
    pairs = [(i, j) for i in range(len(lines)) for j in range(i + 1, len(lines))
             if same_line(lines[i][1], lines[j][1], lines[i][0] == lines[j][0])]
    # strongest overlaps first, so a tall box joins the line it overlaps most
    def strength(p):
        (_, at, _, ab), (_, bt, _, bb) = lines[p[0]][1], lines[p[1]][1]
        return -(min(ab, bb) - max(at, bt)) / max(1e-6, max(ab - at, bb - bt))
    for i, j in sorted(pairs, key=strength):
        gi, gj = find(i), find(j)
        if gi != gj and compatible(gi, gj):
            parent[gi] = gj
            members[gj] += members.pop(gi)
    return [[lines[k] for k in m] for m in members.values()]


def build():
    import pypdf
    import pypdfium2 as pdfium
    pos = {e: json.loads((POS / f"{e}.json").read_text(encoding="utf-8")) for e in VOTERS}
    (OUT / "crops").mkdir(parents=True, exist_ok=True)
    (OUT / "pages").mkdir(parents=True, exist_ok=True)
    short = {s: f"d{k + 1}" for k, s in enumerate(sorted({s for s, _ in scanned_pages()}))}
    items, settled, seen = [], {}, {}
    SC = 3.0
    with tempfile.TemporaryDirectory() as tmp:
        for stem, n in scanned_pages():
            key = f"{stem}|{n}"
            tps = {e: pos[e].get(key) or {} for e in VOTERS}
            W = next((tp["width"] for tp in tps.values() if tp.get("width")), 612.0)
            H = next((tp["height"] for tp in tps.values() if tp.get("height")), 792.0)
            lines = [(e, tuple(ln[:4]), ln[4]) for e, tp in tps.items() for ln in tp.get("lines", [])]
            global PAGE_VERTICAL
            PAGE_VERTICAL = page_is_vertical(lines)
            reader = pypdf.PdfReader(str(pdf_for(stem)))
            w = pypdf.PdfWriter()
            w.add_page(reader.pages[n - 1])
            one = Path(tmp) / "p.pdf"
            with open(one, "wb") as f:
                w.write(f)
            img = pdfium.PdfDocument(str(one))[0].render(scale=SC).to_pil().convert("RGB")
            sx, sy = img.width / W, img.height / H
            thumb = img.resize((img.width * 16 // 30, img.height * 16 // 30))
            page_img = f"pages/{short[stem]}-p{n}.jpg"
            thumb.save(OUT / page_img, quality=78)
            groups = group(lines)
            groups.sort(key=lambda g: (min(b[1][1] for b in g), min(b[1][0] for b in g)))
            for k, g in enumerate(groups):
                readings = {}
                for e in VOTERS:
                    mine = sorted((ln for ln in g if ln[0] == e),
                                  key=lambda ln: (ln[1][1] if vertical(ln[1]) else ln[1][0]))
                    if vertical(g[0][1]):   # text reading bottom to top
                        mine = sorted(mine, key=lambda ln: -ln[1][3])
                    readings[e] = " ".join(ln[2].strip() for ln in mine).strip()
                norm = {e: " ".join(o.words(t)) for e, t in readings.items()}
                left, top = min(b[1][0] for b in g), min(b[1][1] for b in g)
                right, bottom = max(b[1][2] for b in g), max(b[1][3] for b in g)
                sig = json.dumps([stem, n, readings], sort_keys=True)
                seen[sig] = seen.get(sig, 0) + 1
                sid = f"{short[stem]}-p{n}-" + hashlib.sha1(f"{sig}#{seen[sig]}".encode()).hexdigest()[:8]
                if len(set(norm.values())) == 1:
                    settled[sid] = {"doc": stem, "page": n, "text": readings[VOTERS[0]]}
                    continue
                pad = 6
                crop = img.crop((max(0, (left - pad) * sx), max(0, (top - pad) * sy),
                                 min(img.width, (right + pad) * sx), min(img.height, (bottom + pad) * sy)))
                if vertical((left, top, right, bottom)):
                    crop = crop.rotate(-90, expand=True)
                cname = f"crops/{sid}.jpg"
                crop.save(OUT / cname, quality=85)
                counts = Counter(norm.values())
                majority, c = counts.most_common(1)[0]
                default = next(readings[e] for e in VOTERS if norm[e] == majority) if c >= 2 else None
                items.append({"id": sid, "doc": stem, "page": n, "crop": cname, "image": page_img,
                              "box": {"x": left / W, "y": top / H, "w": (right - left) / W,
                                      "h": (bottom - top) / H},
                              "readings": readings, "default": default})
    (OUT / "lines.json").write_text(json.dumps({"spots": items}, ensure_ascii=False), encoding="utf-8")
    (OUT / "settled.json").write_text(json.dumps(settled, ensure_ascii=False), encoding="utf-8")
    print(f"{len(items)} lines to review, {len(settled)} settled, "
          f"{sum(1 for i in items if i['default'] is not None)} with a 2-of-3 default")


if STAGE == "read":
    read_positions(sys.argv[4])
elif STAGE == "all":
    for e in VOTERS:
        subprocess.run([sys.executable, __file__, str(REPO), str(OUT), "read", e], check=True)
    build()
else:
    build()
