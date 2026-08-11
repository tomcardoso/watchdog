#!/usr/bin/env python3
"""
Watchdog document preprocessor.

Usage:
    python3 preprocess.py <file_path> [--force-ocr | --no-force-ocr]

Outputs a single JSON object to stdout:
{
  "filename": str,
  "sha256": str,
  "page_count": int,
  "pages": [{"page": int, "markdown": str}, ...],
  "metadata": {
    "ocr_used": bool,         # OCR was forced on at least one page
    "garbled_detected": bool, # at least one page's text layer read as junk
    "source_type": "direct_text" | "docling",
    "chunked": bool,          # true when the PDF was split for parallel processing
    "ocr_pages": [int]        # 1-indexed pages OCR'd, present only when OCR was
                              # page-scoped — i.e. some pages but not all (#605)
  },
  "file_metadata": dict       # embedded file properties (PDF/Office/EXIF/AV tags) — file-intrinsic
                              # claims the file makes about itself, a sibling of "metadata" above
                              # (which holds pipeline-asserted processing facts), see file_metadata.py
}

Exits non-zero on unrecoverable error; writes error JSON to stdout:
  {"error": str}
"""

import argparse
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

# alphanumeric+space ratio below which text is considered garbled by the character-class
# signal alone. Lowered from 0.75 (#580/#597): 0.75 was too aggressive for tables and
# financial documents — exactly what this tool reads most — and is now only one of three
# signals combined by is_page_garbled(), not a standalone verdict, so a lower (more easily
# tripped) value here no longer means a lower bar for forcing OCR. See DECISIONS D189.
GARBLED_THRESHOLD = 0.6
WORD_SHAPE_THRESHOLD = 0.3  # fraction of tokens that must look word-like, below which text reads as garbled
PAGE_GARBLE_VOTES_REQUIRED = 2  # of the signals in page_garble_signals(), how many must fire
CHUNK_SIZE = 40             # pages per chunk when splitting large PDFs
CHUNK_TIMEOUT = 300         # seconds per chunk subprocess


def _perf_cpu_count() -> int:
    """Performance core count on Apple Silicon; total core count everywhere else."""
    try:
        r = subprocess.run(
            ["sysctl", "-n", "hw.perflevel0.logicalcpu"],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode == 0:
            n = int(r.stdout.strip())
            if n > 0:
                return n
    except Exception:
        pass
    return os.cpu_count() or 4


CHUNK_WORKERS = max(2, _perf_cpu_count() // 2)

DIRECT_TEXT_SUFFIXES = {".txt", ".csv", ".md"}

DOCLING_SUFFIXES = {
    ".pdf", ".docx", ".pptx", ".xlsx",
    ".html", ".xhtml",
    ".xml",
    ".asciidoc", ".adoc",
    ".tex",
    ".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".webp",
    ".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac",
    ".mp4", ".avi", ".mov",
    ".vtt",
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def is_garbled(text: str) -> bool:
    """Character-class signal: True if too few characters are alphanumeric or
    whitespace to be real prose. One vote among several in page_garble_signals()
    — see D189/#597 for why this alone is no longer enough to force OCR."""
    if not text.strip():
        return False
    readable = sum(1 for c in text if c.isalnum() or c.isspace())
    return (readable / len(text)) < _config_get("garbled_threshold", GARBLED_THRESHOLD)


# Deliberately Unicode-aware (`\w`, not `[A-Za-z0-9]`): an accented or non-Latin script is
# not evidence of garbling, and an ASCII-only class would score French, German, Japanese or
# Arabic body text as symbol soup — turning this signal into a standing vote against every
# document that isn't in English, which is exactly the #580 false positive in a new place.
_WORD_TOKEN_RE = re.compile(r"^\w[\w'\-.,/]*$", re.UNICODE)
# Stripped from each token's edges before matching, so ordinary sentence punctuation (including
# CJK's full-width stops and the quotation marks Word substitutes) doesn't disqualify the word
# it's attached to. A token that is *only* punctuation strips to empty and is not counted at all.
_TOKEN_EDGE_PUNCT = ".,;:!?\"'()[]{}<>«»“”‘’。、—–"


def word_shape_ratio(text: str) -> "float | None":
    """Fraction of whitespace-delimited tokens that look like a real word or
    number (starts with a letter or digit in any script, then letters/digits/-'./,
    only) rather than a run of symbols or dot leaders. None when there are no
    tokens at all — including a page of nothing but punctuation, where this
    signal abstains rather than voting on no evidence."""
    tokens = [t.strip(_TOKEN_EDGE_PUNCT) for t in text.split()]
    tokens = [t for t in tokens if t]
    if not tokens:
        return None
    word_like = sum(1 for tok in tokens if _WORD_TOKEN_RE.match(tok))
    return word_like / len(tokens)


def is_word_shape_garbled(text: str) -> bool:
    """Word-shape signal: True if too few tokens look word-shaped to be real
    prose. Catches genuine symbol soup while passing content that the
    character-ratio signal alone misreads — a dot-leader table of contents is
    heavy on '.' characters but its actual words ("Overview", "Review of
    fiscal 2019-2020") still read as words (#580/#597)."""
    if not text.strip():
        return False
    ratio = word_shape_ratio(text)
    if ratio is None:
        return False
    return ratio < WORD_SHAPE_THRESHOLD


# Icon/dingbat font families: their glyphs are symbols (bullets, checkmarks), not
# characters, so an absent Unicode mapping there says nothing about whether the page's
# actual body text is readable. Matched against /BaseFont with any subset-tag prefix
# (e.g. "GFOLJD+SymbolMT") still intact.
_DINGBAT_FONT_RE = re.compile(r"wingding|dingbat|symbol|webding", re.IGNORECASE)


def pdf_page_missing_font_cmap(page) -> bool:
    """Font-CMap signal: True if the page declares a composite (/Type0) font
    with no /ToUnicode CMap — the extractor is recovering glyph codes with no
    reliable path back to characters. Tests a cause rather than a symptom, so
    when it fires it's close to authoritative; when it doesn't, it says
    nothing (most documents' simple fonts rely on a named encoding like
    WinAnsiEncoding instead, which needs no ToUnicode to decode reliably).

    Takes a pypdf-style page object (duck-typed: anything with `.get()` on
    itself and its nested "/Resources"/"/Font" entries works, so tests can
    pass plain dicts without constructing a real PDF) rather than page text,
    since this is a property of the page's font resources, not what was
    extracted from them (#597).
    """
    try:
        resources = page.get("/Resources")
        fonts = resources.get("/Font") if resources else None
        if not fonts:
            return False
        for font_ref in fonts.values():
            font = font_ref.get_object() if hasattr(font_ref, "get_object") else font_ref
            if font.get("/Subtype") != "/Type0":
                continue
            if "/ToUnicode" in font:
                continue
            if _DINGBAT_FONT_RE.search(str(font.get("/BaseFont", ""))):
                continue
            return True
        return False
    except Exception:
        return False


@dataclass
class PageSample:
    """One sampled PDF page's raw inputs to the garble ensemble.

    `missing_font_cmap` is computed once, at extraction time, from the real
    pypdf Page object — the ensemble itself (page_garble_signals, below) works
    from this plain, serializable record instead, so it stays testable without
    constructing a PDF (#597).
    """
    text: str
    missing_font_cmap: bool


def page_garble_signals(sample: PageSample) -> dict:
    """Each independent garble signal's verdict for one sampled page, kept
    separate from the combination rule (is_page_garbled) so each heuristic can
    be reasoned about and tested on its own (#597)."""
    return {
        "char_ratio": is_garbled(sample.text),
        "word_shape": is_word_shape_garbled(sample.text),
        "font_cmap": sample.missing_font_cmap,
    }


def is_page_garbled(sample: PageSample) -> bool:
    """Ensemble verdict for one page: garbled only if at least
    PAGE_GARBLE_VOTES_REQUIRED of the independent signals agree.

    Requiring agreement (not any-signal-fires) is deliberate: a false positive
    here is silently destructive — #580 showed OCR flattening a clean
    reconciliation table's headers into an unusable form, and nothing
    downstream can tell that happened. A false negative feeds the model
    imperfect text instead, which is also bad, but tends to be visible in the
    output rather than silently destroying something that was fine. See D189.
    """
    signals = page_garble_signals(sample)
    return sum(signals.values()) >= PAGE_GARBLE_VOTES_REQUIRED


def pdf_page_count(path: Path) -> int:
    """Return the number of pages in a PDF, or 0 on failure."""
    try:
        import pypdf
        return len(pypdf.PdfReader(str(path)).pages)
    except Exception:
        return 0


def pdf_page_samples(path: Path) -> list[PageSample]:
    """One PageSample per page of a PDF, using pypdf.

    Every page, not the first few (#605): a 3-page sample can say nothing about
    page 150, and the OCR decision is now made per page. Affordable because both
    inputs are local and cheap — measured at roughly 100 pages/second across the
    benchmark corpus, against Docling's seconds *per page*.

    Kept per-page rather than joined into one blob: front matter (a cover, a
    dot-leader table of contents) is systematically unrepresentative of body
    text, and blending it with clean pages before scoring lets one bad page
    drag the whole sample below threshold (#580). The font-CMap signal is
    computed here, against the real pypdf Page object, while it's available
    (#597) — PageSample carries the result forward as a plain bool.

    A page whose text extraction raises is recorded as having no text rather
    than aborting the whole document: under a per-page decision one unreadable
    page costs OCR on that page, where before it cost the entire document.
    """
    try:
        import pypdf
        reader = pypdf.PdfReader(str(path))
        samples = []
        for page in reader.pages:
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            samples.append(
                PageSample(text=text, missing_font_cmap=pdf_page_missing_font_cmap(page))
            )
        return samples
    except Exception:
        return []


def pdf_extract_pages(src: Path, indices: list[int]) -> Path:
    """Write the given 0-indexed pages of src to a temp PDF. Caller must delete.

    Takes an explicit page list rather than a range (#605): the pages needing
    OCR are not necessarily contiguous, and pulling them into one slice is what
    keeps a mixed document to two Docling passes instead of one per run.
    """
    import pypdf
    reader = pypdf.PdfReader(str(src))
    writer = pypdf.PdfWriter()
    for i in indices:
        if 0 <= i < len(reader.pages):
            writer.add_page(reader.pages[i])
    fd, tmp_str = tempfile.mkstemp(suffix=".pdf")
    tmp = Path(tmp_str)
    try:
        with os.fdopen(fd, "wb") as f:
            writer.write(f)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return tmp


def process_direct_text(path: Path, encoding_errors: str = "replace") -> dict:
    text = path.read_text(encoding="utf-8", errors=encoding_errors)
    return {
        "filename": path.name,
        "sha256": sha256_file(path),
        "page_count": 1,
        "pages": [{"page": 1, "markdown": text}],
        "metadata": {"ocr_used": False, "garbled_detected": False,
                     "source_type": "direct_text", "chunked": False},
    }


def pdf_preprocess(src: Path) -> "Path | None":
    """Strip encryption (qpdf) + re-render (Ghostscript) a problem PDF.

    Returns a cleaned temp file path, or None if unavailable/failed.
    Caller is responsible for deleting the returned file.
    """
    fd, tmp_str = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    tmp = Path(tmp_str)
    mid = src

    fd2, qpdf_tmp_str = tempfile.mkstemp(suffix=".pdf")
    os.close(fd2)
    qpdf_tmp = Path(qpdf_tmp_str)
    preprocess_timeout = _config_get("preprocess_timeout", 120)
    try:
        r = subprocess.run(
            ["qpdf", "--decrypt", "--no-warn", str(src), str(qpdf_tmp)],
            capture_output=True,
            timeout=preprocess_timeout,
        )
        if r.returncode == 0 and qpdf_tmp.exists():
            mid = qpdf_tmp

        r = subprocess.run(
            ["gs", "-dBATCH", "-dNOPAUSE", "-dSAFER", "-sDEVICE=pdfwrite",
             "-dCompatibilityLevel=1.4", f"-sOutputFile={tmp}", str(mid)],
            capture_output=True,
            timeout=preprocess_timeout,
        )
    finally:
        if mid != src and mid.exists():
            mid.unlink()

    if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
        return tmp
    if tmp.exists():
        tmp.unlink()
    return None


_config_cache: dict | None = None


def _reset_config_cache() -> None:
    global _config_cache
    _config_cache = None


def _config_get(key: str, default):
    """Read ~/.watchdog/config.json once per process, then serve from cache."""
    global _config_cache
    if _config_cache is None:
        try:
            _config_cache = json.loads((Path.home() / ".watchdog" / "config.json").read_text())
        except Exception:
            _config_cache = {}
    return _config_cache.get(key, default)


def _config_force(key: str, value) -> None:
    """Override a config value for this process, loading config first if needed."""
    _config_get(key, None)
    _config_cache[key] = value  # type: ignore[index]


def _ocr_languages() -> list[str]:
    return _config_get("ocr_languages", [])


def _make_tesseract_opts(force_ocr: bool):
    """Return TesseractOcrOptions if tesserocr is importable, else OcrAutoOptions."""
    try:
        import tesserocr  # noqa: F401
        from docling.datamodel.pipeline_options import TesseractOcrOptions
        return TesseractOcrOptions(force_full_page_ocr=force_ocr)
    except ImportError:
        from docling.datamodel.pipeline_options import OcrAutoOptions
        return OcrAutoOptions(force_full_page_ocr=force_ocr)


def build_converter(force_ocr: bool):
    """Build a Docling DocumentConverter with the configured OCR engine.

    Engine selection (auto mode):
      1. Apple Vision (macOS only, requires ocrmac) — fast, hardware-accelerated
      2. Tesseract (if system binary found) — accurate on document text
      3. EasyOCR (OcrAutoOptions) — universal fallback, no system deps
    """
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.pipeline_options import PdfPipelineOptions, OcrAutoOptions
    from docling.datamodel.base_models import InputFormat

    engine    = _config_get("ocr_engine", "auto")
    do_tables = _config_get("table_structure", True)
    ocr_opts  = None

    if engine in ("auto", "apple_vision"):
        if sys.platform == "darwin":
            try:
                import ocrmac as _ocrmac  # noqa: F401
                from docling.datamodel.pipeline_options import OcrMacOptions
                ocr_opts = OcrMacOptions(lang=_ocr_languages(), force_full_page_ocr=force_ocr)
            except Exception:
                if engine == "apple_vision":
                    sys.exit("Error: apple_vision OCR requires macOS and the ocrmac package.")
        elif engine == "apple_vision":
            sys.exit("Error: apple_vision OCR is only available on macOS.")

    if ocr_opts is None:
        if engine == "easyocr":
            ocr_opts = OcrAutoOptions(force_full_page_ocr=force_ocr)
        elif engine == "rapidocr":
            from docling.datamodel.pipeline_options import RapidOcrOptions
            ocr_opts = RapidOcrOptions(force_full_page_ocr=force_ocr)
        else:  # auto or tesseract
            ocr_opts = _make_tesseract_opts(force_ocr)

    pipeline_options = PdfPipelineOptions(
        do_ocr=True,
        do_table_structure=do_tables,
        ocr_options=ocr_opts,
    )
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
    )


def _run_slice_subprocess(slice_path: Path, page_numbers: list[int], force_ocr: bool) -> dict:
    """Process one slice PDF in a subprocess and return results renumbered to
    the original document's pages.

    The child is told the verdict rather than left to re-derive it (#605):
    pdf_extract_pages rewrites the slice through pypdf, which need not preserve
    the font resources the CMap signal reads, so a child scoring the slice for
    itself could reach a different answer than the parent reached on the intact
    file. The parent has already scored every page; its decision is the one
    that counts.
    """
    cmd = [sys.executable, "-m", "watchdog.pipeline.preprocess", str(slice_path),
           "--force-ocr" if force_ocr else "--no-force-ocr"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=_config_get("chunk_timeout", CHUNK_TIMEOUT))
        if not r.stdout.strip():
            return {"error": f"Empty output from chunk subprocess: {r.stderr[:200]}"}
        result = json.loads(r.stdout)
        if "error" in result:
            return result
        # Renumber to position in the original document. A page number the slice
        # can't account for is a bug, not a page to guess at: a silently wrong
        # page number is worse than a failed file, which at least gets retried.
        for page in result.get("pages", []):
            index = page["page"] - 1
            if not 0 <= index < len(page_numbers):
                return {"error": f"Slice returned page {page['page']} of {len(page_numbers)}"}
            page["page"] = page_numbers[index] + 1
        return result
    except subprocess.TimeoutExpired:
        timeout = _config_get("chunk_timeout", CHUNK_TIMEOUT)
        return {"error": f"Chunk timed out after {timeout}s"}
    except Exception as e:
        return {"error": str(e)}


def process_pdf_slices(path: Path, slices: list[tuple[list[int], bool]]) -> dict:
    """Convert each slice in a parallel subprocess and merge into one page list.

    Returns {"pages": [...]} in page order, or {"error": ...}. Metadata is the
    caller's to assemble: the parent made the OCR decision, so a child's report
    of what it did says nothing the parent doesn't already know (#605).
    """
    chunk_workers = _config_get("chunk_workers", CHUNK_WORKERS)
    if chunk_workers == "auto":
        chunk_workers = CHUNK_WORKERS

    def process_one(pages: list[int], force: bool) -> dict:
        try:
            slice_path = pdf_extract_pages(path, pages)
        except Exception as e:
            return {"error": f"Failed to extract pages {_page_range(pages)}: {e}"}
        try:
            return _run_slice_subprocess(slice_path, pages, force)
        finally:
            if slice_path.exists():
                slice_path.unlink()

    results: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=chunk_workers) as pool:
        futures = {pool.submit(process_one, pages, force): n
                   for n, (pages, force) in enumerate(slices)}
        for future in as_completed(futures):
            results[futures[future]] = future.result()

    # Any failed slice fails the whole document — a silent page gap is worse than
    # a failed file, since a failed file gets retried (#251).
    all_pages, failed = [], []
    for n, (pages, _force) in enumerate(slices):
        r = results[n]
        if "error" in r:
            failed.append(f"pages {_page_range(pages)}: {r['error']}")
            continue
        all_pages.extend(r.get("pages", []))

    if failed:
        return {"error": f"Chunk(s) failed: {'; '.join(failed)}"}

    # Sorted, not concatenated: slices are grouped by OCR verdict, so their
    # pages interleave in the original document.
    return {"pages": sorted(all_pages, key=lambda p: p["page"])}


def _page_range(pages: list[int]) -> str:
    """Human 1-indexed label for a slice's pages, for error messages."""
    if not pages:
        return "(none)"
    if pages == list(range(pages[0], pages[-1] + 1)):
        return f"{pages[0] + 1}-{pages[-1] + 1}"
    return f"{pages[0] + 1}-{pages[-1] + 1} ({len(pages)} pages)"


def _docling_result(path: Path, pages: list[dict], page_count: int, *,
                    ocr_used: bool, garbled: bool, ocr_pages: list[int] | None = None,
                    chunk_count: int | None = None) -> dict:
    metadata = {
        "ocr_used": ocr_used,
        "garbled_detected": garbled,
        "source_type": "docling",
        "chunked": chunk_count is not None,
    }
    if chunk_count is not None:
        metadata["chunk_count"] = chunk_count
    # Named only when OCR was page-scoped (#605) — that is the one case the
    # booleans above can't describe, since `ocr_used` alone can't tell "OCR'd
    # two scanned inserts" from "OCR'd all 202 pages".
    if ocr_pages:
        metadata["ocr_pages"] = [i + 1 for i in ocr_pages]
    return {
        "filename": path.name,
        "sha256": sha256_file(path),
        "page_count": page_count,
        "pages": pages,
        "metadata": metadata,
    }


_PAGE_BREAK = "\n\n<!-- page-break -->\n\n"


def _markdown_pages(doc) -> list[dict]:
    """Export a Docling document to per-page markdown using the native API."""
    try:
        from docling_core.types.doc.document import ContentLayer, ImageRefMode
        layers     = {ContentLayer.BODY, ContentLayer.FURNITURE}
        # Images become a "[image]" placeholder, never embedded base64: the extraction prompt is
        # a text field, so an embedded data URI would reach the model as text, not vision — pure
        # token cost with no visual gain. Image-as-evidence is handled by an on-demand page
        # render to a vision model instead (#183).
        image_mode = ImageRefMode.PLACEHOLDER
    except ImportError:
        layers     = None
        image_mode = None

    kwargs = dict(
        page_break_placeholder=_PAGE_BREAK,
        image_placeholder="[image]",
        traverse_pictures=True,
        included_content_layers=layers,
    )
    if image_mode is not None:
        kwargs["image_mode"] = image_mode

    # Docling HTML-escapes "&" to "&amp;" in some cells/paragraphs even though markdown doesn't
    # require it there — a converter artifact, not something the source document actually
    # contains (#560: it broke quote-locator matching on any "X & Y" phrase, and would otherwise
    # show up verbatim in both the model's extraction input and the rendered morgue note).
    # Neither `_PAGE_BREAK` nor the `[image]` placeholder contain "&", so this can't touch them.
    md = html.unescape(doc.export_to_markdown(**kwargs))

    parts = [p.strip() for p in md.split(_PAGE_BREAK)]
    pages = [
        {"page": i + 1, "markdown": part}
        for i, part in enumerate(parts)
        if part
    ]
    return pages or [{"page": 1, "markdown": md.strip()}]


def pdf_ocr_plan(path: Path, total_pages: int) -> tuple[list[int], list[int]]:
    """Return (force_pages, garbled_pages) as 0-indexed page numbers.

    Two levels of combination, kept distinct (#597): is_page_garbled() combines
    several independent signals into a verdict for one page; this function
    turns those per-page verdicts into a plan for the document. Before #605 it
    collapsed them into two document-wide booleans, so one sampled page decided
    for all of them; now the resolution survives.

    A page is forced when it has **no text layer** or when the text layer it
    does have reads garbled. Both end in OCR but for opposite reasons, and only
    the second is reported as `garbled_detected`.

    Forcing a page with no text layer is free insurance rather than a cost:
    force_full_page_ocr is destructive strictly in proportion to how good the
    existing text layer was (Docling discards the programmatic cells outright,
    see D192), so on a page that has none there is nothing to destroy. It buys
    cover for the case Docling's own OCR drops on the floor — a page whose
    content is under its `bitmap_area_threshold` (5% of the page), e.g. text
    drawn as vector paths, or a mostly-blank page carrying one small scanned
    annotation like an initialled change or a margin note.

    If the text layer can't be read at all, every page is forced: that is the
    same verdict the document-wide `empty` path reached before #605.
    """
    samples = pdf_page_samples(path)
    if len(samples) != total_pages:
        return list(range(total_pages)), []

    force_pages, garbled_pages = [], []
    for i, sample in enumerate(samples):
        if not sample.text.strip():
            force_pages.append(i)
        elif is_page_garbled(sample):
            force_pages.append(i)
            garbled_pages.append(i)
    return force_pages, garbled_pages


def _ocr_slices(total_pages: int, force_pages: list[int], chunk_size: int,
                force_all: bool = False) -> list[tuple[list[int], bool]]:
    """Group pages by OCR verdict, then split each group into slices of at most
    chunk_size pages. Returns (0-indexed page numbers, force_ocr) per slice.

    Grouped by verdict rather than into contiguous runs (#605). Slicing builds a
    temp PDF either way, so contiguity buys nothing — and grouping bounds the
    pathological case: a document alternating scanned and born-digital pages
    would otherwise become one slice, and one Docling subprocess start-up, per
    page. By class it is two.

    A document whose pages all agree yields one group of contiguous pages, which
    is exactly the chunking that existed before this change.
    """
    forced = set(force_pages)
    if force_all or not forced:
        groups = [(list(range(total_pages)), bool(force_all))]
    else:
        groups = [
            ([i for i in range(total_pages) if i not in forced], False),
            (sorted(forced), True),
        ]

    return [
        (pages[i:i + chunk_size], force)
        for pages, force in groups
        for i in range(0, len(pages), chunk_size)
    ]


def process_with_docling(path: Path, force_ocr: bool = False, detect: bool = True) -> dict:
    """Convert a document to per-page markdown.

    force_ocr forces OCR on every page and skips detection; detect=False says
    the caller has already decided and no page needs it (see
    _run_slice_subprocess).
    """
    is_pdf = path.suffix.lower() == ".pdf"
    total_pages = pdf_page_count(path) if is_pdf else 0
    force_pages: list[int] = []
    garbled_pages: list[int] = []

    # For PDFs: score every page to decide which of them need OCR (#605).
    if is_pdf and detect and not force_ocr:
        if total_pages == 0:
            # pypdf can't read the file at all, so nothing can be said about its
            # text layer, page by page or otherwise. Same verdict the
            # document-wide `empty` path reached before #605.
            force_ocr = True
        else:
            force_pages, garbled_pages = pdf_ocr_plan(path, total_pages)
            if len(force_pages) == total_pages:
                force_ocr, force_pages = True, []   # every page — no need to split

    # PDFs needing more than one conversion: mixed verdicts (force_pages), or
    # simply too large for one pass. Split into slices and run them in parallel
    # subprocesses; no docling needed in the parent.
    chunk_size = _config_get("chunk_size", CHUNK_SIZE)
    if is_pdf and total_pages and (force_pages or total_pages > chunk_size):
        slices = _ocr_slices(total_pages, force_pages, chunk_size, force_all=force_ocr)
        result = process_pdf_slices(path, slices)
        if "error" in result:
            # Fallback: clean the whole file with qpdf/gs and retry, mirroring
            # the small-PDF fallback path below.
            try:
                cleaned = pdf_preprocess(path)
            except Exception:
                cleaned = None
            if cleaned is None:
                return result
            try:
                # Re-decide against the cleaned file: Ghostscript rewrites the
                # text layer and can change the page count, so page indices from
                # the original may not describe this file at all.
                cleaned_pages = pdf_page_count(cleaned) or total_pages
                if detect and not force_ocr:
                    force_pages, garbled_pages = pdf_ocr_plan(cleaned, cleaned_pages)
                    if len(force_pages) == cleaned_pages:
                        force_ocr, force_pages = True, []
                total_pages = cleaned_pages
                retry_slices = _ocr_slices(total_pages, force_pages, chunk_size,
                                           force_all=force_ocr)
                result = process_pdf_slices(cleaned, retry_slices)
            finally:
                cleaned.unlink(missing_ok=True)
            if "error" in result:
                return result
            slices = retry_slices
        return _docling_result(
            path, result["pages"], total_pages,
            ocr_used=force_ocr or bool(force_pages),
            garbled=bool(garbled_pages),
            ocr_pages=force_pages,
            chunk_count=len(slices),
        )

    # Small PDFs and all other formats: single Docling conversion
    try:
        from docling.document_converter import DocumentConverter
    except ImportError:
        return {"error": "Docling is not installed. Run: pip install docling"}
    if is_pdf:
        try:
            converter = build_converter(force_ocr)
        except Exception as e:
            return {"error": f"Failed to build converter: {e}"}
    else:
        converter = DocumentConverter()

    try:
        result = converter.convert(str(path))
    except Exception as first_err:
        if not is_pdf:
            return {"error": f"Docling conversion failed: {first_err}"}
        # Fallback: decrypt + re-render, then retry
        try:
            cleaned = pdf_preprocess(path)
        except Exception:
            cleaned = None
        if cleaned is None:
            return {"error": f"Docling conversion failed: {first_err}"}
        try:
            result = converter.convert(str(cleaned))
        except Exception as second_err:
            return {"error": f"Docling conversion failed after preprocessing: {second_err}"}
        finally:
            if cleaned.exists():
                cleaned.unlink()

    doc = result.document
    pages = _markdown_pages(doc)
    page_count = max(p["page"] for p in pages)

    return _docling_result(path, pages, page_count,
                           ocr_used=force_ocr, garbled=bool(garbled_pages))


def main() -> None:
    parser = argparse.ArgumentParser(description="Watchdog document preprocessor")
    parser.add_argument("file", help="Path to the document")
    ocr = parser.add_mutually_exclusive_group()
    ocr.add_argument("--force-ocr", action="store_true",
                     help="Force full-page OCR on every page")
    ocr.add_argument("--no-force-ocr", action="store_true",
                     help="Skip garble detection and never force OCR — for a slice whose "
                          "verdict the parent process already made")
    parser.add_argument("--chunk-workers", type=int, metavar="N",
                        help="Override chunk_workers config for this run")
    args = parser.parse_args()

    if args.chunk_workers is not None:
        _config_force("chunk_workers", args.chunk_workers)

    path = Path(args.file).resolve()
    if not path.exists():
        print(json.dumps({"error": f"File not found: {path}"}))
        sys.exit(1)

    suffix = path.suffix.lower()



    if suffix in DIRECT_TEXT_SUFFIXES:
        result = process_direct_text(path)
    elif suffix in DOCLING_SUFFIXES:
        result = process_with_docling(path, force_ocr=args.force_ocr,
                                      detect=not args.no_force_ocr)
    else:
        try:
            result = process_direct_text(path, encoding_errors="strict")
            if is_garbled(result["pages"][0]["markdown"]):
                result["metadata"]["garbled_detected"] = True
        except UnicodeDecodeError:
            result = process_with_docling(path, force_ocr=args.force_ocr,
                                      detect=not args.no_force_ocr)

    if "error" in result:
        print(json.dumps(result))
        sys.exit(1)

    # Embedded file metadata (#369) — always read from the ORIGINAL source path, never a
    # Ghostscript-cleaned or chunk temp file: pdf_preprocess() re-renders problem PDFs through
    # Ghostscript, which strips DocumentInfo, so reading from a cleaned file would silently
    # return nothing.
    from watchdog.pipeline import file_metadata
    result["file_metadata"] = file_metadata.extract(path)

    # The corpus search index is built at ingest, not chew: write_vault embeds each
    # document's passages with a contextual prefix (title, type, the entities it names),
    # which only exists after extraction. See embed.add_document / DECISIONS D43.

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
