#!/usr/bin/env python3
"""
Watchdog document preprocessor.

Usage:
    python3 preprocess.py <file_path> [--force-ocr]

Outputs a single JSON object to stdout:
{
  "filename": str,
  "sha256": str,
  "page_count": int,
  "pages": [{"page": int, "markdown": str}, ...],
  "metadata": {
    "ocr_used": bool,
    "garbled_detected": bool,
    "source_type": "direct_text" | "docling",
    "chunked": bool           # true when large PDF was split for parallel processing
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


def pdf_sample_pages(path: Path) -> list[PageSample]:
    """Sample the first few PDF pages using pypdf, one PageSample per page.

    Kept per-page rather than joined into one blob: front matter (a cover, a
    dot-leader table of contents) is systematically unrepresentative of body
    text, and blending it with clean pages before scoring lets one bad page
    drag the whole sample below threshold (#580). The font-CMap signal is
    computed here, against the real pypdf Page object, while it's available
    (#597) — PageSample carries the result forward as a plain bool.
    """
    try:
        import pypdf
        reader = pypdf.PdfReader(str(path))
        sample_pages = reader.pages[: min(3, len(reader.pages))]
        return [
            PageSample(text=p.extract_text() or "", missing_font_cmap=pdf_page_missing_font_cmap(p))
            for p in sample_pages
        ]
    except Exception:
        return []


def pdf_extract_chunk(src: Path, start: int, end: int) -> Path:
    """Write pages [start, end) of src to a temp PDF. Caller must delete."""
    import pypdf
    reader = pypdf.PdfReader(str(src))
    writer = pypdf.PdfWriter()
    for i in range(start, min(end, len(reader.pages))):
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


def _run_chunk_subprocess(chunk_path: Path, page_offset: int, force_ocr: bool) -> dict:
    """Process a single chunk PDF in a subprocess and return adjusted results."""
    cmd = [sys.executable, "-m", "watchdog.pipeline.preprocess", str(chunk_path)]
    if force_ocr:
        cmd.append("--force-ocr")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=_config_get("chunk_timeout", CHUNK_TIMEOUT))
        if not r.stdout.strip():
            return {"error": f"Empty output from chunk subprocess: {r.stderr[:200]}"}
        result = json.loads(r.stdout)
        if "error" in result:
            return result
        # Shift page numbers by offset so they reflect position in the original document
        for page in result.get("pages", []):
            page["page"] += page_offset
        return result
    except subprocess.TimeoutExpired:
        timeout = _config_get("chunk_timeout", CHUNK_TIMEOUT)
        return {"error": f"Chunk timed out after {timeout}s"}
    except Exception as e:
        return {"error": str(e)}


def process_large_pdf(path: Path, force_ocr: bool, total_pages: int) -> dict:
    """Split a large PDF into chunk_size-page chunks and process in parallel."""
    chunk_size    = _config_get("chunk_size",    CHUNK_SIZE)
    chunk_workers = _config_get("chunk_workers", CHUNK_WORKERS)
    if chunk_workers == "auto":
        chunk_workers = CHUNK_WORKERS
    chunks = [
        (start, min(start + chunk_size, total_pages))
        for start in range(0, total_pages, chunk_size)
    ]

    chunk_results: dict[int, dict] = {}

    def process_one(start: int, end: int) -> tuple[int, dict]:
        try:
            chunk_path = pdf_extract_chunk(path, start, end)
        except Exception as e:
            return start, {"error": f"Failed to extract pages {start+1}-{end}: {e}"}
        try:
            return start, _run_chunk_subprocess(chunk_path, start, force_ocr)
        finally:
            if chunk_path.exists():
                chunk_path.unlink()

    with ThreadPoolExecutor(max_workers=chunk_workers) as pool:
        futures = {pool.submit(process_one, s, e): (s, e) for s, e in chunks}
        for future in as_completed(futures):
            start, result = future.result()
            chunk_results[start] = result

    # Merge in page order; any failed chunk fails the whole document — a silent
    # page gap is worse than a failed file, since a failed file gets retried (#251).
    all_pages = []
    failed_chunks = []
    garbled_detected = False
    ocr_used = force_ocr

    chunk_end = {s: e for s, e in chunks}
    for start in sorted(chunk_results.keys()):
        r = chunk_results[start]
        if "error" in r:
            failed_chunks.append(f"pages {start+1}-{chunk_end[start]}: {r['error']}")
            continue
        all_pages.extend(r.get("pages", []))
        if r.get("metadata", {}).get("garbled_detected"):
            garbled_detected = True
        if r.get("metadata", {}).get("ocr_used"):
            ocr_used = True

    if failed_chunks:
        return {"error": f"Chunk(s) failed: {'; '.join(failed_chunks)}"}

    return {
        "filename": path.name,
        "sha256": sha256_file(path),
        "page_count": total_pages,
        "pages": all_pages,
        "metadata": {
            "ocr_used": ocr_used,
            "garbled_detected": garbled_detected,
            "source_type": "docling",
            "chunked": True,
            "chunk_count": len(chunks),
        },
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


def pdf_garble_verdict(path: Path) -> tuple[bool, bool]:
    """Return (empty, garbled) for a PDF's sampled pages.

    Two levels of combination, kept distinct (#597): is_page_garbled() combines
    several independent signals into a verdict for one page; this function
    combines per-page verdicts into a verdict for the document.

    empty: none of the sampled pages have any extracted text (e.g. a fully
    scanned document). garbled: every non-empty sampled page reads as garbled
    by the page-level ensemble — judged per page, with blank pages excluded, so
    one unrepresentative page (a dot-leader table of contents, a cover) can't
    drag a document whose body text is fine into a garbled verdict (#580).
    """
    sampled_pages = [s for s in pdf_sample_pages(path) if s.text.strip()]
    if not sampled_pages:
        return True, False
    return False, all(is_page_garbled(s) for s in sampled_pages)


def process_with_docling(path: Path, force_ocr: bool = False) -> dict:
    is_pdf = path.suffix.lower() == ".pdf"
    garbled_detected = False

    # For PDFs: sample text layer to decide whether to force OCR.
    if is_pdf and not force_ocr:
        empty, garbled = pdf_garble_verdict(path)
        if empty:
            force_ocr = True
        elif garbled:
            garbled_detected = True
            force_ocr = True

    # Large PDFs: split into chunks and process in parallel (no docling needed in parent)
    if is_pdf:
        total_pages = pdf_page_count(path)
        if total_pages > _config_get("chunk_size", CHUNK_SIZE):
            large_result = process_large_pdf(path, force_ocr, total_pages)
            if "error" not in large_result:
                return large_result
            # Fallback: clean the whole file with qpdf/gs and retry chunking,
            # mirroring the small-PDF fallback path below.
            try:
                cleaned = pdf_preprocess(path)
            except Exception:
                cleaned = None
            if cleaned is None:
                return large_result
            try:
                cleaned_pages = pdf_page_count(cleaned) or total_pages
                retry = process_large_pdf(cleaned, force_ocr, cleaned_pages)
            finally:
                cleaned.unlink(missing_ok=True)
            if "error" not in retry:
                retry["filename"] = path.name
                retry["sha256"] = sha256_file(path)
            return retry

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

    return {
        "filename": path.name,
        "sha256": sha256_file(path),
        "page_count": page_count,
        "pages": pages,
        "metadata": {
            "ocr_used": force_ocr,
            "garbled_detected": garbled_detected,
            "source_type": "docling",
            "chunked": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Watchdog document preprocessor")
    parser.add_argument("file", help="Path to the document")
    parser.add_argument("--force-ocr", action="store_true", help="Force full-page OCR")
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
        result = process_with_docling(path, force_ocr=args.force_ocr)
    else:
        try:
            result = process_direct_text(path, encoding_errors="strict")
            if is_garbled(result["pages"][0]["markdown"]):
                result["metadata"]["garbled_detected"] = True
        except UnicodeDecodeError:
            result = process_with_docling(path, force_ocr=args.force_ocr)

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
