"""Unit tests for `benchmarks/verify_keys.py` (#625).

The real check runs against a chewed benchmark run, and `benchmarks/runs/` is
gitignored — so everything here is synthetic. That is the point: the fixtures
below encode the specific conversion artifacts the checker exists to see
through, each of which cost a false alarm to find on the real corpus.
"""
import json
import sys
from pathlib import Path

import pytest
import yaml

BENCHMARKS_DIR = Path(__file__).resolve().parent.parent / "benchmarks"
if str(BENCHMARKS_DIR) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS_DIR))

import verify_keys  # noqa: E402


PAGES = {
    1: "The Court ordered a stay of proceedings until March 31, 2021.",
    2: "Payroll & Benefits totalled $4,903 for the period. The furlough days",
    3: "provided LU with a one-time cash benefit of $450,000 in fiscal 201920.",
}


def entry(**kw):
    return {"id": "F1", **kw}


def check(**kw):
    return verify_keys.check_entry(entry(**kw), PAGES)[0]


# ── locating a quote ───────────────────────────────────────────────────────

def test_string_quote_on_its_cited_page_is_ok():
    assert check(page=1, quote="a stay of proceedings until March 31, 2021") == "ok"


def test_list_quote_with_every_span_on_the_cited_page_is_ok():
    assert check(page=2, quote=["Payroll & Benefits", "4,903"]) == "ok"


def test_quote_on_a_page_the_entry_does_not_cite_is_offpage():
    assert check(page=1, quote="Payroll & Benefits totalled") == "offpage"


def test_quote_that_is_nowhere_in_the_document_fails():
    assert check(page=1, quote="a stay of proceedings until December 25, 1999") == "fail"


def test_quote_with_no_page_cite_at_all_is_nocite():
    assert check(quote="a stay of proceedings") == "nocite"


def test_entry_with_no_quote_has_no_spans():
    assert verify_keys.spans_of(entry(page=1)) == []


# ── the two spellings of the page field ────────────────────────────────────

def test_pages_list_is_read_the_same_as_page():
    """Keys spell this field both ways. Reading only `page` reported every
    `pages` entry as uncited and produced eleven false positives."""
    assert check(pages=[2], quote="Payroll & Benefits") == "ok"


def test_page_given_as_a_string_range_is_parsed():
    assert check(page="2-3", quote="Payroll & Benefits") == "ok"


def test_a_span_list_may_straddle_the_pages_it_cites():
    assert check(pages=[2, 3],
                 quote=["The furlough days",
                        "provided LU with a one-time cash benefit"]) == "ok"


# ── conversion artifacts the checker must see through ──────────────────────

def test_hyphen_lost_at_a_line_break_still_matches():
    """The chew renders `2019-20` as `201920` where the word broke across a
    line. A correctly copied quote must not fail over where the line wrapped."""
    assert check(page=3, quote="in fiscal 2019-20") == "ok"


def test_html_entity_in_the_chew_still_matches_the_printed_ampersand():
    pages = {1: "Payroll &amp; Benefits"}
    assert verify_keys.check_entry(
        entry(page=1, quote="Payroll & Benefits"), pages)[0] == "ok"


def test_whitespace_and_quote_padding_are_ignored():
    pages = {1: "the  ' Initial  Order '  was  granted"}
    assert verify_keys.check_entry(
        entry(page=1, quote='the "Initial Order" was granted'), pages)[0] == "ok"


def test_markdown_table_pipes_do_not_block_a_match():
    pages = {1: "| Legal fees | 4,903 |"}
    assert verify_keys.check_entry(entry(page=1, quote="Legal fees"), pages)[0] == "ok"


# ── elided quotes (the #630 case, applied to keys) ─────────────────────────

@pytest.mark.parametrize("mark", ["…", "..."])
def test_an_elided_quote_verifies_from_its_kept_parts(mark):
    assert check(page=2, quote=f"Payroll & Benefits {mark} for the period") == "ok"


def test_an_elided_quote_whose_parts_are_out_of_order_fails():
    """Otherwise any two phrases sharing a page would 'verify' as a quote
    nobody wrote."""
    assert check(page=2, quote="for the period … Payroll & Benefits") == "fail"


def test_an_elision_may_cross_a_page_break():
    assert check(pages=[2, 3],
                 quote="The furlough days … cash benefit of $450,000") == "ok"


def test_an_elided_quote_with_an_unfindable_part_fails():
    assert check(page=2, quote="Payroll & Benefits … never printed anywhere") == "fail"


# ── end to end ─────────────────────────────────────────────────────────────

def _write_corpus(tmp_path, key_entries):
    keys = tmp_path / "keys"
    pages = tmp_path / "pages"
    keys.mkdir()
    pages.mkdir()
    (pages / "doc.json").write_text(json.dumps({
        "sha256": "abc123",
        "filename": "doc.pdf",
        "pages": [{"page": n, "markdown": md} for n, md in PAGES.items()],
    }))
    (keys / "doc.yaml").write_text(yaml.safe_dump({
        "document": {"sha256": "abc123", "file": "doc.pdf", "pages": 3},
        "facts": key_entries,
    }))
    return keys, pages


def test_verify_reports_clean_over_a_sound_key(tmp_path):
    keys, pages = _write_corpus(tmp_path, [
        {"id": "F1", "page": 1, "quote": "a stay of proceedings"},
        {"id": "F2", "page": 2, "quote": ["Payroll & Benefits", "4,903"]},
    ])
    totals, problems, skipped = verify_keys.verify(str(keys), str(pages))
    assert totals["ok"] == 2
    assert problems == [] and skipped == []


def test_verify_flags_a_wrong_page_and_main_exits_nonzero(tmp_path, capsys):
    keys, pages = _write_corpus(tmp_path, [
        {"id": "F1", "page": 3, "quote": "a stay of proceedings"},
    ])
    totals, problems, _ = verify_keys.verify(str(keys), str(pages))
    assert totals["offpage"] == 1
    assert problems[0][1] == "facts:F1" and problems[0][2] == "offpage"
    assert verify_keys.main(["--keys", str(keys), "--pages", str(pages)]) == 1
    assert "OFFPAGE" in capsys.readouterr().out


def test_a_key_whose_document_is_absent_from_the_chew_is_skipped_not_failed(tmp_path):
    """A run that chewed five of six documents must not look like a key defect."""
    keys, pages = _write_corpus(tmp_path, [{"id": "F1", "page": 1, "quote": "a stay"}])
    (pages / "doc.json").unlink()
    totals, problems, skipped = verify_keys.verify(str(keys), str(pages))
    assert skipped == ["doc"] and problems == [] and totals["ok"] == 0
