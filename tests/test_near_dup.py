"""Tests for near_dup core logic and CLI."""

import pytest

from watchdog.pipeline.near_dup import tokenize, shingles, jaccard, shingles_from_text, minhash, minhash_similarity


# ── tokenize ──────────────────────────────────────────────────────────────────

def test_tokenize_basic():
    assert tokenize("Hello, World!") == ["hello", "world"]

def test_tokenize_numbers():
    assert "123" in tokenize("case 123 filed")

def test_tokenize_empty():
    assert tokenize("") == []

def test_tokenize_strips_punctuation():
    assert tokenize("don't stop") == ["don", "t", "stop"]


# ── shingles ──────────────────────────────────────────────────────────────────

def test_shingles_basic():
    tokens = ["a", "b", "c", "d"]
    s = shingles(tokens, k=3)
    assert "a b c" in s
    assert "b c d" in s
    assert len(s) == 2

def test_shingles_fewer_tokens_than_k():
    s = shingles(["a", "b"], k=3)
    assert s == {"a b"}

def test_shingles_exactly_k():
    s = shingles(["x", "y", "z"], k=3)
    assert s == {"x y z"}

def test_shingles_empty():
    s = shingles([], k=3)
    assert s == {""}


# ── jaccard ───────────────────────────────────────────────────────────────────

def test_jaccard_identical():
    a = {"a b c", "b c d"}
    assert jaccard(a, a) == 1.0

def test_jaccard_disjoint():
    assert jaccard({"a b"}, {"c d"}) == 0.0

def test_jaccard_partial():
    a = {"a b", "b c"}
    b = {"b c", "c d"}
    assert jaccard(a, b) == pytest.approx(1/3, abs=1e-6)

def test_jaccard_both_empty():
    assert jaccard(set(), set()) == 1.0

def test_jaccard_one_empty():
    assert jaccard(set(), {"a b"}) == 0.0
    assert jaccard({"a b"}, set()) == 0.0


# ── shingles_from_text ────────────────────────────────────────────────────────

def test_shingles_from_text_returns_set():
    result = shingles_from_text("the quick brown fox")
    assert result == {"the quick brown", "quick brown fox"}

def test_shingles_from_text_empty():
    result = shingles_from_text("")
    assert result == {""}


# ── minhash ───────────────────────────────────────────────────────────────────

def test_minhash_returns_correct_length():
    sh = shingles_from_text("the quick brown fox jumps over the lazy dog")
    sig = minhash(sh)
    assert len(sig) == 128

def test_minhash_identical_sets_give_identical_signatures():
    sh = shingles_from_text("hello world foo bar baz")
    assert minhash(sh) == minhash(sh)

def test_minhash_empty_set():
    sig = minhash(set())
    assert len(sig) == 128
    assert all(v == 0 for v in sig)

def test_minhash_similarity_identical():
    sh = shingles_from_text("the quick brown fox " * 20)
    sig = minhash(sh)
    assert minhash_similarity(sig, sig) == 1.0

def test_minhash_similarity_disjoint():
    sig_a = minhash(shingles_from_text("apple banana cherry " * 20))
    sig_b = minhash(shingles_from_text("xylophone zither oboe " * 20))
    assert minhash_similarity(sig_a, sig_b) < 0.1

def test_minhash_similarity_near_identical():
    # Long text with unique words so the shingle set is large; appending a few
    # words changes very few shingles and similarity stays high.
    text = " ".join(f"token{i}" for i in range(500))
    sh_a = shingles_from_text(text)
    sh_b = shingles_from_text(text + " extra unique words here")
    sim = minhash_similarity(minhash(sh_a), minhash(sh_b))
    assert sim > 0.9

def test_minhash_similarity_length_mismatch():
    assert minhash_similarity([1, 2], [1, 2, 3]) == 0.0
