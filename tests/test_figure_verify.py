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


# ── comma-grouped numbers must not leak split fragments (#636) ──────────────

def test_comma_split_fragment_of_a_grouped_number_does_not_verify():
    """A fabricated fact citing "345" must not pass just because "345" is a
    substring fragment of the comma-grouped "12,345,000" on the page."""
    extraction = _fact("The filing lists 345 shareholders of record.", page=1, basis="stated")
    pages = {1: "Total assets were $12,345,000 as of year end."}
    warnings = verify_figures(extraction, pages)
    assert len(warnings) == 1
    assert "345" in warnings[0]
    assert extraction["document"]["key_facts"][0]["figures_unverified"] == ["345"]


# ── multi-number fact, only some missing ─────────────────────────────────────

def test_multi_number_fact_lists_only_the_missing_figures():
    extraction = _fact("Paid $250,000 in January and $999,000 in February.")
    pages = {3: "A payment of $250,000 was recorded in January."}
    warnings = verify_figures(extraction, pages)
    assert len(warnings) == 1
    assert "999000" in warnings[0]
    assert "250000" not in warnings[0]


# ── D141/#397: exact x1,000 / x1,000,000 scale match (financial "$000s" tables) ──

def test_thousands_scale_match_produces_no_warning():
    extraction = _fact("Operating grants totalled $21,406,000 in the forecast period.")
    pages = {3: "Operating Grants 21,406"}
    assert verify_figures(extraction, pages) == []


def test_millions_scale_match_produces_no_warning():
    extraction = _fact("Total consolidated revenue was $193,400,000.")
    pages = {3: "Consolidated revenue of $193.4 million was reported."}
    assert verify_figures(extraction, pages) == []


def test_reverse_thousands_scale_match_produces_no_warning():
    extraction = _fact("Revenue of $21,406 thousand was recorded.")
    pages = {3: "Total operating grants were $21,406,000."}
    assert verify_figures(extraction, pages) == []


def test_scale_match_does_not_mask_a_genuinely_wrong_figure():
    extraction = _fact("Paid $430,000 across two transfers.")
    pages = {3: "The company recorded transfers of $250,000 and $180,000."}
    warnings = verify_figures(extraction, pages)
    assert len(warnings) == 1
    assert "430000" in warnings[0]
    assert "may be derived or garbled" in warnings[0]


# ── D141/#397: figure verbatim elsewhere in the document → softer citation warning ──

def test_figure_found_on_a_different_page_gets_a_citation_warning_not_a_garbled_one():
    extraction = _fact("Total consolidated revenue was $193.4 million "
                        "(prior year: $197.6 million).", page=10)
    pages = {
        3: "Consolidated revenue of $197.6 million was reported last year.",
        10: "Consolidated revenue of $193.4 million decreased from the previous year.",
    }
    warnings = verify_figures(extraction, pages)
    assert len(warnings) == 1
    assert "figure(s) 197.6" in warnings[0]
    assert "page citation may be wrong" in warnings[0]
    assert "may be derived or garbled" not in warnings[0]


def test_figure_missing_everywhere_still_gets_the_garbled_warning_alongside_citation_warning():
    extraction = _fact("Paid $197,600 last year and $999,999 this year.", page=10)
    pages = {
        3: "The prior payment was $197,600.",
        10: "Nothing about payments on this page.",
    }
    warnings = verify_figures(extraction, pages)
    assert len(warnings) == 2
    garbled = next(w for w in warnings if "may be derived or garbled" in w)
    citation = next(w for w in warnings if "page citation may be wrong" in w)
    assert "999999" in garbled
    assert "197600" in citation


def test_citation_warning_does_not_truncate_the_fact_before_the_flagged_figure():
    """Real #456/#460 case: the fact text is long enough that the old hard 80-char truncation
    (no ellipsis) cut it off right after the first figure, so the citation warning about the
    *second* figure showed a preview that never contained the number it was flagging."""
    fact_text = ("Total claims asserted by creditors as per the claims process were $360.3 "
                "million, compared to $186.8 million recognized as subject to compromise.")
    assert len(fact_text) > 80
    assert fact_text.index("186.8") > 80   # falls past the old truncation point
    extraction = _fact(fact_text, page=49)
    pages = {
        19: "Recognized as subject to compromise: $186.8 million.",
        49: "Nothing about claims figures on this page.",
    }
    warnings = verify_figures(extraction, pages)
    citation = next(w for w in warnings if "page citation may be wrong" in w)
    assert fact_text in citation


# ── #623: the finding is annotated onto the fact, not just returned as a warning ──

def test_figure_missing_from_document_is_annotated_on_the_fact():
    extraction = _fact("$430,000 across two transfers.")
    pages = {3: "The company recorded transfers of $250,000 and $180,000."}
    verify_figures(extraction, pages)
    fact = extraction["document"]["key_facts"][0]
    assert fact["figures_unverified"] == ["430000"]
    assert "figures_off_page" not in fact


def test_figure_found_elsewhere_is_annotated_with_the_pages_holding_it():
    extraction = _fact("Revenue was $193.4 million (prior year: $197.6 million).", page=10)
    pages = {
        3: "Consolidated revenue of $197.6 million was reported last year.",
        5: "A restatement note repeats the $197.6 million prior-year figure.",
        10: "Consolidated revenue of $193.4 million decreased from the previous year.",
    }
    verify_figures(extraction, pages)
    fact = extraction["document"]["key_facts"][0]
    assert fact["figures_off_page"] == {"197.6": [3, 5]}
    assert "figures_unverified" not in fact


def test_clean_fact_is_left_unannotated():
    extraction = _fact("Paid $430,000 to the fund.")
    pages = {3: "The transfer totalled 430,000 dollars."}
    verify_figures(extraction, pages)
    fact = extraction["document"]["key_facts"][0]
    assert "figures_unverified" not in fact
    assert "figures_off_page" not in fact


def test_bare_year_is_not_treated_as_a_figure():
    """#623: a four-digit year is a date. Flagging "the fiscal year ended April 30, 2021" as an
    ungrounded *figure* was 9.6% of all flags, and it now renders in the vault."""
    extraction = _fact("Revenue fell in fiscal 2021.")
    pages = {3: "Nothing here names that period."}
    assert verify_figures(extraction, pages) == []
    assert "figures_unverified" not in extraction["document"]["key_facts"][0]


def test_year_exclusion_does_not_mask_a_real_figure_in_the_same_fact():
    extraction = _fact("Paid $500,000 in fiscal 2021.")
    pages = {3: "Nothing about payments on this page."}
    warnings = verify_figures(extraction, pages)
    assert len(warnings) == 1
    assert extraction["document"]["key_facts"][0]["figures_unverified"] == ["500000"]


def test_stale_annotation_is_cleared_when_the_figure_now_resolves():
    """`watchdog bark` re-runs post-flight over the same staged extraction. A figure that
    resolves this time — page text that wasn't on disk before, better OCR — must not keep the
    flag the earlier pass wrote."""
    extraction = _fact("Paid $430,000 to the fund.")
    extraction["document"]["key_facts"][0]["figures_unverified"] = ["430000"]
    extraction["document"]["key_facts"][0]["figures_off_page"] = {"430000": [9]}
    pages = {3: "The transfer totalled 430,000 dollars."}
    assert verify_figures(extraction, pages) == []
    fact = extraction["document"]["key_facts"][0]
    assert "figures_unverified" not in fact
    assert "figures_off_page" not in fact


# ── D213: a figure reported to fewer digits than the page prints it at ───────

def test_rounded_restatement_of_a_thousands_table_figure_is_not_flagged():
    """The dominant false positive this check produced: a $000s statement prints 360,291 and
    the model correctly writes "$360.3 million". Exact x1,000 scaling gives 360,291,000, not
    360.3, so `_scale_variants` alone never matched it."""
    extraction = _fact("Total claims asserted by creditors were $360.3 million.")
    pages = {3: "Total claims                          360,291"}
    assert verify_figures(extraction, pages) == []


def test_truncated_restatement_is_not_flagged():
    """We can't know whether a model rounded or truncated, so both are accepted: 7.9 -> "7"
    is a truncation, and needs the full unit above the written figure."""
    extraction = _fact("The facility was drawn to about $7 million.")
    pages = {3: "The facility was drawn to 7.9 million dollars."}
    assert verify_figures(extraction, pages) == []


def test_figure_below_the_rounding_floor_is_still_flagged():
    """The window is deliberately not symmetric — nothing shortens 6.0 to "7", so the low side
    stays at half a unit while the high side carries a full one for truncation."""
    extraction = _fact("The facility was drawn to about $7 million.")
    pages = {3: "The facility was drawn to 6.0 million dollars."}
    warnings = verify_figures(extraction, pages)
    assert len(warnings) == 1
    assert "may be derived or garbled" in warnings[0]


def test_written_precision_sets_the_window_width():
    """A figure written to four significant digits gets a tight window: 361,000 is nowhere
    near close enough to ground "$360.3 million", even though 360.3 and 361.0 are adjacent
    once you round to whole millions."""
    extraction = _fact("Total claims asserted by creditors were $360.3 million.")
    pages = {3: "Total claims                          361,000"}
    warnings = verify_figures(extraction, pages)
    assert len(warnings) == 1
    assert "360.3" in warnings[0]


def test_rounding_does_not_mask_a_derived_sum():
    """The whole point of the check survives: a computed total is still flagged, because no
    single printed figure rounds to it."""
    extraction = _fact("$430,000 across two transfers.")
    pages = {3: "The company recorded transfers of $250,000 and $180,000."}
    warnings = verify_figures(extraction, pages)
    assert len(warnings) == 1
    assert "430000" in warnings[0]


def test_rounded_match_elsewhere_in_the_document_gets_the_citation_warning():
    """The document-wide pass rounds too, so a rounded figure that is real but on another page
    gets the softer "check the citation" warning rather than "may be derived"."""
    extraction = _fact("Total claims asserted by creditors were $360.3 million.", page=10)
    pages = {3: "Total claims                          360,291", 10: "No figures on this page."}
    warnings = verify_figures(extraction, pages)
    assert len(warnings) == 1
    assert "appear(s) elsewhere in the document" in warnings[0]
    fact = extraction["document"]["key_facts"][0]
    assert "figures_unverified" not in fact
    assert fact["figures_off_page"] == {"360.3": [3]}
