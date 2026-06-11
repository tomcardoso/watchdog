"""Tests for preprocess_batch helpers (find_files + preprocess_one paths)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from watchdog.pipeline.preprocess_batch import (
    find_files,
    preprocess_one,
    _count_pdf_pages,
    _adaptive_workers,
    _resolve_workers,
    _run_ingest_inner,
    _page_label,
    _prune_empty_dirs,
)
import watchdog.pipeline.preprocess_batch as ppb


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

class _FakePopen:
    """Minimal Popen mock: communicate() returns immediately."""
    def __init__(self, stdout="", stderr=""):
        self._stdout = stdout
        self._stderr = stderr
        self.cmd_seen = None

    def communicate(self, timeout=None):
        return self._stdout, self._stderr

    def kill(self): pass
    def wait(self): pass


class _FakePopenTimeout:
    """Popen mock that always raises TimeoutExpired from communicate()."""
    def communicate(self, timeout=None):
        raise subprocess.TimeoutExpired([], timeout or 0)

    def kill(self): pass
    def wait(self): pass


def _fake_popen_factory(stdout="", stderr="", captured=None):
    def fake_popen(cmd, **kw):
        p = _FakePopen(stdout, stderr)
        if captured is not None:
            captured["cmd"] = cmd
        return p
    return fake_popen


def test_preprocess_one_success(tmp_path, monkeypatch):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"")
    payload = {"filename": "doc.pdf", "pages": [{"page": 1, "markdown": "hello world"}]}
    monkeypatch.setattr("watchdog.pipeline.preprocess_batch.subprocess.Popen",
                        _fake_popen_factory(stdout=json.dumps(payload)))
    result = preprocess_one(f)
    assert result["filename"] == "doc.pdf"
    assert result["char_count"] == len("hello world")
    assert result["source_path"] == str(f)


def test_preprocess_one_empty_output_is_error(tmp_path, monkeypatch):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"")
    monkeypatch.setattr("watchdog.pipeline.preprocess_batch.subprocess.Popen",
                        _fake_popen_factory(stdout="", stderr="something went wrong"))
    result = preprocess_one(f)
    assert "error" in result
    assert result["source_path"] == str(f)


def test_preprocess_one_timeout_is_error(tmp_path, monkeypatch):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"")
    monkeypatch.setattr("watchdog.pipeline.preprocess_batch.subprocess.Popen",
                        lambda cmd, **kw: _FakePopenTimeout())
    result = preprocess_one(f, timeout=0)  # deadline=now, triggers on first poll
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
    monkeypatch.setattr("watchdog.pipeline.preprocess_batch.subprocess.Popen",
                        _fake_popen_factory(stdout=json.dumps(payload)))
    result = preprocess_one(f)
    assert result["char_count"] == 5


def test_preprocess_one_passes_vault_path(tmp_path, monkeypatch):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"")
    payload = {"filename": "doc.pdf", "pages": []}
    captured = {}
    monkeypatch.setattr("watchdog.pipeline.preprocess_batch.subprocess.Popen",
                        _fake_popen_factory(stdout=json.dumps(payload), captured=captured))
    preprocess_one(f, vault_path="/vault/path")
    assert "--vault-path" in captured["cmd"]
    assert "/vault/path" in captured["cmd"]


def test_preprocess_one_passes_chunk_workers(tmp_path, monkeypatch):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"")
    payload = {"filename": "doc.pdf", "pages": []}
    captured = {}
    monkeypatch.setattr("watchdog.pipeline.preprocess_batch.subprocess.Popen",
                        _fake_popen_factory(stdout=json.dumps(payload), captured=captured))
    preprocess_one(f, chunk_workers=6)
    assert "--chunk-workers" in captured["cmd"]
    assert "6" in captured["cmd"]


def test_preprocess_one_omits_chunk_workers_when_none(tmp_path, monkeypatch):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"")
    payload = {"filename": "doc.pdf", "pages": []}
    captured = {}
    monkeypatch.setattr("watchdog.pipeline.preprocess_batch.subprocess.Popen",
                        _fake_popen_factory(stdout=json.dumps(payload), captured=captured))
    preprocess_one(f)
    assert "--chunk-workers" not in captured["cmd"]


# ── _count_pdf_pages ──────────────────────────────────────────────────────────

def test_count_pdf_pages_non_pdf_returns_one(tmp_path):
    f = tmp_path / "doc.txt"
    f.write_text("hello")
    assert _count_pdf_pages(f) == 1


def test_count_pdf_pages_reads_qpdf_output(tmp_path, monkeypatch):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"")

    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout="42\n", stderr="")

    monkeypatch.setattr("watchdog.pipeline.preprocess_batch.subprocess.run", fake_run)
    assert _count_pdf_pages(f) == 42


def test_count_pdf_pages_qpdf_failure_returns_one(tmp_path, monkeypatch):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"")

    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="error")

    monkeypatch.setattr("watchdog.pipeline.preprocess_batch.subprocess.run", fake_run)
    assert _count_pdf_pages(f) == 1


# ── _adaptive_workers ─────────────────────────────────────────────────────────

def test_adaptive_workers_short_docs_favor_preprocess(tmp_path, monkeypatch):
    files = [tmp_path / f"doc{i}.pdf" for i in range(5)]
    for f in files:
        f.write_bytes(b"")
    monkeypatch.setattr("watchdog.pipeline.preprocess_batch._count_pdf_pages", lambda p: 1)
    monkeypatch.setattr("watchdog.pipeline.preprocess_batch._perf_cpu_count", lambda: 10)

    pre, chunk, counts = _adaptive_workers(files)
    assert pre >= chunk
    assert set(counts.keys()) == set(files)


def test_adaptive_workers_long_docs_favor_chunk(tmp_path, monkeypatch):
    files = [tmp_path / "big.pdf"]
    files[0].write_bytes(b"")
    monkeypatch.setattr("watchdog.pipeline.preprocess_batch._count_pdf_pages", lambda p: 200)
    monkeypatch.setattr("watchdog.pipeline.preprocess_batch._perf_cpu_count", lambda: 10)

    pre, chunk, counts = _adaptive_workers(files)
    assert chunk >= pre


# ── _resolve_workers ──────────────────────────────────────────────────────────

def test_resolve_workers_auto_uses_adaptive(tmp_path, monkeypatch):
    files = [tmp_path / "a.pdf", tmp_path / "b.pdf"]
    for f in files:
        f.write_bytes(b"")
    monkeypatch.setenv("HOME", str(tmp_path))  # no config.json → both default to "auto"
    monkeypatch.setattr("watchdog.pipeline.preprocess_batch._count_pdf_pages", lambda p: 1)
    monkeypatch.setattr("watchdog.pipeline.preprocess_batch._perf_cpu_count", lambda: 10)

    _, _, adaptive, counts = _resolve_workers(files, explicit_pre=None)
    assert adaptive is True
    assert counts is not None


def test_resolve_workers_config_int_overrides_adaptive(tmp_path, monkeypatch):
    files = [tmp_path / f"doc{i}.pdf" for i in range(3)]
    for f in files:
        f.write_bytes(b"")
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".watchdog").mkdir()
    (tmp_path / ".watchdog" / "config.json").write_text(
        json.dumps({"chew_workers": 2, "chunk_workers": 3})
    )

    pre, chunk, adaptive, counts = _resolve_workers(files, explicit_pre=None)
    assert pre == 2
    assert chunk == 3
    assert adaptive is False
    assert counts is None


def test_resolve_workers_explicit_pre_overrides_config(tmp_path, monkeypatch):
    files = [tmp_path / f"doc{i}.pdf" for i in range(10)]
    for f in files:
        f.write_bytes(b"")
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".watchdog").mkdir()
    (tmp_path / ".watchdog" / "config.json").write_text(
        json.dumps({"chew_workers": 2, "chunk_workers": 3})
    )

    pre, chunk, *_ = _resolve_workers(files, explicit_pre=7)
    assert pre == 7
    assert chunk == 3  # chunk still from config


def test_resolve_workers_explicit_chunk_overrides_config(tmp_path, monkeypatch):
    files = [tmp_path / f"doc{i}.pdf" for i in range(4)]
    for f in files:
        f.write_bytes(b"")
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".watchdog").mkdir()
    (tmp_path / ".watchdog" / "config.json").write_text(
        json.dumps({"chew_workers": 2, "chunk_workers": 3})
    )

    pre, chunk, *_ = _resolve_workers(files, explicit_pre=None, explicit_chunk=8)
    assert pre == 2   # from config
    assert chunk == 8  # from explicit flag


def test_resolve_workers_caps_pre_to_file_count(tmp_path, monkeypatch):
    files = [tmp_path / "only.pdf"]
    files[0].write_bytes(b"")
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".watchdog").mkdir()
    (tmp_path / ".watchdog" / "config.json").write_text(
        json.dumps({"chew_workers": 8, "chunk_workers": 2})
    )

    pre, *_ = _resolve_workers(files, explicit_pre=None)
    assert pre == 1  # capped to len(files)


def test_resolve_workers_returns_page_counts_when_adaptive(tmp_path, monkeypatch):
    files = [tmp_path / f"doc{i}.pdf" for i in range(3)]
    for f in files:
        f.write_bytes(b"")
    monkeypatch.setenv("HOME", str(tmp_path))
    call_counts = {f: i + 1 for i, f in enumerate(files)}
    monkeypatch.setattr(
        "watchdog.pipeline.preprocess_batch._count_pdf_pages",
        lambda p: call_counts[p],
    )
    monkeypatch.setattr("watchdog.pipeline.preprocess_batch._perf_cpu_count", lambda: 10)

    _, _, _, counts = _resolve_workers(files, explicit_pre=None)
    assert counts == call_counts


# ── _prune_empty_dirs ─────────────────────────────────────────────────────────

def test_prune_empty_dirs_removes_empty_subdir(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    _prune_empty_dirs(tmp_path)
    assert not sub.exists()


def test_prune_empty_dirs_removes_dir_with_only_ds_store(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / ".DS_Store").write_bytes(b"")
    _prune_empty_dirs(tmp_path)
    assert not sub.exists()


def test_prune_empty_dirs_keeps_dir_with_real_file(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "doc.pdf").write_bytes(b"")
    _prune_empty_dirs(tmp_path)
    assert sub.exists()


# ── _page_label ───────────────────────────────────────────────────────────────

def test_page_label_pdf_plural(tmp_path):
    assert _page_label(tmp_path / "doc.pdf", 3) == "3 pages"

def test_page_label_pdf_singular(tmp_path):
    assert _page_label(tmp_path / "doc.pdf", 1) == "1 page"

def test_page_label_xlsx_sheets(tmp_path):
    assert _page_label(tmp_path / "data.xlsx", 4) == "4 sheets"

def test_page_label_xlsx_singular(tmp_path):
    assert _page_label(tmp_path / "data.xlsx", 1) == "1 sheet"

def test_page_label_image_omitted(tmp_path):
    assert _page_label(tmp_path / "scan.jpg", 1) == ""

def test_page_label_txt_omitted(tmp_path):
    assert _page_label(tmp_path / "notes.txt", 5) == ""

def test_page_label_zero_omitted(tmp_path):
    assert _page_label(tmp_path / "doc.pdf", 0) == ""

def test_page_label_case_insensitive(tmp_path):
    assert _page_label(tmp_path / "DOC.PDF", 2) == "2 pages"


# ── _run_ingest_inner skipping ─────────────────────────────────────────────────

def _make_vault(tmp_path):
    vault    = tmp_path / "vault"
    incoming = vault / "_INCOMING"
    queue    = vault / ".watchdog" / "queue"
    staging  = vault / ".watchdog" / "staging"
    incoming.mkdir(parents=True)
    queue.mkdir(parents=True)
    staging.mkdir(parents=True)
    return vault, incoming, queue, staging


def test_empty_doc_moves_to_skipped(tmp_path, monkeypatch):
    vault, incoming, queue, staging = _make_vault(tmp_path)
    f = incoming / "photo.jpg"
    f.write_bytes(b"")

    monkeypatch.setattr(ppb, "preprocess_one", lambda path, *a, **kw: {
        "sha256": "abc123", "pages": [], "char_count": 0, "source_path": str(path)
    })

    _run_ingest_inner(vault, incoming, queue, staging, workers=1, chunk_workers=None, files=[f])

    assert (incoming / "_SKIPPED" / "photo.jpg").exists()
    assert not f.exists()
    assert list(queue.glob("*.json")) == []


def test_empty_doc_not_written_to_queue(tmp_path, monkeypatch):
    vault, incoming, queue, staging = _make_vault(tmp_path)
    f = incoming / "scan.png"
    f.write_bytes(b"")

    monkeypatch.setattr(ppb, "preprocess_one", lambda path, *a, **kw: {
        "sha256": "deadbeef", "pages": [], "char_count": 0, "source_path": str(path)
    })

    _run_ingest_inner(vault, incoming, queue, staging, workers=1, chunk_workers=None, files=[f])

    assert not (queue / "deadbeef.json").exists()


def test_nonempty_doc_still_queued(tmp_path, monkeypatch):
    vault, incoming, queue, staging = _make_vault(tmp_path)
    f = incoming / "report.pdf"
    f.write_bytes(b"")

    monkeypatch.setattr(ppb, "preprocess_one", lambda path, *a, **kw: {
        "sha256": "aabbcc", "pages": [{"markdown": "hello"}], "char_count": 5,
        "source_path": str(path)
    })

    _run_ingest_inner(vault, incoming, queue, staging, workers=1, chunk_workers=None, files=[f])

    assert (queue / "aabbcc.json").exists()
    assert not (incoming / "_SKIPPED").exists()


def test_summary_includes_skipped_count(tmp_path, monkeypatch, capsys):
    vault, incoming, queue, staging = _make_vault(tmp_path)
    f = incoming / "image.jpg"
    f.write_bytes(b"")

    monkeypatch.setattr(ppb, "preprocess_one", lambda path, *a, **kw: {
        "sha256": "abc", "pages": [], "char_count": 0, "source_path": str(path)
    })

    _run_ingest_inner(vault, incoming, queue, staging, workers=1, chunk_workers=None, files=[f])

    out = capsys.readouterr().out
    assert "skipped" in out


def test_garbled_doc_shows_annotation(tmp_path, monkeypatch, capsys):
    vault, incoming, queue, staging = _make_vault(tmp_path)
    f = incoming / "scan.pdf"
    f.write_bytes(b"")

    monkeypatch.setattr(ppb, "preprocess_one", lambda path, *a, **kw: {
        "sha256": "gg99", "pages": [{"markdown": "hello"}], "char_count": 5,
        "page_count": 1, "source_path": str(path),
        "metadata": {"garbled_detected": True},
    })

    _run_ingest_inner(vault, incoming, queue, staging, workers=1, chunk_workers=None, files=[f])

    out = capsys.readouterr().out
    assert "garbled" in out
    assert (queue / "gg99.json").exists()  # still queued


def test_garbled_doc_still_queued(tmp_path, monkeypatch, capsys):
    vault, incoming, queue, staging = _make_vault(tmp_path)
    f = incoming / "scan.pdf"
    f.write_bytes(b"")

    monkeypatch.setattr(ppb, "preprocess_one", lambda path, *a, **kw: {
        "sha256": "gg00", "pages": [{"markdown": "hello"}], "char_count": 5,
        "page_count": 2, "source_path": str(path),
        "metadata": {"garbled_detected": True},
    })

    _run_ingest_inner(vault, incoming, queue, staging, workers=1, chunk_workers=None, files=[f])

    assert (queue / "gg00.json").exists()
    assert not (incoming / "_SKIPPED").exists()
    assert not (incoming / "_FAILED").exists()


def test_skipped_subdir_excluded_from_find_files(tmp_path):
    incoming = tmp_path
    (incoming / "_SKIPPED").mkdir()
    (incoming / "_SKIPPED" / "photo.jpg").write_bytes(b"")
    (incoming / "doc.pdf").write_bytes(b"")
    result = find_files([str(tmp_path)])
    names = [f.name for f in result]
    assert "doc.pdf" in names
    assert "photo.jpg" not in names
