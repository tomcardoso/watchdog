"""Unreadable source text (D392): a page whose converted text came through as codes or symbols
rather than words is flagged on the document's record and in processing.log, does not count as
words to extract from (neither for the empty-extraction guard nor the thin-extraction warning),
and is not offered a retry. Every page below is invented."""

import asyncio
import json

import pytest

from watchdog.pipeline import orchestrate, postflight
from watchdog.pipeline.postflight import find_unreadable_pages

from tests.test_orchestrate import _extraction, _mock, _queue_doc
from tests.test_write_vault import make_vault

pytest_plugins = ["tests.gui_support"]   # the wdg_home fixture

# ── invented pages ────────────────────────────────────────────────────────────────────────

# Glyph codes read as digits: long runs with no separators, the shape a broken text layer gives.
DIGIT_RUNS = "\n".join(
    " ".join(f"{(i * 7919 + j * 104729) % 10**12:012d}{j % 10}" for j in range(8)) for i in range(14)
)
# The same codes split into short zero-led groups.
SHORT_CODES = " ".join(f"00{(i * 37) % 90 + 10}" for i in range(120))
# Symbol soup: no letters and no digits.
SYMBOLS = " ".join(["□□", "◆", "▯▯▯", "@@", "§¶", "†‡"] * 30)

FINANCIAL = """## Statement of financial position
As at December 31

| | 2025 | 2024 |
|---|---|---|
| Cash and cash equivalents | 1,204,331 | 987,002 |
| Accounts receivable | 412,880 | 377,105 |
| Total assets | 9,871,442 | 8,920,317 |

Cash 1,204,331 987,002 1,104,220 950,331
Receivables 412,880 377,105 401,990 365,442
Inventory 88,210 91,004 79,315 84,120
Total 1,705,421 1,455,111 1,585,525 1,399,893
Note 4 (12,500) (11,250) 3.5% 4.1%
"""
# A ledger page of mostly figures and dates, with hardly a word on it.
LEDGER = "\n".join(
    f"2025-{m:02d}-{d:02d} {1000 + d} {d * 125:,}.00 {(d * 125) // 2:,}.50 ({d * 60:,}.00)"
    for m in range(1, 4) for d in range(1, 11)
) + "\nTotals"
# A spreadsheet of numbers rendered as a table, and the same as raw comma-separated text.
NUMERIC_TABLE = "| Year | A | B | C |\n|---|---|---|---|\n" + "\n".join(
    f"| {2000 + i} | {i * 13} | {i * 17} | {i * 19} |" for i in range(40))
# A register of reference numbers: codes, but in a table, so a real listing.
CODE_TABLE = "| Reference | Parcel |\n|---|---|\n" + "\n".join(
    f"| {40012345678900 + i} | 00{i % 90 + 10} |" for i in range(40))
CSV = "year,a,b,c\n" + "\n".join(f"{2000 + i},{i * 13},{i * 17},{i * 19}" for i in range(40))
SHORT_FORM = "Application form\nName: ______ Date: 2025-01-01\nSignature: ______"
FRENCH = ("Le tribunal a ordonné à la société de déposer ses états financiers avant le 30 juin, "
          "faute de quoi une amende de 5 000 $ par jour sera imposée. ") * 4
RUSSIAN = ("Суд постановил, что компания обязана представить отчётность до 30 июня, "
           "иначе будет наложен штраф в размере 5 000 рублей в день. ") * 4
ARABIC = "قررت المحكمة أن على الشركة تقديم بياناتها المالية قبل 30 يونيو وإلا فرضت غرامة يومية. " * 4
CHINESE = "法院裁定该公司须于六月三十日前提交财务报表，否则每日罚款五千元。 " * 6
JAPANESE = "裁判所は 会社に対し 6月30日 までに 財務諸表を 提出するよう 命じた。 罰金は 1日 5,000円 である。 " * 4
PROSE = ("The board approved the transfer of the property to the holding company on the date "
         "shown in the schedule, and the minutes record no objection from any director. ") * 4


@pytest.mark.parametrize("text", [DIGIT_RUNS, SHORT_CODES, SYMBOLS], ids=["digit-runs", "short-codes", "symbols"])
def test_codes_and_symbols_are_unreadable(text):
    assert find_unreadable_pages({1: text}) == [1]


@pytest.mark.parametrize("text", [FINANCIAL, LEDGER, NUMERIC_TABLE, CODE_TABLE, CSV, SHORT_FORM, FRENCH, RUSSIAN,
                                  ARABIC, CHINESE, JAPANESE, PROSE, ""],
                         ids=["financial", "ledger", "numeric-table", "code-table", "csv", "short-form", "french",
                              "russian", "arabic", "chinese", "japanese", "prose", "blank"])
def test_real_documents_are_readable(text):
    assert find_unreadable_pages({1: text}) == []


def test_a_few_codes_among_words_do_not_make_a_page_unreadable():
    # Account numbers and phone numbers in a letter are codes, but the page is words.
    text = PROSE + " ".join(["40012345678901", "0041", "0036"] * 5)
    assert find_unreadable_pages({1: text}) == []


def test_only_the_unreadable_pages_are_named():
    assert find_unreadable_pages({1: PROSE, 2: DIGIT_RUNS, 3: FINANCIAL, 4: SYMBOLS}) == [2, 4]


def test_threshold_is_configurable_and_zero_turns_it_off(monkeypatch):
    monkeypatch.setattr(postflight, "_config_get",
                        lambda k, d: 0 if k == "unreadable_text_letter_share" else d)
    assert find_unreadable_pages({1: DIGIT_RUNS}) == []
    # A page of codes with a few words on it is caught only with a higher share.
    mixed = DIGIT_RUNS + "\n" + " ".join(["page"] * 15)
    monkeypatch.setattr(postflight, "_config_get", lambda k, d: d)
    assert find_unreadable_pages({1: mixed}) == []
    monkeypatch.setattr(postflight, "_config_get",
                        lambda k, d: 0.3 if k == "unreadable_text_letter_share" else d)
    assert find_unreadable_pages({1: mixed}) == [1]


def test_warning_names_the_pages_and_what_to_do():
    some = postflight.render_unreadable_warning({"pages": [2, 3, 4, 7], "page_count": 9})
    assert "pages 2–4 and 7 of 9" in some
    assert "retrying will not help" in some and "OCR" in some
    whole = postflight.render_unreadable_warning({"pages": [1, 2], "page_count": 2})
    assert whole.startswith("its text could not be read:")


# ── the guards that weigh words of text ───────────────────────────────────────────────────

def _doc(facts):
    return {"document": {"sha256": "x", "filename": "f.pdf", "key_facts": facts},
            "entities": [], "morgue_entity_id": "x", "morgue_document_type": "letter"}


def test_empty_extraction_of_unreadable_text_is_not_a_failure():
    long_codes = {i: DIGIT_RUNS for i in range(1, 6)}   # well over 500 whitespace tokens
    assert sum(len(t.split()) for t in long_codes.values()) >= 500
    assert postflight._validate(_doc([]), long_codes) == []
    # The same volume of words is still a failed extraction.
    errors = postflight._validate(_doc([]), {i: PROSE for i in range(1, 6)})
    assert any("key_facts is empty" in e for e in errors)


# ── through a run ─────────────────────────────────────────────────────────────────────────

SHA = "abc123"


def _no_local_ner(monkeypatch):
    from watchdog.pipeline import harvest
    monkeypatch.setattr(harvest, "harvest_entities", lambda *a, **k: [])


def _record(vault):
    return json.loads((vault / ".watchdog" / "registry" / "documents.json").read_text())[SHA]


def test_unreadable_document_is_kept_warned_flagged_and_not_offered_a_retry(wdg_home, tmp_path, monkeypatch):
    _no_local_ner(monkeypatch)
    vault = make_vault(tmp_path)
    # Thousands of tokens of codes and one meta-fact: thin by word count, but the words are not text.
    _queue_doc(vault, sha=SHA, text="\n".join([DIGIT_RUNS] * 10))
    _mock(monkeypatch, extraction=_extraction(sha=SHA))
    asyncio.run(orchestrate.run(vault))

    rec = _record(vault)
    assert rec["unreadable_text"] == {"pages": [1], "page_count": 1}
    assert rec["thin_extraction"] is None

    log = (vault / ".watchdog" / "registry" / "processing.log").read_text(encoding="utf-8")
    assert "OK test-doc.pdf" in log
    assert any("WARN test-doc.pdf: its text could not be read" in ln for ln in log.splitlines())
    assert not (vault / ".watchdog" / "queue" / "_kept" / f"{SHA}.json").exists()

    from watchdog.gui import vaultio
    row = vaultio.document_row(vault, SHA, rec)
    assert row["unreadable_text"] == {"pages": [1], "page_count": 1}
    assert row["thin_extraction"] is None


def test_readable_document_is_not_flagged(wdg_home, tmp_path, monkeypatch):
    _no_local_ner(monkeypatch)
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha=SHA, text=FINANCIAL)
    _mock(monkeypatch, extraction=_extraction(sha=SHA))
    asyncio.run(orchestrate.run(vault))
    rec = _record(vault)
    assert rec["unreadable_text"] is None
    from watchdog.gui import vaultio
    assert vaultio.document_row(vault, SHA, rec)["unreadable_text"] is None


def test_row_ignores_a_malformed_record():
    from watchdog.gui import vaultio
    assert vaultio.unreadable_text({"unreadable_text": {"pages": "1"}}) is None
    assert vaultio.unreadable_text({"unreadable_text": {"pages": [True]}}) is None
    assert vaultio.unreadable_text({}) is None
    assert vaultio.unreadable_text({"unreadable_text": {"pages": [2], "page_count": "3"}}) == \
        {"pages": [2], "page_count": None}
