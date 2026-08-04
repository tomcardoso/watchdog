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
])
def test_normalize_with_map_matches_normalize(text):
    norm, idx_map = _normalize_with_map(text)
    assert norm == _normalize(text)
    assert len(idx_map) == len(norm)


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
