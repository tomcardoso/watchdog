from watchdog.pipeline.quote_verify import verify_quote, verify_quotes


# ── verify_quote ─────────────────────────────────────────────────────────────

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


# ── verify_quotes (annotates key_facts + fanned-out evidence_fragments) ─────

def test_verify_quotes_flags_unverified_key_fact_and_warns():
    extraction = {"document": {"key_facts": [
        {"fact": "Revenue rose.", "page": 3, "quote": "This text is nowhere on the page."},
    ]}, "entities": []}
    warnings = verify_quotes(extraction, {3: "Totally unrelated page content."})
    assert extraction["document"]["key_facts"][0]["quote_verified"] is False
    assert len(warnings) == 1
    assert "key_facts[0]" in warnings[0]


def test_verify_quotes_leaves_exact_match_unannotated():
    extraction = {"document": {"key_facts": [
        {"fact": "Revenue rose.", "page": 3, "quote": "Revenue rose sharply."},
    ]}, "entities": []}
    warnings = verify_quotes(extraction, {3: "Revenue rose sharply."})
    assert "quote_verified" not in extraction["document"]["key_facts"][0]
    assert warnings == []


def test_verify_quotes_annotates_fanned_out_evidence_fragments():
    extraction = {
        "document": {"key_facts": [
            {"fact": "x", "page": 3, "quote": "missing text", "entities": ["a"]},
        ]},
        "entities": [{"id": "a", "name": "A", "type": "Person",
                      "evidence_fragments": [{"claim": "x", "page": 3, "quote": "missing text"}]}],
    }
    verify_quotes(extraction, {3: "unrelated"})
    assert extraction["entities"][0]["evidence_fragments"][0]["quote_verified"] is False


def test_verify_quotes_skips_facts_without_a_quote():
    extraction = {"document": {"key_facts": [{"fact": "x", "page": 3}]}, "entities": []}
    assert verify_quotes(extraction, {3: "anything"}) == []


def test_verify_quotes_skips_verification_when_no_page_text_available():
    """A `watchdog finalize` re-run has no chew-time queue descriptor on disk — with no
    page text at all, quotes are left unannotated rather than flagged unverified (#267)."""
    extraction = {"document": {"key_facts": [
        {"fact": "x", "page": 3, "quote": "anything"},
    ]}, "entities": []}
    warnings = verify_quotes(extraction, {})
    assert "quote_verified" not in extraction["document"]["key_facts"][0]
    assert warnings == []
