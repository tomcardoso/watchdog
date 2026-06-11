"""Tests for near_dup core logic and CLI."""

import json
import sys
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
    assert isinstance(result, set)
    assert len(result) > 0

def test_shingles_from_text_empty():
    result = shingles_from_text("")
    assert isinstance(result, set)


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

def test_cli_falls_back_to_shingles_for_old_docs(tmp_path, capsys):
    text = "the quick brown fox jumps over the lazy dog " * 10
    sh = list(shingles_from_text(text))[:200]
    reg = tmp_path / "documents.json"
    reg.write_text(json.dumps({
        "old123": {"filename": "old.pdf", "shingles": sh}
    }))
    out, _, code = _run("--text", text, "--registry", str(reg), "--threshold", "0.8", capsys=capsys)
    assert code == 0
    data = json.loads(out)
    assert len(data["near_duplicates"]) == 1
    assert data["near_duplicates"][0]["sha256"] == "old123"


# ── CLI ───────────────────────────────────────────────────────────────────────

def _run(*argv, capsys, stdin_text=None):
    import watchdog.pipeline.near_dup as nd
    old_argv = sys.argv
    sys.argv = ["watchdog-near-dup", *argv]
    code = 0
    if stdin_text is not None:
        import io
        old_stdin = sys.stdin
        sys.stdin = io.StringIO(stdin_text)
    try:
        nd.main()
    except SystemExit as e:
        code = e.code
    finally:
        sys.argv = old_argv
        if stdin_text is not None:
            sys.stdin = old_stdin
    out, err = capsys.readouterr()
    return out, err, code


def test_cli_text_no_matches(tmp_path, capsys):
    reg = tmp_path / "documents.json"
    reg.write_text("{}")
    out, _, code = _run("--text", "hello world foo bar", "--registry", str(reg), capsys=capsys)
    assert code == 0
    data = json.loads(out)
    assert data["near_duplicates"] == []


def test_cli_text_finds_match(tmp_path, capsys):
    text = "the quick brown fox jumps over the lazy dog " * 10
    sig = minhash(shingles_from_text(text))
    reg = tmp_path / "documents.json"
    reg.write_text(json.dumps({
        "abc123": {"filename": "original.pdf", "minhash": sig, "document_note": "documents/original"}
    }))
    out, _, code = _run("--text", text, "--registry", str(reg), "--threshold", "0.8", capsys=capsys)
    assert code == 0
    data = json.loads(out)
    assert len(data["near_duplicates"]) == 1
    assert data["near_duplicates"][0]["sha256"] == "abc123"


def test_cli_text_file(tmp_path, capsys):
    text_file = tmp_path / "text.txt"
    text_file.write_text("hello world")
    reg = tmp_path / "documents.json"
    reg.write_text("{}")
    out, _, code = _run("--text-file", str(text_file), "--registry", str(reg), capsys=capsys)
    assert code == 0
    data = json.loads(out)
    assert "near_duplicates" in data


def test_cli_threshold_filters_partial_match(tmp_path, capsys):
    text_a = "apple banana cherry date elderberry " * 5
    text_b = "apple banana cherry fig grape " * 5
    sig_b = minhash(shingles_from_text(text_b))
    reg = tmp_path / "documents.json"
    reg.write_text(json.dumps({
        "xyz": {"filename": "b.pdf", "minhash": sig_b}
    }))
    # High threshold — should not match
    out, _, code = _run("--text", text_a, "--registry", str(reg), "--threshold", "0.99", capsys=capsys)
    data = json.loads(out)
    assert data["near_duplicates"] == []


def test_cli_no_text_source_exits(tmp_path, capsys):
    reg = tmp_path / "documents.json"
    reg.write_text("{}")
    _, _, code = _run("--registry", str(reg), capsys=capsys)
    assert code != 0


def test_cli_output_includes_minhash(tmp_path, capsys):
    reg = tmp_path / "documents.json"
    reg.write_text("{}")
    out, _, code = _run("--text", "hello world foo bar baz", "--registry", str(reg), capsys=capsys)
    data = json.loads(out)
    assert "candidate_minhash" in data
    assert len(data["candidate_minhash"]) == 128
    assert all(isinstance(v, int) for v in data["candidate_minhash"])


def test_cli_summary_flag_prints_decision_only(tmp_path, capsys):
    full_output = {
        "near_duplicates": [{"sha256": "abc", "filename": "a.pdf", "similarity": 0.92, "document_note": "documents/a"}],
        "candidate_minhash": list(range(128)),
    }
    out_file = tmp_path / "nd.json"
    out_file.write_text(json.dumps(full_output))
    out, _, code = _run("--summary", str(out_file), capsys=capsys)
    assert code == 0
    data = json.loads(out)
    assert "near_duplicates" in data
    assert "top_similarity" in data
    assert data["top_similarity"] == 0.92
    assert "candidate_minhash" not in data


def test_cli_summary_flag_no_matches(tmp_path, capsys):
    out_file = tmp_path / "nd.json"
    out_file.write_text(json.dumps({"near_duplicates": [], "candidate_minhash": list(range(128))}))
    out, _, code = _run("--summary", str(out_file), capsys=capsys)
    data = json.loads(out)
    assert data["near_duplicates"] == []
    assert data["top_similarity"] == 0.0
