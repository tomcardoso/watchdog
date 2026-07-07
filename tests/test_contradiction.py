"""Tests for `watchdog contradiction-add` — the deterministic promote command that writes a
verified surface-found contradiction candidate into an entity note (#312)."""

import json
from pathlib import Path

import pytest

from watchdog.pipeline import contradiction, resolutions
from watchdog.pipeline.write_vault import run as wv_run, _extract_section

from tests.test_write_vault import make_vault, make_extraction


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _setup(tmp_path: Path) -> Path:
    """A vault with entity ``alice-smith`` and two documents (slugs ``test-doc`` /
    ``second-doc``) ingested through the real pipeline writer."""
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    wv_run(make_extraction(tmp_path), vault)
    wv_run(make_extraction(tmp_path, {
        "document": {"sha256": "def456", "filename": "second-doc.pdf",
                     "original_path": "_INCOMING/second-doc.pdf", "title": "Second Document"},
    }), vault)
    return vault


def _note(vault: Path, eid: str = "alice-smith") -> str:
    return (vault / "entities" / "person" / f"{eid}.md").read_text()


def _entities(vault: Path) -> dict:
    return json.loads((vault / ".watchdog" / "Registry" / "entities.json").read_text())


# ── build_callout format ──────────────────────────────────────────────────────

def test_build_callout_matches_extraction_format():
    c = contradiction.build_callout(
        "role mismatch",
        "director", "doc-a", "Doc A", 3,
        "officer", "doc-b", "Doc B", None,
    )
    assert c == (
        "> [!contradiction] role mismatch\n"
        "> - **director** — [[documents/doc-a|Doc A]], p. 3\n"
        "> - **officer** — [[documents/doc-b|Doc B]]"
    )


def test_build_callout_defangs_document_title():
    c = contradiction.build_callout("x", "a", "s", "Evil ]] [[secret", 1, "b", "t", "T", 2)
    assert "]] [[" not in c  # wikilink brackets in a registry-sourced title are neutralized


# ── Happy path ────────────────────────────────────────────────────────────────

def test_add_writes_callout_to_note_and_ledger(tmp_path):
    vault = _setup(tmp_path)
    result = contradiction.run(
        vault, "alice-smith", "role mismatch",
        "director", "test-doc", 3,
        "officer", "second-doc", 5,
    )
    assert result["added"] is True
    assert result["entity_name"] == "Alice Smith"

    body = _extract_section(_note(vault), "Contradictions")
    assert "[!contradiction] role mismatch" in body
    assert "**director** — [[documents/test-doc|Test Document]], p. 3" in body
    assert "**officer** — [[documents/second-doc|Second Document]], p. 5" in body

    # The registry entry is the ledger; the callout is keyed so resolve/unresolve target it.
    entry = _entities(vault)["alice-smith"]
    assert any(resolutions.contradiction_id(c) == result["rid"] for c in entry["contradictions"])


def test_page_is_optional(tmp_path):
    vault = _setup(tmp_path)
    contradiction.run(vault, "alice-smith", "address",
                      "123 Main St", "test-doc", None,
                      "456 Oak Ave", "second-doc", None)
    body = _extract_section(_note(vault), "Contradictions")
    assert "**123 Main St** — [[documents/test-doc|Test Document]]" in body
    assert "p." not in body


def test_doc_ref_accepts_documents_prefix(tmp_path):
    vault = _setup(tmp_path)
    result = contradiction.run(vault, "alice-smith", "role",
                               "d", "documents/test-doc", 1,
                               "o", "documents/second-doc", 2)
    assert result["added"] is True
    assert "[[documents/test-doc|Test Document]]" in _extract_section(_note(vault), "Contradictions")


# ── Validation ────────────────────────────────────────────────────────────────

def test_unknown_entity_errors(tmp_path):
    vault = _setup(tmp_path)
    with pytest.raises(ValueError, match="entity 'nobody' not found"):
        contradiction.run(vault, "nobody", "x", "a", "test-doc", None, "b", "second-doc", None)


def test_unknown_document_slug_errors(tmp_path):
    vault = _setup(tmp_path)
    with pytest.raises(ValueError, match="no-such-doc"):
        contradiction.run(vault, "alice-smith", "x", "a", "no-such-doc", None, "b", "second-doc", None)


# ── Idempotence ───────────────────────────────────────────────────────────────

def test_second_identical_add_is_a_noop(tmp_path):
    vault = _setup(tmp_path)
    args = ("alice-smith", "role mismatch",
            "director", "test-doc", 3, "officer", "second-doc", 5)
    first = contradiction.run(vault, *args)
    second = contradiction.run(vault, *args)
    assert first["added"] is True
    assert second["added"] is False
    assert second["rid"] == first["rid"]
    assert _note(vault).count("[!contradiction]") == 1


# ── Resolutions layer integration ─────────────────────────────────────────────

def test_resolved_callout_is_filtered_from_body_but_kept_in_ledger(tmp_path):
    vault = _setup(tmp_path)
    first = contradiction.run(vault, "alice-smith", "role",
                              "director", "test-doc", 1, "officer", "second-doc", 2)
    resolutions.resolve(vault, [first["rid"]])

    # Adding a second, different callout triggers a note rewrite; the resolved one drops
    # from the rendered body while the ledger keeps both (unresolving would restore it).
    contradiction.run(vault, "alice-smith", "address",
                      "123 Main St", "test-doc", 1, "456 Oak Ave", "second-doc", 2)

    body = _extract_section(_note(vault), "Contradictions")
    assert "[!contradiction] role" not in body       # resolved → filtered from the body
    assert "[!contradiction] address" in body         # the new one is present
    assert len(_entities(vault)["alice-smith"]["contradictions"]) == 2
