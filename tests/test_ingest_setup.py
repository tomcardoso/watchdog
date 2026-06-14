import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from watchdog.pipeline.ingest_setup import STALE_SECONDS, run


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / ".watchdog" / "Registry").mkdir(parents=True)
    (vault / ".watchdog" / "queue").mkdir(parents=True)
    return vault


def _write_queue_file(vault: Path, sha256: str, source_type: str = "docling", filename: str = "") -> None:
    qf = vault / ".watchdog" / "queue" / f"{sha256}.json"
    qf.write_text(json.dumps({"filename": filename or f"{sha256}.pdf", "metadata": {"source_type": source_type}, "pages": []}))


def test_empty_queue_returns_total_zero(tmp_path):
    vault = _make_vault(tmp_path)
    result = run(vault)
    assert result["total"] == 0
    assert result["lock_acquired"] is False
    assert not (vault / ".watchdog" / "ingest-state.json").exists()


def test_queued_files_acquires_lock_and_writes_state(tmp_path):
    vault = _make_vault(tmp_path)
    _write_queue_file(vault, "abc123")
    _write_queue_file(vault, "def456")

    result = run(vault)

    assert result["total"] == 2
    assert result["lock_acquired"] is True
    assert len(result["queue_files"]) == 2
    assert "batch_start" in result
    assert "started_at" in result

    state_file = vault / ".watchdog" / "ingest-state.json"
    assert state_file.exists()
    assert json.loads(state_file.read_text()) == result

    lock_file = vault / ".watchdog" / "Registry" / ".ingest-lock"
    assert lock_file.exists()
    assert "pid: cli" in lock_file.read_text()


def test_queue_file_paths_are_vault_relative(tmp_path):
    vault = _make_vault(tmp_path)
    _write_queue_file(vault, "abc123")

    result = run(vault)

    path = result["queue_files"][0]["path"]
    assert not Path(path).is_absolute()
    assert path.startswith(".watchdog/queue/")


def test_queue_files_include_filename(tmp_path):
    vault = _make_vault(tmp_path)
    _write_queue_file(vault, "abc123", filename="Annual Report 2024.pdf")

    result = run(vault)

    assert result["queue_files"][0]["filename"] == "Annual Report 2024.pdf"


def test_queue_files_include_document_type(tmp_path):
    vault = _make_vault(tmp_path)
    qf = vault / ".watchdog" / "queue" / "abc123.json"
    qf.write_text(json.dumps({
        "filename": "affidavit.pdf",
        "metadata": {"source_type": "docling"},
        "document_type": "court-documents",
        "pages": [],
    }))

    result = run(vault)

    assert result["queue_files"][0]["document_type"] == "court-documents"


def test_queue_files_document_type_none_when_absent(tmp_path):
    vault = _make_vault(tmp_path)
    _write_queue_file(vault, "abc123")

    result = run(vault)

    assert result["queue_files"][0]["document_type"] is None


def test_queue_files_filename_falls_back_to_sha256(tmp_path):
    vault = _make_vault(tmp_path)
    qf = vault / ".watchdog" / "queue" / "abc123.json"
    qf.write_text(json.dumps({"metadata": {"source_type": "docling"}, "pages": []}))

    result = run(vault)

    assert result["queue_files"][0]["filename"] == "abc123"


def test_fresh_lock_blocks_ingest(tmp_path):
    vault = _make_vault(tmp_path)
    lock_file = vault / ".watchdog" / "Registry" / ".ingest-lock"
    lock_file.write_text(f"pid: cli\nstarted_at: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n")

    result = run(vault)

    assert "error" in result
    assert "already running" in result["error"]


def test_stale_lock_is_replaced(tmp_path):
    vault = _make_vault(tmp_path)
    _write_queue_file(vault, "abc123")

    stale_ts = datetime.fromtimestamp(
        time.time() - STALE_SECONDS - 60, tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    lock_file = vault / ".watchdog" / "Registry" / ".ingest-lock"
    lock_file.write_text(f"pid: old\nstarted_at: {stale_ts}\n")

    result = run(vault)

    assert result["lock_acquired"] is True
    assert "pid: cli" in lock_file.read_text()


def test_extractor_model_written_to_state(tmp_path):
    vault = _make_vault(tmp_path)
    _write_queue_file(vault, "abc123")

    result = run(vault, extractor_model="haiku")

    assert result["extractor_model"] == "haiku"
    state = json.loads((vault / ".watchdog" / "ingest-state.json").read_text())
    assert state["extractor_model"] == "haiku"


def test_extractor_model_defaults_to_sonnet(tmp_path):
    vault = _make_vault(tmp_path)
    _write_queue_file(vault, "abc123")

    result = run(vault)

    assert result["extractor_model"] == "sonnet"


def test_finalizer_model_written_to_state(tmp_path):
    vault = _make_vault(tmp_path)
    _write_queue_file(vault, "abc123")

    result = run(vault, finalizer_model="opus")

    assert result["finalizer_model"] == "opus"
    state = json.loads((vault / ".watchdog" / "ingest-state.json").read_text())
    assert state["finalizer_model"] == "opus"


def test_finalizer_model_defaults_to_sonnet(tmp_path):
    vault = _make_vault(tmp_path)
    _write_queue_file(vault, "abc123")

    result = run(vault)

    assert result["finalizer_model"] == "sonnet"


def test_queue_files_include_page_count(tmp_path):
    vault = _make_vault(tmp_path)
    qf = vault / ".watchdog" / "queue" / "abc123.json"
    qf.write_text(json.dumps({
        "filename": "big.pdf", "metadata": {"source_type": "docling"},
        "page_count": 312, "pages": [],
    }))

    result = run(vault)

    assert result["queue_files"][0]["page_count"] == 312


def test_queue_files_page_count_falls_back_to_len_pages(tmp_path):
    vault = _make_vault(tmp_path)
    qf = vault / ".watchdog" / "queue" / "abc123.json"
    qf.write_text(json.dumps({
        "filename": "x.pdf", "metadata": {"source_type": "docling"},
        "pages": [{"page": 1, "markdown": ""}, {"page": 2, "markdown": ""}],
    }))

    result = run(vault)

    assert result["queue_files"][0]["page_count"] == 2


def test_state_includes_section_token_threshold(tmp_path):
    vault = _make_vault(tmp_path)
    _write_queue_file(vault, "abc123")

    result = run(vault)

    assert isinstance(result["section_token_threshold"], int)


def test_queue_files_include_est_tokens(tmp_path):
    vault = _make_vault(tmp_path)
    qf = vault / ".watchdog" / "queue" / "abc123.json"
    qf.write_text(json.dumps({
        "filename": "x.pdf", "metadata": {"source_type": "docling"},
        "pages": [{"page": 1, "markdown": "a" * 400}, {"page": 2, "markdown": "a" * 400}],
    }))

    result = run(vault)

    assert result["queue_files"][0]["est_tokens"] == 200  # 800 chars / 4


def test_empty_queue_cleans_up_stale_state_file(tmp_path):
    vault = _make_vault(tmp_path)
    state_file = vault / ".watchdog" / "ingest-state.json"
    state_file.write_text('{"stale": true}')

    result = run(vault)

    assert result["total"] == 0
    assert not state_file.exists()
