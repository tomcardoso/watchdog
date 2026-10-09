"""Where OCR'd text sits on each page (D289).

Pre-processing OCRs a PDF page that has no usable text layer, and every page of a scanned image.
Docling's OCR model returns each recognised line with its box on the page; before D289 the boxes
were dropped once the text was exported. This module keeps them, so the app can lay an invisible,
selectable text layer over a scanned page and find, highlight, select and copy text on it. The
original file is never changed, and no second OCR pass runs: the boxes are the same OCR output the
page's extracted text was built from.

One small JSON file per document, `.watchdog/text-positions/<sha256>.json`, written by
pre-processing (`preprocess_batch`) beside the queue descriptor and kept after the document is
committed. It is keyed by the document's SHA-256, so it never moves when the original's morgue
folder does. Only pages whose text came from a full-page OCR pass are recorded: a page that kept
its own text layer is searched through that layer.

    {"format": 1, "sha256": "...", "engine": "auto" | "rapidocr" | "ocrmac" | ...,
     "unit": "line",
     "pages": {"3": {"width": 612.0, "height": 792.0,
                     "lines": [[left, top, right, bottom, "text"], ...]}}}

Coordinates are in the page's own units (PDF points for a PDF, pixels for an image) with the
origin at the top left, as Docling reports a page's size. A reader scales them to the size it
draws the page at. Readers ignore fields they do not know.

Granularity: Docling's OCR models report one cell per text line (RapidOCR's detector finds lines;
Apple Vision returns one observation per line), so `unit` is `line`, and a match inside a line is
placed by estimating where its characters fall.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

FORMAT = 1
UNIT = "line"
_DIR = "text-positions"
_MAX_PAGES_PER_READ = 50


def path_for(vault: Path, sha256: str) -> Path:
    return Path(vault) / ".watchdog" / _DIR / f"{sha256}.json"


def _round(v: float) -> float:
    return round(float(v), 1)


def _top_left_box(cell, page_height: float) -> tuple[float, float, float, float] | None:
    """A Docling cell's box as (left, top, right, bottom) with a top-left origin, or None."""
    try:
        bbox = cell.rect.to_bounding_box()
        origin = str(getattr(bbox, "coord_origin", "")).upper()
        left, right = float(bbox.l), float(bbox.r)
        top, bottom = float(bbox.t), float(bbox.b)
        if "BOTTOM" in origin:
            top, bottom = page_height - top, page_height - bottom
        if top > bottom:
            top, bottom = bottom, top
        if left > right:
            left, right = right, left
        return left, top, right, bottom
    except Exception:
        return None


def from_conversion(conv_result) -> dict[str, dict]:
    """Every OCR'd line of a Docling conversion, by 1-based page number (as a string).

    Reads `pages[i].parsed_page.textline_cells`, which Docling keeps only when the pipeline
    was built with `generate_parsed_pages` (see `preprocess.build_converter`). A page without
    them, or a Docling without the option, yields nothing, never an error: the positions are an
    aid to reading, and their absence only means the app cannot highlight on that page."""
    out: dict[str, dict] = {}
    for page in getattr(conv_result, "pages", None) or []:
        try:
            parsed = getattr(page, "parsed_page", None)
            size = getattr(page, "size", None)
            if parsed is None or size is None:
                continue
            width, height = float(size.width), float(size.height)
            lines = []
            for cell in getattr(parsed, "textline_cells", None) or []:
                if not getattr(cell, "from_ocr", False):
                    continue
                text = (getattr(cell, "text", "") or "").strip()
                box = _top_left_box(cell, height)
                if not text or box is None:
                    continue
                lines.append([*(_round(v) for v in box), text])
            if lines:
                out[str(int(page.page_no))] = {"width": _round(width), "height": _round(height),
                                               "lines": lines}
        except Exception:
            continue
    return out


def renumber(pages: dict[str, dict], page_numbers: list[int]) -> dict[str, dict] | None:
    """Positions from a slice of a PDF renumbered to the original document: slice page n is
    original page `page_numbers[n - 1] + 1` (0-indexed list, as `preprocess` slices carry).
    None when a page number falls outside the slice, which the caller treats like the slice's
    own page-numbering failure."""
    out: dict[str, dict] = {}
    for key, value in (pages or {}).items():
        index = int(key) - 1
        if not 0 <= index < len(page_numbers):
            return None
        out[str(page_numbers[index] + 1)] = value
    return out


def block(pages: dict[str, dict], engine: str) -> dict | None:
    """The `text_positions` value pre-processing puts in its result, or None for no pages."""
    if not pages:
        return None
    return {"format": FORMAT, "engine": engine, "unit": UNIT, "pages": pages}


def write(vault: Path, sha256: str, positions: dict | None) -> Path | None:
    """Write (or, with no positions, remove) the document's file. Atomic; returns the path
    written. A re-processed document replaces its file, so stale boxes never outlive their text."""
    target = path_for(vault, sha256)
    pages = (positions or {}).get("pages") if isinstance(positions, dict) else None
    if not pages:
        target.unlink(missing_ok=True)
        return None
    payload = {"format": FORMAT, "sha256": sha256,
               "engine": str(positions.get("engine") or "unknown"),
               "unit": str(positions.get("unit") or UNIT), "pages": pages}
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".tp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target


# A long scanned document's file can run to a few megabytes; the app asks for a few pages at a
# time as the reader scrolls, so the parsed file is kept for the next request.
_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, int, dict]] = {}
_CACHE_SIZE = 4


def _load(path: Path) -> dict | None:
    try:
        st = path.stat()
    except OSError:
        return None
    key = str(path)
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
            return hit[2]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("pages"), dict):
        return None
    with _cache_lock:
        if len(_cache) >= _CACHE_SIZE:
            _cache.pop(next(iter(_cache)))
        _cache[key] = (st.st_mtime, st.st_size, data)
    return data


def _clean_page(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    try:
        width, height = float(value["width"]), float(value["height"])
    except (KeyError, TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    lines = []
    for line in value.get("lines") or []:
        if (isinstance(line, list) and len(line) >= 5 and isinstance(line[4], str)
                and all(isinstance(v, (int, float)) for v in line[:4])):
            lines.append([float(v) for v in line[:4]] + [line[4]])
    return {"width": width, "height": height, "lines": lines}


def available_pages(vault: Path, sha256: str) -> list[int]:
    """The pages that have saved positions, ascending; empty when the document has none."""
    data = _load(path_for(vault, sha256))
    if not data:
        return []
    pages = []
    for key in data["pages"]:
        try:
            pages.append(int(key))
        except (TypeError, ValueError):
            continue
    return sorted(pages)


def read(vault: Path, sha256: str, pages: list[int] | None = None) -> dict:
    """`{unit, engine, pages: [n...], boxes: {"n": {width, height, lines}}}` for the requested
    pages (at most 50 per call); `boxes` is empty without a request, and every list is empty for a
    document with no saved positions. A page that has none is left out of `boxes`."""
    data = _load(path_for(vault, sha256))
    if not data:
        return {"unit": None, "engine": None, "pages": [], "boxes": {}}
    boxes: dict[str, dict] = {}
    for n in (pages or [])[:_MAX_PAGES_PER_READ]:
        page = _clean_page(data["pages"].get(str(n)))
        if page is not None:
            boxes[str(n)] = page
    return {"unit": data.get("unit") or UNIT, "engine": data.get("engine"),
            "pages": available_pages(vault, sha256), "boxes": boxes}
