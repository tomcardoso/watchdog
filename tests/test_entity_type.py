"""Tests for the closed entity-type vocabulary and its deterministic collapse map (#335)."""

import pytest

from watchdog.pipeline.entity_type import (
    ENTITY_TYPES,
    FALLBACK_TYPE,
    canonical_type,
)


@pytest.mark.parametrize("canonical", ENTITY_TYPES)
def test_canonical_values_map_to_themselves(canonical):
    """Every canonical slug is a fixed point — canonicalization is idempotent."""
    assert canonical_type(canonical) == canonical
    assert canonical_type(canonical_type(canonical)) == canonical


@pytest.mark.parametrize("raw, expected", [
    # person
    ("Person", "person"),
    ("individual", "person"),
    ("Director", "person"),
    ("judge", "person"),
    # organization — the #335 near-synonyms all collapse together
    ("Company", "organization"),
    ("corporation", "organization"),
    ("Financial Institution", "organization"),
    ("financialinstitution", "organization"),
    ("Bank", "organization"),
    ("insurer", "organization"),
    ("Pension Plan", "organization"),
    ("benefit plan", "organization"),
    ("Fund", "organization"),
    ("Trade Union", "organization"),
    # public-body
    ("Government", "public-body"),
    ("public body", "public-body"),
    ("public_body", "public-body"),
    ("Regulator", "public-body"),
    ("Tribunal", "public-body"),
    ("Municipality", "public-body"),
    # place
    ("Place", "place"),
    ("Address", "place"),
    ("Real Property", "place"),
    ("facility", "place"),
    # asset
    ("Asset", "asset"),
    ("Vehicle", "asset"),
    ("aircraft", "asset"),
    ("Domain Name", "asset"),
    ("Bank Account", "asset"),
    # proceeding — the generalized `case` bucket
    ("Proceeding", "proceeding"),
    ("Court Case", "proceeding"),
    ("Lawsuit", "proceeding"),
    ("insolvency", "proceeding"),
    ("Arbitration", "proceeding"),
    ("inquiry", "proceeding"),
])
def test_synonyms_collapse_to_bucket(raw, expected):
    assert canonical_type(raw) == expected


def test_casing_and_punctuation_are_ignored():
    """Folding is case- and separator-insensitive, so drift in formatting never forks a folder."""
    assert (
        canonical_type("Public-Body")
        == canonical_type("public body")
        == canonical_type("PUBLIC_BODY")
        == canonical_type("publicbody")
        == "public-body"
    )


@pytest.mark.parametrize("raw", ["", "   ", None])
def test_empty_maps_to_fallback(raw):
    assert canonical_type(raw) == FALLBACK_TYPE


@pytest.mark.parametrize("raw", ["db-pension-surp", "widget", "quux-thing", "???"])
def test_unknown_maps_to_fallback(raw):
    """A novel coinage the synonym map doesn't recognise lands in `other` rather than
    silently minting a new folder."""
    assert canonical_type(raw) == FALLBACK_TYPE


def test_fallback_is_not_a_canonical_type():
    """`other` is a code-only backstop — it is never offered to the model as a class."""
    assert FALLBACK_TYPE not in ENTITY_TYPES
