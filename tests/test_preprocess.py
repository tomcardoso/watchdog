"""Tests for preprocess helpers that don't require Docling to be installed."""

import json
from pathlib import Path

import pytest

from watchdog.pipeline import preprocess
from watchdog.pipeline.preprocess import (
    is_garbled,
    process_direct_text,
    process_pdf_slices,
    _ocr_slices,
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
    # 50% alphanumeric — below the 0.6 default threshold
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


# ── process_pdf_slices ───────────────────────────────────────────────────────

def _fake_extract(tmp_path):
    """Return a pdf_extract_pages stub that creates real temp files (so unlink works)."""
    def _extract(src, indices):
        f = tmp_path / f"slice_{indices[0]}.pdf"
        f.write_bytes(b"")
        return f
    return _extract


def test_process_pdf_slices_all_slices_fail_returns_error(tmp_path, monkeypatch):
    """When every slice subprocess fails, return an error dict rather than empty pages."""
    from pathlib import Path as _Path
    import watchdog.pipeline.preprocess as preprocess_mod

    monkeypatch.setattr(preprocess_mod, "pdf_extract_pages", _fake_extract(tmp_path))
    monkeypatch.setattr(preprocess_mod, "_run_slice_subprocess",
                        lambda slice_path, page_numbers, force_ocr: {"error": "simulated failure"})

    result = process_pdf_slices(_Path("/fake/doc.pdf"), [(list(range(10)), False)])
    assert "error" in result
    assert "failed" in result["error"].lower()


def test_process_pdf_slices_partial_failure_returns_error(tmp_path, monkeypatch):
    """A single failed slice must fail the whole document, not queue it with a silent page gap (#251)."""
    from pathlib import Path as _Path
    import watchdog.pipeline.preprocess as preprocess_mod

    call_count = {"n": 0}

    def fake_slice(slice_path, page_numbers, force_ocr):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return {"error": "slice 1 failed"}
        return {"pages": [{"page": page_numbers[0] + 1, "markdown": "ok page"}]}

    monkeypatch.setattr(preprocess_mod, "pdf_extract_pages", _fake_extract(tmp_path))
    monkeypatch.setattr(preprocess_mod, "_run_slice_subprocess", fake_slice)

    slices = [(list(range(0, 40)), False), (list(range(40, 80)), False)]
    result = process_pdf_slices(_Path("/fake/doc.pdf"), slices)
    assert "error" in result
    assert "slice 1 failed" in result["error"]


def test_process_pdf_slices_merges_interleaved_pages_in_order(tmp_path, monkeypatch):
    """Slices are grouped by OCR verdict, so their pages interleave in the
    original document and must be sorted, not concatenated (#605)."""
    from pathlib import Path as _Path
    import watchdog.pipeline.preprocess as preprocess_mod

    def fake_slice(slice_path, page_numbers, force_ocr):
        tag = "ocr" if force_ocr else "text"
        return {"pages": [{"page": n + 1, "markdown": f"{tag} {n + 1}"} for n in page_numbers]}

    monkeypatch.setattr(preprocess_mod, "pdf_extract_pages", _fake_extract(tmp_path))
    monkeypatch.setattr(preprocess_mod, "_run_slice_subprocess", fake_slice)

    # Pages 2 and 4 need OCR; 1, 3, 5 keep their text layer.
    slices = [([0, 2, 4], False), ([1, 3], True)]
    result = process_pdf_slices(_Path("/fake/doc.pdf"), slices)

    assert [p["page"] for p in result["pages"]] == [1, 2, 3, 4, 5]
    assert [p["markdown"] for p in result["pages"]] == [
        "text 1", "ocr 2", "text 3", "ocr 4", "text 5",
    ]


# ── process_with_docling large-PDF fallback ───────────────────────────────────

def test_large_pdf_fallback_retries_after_all_chunks_fail(tmp_path, monkeypatch):
    """When large-PDF chunking fails entirely, pdf_preprocess fallback is tried."""
    from pathlib import Path as _Path
    import watchdog.pipeline.preprocess as preprocess_mod

    cleaned = tmp_path / "cleaned.pdf"
    cleaned.write_bytes(b"")

    call_log = []

    def fake_page_count(path):
        return 50  # always > CHUNK_SIZE so we go the sliced path

    def fake_slices(path, slices):
        call_log.append(str(path))
        if "fake" in str(path):
            return {"error": "all chunks failed"}
        return {"pages": [{"page": 1, "markdown": "recovered text"}]}

    monkeypatch.setattr(preprocess_mod, "pdf_page_count", fake_page_count)
    monkeypatch.setattr(preprocess_mod, "process_pdf_slices", fake_slices)
    monkeypatch.setattr(preprocess_mod, "pdf_preprocess", lambda p: cleaned)
    monkeypatch.setattr(preprocess_mod, "sha256_file", lambda p: "original-hash")
    # Prevent docling import from being tried
    monkeypatch.setattr(preprocess_mod, "pdf_page_samples",
                        lambda p: [preprocess_mod.PageSample("some text", False)] * 50)

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
    monkeypatch.setattr(preprocess_mod, "process_pdf_slices",
                        lambda path, slices: {"error": "all chunks failed"})
    monkeypatch.setattr(preprocess_mod, "pdf_preprocess", lambda p: None)
    monkeypatch.setattr(preprocess_mod, "pdf_page_samples",
                        lambda p: [preprocess_mod.PageSample("some text", False)] * 50)

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

    def fake_docling(path, force_ocr=False, detect=True):
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


# ── word_shape_ratio / is_word_shape_garbled — signal #2 (#597) ───────────────

def test_word_shape_clean_prose_scores_high():
    ratio = preprocess.word_shape_ratio("This is a normal sentence with words and spaces.")
    assert ratio == 1.0
    assert not preprocess.is_word_shape_garbled("This is a normal sentence with words and spaces.")


def test_word_shape_dot_leader_toc_line_still_reads_as_words():
    """A dot-leader table-of-contents line is heavy on '.' characters (tripping
    the character-ratio signal) but its actual words still read as words —
    this is the whole reason word-shape exists (#580/#597)."""
    toc_line = "Overview .................................................... 2"
    ratio = preprocess.word_shape_ratio(toc_line)
    assert ratio is not None and ratio > 0.5   # "Overview" and "2" are words; the dot run isn't
    assert not preprocess.is_word_shape_garbled(toc_line)


def test_word_shape_symbol_soup_scores_zero():
    assert preprocess.word_shape_ratio("©®™†‡§¶•∞≠≈∂∑∏√∫") == 0.0
    assert preprocess.is_word_shape_garbled("©®™†‡§¶•∞≠≈∂∑∏√∫")


def test_word_shape_empty_text_not_garbled():
    assert not preprocess.is_word_shape_garbled("")


def test_word_shape_ratio_none_for_no_tokens():
    assert preprocess.word_shape_ratio("") is None


@pytest.mark.parametrize("label,text", [
    ("accented French", "Réunion à Montréal avec Éric Thériault, chargé de l'équité."),
    ("German", "Der Aufsichtsrat genehmigte die Überprüfung der Geschäftsjahresausgaben."),
    ("Japanese", "取締役会は2019年度の営業費用の調整を承認した。"),
    ("Arabic", "وافق مجلس الإدارة على تسوية نفقات التشغيل."),
])
def test_word_shape_does_not_penalize_non_ascii_scripts(label, text):
    """A non-English script is not evidence of garbling. An ASCII-only token class
    scored accented French at 0.31 and Japanese at 0.00 — i.e. a permanent vote
    against every document not written in English, which combined with the
    font-CMap signal (composite /Type0 fonts are the norm for CJK) could reach
    the 2-of-3 threshold and force OCR on perfectly clean born-digital text."""
    assert preprocess.word_shape_ratio(text) == 1.0
    assert not preprocess.is_word_shape_garbled(text)


def test_word_shape_punctuation_only_page_abstains():
    """Nothing but dot leaders and brackets leaves no token to judge — the signal
    returns None (abstains) rather than voting garbled on no evidence. The
    character-ratio signal still fires on such a page."""
    assert preprocess.word_shape_ratio(".... ...... ()") is None
    assert not preprocess.is_word_shape_garbled(".... ...... ()")


# ── pdf_page_missing_font_cmap — signal #3 (#597) ──────────────────────────────
# Takes a duck-typed page object; plain nested dicts stand in for pypdf's
# DictionaryObject (which also supports .get()), so these run with no real PDF.

def _fake_page(fonts: dict) -> dict:
    return {"/Resources": {"/Font": fonts}}


def test_font_cmap_type0_without_tounicode_is_missing():
    page = _fake_page({
        "/F1": {"/Subtype": "/Type0", "/BaseFont": "ABCDEF+CustomBodyFont"},
    })
    assert preprocess.pdf_page_missing_font_cmap(page)


def test_font_cmap_type0_with_tounicode_is_not_missing():
    page = _fake_page({
        "/F1": {"/Subtype": "/Type0", "/BaseFont": "ABCDEF+CustomBodyFont", "/ToUnicode": object()},
    })
    assert not preprocess.pdf_page_missing_font_cmap(page)


def test_font_cmap_simple_truetype_without_tounicode_is_not_missing():
    """A simple font with a named encoding (WinAnsiEncoding etc.) decodes fine
    without ToUnicode — only composite (/Type0) fonts are checked."""
    page = _fake_page({
        "/F1": {"/Subtype": "/TrueType", "/BaseFont": "ABCDEF+Arial", "/Encoding": "/WinAnsiEncoding"},
    })
    assert not preprocess.pdf_page_missing_font_cmap(page)


def test_font_cmap_dingbat_font_without_tounicode_is_excluded():
    """A Type0 Wingdings/Symbol font missing ToUnicode is a known, common case
    (bullets/icons) that says nothing about body-text readability — this is
    the exact false positive observed on the real #580 corpus document, where
    a decorative Wingdings font appears on both a garbled TOC page and clean
    body pages alike."""
    page = _fake_page({
        "/F1": {"/Subtype": "/Type0", "/BaseFont": "GFOLCA+Wingdings-Regular"},
    })
    assert not preprocess.pdf_page_missing_font_cmap(page)


def test_font_cmap_no_fonts_is_not_missing():
    assert not preprocess.pdf_page_missing_font_cmap({"/Resources": {}})
    assert not preprocess.pdf_page_missing_font_cmap({})


# ── page_garble_signals / is_page_garbled — level 1: signals -> page verdict ───

def test_page_garbled_requires_at_least_two_signals():
    """One signal firing alone must not condemn a page — this is the
    require-agreement combination rule chosen in D189 for #597."""
    PS = preprocess.PageSample
    # A dot-leader line: heavy on '.' (trips char_ratio) but its actual tokens
    # ("Overview", "2") still read as words (does not trip word_shape).
    only_ratio = PS(text="Overview .......................................... 2", missing_font_cmap=False)
    signals = preprocess.page_garble_signals(only_ratio)
    assert signals == {"char_ratio": True, "word_shape": False, "font_cmap": False}
    assert not preprocess.is_page_garbled(only_ratio)

    only_cmap = PS(text="This is ordinary clean prose text.", missing_font_cmap=True)
    assert sum(preprocess.page_garble_signals(only_cmap).values()) == 1
    assert not preprocess.is_page_garbled(only_cmap)


def test_page_garbled_two_signals_agreeing_is_enough():
    PS = preprocess.PageSample
    soup = PS(text="©®™†‡§¶•∞≠≈∂∑∏√∫", missing_font_cmap=False)  # char_ratio + word_shape fire
    assert sum(preprocess.page_garble_signals(soup).values()) == 2
    assert preprocess.is_page_garbled(soup)


def test_page_garbled_all_three_signals_agreeing():
    PS = preprocess.PageSample
    worst = PS(text="©®™†‡§¶•∞≠≈∂∑∏√∫", missing_font_cmap=True)
    assert sum(preprocess.page_garble_signals(worst).values()) == 3
    assert preprocess.is_page_garbled(worst)


def test_page_garbled_dot_leader_toc_page_not_garbled():
    """The #580 case at the page-verdict level: char_ratio fires on the dot
    leaders, but word_shape doesn't (real words present) and there's no font
    signal — only 1 of 3 votes, so the page reads as clean."""
    toc_page = preprocess.PageSample(
        text=("Overview ...................................................... 2\n"
              "Review of fiscal 2019-2020 .................................... 4\n"),
        missing_font_cmap=False,
    )
    signals = preprocess.page_garble_signals(toc_page)
    assert signals["char_ratio"] is True
    assert signals["word_shape"] is False
    assert not preprocess.is_page_garbled(toc_page)


# ── pdf_ocr_plan — level 2: page verdicts -> a per-page plan (#580, #605) ─────

_BODY = "This report reviews the fiscal year in clear, ordinary prose."
_TOC = ("Overview ...................................................... 2\n"
        "Review of fiscal 2019-2020 .................................... 4\n")
_SOUP = "©®™†‡§¶•∞≠≈∂∑∏√∫"


def _plan(monkeypatch, samples):
    import watchdog.pipeline.preprocess as preprocess_mod
    monkeypatch.setattr(preprocess_mod, "pdf_page_samples", lambda p: samples)
    return preprocess_mod.pdf_ocr_plan("fake.pdf", len(samples))


def _sample(text, missing_font_cmap=False):
    return preprocess.PageSample(text=text, missing_font_cmap=missing_font_cmap)


def test_plan_dot_leader_toc_page_does_not_force_ocr(monkeypatch):
    """A dot-leader table-of-contents page alongside clean body-text pages must
    not be forced — nor drag its neighbours into OCR (#580)."""
    force, garbled = _plan(monkeypatch, [_sample(_TOC), _sample(_BODY), _sample(_BODY)])
    assert force == []
    assert garbled == []


def test_plan_symbol_soup_on_every_page_still_forces_ocr(monkeypatch):
    """Genuinely garbled text across every page must still be caught — the fix
    must not make the detector toothless."""
    force, garbled = _plan(monkeypatch, [_sample(_SOUP)] * 3)
    assert force == [0, 1, 2]
    assert garbled == [0, 1, 2]


def test_plan_all_pages_empty_forces_every_page_but_reports_no_garble(monkeypatch):
    """A fully scanned document must still force OCR everywhere, but via the
    no-text-layer path, not the garbled one — the two have different causes and
    only the second is reported as garbled_detected."""
    force, garbled = _plan(monkeypatch, [_sample("")] * 3)
    assert force == [0, 1, 2]
    assert garbled == []


def test_plan_scopes_ocr_to_the_garbled_page_alone(monkeypatch):
    """The point of #605: one bad page among clean ones is forced by itself,
    instead of condemning every page in the document."""
    force, garbled = _plan(
        monkeypatch,
        [_sample(_BODY), _sample(_SOUP, missing_font_cmap=True), _sample(_BODY)],
    )
    assert force == [1]
    assert garbled == [1]


def test_plan_forces_a_page_with_no_text_layer_among_clean_pages(monkeypatch):
    """A scanned insert in a born-digital document is forced on its own — free
    insurance, since a page with no text layer has nothing for OCR to destroy,
    and it covers the case Docling's bitmap_area_threshold drops (D192)."""
    force, garbled = _plan(monkeypatch, [_sample(_BODY), _sample(""), _sample(_BODY)])
    assert force == [1]
    assert garbled == []       # no text layer is not the same as a junk one


def test_plan_forces_everything_when_the_text_layer_cannot_be_read(monkeypatch):
    """If pypdf yields nothing at all, no page-level claim is possible — the
    document-wide force is the same verdict the `empty` path reached before."""
    import watchdog.pipeline.preprocess as preprocess_mod
    monkeypatch.setattr(preprocess_mod, "pdf_page_samples", lambda p: [])

    force, garbled = preprocess_mod.pdf_ocr_plan("fake.pdf", 12)
    assert force == list(range(12))
    assert garbled == []


# ── _ocr_slices — grouping pages into conversion passes (#605) ────────────────

def test_slices_clean_document_is_one_contiguous_group():
    """A document whose pages all agree must chunk exactly as it did before
    #605: contiguous runs of chunk_size, OCR off."""
    assert _ocr_slices(90, [], chunk_size=40) == [
        (list(range(0, 40)), False),
        (list(range(40, 80)), False),
        (list(range(80, 90)), False),
    ]


def test_slices_force_all_marks_the_single_group():
    assert _ocr_slices(5, [], chunk_size=40, force_all=True) == [([0, 1, 2, 3, 4], True)]


def test_slices_mixed_document_splits_into_two_groups_by_verdict():
    """Pages are grouped by verdict, not contiguity — so two scanned inserts
    cost two passes, not one pass per run of pages."""
    assert _ocr_slices(6, [1, 3], chunk_size=40) == [
        ([0, 2, 4, 5], False),
        ([1, 3], True),
    ]


def test_slices_alternating_pages_still_cost_only_two_passes():
    """The pathological case grouping exists to bound: every other page scanned
    would be one slice per page if slices had to be contiguous."""
    slices = _ocr_slices(40, list(range(1, 40, 2)), chunk_size=40)
    assert len(slices) == 2
    assert slices[0] == (list(range(0, 40, 2)), False)
    assert slices[1] == (list(range(1, 40, 2)), True)


def test_slices_each_group_is_split_at_chunk_size():
    slices = _ocr_slices(100, list(range(50)), chunk_size=40)
    assert [(len(pages), force) for pages, force in slices] == [
        (40, False), (10, False), (40, True), (10, True),
    ]


def test_slices_of_a_small_mixed_document_are_not_padded():
    """Every page appears exactly once across the slices, whatever the grouping."""
    slices = _ocr_slices(7, [2, 5], chunk_size=40)
    seen = sorted(p for pages, _ in slices for p in pages)
    assert seen == list(range(7))


# ── page renumbering across a slice boundary (#605) ───────────────────────────

def _stub_subprocess_run(monkeypatch, payload):
    """Stand in for the slice subprocess, returning payload as its stdout JSON."""
    import subprocess as subprocess_mod
    import watchdog.pipeline.preprocess as preprocess_mod

    class _Completed:
        stdout = json.dumps(payload)
        stderr = ""

    monkeypatch.setattr(preprocess_mod.subprocess, "run",
                        lambda *a, **k: _Completed())
    return subprocess_mod


def test_slice_pages_are_renumbered_to_the_original_document(monkeypatch, tmp_path):
    """A slice numbers its own pages from 1; they must come back as the pages
    they actually were in the source document."""
    _stub_subprocess_run(monkeypatch, {"pages": [
        {"page": 1, "markdown": "first"},
        {"page": 2, "markdown": "second"},
    ]})
    slice_path = tmp_path / "slice.pdf"
    slice_path.write_bytes(b"")

    result = preprocess._run_slice_subprocess(slice_path, [100, 148], force_ocr=True)
    assert [p["page"] for p in result["pages"]] == [101, 149]


def test_slice_page_outside_the_slice_is_an_error_not_a_guess(monkeypatch, tmp_path):
    """A page number the slice can't account for means the mapping is wrong; a
    silently misnumbered page is worse than a failed file, which gets retried."""
    _stub_subprocess_run(monkeypatch, {"pages": [{"page": 4, "markdown": "?"}]})
    slice_path = tmp_path / "slice.pdf"
    slice_path.write_bytes(b"")

    result = preprocess._run_slice_subprocess(slice_path, [0, 1], force_ocr=False)
    assert "error" in result


@pytest.mark.parametrize("force_ocr,expected_flag", [(True, "--force-ocr"),
                                                     (False, "--no-force-ocr")])
def test_slice_child_is_told_the_verdict_never_left_to_re_derive_it(
        monkeypatch, tmp_path, force_ocr, expected_flag):
    """pdf_extract_pages rewrites the slice through pypdf, which need not
    preserve the font resources the CMap signal reads — so the child must be
    handed the parent's decision, not score the slice for itself."""
    captured = {}

    class _Completed:
        stdout = json.dumps({"pages": []})
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Completed()

    monkeypatch.setattr(preprocess.subprocess, "run", fake_run)
    slice_path = tmp_path / "slice.pdf"
    slice_path.write_bytes(b"")

    preprocess._run_slice_subprocess(slice_path, [0], force_ocr=force_ocr)
    assert expected_flag in captured["cmd"]


def test_no_force_ocr_flag_skips_detection_entirely(monkeypatch, tmp_path):
    """--no-force-ocr means the parent already decided: the child must not run
    the garble ensemble again."""
    import watchdog.pipeline.preprocess as preprocess_mod

    def explode(*a, **k):
        raise AssertionError("detection must not run when the parent has decided")

    monkeypatch.setattr(preprocess_mod, "pdf_ocr_plan", explode)
    monkeypatch.setattr(preprocess_mod, "pdf_page_count", lambda p: 3)
    monkeypatch.setattr(preprocess_mod, "sha256_file", lambda p: "abc")
    monkeypatch.setattr(preprocess_mod, "build_converter",
                        lambda force: (_ for _ in ()).throw(RuntimeError("stop here")))

    pdf = tmp_path / "slice.pdf"
    pdf.write_bytes(b"")
    result = preprocess_mod.process_with_docling(pdf, detect=False)
    assert "error" in result   # stopped at the converter, having skipped detection


# ── metadata: what the page-scoped decision reports (#605) ────────────────────

def test_metadata_names_the_pages_when_ocr_was_page_scoped(monkeypatch, tmp_path):
    import watchdog.pipeline.preprocess as preprocess_mod

    monkeypatch.setattr(preprocess_mod, "pdf_page_count", lambda p: 6)
    monkeypatch.setattr(preprocess_mod, "pdf_ocr_plan", lambda p, n: ([1, 3], [1]))
    monkeypatch.setattr(preprocess_mod, "process_pdf_slices",
                        lambda p, slices: {"pages": [{"page": 1, "markdown": "x"}]})
    monkeypatch.setattr(preprocess_mod, "sha256_file", lambda p: "abc")

    result = preprocess_mod.process_with_docling(tmp_path / "doc.pdf")
    assert result["metadata"]["ocr_pages"] == [2, 4]        # 1-indexed
    assert result["metadata"]["ocr_used"] is True
    assert result["metadata"]["garbled_detected"] is True


def test_metadata_omits_ocr_pages_when_the_whole_document_was_ocred(monkeypatch, tmp_path):
    """`ocr_used` already says it — a page list adds nothing when it's every page."""
    import watchdog.pipeline.preprocess as preprocess_mod

    monkeypatch.setattr(preprocess_mod, "pdf_page_count", lambda p: 60)
    monkeypatch.setattr(preprocess_mod, "pdf_ocr_plan", lambda p, n: (list(range(60)), []))
    monkeypatch.setattr(preprocess_mod, "process_pdf_slices",
                        lambda p, slices: {"pages": [{"page": 1, "markdown": "x"}]})
    monkeypatch.setattr(preprocess_mod, "sha256_file", lambda p: "abc")

    result = preprocess_mod.process_with_docling(tmp_path / "doc.pdf")
    assert "ocr_pages" not in result["metadata"]
    assert result["metadata"]["ocr_used"] is True


def test_a_clean_small_pdf_never_reaches_the_slicing_path(monkeypatch, tmp_path):
    """No mixed verdict and under chunk_size: one in-process conversion, exactly
    as before #605. Slicing a document that doesn't need it would cost a
    subprocess and a Docling start-up for nothing."""
    import watchdog.pipeline.preprocess as preprocess_mod

    monkeypatch.setattr(preprocess_mod, "pdf_page_count", lambda p: 5)
    monkeypatch.setattr(preprocess_mod, "pdf_ocr_plan", lambda p, n: ([], []))
    monkeypatch.setattr(preprocess_mod, "process_pdf_slices",
                        lambda p, slices: pytest.fail("must not slice a uniform small PDF"))
    monkeypatch.setattr(preprocess_mod, "build_converter",
                        lambda force: (_ for _ in ()).throw(RuntimeError("stop here")))

    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"")
    assert "error" in preprocess_mod.process_with_docling(pdf)


# ── pdf_ocr_plan — real corpus documents (#580, #605) ─────────────────────────
# Slow-ish (real pypdf extraction on real files) and skipped when the corpus
# isn't present, so kept separate from the fast synthetic tests above.

_CORPUS_DIR = Path(__file__).resolve().parent.parent / "benchmarks" / "corpora" / "extract"

# (total_pages, force_pages, garbled_pages) — measured over every page, which is
# strictly stronger than the three-page sample the #580 table was built from.
_CORPUS_EXPECTED = {
    # Page 1 is an image-only cover; the rest of the text layer is sound. Under
    # the pre-#605 sample this document was the false positive that forced OCR
    # over all 36 pages and flattened its reconciliation table.
    "Annual-Financial-Report-19-20.pdf": (36, [0], []),
    "Annual-Financial-Report-20-21.pdf": (70, [], []),
    # Pages 25-31 are a scanned court order bound into a born-digital report —
    # the mixed document this issue exists for.
    "Laurentian First Report of the Monitor.pdf": (34, [24, 25, 26, 27, 28, 29, 30], []),
    "Laurentian Pre-Filing Report of the Proposed Monitor.pdf": (47, [], []),
    # Genuinely no text layer on any page: still forced, all 17.
    "CV-21-00656040-00CL Laurentian U Initial Order 1 FEB 2021.pdf": (17, list(range(17)), []),
    "Pension Order Morawetz CJ- March 17 2021(as stamped by Court).PDF": (5, [], []),
}


@pytest.mark.skipif(not _CORPUS_DIR.is_dir(), reason="benchmark corpus not present")
@pytest.mark.parametrize("filename,expected", _CORPUS_EXPECTED.items())
def test_corpus_ocr_plans(filename, expected):
    """Regression table from #580/#597, now page by page: the AFR 19-20 misfire
    must stay fixed, the Initial Order's genuine empty text layer must keep
    forcing OCR, and no clean page in any of the six may read as garbled."""
    total_pages, force_pages, garbled_pages = expected
    assert preprocess.pdf_page_count(_CORPUS_DIR / filename) == total_pages
    result = preprocess.pdf_ocr_plan(_CORPUS_DIR / filename, total_pages)
    assert result == (force_pages, garbled_pages), (
        f"{filename}: expected (force, garbled)={(force_pages, garbled_pages)}, got {result}"
    )



