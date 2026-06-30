"""Tests for the search index (embed.py). Fastembed is monkeypatched out."""

import hashlib
import numpy as np
import pytest
from pathlib import Path
from watchdog.pipeline import embed as embed_mod


class _FakeEmbedder:
    """Returns a deterministic unit vector for each text.

    Uses hashlib.md5 rather than hash() so the dimension index is stable
    across processes regardless of PYTHONHASHSEED.
    """
    DIM = 8

    def embed(self, texts):
        for text in texts:
            v = np.zeros(self.DIM, dtype=np.float32)
            idx = int(hashlib.md5(text.encode()).hexdigest(), 16) % self.DIM
            v[idx] = 1.0
            yield v


@pytest.fixture(autouse=True)
def fake_embedder(monkeypatch):
    monkeypatch.setattr(embed_mod, "_embedder", _FakeEmbedder())


@pytest.fixture(autouse=True)
def no_rerank(monkeypatch):
    # Reranking would load the real ~300MB cross-encoder; disable it for the unit tests
    # that exercise indexing + fusion. The reranker is tested separately with a fake.
    monkeypatch.setattr(embed_mod, "_rerank_enabled", lambda: False)


@pytest.fixture
def vault(tmp_path):
    return tmp_path / "vault"


def _pages(*texts):
    return [{"page": i + 1, "markdown": t} for i, t in enumerate(texts)]


# --- index_stats ---

def test_stats_empty(vault):
    assert embed_mod.index_stats(vault) == {"passages": 0, "notes": 0, "total": 0}


def test_stats_after_add(vault):
    embed_mod.add_document(vault, "doc.pdf", _pages("a", "b", "c"))
    s = embed_mod.index_stats(vault)
    assert s["passages"] == 3   # three short pages → one passage each
    assert s["notes"] == 0
    assert s["total"] == 3


def test_stats_notes_counted_separately(vault):
    embed_mod.add_document(vault, "doc.pdf", _pages("page text"))
    embed_mod.add_note(vault, "entities/alice.md", "Alice is a person")
    s = embed_mod.index_stats(vault)
    assert s["passages"] == 1
    assert s["notes"] == 1
    assert s["total"] == 2


# --- add_document ---

def test_add_returns_passage_count(vault):
    n = embed_mod.add_document(vault, "doc.pdf", _pages("hello", "world"))
    assert n == 2  # two short pages → two passages


def test_add_creates_index_files(vault):
    embed_mod.add_document(vault, "doc.pdf", _pages("text"))
    assert any((vault / ".embeddings" / "docs").glob("*.npy"))


def test_add_multiple_documents(vault):
    embed_mod.add_document(vault, "a.pdf", _pages("first"))
    embed_mod.add_document(vault, "b.pdf", _pages("second"))
    assert embed_mod.index_stats(vault)["passages"] == 2


def test_reingest_replaces_not_duplicates(vault):
    embed_mod.add_document(vault, "doc.pdf", _pages("v1 content"))
    embed_mod.add_document(vault, "doc.pdf", _pages("v2 content", "v2 page 2"))
    assert embed_mod.index_stats(vault)["passages"] == 2  # not 3


def test_blank_pages_index_nothing(vault):
    assert embed_mod.add_document(vault, "doc.pdf", _pages("", "   ")) == 0
    assert embed_mod.index_stats(vault)["passages"] == 0


def test_meta_fields(vault):
    embed_mod.add_document(vault, "report.pdf", _pages("some text"))
    _, meta = embed_mod._load(vault)
    assert meta[0]["type"] == "passage"
    assert meta[0]["filename"] == "report.pdf"
    assert meta[0]["page"] == 1
    assert meta[0]["text"] == "some text"


# --- windowing ---

def test_short_page_is_single_window():
    assert embed_mod._windows("just a few words") == ["just a few words"]


def test_empty_text_yields_no_windows():
    assert embed_mod._windows("") == []
    assert embed_mod._windows("   ") == []


def test_long_page_splits_into_overlapping_windows():
    text = " ".join(f"w{i}" for i in range(300))  # 300 words
    windows = embed_mod._windows(text)
    assert len(windows) > 1
    # each window is at most _WINDOW_SIZE words
    assert all(len(w.split()) <= embed_mod._WINDOW_SIZE for w in windows)
    # consecutive windows overlap by _WINDOW_OVERLAP words
    first_tail = windows[0].split()[-embed_mod._WINDOW_OVERLAP:]
    second_head = windows[1].split()[:embed_mod._WINDOW_OVERLAP]
    assert first_tail == second_head


def test_long_page_produces_multiple_passages(vault):
    text = " ".join(f"w{i}" for i in range(300))
    n = embed_mod.add_document(vault, "long.pdf", [{"page": 4, "markdown": text}])
    assert n > 1
    _, meta = embed_mod._load(vault)
    # every passage from this page keeps the page citation
    assert all(m["page"] == 4 for m in meta)


# --- query parsing ---

def test_parse_plain_query():
    assert embed_mod._parse_query("kickback scheme") == (["kickback scheme"], [])


def test_parse_negative_phrase():
    pos, neg = embed_mod._parse_query("kickback scheme -legitimate consulting")
    assert pos == ["kickback scheme"]
    assert neg == ["legitimate consulting"]


def test_parse_positive_and_negative():
    pos, neg = embed_mod._parse_query("payment +offshore -salary")
    assert pos == ["payment", "offshore"]
    assert neg == ["salary"]


def test_parse_keeps_hyphenated_word_intact():
    pos, neg = embed_mod._parse_query("anti-bribery controls")
    assert pos == ["anti-bribery controls"]
    assert neg == []


# --- search ---

def test_search_empty_index(vault):
    assert embed_mod.search(vault, "query") == []


def test_search_returns_results(vault):
    embed_mod.add_document(vault, "doc.pdf", _pages("alpha", "beta", "gamma"))
    results = embed_mod.search(vault, "alpha", top_n=3)
    assert len(results) == 3
    assert all("filename" in r and "page" in r and "score" in r and "text" in r for r in results)


def test_search_respects_top_n(vault):
    embed_mod.add_document(vault, "doc.pdf", _pages("a", "b", "c", "d", "e"))
    results = embed_mod.search(vault, "query", top_n=2)
    assert len(results) == 2


def test_search_scores_descending(vault):
    embed_mod.add_document(vault, "doc.pdf", _pages("a", "b", "c"))
    results = embed_mod.search(vault, "query", top_n=3)
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)


def test_search_min_score_filters(vault):
    embed_mod.add_document(vault, "doc.pdf", _pages("alpha", "beta", "gamma"))
    # A very high threshold drops everything but a (near-)exact match.
    results = embed_mod.search(vault, "query", min_score=0.99)
    assert all(r["score"] >= 0.99 for r in results)


def test_search_scope_corpus_excludes_notes(vault):
    embed_mod.add_document(vault, "doc.pdf", _pages("corporation filing"))
    embed_mod.add_note(vault, "entities/person/alice", "Alice is a director")
    results = embed_mod.search(vault, "director", scope="corpus")
    assert all(r.get("type") == "passage" for r in results)


def test_search_scope_notes_excludes_passages(vault):
    embed_mod.add_document(vault, "doc.pdf", _pages("corporation filing"))
    embed_mod.add_note(vault, "entities/person/alice", "Alice is a director")
    results = embed_mod.search(vault, "director", scope="notes")
    assert all(r.get("type") == "note" for r in results)


# --- query embedding / prefix ---

def test_query_uses_bge_prefix(vault, monkeypatch):
    captured = {}

    class _Spy(_FakeEmbedder):
        def embed(self, texts):
            captured.setdefault("texts", []).extend(texts)
            return super().embed(texts)

    embed_mod.add_document(vault, "doc.pdf", _pages("some corpus text"))
    monkeypatch.setattr(embed_mod, "_embedder", _Spy())
    embed_mod.search(vault, "shell company")
    assert any(t == embed_mod._QUERY_PREFIX + "shell company" for t in captured["texts"])


# --- add_note ---

def test_add_note_basic(vault):
    embed_mod.add_note(vault, "entities/person/john-doe", "John Doe is a director of Shell Co.")
    assert embed_mod.index_stats(vault)["notes"] == 1


def test_add_note_strips_frontmatter(vault):
    content = "---\nid: john-doe\ntype: person\n---\n\n# John Doe\n\nDirector of Shell Co."
    embed_mod.add_note(vault, "entities/person/john-doe", content)
    _, meta = embed_mod._load(vault)
    assert "---" not in meta[0]["preview"]
    assert "John Doe" in meta[0]["preview"]


def test_add_note_empty_body_skipped(vault):
    embed_mod.add_note(vault, "entities/person/empty", "---\nid: empty\n---\n")
    assert embed_mod.index_stats(vault)["total"] == 0


def test_add_note_deduplicates(vault):
    embed_mod.add_note(vault, "entities/person/john-doe", "first version")
    embed_mod.add_note(vault, "entities/person/john-doe", "updated version")
    assert embed_mod.index_stats(vault)["notes"] == 1
    _, meta = embed_mod._load(vault)
    assert "updated" in meta[0]["preview"]


def test_passage_and_note_coexist(vault):
    embed_mod.add_document(vault, "doc.pdf", _pages("page text"))
    embed_mod.add_note(vault, "entities/person/alice", "Alice is a director")
    s = embed_mod.index_stats(vault)
    assert s["passages"] == 1
    assert s["notes"] == 1
    assert s["total"] == 2


def test_note_reingest_does_not_remove_passages(vault):
    embed_mod.add_document(vault, "doc.pdf", _pages("a", "b"))
    embed_mod.add_note(vault, "entities/person/alice", "Alice")
    embed_mod.add_note(vault, "entities/person/alice", "Alice updated")
    s = embed_mod.index_stats(vault)
    assert s["passages"] == 2
    assert s["notes"] == 1
    assert s["total"] == 3


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


def test_search_returns_passage_type(vault):
    embed_mod.add_document(vault, "doc.pdf", _pages("corporation filing"))
    results = embed_mod.search(vault, "corporation", top_n=1, scope="corpus")
    assert results[0].get("type") == "passage"


# --- exact match test (kept at end) ---

def test_search_exact_match_scores_high(vault, monkeypatch):
    # With the bge query prefix, an exact-text match no longer hashes to the same
    # fake vector — so disable the prefix to test the pure exact-match behaviour.
    monkeypatch.setattr(embed_mod, "_QUERY_PREFIX", "")
    embed_mod.add_document(vault, "doc.pdf", _pages("needle", "haystack"))
    results = embed_mod.search(vault, "needle", top_n=2)
    # The passage containing "needle" should score 1.0 (same unit vector as query)
    assert results[0]["score"] == pytest.approx(1.0)


# --- hybrid corpus retrieval: BM25 + fusion ---

def test_tokenize_unicode():
    # exact-term retrieval must not silently drop non-ASCII corpora (symbols like № are dropped)
    assert embed_mod._tokenize("Acme Corp. №42 café Москва") == [
        "acme", "corp", "42", "café", "москва"]


def test_bm25_ranks_exact_term_match_first():
    docs = [embed_mod._tokenize(t) for t in
            ["the quarterly report", "shell company in cyprus", "annual filing"]]
    scores = embed_mod._bm25_scores(embed_mod._tokenize("cyprus shell"), docs)
    assert scores[1] == max(scores) and scores[1] > 0
    assert scores[0] == 0 and scores[2] == 0


def test_bm25_no_query_terms_is_uniform_zero():
    docs = [embed_mod._tokenize("alpha"), embed_mod._tokenize("beta")]
    assert embed_mod._bm25_scores(embed_mod._tokenize("zzz"), docs) == [0.0, 0.0]


def test_rrf_fuses_rankings():
    # identical rankings preserve order
    assert embed_mod._rrf([0, 1, 2], [0, 1, 2])[0] == 0
    # an item present in BOTH rankings outranks one present in only a single ranking
    assert embed_mod._rrf([0, 1], [1])[0] == 1


def test_bm25_recovers_exact_token_cosine_misses(vault):
    # Dense FakeEmbedder hashes whole strings, so an exact token inside a longer
    # passage won't match by cosine — BM25 must surface it. Query token "kickback"
    # appears only in the second passage.
    embed_mod.add_document(vault, "doc.pdf", _pages(
        "routine administrative correspondence about scheduling",
        "the kickback was wired through an offshore intermediary"))
    results = embed_mod.search(vault, "kickback", top_n=2, scope="corpus")
    assert results[0]["text"].startswith("the kickback")


def test_context_prefix_stored_and_embedded(vault, monkeypatch):
    captured = {}

    class _Spy(_FakeEmbedder):
        def embed(self, texts):
            captured.setdefault("texts", []).extend(texts)
            return super().embed(texts)

    monkeypatch.setattr(embed_mod, "_embedder", _Spy())
    embed_mod.add_document(vault, "doc.pdf", _pages("the board approved the transfer"),
                           context="Acme 2024 filing — corporate. Mentions: Acme Corp.")
    # the prefix is embedded with the window…
    assert any("Acme Corp" in t and "board approved" in t for t in captured["texts"])
    # …but the stored/cited text stays the clean window, with the prefix kept separately
    _, meta = embed_mod._load(vault)
    assert meta[0]["text"] == "the board approved the transfer"
    assert "Acme Corp" in meta[0]["context"]


def test_rerank_reorders_pool(vault, monkeypatch):
    embed_mod.add_document(vault, "doc.pdf", _pages("first passage", "second passage", "third passage"))

    class _FakeReranker:
        def rerank(self, query, docs):
            # score so the passage containing "third" wins regardless of fusion order
            return [10.0 if "third" in d else 0.0 for d in docs]

    monkeypatch.setattr(embed_mod, "_rerank_enabled", lambda: True)
    monkeypatch.setattr(embed_mod, "_get_reranker", lambda: _FakeReranker())
    results = embed_mod.search(vault, "passage", top_n=3, scope="corpus", rerank=True)
    assert results[0]["text"] == "third passage"


def test_rerank_failure_falls_back_to_fusion(vault, monkeypatch):
    embed_mod.add_document(vault, "doc.pdf", _pages("alpha", "beta"))

    def _boom():
        raise RuntimeError("reranker model unavailable")

    monkeypatch.setattr(embed_mod, "_rerank_enabled", lambda: True)
    monkeypatch.setattr(embed_mod, "_get_reranker", _boom)
    # must not raise — search degrades to fusion order
    results = embed_mod.search(vault, "alpha", top_n=2, scope="corpus", rerank=True)
    assert len(results) == 2
    assert results[0]["page"] == 1
