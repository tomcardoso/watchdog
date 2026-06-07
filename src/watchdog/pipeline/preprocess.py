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
  "text": str,               # full document text
  "pages": [{"page": int, "text": str}, ...],
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
    return (readable / len(text)) < GARBLED_THRESHOLD


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
        "text": text,
        "pages": [{"page": 1, "text": text}],
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
    if mid != src and mid.exists():
        mid.unlink()

    if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
        return tmp
    if tmp.exists():
        tmp.unlink()
    return None


_DEFAULT_OCR_LANGUAGES = ["en-US", "fr-FR", "es-ES", "pt-BR", "de-DE", "it-IT", "nl-NL"]


def _ocr_languages() -> list[str]:
    """Return OCR language list from ~/.watchdog/config.json, or a broad international default."""
    config_path = Path.home() / ".watchdog" / "config.json"
    try:
        config = json.loads(config_path.read_text())
        return config.get("ocr_languages", _DEFAULT_OCR_LANGUAGES)
    except Exception:
        return _DEFAULT_OCR_LANGUAGES


def build_converter(force_ocr: bool):
    """Build a Docling DocumentConverter for PDFs with the best available OCR engine.

    Engine priority:
      1. Apple Vision (macOS only, ocrmac package) — fastest
      2. EasyOCR (OcrAutoOptions) — universal fallback
    """
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.pipeline_options import PdfPipelineOptions, OcrAutoOptions
    from docling.datamodel.base_models import InputFormat

    ocr_opts = None

    # Try Apple Vision on macOS — check by importing ocrmac directly rather than
    # instantiating a throwaway DocumentConverter (which loads all ML models).
    if sys.platform == "darwin":
        try:
            import ocrmac as _ocrmac  # noqa: F401
            from docling.datamodel.pipeline_options import OcrMacOptions
            ocr_opts = OcrMacOptions(lang=_ocr_languages(), force_full_page_ocr=force_ocr)
        except Exception:
            ocr_opts = None  # fall through to EasyOCR

    if ocr_opts is None:
        ocr_opts = OcrAutoOptions(lang=[], force_full_page_ocr=force_ocr)

    pipeline_options = PdfPipelineOptions(do_ocr=True, ocr_options=ocr_opts)
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
    )


def _run_chunk_subprocess(chunk_path: Path, page_offset: int, force_ocr: bool) -> dict:
    """Process a single chunk PDF in a subprocess and return adjusted results."""
    cmd = [sys.executable, "-m", "watchdog.pipeline.preprocess", str(chunk_path)]
    if force_ocr:
        cmd.append("--force-ocr")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=CHUNK_TIMEOUT)
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
        return {"error": f"Chunk timed out after {CHUNK_TIMEOUT}s"}
    except Exception as e:
        return {"error": str(e)}


def process_large_pdf(path: Path, force_ocr: bool, total_pages: int) -> dict:
    """Split a large PDF into CHUNK_SIZE-page chunks and process in parallel."""
    chunks = [
        (start, min(start + CHUNK_SIZE, total_pages))
        for start in range(0, total_pages, CHUNK_SIZE)
    ]

    chunk_results: dict[int, dict] = {}

    def process_one(start: int, end: int) -> tuple[int, dict]:
        chunk_path = pdf_extract_chunk(path, start, end)
        try:
            return start, _run_chunk_subprocess(chunk_path, start, force_ocr)
        finally:
            if chunk_path.exists():
                chunk_path.unlink()

    with ThreadPoolExecutor(max_workers=CHUNK_WORKERS) as pool:
        futures = {pool.submit(process_one, s, e): (s, e) for s, e in chunks}
        for future in as_completed(futures):
            start, result = future.result()
            chunk_results[start] = result

    # Merge in page order; skip failed chunks but note them
    all_pages = []
    failed_chunks = []
    garbled_detected = False
    ocr_used = force_ocr

    for start in sorted(chunk_results.keys()):
        r = chunk_results[start]
        if "error" in r:
            failed_chunks.append(f"pages {start+1}-{start+CHUNK_SIZE}: {r['error']}")
            continue
        all_pages.extend(r.get("pages", []))
        if r.get("metadata", {}).get("garbled_detected"):
            garbled_detected = True
        if r.get("metadata", {}).get("ocr_used"):
            ocr_used = True

    full_text = "\n\n".join(p["text"] for p in all_pages)

    result = {
        "filename": path.name,
        "sha256": sha256_file(path),
        "page_count": total_pages,
        "text": full_text,
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

    return result


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
        if total_pages > CHUNK_SIZE:
            return process_large_pdf(path, force_ocr, total_pages)

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
        cleaned = pdf_preprocess(path)
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

    pages_dict: dict[int, list[str]] = {}
    try:
        for item, _level in doc.iterate_items():
            text = getattr(item, "text", None)
            if not text:
                continue
            page_no = 1
            if getattr(item, "prov", None):
                page_no = item.prov[0].page_no
            pages_dict.setdefault(page_no, []).append(text)
    except Exception:
        pass

    if pages_dict:
        pages = [
            {"page": pno, "text": " ".join(parts)}
            for pno, parts in sorted(pages_dict.items())
        ]
        full_text = "\n\n".join(p["text"] for p in pages)
        page_count = max(pages_dict.keys())
    else:
        full_text = doc.export_to_markdown()
        pages = [{"page": 1, "text": full_text}]
        page_count = 1

    return {
        "filename": path.name,
        "sha256": sha256_file(path),
        "page_count": page_count,
        "text": full_text,
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

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
