"""Tests for preprocess helpers that don't require Docling to be installed."""

from watchdog.pipeline.preprocess import is_garbled, process_large_pdf


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
