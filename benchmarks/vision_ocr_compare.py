"""Apple Vision OCR, read several ways over one PDF page — is language correction, or resolution,
what turns clear text into wrong text?

Not a pytest module — a standalone benchmark tool, same posture as `tokenizer_ratio.py`. macOS
only (Apple Vision). Run it with the app's own engine Python, so it uses the Docling and `ocrmac`
the app uses:

    ~/Library/Application\\ Support/Watchdog/engine/venv/bin/python \\
        benchmarks/vision_ocr_compare.py capture.pdf --expect "name@example.org"

(Add `PYTHONPATH=<checkout>/src` to read through a checkout's `pipeline/preprocess.py` rather
than the installed copy's.)

Why: a full-page screen capture, OCR forced and rendered at the OCR's scale since D352, still
read the email address in its footer with two letters wrong and a space after the @. RapidOCR
read the same 3x render correctly, so the render is legible. Vision's text request corrects what it reads against a language model
(`usesLanguageCorrection`, on by default; neither `ocrmac` nor Docling turns it off), which
plausibly "fixes" names, addresses and numbers that are not words. This measures it.

Two groups of readings, each with language correction on and off:

`pipeline` — the page converted exactly as Watchdog converts a page it OCRs:
`preprocess.process_with_docling(..., force_ocr=True)`, Watchdog's own OCR settings and Docling's
own render, with only the correction flag changed (by replacing `ocrmac`'s request builder, the one
place it is set). The chosen page is copied to a one-page PDF first, so every conversion is a single
in-process one.

`image` — Vision called directly on two pictures of the same page: the page rendered with pdfium
at Docling's OCR scale (3x, 216 DPI; close to, not identical with, Docling's own render), and the
page's largest embedded image at its own size, when it has one. A gap between these two that the
pipeline readings share says resolution matters; a gap between correction on and off says the
correction does.

For each reading it reports whether each `--expect` string appears exactly (spacing aside) and the
closest thing that does. `--out DIR` writes every reading's full text for diffing. `--langs` sets
Vision's recognition languages for every reading (`en-US,fr-FR`); without it each reading uses
what Watchdog would (the `ocr_languages` setting, empty by default, which leaves Vision's own).
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
import tempfile
from pathlib import Path

SCALE = 3.0   # Docling's OCR scale (OcrOptions.scale), 216 DPI


# ── Vision ───────────────────────────────────────────────────────────────────────────────────

def vision_read(image, *, correction: bool, langs: list[str] | None,
                level: str = "accurate") -> list[tuple[str, float, list[float]]]:
    """`ocrmac.text_from_image`'s request, with language correction set explicitly. Returns
    (text, confidence, [x, y, w, h]) per observation, as `ocrmac` does."""
    import objc
    import Vision
    from ocrmac.ocrmac import pil2buf

    with objc.autorelease_pool():
        req = Vision.VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLevel_(1 if level == "fast" else 0)
        req.setUsesLanguageCorrection_(correction)
        if langs:
            req.setRecognitionLanguages_(langs)
        handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(pil2buf(image), None)
        ret = handler.performRequests_error_([req], None)
        ok, err = ret if isinstance(ret, tuple) else (bool(ret), None)
        if not ok or err is not None:
            raise RuntimeError(f"Vision failed: {err}")
        out = []
        for r in req.results():
            box = r.boundingBox()
            out.append((r.text(), r.confidence(),
                        [box.origin.x, box.origin.y, box.size.width, box.size.height]))
        return out


def vision_text(image, *, correction: bool, langs: list[str] | None) -> str:
    """Observations top to bottom, one per line (Vision's origin is bottom left)."""
    obs = vision_read(image, correction=correction, langs=langs)
    obs.sort(key=lambda o: (-(o[2][1] + o[2][3]), o[2][0]))
    return "\n".join(o[0] for o in obs)


def patch_ocrmac(correction: bool, langs: list[str] | None) -> None:
    """Make every `ocrmac` request Docling builds use this correction setting (and these languages,
    when given). Docling's OcrMac model calls `ocrmac.OCR(...).recognize()`, which calls the module's
    `text_from_image` by name, so replacing that name reaches every call."""
    import ocrmac.ocrmac as om

    def text_from_image(image, recognition_level="accurate", language_preference=None,
                        confidence_threshold=0.0, detail=True):
        res = vision_read(image, correction=correction, langs=langs or language_preference,
                          level=recognition_level)
        res = [r for r in res if r[1] >= confidence_threshold]
        return res if detail else [r[0] for r in res]

    om.text_from_image = text_from_image


# ── Readings ─────────────────────────────────────────────────────────────────────────────────

def one_page_pdf(path: Path, page: int, tmp: Path) -> Path:
    import pypdf
    reader = pypdf.PdfReader(str(path))
    writer = pypdf.PdfWriter()
    writer.add_page(reader.pages[page - 1])
    out = tmp / "page.pdf"
    with open(out, "wb") as f:
        writer.write(f)
    return out


def pipeline_text(page_pdf: Path, *, correction: bool, langs: list[str] | None) -> str:
    from watchdog.pipeline import preprocess
    patch_ocrmac(correction, langs)
    if langs:
        preprocess._ocr_languages = lambda: langs
    result = preprocess.process_with_docling(page_pdf, force_ocr=True)
    if "error" in result:
        raise RuntimeError(result["error"])
    return "\n".join(p.get("markdown", "") for p in result["pages"])


def page_render(page_pdf: Path):
    import pypdfium2 as pdfium
    return pdfium.PdfDocument(str(page_pdf))[0].render(scale=SCALE).to_pil().convert("RGB")


def largest_image(page_pdf: Path):
    """The page's largest embedded image at its own size, with its resolution as placed on the
    page in DPI (None when the placement cannot be read), or None without one."""
    import pypdf
    page = pypdf.PdfReader(str(page_pdf)).pages[0]
    images = list(page.images)
    if not images:
        return None
    img = max(images, key=lambda i: i.image.width * i.image.height).image.convert("RGB")
    # The widest `a 0 0 d e f cm` before an image draw gives its placed width in points.
    content = page.get_contents().get_data().decode("latin-1", "replace")
    widths = [abs(float(m.group(1))) for m in re.finditer(r"(-?[\d.]+)\s+0\s+0\s+-?[\d.]+\s+-?[\d.]+\s+-?[\d.]+\s+cm", content)]
    dpi = round(img.width / max(widths) * 72) if widths else None
    return img, dpi


# ── Matching ─────────────────────────────────────────────────────────────────────────────────

def squash(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def closest(expected: str, text: str) -> tuple[str, float]:
    """The run of words in `text` most like `expected` (one more or fewer word than it has, since
    OCR splits and joins), with its similarity from 0 to 1."""
    words = text.split()
    n = len(expected.split())
    best, score = "", 0.0
    for size in {max(1, n - 1), n, n + 1}:
        for i in range(max(1, len(words) - size + 1)):
            cand = " ".join(words[i:i + size])
            r = difflib.SequenceMatcher(None, squash(expected), squash(cand)).ratio()
            if r > score:
                best, score = cand, r
    return best, score


# ── Main ─────────────────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--page", type=int, default=1, help="page to read, from 1 (default 1)")
    ap.add_argument("--expect", action="append", default=[], metavar="TEXT",
                    help="text the page shows; repeat for several")
    ap.add_argument("--langs", help="Vision recognition languages, comma-separated (en-US,fr-FR)")
    ap.add_argument("--only", choices=["pipeline", "image"], help="run one group of readings")
    ap.add_argument("--out", type=Path, help="folder to write each reading's full text to")
    args = ap.parse_args()
    if sys.platform != "darwin":
        sys.exit("Apple Vision runs only on macOS.")
    langs = [s.strip() for s in args.langs.split(",") if s.strip()] if args.langs else None

    readings: list[tuple[str, str]] = []
    with tempfile.TemporaryDirectory() as tmp:
        page_pdf = one_page_pdf(args.pdf, args.page, Path(tmp))
        if args.only != "image":
            for corr in (True, False):
                name = f"pipeline, correction {'on' if corr else 'off'}"
                print(f"reading: {name} …", file=sys.stderr)
                readings.append((name, pipeline_text(page_pdf, correction=corr, langs=langs)))
        if args.only != "pipeline":
            pictures = [(f"page rendered at {SCALE:g}x ({SCALE * 72:.0f} DPI)", page_render(page_pdf))]
            embedded = largest_image(page_pdf)
            if embedded:
                img, dpi = embedded
                pictures.append((f"embedded image, own size {img.width}x{img.height}"
                                 + (f" (~{dpi} DPI as placed)" if dpi else ""), img))
            for label, img in pictures:
                for corr in (True, False):
                    name = f"image: {label}, correction {'on' if corr else 'off'}"
                    print(f"reading: {name} …", file=sys.stderr)
                    readings.append((name, vision_text(img, correction=corr, langs=langs)))

    print(f"\n{args.pdf.name}, page {args.page}" + (f", languages {','.join(langs)}" if langs else ", Watchdog's languages"))
    for name, text in readings:
        print(f"\n{name} — {len(text):,} characters")
        for exp in args.expect:
            if squash(exp) in squash(text):
                print(f"  found      {exp!r}")
            else:
                best, score = closest(exp, text)
                print(f"  NOT FOUND  {exp!r}; closest {best!r} ({score:.0%} alike)")
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        for name, text in readings:
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            (args.out / f"{slug}.txt").write_text(text, encoding="utf-8")
        print(f"\nFull text of each reading in {args.out}")


if __name__ == "__main__":
    main()
