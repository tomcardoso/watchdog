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
  }
}

Exits non-zero on unrecoverable error; writes error JSON to stdout:
  {"error": str}
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

GARBLED_THRESHOLD = 0.75   # alphanumeric+space ratio below which text is considered garbled
CHUNK_SIZE = 40             # pages per chunk when splitting large PDFs
CHUNK_WORKERS = 4           # parallel subprocesses for chunked processing
CHUNK_TIMEOUT = 300         # seconds per chunk subprocess

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
    """Return True if the text layer is too symbol-heavy to be real prose."""
    if not text.strip():
        return False
    readable = sum(1 for c in text if c.isalnum() or c.isspace())
    return (readable / len(text)) < _config_get("garbled_threshold", GARBLED_THRESHOLD)


def pdf_page_count(path: Path) -> int:
    """Return the number of pages in a PDF, or 0 on failure."""
    try:
        import pypdf
        return len(pypdf.PdfReader(str(path)).pages)
    except Exception:
        return 0


def pdf_sample_text(path: Path) -> str:
    """Extract a small text sample from the first few PDF pages using pypdf."""
    try:
        import pypdf
        reader = pypdf.PdfReader(str(path))
        sample_pages = reader.pages[: min(3, len(reader.pages))]
        return " ".join(p.extract_text() or "" for p in sample_pages)
    except Exception:
        return ""


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


def process_direct_text(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
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
    try:
        r = subprocess.run(
            ["qpdf", "--decrypt", "--no-warn", str(src), str(qpdf_tmp)],
            capture_output=True,
        )
        if r.returncode == 0 and qpdf_tmp.exists():
            mid = qpdf_tmp

        r = subprocess.run(
            ["gs", "-dBATCH", "-dNOPAUSE", "-dSAFER", "-sDEVICE=pdfwrite",
             "-dCompatibilityLevel=1.4", f"-sOutputFile={tmp}", str(mid)],
            capture_output=True,
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


def _config_get(key: str, default):
    """Read ~/.watchdog/config.json once per process, then serve from cache."""
    global _config_cache
    if _config_cache is None:
        try:
            _config_cache = json.loads((Path.home() / ".watchdog" / "config.json").read_text())
        except Exception:
            _config_cache = {}
    return _config_cache.get(key, default)


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

    # Merge in page order; skip failed chunks but note them
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

    result = {
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

    if failed_chunks:
        result["metadata"]["failed_chunks"] = failed_chunks

    if not all_pages:
        return {"error": f"All chunks failed: {'; '.join(failed_chunks)}"}

    return result


_PAGE_BREAK = "\n\n<!-- page-break -->\n\n"


def _markdown_pages(doc) -> list[dict]:
    """Export a Docling document to per-page markdown using the native API."""
    try:
        from docling_core.types.doc.document import ContentLayer, ImageRefMode
        layers     = {ContentLayer.BODY, ContentLayer.FURNITURE}
        image_mode = (
            ImageRefMode.EMBEDDED if _config_get("embed_images", False)
            else ImageRefMode.PLACEHOLDER
        )
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

    md = doc.export_to_markdown(**kwargs)

    parts = [p.strip() for p in md.split(_PAGE_BREAK)]
    pages = [
        {"page": i + 1, "markdown": part}
        for i, part in enumerate(parts)
        if part
    ]
    return pages or [{"page": 1, "markdown": md.strip()}]


def process_with_docling(path: Path, force_ocr: bool = False) -> dict:
    try:
        from docling.document_converter import DocumentConverter
    except ImportError:
        return {"error": "Docling is not installed. Run: pip install docling"}

    is_pdf = path.suffix.lower() == ".pdf"
    garbled_detected = False

    # For PDFs: sample text layer to decide whether to force OCR
    if is_pdf and not force_ocr:
        sample = pdf_sample_text(path)
        if not sample.strip():
            force_ocr = True
        elif is_garbled(sample):
            garbled_detected = True
            force_ocr = True

    # Large PDFs: split into chunks and process in parallel
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
    parser.add_argument("--vault-path", metavar="PATH",
                        help="Vault directory — when set, pages are added to the embedding index")
    args = parser.parse_args()

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
            result = process_direct_text(path)
        except UnicodeDecodeError:
            result = process_with_docling(path, force_ocr=args.force_ocr)

    if "error" in result:
        print(json.dumps(result))
        sys.exit(1)

    if args.vault_path:
        try:
            from watchdog.pipeline.embed import add_document
            add_document(Path(args.vault_path), result["filename"], result["pages"])
        except Exception:
            pass  # embedding is best-effort; never fail the preprocess

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
