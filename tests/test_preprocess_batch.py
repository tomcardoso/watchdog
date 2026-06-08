"""Tests for preprocess_batch helpers (find_files + preprocess_one paths)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from watchdog.pipeline.preprocess_batch import find_files, preprocess_one


# ── find_files ────────────────────────────────────────────────────────────────

def test_single_file_included(tmp_path):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"")
    assert find_files([str(f)]) == [f]


def test_yml_sidecar_excluded(tmp_path):
    pdf = tmp_path / "doc.pdf"
    yml = tmp_path / "doc.pdf.yml"
    pdf.write_bytes(b"")
    yml.write_bytes(b"")
    result = find_files([str(tmp_path)])
    assert pdf in result
    assert yml not in result


def test_ds_store_excluded(tmp_path):
    f = tmp_path / ".DS_Store"
    f.write_bytes(b"")
    assert find_files([str(tmp_path)]) == []


def test_failed_subdir_excluded(tmp_path):
    ok = tmp_path / "ok.pdf"
    failed_dir = tmp_path / "_FAILED"
    failed_dir.mkdir()
    bad = failed_dir / "bad.pdf"
    ok.write_bytes(b"")
    bad.write_bytes(b"")
    result = find_files([str(tmp_path)])
    assert ok in result
    assert bad not in result


def test_directory_recursion(tmp_path):
    sub = tmp_path / "subdir"
    sub.mkdir()
    f = sub / "nested.pdf"
    f.write_bytes(b"")
    result = find_files([str(tmp_path)])
    assert f in result


def test_output_in_sorted_order(tmp_path):
    for name in ["c.pdf", "a.pdf", "b.pdf"]:
        (tmp_path / name).write_bytes(b"")
    result = find_files([str(tmp_path)])
    names = [r.name for r in result]
    assert names == sorted(names)


def test_empty_directory_returns_empty(tmp_path):
    assert find_files([str(tmp_path)]) == []


def test_nonexistent_path_returns_empty(tmp_path):
    assert find_files([str(tmp_path / "nope.pdf")]) == []


def test_ingest_lock_excluded(tmp_path):
    lock = tmp_path / ".ingest-lock"
    lock.write_bytes(b"")
    assert find_files([str(tmp_path)]) == []


# ── preprocess_one ────────────────────────────────────────────────────────────

def test_preprocess_one_success(tmp_path, monkeypatch):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"")
    payload = {"filename": "doc.pdf", "pages": [{"page": 1, "markdown": "hello world"}]}

    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("watchdog.pipeline.preprocess_batch.subprocess.run", fake_run)
    result = preprocess_one(f)
    assert result["filename"] == "doc.pdf"
    assert result["char_count"] == len("hello world")
    assert result["source_path"] == str(f)


def test_preprocess_one_empty_output_is_error(tmp_path, monkeypatch):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"")

    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="something went wrong")

    monkeypatch.setattr("watchdog.pipeline.preprocess_batch.subprocess.run", fake_run)
    result = preprocess_one(f)
    assert "error" in result
    assert result["source_path"] == str(f)


def test_preprocess_one_timeout_is_error(tmp_path, monkeypatch):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"")

    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 600)

    monkeypatch.setattr("watchdog.pipeline.preprocess_batch.subprocess.run", fake_run)
    result = preprocess_one(f)
    assert "error" in result
    assert "timed out" in result["error"].lower()
    assert result["source_path"] == str(f)


def test_preprocess_one_char_count_sums_pages(tmp_path, monkeypatch):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"")
    payload = {
        "filename": "doc.pdf",
        "pages": [
            {"page": 1, "markdown": "abc"},
            {"page": 2, "markdown": "de"},
        ],
    }

    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("watchdog.pipeline.preprocess_batch.subprocess.run", fake_run)
    result = preprocess_one(f)
    assert result["char_count"] == 5


def test_preprocess_one_passes_vault_path(tmp_path, monkeypatch):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"")
    payload = {"filename": "doc.pdf", "pages": []}
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("watchdog.pipeline.preprocess_batch.subprocess.run", fake_run)
    preprocess_one(f, vault_path="/vault/path")
    assert "--vault-path" in captured["cmd"]
    assert "/vault/path" in captured["cmd"]
