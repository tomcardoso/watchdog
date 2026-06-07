import json
import pytest
from pathlib import Path

from watchdog.pipeline.batch_get import main as batch_get_main


def run(*argv, capsys):
    """Run batch_get_main with given args, return (stdout, stderr, exit_code)."""
    import sys
    old_argv = sys.argv
    sys.argv = ["watchdog-batch-get", *argv]
    try:
        batch_get_main()
        code = 0
    except SystemExit as e:
        code = e.code
    finally:
        sys.argv = old_argv
    out, err = capsys.readouterr()
    return out, err, code


@pytest.fixture
def batch_file(tmp_path):
    data = [
        {"filename": "a.pdf", "sha256": "aaa111", "page_count": 3, "text": "text of a"},
        {"filename": "b.pdf", "sha256": "bbb222", "page_count": 1, "text": "text of b", "document_type": "Court Order"},
        {"filename": "c.pdf", "error": "OCR failed", "source_path": "_INCOMING/c.pdf"},
    ]
    p = tmp_path / "ingest.json"
    p.write_text(json.dumps(data))
    return p


# ── --count ───────────────────────────────────────────────────────────────────

def test_count(batch_file, capsys):
    out, _, code = run(str(batch_file), "--count", capsys=capsys)
    assert code == 0
    assert out.strip() == "3"


def test_count_empty_batch(tmp_path, capsys):
    p = tmp_path / "empty.json"
    p.write_text("[]")
    out, _, code = run(str(p), "--count", capsys=capsys)
    assert code == 0
    assert out.strip() == "0"


# ── --meta ────────────────────────────────────────────────────────────────────

def test_meta_excludes_text(batch_file, capsys):
    out, _, code = run(str(batch_file), "--index", "0", "--meta", capsys=capsys)
    assert code == 0
    result = json.loads(out)
    assert result["filename"] == "a.pdf"
    assert result["sha256"] == "aaa111"
    assert "text" not in result


def test_meta_second_entry(batch_file, capsys):
    out, _, code = run(str(batch_file), "--index", "1", "--meta", capsys=capsys)
    assert code == 0
    result = json.loads(out)
    assert result["filename"] == "b.pdf"
    assert result["document_type"] == "Court Order"
    assert "text" not in result


# ── --text ────────────────────────────────────────────────────────────────────

def test_text_returns_content(batch_file, capsys):
    out, _, code = run(str(batch_file), "--index", "0", "--text", capsys=capsys)
    assert code == 0
    assert out.strip() == "text of a"


def test_text_missing_field_returns_empty(batch_file, capsys):
    out, _, code = run(str(batch_file), "--index", "2", "--text", capsys=capsys)
    assert code == 0
    assert out.strip() == ""


# ── --field ───────────────────────────────────────────────────────────────────

def test_field_sha256(batch_file, capsys):
    out, _, code = run(str(batch_file), "--index", "0", "--field", "sha256", capsys=capsys)
    assert code == 0
    assert out.strip() == "aaa111"


def test_field_integer_value(batch_file, capsys):
    out, _, code = run(str(batch_file), "--index", "0", "--field", "page_count", capsys=capsys)
    assert code == 0
    assert out.strip() == "3"


def test_field_missing_exits(batch_file, capsys):
    _, _, code = run(str(batch_file), "--index", "0", "--field", "nonexistent", capsys=capsys)
    assert code != 0


# ── error cases ───────────────────────────────────────────────────────────────

def test_missing_file_exits(tmp_path, capsys):
    _, _, code = run(str(tmp_path / "nope.json"), "--count", capsys=capsys)
    assert code != 0


def test_index_out_of_range_exits(batch_file, capsys):
    _, _, code = run(str(batch_file), "--index", "99", "--meta", capsys=capsys)
    assert code != 0


def test_negative_index_exits(batch_file, capsys):
    _, _, code = run(str(batch_file), "--index", "-1", "--meta", capsys=capsys)
    assert code != 0


def test_no_mode_flag_exits(batch_file, capsys):
    _, _, code = run(str(batch_file), "--index", "0", capsys=capsys)
    assert code != 0


def test_no_index_without_count_exits(batch_file, capsys):
    _, _, code = run(str(batch_file), "--meta", capsys=capsys)
    assert code != 0


def test_corrupt_json_exits(tmp_path, capsys):
    p = tmp_path / "bad.json"
    p.write_text("not json {{{")
    _, _, code = run(str(p), "--count", capsys=capsys)
    assert code != 0


def test_non_array_json_exits(tmp_path, capsys):
    p = tmp_path / "obj.json"
    p.write_text('{"key": "value"}')
    _, _, code = run(str(p), "--count", capsys=capsys)
    assert code != 0
