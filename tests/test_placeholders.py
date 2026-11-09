"""Placeholder entities and the document with no subject (D393). Invented fixtures only."""
import pytest

from watchdog.pipeline import placeholders


@pytest.mark.parametrize("name", [
    "Unknown",
    "Unidentified subject",
    "Unidentified person (name not shown)",
    "Undetermined party",
    "Unidentified agency",
    "unidentified bidder",              # a lower-case word is not a proper name
    "Unnamed company",
    "Name not shown",
    "Not legible",
    "N/A",
    "[Redacted]",
    "Personne non identifiée",
    "Société inconnue",
    "Inconnu",
    "Sujet indéterminé",
])
def test_placeholder_names_are_recognized(name):
    assert placeholders.is_placeholder(name)


@pytest.mark.parametrize("name", [
    "Unknown Mortal Orchestra",         # a capitalized, non-generic word: a real name
    "Unknown Driver",                   # kept on purpose: doubtful entities err toward staying
    "Jane Unknown-Smith",
    "Acme (name not shown)",            # a real name beside placeholder words stays
    "Unknown (Northgate Supply)",
    "Acme Corp",
    "The Company",                      # generic words alone are a defined term, not a placeholder
    "Anonymous",                        # not a "could not tell" word
    "Unknown Pleasures Records Ltd",
    "",
])
def test_real_names_are_kept(name):
    assert not placeholders.is_placeholder(name)


def test_an_id_is_judged_without_case():
    assert placeholders.is_placeholder("unidentified-subject", case_matters=False)
    assert not placeholders.is_placeholder("unknown-mortal-orchestra", case_matters=False)
    # Lower case in an id is no evidence of a common noun.
    assert not placeholders.is_placeholder("unidentified-bidder", case_matters=False)


def _extraction():
    return {
        "document": {"key_facts": [
            {"fact": "Beacon Ltd paid an unnamed firm $4,000.", "entities": ["beacon-ltd", "unknown-firm"]},
            {"fact": "The unnamed firm signed the invoice.", "entities": ["Unknown Firm"]},
            {"fact": "Beacon Ltd filed the return.", "entities": ["beacon-ltd"]},
        ]},
        "entities": [
            {"id": "beacon-ltd", "name": "Beacon Ltd", "type": "Company", "roles": [
                {"relationship": "paid", "target_id": "unknown-firm"},
                {"relationship": "owned by", "target_id": "harbour-trust"},
            ]},
            {"id": "unknown-firm", "name": "Unknown firm", "type": "Company", "roles": [
                {"relationship": "paid by", "target_id": "beacon-ltd"},
            ]},
            {"id": "harbour-trust", "name": "Harbour Trust", "type": "Organization", "roles": []},
        ],
        "morgue_entity_id": "unknown-firm",
    }


def test_drop_removes_the_entity_and_every_reference_to_it():
    ext = _extraction()
    warnings = placeholders.drop(ext)

    assert [e["id"] for e in ext["entities"]] == ["beacon-ltd", "harbour-trust"]
    assert len(warnings) == 1 and "Unknown firm" in warnings[0]
    facts = ext["document"]["key_facts"]
    assert len(facts) == 3                                   # every fact keeps its text
    assert facts[0]["entities"] == ["beacon-ltd"]
    assert facts[1]["entities"] == []                        # a non-slug tag of it goes too
    assert facts[2]["entities"] == ["beacon-ltd"]
    beacon = ext["entities"][0]
    assert [r["target_id"] for r in beacon["roles"]] == ["harbour-trust"]
    assert ext["morgue_entity_id"] is None                   # filed as having no subject


def test_drop_clears_a_placeholder_subject_that_names_no_entity():
    ext = {"document": {"key_facts": []}, "entities": [], "morgue_entity_id": "undetermined-subject"}
    assert placeholders.drop(ext) == []
    assert ext["morgue_entity_id"] is None


def test_drop_leaves_a_clean_extraction_alone():
    ext = _extraction()
    ext["entities"][1]["name"] = "Northgate Supply"
    before = repr(ext)
    assert placeholders.drop(ext) == []
    assert repr(ext) == before


def test_drop_never_clears_an_id_a_kept_entity_holds():
    """A dropped entity whose name slugifies to a kept entity's id must not take that id's facts,
    roles or subject with it."""
    ext = {
        "document": {"key_facts": [{"fact": "x", "entities": ["unknown"]}]},
        "entities": [
            {"id": "unknown", "name": "Unknown Mortal Orchestra", "type": "Organization", "roles": []},
            {"id": "placeholder-1", "name": "Unknown", "type": "Person", "roles": []},
        ],
        "morgue_entity_id": "unknown",
    }
    placeholders.drop(ext)
    assert [e["id"] for e in ext["entities"]] == ["unknown"]
    assert ext["document"]["key_facts"][0]["entities"] == ["unknown"]
    assert ext["morgue_entity_id"] == "unknown"
