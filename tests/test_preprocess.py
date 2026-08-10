"""Tests for preprocess helpers that don't require Docling to be installed."""

import json
from pathlib import Path

import pytest

from watchdog.pipeline import preprocess
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


def test_process_direct_text_default_replaces_invalid_bytes(tmp_path):
    """Default behaviour (used for DIRECT_TEXT_SUFFIXES) is unchanged: invalid
    UTF-8 is substituted, not raised."""
    f = tmp_path / "mystery.bin"
    f.write_bytes(b"\x8c\xff\x00\x01")
    result = process_direct_text(f)
    assert result["pages"][0]["markdown"]  # decoded to replacement chars, no raise


def test_main_attaches_file_metadata_as_sibling_of_metadata(tmp_path, monkeypatch, capsys):
    """main() is the single convergence point for all three chew paths (#369) — it must
    attach `file_metadata` as a top-level key, a sibling of `metadata`, not nested inside it."""
    f = tmp_path / "doc.txt"
    f.write_text("hello world")
    monkeypatch.setattr("sys.argv", ["preprocess.py", str(f)])
    preprocess.main()
    result = json.loads(capsys.readouterr().out)
    assert "file_metadata" in result
    assert "file_metadata" not in result["metadata"]
    # .txt carries no embedded metadata layer at all (DIRECT_TEXT_SUFFIXES, not in the
    # file_metadata dispatch table) — a valid empty dict, not an error.
    assert result["file_metadata"] == {}


def test_main_captures_file_metadata_from_original_path_not_a_temp_file(tmp_path, monkeypatch, capsys):
    """A stand-in reader confirms main() calls file_metadata.extract() with the resolved
    original source path — the exact object chew must never substitute a cleaned/temp path for."""
    f = tmp_path / "doc.txt"
    f.write_text("hello world")
    monkeypatch.setattr("sys.argv", ["preprocess.py", str(f)])
    seen_paths = []
    monkeypatch.setattr(
        "watchdog.pipeline.file_metadata.extract",
        lambda path: (seen_paths.append(path), {"author": "stand-in"})[1],
    )
    preprocess.main()
    result = json.loads(capsys.readouterr().out)
    assert seen_paths == [f.resolve()]
    assert result["file_metadata"] == {"author": "stand-in"}


def test_process_direct_text_strict_raises_on_invalid_utf8(tmp_path):
    """The unknown-suffix dispatch branch passes encoding_errors='strict' so
    binary/undecodable content raises instead of silently becoming mojibake."""
    f = tmp_path / "mystery.bin"
    f.write_bytes(b"\x8c\xff\x00\x01")
    with pytest.raises(UnicodeDecodeError):
        process_direct_text(f, encoding_errors="strict")


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


def test_markdown_pages_decodes_html_entities():
    """Docling HTML-escapes "&" to "&amp;" in some cells/paragraphs even though markdown
    doesn't require it there (#560) — decode it once here, at the chew/canonicalization
    boundary, so every downstream consumer (the model's own extraction input, the rendered
    morgue note, quote/figure matching) sees the real character rather than the escaped form."""
    doc = _FakeDoc("Ernst &amp; Young Inc. &lt;the Monitor&gt;")
    pages = _markdown_pages(doc)
    assert pages == [{"page": 1, "markdown": "Ernst & Young Inc. <the Monitor>"}]


def test_markdown_pages_entity_decoding_does_not_disturb_page_break_or_image_placeholder():
    sep = "\n\n<!-- page-break -->\n\n"
    doc = _FakeDoc(f"Ernst &amp; Young{sep}[image] on page two")
    pages = _markdown_pages(doc)
    assert pages == [
        {"page": 1, "markdown": "Ernst & Young"},
        {"page": 2, "markdown": "[image] on page two"},
    ]


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


def test_process_large_pdf_partial_failure_returns_error(tmp_path, monkeypatch):
    """A single failed chunk must fail the whole document, not queue it with a silent page gap (#251)."""
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
    assert "error" in result
    assert "chunk 1 failed" in result["error"]


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
    monkeypatch.setattr(preprocess_mod, "pdf_sample_pages", lambda p: ["some text"])

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
    monkeypatch.setattr(preprocess_mod, "pdf_sample_pages", lambda p: ["some text"])

    from watchdog.pipeline.preprocess import process_with_docling
    result = process_with_docling(_Path("/fake/doc.pdf"), force_ocr=False)

    assert "error" in result
    assert "chunks" in result["error"]


# ── main() dispatch — unknown suffix (#254) ───────────────────────────────────

def test_unknown_suffix_binary_file_falls_back_to_docling_not_silent_ok(tmp_path, monkeypatch, capsys):
    """A file with an unrecognized extension containing random binary bytes must
    not decode as replacement-character soup and queue as a silent OK. The
    strict decode should raise, routing to Docling instead."""
    import sys
    import random
    import watchdog.pipeline.preprocess as preprocess_mod

    f = tmp_path / "mystery.bin"
    rng = random.Random(42)
    f.write_bytes(bytes(rng.randrange(0, 256) for _ in range(2000)))

    docling_called = {}

    def fake_docling(path, force_ocr=False):
        docling_called["path"] = path
        return {"error": "Docling conversion failed: simulated"}

    monkeypatch.setattr(preprocess_mod, "process_with_docling", fake_docling)
    monkeypatch.setattr(sys, "argv", ["preprocess.py", str(f)])

    with pytest.raises(SystemExit):
        preprocess_mod.main()

    assert docling_called  # fell through to Docling rather than a silent direct_text OK
    out = json.loads(capsys.readouterr().out)
    assert "error" in out


def test_unknown_suffix_garbled_but_valid_text_gets_flagged(tmp_path, monkeypatch, capsys):
    """A file with an unrecognized extension that happens to decode as valid
    UTF-8 but is symbol-heavy must come back flagged via garbled_detected
    rather than queueing silently as clean OK text."""
    import sys
    import watchdog.pipeline.preprocess as preprocess_mod

    f = tmp_path / "mystery.xyz"
    f.write_text("©®™†‡§¶•∞≠≈∂∑∏√∫", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["preprocess.py", str(f)])
    preprocess_mod.main()  # decodes fine, no Docling fallback needed

    out = json.loads(capsys.readouterr().out)
    assert out["metadata"]["source_type"] == "direct_text"
    assert out["metadata"]["garbled_detected"] is True


# ── pdf_garble_verdict — per-page combination, not a blended blob (#580) ──────

def test_verdict_dot_leader_toc_page_does_not_force_ocr(monkeypatch):
    """A dot-leader table-of-contents page sampled alongside clean body-text
    pages must not push the whole-document verdict to garbled. Regression for
    #580: a blended ratio over all sampled pages let one TOC page (heavy on
    dot leaders) drag a born-digital document's score below threshold."""
    import watchdog.pipeline.preprocess as preprocess_mod

    toc_page = ("Overview ...................................................... 2\n"
                "Review of fiscal 2019-2020 .................................... 4\n")
    body_page = "This report reviews the fiscal year in clear, ordinary prose."

    # Sanity: the TOC page alone would trip the per-page check on its own.
    assert is_garbled(toc_page)
    assert not is_garbled(body_page)

    monkeypatch.setattr(preprocess_mod, "pdf_sample_pages",
                         lambda p: [toc_page, body_page, body_page])

    empty, garbled = preprocess_mod.pdf_garble_verdict("fake.pdf")
    assert empty is False
    assert garbled is False


def test_verdict_symbol_soup_on_every_page_still_forces_ocr(monkeypatch):
    """Genuinely garbled text across every sampled page must still be caught —
    the fix must not make the detector toothless."""
    import watchdog.pipeline.preprocess as preprocess_mod

    soup = "©®™†‡§¶•∞≠≈∂∑∏√∫"
    monkeypatch.setattr(preprocess_mod, "pdf_sample_pages", lambda p: [soup, soup, soup])

    empty, garbled = preprocess_mod.pdf_garble_verdict("fake.pdf")
    assert empty is False
    assert garbled is True


def test_verdict_all_sampled_pages_empty_is_reported_as_empty_not_garbled(monkeypatch):
    """A fully scanned document (no text on any sampled page) must still force
    OCR, but via the 'empty' path, not the 'garbled' one — the two have
    different causes."""
    import watchdog.pipeline.preprocess as preprocess_mod

    monkeypatch.setattr(preprocess_mod, "pdf_sample_pages", lambda p: ["", "", ""])

    empty, garbled = preprocess_mod.pdf_garble_verdict("fake.pdf")
    assert empty is True
    assert garbled is False


def test_verdict_blank_pages_among_sampled_pages_are_ignored(monkeypatch):
    """A blank page (e.g. an unlabelled cover) mixed with clean body pages must
    not itself count toward the garbled verdict."""
    import watchdog.pipeline.preprocess as preprocess_mod

    body_page = "This report reviews the fiscal year in clear, ordinary prose."
    monkeypatch.setattr(preprocess_mod, "pdf_sample_pages",
                         lambda p: ["", body_page, body_page])

    empty, garbled = preprocess_mod.pdf_garble_verdict("fake.pdf")
    assert empty is False
    assert garbled is False


# ── pdf_garble_verdict — real corpus documents (#580) ──────────────────────────
# Slow-ish (real pypdf extraction on real files) and skipped when the corpus
# isn't present, so kept separate from the fast synthetic tests above.

_CORPUS_DIR = Path(__file__).resolve().parent.parent / "benchmarks" / "corpora" / "extract"

_CORPUS_EXPECTED = {
    "Annual-Financial-Report-19-20.pdf": (False, False),
    "Annual-Financial-Report-20-21.pdf": (False, False),
    "Laurentian First Report of the Monitor.pdf": (False, False),
    "Laurentian Pre-Filing Report of the Proposed Monitor.pdf": (False, False),
    "CV-21-00656040-00CL Laurentian U Initial Order 1 FEB 2021.pdf": (True, False),
    "Pension Order Morawetz CJ- March 17 2021(as stamped by Court).PDF": (False, False),
}


@pytest.mark.skipif(not _CORPUS_DIR.is_dir(), reason="benchmark corpus not present")
@pytest.mark.parametrize("filename,expected", _CORPUS_EXPECTED.items())
def test_corpus_garble_verdicts(filename, expected):
    """Regression table from #580: the AFR 19-20 misfire must be fixed, and the
    Initial Order's genuine empty-text-layer case must keep forcing OCR."""
    result = preprocess.pdf_garble_verdict(_CORPUS_DIR / filename)
    assert result == expected, f"{filename}: expected (empty, garbled)={expected}, got {result}"



