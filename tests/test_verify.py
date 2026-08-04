"""The verification pass's deterministic merge (#535, `pipeline/verify.py`).

Everything a verifier returns is filtered here, in code — these tests are the contract for what
a candidate fact is allowed to assert and what counts as a restatement of a fact the extractor
already had.
"""

from watchdog.pipeline import verify


def _extraction(facts, entities=("acme-corp",)):
    return {
        "document": {"key_facts": [dict(f) for f in facts]},
        "entities": [{"id": e, "name": e.replace("-", " ").title(), "type": "organization"}
                     for e in entities],
    }


def _facts(extraction):
    return extraction["document"]["key_facts"]


# ── sanitization ──────────────────────────────────────────────────────────────

def test_added_facts_are_tagged_and_appended_after_the_extractors_own():
    ext = _extraction([{"fact": "Acme filed its annual report in 2024."}])
    stats = verify.merge_candidates(ext, [{"fact": "The report was audited by Ernst & Young.",
                                           "page": 4}])

    assert stats == {"added": 1, "suppressed": 0}
    assert len(_facts(ext)) == 2
    assert _facts(ext)[0].get("added_by") is None      # the extractor's fact is untouched
    assert _facts(ext)[1] == {"fact": "The report was audited by Ernst & Young.",
                              "page": 4, "added_by": "verify"}


def test_entity_tags_are_filtered_to_ids_the_extraction_actually_produced():
    """A verifier that coins its own id must lose the tag, not file the fact under an entity
    that exists nowhere — the id is a path segment and a wikilink target downstream."""
    ext = _extraction([{"fact": "Existing."}], entities=("acme-corp",))
    verify.merge_candidates(ext, [
        {"fact": "Acme's board met in March.", "entities": ["acme-corp", "acme-board"]},
        {"fact": "The trustee was replaced.", "entities": ["some-trustee"]},
    ])

    assert _facts(ext)[1]["entities"] == ["acme-corp"]
    assert "entities" not in _facts(ext)[2]


def test_unusable_candidates_are_dropped_and_counted_as_suppressed():
    ext = _extraction([{"fact": "Existing."}])
    stats = verify.merge_candidates(ext, [
        "a bare string, not a fact object",
        {"fact": ""},
        {"fact": "   "},
        {"page": 3},
        {"fact": "A real one."},
    ])

    assert stats == {"added": 1, "suppressed": 4}
    assert len(_facts(ext)) == 2


def test_page_is_kept_only_when_it_is_a_real_page_number():
    ext = _extraction([{"fact": "Existing."}])
    verify.merge_candidates(ext, [
        {"fact": "Page as a string.", "page": "4"},
        {"fact": "Page zero.", "page": 0},
        {"fact": "Page as a bool.", "page": True},
        {"fact": "Page nulled.", "page": None},
        {"fact": "Page for real.", "page": 12},
    ])

    added = _facts(ext)[1:]
    assert [f.get("page") for f in added] == [None, None, None, None, 12]


def test_only_a_declared_inferred_basis_survives():
    """`stated` is the omit-default, and an unrecognized basis would fail post-flight's
    validation for the whole document — so anything but `inferred` is simply dropped."""
    ext = _extraction([{"fact": "Existing."}])
    verify.merge_candidates(ext, [
        {"fact": "Reasoned to it.", "basis": "inferred"},
        {"fact": "Read off the page.", "basis": "stated"},
        {"fact": "Confident, apparently.", "basis": "high-confidence"},
    ])

    assert [f.get("basis") for f in _facts(ext)[1:]] == ["inferred", None, None]


def test_date_and_quote_are_carried_through_when_non_empty():
    ext = _extraction([{"fact": "Existing."}])
    verify.merge_candidates(ext, [
        {"fact": "The order was issued.", "date": "2021-02-01", "quote": "IT IS ORDERED"},
        {"fact": "Undated.", "date": "  ", "quote": ""},
    ])

    assert _facts(ext)[1]["date"] == "2021-02-01"
    assert _facts(ext)[1]["quote"] == "IT IS ORDERED"
    assert "date" not in _facts(ext)[2] and "quote" not in _facts(ext)[2]


# ── restatement suppression ───────────────────────────────────────────────────

def test_identical_and_reworded_restatements_are_suppressed():
    ext = _extraction([{"fact": "The University was insolvent as of February 2021."}])
    stats = verify.merge_candidates(ext, [
        {"fact": "The University was insolvent as of February 2021."},
        {"fact": "As of February 2021 the University was insolvent."},
    ])

    assert stats == {"added": 0, "suppressed": 2}
    assert len(_facts(ext)) == 1


def test_a_sub_clause_of_a_captured_fact_is_suppressed():
    """The generalization case: everything the candidate says is already in a longer fact."""
    ext = _extraction([{"fact": "The Monitor was appointed under the CCAA on 1 February 2021."}])
    stats = verify.merge_candidates(ext, [{"fact": "The Monitor was appointed."}])

    assert stats["added"] == 0


def test_a_genuinely_different_fact_on_the_same_subject_survives():
    ext = _extraction([{"fact": "The Monitor was appointed under the CCAA on 1 February 2021."}])
    stats = verify.merge_candidates(ext, [
        {"fact": "The Monitor may act in its sole and unfettered discretion."}])

    assert stats["added"] == 1


def test_a_candidate_carrying_a_new_figure_survives_heavy_word_overlap():
    """The carve-out that keeps the pass useful: a buried figure attached to a fact already in
    the list is exactly what this pass exists to recover, and word overlap alone would bury it."""
    ext = _extraction([{"fact": "The University reported an operating deficit for fiscal 2019-20."}])
    stats = verify.merge_candidates(ext, [
        {"fact": "The University reported an operating deficit of $5.4 million for fiscal 2019-20."}])

    assert stats["added"] == 1
    assert "$5.4" in _facts(ext)[1]["fact"]


def test_a_figure_already_in_the_matched_fact_does_not_defeat_suppression():
    ext = _extraction([{"fact": "The endowment stood at $52.8 million, up $1 million over 2018-19."}])
    stats = verify.merge_candidates(ext, [
        {"fact": "The endowment stood at $52.8 million, up $1 million over 2018-19."}])

    assert stats == {"added": 0, "suppressed": 1}


def test_the_pass_does_not_add_its_own_duplicate_twice():
    ext = _extraction([{"fact": "Existing."}])
    stats = verify.merge_candidates(ext, [
        {"fact": "The Board approved the budget on 30 April 2020.", "page": 2},
        {"fact": "On 30 April 2020 the Board approved the budget.", "page": 9},
    ])

    assert stats == {"added": 1, "suppressed": 1}


def test_shared_function_words_alone_never_make_two_facts_look_alike():
    """Two sentences about different things share most of their function words. Counting those
    pushes any pair of English sentences toward the suppression threshold, so a real difference
    — a report versus an order — has to be decided on content words only."""
    ext = _extraction([{"fact": "The report was filed with the court on that day."}])
    stats = verify.merge_candidates(ext, [
        {"fact": "The order was filed with the court on that day."}])

    assert stats["added"] == 1


# ── shape ─────────────────────────────────────────────────────────────────────

def test_empty_candidate_list_is_a_no_op():
    ext = _extraction([{"fact": "Existing."}])
    assert verify.merge_candidates(ext, []) == {"added": 0, "suppressed": 0}
    assert verify.merge_candidates(ext, None) == {"added": 0, "suppressed": 0}
    assert len(_facts(ext)) == 1


def test_an_extraction_with_no_facts_yet_still_takes_candidates():
    """A section that legitimately found nothing still gets verified — `key_facts` may be absent
    entirely on a SECTION-shaped dict."""
    ext = {"document": {}, "entities": []}
    stats = verify.merge_candidates(ext, [{"fact": "Something on the page."}])

    assert stats["added"] == 1
    assert ext["document"]["key_facts"][0]["fact"] == "Something on the page."


def test_a_malformed_entities_field_does_not_crash_the_merge():
    ext = {"document": {"key_facts": []}, "entities": ["not-an-object", {"name": "no id"}]}
    stats = verify.merge_candidates(ext, [{"fact": "Fact.", "entities": ["not-an-object"]}])

    assert stats["added"] == 1
    assert "entities" not in ext["document"]["key_facts"][0]
