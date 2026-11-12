"""Tests for near_dup core logic and CLI."""


from watchdog.pipeline.near_dup import tokenize, shingles, shingles_from_text, minhash


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
