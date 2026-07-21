"""Tests for preprocess_batch helpers (find_files + preprocess_one paths)."""

import io
import json
import subprocess

import pytest

from watchdog.cmd.live import LiveRegion

from watchdog.pipeline.preprocess_batch import (
    find_files,
    preprocess_one,
    _count_pdf_pages,
    _adaptive_workers,
    _resolve_workers,
    _run_ingest_inner,
    run_ingest,
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


# ── chew mutual exclusion (#257) ──────────────────────────────────────────────

def test_chew_refuses_when_a_fresh_chew_lock_exists(tmp_path):
    """Two chews on one vault (e.g. `watchdog watch` + a manual `watchdog chew`) previously both
    ran, racing staging renames. A fresh .chew-lock now makes the second refuse."""
    import time
    vault = tmp_path / "vault"
    (vault / ".watchdog").mkdir(parents=True)
    (vault / "_INCOMING").mkdir()
    lock = vault / ".watchdog" / ".chew-lock"
    fresh = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    lock.write_text(f"started_at: {fresh}\npid: 999\n")

    with pytest.raises(SystemExit) as exc:
        run_ingest(vault)
    assert "already in progress" in str(exc.value)
    assert lock.read_text().startswith(f"started_at: {fresh}")   # incumbent untouched



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


# ── sidecar handling (D121) ─────────────────────────────────────────────────────

def test_sidecar_filtered_and_embedded_in_queue_json(tmp_path, monkeypatch):
    vault, incoming, queue, staging = _make_vault(tmp_path)
    f = incoming / "report.pdf"
    f.write_bytes(b"")
    (incoming / "report.pdf.yml").write_text(
        "source: https://sedar.com/x\nobtained: 2026-06-05\n", encoding="utf-8")

    monkeypatch.setattr(ppb, "preprocess_one", lambda path, *a, **kw: {
        "sha256": "aabbcc", "pages": [{"markdown": "hello"}], "char_count": 5,
        "source_path": str(path)
    })

    _run_ingest_inner(vault, incoming, queue, staging, workers=1, chunk_workers=None, files=[f])

    entry = json.loads((queue / "aabbcc.json").read_text())
    assert "source: https://sedar.com/x" in entry["sidecar"]
    assert not (incoming / "report.pdf.yml").exists()


def test_sidecar_absent_leaves_queue_field_none(tmp_path, monkeypatch):
    vault, incoming, queue, staging = _make_vault(tmp_path)
    f = incoming / "report.pdf"
    f.write_bytes(b"")

    monkeypatch.setattr(ppb, "preprocess_one", lambda path, *a, **kw: {
        "sha256": "aabbcc", "pages": [{"markdown": "hello"}], "char_count": 5,
        "source_path": str(path)
    })

    _run_ingest_inner(vault, incoming, queue, staging, workers=1, chunk_workers=None, files=[f])

    entry = json.loads((queue / "aabbcc.json").read_text())
    assert entry["sidecar"] is None


def test_sidecar_unknown_field_dropped_and_warned(tmp_path, monkeypatch, capsys):
    vault, incoming, queue, staging = _make_vault(tmp_path)
    f = incoming / "report.pdf"
    f.write_bytes(b"")
    (incoming / "report.pdf.yml").write_text(
        "source: https://sedar.com/x\nweird_field: nope\n", encoding="utf-8")

    monkeypatch.setattr(ppb, "preprocess_one", lambda path, *a, **kw: {
        "sha256": "aabbcc", "pages": [{"markdown": "hello"}], "char_count": 5,
        "source_path": str(path)
    })

    _run_ingest_inner(vault, incoming, queue, staging, workers=1, chunk_workers=None, files=[f])

    entry = json.loads((queue / "aabbcc.json").read_text())
    assert "weird_field" not in entry["sidecar"]
    assert "source: https://sedar.com/x" in entry["sidecar"]
    assert "weird_field" in capsys.readouterr().out


def test_sidecar_moved_to_failed_alongside_source(tmp_path, monkeypatch):
    vault, incoming, queue, staging = _make_vault(tmp_path)
    f = incoming / "broken.pdf"
    f.write_bytes(b"")
    (incoming / "broken.pdf.yml").write_text("source: https://x\n", encoding="utf-8")

    monkeypatch.setattr(ppb, "preprocess_one", lambda path, *a, **kw: {
        "error": "boom", "source_path": str(path)
    })

    _run_ingest_inner(vault, incoming, queue, staging, workers=1, chunk_workers=None, files=[f])

    assert (incoming / "_FAILED" / "broken.pdf.yml").exists()
    assert not (incoming / "broken.pdf.yml").exists()


def test_sidecar_moved_to_skipped_alongside_source(tmp_path, monkeypatch):
    vault, incoming, queue, staging = _make_vault(tmp_path)
    f = incoming / "blank.jpg"
    f.write_bytes(b"")
    (incoming / "blank.jpg.yml").write_text("source: https://x\n", encoding="utf-8")

    monkeypatch.setattr(ppb, "preprocess_one", lambda path, *a, **kw: {
        "sha256": "deadbeef", "pages": [], "char_count": 0, "source_path": str(path)
    })

    _run_ingest_inner(vault, incoming, queue, staging, workers=1, chunk_workers=None, files=[f])

    assert (incoming / "_SKIPPED" / "blank.jpg.yml").exists()
    assert not (incoming / "blank.jpg.yml").exists()


# ── near-dup self-match guard (#424) ────────────────────────────────────────────
#
# Re-chewing a committed document's own morgue original for `--force <selector>` compares its
# text against `registry/documents.json`, which still holds that same document's own minhash —
# without excluding it, the re-queued document would always "near-duplicate" itself at ~1.0
# similarity, since it IS itself.

def test_compute_near_dup_excludes_forced_self_match(tmp_path):
    from watchdog.pipeline.near_dup import shingles_from_text, minhash
    from watchdog.pipeline.preprocess_batch import _compute_near_dup

    vault = tmp_path / "vault"
    (vault / ".watchdog" / "registry").mkdir(parents=True)
    text = ("Acme Corp filed an annual report disclosing significant revenue "
            "for the fiscal year ending in December.")
    mh = minhash(shingles_from_text(text))
    (vault / ".watchdog" / "registry" / "documents.json").write_text(json.dumps({
        "self-sha": {"filename": "alpha.pdf", "minhash": mh, "document_note": "documents/alpha"},
    }))
    result = {"pages": [{"markdown": text}]}

    without_exclude = _compute_near_dup(result, vault)
    assert without_exclude["top_similarity"] >= 0.85
    assert any(m["sha256"] == "self-sha" for m in without_exclude["near_duplicates"])

    with_exclude = _compute_near_dup(result, vault, exclude_sha="self-sha")
    assert with_exclude["near_duplicates"] == []
    assert with_exclude["top_similarity"] == 0.0


# ── live status region (#158) ───────────────────────────────────────────────────

def test_tty_run_shows_inflight_row_and_progress(tmp_path, monkeypatch):
    """On a TTY the batch draws an in-flight 'chewing…' row per file plus a progress/ETA row,
    and the file's result scrolls above when it settles."""
    vault, incoming, queue, staging = _make_vault(tmp_path)
    f = incoming / "report.pdf"
    f.write_bytes(b"")

    monkeypatch.setattr(ppb, "preprocess_one", lambda path, *a, **kw: {
        "sha256": "feed01", "pages": [{"markdown": "hi"}], "char_count": 2,
        "page_count": 1, "source_path": str(path),
    })

    buf = io.StringIO()
    # Inject a TTY-enabled region over a capture buffer (the test process is not a real TTY).
    monkeypatch.setattr(ppb, "LiveRegion", lambda *a, **kw: LiveRegion(buf, enabled=True))

    _run_ingest_inner(vault, incoming, queue, staging, workers=1, chunk_workers=None, files=[f])

    out = buf.getvalue()
    assert "chewing…" in out              # in-flight row appeared while the worker ran
    assert "report.pdf" in out            # settled result line scrolled above
    assert "1/1" in out                   # progress row rendered
    assert "\x1b[" in out                 # cursor escapes used (TTY path, not append-only)
    assert (queue / "feed01.json").exists()


def test_skipped_subdir_excluded_from_find_files(tmp_path):
    incoming = tmp_path
    (incoming / "_SKIPPED").mkdir()
    (incoming / "_SKIPPED" / "photo.jpg").write_bytes(b"")
    (incoming / "doc.pdf").write_bytes(b"")
    result = find_files([str(tmp_path)])
    names = [f.name for f in result]
    assert "doc.pdf" in names
    assert "photo.jpg" not in names
