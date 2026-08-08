import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest


from watchdog.pipeline.ingest_setup import (
    STALE_SECONDS, cost_estimate, cost_estimate_all_models, finalize_cost_estimate,
    finalize_cost_estimate_all_models, run, scan_queue,
)


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / ".watchdog" / "registry").mkdir(parents=True)
    (vault / ".watchdog" / "queue").mkdir(parents=True)
    return vault


def _write_queue_file(vault: Path, sha256: str, source_type: str = "docling", filename: str = "") -> None:
    qf = vault / ".watchdog" / "queue" / f"{sha256}.json"
    qf.write_text(json.dumps({"filename": filename or f"{sha256}.pdf", "metadata": {"source_type": source_type}, "pages": []}))


def _write_usage_file(vault: Path, ts: str, input_tokens: int, cost_usd,
                      est_input_tokens: int | None = None, calls: list | None = None,
                      cache_read_tokens: int = 0, cache_write_tokens: int = 0,
                      output_tokens: int = 0) -> None:
    reg = vault / ".watchdog" / "registry"
    reg.mkdir(parents=True, exist_ok=True)
    totals = {"input_tokens": input_tokens, "output_tokens": output_tokens,
              "cache_read_tokens": cache_read_tokens, "cache_write_tokens": cache_write_tokens,
              "cost_usd": cost_usd}
    if est_input_tokens is not None:
        totals["est_input_tokens"] = est_input_tokens
    (reg / f"usage-{ts}.json").write_text(json.dumps({
        "calls": calls if calls is not None else [], "totals": totals,
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


def test_cost_estimate_usage_files_param_overrides_vault_history(tmp_path):
    """issue #478: a caller (the benchmark harness) can supply usage files from elsewhere in
    place of this vault's own history — needed because every benchmark arm vault is fresh by
    design and so never has usage history of its own to derive a ratio from."""
    vault = _make_vault(tmp_path)
    qf = vault / ".watchdog" / "queue" / "abc123.json"
    qf.write_text(json.dumps({"filename": "x.pdf", "pages": [{"page": 1, "markdown": "a" * 4000}]}))
    _write_usage_file(vault, "20260101T000000Z", input_tokens=1000, cost_usd=100.0)  # ignored

    other = tmp_path / "elsewhere"
    other.mkdir()
    uf = other / "usage-20260102T000000Z.json"
    uf.write_text(json.dumps({"calls": [], "totals": {
        "input_tokens": 1000, "output_tokens": 0, "cache_read_tokens": 0,
        "cache_write_tokens": 0, "cost_usd": 1.5,
    }}))

    est = cost_estimate(vault, scan_queue(vault), backend="claude-api", usage_files=[uf])

    assert est["cost_low"] == est["cost_high"] == 1.5
    assert est["runs_used"] == 1


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


# ── tokens-in calibration (#417) ────────────────────────────────────────────────

def test_cost_estimate_uncalibrated_without_est_input_tokens_history(tmp_path):
    """No usage file yet carries `est_input_tokens` (either no history, or every past run was a
    standalone finalize) — the raw chars/4 heuristic is used unchanged, matching the pre-#417
    behaviour exactly."""
    vault = _make_vault(tmp_path)
    qf = vault / ".watchdog" / "queue" / "abc123.json"
    qf.write_text(json.dumps({"filename": "x.pdf", "pages": [{"page": 1, "markdown": "a" * 4000}]}))
    _write_usage_file(vault, "20260101T000000Z", input_tokens=1000, cost_usd=1.0)   # no est_input_tokens

    est = cost_estimate(vault, scan_queue(vault), backend="claude-api")

    assert est["est_tokens"] == 1000   # raw chars/4, uncalibrated


def test_cost_estimate_applies_empirical_calibration(tmp_path):
    """Real extraction consistently ran 50% over the naive chars/4 estimate in this vault's
    history — the displayed 'tokens in' (and, downstream, the dollar range) scales accordingly."""
    vault = _make_vault(tmp_path)
    qf = vault / ".watchdog" / "queue" / "abc123.json"
    qf.write_text(json.dumps({"filename": "x.pdf", "pages": [{"page": 1, "markdown": "a" * 4000}]}))
    # est_tokens=1000 naive; actual consumed 1500 → ratio 1.5, consistent across both runs.
    _write_usage_file(vault, "20260101T000000Z", input_tokens=1500, cost_usd=1.5, est_input_tokens=1000)
    _write_usage_file(vault, "20260102T000000Z", input_tokens=3000, cost_usd=3.0, est_input_tokens=2000)

    est = cost_estimate(vault, scan_queue(vault), backend="claude-api")

    assert est["est_tokens"] == 1500   # 1000 (raw) * 1.5 (calibration)
    # The dollar range is derived from the *calibrated* tokens, so it also reflects the correction.
    assert est["cost_low"] == est["cost_high"] == 1.5


def test_cost_estimate_calibration_counts_cache_tokens_as_real_input(tmp_path):
    """Prompt caching (#470) moves most of a call's real input volume into
    `cache_read_tokens`/`cache_write_tokens`, leaving bare `input_tokens` in the single digits even
    though the model processed the document's full content — a sectioned extraction with a shared,
    growing prefix across calls is exactly this shape. Calibrating against `input_tokens` alone
    used to produce a near-zero ratio and an absurdly undercounted 'tokens in' display; it must
    fold cache reads/writes into the real-input figure instead."""
    vault = _make_vault(tmp_path)
    qf = vault / ".watchdog" / "queue" / "abc123.json"
    qf.write_text(json.dumps({"filename": "x.pdf", "pages": [{"page": 1, "markdown": "a" * 4000}]}))
    # est_input_tokens=1000 naive; almost nothing counted as bare input_tokens, but cache
    # read+write together account for the document's real ~1500-token volume → ratio 1.5.
    _write_usage_file(vault, "20260101T000000Z", input_tokens=1, cost_usd=1.5, est_input_tokens=1000,
                      cache_read_tokens=500, cache_write_tokens=999)

    est = cost_estimate(vault, scan_queue(vault), backend="claude-api")

    assert est["est_tokens"] == 1500   # 1000 (raw) * 1.5 (calibration from input+cache tokens)


def test_cost_estimate_calibration_ignores_standalone_finalize_runs(tmp_path):
    """A usage file from a standalone `watchdog finalize` never carries `est_input_tokens`
    (nothing was extracted) — it must not be mistaken for an extraction data point."""
    vault = _make_vault(tmp_path)
    qf = vault / ".watchdog" / "queue" / "abc123.json"
    qf.write_text(json.dumps({"filename": "x.pdf", "pages": [{"page": 1, "markdown": "a" * 4000}]}))
    _write_usage_file(vault, "20260101T000000Z", input_tokens=1500, cost_usd=1.5, est_input_tokens=1000)
    _write_usage_file(vault, "20260102T000000Z", input_tokens=200, cost_usd=0.05)   # standalone finalize

    est = cost_estimate(vault, scan_queue(vault), backend="claude-api")

    assert est["est_tokens"] == 1500   # only the extraction run's 1.5x ratio applied


def test_cost_estimate_calibration_still_applies_under_subscription_auth(tmp_path):
    """Subscription auth withholds the dollar figure (D72), not the calibrated token count — a
    subscriber still budgets a session window against tokens."""
    vault = _make_vault(tmp_path)
    qf = vault / ".watchdog" / "queue" / "abc123.json"
    qf.write_text(json.dumps({"filename": "x.pdf", "pages": [{"page": 1, "markdown": "a" * 4000}]}))
    _write_usage_file(vault, "20260101T000000Z", input_tokens=1500, cost_usd=1.5, est_input_tokens=1000)

    est = cost_estimate(vault, scan_queue(vault), backend="claude-agent-sdk")

    assert est["est_tokens"] == 1500
    assert est["cost_low"] is None and est["cost_high"] is None


# ── finalize --estimate (#417) ──────────────────────────────────────────────────

def _stage_finalize_corpus(vault: Path, docs: dict[str, str]) -> None:
    """Write `result_<sha>.json` (with `docs[sha]` as filler text) + a matching `notes_<sha>.md`
    for each sha — the staged tmp/ corpus `finalize_cost_estimate` reads."""
    tmp = vault / ".watchdog" / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    for sha, text in docs.items():
        (tmp / f"result_{sha}.json").write_text(json.dumps({"sha256": sha, "key_facts": text}))
        (tmp / f"notes_{sha}.md").write_text(text)


def test_finalize_cost_estimate_empty_tmp_returns_zero(tmp_path):
    vault = _make_vault(tmp_path)
    est = finalize_cost_estimate(vault, backend="claude-api")
    assert est == {"docs": 0, "est_tokens": 0, "cost_low": None, "cost_high": None, "runs_used": 0}


def test_finalize_cost_estimate_no_standalone_history_omits_cost(tmp_path):
    """A staged batch with no prior *standalone* finalize to price against gets tokens only —
    same 'not enough history yet' treatment `cost_estimate` gives a first-run vault."""
    vault = _make_vault(tmp_path)
    _stage_finalize_corpus(vault, {"sha1": "a" * 400})

    est = finalize_cost_estimate(vault, backend="claude-api")

    assert est["docs"] == 1
    assert est["est_tokens"] > 0
    assert est["cost_low"] is None and est["runs_used"] == 0


def test_finalize_cost_estimate_derives_range_from_standalone_finalize_history(tmp_path):
    vault = _make_vault(tmp_path)
    _stage_finalize_corpus(vault, {"sha1": "a" * 4000})   # result_sha1.json (4035 chars) + notes (4000) → 2008 est_tokens
    finalize_calls = [{"task": "reconcile"}, {"task": "entity-synthesis"}]
    _write_usage_file(vault, "20260101T000000Z", input_tokens=1000, cost_usd=1.0, calls=finalize_calls)
    _write_usage_file(vault, "20260102T000000Z", input_tokens=1000, cost_usd=2.0, calls=finalize_calls)

    est = finalize_cost_estimate(vault, backend="claude-api")

    assert est["docs"] == 1
    assert est["est_tokens"] == 2008
    assert est["cost_low"] == 2.008
    assert est["cost_high"] == 4.016
    assert est["runs_used"] == 2


def test_finalize_cost_estimate_excludes_runs_with_extraction_calls(tmp_path):
    """A `run()` ingest's own finalize tail shares its usage file with extraction — that file
    must not be mistaken for a standalone finalize's $/token profile."""
    vault = _make_vault(tmp_path)
    _stage_finalize_corpus(vault, {"sha1": "a" * 4000})
    mixed_calls = [{"task": "extract"}, {"task": "reconcile"}]
    standalone_calls = [{"task": "reconcile"}, {"task": "briefing"}]
    _write_usage_file(vault, "20260101T000000Z", input_tokens=999999, cost_usd=999.0, calls=mixed_calls)
    _write_usage_file(vault, "20260102T000000Z", input_tokens=1000, cost_usd=1.0, calls=standalone_calls)

    est = finalize_cost_estimate(vault, backend="claude-api")

    assert est["runs_used"] == 1   # only the standalone run counted
    assert est["cost_low"] == est["cost_high"] == 2.008


def test_finalize_cost_estimate_usage_files_param_overrides_vault_history(tmp_path):
    """Same override as `cost_estimate`'s own (issue #478), for the standalone-`bark` path."""
    vault = _make_vault(tmp_path)
    _stage_finalize_corpus(vault, {"sha1": "a" * 4000})
    standalone_calls = [{"task": "reconcile"}]
    _write_usage_file(vault, "20260101T000000Z", input_tokens=999999, cost_usd=999.0,
                      calls=standalone_calls)  # ignored

    other = tmp_path / "elsewhere"
    other.mkdir()
    uf = other / "usage-20260102T000000Z.json"
    uf.write_text(json.dumps({"calls": standalone_calls, "totals": {
        "input_tokens": 1000, "output_tokens": 0, "cache_read_tokens": 0,
        "cache_write_tokens": 0, "cost_usd": 1.0,
    }}))

    est = finalize_cost_estimate(vault, backend="claude-api", usage_files=[uf])

    assert est["runs_used"] == 1
    assert est["cost_low"] == est["cost_high"] == 2.008


def test_finalize_cost_estimate_subscription_backend_never_shows_cost(tmp_path):
    vault = _make_vault(tmp_path)
    _stage_finalize_corpus(vault, {"sha1": "a" * 4000})
    standalone_calls = [{"task": "reconcile"}]
    _write_usage_file(vault, "20260101T000000Z", input_tokens=1000, cost_usd=1.0, calls=standalone_calls)

    est = finalize_cost_estimate(vault, backend="claude-agent-sdk")

    assert est["cost_low"] is None and est["cost_high"] is None and est["runs_used"] == 0


# ── cost projection across every catalog model (#469) ───────────────────────────

def test_cost_estimate_all_models_no_history_returns_empty(tmp_path):
    """No usage history yet means no output:input ratio to project output tokens from — no
    dollar figure is invented for any catalog model."""
    vault = _make_vault(tmp_path)
    assert cost_estimate_all_models(vault, est_tokens=1000) == []


def test_cost_estimate_all_models_projects_every_catalog_model(tmp_path):
    from watchdog.model_catalog import all_models
    vault = _make_vault(tmp_path)
    _write_usage_file(vault, "20260101T000000Z", input_tokens=1000, cost_usd=1.0, output_tokens=500)

    rows = cost_estimate_all_models(vault, est_tokens=2000)

    catalog = all_models()
    assert {r["id"] for r in rows} == {m["id"] for m in catalog}
    assert [r["cost"] for r in rows] == sorted(r["cost"] for r in rows)   # cheapest first
    # output ratio 500/1000 = 0.5 -> est_output = 1000; cost = 2000*input + 1000*output per model
    by_id = {r["id"]: r["cost"] for r in rows}
    haiku = next(m for m in catalog if m["id"] == "claude-haiku-4-5")
    assert by_id["claude-haiku-4-5"] == pytest.approx(2000 * haiku["input"] + 1000 * haiku["output"])


def test_cost_estimate_all_models_ignores_runs_with_no_output_tokens(tmp_path):
    """A run with zero recorded output tokens can't contribute a ratio — must be skipped, not
    treated as an output:input ratio of 0 (which would zero out every model's output cost)."""
    vault = _make_vault(tmp_path)
    _write_usage_file(vault, "20260101T000000Z", input_tokens=1000, cost_usd=1.0, output_tokens=0)
    _write_usage_file(vault, "20260102T000000Z", input_tokens=1000, cost_usd=1.0, output_tokens=1000)

    rows = cost_estimate_all_models(vault, est_tokens=1000)

    # only the second run's 1:1 ratio counts -> est_output = 1000
    by_id = {r["id"]: r["cost"] for r in rows}
    from watchdog.model_catalog import all_models
    haiku = next(m for m in all_models() if m["id"] == "claude-haiku-4-5")
    assert by_id["claude-haiku-4-5"] == pytest.approx(1000 * haiku["input"] + 1000 * haiku["output"])


def test_cost_estimate_all_models_scales_new_tokenizer_claude_models(tmp_path):
    # Sonnet 5 / Opus 4.8 (#574, new tokenizer) get their own tokenizer_ratio applied to the
    # projected input AND output tokens, so they're priced against their own higher real token
    # count for the same text rather than the old-tokenizer figure other catalog models use as-is.
    from watchdog.model_catalog import all_models
    vault = _make_vault(tmp_path)
    _write_usage_file(vault, "20260101T000000Z", input_tokens=1000, cost_usd=1.0, output_tokens=500)

    rows = cost_estimate_all_models(vault, est_tokens=2000)

    by_id = {r["id"]: r["cost"] for r in rows}
    catalog = {m["id"]: m for m in all_models()}
    sonnet5 = catalog["claude-sonnet-5"]
    scaled_in = 2000 * 1.3       # tokenizer_ratio
    scaled_out = scaled_in * 0.5  # output_ratio (500/1000) applied after scaling
    assert by_id["claude-sonnet-5"] == pytest.approx(
        scaled_in * sonnet5["input"] + scaled_out * sonnet5["output"])
    # Old-tokenizer Sonnet 4.6 is unaffected (ratio 1.0) — same figure as before this fix.
    sonnet46 = catalog["claude-sonnet-4-6"]
    assert by_id["claude-sonnet-4-6"] == pytest.approx(2000 * sonnet46["input"] + 1000 * sonnet46["output"])


def test_finalize_cost_estimate_all_models_no_standalone_history_returns_empty(tmp_path):
    """A usage file exists, but only from a mixed dig+bark run — not a standalone finalize —
    so there's still no ratio to project from, mirroring `finalize_cost_estimate`'s own gate."""
    vault = _make_vault(tmp_path)
    _write_usage_file(vault, "20260101T000000Z", input_tokens=1000, cost_usd=1.0, output_tokens=500,
                      calls=[{"task": "extract"}, {"task": "reconcile"}])

    assert finalize_cost_estimate_all_models(vault, est_tokens=1000) == []


def test_finalize_cost_estimate_all_models_uses_standalone_finalize_history(tmp_path):
    from watchdog.model_catalog import all_models
    vault = _make_vault(tmp_path)
    standalone_calls = [{"task": "reconcile"}, {"task": "briefing"}]
    _write_usage_file(vault, "20260101T000000Z", input_tokens=1000, cost_usd=1.0, output_tokens=500,
                      calls=standalone_calls)

    rows = finalize_cost_estimate_all_models(vault, est_tokens=2000)

    assert {r["id"] for r in rows} == {m["id"] for m in all_models()}
    assert [r["cost"] for r in rows] == sorted(r["cost"] for r in rows)
