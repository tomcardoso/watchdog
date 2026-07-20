"""Tier 0 candidate harvest (#361/D123): deterministic regex spans + optional GLiNER entities."""

import builtins
import sys
import types
import warnings

from watchdog.pipeline import harvest


def _paged(text, page=1):
    return f"<!-- PAGE {page} -->\n\n{text}"


# ── money / figure ────────────────────────────────────────────────────────────────────────

def test_money_forms():
    text = _paged("Fees of $66.7 million, $(66,671), $590, and $1,412 thousand were noted.")
    values = {c["value"] for c in harvest.harvest(text) if c["kind"] == "money"}
    assert values == {"$66.7 million", "$(66,671)", "$590", "$1,412 thousand"}


def test_money_dollar_space_and_letter_scale():
    text = _paged("Total $ 4,903 was recorded, up from $85.9M last year.")
    values = {c["value"] for c in harvest.harvest(text) if c["kind"] == "money"}
    assert {"$ 4,903", "$85.9M"} <= values


def test_bare_figures_are_not_labelled_money():
    text = _paged("Total assets were 186,820 and liabilities (89,207) as reported.")
    figs = {c["value"] for c in harvest.harvest(text) if c["kind"] == "figure"}
    assert figs == {"186,820", "(89,207)"}
    assert not [c for c in harvest.harvest(text) if c["kind"] == "money"]


def test_figure_regex_does_not_double_count_digits_inside_a_money_match():
    text = _paged("The adjustment was $(66,671) in total.")
    kinds = [c["kind"] for c in harvest.harvest(text)]
    assert kinds.count("money") == 1
    assert kinds.count("figure") == 0


def test_figure_captures_decimal_tail_whole():
    text = _paged("Revenue for the period was 16,423.53 in total.")
    figs = {c["value"] for c in harvest.harvest(text) if c["kind"] == "figure"}
    assert "16,423.53" in figs
    assert "16,423" not in figs


# ── percent / date / docket ──────────────────────────────────────────────────────────────

def test_percent_forms():
    text = _paged("Rates were 85.4%, 2.0%, and 8.50% respectively.")
    pct = {c["value"] for c in harvest.harvest(text) if c["kind"] == "percent"}
    assert pct == {"85.4%", "2.0%", "8.50%"}


def test_date_forms_iso_month_name_and_french():
    text = _paged("Filed January 30, 2021, then 30 January 2021, then 2021-01-30, then 17 mars 2021.")
    dates = {c["value"] for c in harvest.harvest(text) if c["kind"] == "date"}
    assert dates == {"January 30, 2021", "30 January 2021", "2021-01-30", "17 mars 2021"}


def test_date_forms_abbreviated_english_months():
    text = _paged("Signed Sep. 26, 2021, then Sept 26, 2021, then Jan 4, 2020.")
    dates = {c["value"] for c in harvest.harvest(text) if c["kind"] == "date"}
    assert {"Sep. 26, 2021", "Sept 26, 2021", "Jan 4, 2020"} <= dates


def test_docket_form():
    text = _paged("Court file no. CV-21-00656040-00CL was opened.")
    dockets = {c["value"] for c in harvest.harvest(text) if c["kind"] == "docket"}
    assert dockets == {"CV-21-00656040-00CL"}


# ── table-row bare figures ───────────────────────────────────────────────────────────────

def test_table_row_bare_figures_are_harvested():
    text = _paged("| Consulting fees | 223 order |")
    figs = {c["value"] for c in harvest.harvest(text) if c["kind"] == "figure"}
    assert "223" in figs


def test_bare_figures_in_prose_are_not_harvested():
    text = _paged("The report noted about 223 people were affected.")
    figs = [c for c in harvest.harvest(text) if c["kind"] == "figure"]
    assert figs == []


def test_table_row_standalone_year_is_not_harvested_as_figure():
    text = _paged("| Revenue | 2020 | 2021 |")
    figs = {c["value"] for c in harvest.harvest(text) if c["kind"] == "figure"}
    assert "2020" not in figs
    assert "2021" not in figs


# ── page attribution ─────────────────────────────────────────────────────────────────────

def test_page_attribution_and_pre_marker_text_is_page_none():
    text = "Filed for $500 before any marker.\n\n<!-- PAGE 3 -->\n\n$700 more on page three."
    cands = harvest.harvest(text)
    pre = [c for c in cands if c["page"] is None]
    on3 = [c for c in cands if c["page"] == 3]
    assert any(c["value"] == "$500" for c in pre)
    assert any(c["value"] == "$700" for c in on3)


def test_text_with_no_markers_at_all_is_page_none():
    cands = harvest.harvest("Just $500 with no page markers at all.")
    assert cands and all(c["page"] is None for c in cands)


# ── dedupe + cap ──────────────────────────────────────────────────────────────────────────

def test_recurring_header_capped_at_three_pages():
    text = "".join(
        f"<!-- PAGE {n} -->\n\nCV-21-00656040-00CL header text.\n\n" for n in range(1, 11)
    )
    cands = harvest.harvest(text)
    docket_pages = [c["page"] for c in cands if c["kind"] == "docket"]
    assert docket_pages == [1, 2, 3]


def test_per_page_cap_eighty_with_priority_order():
    # 85 distinct bare figures (low priority) plus 2 money values (top priority) on one page —
    # over the 80 cap, so priority ordering must keep both money candidates and drop the
    # lowest-priority (figure) candidates first, in their original order of appearance.
    figures = " ".join(f"{n},000" for n in range(100, 185))
    text = _paged(f"{figures} $500 and $1,412 were also noted.")
    cands = harvest.harvest(text)
    assert len(cands) == 80
    values = {c["value"] for c in cands}
    assert "$500" in values and "$1,412" in values
    assert "100,000" in values          # first-appearing figure survives
    assert "177,000" in values          # 78th figure (2 money + 78 figures == 80)
    assert "178,000" not in values      # 79th figure dropped by the cap
    assert "184,000" not in values


# ── format_checklist ─────────────────────────────────────────────────────────────────────

def test_format_checklist_exact_format():
    cands = [
        {"page": 52, "kind": "money", "value": "$590"},
        {"page": 52, "kind": "percent", "value": "2.0%"},
        {"page": 52, "kind": "percent", "value": "3.5%"},
        {"page": 52, "kind": "person", "value": "Robert Haché"},
    ]
    assert harvest.format_checklist(cands) == (
        "p.52: [money] $590 · [percent] 2.0% · [percent] 3.5% · [person] Robert Haché"
    )


def test_format_checklist_empty_input():
    assert harvest.format_checklist([]) == ""


def test_format_checklist_page_none_renders_as_p_question_mark():
    cands = [{"page": None, "kind": "money", "value": "$1"}]
    assert harvest.format_checklist(cands) == "p.?: [money] $1"


# ── GLiNER absence ────────────────────────────────────────────────────────────────────────

def test_harvest_entities_returns_empty_when_gliner_not_installed(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "gliner":
            raise ImportError("no module named gliner")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(harvest, "_gliner_model", None)
    assert harvest.harvest_entities({1: "Some text about Robert Haché."}) == []


def test_harvest_and_checklist_unaffected_by_missing_gliner(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "gliner":
            raise ImportError("no module named gliner")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(harvest, "_gliner_model", None)
    text = _paged("Fees of $590 were charged.")
    cands = harvest.harvest(text) + harvest.harvest_entities(harvest.split_pages(text))
    assert harvest.format_checklist(cands) == "p.1: [money] $590"


def test_load_gliner_suppresses_load_time_warnings_and_stderr(monkeypatch, capsys):
    """`from_pretrained` emits a deprecation UserWarning and writes an unauthenticated-requests
    notice straight to stderr on load (#419) — neither is a predict-time warning, so they slip
    past harvest_entities' own catch_warnings. The live ingest board isn't resilient to
    arbitrary foreign stderr, so this load path must stay silent by construction."""
    class _FakeGLiNER:
        @staticmethod
        def from_pretrained(name):
            warnings.warn("The `resume_download` argument is deprecated", UserWarning)
            print("Warning: you are sending unauthenticated requests", file=sys.stderr)
            return "fake-model"

    monkeypatch.setitem(sys.modules, "gliner", types.SimpleNamespace(GLiNER=_FakeGLiNER))
    monkeypatch.setattr(harvest, "_gliner_model", None)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        model = harvest._load_gliner()

    assert model == "fake-model"
    assert caught == []
    assert capsys.readouterr().err == ""


# ── real-corpus regression fixtures (#361 benchmark misses) ────────────────────────────────

def test_regression_university_administrative_fee():
    text = _paged("the University charged an administrative fee of $590 to the endowment")
    values = {c["value"] for c in harvest.harvest(text) if c["kind"] == "money"}
    assert "$590" in values


def test_regression_cash_restated():
    text = _paged("cash of $7,505 as previously reported was restated to $5,185")
    values = {c["value"] for c in harvest.harvest(text) if c["kind"] == "money"}
    assert {"$7,505", "$5,185"} <= values


def test_regression_owed_to_nosm():
    text = _paged("owed to NOSM of $1,412 (2020 – $28)")
    values = {c["value"] for c in harvest.harvest(text) if c["kind"] == "money"}
    assert {"$1,412", "$28"} <= values
