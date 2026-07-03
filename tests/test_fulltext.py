"""Tests for the full-text (exact-term) search index (fulltext.py, #109)."""

import pytest
from watchdog.pipeline import fulltext as fts


@pytest.fixture
def vault(tmp_path):
    return tmp_path / "vault"


def _pages(*texts):
    return [{"page": i + 1, "markdown": t} for i, t in enumerate(texts)]


# --- index_stats ---

def test_stats_empty(vault):
    assert fts.index_stats(vault) == {"corpus": 0, "notes": 0, "total": 0}


def test_stats_after_add_document(vault):
    fts.add_document(vault, "doc.pdf", "sha1", _pages("hello world", "second page"))
    s = fts.index_stats(vault)
    assert s == {"corpus": 2, "notes": 0, "total": 2}


def test_stats_counts_notes_separately(vault):
    fts.add_document(vault, "doc.pdf", "sha1", _pages("hello world"))
    fts.add_note(vault, "entities/alice", "entity", "Alice", "Alice is a person")
    s = fts.index_stats(vault)
    assert s == {"corpus": 1, "notes": 1, "total": 2}


# --- add_document ---

def test_add_document_returns_page_count(vault):
    assert fts.add_document(vault, "doc.pdf", "sha1", _pages("a", "b", "c")) == 3


def test_add_document_blank_pages_indexed_nothing(vault):
    assert fts.add_document(vault, "doc.pdf", "sha1", _pages("", "   ")) == 0
    assert fts.index_stats(vault)["corpus"] == 0


def test_add_document_reingest_replaces_not_duplicates(vault):
    fts.add_document(vault, "doc.pdf", "sha1", _pages("hello world", "second page"))
    fts.add_document(vault, "doc.pdf", "sha1", _pages("hello world revised"))
    assert fts.index_stats(vault)["corpus"] == 1


def test_add_document_carries_page_and_morgue_path(vault):
    fts.add_document(vault, "doc.pdf", "sha1", _pages("the shell company filed"),
                     morgue_path="morgue/acme/filings/doc.pdf")
    hits = fts.search(vault, "shell company")
    assert len(hits) == 1
    assert hits[0]["kind"] == "corpus"
    assert hits[0]["page"] == 1
    assert hits[0]["path"] == "morgue/acme/filings/doc.pdf"
    assert hits[0]["title"] == "doc.pdf"


# --- add_note ---

def test_add_note_indexed_with_kind_and_title(vault):
    fts.add_note(vault, "entities/person/alice", "entity", "Alice Smith", "Alice Smith is a director.")
    hits = fts.search(vault, "director")
    assert len(hits) == 1
    assert hits[0]["kind"] == "entity"
    assert hits[0]["title"] == "Alice Smith"
    assert hits[0]["path"] == "entities/person/alice"
    assert hits[0]["page"] is None


def test_add_note_reingest_replaces_not_duplicates(vault):
    fts.add_note(vault, "timeline", "timeline", "Timeline", "2020: something happened")
    fts.add_note(vault, "timeline", "timeline", "Timeline", "2021: something else happened")
    assert fts.index_stats(vault)["notes"] == 1
    hits = fts.search(vault, "2020")
    assert hits == []


def test_add_note_empty_body_removes_existing_row(vault):
    fts.add_note(vault, "hot", "hot", "Hot cache", "some content")
    assert fts.index_stats(vault)["notes"] == 1
    fts.add_note(vault, "hot", "hot", "Hot cache", "   ")
    assert fts.index_stats(vault)["notes"] == 0


def test_documents_and_notes_use_independent_keys(vault):
    # A note path and a document sha never collide even if coincidentally equal strings.
    fts.add_document(vault, "doc.pdf", "shared-key", _pages("corpus text"))
    fts.add_note(vault, "shared-key", "entity", "Someone", "note text")
    assert fts.index_stats(vault) == {"corpus": 1, "notes": 1, "total": 2}


# --- build_match / search query semantics ---

def test_build_match_bare_words_are_anded():
    assert fts.build_match("shell company") == '"shell" AND "company"'


def test_build_match_quoted_phrase():
    assert fts.build_match('"shell company"') == '"shell company"'


def test_build_match_empty_query():
    assert fts.build_match("   ") == ""


def test_build_match_escapes_embedded_quotes():
    assert fts.build_match('say "hi"') == '"hi" AND "say"'


def test_search_bare_words_require_all_terms(vault):
    fts.add_note(vault, "a", "entity", "A", "shell company filed papers")
    fts.add_note(vault, "b", "entity", "B", "shell corporation filed papers")
    hits = fts.search(vault, "shell company")
    assert [h["path"] for h in hits] == ["a"]


def test_search_quoted_phrase_requires_adjacency(vault):
    fts.add_note(vault, "a", "entity", "A", "Jane Doe signed the filing")
    fts.add_note(vault, "b", "entity", "B", "Doe, Jane signed the other filing")
    hits = fts.search(vault, '"jane doe"')
    assert [h["path"] for h in hits] == ["a"]


def test_search_handles_apostrophe_without_crashing(vault):
    fts.add_note(vault, "a", "entity", "O'Brien", "O'Brien signed the filing")
    hits = fts.search(vault, "O'Brien")
    assert len(hits) == 1


def test_search_is_case_insensitive(vault):
    fts.add_note(vault, "a", "entity", "A", "The Shell Company filed papers")
    hits = fts.search(vault, "shell company")
    assert len(hits) == 1


def test_search_empty_query_returns_no_results(vault):
    fts.add_note(vault, "a", "entity", "A", "some content")
    assert fts.search(vault, "") == []


def test_search_no_index_returns_empty(vault):
    assert fts.search(vault, "anything") == []


def test_search_respects_limit(vault):
    for i in range(5):
        fts.add_note(vault, f"n{i}", "entity", f"N{i}", "shell company filing")
    hits = fts.search(vault, "shell company", limit=2)
    assert len(hits) == 2


def test_search_filters_by_kind(vault):
    fts.add_document(vault, "doc.pdf", "sha1", _pages("shell company in the corpus"))
    fts.add_note(vault, "entities/shell", "entity", "Shell Co", "shell company entity note")
    corpus_only = fts.search(vault, "shell company", kinds=["corpus"])
    assert [h["kind"] for h in corpus_only] == ["corpus"]


def test_search_no_match_returns_empty(vault):
    fts.add_note(vault, "a", "entity", "A", "unrelated content")
    assert fts.search(vault, "nonexistent term") == []
