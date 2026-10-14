"""A source passage for every fact (D270): tokens, sentence splitting, scoring, page fallback,
the post-flight stamp and its format version, and the measured accuracy on the demo story."""

import importlib.util
from pathlib import Path

import pytest

from watchdog.pipeline import passages
from watchdog.pipeline.passages import _DocIndex, find_passage, locate, locate_passages, tokens

PAGE = (
    "# Minutes of the Regular Meeting\n\n"
    "Mayor Robert Delacroix called the meeting to order at 6:02 p.m. All members were present.\n\n"
    "Council received Report CR-2022-011 from Leonard Pike. The report recommends that the City "
    "acquire 14 Dockside Road from 7714882 Holdings Ltd. for $3,900,000.\n\n"
    "Mr. Pike advised that the vendor would entertain other offers. Councillor Oyelaran asked "
    "why the appraisal was not appended.\n\n"
    "| Member | Vote |\n| --- | --- |\n| Councillor Sofia Marchetti | Opposed |\n"
)


def test_figures_normalize_across_spellings():
    assert "#3900000" in tokens("for $3,900,000")
    assert "#3900000" in tokens("about $3.9 million")
    assert "#3900000" in tokens("3900000 dollars")
    assert tokens("$48,600,000")["#48600000"] is True        # a figure is a strong signal


def test_dates_normalize_to_iso_and_keep_their_month():
    for text in ("on February 8, 2022", "on 8 February 2022", "dated 2022-02-08", "the 8th day of February, 2022"):
        t = tokens(text)
        assert "@2022-02-08" in t and "@2022-02" in t, text
    assert "@2022-02" in tokens("in February 2022") and "@2022-02-08" not in tokens("in February 2022")
    # the date's parts are not counted again as figures
    assert "#8" not in tokens("on February 8, 2022")


def test_names_are_strong_and_stopwords_dropped():
    t = tokens("The contract was awarded to Northgate Civil Works.")
    assert t["northgate"] is True and "the" not in t and "was" not in t
    assert t["award"] is False                                 # stemmed, ordinary word
    assert tokens("Contracts awarded")["contract"] is False      # plural meets singular


def test_sentences_respect_abbreviations_and_blocks():
    found = passages.split_passages(PAGE)
    assert "Mr. Pike advised that the vendor would entertain other offers." in found
    assert "Councillor Sofia Marchetti · Opposed" in found        # a table row, cleaned
    assert not any(p.startswith("---") or "| ---" in p for p in found)
    assert "Minutes of the Regular Meeting" in found              # heading marker stripped


def test_finds_the_supporting_sentence_on_the_cited_page():
    index = _DocIndex({1: "Unrelated cover page text about procedures.", 2: PAGE})
    r = find_passage(index, {"fact": "The report recommended buying 14 Dockside Road from 7714882 Holdings Ltd. for $3,900,000.", "page": 2})
    assert r["page"] == 2 and r["passage"].startswith("The report recommends that the City acquire")
    assert r["score"] >= passages.MATCH_THRESHOLD


def test_a_different_figure_is_not_support():
    index = _DocIndex({1: "Client Meridian Shoreline Developments Inc., 410 Wharf Street, Suite 1200."})
    r = find_passage(index, {"fact": "Northgate's address in the contract is 410 Wharf Street, Suite 1100.", "page": 1})
    assert r["passage"] is None and r["score"] < passages.MATCH_THRESHOLD


def test_falls_back_to_a_neighbouring_page_and_records_it():
    index = _DocIndex({1: "Cover sheet.", 2: PAGE, 3: "Appendix of schedules."})
    r = find_passage(index, {"fact": "Pike advised Council that the vendor would entertain other offers.", "page": 3})
    assert r["page"] == 2 and "entertain other offers" in r["passage"]


def test_without_a_page_the_whole_document_is_searched():
    index = _DocIndex({1: "Cover sheet.", 2: "Nothing here.", 3: PAGE})
    r = find_passage(index, {"fact": "Mayor Robert Delacroix called the meeting to order at 6:02 p.m."})
    assert r["page"] == 3


def test_an_inferred_comparison_stays_unlocated():
    index = _DocIndex({2: PAGE})
    r = find_passage(index, {"fact": "The price was about 2.7 times the appraised value.", "page": 2})
    assert r["passage"] is None


def test_locate_stamps_quote_matched_and_unlocated():
    facts = [
        {"fact": "The vendor would entertain other offers.", "page": 2,
         "quote": "Mr. Pike advised that the vendor would entertain other offers."},
        {"fact": "Councillor Oyelaran asked why the appraisal was not appended.", "page": 2},
        {"fact": "The price was about 2.7 times the appraised value.", "page": 2},
        {"fact": "A quote that failed", "page": 2, "quote": "never said", "quote_verified": False},
    ]
    counts = locate(facts, {2: PAGE})
    assert [f["passage_method"] for f in facts] == ["quote", "matched", "unlocated", "unlocated"]
    assert counts == {"quote": 1, "matched": 1, "unlocated": 2}
    assert facts[0]["passage"] == facts[0]["quote"] and facts[0]["passage_page"] == 2
    assert facts[1]["passage_page"] == 2 and "Oyelaran" in facts[1]["passage"]
    assert facts[2]["passage"] is None and facts[2]["passage_page"] is None
    # an already-located fact is left alone unless forced
    facts[1]["passage"] = "kept"
    locate(facts, {2: PAGE})
    assert facts[1]["passage"] == "kept"
    locate(facts, {2: PAGE}, force=True)
    assert facts[1]["passage"] != "kept"


def test_quote_found_on_another_page_sets_the_passage_page():
    facts = [{"fact": "x", "page": 2, "quote": "found elsewhere", "quote_found_page": 3}]
    locate(facts, {2: "a", 3: "found elsewhere"})
    assert facts[0]["passage_page"] == 3


def test_postflight_step_records_its_format_version():
    ex = {"document": {"key_facts": [{"fact": "Councillor Oyelaran asked why the appraisal was not appended.", "page": 2}]}}
    assert locate_passages(ex, {2: PAGE}) == []
    assert ex["document"]["passages_version"] == passages.PASSAGES_VERSION
    assert ex["document"]["key_facts"][0]["passage_method"] == "matched"
    assert passages.readable(ex["document"])
    assert passages.readable({})                                   # no version: version 1
    assert not passages.readable({"passages_version": passages.PASSAGES_VERSION + 1})
    # no page text: nothing stamped, so the fact reads as "not computed", not "unlocated"
    ex2 = {"document": {"key_facts": [{"fact": "x", "page": 1}]}}
    locate_passages(ex2, {})
    assert "passage_method" not in ex2["document"]["key_facts"][0]


def test_postflight_runs_the_passage_step(tmp_path):
    import json
    from watchdog.pipeline import postflight
    vault = tmp_path / "v"
    (vault / ".watchdog" / "queue").mkdir(parents=True)
    sha = "a" * 64
    (vault / ".watchdog" / "queue" / f"{sha}.json").write_text(json.dumps(
        {"pages": [{"page": 1, "markdown": "Short cover."}, {"page": 2, "markdown": PAGE}]}))
    ex = {"document": {"sha256": sha, "filename": "m.pdf", "page_count": 2, "key_facts": [
        {"fact": "Councillor Oyelaran asked why the appraisal was not appended.", "page": 2}]},
        "entities": [], "morgue_entity_id": "x", "morgue_document_type": "minutes"}
    path = tmp_path / "ex.json"
    path.write_text(json.dumps(ex))
    assert postflight.run(vault, path, warn=lambda m: None)["ok"]
    staged = json.loads((vault / ".watchdog" / "extracted" / f"{sha}.json").read_text())
    fact = staged["document"]["key_facts"][0]
    assert fact["passage_method"] == "matched" and fact["passage_page"] == 2
    assert staged["document"]["passages_version"] == 1


def _accuracy_module():
    path = Path(__file__).resolve().parents[1] / "benchmarks" / "passage_accuracy.py"
    spec = importlib.util.spec_from_file_location("passage_accuracy", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.timeout(120)
def test_demo_story_accuracy_holds_at_the_threshold():
    """The calibration D270 reports: on the demo story's 113 facts with a known source sentence,
    at least 95% of the passages the matcher accepts are the right sentence, it locates at least
    80%, and it accepts nothing on an unrelated document's page."""
    mod = _accuracy_module()
    cases = mod.story_cases()
    results = mod.lexical(cases)
    accepted = [ok for score, ok in results if score >= passages.MATCH_THRESHOLD]
    assert len(cases) >= 100
    assert sum(accepted) / len(accepted) >= 0.95
    assert sum(accepted) / len(cases) >= 0.80
    assert sum(1 for s in mod.decoys(cases) if s >= passages.MATCH_THRESHOLD) == 0
