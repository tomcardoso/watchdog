"""Tests for preprocess helpers that don't require Docling to be installed."""

from watchdog.pipeline.preprocess import (
    is_garbled,
    process_direct_text,
    process_large_pdf,
    _markdown_pages,
)


def test_garbled_clean_text():
    assert not is_garbled("This is a normal sentence with words and spaces.")


def test_garbled_empty_string():
    # Empty text is not considered garbled — no text layer at all is a separate check
    assert not is_garbled("")


def test_garbled_symbol_heavy():
    assert is_garbled("©®™†‡§¶•∞≠≈∂∑∏√∫")


def test_garbled_mixed_borderline():
    # 50% alphanumeric — well below the 0.75 default threshold
    assert is_garbled("abc©©©")


def test_garbled_numbers_and_spaces_count_as_readable():
    assert not is_garbled("12345 67890 page 4 of 12")


# ── process_direct_text ───────────────────────────────────────────────────────

def test_process_direct_text_returns_expected_shape(tmp_path):
    f = tmp_path / "doc.txt"
    f.write_text("hello world")
    result = process_direct_text(f)
    assert result["filename"] == "doc.txt"
    assert result["page_count"] == 1
    assert result["pages"] == [{"page": 1, "markdown": "hello world"}]
    assert result["metadata"]["source_type"] == "direct_text"
    assert result["metadata"]["ocr_used"] is False


def test_process_direct_text_has_sha256(tmp_path):
    f = tmp_path / "doc.txt"
    f.write_text("content")
    result = process_direct_text(f)
    assert len(result["sha256"]) == 64  # hex SHA-256


# ── _markdown_pages ───────────────────────────────────────────────────────────

class _FakeDoc:
    """Minimal stub for a Docling document that has export_to_markdown."""
    def __init__(self, text):
        self._text = text

    def export_to_markdown(self, **kw):
        return self._text


def test_markdown_pages_splits_on_page_break():
    sep = "\n\n<!-- page-break -->\n\n"
    doc = _FakeDoc(f"page one{sep}page two{sep}page three")
    pages = _markdown_pages(doc)
    assert len(pages) == 3
    assert pages[0] == {"page": 1, "markdown": "page one"}
    assert pages[2] == {"page": 3, "markdown": "page three"}


def test_markdown_pages_filters_empty_parts():
    sep = "\n\n<!-- page-break -->\n\n"
    doc = _FakeDoc(f"page one{sep}{sep}page three")
    pages = _markdown_pages(doc)
    assert len(pages) == 2


def test_markdown_pages_single_page_fallback():
    doc = _FakeDoc("no page breaks here")
    pages = _markdown_pages(doc)
    assert pages == [{"page": 1, "markdown": "no page breaks here"}]


def test_markdown_pages_empty_doc_fallback():
    doc = _FakeDoc("")
    pages = _markdown_pages(doc)
    assert pages == [{"page": 1, "markdown": ""}]


# ── process_large_pdf ────────────────────────────────────────────────────────

def _fake_extract(tmp_path):
    """Return a pdf_extract_chunk stub that creates real temp files (so unlink works)."""
    def _extract(src, start, end):
        f = tmp_path / f"chunk_{start}.pdf"
        f.write_bytes(b"")
        return f
    return _extract


def test_process_large_pdf_all_chunks_fail_returns_error(tmp_path, monkeypatch):
    """When every chunk subprocess fails, return an error dict rather than empty pages."""
    from pathlib import Path as _Path
    import watchdog.pipeline.preprocess as preprocess_mod

    monkeypatch.setattr(preprocess_mod, "pdf_extract_chunk", _fake_extract(tmp_path))
    monkeypatch.setattr(preprocess_mod, "_run_chunk_subprocess",
                        lambda chunk_path, page_offset, force_ocr: {"error": "simulated failure"})
    monkeypatch.setattr(preprocess_mod, "sha256_file", lambda p: "abc123")

    result = process_large_pdf(_Path("/fake/doc.pdf"), force_ocr=False, total_pages=10)
    assert "error" in result
    assert "failed" in result["error"].lower()


def test_process_large_pdf_partial_failure_still_returns_pages(tmp_path, monkeypatch):
    """When only some chunks fail, return the pages from successful chunks."""
    from pathlib import Path as _Path
    import watchdog.pipeline.preprocess as preprocess_mod

    call_count = {"n": 0}

    def fake_chunk(chunk_path, page_offset, force_ocr):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return {"error": "chunk 1 failed"}
        return {
            "pages": [{"page": page_offset + 1, "markdown": "ok page"}],
            "metadata": {"ocr_used": False, "garbled_detected": False},
        }

    monkeypatch.setattr(preprocess_mod, "pdf_extract_chunk", _fake_extract(tmp_path))
    monkeypatch.setattr(preprocess_mod, "_run_chunk_subprocess", fake_chunk)
    monkeypatch.setattr(preprocess_mod, "sha256_file", lambda p: "abc123")

    result = process_large_pdf(_Path("/fake/doc.pdf"), force_ocr=False, total_pages=80)
    assert "error" not in result
    assert result["pages"]
    assert result["metadata"]["failed_chunks"]


# ── process_with_docling large-PDF fallback ───────────────────────────────────

def test_large_pdf_fallback_retries_after_all_chunks_fail(tmp_path, monkeypatch):
    """When large-PDF chunking fails entirely, pdf_preprocess fallback is tried."""
    from pathlib import Path as _Path
    import watchdog.pipeline.preprocess as preprocess_mod

    cleaned = tmp_path / "cleaned.pdf"
    cleaned.write_bytes(b"")

    call_log = []

    def fake_page_count(path):
        return 50  # always > CHUNK_SIZE so we go the large-PDF path

    def fake_process_large(path, force_ocr, total_pages):
        call_log.append(str(path))
        if "fake" in str(path):
            return {"error": "all chunks failed"}
        # Second call (on cleaned file) succeeds
        return {
            "filename": path.name,
            "sha256": "cleaned-hash",
            "page_count": 50,
            "pages": [{"page": 1, "markdown": "recovered text"}],
            "metadata": {"ocr_used": True, "garbled_detected": False,
                         "source_type": "docling", "chunked": True, "chunk_count": 2},
        }

    monkeypatch.setattr(preprocess_mod, "pdf_page_count", fake_page_count)
    monkeypatch.setattr(preprocess_mod, "process_large_pdf", fake_process_large)
    monkeypatch.setattr(preprocess_mod, "pdf_preprocess", lambda p: cleaned)
    monkeypatch.setattr(preprocess_mod, "sha256_file", lambda p: "original-hash")
    # Prevent docling import from being tried
    monkeypatch.setattr(preprocess_mod, "pdf_sample_text", lambda p: "some text")

    from watchdog.pipeline.preprocess import process_with_docling
    result = process_with_docling(_Path("/fake/doc.pdf"), force_ocr=False)

    assert "error" not in result, result
    assert result["sha256"] == "original-hash"   # original file's hash, not cleaned's
    assert result["filename"] == "doc.pdf"        # original filename, not temp name
    assert result["pages"][0]["markdown"] == "recovered text"
    assert len(call_log) == 2                     # tried original, then cleaned


def test_large_pdf_fallback_returns_original_error_if_preprocess_unavailable(tmp_path, monkeypatch):
    """If pdf_preprocess returns None, the original chunking error is returned."""
    from pathlib import Path as _Path
    import watchdog.pipeline.preprocess as preprocess_mod

    monkeypatch.setattr(preprocess_mod, "pdf_page_count", lambda p: 50)
    monkeypatch.setattr(preprocess_mod, "process_large_pdf",
                        lambda path, force_ocr, total_pages: {"error": "all chunks failed"})
    monkeypatch.setattr(preprocess_mod, "pdf_preprocess", lambda p: None)
    monkeypatch.setattr(preprocess_mod, "pdf_sample_text", lambda p: "some text")

    from watchdog.pipeline.preprocess import process_with_docling
    result = process_with_docling(_Path("/fake/doc.pdf"), force_ocr=False)

    assert "error" in result
    assert "chunks" in result["error"]
