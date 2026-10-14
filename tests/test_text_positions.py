"""Saved positions of OCR'd text (D289): read from a Docling conversion, carried through sliced
pre-processing, written to `.watchdog/text-positions/<sha>.json` and read back a page at a time."""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

import watchdog.pipeline.preprocess as preprocess_mod
import watchdog.pipeline.preprocess_batch as ppb
from watchdog.pipeline import text_positions as tp


def _cell(text, left, t, r, b, origin="CoordOrigin.TOPLEFT", ocr=True):
    bbox = NS(l=left, t=t, r=r, b=b, coord_origin=origin)
    return NS(text=text, from_ocr=ocr, rect=NS(to_bounding_box=lambda: bbox))


def _page(no, cells, w=612.0, h=792.0, parsed=True):
    return NS(page_no=no, size=NS(width=w, height=h),
              parsed_page=NS(textline_cells=cells) if parsed else None)


def test_from_conversion_keeps_ocr_lines_in_top_left_page_units():
    conv = NS(pages=[
        _page(1, [_cell("Harbour Commission", 52.24, 104.6, 429.1, 121.44),
                  _cell("native text", 1, 1, 2, 2, ocr=False),
                  _cell("   ", 1, 1, 2, 2)]),
        # A bottom-left box is flipped onto the top-left origin the app draws in.
        _page(2, [_cell("Pier 9", 50, 700, 120, 680, origin="CoordOrigin.BOTTOMLEFT")]),
        _page(3, [], parsed=True),
        _page(4, [_cell("x", 0, 0, 1, 1)], parsed=False),
    ])
    out = tp.from_conversion(conv)
    assert out == {
        "1": {"width": 612.0, "height": 792.0, "lines": [[52.2, 104.6, 429.1, 121.4, "Harbour Commission"]]},
        "2": {"width": 612.0, "height": 792.0, "lines": [[50.0, 92.0, 120.0, 112.0, "Pier 9"]]},
    }


def test_from_conversion_never_raises_on_an_unexpected_shape():
    assert tp.from_conversion(NS(pages=[NS(page_no="x")])) == {}
    assert tp.from_conversion(NS()) == {}


def test_renumber_maps_slice_pages_to_the_original_and_refuses_strays():
    pages = {"1": {"lines": []}, "2": {"lines": [1]}}
    assert tp.renumber(pages, [3, 7]) == {"4": {"lines": []}, "8": {"lines": [1]}}
    assert tp.renumber({"3": {}}, [3, 7]) is None


def test_write_then_read_a_page_at_a_time(tmp_path):
    pages = {str(n): {"width": 612, "height": 792, "lines": [[1, 2, 3, 4, f"line {n}"]]} for n in (2, 5)}
    path = tp.write(tmp_path, "ab" * 32, tp.block(pages, "rapidocr"))
    assert path == tmp_path / ".watchdog" / "text-positions" / f"{'ab' * 32}.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["format"] == 1 and stored["unit"] == "line" and stored["engine"] == "rapidocr"

    assert tp.available_pages(tmp_path, "ab" * 32) == [2, 5]
    index = tp.read(tmp_path, "ab" * 32)
    assert index["pages"] == [2, 5] and index["boxes"] == {}
    one = tp.read(tmp_path, "ab" * 32, [5, 9])
    assert one["boxes"] == {"5": {"width": 612.0, "height": 792.0, "lines": [[1.0, 2.0, 3.0, 4.0, "line 5"]]}}


def test_read_without_a_file_or_with_a_bad_one_is_empty(tmp_path):
    assert tp.read(tmp_path, "cd" * 32, [1]) == {"unit": None, "engine": None, "pages": [], "boxes": {}}
    path = tp.path_for(tmp_path, "cd" * 32)
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert tp.read(tmp_path, "cd" * 32, [1])["pages"] == []
    path.write_text(json.dumps({"pages": {"1": {"width": 0, "height": 5, "lines": []},
                                          "2": {"width": 5, "height": 5, "lines": [[1, 2, "x"], [1, 2, 3, 4, "ok"]]}}}),
                    encoding="utf-8")
    out = tp.read(tmp_path, "cd" * 32, [1, 2])
    assert out["boxes"] == {"2": {"width": 5.0, "height": 5.0, "lines": [[1.0, 2.0, 3.0, 4.0, "ok"]]}}


def test_read_is_capped_per_call(tmp_path):
    pages = {str(n): {"width": 1, "height": 1, "lines": []} for n in range(1, 80)}
    tp.write(tmp_path, "ef" * 32, tp.block(pages, "auto"))
    assert len(tp.read(tmp_path, "ef" * 32, list(range(1, 80)))["boxes"]) == 50


def test_writing_no_positions_removes_a_stale_file(tmp_path):
    sha = "12" * 32
    tp.write(tmp_path, sha, tp.block({"1": {"width": 1, "height": 1, "lines": []}}, "auto"))
    assert tp.path_for(tmp_path, sha).exists()
    assert tp.write(tmp_path, sha, None) is None
    assert not tp.path_for(tmp_path, sha).exists()


# ── carried through pre-processing ────────────────────────────────────────────

def test_slice_subprocess_renumbers_positions(tmp_path, monkeypatch):
    payload = {"pages": [{"page": 1, "markdown": "a"}, {"page": 2, "markdown": "b"}],
               "text_positions": tp.block({"2": {"width": 1, "height": 1, "lines": []}}, "auto")}
    monkeypatch.setattr(preprocess_mod.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, json.dumps(payload), ""))
    out = preprocess_mod._run_slice_subprocess(tmp_path / "s.pdf", [1, 3], True)
    assert [p["page"] for p in out["pages"]] == [2, 4]
    assert list(out["text_positions"]["pages"]) == ["4"]


def test_slice_subprocess_refuses_positions_for_a_page_it_does_not_have(tmp_path, monkeypatch):
    payload = {"pages": [{"page": 1, "markdown": "a"}],
               "text_positions": tp.block({"5": {"width": 1, "height": 1, "lines": []}}, "auto")}
    monkeypatch.setattr(preprocess_mod.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, json.dumps(payload), ""))
    assert "error" in preprocess_mod._run_slice_subprocess(tmp_path / "s.pdf", [0], True)


def test_process_pdf_slices_merges_positions_from_the_ocr_slices(tmp_path, monkeypatch):
    def fake_extract(src, indices):
        p = tmp_path / f"slice-{indices[0]}.pdf"
        p.write_bytes(b"")
        return p

    def fake_slice(slice_path, page_numbers, force_ocr):
        out = {"pages": [{"page": n + 1, "markdown": str(n + 1)} for n in page_numbers]}
        if force_ocr:
            out["text_positions"] = tp.block({str(n + 1): {"width": 1, "height": 1, "lines": []}
                                              for n in page_numbers}, "rapidocr")
        return out

    monkeypatch.setattr(preprocess_mod, "pdf_extract_pages", fake_extract)
    monkeypatch.setattr(preprocess_mod, "_run_slice_subprocess", fake_slice)
    result = preprocess_mod.process_pdf_slices(Path("/fake.pdf"), [([0, 2], False), ([1, 3], True)])
    assert sorted(result["text_positions"]["pages"]) == ["2", "4"]
    assert result["text_positions"]["engine"] == "rapidocr"

    monkeypatch.setattr(preprocess_mod, "_run_slice_subprocess",
                        lambda s, pages, f: {"pages": [{"page": n + 1, "markdown": "x"} for n in pages]})
    assert "text_positions" not in preprocess_mod.process_pdf_slices(Path("/fake.pdf"), [([0, 1], False)])


def test_parsed_pages_are_kept_only_where_docling_has_the_option():
    class New:
        model_fields = {"generate_parsed_pages": None, "do_ocr": None}

    class Old:
        model_fields = {"do_ocr": None}

    assert preprocess_mod._keep_parsed_pages(New) == {"generate_parsed_pages": True}
    assert preprocess_mod._keep_parsed_pages(Old) == {}


@pytest.mark.parametrize("positions", [None, tp.block({"1": {"width": 1, "height": 1, "lines": [[0, 0, 1, 1, "Pier"]]}}, "auto")])
def test_pre_processing_writes_positions_beside_the_queue_never_into_it(tmp_path, monkeypatch, positions):
    vault = tmp_path / "vault"
    incoming = vault / "incoming"
    queue = vault / ".watchdog" / "queue"
    staging = vault / ".watchdog" / "staging"
    for d in (incoming, queue, staging):
        d.mkdir(parents=True)
    f = incoming / "scan.pdf"
    f.write_bytes(b"")
    sha = "9f" * 32
    result = {"sha256": sha, "pages": [{"page": 1, "markdown": "Pier"}], "char_count": 4,
              "page_count": 1, "source_path": str(f)}
    if positions:
        result["text_positions"] = positions
    monkeypatch.setattr(ppb, "preprocess_one", lambda path, *a, **kw: dict(result))

    ppb._run_ingest_inner(vault, incoming, queue, staging, workers=1, chunk_workers=None, files=[f])

    queued = json.loads((queue / f"{sha}.json").read_text(encoding="utf-8"))
    assert "text_positions" not in queued
    assert tp.path_for(vault, sha).exists() == bool(positions)
    if positions:
        assert tp.read(vault, sha, [1])["boxes"]["1"]["lines"][0][4] == "Pier"
