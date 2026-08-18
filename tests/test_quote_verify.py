import pytest

from watchdog.pipeline.quote_verify import (
    _SENTENCE_FORWARD_WINDOW,
    _normalize,
    _normalize_with_map,
    resolve_quote,
    resolve_quotes,
    verify_quote,
)


# ── verify_quote (legacy path: a model-supplied `quote`, no locator) ────────

def test_exact_match_on_cited_page():
    pages = {3: "Total revenue for the year was $1,000,000."}
    result = verify_quote(pages, 3, "Total revenue for the year was $1,000,000.")
    assert result == {"verified": True}


def test_no_page_cited_is_unchecked():
    assert verify_quote({1: "text"}, None, "a quote") == {"verified": None}


def test_empty_quote_is_unchecked():
    assert verify_quote({1: "text"}, 1, "  ") == {"verified": None}


def test_no_page_text_available_is_unchecked():
    assert verify_quote({}, 3, "a quote") == {"verified": None}


def test_normalized_match_survives_whitespace_and_punctuation_drift():
    pages = {3: 'The board said: "Total   revenue\nfor the year" was $1,000,000!'}
    result = verify_quote(pages, 3, "Total revenue for the year was $1,000,000")
    assert result == {"verified": True}


def test_normalized_match_survives_curly_quotes_and_hyphenation_break():
    pages = {3: "The share-\nholder agreement was signed."}
    result = verify_quote(pages, 3, "The shareholder agreement was signed.")
    assert result == {"verified": True}


def test_match_on_adjacent_page_notes_found_page():
    pages = {2: "irrelevant", 3: "Total revenue for the year was $1,000,000."}
    result = verify_quote(pages, 4, "total revenue for the year was $1000000")
    assert result == {"verified": True, "found_page": 3}


def test_no_match_anywhere_is_unverified():
    pages = {3: "Something entirely different."}
    result = verify_quote(pages, 3, "Total revenue for the year was $1,000,000.")
    assert result == {"verified": False}


def test_page_out_of_range_of_available_text_is_unverified_not_unchecked():
    # page_texts has data (verification is possible in principle) but not near this page.
    pages = {1: "Something entirely different."}
    result = verify_quote(pages, 50, "Total revenue for the year was $1,000,000.")
    assert result == {"verified": False}


# ── verify_quote: elided quotes (#630) ─────────────────────────────────────

_ELISION_PAGE = {
    3: ("THIS COURT ORDERS that the payment due on March 30, 2021 in the amount of "
        "$842,018.34 in respect of LU's pro rata portion of assessment fees payable to "
        "the Pension Benefits Guarantee Fund relating to the Pension Plan is stayed and "
        "suspended until further order of this Court."),
}


@pytest.mark.parametrize("ellipsis", ["…", "..."])
def test_elided_quote_verifies_when_parts_appear_in_order(ellipsis):
    quote = f"the payment due on March 30, 2021 in the amount of $842,018.34 {ellipsis} is stayed and suspended"
    assert verify_quote(_ELISION_PAGE, 3, quote) == {"verified": True}


def test_elided_quote_with_no_spaces_around_the_ellipsis_verifies():
    # "LU... shall be permitted" — models elide without padding too.
    pages = {2: "LU and the Administrator shall be permitted to transfer commuted values."}
    assert verify_quote(pages, 2, "LU and the Administrator... shall be permitted to transfer") == {
        "verified": True}


def test_elided_quote_with_parts_out_of_order_is_unverified():
    quote = "is stayed and suspended … the payment due on March 30, 2021 in the amount of"
    assert verify_quote(_ELISION_PAGE, 3, quote) == {"verified": False}


def test_elided_quote_stitching_distant_fragments_is_unverified():
    filler = "Some entirely unrelated intervening provision. " * 30   # > _MAX_ELISION_GAP
    pages = {3: f"the payment due on March 30, 2021 {filler} is stayed and suspended"}
    quote = "the payment due on March 30, 2021 … is stayed and suspended"
    assert verify_quote(pages, 3, quote) == {"verified": False}


def test_elided_quote_with_a_too_short_part_is_unverified():
    # A fragment this small would match almost any page; better a false negative.
    pages = {3: "The board approved the measure and the total was $1,000,000 in the end."}
    assert verify_quote(pages, 3, "The board … was $1,000,000 in the end") == {"verified": False}


def test_elision_resuming_on_a_currency_figure_is_detected():
    # The cut very often lands just before a number, so the character after the
    # ellipsis is "$" rather than a word character.
    pages = {3: "The total assessed against the university was $842,018.34 for the period."}
    quote = "The total assessed against the university … $842,018.34 for the period"
    assert verify_quote(pages, 3, quote) == {"verified": True}


def test_trailing_ellipsis_is_truncation_not_elision():
    # No word character after the ellipsis, so this is not treated as elided and
    # keeps its pre-#630 behaviour: it still matches as a plain prefix.
    assert verify_quote(_ELISION_PAGE, 3, "the payment due on March 30, 2021 …") == {
        "verified": True}


def test_elided_quote_resolves_on_an_adjacent_page():
    pages = {2: "irrelevant", 3: _ELISION_PAGE[3]}
    quote = "the payment due on March 30, 2021 in the amount of $842,018.34 … is stayed and suspended"
    assert verify_quote(pages, 4, quote) == {"verified": True, "found_page": 3}


def test_unelided_quote_that_never_appears_is_still_unverified():
    # Guards the change: adding elision handling must not soften the plain path.
    pages = {3: "Something entirely different."}
    assert verify_quote(pages, 3, "Total revenue … for the year was $1,000,000") == {
        "verified": False}


# ── _normalize_with_map (must never drift from _normalize, #529) ────────────

@pytest.mark.parametrize("text", [
    "",
    "Simple ASCII sentence.",
    "Café résumé naïve façade",             # accents (NFKD + combining-mark drop)
    "The share-\nholder agreement.",        # hyphen line break
    "soft­hyphen inside a word",       # soft hyphen
    "!!!   ...,,,???   ---",                # runs of punctuation and whitespace
    "Multiple   spaces\t\tand\ntabs\nhere",
    "MiXeD CaSe TeXt",
    "—em—dash—runs—",   # em dashes
    "   leading and trailing whitespace   ",
    "Payroll &amp; Benefits",               # HTML entity the chew emits for "&"
    "&lt;tag&gt; &#39;quoted&#39; &nbsp;x",  # several entities, incl. numeric
    "a &amp; b &notanentity; c",             # a bare ampersand run must survive
])
def test_normalize_with_map_matches_normalize(text):
    norm, idx_map = _normalize_with_map(text)
    assert norm == _normalize(text)
    assert len(idx_map) == len(norm)


def test_normalize_with_map_indices_point_into_the_original_string():
    """An entity is five characters wide and normalizes to one, so the map has
    to survive the length change — otherwise `resolve_quote` renders a span
    from the wrong offsets."""
    text = "Payroll &amp; Benefits"
    norm, idx_map = _normalize_with_map(text)
    assert max(idx_map) < len(text)
    # every index still lands on the character it came from
    assert text[idx_map[norm.index("benefits")]] == "B"


# ── HTML entities (the chew emits `&amp;` where the page prints `&`) ────────

def test_entity_in_the_page_matches_the_printed_character_in_the_quote():
    pages = {1: "the Payroll &amp; Benefits line"}
    assert verify_quote(pages, 1, "Payroll & Benefits") == {"verified": True}


def test_numeric_entity_is_unescaped_too():
    assert _normalize("it&#39;s") == _normalize("it's")


def test_a_bare_ampersand_is_left_alone():
    """`&notanentity;` is not an entity; mangling it would lose real text."""
    assert "notanentity" in _normalize("a &notanentity; b")


# ── collapse_spaces=False, for checking against chewed markdown ─────────────

def test_collapse_spaces_false_matches_a_word_the_conversion_split():
    """Docling converts the pension order's header to "WEDNESDAY, THE 17 th".
    Collapsing runs to one space cannot reconcile that with "17th"; discarding
    whitespace can, which is why the key checker asks for it."""
    page, quote = "WEDNESDAY, THE 17 th", "WEDNESDAY, THE 17th"
    assert _normalize(quote) not in _normalize(page)
    assert _normalize(quote, collapse_spaces=False) in _normalize(page, collapse_spaces=False)


def test_collapse_spaces_defaults_to_true_so_the_pipeline_is_unchanged():
    assert _normalize("a  b") == "a b"
    assert _normalize("a  b", collapse_spaces=False) == "ab"


# ── resolve_quote (#529: resolves a `quote_locator` against real page text) ─

def test_resolve_quote_empty_locator_returns_nothing():
    assert resolve_quote({3: "text"}, 3, "") == {}
    assert resolve_quote({3: "text"}, 3, "   ") == {}


def test_resolve_quote_no_page_cited_returns_locator_unflagged():
    assert resolve_quote({1: "text"}, None, "a locator") == {"quote": "a locator"}


def test_resolve_quote_no_page_text_available_returns_locator_unflagged():
    assert resolve_quote({}, 3, "a locator") == {"quote": "a locator"}


def test_resolve_quote_exact_opening_words_resolve_to_whole_sentence():
    pages = {3: "Total revenue for the year was $1,000,000. Next sentence starts here."}
    result = resolve_quote(pages, 3, "Total revenue for the year")
    assert result["quote"] == "Total revenue for the year was $1,000,000."
    assert len(result["quote"]) > len("Total revenue for the year")
    assert "verified" not in result
    assert "found_page" not in result


def test_resolve_quote_normalizes_locator_but_returns_source_casing_and_punctuation():
    pages = {3: "Total Revenue, for the YEAR — was $1,000,000.00! Next sentence starts here."}
    result = resolve_quote(pages, 3, "total revenue for the year")
    assert result["quote"] == "Total Revenue, for the YEAR — was $1,000,000.00!"


def test_resolve_quote_multiline_match_collapses_to_single_line():
    """The locator's own words straddle a line break in the source markdown; the resolved
    quote must read as one line, not two."""
    pages = {3: "The board resolved that\nthe transfer ratio would be set at 65.8% going forward. "
                "Next paragraph starts."}
    result = resolve_quote(pages, 3, "The board resolved that the transfer")
    assert "\n" not in result["quote"]
    assert result["quote"] == "The board resolved that the transfer ratio would be set at 65.8% going forward."


def test_resolve_quote_dehyphenates_line_break_inside_the_match():
    pages = {3: "The share-\nholder agreement was signed on Monday. Next sentence follows."}
    result = resolve_quote(pages, 3, "The shareholder agreement")
    assert result["quote"] == "The shareholder agreement was signed on Monday."


def test_resolve_quote_newline_boundary_does_not_swallow_the_following_line():
    pages = {3: "Total assets stood at $5 million\nUnrelated later line that must not appear at all."}
    result = resolve_quote(pages, 3, "Total assets stood at")
    assert result["quote"] == "Total assets stood at $5 million"
    assert "Unrelated later line" not in result["quote"]
    assert "\n" not in result["quote"]


def test_resolve_quote_reads_through_a_hard_wrapped_line():
    """`process_direct_text` passes a .txt/.md file's raw text through as page markdown, and
    those are routinely hard-wrapped mid-sentence — a newline whose next line resumes in
    lowercase continues the sentence rather than ending it."""
    pages = {3: 'On February 1, 2021, the Court granted an initial order that, among\n'
                'other things, appointed a monitor of the Applicant in these\n'
                'proceedings. A later matter followed.'}
    result = resolve_quote(pages, 3, "On February 1, 2021, the Court granted")
    assert result["quote"] == ("On February 1, 2021, the Court granted an initial order that, "
                               "among other things, appointed a monitor of the Applicant in "
                               "these proceedings.")
    assert "\n" not in result["quote"]
    assert "A later matter" not in result["quote"]


def test_resolve_quote_blank_line_stops_expansion_even_before_a_lowercase_line():
    """A blank line is a block break regardless of how the next paragraph starts."""
    pages = {3: "The order was granted on Monday\n\nthe applicant did not appear."}
    result = resolve_quote(pages, 3, "The order was granted")
    assert result["quote"] == "The order was granted on Monday"


def test_resolve_quote_reads_through_a_wrap_resuming_on_a_capitalized_word():
    """Legal prose wraps onto dates and proper nouns constantly. A long line that ends
    mid-sentence is a column wrap regardless of how the next line is capitalized."""
    pages = {3: "The Applicant seeks an extension of the Stay Period up to and including\n"
                "April 30, 2021, on the terms set out in the report. A separate motion follows."}
    result = resolve_quote(pages, 3, "The Applicant seeks an extension")
    assert result["quote"] == ("The Applicant seeks an extension of the Stay Period up to and "
                               "including April 30, 2021, on the terms set out in the report.")


def test_resolve_quote_stops_at_a_block_marker_on_the_next_line():
    """A table row, heading or list bullet is a new block, never a wrapped continuation — even
    after a long line that would otherwise read as column-wrapped."""
    pages = {3: "Total assets stood at $5 million\n| Administration Charge | $1,000,000 |",
             4: "Total assets stood at $5 million\n## Schedule B",
             5: "The order granted the following relief on a long wrapped opening line\n"
                "- an administration charge"}
    assert resolve_quote(pages, 3, "Total assets stood at")["quote"] == "Total assets stood at $5 million"
    assert resolve_quote(pages, 4, "Total assets stood at")["quote"] == "Total assets stood at $5 million"
    assert resolve_quote(pages, 5, "The order granted")["quote"] == (
        "The order granted the following relief on a long wrapped opening line")


def test_resolve_quote_short_line_resuming_in_caps_stops():
    """The conservative case that survives: consecutive short lines are an address or signature
    block, where the next line really is a separate item rather than a wrap."""
    pages = {3: "the monitor was appointed by\nErnst & Young Inc. under the order."}
    result = resolve_quote(pages, 3, "the monitor was appointed")
    assert result["quote"] == "the monitor was appointed by"


def test_resolve_quote_long_line_ending_in_a_terminator_stops():
    """A wrapped line ends mid-sentence by definition; one ending on a full stop has finished
    its sentence, even where the next line opens on a digit rather than a capital."""
    pages = {3: "The applicant paid the full amount owing under the settlement in full.\n"
                "2021 was a difficult year for the university."}
    result = resolve_quote(pages, 3, "The applicant paid")
    assert result["quote"] == "The applicant paid the full amount owing under the settlement in full."


def test_resolve_quote_backward_window_fall_through_starts_on_a_whole_word():
    """With no punctuation or line break anywhere in range, the backward scan runs out of
    window — the span must still open on a whole word rather than mid-token."""
    tokens = [f"tok{i:04d}" for i in range(400)]
    result = resolve_quote({3: " ".join(tokens)}, 3, "tok0200 tok0201")
    assert result["quote"].removesuffix(" …").split(" ")[0] in tokens


def test_resolve_quote_forward_window_fall_through_ends_on_a_whole_word():
    """Mirror of the above for the forward scan. The locator sits at the very start of the page
    so the whole span stays under the 500-char cap — otherwise the cap's own word-boundary cut
    would mask whether the forward window edge was snapped."""
    tokens = [f"t{i:04d}" for i in range(400)]
    text = "LOCATOR HERE " + " ".join(tokens)
    # The token width is chosen so the forward window edge lands *inside* a token; with an
    # edge that happens to fall on a space there would be nothing for the snap to do.
    assert not text[12 + _SENTENCE_FORWARD_WINDOW].isspace()
    result = resolve_quote({3: text}, 3, "LOCATOR HERE")
    quote = result["quote"]
    assert not quote.endswith(" …")   # the cap must not be what ended this quote
    assert quote.split(" ")[-1] in tokens


def test_resolve_quote_does_not_break_on_abbreviation_periods():
    pages = {3: "Acme Inc. as monitor filed the report on Feb. 1, 2021 confirming the transfer. "
                "A new matter followed."}
    result = resolve_quote(pages, 3, "Acme Inc. as monitor filed")
    assert result["quote"] == ("Acme Inc. as monitor filed the report on Feb. 1, 2021 confirming "
                                "the transfer.")


def test_resolve_quote_ambiguous_match_uses_first_occurrence_and_flags():
    pages = {3: "Total revenue was strong this quarter. Total revenue was strong again next year."}
    result = resolve_quote(pages, 3, "Total revenue was strong")
    assert result["ambiguous"] is True
    assert result["occurrences"] == 2
    assert result["quote"] == "Total revenue was strong this quarter."


def test_resolve_quote_no_match_falls_back_to_locator_and_flags_unverified():
    pages = {3: "Something entirely different."}
    result = resolve_quote(pages, 3, "Total revenue was strong")
    assert result == {"quote": "Total revenue was strong", "verified": False}


def test_resolve_quote_adjacent_page_resolves_with_found_page():
    pages = {3: "Irrelevant content here.",
             4: "Total revenue for the year was strong. Next sentence."}
    result = resolve_quote(pages, 3, "Total revenue for the year")
    assert result["found_page"] == 4
    assert result["quote"] == "Total revenue for the year was strong."


def test_resolve_quote_whitespace_blind_fallback_recovers_ocr_dropped_spaces():
    """OCR/table extraction can fuse or drop a word boundary ("DEFERREDCONTRIBUTIONS ANDNET
    ASSETS" for "DEFERRED CONTRIBUTIONS AND NET ASSETS", #560) — the model transcribes the
    words correctly spaced, so the space-preserving match fails; a whitespace-blind fallback
    recovers it without needing to guess where the missing spaces belong."""
    pages = {3: "LIABILITIES, DEFERREDCONTRIBUTIONS ANDNET ASSETS"}
    result = resolve_quote(pages, 3, "LIABILITIES, DEFERRED CONTRIBUTIONS AND NET ASSETS")
    assert result["quote"] == "LIABILITIES, DEFERREDCONTRIBUTIONS ANDNET ASSETS"
    assert "verified" not in result


def test_resolve_quote_whitespace_blind_fallback_requires_a_minimum_length():
    """A short locator matched only space-insensitively is too easy to collide with an
    unrelated span — below `_MIN_SPACELESS_LOCATOR` the fallback must not fire at all."""
    pages = {3: "The cat sat on the mat."}
    result = resolve_quote(pages, 3, "cats at")   # "cat s at" spaceless == "cats at" spaceless
    assert result == {"quote": "cats at", "verified": False}


def test_resolve_quote_joins_pages_when_sentence_spans_a_page_break():
    """A sentence hard-wrapped across a page break can never be found on either page alone —
    each page's text is searched independently — so `resolve_quote` falls back to joining two
    adjacent pages' text together (#560)."""
    pages = {2: "Any applicant who does not respond within 30 days shall be deemed to have "
                "withdrawn their application for",
             3: "a transfer of their commuted value and shall retain their entitlement."}
    result = resolve_quote(pages, 2, "shall be deemed to have withdrawn their application for a transfer")
    assert result["spans_pages"] == (2, 3)
    assert result["quote"] == (
        "Any applicant who does not respond within 30 days shall be deemed to have withdrawn "
        "their application for a transfer of their commuted value and shall retain their "
        "entitlement."
    )
    assert "found_page" not in result   # neither single page fully contains the quote


def test_resolve_quote_join_strips_page_number_furniture_at_the_boundary():
    """A lone page-number stamp at a page's edge (chew furniture, not sentence content) sits
    literally between the two true halves of a page-spanning sentence if left in — breaking the
    very match the join exists to recover (#560, found via the real corpus)."""
    pages = {2: "shall be deemed to have withdrawn their application for",
             3: "- 3 -\n\na transfer of their commuted value."}
    result = resolve_quote(pages, 2, "shall be deemed to have withdrawn their application for a transfer")
    assert result["spans_pages"] == (2, 3)
    assert "- 3 -" not in result["quote"]


def test_resolve_quote_join_tries_mirror_direction():
    """The sentence can also open on the page BEFORE the one cited, continuing onto the cited
    page — the mirror of the forward-join case, tried when the forward join fails."""
    pages = {2: "As of January 29, 2021, the outstanding principal balance owing under",
             3: "this facility is $10,575,875, exclusive of accrued interest and costs."}
    result = resolve_quote(pages, 3, "balance owing under this facility is $10,575,875")
    assert result["spans_pages"] == (2, 3)
    assert "outstanding principal balance" in result["quote"]


def test_resolve_quote_join_not_used_when_a_single_page_already_matches():
    pages = {3: "Revenue rose sharply this year.", 4: "Unrelated content."}
    result = resolve_quote(pages, 3, "Revenue rose sharply")
    assert "spans_pages" not in result


def test_resolve_quote_caps_long_quote_at_500_chars_on_word_boundary():
    tokens = [f"tok{i:04d}" for i in range(200)]
    text = " ".join(tokens)   # one giant run-on with no punctuation anywhere
    result = resolve_quote({3: text}, 3, "tok0100 tok0101")
    quote = result["quote"]
    assert quote.endswith(" …")
    assert len(quote) <= 502
    body = quote[:-2]
    assert not body.endswith(" ")
    assert body.split(" ")[-1] in tokens


# ── resolve_quotes (the post-flight driver: quote_locator + legacy quote) ───

def test_resolve_quotes_resolves_locator_and_leaves_it_in_place():
    extraction = {"document": {"key_facts": [
        {"fact": "Revenue rose.", "page": 3, "quote_locator": "Revenue rose"},
    ]}, "entities": []}
    warnings = resolve_quotes(extraction, {3: "Revenue rose sharply this year."})
    fact = extraction["document"]["key_facts"][0]
    assert fact["quote"] == "Revenue rose sharply this year."
    assert fact["quote_locator"] == "Revenue rose"
    assert warnings == []


def test_resolve_quotes_flags_unresolved_locator_and_warns():
    extraction = {"document": {"key_facts": [
        {"fact": "Revenue rose.", "page": 3, "quote_locator": "This text is nowhere on the page"},
    ]}, "entities": []}
    warnings = resolve_quotes(extraction, {3: "Totally unrelated page content."})
    fact = extraction["document"]["key_facts"][0]
    assert fact["quote"] == "This text is nowhere on the page"
    assert fact["quote_verified"] is False
    assert len(warnings) == 1
    assert "key_facts[0]" in warnings[0]


def test_resolve_quotes_warns_on_ambiguous_locator():
    extraction = {"document": {"key_facts": [
        {"fact": "x", "page": 3, "quote_locator": "Total revenue was strong"},
    ]}, "entities": []}
    pages = {3: "Total revenue was strong this quarter. Total revenue was strong again next year."}
    warnings = resolve_quotes(extraction, pages)
    assert len(warnings) == 1
    assert "matched 2 times on page 3" in warnings[0]
    assert extraction["document"]["key_facts"][0]["quote"] == "Total revenue was strong this quarter."


def test_resolve_quotes_corrects_page_when_locator_is_unique_document_wide():
    """A locator that resolves to exactly one match — on a page other than the one the model
    cited — used to be completely silent (#560): `fact["page"]` (what every downstream reader
    displays) kept the model's wrong citation even though the quote text itself was quietly
    recovered. When the locator is unique across the WHOLE document, not just within
    `resolve_quote`'s ±1 window, it's safe to correct the citation itself, not just warn."""
    extraction = {"document": {"key_facts": [
        {"fact": "x", "page": 3, "quote_locator": "Total revenue for the year"},
    ]}, "entities": []}
    pages = {3: "Irrelevant content here.",
             4: "Total revenue for the year was strong. Next sentence."}
    warnings = resolve_quotes(extraction, pages)
    assert len(warnings) == 1
    assert "page corrected from 3 to 4" in warnings[0]
    fact = extraction["document"]["key_facts"][0]
    assert fact["quote_found_page"] == 4
    assert fact["page"] == 4   # corrected — the match is unique across the whole document


def test_resolve_quotes_does_not_correct_page_when_locator_recurs_elsewhere():
    """The same single, non-ambiguous match within the ±1 window — but the locator ALSO appears
    on a page well outside that window. `resolve_quote` itself never sees that other occurrence
    (by design, to avoid flagging every harmless recurring label), so `resolve_quotes` must check
    document-wide before trusting the match enough to overwrite the citation a reporter reads."""
    extraction = {"document": {"key_facts": [
        {"fact": "x", "page": 3, "quote_locator": "Total revenue for the year"},
    ]}, "entities": []}
    pages = {3: "Irrelevant content here.",
             4: "Total revenue for the year was strong. Next sentence.",
             40: "Elsewhere, Total revenue for the year came in lower than expected."}
    warnings = resolve_quotes(extraction, pages)
    assert len(warnings) == 1
    assert "not auto-corrected" in warnings[0]
    fact = extraction["document"]["key_facts"][0]
    assert fact["quote_found_page"] == 4
    assert fact["page"] == 3   # left as the model wrote it — not safe to trust as unique


def test_resolve_quotes_warns_on_page_spanning_match():
    extraction = {"document": {"key_facts": [
        {"fact": "x", "page": 2, "quote_locator": "shall be deemed to have withdrawn their application"},
    ]}, "entities": []}
    pages = {2: "Any applicant who does not respond shall be deemed to have withdrawn",
             3: "their application for a transfer of their commuted value."}
    warnings = resolve_quotes(extraction, pages)
    assert len(warnings) == 1
    assert "crosses a page break" in warnings[0]
    fact = extraction["document"]["key_facts"][0]
    assert fact["quote_spans_pages"] == [2, 3]
    assert fact["page"] == 2   # left as-is — no single page fully contains the quote


def test_resolve_quotes_ambiguous_and_page_mismatch_warns_once_not_twice():
    """When a match is both ambiguous AND on a different page than cited, the ambiguous warning
    already names the found page — a second, separate page-mismatch warning would be redundant."""
    extraction = {"document": {"key_facts": [
        {"fact": "x", "page": 3, "quote_locator": "Total revenue was strong"},
    ]}, "entities": []}
    pages = {4: "Total revenue was strong this quarter. Total revenue was strong again next year."}
    warnings = resolve_quotes(extraction, pages)
    assert len(warnings) == 1
    assert "matched 2 times on page 4" in warnings[0]


def test_resolve_quotes_skips_facts_without_a_locator_or_quote():
    extraction = {"document": {"key_facts": [{"fact": "x", "page": 3}]}, "entities": []}
    assert resolve_quotes(extraction, {3: "anything"}) == []
    assert "quote" not in extraction["document"]["key_facts"][0]


def test_resolve_quotes_legacy_quote_still_flags_unverified_and_warns():
    """A pre-#529 staged extraction, re-run through post-flight with no `quote_locator`, is
    still verified the old way."""
    extraction = {"document": {"key_facts": [
        {"fact": "Revenue rose.", "page": 3, "quote": "This text is nowhere on the page."},
    ]}, "entities": []}
    warnings = resolve_quotes(extraction, {3: "Totally unrelated page content."})
    assert extraction["document"]["key_facts"][0]["quote_verified"] is False
    assert len(warnings) == 1
    assert "key_facts[0]" in warnings[0]


def test_resolve_quotes_legacy_quote_leaves_exact_match_unannotated():
    extraction = {"document": {"key_facts": [
        {"fact": "Revenue rose.", "page": 3, "quote": "Revenue rose sharply."},
    ]}, "entities": []}
    warnings = resolve_quotes(extraction, {3: "Revenue rose sharply."})
    assert "quote_verified" not in extraction["document"]["key_facts"][0]
    assert warnings == []


def test_resolve_quotes_legacy_quote_skips_verification_when_no_page_text_available():
    """A `watchdog finalize` re-run has no chew-time queue descriptor on disk — with no
    page text at all, quotes are left unannotated rather than flagged unverified (#267)."""
    extraction = {"document": {"key_facts": [
        {"fact": "x", "page": 3, "quote": "anything"},
    ]}, "entities": []}
    warnings = resolve_quotes(extraction, {})
    assert "quote_verified" not in extraction["document"]["key_facts"][0]
    assert warnings == []


def test_resolve_quotes_does_not_truncate_the_quote_shown_in_the_warning():
    """The warning used to hard-truncate the quote to 80 chars with no ellipsis (#456/#460) —
    on a quote over that length, the truncated preview can cut off before the part that
    actually failed to verify, making the warning look inexplicable rather than accurate."""
    long_quote = ("Total claims asserted by creditors as per the claims process were $360.3 "
                  "million, compared to $186.8 million recognized as subject to compromise.")
    assert len(long_quote) > 80
    extraction = {"document": {"key_facts": [
        {"fact": "x", "page": 3, "quote": long_quote},
    ]}, "entities": []}
    warnings = resolve_quotes(extraction, {3: "Totally unrelated page content."})
    assert len(warnings) == 1
    assert long_quote in warnings[0]
