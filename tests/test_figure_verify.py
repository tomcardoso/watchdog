from watchdog.pipeline.figure_verify import verify_figures


def _fact(fact, page=3, basis=None):
    d = {"fact": fact, "page": page}
    if basis is not None:
        d["basis"] = basis
    return {"document": {"key_facts": [d]}}


# ── separator drift ──────────────────────────────────────────────────────────

def test_matches_across_comma_grouping_drift():
    extraction = _fact("Paid $430,000 to the fund.")
    pages = {3: "The transfer totalled 430,000 dollars."}
    assert verify_figures(extraction, pages) == []


def test_matches_across_space_grouping_drift():
    extraction = _fact("Paid $430,000 to the fund.")
    pages = {3: "The transfer totalled 430 000 dollars."}
    assert verify_figures(extraction, pages) == []


# ── currency symbols / percent signs don't block a match ────────────────────

def test_percent_sign_does_not_block_match():
    extraction = _fact("Transfer ratio set at 4.5%.")
    pages = {3: "The transfer ratio was 4.5 per cent as of the valuation date."}
    assert verify_figures(extraction, pages) == []


def test_currency_symbol_does_not_block_match():
    extraction = _fact("Paid $842,018.34 to the fund.")
    pages = {3: "The company paid 842,018.34 to the fund on that date."}
    assert verify_figures(extraction, pages) == []


# ── genuinely absent figure ──────────────────────────────────────────────────

def test_absent_figure_produces_exactly_one_warning():
    extraction = _fact("Paid $500,000 to the fund.")
    pages = {3: "Nothing about payments on this page."}
    warnings = verify_figures(extraction, pages)
    assert len(warnings) == 1
    assert "500000" in warnings[0]
    assert "key_facts[0]" in warnings[0]


# ── adjacent page ────────────────────────────────────────────────────────────

def test_match_on_adjacent_page_produces_no_warning():
    extraction = _fact("Paid $500,000 to the fund.", page=4)
    pages = {3: "The transfer of 500,000 dollars was recorded.", 4: "Cited page has no figure."}
    assert verify_figures(extraction, pages) == []


# ── inferred facts are exempt ────────────────────────────────────────────────

def test_inferred_fact_with_bogus_figure_is_skipped():
    extraction = _fact("Paid $999,999,999 to the fund.", basis="inferred")
    pages = {3: "Nothing about payments here."}
    assert verify_figures(extraction, pages) == []


# ── no digits → skip ──────────────────────────────────────────────────────────

def test_fact_with_no_digits_is_skipped():
    extraction = _fact("The board approved the transfer.")
    pages = {3: "Unrelated text."}
    assert verify_figures(extraction, pages) == []


# ── no page cited / bool page → skip ─────────────────────────────────────────

def test_no_page_cited_is_skipped():
    extraction = {"document": {"key_facts": [{"fact": "Paid $500,000."}]}}
    pages = {3: "Unrelated."}
    assert verify_figures(extraction, pages) == []


def test_bool_page_is_skipped():
    extraction = {"document": {"key_facts": [{"fact": "Paid $500,000.", "page": True}]}}
    pages = {1: "Unrelated."}
    assert verify_figures(extraction, pages) == []


# ── no page text for the cited page → skip (finalize re-run posture) ────────

def test_no_page_text_available_is_skipped():
    extraction = _fact("Paid $500,000 to the fund.")
    assert verify_figures(extraction, {}) == []


# ── leading-zero / trailing-decimal-zero normalization ───────────────────────

def test_leading_zero_normalizes_to_match():
    extraction = _fact("Filed under docket 5.")
    pages = {3: "The matter was filed under docket 05 last year."}
    assert verify_figures(extraction, pages) == []


def test_trailing_decimal_zero_normalizes_to_match():
    extraction = _fact("Rate set at 1.50%.")
    pages = {3: "The applicable rate is 1.5 per cent."}
    assert verify_figures(extraction, pages) == []


# ── the derived-sum failure class (#363 / Tributary finding) ────────────────

def test_derived_sum_not_traceable_to_page_is_flagged():
    extraction = _fact("$430,000 across two transfers.")
    pages = {3: "The company recorded transfers of $250,000 and $180,000."}
    warnings = verify_figures(extraction, pages)
    assert len(warnings) == 1
    assert "430000" in warnings[0]


# ── multi-number fact, only some missing ─────────────────────────────────────

def test_multi_number_fact_lists_only_the_missing_figures():
    extraction = _fact("Paid $250,000 in January and $999,000 in February.")
    pages = {3: "A payment of $250,000 was recorded in January."}
    warnings = verify_figures(extraction, pages)
    assert len(warnings) == 1
    assert "999000" in warnings[0]
    assert "250000" not in warnings[0]
