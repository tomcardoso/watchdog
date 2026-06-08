"""Tests for the embedding index (embed.py). Fastembed is monkeypatched out."""

import numpy as np
import pytest
from pathlib import Path
from watchdog.pipeline import embed as embed_mod


class _FakeEmbedder:
    """Returns a deterministic unit vector for each text (based on text hash)."""
    DIM = 8

    def embed(self, texts):
        for text in texts:
            v = np.zeros(self.DIM, dtype=np.float32)
            v[hash(text) % self.DIM] = 1.0
            yield v


@pytest.fixture(autouse=True)
def fake_embedder(monkeypatch):
    monkeypatch.setattr(embed_mod, "_embedder", _FakeEmbedder())


@pytest.fixture
def vault(tmp_path):
    return tmp_path / "vault"


def _pages(*texts):
    return [{"page": i + 1, "markdown": t} for i, t in enumerate(texts)]


# --- index_stats ---

def test_stats_empty(vault):
    assert embed_mod.index_stats(vault) == {"pages": 0}


def test_stats_after_add(vault):
    embed_mod.add_document(vault, "doc.pdf", _pages("a", "b", "c"))
    assert embed_mod.index_stats(vault)["pages"] == 3


# --- add_document ---

def test_add_returns_page_count(vault):
    n = embed_mod.add_document(vault, "doc.pdf", _pages("hello", "world"))
    assert n == 2


def test_add_creates_index_files(vault):
    embed_mod.add_document(vault, "doc.pdf", _pages("text"))
    assert (vault / ".embeddings" / "vectors.npy").exists()
    assert (vault / ".embeddings" / "meta.json").exists()


def test_add_multiple_documents(vault):
    embed_mod.add_document(vault, "a.pdf", _pages("first"))
    embed_mod.add_document(vault, "b.pdf", _pages("second"))
    assert embed_mod.index_stats(vault)["pages"] == 2


def test_reingest_replaces_not_duplicates(vault):
    embed_mod.add_document(vault, "doc.pdf", _pages("v1 content"))
    embed_mod.add_document(vault, "doc.pdf", _pages("v2 content", "v2 page 2"))
    assert embed_mod.index_stats(vault)["pages"] == 2  # not 3


def test_preview_truncated_to_300(vault):
    long = "x" * 500
    embed_mod.add_document(vault, "doc.pdf", _pages(long))
    _, meta = embed_mod._load(vault)
    assert len(meta[0]["preview"]) == 300


def test_meta_fields(vault):
    embed_mod.add_document(vault, "report.pdf", _pages("some text"))
    _, meta = embed_mod._load(vault)
    assert meta[0]["filename"] == "report.pdf"
    assert meta[0]["page"] == 1
    assert "preview" in meta[0]


# --- search ---

def test_search_empty_index(vault):
    assert embed_mod.search(vault, "query") == []


def test_search_returns_results(vault):
    embed_mod.add_document(vault, "doc.pdf", _pages("alpha", "beta", "gamma"))
    results = embed_mod.search(vault, "alpha", top_n=3)
    assert len(results) == 3
    assert all("filename" in r and "page" in r and "score" in r and "preview" in r for r in results)


def test_search_respects_top_n(vault):
    embed_mod.add_document(vault, "doc.pdf", _pages("a", "b", "c", "d", "e"))
    results = embed_mod.search(vault, "query", top_n=2)
    assert len(results) == 2


def test_search_scores_descending(vault):
    embed_mod.add_document(vault, "doc.pdf", _pages("a", "b", "c"))
    results = embed_mod.search(vault, "query", top_n=3)
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)


# --- add_note ---

def test_add_note_basic(vault):
    embed_mod.add_note(vault, "entities/person/john-doe", "John Doe is a director of Shell Co.")
    assert embed_mod.index_stats(vault)["pages"] == 1


def test_add_note_strips_frontmatter(vault):
    content = "---\nid: john-doe\ntype: person\n---\n\n# John Doe\n\nDirector of Shell Co."
    embed_mod.add_note(vault, "entities/person/john-doe", content)
    _, meta = embed_mod._load(vault)
    assert "---" not in meta[0]["preview"]
    assert "John Doe" in meta[0]["preview"]


def test_add_note_empty_body_skipped(vault):
    embed_mod.add_note(vault, "entities/person/empty", "---\nid: empty\n---\n")
    assert embed_mod.index_stats(vault)["pages"] == 0


def test_add_note_deduplicates(vault):
    embed_mod.add_note(vault, "entities/person/john-doe", "first version")
    embed_mod.add_note(vault, "entities/person/john-doe", "updated version")
    assert embed_mod.index_stats(vault)["pages"] == 1
    _, meta = embed_mod._load(vault)
    assert "updated" in meta[0]["preview"]


def test_page_and_note_coexist(vault):
    embed_mod.add_document(vault, "doc.pdf", _pages("page text"))
    embed_mod.add_note(vault, "entities/person/alice", "Alice is a director")
    assert embed_mod.index_stats(vault)["pages"] == 2


def test_note_reingest_does_not_remove_pages(vault):
    embed_mod.add_document(vault, "doc.pdf", _pages("a", "b"))
    embed_mod.add_note(vault, "entities/person/alice", "Alice")
    embed_mod.add_note(vault, "entities/person/alice", "Alice updated")
    assert embed_mod.index_stats(vault)["pages"] == 3  # 2 pages + 1 note


def test_strip_frontmatter_with_frontmatter():
    content = "---\nkey: value\n---\n\nbody text"
    assert embed_mod._strip_frontmatter(content) == "body text"


def test_strip_frontmatter_without_frontmatter():
    content = "just body text"
    assert embed_mod._strip_frontmatter(content) == "just body text"


def test_search_returns_note_type(vault):
    embed_mod.add_note(vault, "entities/person/alice", "Alice Smith is a director")
    results = embed_mod.search(vault, "director", top_n=1)
    assert results[0]["type"] == "note"
    assert results[0]["note_path"] == "entities/person/alice"


def test_search_returns_page_type(vault):
    embed_mod.add_document(vault, "doc.pdf", _pages("corporation filing"))
    results = embed_mod.search(vault, "corporation", top_n=1)
    assert results[0].get("type", "page") == "page"


# --- exact match test (kept at end) ---

def test_search_exact_match_scores_high(vault):
    # Two texts: one identical to query, one different
    # hash("needle") and hash("haystack") land on different dims
    # so the vector for "needle" will have score 1.0 with query "needle"
    embed_mod.add_document(vault, "doc.pdf", _pages("needle", "haystack"))
    results = embed_mod.search(vault, "needle", top_n=2)
    # The page containing "needle" should score 1.0 (same unit vector as query)
    assert results[0]["score"] == pytest.approx(1.0)
    assert results[0]["page"] == 1
