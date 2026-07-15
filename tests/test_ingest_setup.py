import json
import time
from datetime import datetime, timezone
from pathlib import Path


from watchdog.pipeline.ingest_setup import STALE_SECONDS, cost_estimate, run, scan_queue


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / ".watchdog" / "registry").mkdir(parents=True)
    (vault / ".watchdog" / "queue").mkdir(parents=True)
    return vault


def _write_queue_file(vault: Path, sha256: str, source_type: str = "docling", filename: str = "") -> None:
    qf = vault / ".watchdog" / "queue" / f"{sha256}.json"
    qf.write_text(json.dumps({"filename": filename or f"{sha256}.pdf", "metadata": {"source_type": source_type}, "pages": []}))


def _write_usage_file(vault: Path, ts: str, input_tokens: int, cost_usd) -> None:
    reg = vault / ".watchdog" / "registry"
    reg.mkdir(parents=True, exist_ok=True)
    (reg / f"usage-{ts}.json").write_text(json.dumps({
        "calls": [],
        "totals": {"input_tokens": input_tokens, "output_tokens": 0,
                   "cache_read_tokens": 0, "cache_write_tokens": 0, "cost_usd": cost_usd},
    }))


def test_empty_queue_returns_total_zero(tmp_path):
    vault = _make_vault(tmp_path)
    result = run(vault)
    assert result["total"] == 0
    assert result["lock_acquired"] is False
    assert not (vault / ".watchdog" / "ingest-state.json").exists()


def test_force_lock_acquires_lock_despite_empty_queue(tmp_path):
    """#214: a pending claude-batch collection needs mutual exclusion even when nothing new
    is queued — two concurrent `watchdog ingest` invocations must not both try to collect it."""
    vault = _make_vault(tmp_path)
    result = run(vault, force_lock=True)
    assert result["total"] == 0
    assert result["lock_acquired"] is True
    lock_file = vault / ".watchdog" / "registry" / ".ingest-lock"
    assert lock_file.exists()


def test_force_lock_still_blocked_by_a_fresh_existing_lock(tmp_path):
    vault = _make_vault(tmp_path)
    lock_file = vault / ".watchdog" / "registry" / ".ingest-lock"
    lock_file.write_text(f"pid: cli\nstarted_at: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n")
    result = run(vault, force_lock=True)
    assert "error" in result
    assert "already running" in result["error"]


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

    lock_file = vault / ".watchdog" / "registry" / ".ingest-lock"
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
    lock_file = vault / ".watchdog" / "registry" / ".ingest-lock"
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
    lock_file = vault / ".watchdog" / "registry" / ".ingest-lock"
    lock_file.write_text(f"pid: old\nstarted_at: {stale_ts}\n")

    result = run(vault)

    assert result["lock_acquired"] is True
    assert "pid: cli" in lock_file.read_text()


def test_malformed_lock_is_refused_not_deleted(tmp_path):
    """A lock with no parseable started_at is of unknown age. The old check-then-unlink deleted
    it regardless and proceeded; now ingest refuses and leaves it for `watchdog unlock` (#257)."""
    vault = _make_vault(tmp_path)
    _write_queue_file(vault, "abc123")
    lock_file = vault / ".watchdog" / "registry" / ".ingest-lock"
    lock_file.write_text("pid: mystery\n")   # no started_at line

    result = run(vault)

    assert "error" in result
    assert "already running" in result["error"]
    assert lock_file.read_text() == "pid: mystery\n"   # preserved, not clobbered


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


def test_scan_queue_empty_returns_empty_list(tmp_path):
    vault = _make_vault(tmp_path)
    assert scan_queue(vault) == []


def test_scan_queue_missing_dir_returns_empty_list(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    assert scan_queue(vault) == []


def test_cost_estimate_empty_queue(tmp_path):
    vault = _make_vault(tmp_path)
    est = cost_estimate(vault, scan_queue(vault), backend="claude-api")
    assert est == {"documents": 0, "pages": 0, "est_tokens": 0,
                    "cost_low": None, "cost_high": None, "runs_used": 0}


def test_cost_estimate_no_usage_history_omits_cost(tmp_path):
    """First run (#269): no usage-*.json yet, so the estimate is tokens only — no invented
    dollar figure."""
    vault = _make_vault(tmp_path)
    qf = vault / ".watchdog" / "queue" / "abc123.json"
    qf.write_text(json.dumps({
        "filename": "x.pdf", "page_count": 3,
        "pages": [{"page": 1, "markdown": "a" * 400}],
    }))

    est = cost_estimate(vault, scan_queue(vault), backend="claude-api")

    assert est["documents"] == 1
    assert est["pages"] == 3
    assert est["est_tokens"] == 100
    assert est["cost_low"] is None
    assert est["cost_high"] is None
    assert est["runs_used"] == 0


def test_cost_estimate_subscription_backend_never_shows_cost(tmp_path):
    """Subscription auth (claude-agent-sdk) gets no dollar figure even with usage history —
    there's no real billing to project, only a session-limit fraction (#269)."""
    vault = _make_vault(tmp_path)
    qf = vault / ".watchdog" / "queue" / "abc123.json"
    qf.write_text(json.dumps({"filename": "x.pdf", "pages": [{"page": 1, "markdown": "a" * 400}]}))
    _write_usage_file(vault, "20260101T000000Z", input_tokens=1000, cost_usd=1.0)

    est = cost_estimate(vault, scan_queue(vault), backend="claude-agent-sdk")

    assert est["cost_low"] is None
    assert est["cost_high"] is None
    assert est["runs_used"] == 0


def test_cost_estimate_derives_range_from_usage_history(tmp_path):
    vault = _make_vault(tmp_path)
    qf = vault / ".watchdog" / "queue" / "abc123.json"
    qf.write_text(json.dumps({"filename": "x.pdf", "pages": [{"page": 1, "markdown": "a" * 4000}]}))
    # est_tokens = 1000. Two historical runs at $0.001/token and $0.002/token.
    _write_usage_file(vault, "20260101T000000Z", input_tokens=1000, cost_usd=1.0)
    _write_usage_file(vault, "20260102T000000Z", input_tokens=1000, cost_usd=2.0)

    est = cost_estimate(vault, scan_queue(vault), backend="claude-api")

    assert est["est_tokens"] == 1000
    assert est["cost_low"] == 1.0
    assert est["cost_high"] == 2.0
    assert est["runs_used"] == 2


def test_cost_estimate_only_uses_last_n_runs(tmp_path):
    vault = _make_vault(tmp_path)
    qf = vault / ".watchdog" / "queue" / "abc123.json"
    qf.write_text(json.dumps({"filename": "x.pdf", "pages": [{"page": 1, "markdown": "a" * 4000}]}))
    # Oldest run has a wildly different ratio and should be dropped once more than max_runs exist.
    _write_usage_file(vault, "20260101T000000Z", input_tokens=1000, cost_usd=100.0)
    _write_usage_file(vault, "20260102T000000Z", input_tokens=1000, cost_usd=1.0)
    _write_usage_file(vault, "20260103T000000Z", input_tokens=1000, cost_usd=1.0)
    _write_usage_file(vault, "20260104T000000Z", input_tokens=1000, cost_usd=1.0)

    est = cost_estimate(vault, scan_queue(vault), backend="claude-api", max_runs=3)

    assert est["runs_used"] == 3
    assert est["cost_low"] == est["cost_high"] == 1.0  # the $100 outlier was dropped


def test_cost_estimate_skips_usage_files_with_no_cost(tmp_path):
    """Subscription-mode usage files (no real cost_usd) shouldn't poison a metered-key ratio."""
    vault = _make_vault(tmp_path)
    qf = vault / ".watchdog" / "queue" / "abc123.json"
    qf.write_text(json.dumps({"filename": "x.pdf", "pages": [{"page": 1, "markdown": "a" * 4000}]}))
    _write_usage_file(vault, "20260101T000000Z", input_tokens=1000, cost_usd=None)
    _write_usage_file(vault, "20260102T000000Z", input_tokens=1000, cost_usd=1.0)

    est = cost_estimate(vault, scan_queue(vault), backend="claude-api")

    assert est["runs_used"] == 1
    assert est["cost_low"] == est["cost_high"] == 1.0
