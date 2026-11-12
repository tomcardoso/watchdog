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
    reg = vault / ".watchdog" / "registry" / "usage"
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
    assert not (vault / ".watchdog" / "processing-state.json").exists()


def test_force_lock_acquires_lock_despite_empty_queue(tmp_path):
    """#214: a pending claude-batch collection needs mutual exclusion even when nothing new
    is queued — two concurrent `watchdog ingest` invocations must not both try to collect it."""
    vault = _make_vault(tmp_path)
    result = run(vault, force_lock=True)
    assert result["total"] == 0
    assert result["lock_acquired"] is True
    lock_file = vault / ".watchdog" / "registry" / ".processing-lock"
    assert lock_file.exists()


def test_force_lock_still_blocked_by_a_fresh_existing_lock(tmp_path):
    vault = _make_vault(tmp_path)
    lock_file = vault / ".watchdog" / "registry" / ".processing-lock"
    lock_file.write_text(f"pid: cli\nstarted_at: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n")
    result = run(vault, force_lock=True)
    assert "error" in result
    assert "already being added" in result["error"]


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

    state_file = vault / ".watchdog" / "processing-state.json"
    assert state_file.exists()
    assert json.loads(state_file.read_text()) == result

    lock_file = vault / ".watchdog" / "registry" / ".processing-lock"
    assert lock_file.exists()
    assert "label: processing" in lock_file.read_text()


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
    lock_file = vault / ".watchdog" / "registry" / ".processing-lock"
    lock_file.write_text(f"pid: cli\nstarted_at: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n")

    result = run(vault)

    assert "error" in result
    assert "already being added" in result["error"]


def test_stale_lock_is_replaced(tmp_path):
    vault = _make_vault(tmp_path)
    _write_queue_file(vault, "abc123")

    stale_ts = datetime.fromtimestamp(
        time.time() - STALE_SECONDS - 60, tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    lock_file = vault / ".watchdog" / "registry" / ".processing-lock"
    lock_file.write_text(f"pid: old\nstarted_at: {stale_ts}\n")

    result = run(vault)

    assert result["lock_acquired"] is True
    assert "label: processing" in lock_file.read_text()


def test_malformed_lock_is_refused_not_deleted(tmp_path):
    """A lock with no parseable started_at is of unknown age. The old check-then-unlink deleted
    it regardless and proceeded; now ingest refuses and leaves it to be cleared from the app (#257)."""
    vault = _make_vault(tmp_path)
    _write_queue_file(vault, "abc123")
    lock_file = vault / ".watchdog" / "registry" / ".processing-lock"
    lock_file.write_text("pid: mystery\n")   # no started_at line

    result = run(vault)

    assert "error" in result
    assert "already being added" in result["error"]
    assert lock_file.read_text() == "pid: mystery\n"   # preserved, not clobbered


def test_extractor_model_written_to_state(tmp_path):
    vault = _make_vault(tmp_path)
    _write_queue_file(vault, "abc123")

    result = run(vault, extractor_model="haiku")

    assert result["extractor_model"] == "haiku"
    state = json.loads((vault / ".watchdog" / "processing-state.json").read_text())
    assert state["extractor_model"] == "haiku"


def test_extractor_model_defaults_to_haiku(tmp_path):
    vault = _make_vault(tmp_path)
    _write_queue_file(vault, "abc123")

    result = run(vault)

    assert result["extractor_model"] == "haiku"


def test_finalizer_model_written_to_state(tmp_path):
    vault = _make_vault(tmp_path)
    _write_queue_file(vault, "abc123")

    result = run(vault, finalizer_model="sonnet-5.5")

    assert result["finalizer_model"] == "sonnet-5.5"
    state = json.loads((vault / ".watchdog" / "processing-state.json").read_text())
    assert state["finalizer_model"] == "sonnet-5.5"


def test_finalizer_model_defaults_to_the_shared_default(tmp_path):
    from watchdog import defaults
    vault = _make_vault(tmp_path)
    _write_queue_file(vault, "abc123")

    result = run(vault)

    assert result["finalizer_model"] == defaults.FINALIZER_MODEL


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
    state_file = vault / ".watchdog" / "processing-state.json"
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
                    "cost_low": None, "cost_high": None, "runs_used": 0,
                    "subscription": False, "long_prompt_calls": 0, "long_prompt_documents": 0}


def test_cost_estimate_marks_subscription_runs(tmp_path):
    vault = _make_vault(tmp_path)
    assert cost_estimate(vault, scan_queue(vault), backend="claude-agent-sdk")["subscription"] is True


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


# ── model-scoped tokenizer calibration (#606 Part B) ────────────────────────────

def _record(model, backend, task, est, act, **extra):
    """One `usage-<ts>.json` `calls` entry — the fields `_model_tokenizer_calibration` and
    `_real_input_tokens` actually read (`orchestrate._record_usage`'s own record shape).

    `est` is the WHOLE-PROMPT estimate (`est_prompt_tokens`), which is the denominator the
    calibration must use (#617). A deliberately different document-only `est_input_tokens` — half
    of it — is written alongside, exactly as a real record carries both: reading the wrong one
    would double every ratio below rather than silently agreeing, which is the point."""
    return {"model": model, "backend": backend, "task": task,
            "est_prompt_tokens": est, "est_input_tokens": est // 2, "input_tokens": act,
            "cache_read_tokens": 0, "cache_write_tokens": 0, **extra}


def test_real_input_tokens_sums_cache_fields_only_for_anthropic(tmp_path):
    """#617: the two provider families report cache tokens under opposite conventions.

    Anthropic reports cache reads/writes as counts *additional* to `input_tokens` — the archived
    `claude-agent-sdk` arm shows 18 input tokens against 230,155 cache-write tokens for the same
    six documents — so they must be summed or the real volume vanishes. OpenAI's `prompt_tokens`
    and DeepSeek's already include their cached counts, so summing there double-counts every
    cached token (it read `gpt-5.4-mini` 12.6% high on the archived corpus)."""
    from watchdog.pipeline.ingest_setup import _real_input_tokens
    rec = {"input_tokens": 1000, "cache_read_tokens": 400, "cache_write_tokens": 100}

    assert _real_input_tokens(rec, "claude-api") == 1500
    assert _real_input_tokens(rec, "claude-agent-sdk") == 1500
    assert _real_input_tokens(rec, "claude-batch") == 1500
    assert _real_input_tokens(rec, "openai") == 1000
    assert _real_input_tokens(rec, "openai-batch") == 1000
    assert _real_input_tokens(rec, "deepseek") == 1000
    assert _real_input_tokens(rec, "gemini") == 1000
    # No backend (a run-level `totals`, which may mix providers) keeps the summing form: Anthropic
    # is the default backend and the only one the naive form is catastrophically wrong for.
    assert _real_input_tokens(rec) == 1500


def test_model_tokenizer_calibration_no_history_returns_none(tmp_path):
    from watchdog.pipeline.ingest_setup import _model_tokenizer_calibration
    vault = _make_vault(tmp_path)
    assert _model_tokenizer_calibration(vault, "sonnet-5.5", None) is None


def test_model_tokenizer_calibration_below_min_records_returns_none(tmp_path):
    """A model tried once or twice in this vault shouldn't override the catalog constant —
    only 2 matching records, below the default min_records=3."""
    from watchdog.model_catalog import resolve_model_id
    from watchdog.pipeline.ingest_setup import _model_tokenizer_calibration
    vault = _make_vault(tmp_path)
    model_id = resolve_model_id("sonnet-5.5")
    calls = [_record(model_id, None, "extract", 1000, 1300),
             _record(model_id, None, "extract-section", 500, 650)]
    _write_usage_file(vault, "20260101T000000Z", input_tokens=0, cost_usd=None, calls=calls)

    assert _model_tokenizer_calibration(vault, "sonnet-5.5", None) is None


def test_model_tokenizer_calibration_pools_matching_records(tmp_path):
    """Enough matching records (>= min_records) produce the pooled sum(actual)/sum(estimated)
    ratio — not a per-file or per-record average — across both `extract` and `extract-section`
    tasks, which the tokenizer ratio treats as equally valid evidence."""
    from watchdog.model_catalog import resolve_model_id
    from watchdog.pipeline.ingest_setup import _model_tokenizer_calibration
    vault = _make_vault(tmp_path)
    model_id = resolve_model_id("sonnet-5.5")
    calls = [
        _record(model_id, None, "extract", 1000, 1300),
        _record(model_id, None, "extract-section", 2000, 2600),
        _record(model_id, None, "extract-section", 500, 650),
    ]
    _write_usage_file(vault, "20260101T000000Z", input_tokens=0, cost_usd=None, calls=calls)

    # sum(actual)/sum(estimated) = (1300+2600+650) / (1000+2000+500) = 4550/3500 = 1.3
    assert _model_tokenizer_calibration(vault, "sonnet-5.5", None) == pytest.approx(1.3)


def test_model_tokenizer_calibration_skips_pre_617_records(tmp_path):
    """A record written before #617 carries only the document-only `est_input_tokens`, never
    `est_prompt_tokens`. Those records are skipped, not silently divided on the old denominator:
    `input_tokens` covers the whole rendered prompt, so pairing it with a document-only estimate
    measures `tokenizer_ratio x (1 + prompt_overhead / document_tokens)` — the contamination
    #617 was opened about, which read a true 1.09 Claude ratio as 1.41. With nothing usable left
    the vault calibrates to None and the caller falls back to the measured catalog constant."""
    from watchdog.model_catalog import resolve_model_id
    from watchdog.pipeline.ingest_setup import _model_tokenizer_calibration
    vault = _make_vault(tmp_path)
    model_id = resolve_model_id("sonnet-5.5")
    legacy = [{"model": model_id, "backend": None, "task": "extract",
               "est_input_tokens": est, "input_tokens": act,
               "cache_read_tokens": 0, "cache_write_tokens": 0}
              for est, act in ((1000, 1300), (2000, 2600), (500, 650))]
    _write_usage_file(vault, "20260101T000000Z", input_tokens=0, cost_usd=None, calls=legacy)

    assert _model_tokenizer_calibration(vault, "sonnet-5.5", None) is None


def test_model_tokenizer_calibration_ignores_non_matching_model_or_backend(tmp_path):
    """Records for a different model, or the same model on a different backend, must not be
    pooled into this model's ratio — each is a distinct tokenizer."""
    from watchdog.model_catalog import resolve_model_id
    from watchdog.pipeline.ingest_setup import _model_tokenizer_calibration
    vault = _make_vault(tmp_path)
    model_id = resolve_model_id("sonnet-5.5")
    calls = [
        _record(model_id, None, "extract", 1000, 1300),
        _record(model_id, None, "extract-section", 2000, 2600),
        _record(model_id, None, "extract-section", 500, 650),
        # Noise: wrong model id, and the right model id on a different (hypothetical) backend.
        _record("gpt-5-mini", "openai", "extract", 1000, 9999),
        _record(model_id, "claude-api", "extract", 1000, 9999),
    ]
    _write_usage_file(vault, "20260101T000000Z", input_tokens=0, cost_usd=None, calls=calls)

    assert _model_tokenizer_calibration(vault, "sonnet-5.5", None) == pytest.approx(1.3)


def test_model_tokenizer_calibration_ignores_non_extract_tasks(tmp_path):
    """A `reconcile`/`briefing`/etc. record for the same model must not be pooled — only
    `extract`/`extract-section` calls carry `est_input_tokens` at all in practice, but the task
    filter is explicit rather than relying on that."""
    from watchdog.model_catalog import resolve_model_id
    from watchdog.pipeline.ingest_setup import _model_tokenizer_calibration
    vault = _make_vault(tmp_path)
    model_id = resolve_model_id("sonnet-5.5")
    calls = [
        _record(model_id, None, "extract", 1000, 1300),
        _record(model_id, None, "extract-section", 2000, 2600),
        _record(model_id, None, "extract-section", 500, 650),
        _record(model_id, None, "briefing", 1000, 9999),
    ]
    _write_usage_file(vault, "20260101T000000Z", input_tokens=0, cost_usd=None, calls=calls)

    assert _model_tokenizer_calibration(vault, "sonnet-5.5", None) == pytest.approx(1.3)


def test_model_tokenizer_calibration_stops_at_max_records(tmp_path):
    """Once `max_records` matching records have been collected, scanning stops — a record that
    would push the pool past the cap is never counted, so it can't skew the ratio."""
    from watchdog.model_catalog import resolve_model_id
    from watchdog.pipeline.ingest_setup import _model_tokenizer_calibration
    vault = _make_vault(tmp_path)
    model_id = resolve_model_id("sonnet-5.5")
    # 4 identical 1.3-ratio records, then a 5th wildly different one — with max_records=4 the
    # 5th must never be counted.
    calls = [_record(model_id, None, "extract", 1000, 1300) for _ in range(4)]
    calls.append(_record(model_id, None, "extract", 1000, 100000))
    _write_usage_file(vault, "20260101T000000Z", input_tokens=0, cost_usd=None, calls=calls)

    assert _model_tokenizer_calibration(vault, "sonnet-5.5", None, max_records=4) == pytest.approx(1.3)


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
    assert est == {"docs": 0, "est_tokens": 0, "cost_low": None, "cost_high": None, "runs_used": 0,
                   "basis": None,
                   "same_name_groups": {"groups": 0, "records": 0, "calls": 0, "est_tokens": 0},
                   "housekeeping": {"items": 0, "calls": 0, "est_tokens": 0}}


def test_finalize_cost_estimate_counts_the_same_name_group_calls(tmp_path):
    """Records sharing a generic name are compared in group calls (#755), which the estimate adds
    from the registry: 70 registered "Harbour Board" records plus one staged make 71, three
    slices and a round over their survivors. A distinctive name merges in code and costs none."""
    from watchdog.pipeline import reconcile
    vault = _make_vault(tmp_path)
    _stage_finalize_corpus(vault, {"sha1": "a" * 400})
    reg = {f"hb-{i}": {"id": f"hb-{i}", "name": "Harbour Board", "type": "organization"}
           for i in range(70)}
    reg["nw"] = {"id": "nw", "name": "Northgate Civil Works", "type": "organization"}
    (vault / ".watchdog" / "registry").mkdir(parents=True, exist_ok=True)
    (vault / ".watchdog" / "registry" / "entities.json").write_text(json.dumps(reg))
    (vault / ".watchdog" / "extracted").mkdir(parents=True, exist_ok=True)
    (vault / ".watchdog" / "extracted" / "sha1.json").write_text(json.dumps({"document": {}, "entities": [
        {"id": "hb", "name": "Harbour Board", "type": "Organization"},
        {"id": "nw", "name": "Northgate Civil Works", "type": "Organization"}]}))

    est = finalize_cost_estimate(vault, backend="claude-api")

    groups = est["same_name_groups"]
    assert (groups["groups"], groups["records"], groups["calls"]) == (1, 71, 4)
    assert groups["est_tokens"] == 71 * reconcile._GROUP_MEMBER_EST_CHARS * 2 // 4
    assert est["est_tokens"] > groups["est_tokens"]


def _row(task, input_tokens, cost_usd, output_tokens=0, backend="claude-api"):
    """One usage row with the fields the stage history reads (D403)."""
    return {"task": task, "backend": backend, "model": "m", "input_tokens": input_tokens,
            "output_tokens": output_tokens, "cache_read_tokens": 0, "cache_write_tokens": 0,
            "cost_usd": cost_usd}


def _real(vault, est_tokens, model=None):
    from watchdog import model_client
    return est_tokens * model_client.tokenizer_ratio(model, "claude-api", vault)


def test_finalize_cost_estimate_without_history_is_at_list_price(tmp_path):
    """With no priced post-processing here, the staged batch is priced at the finalizer's list
    price and says so (D403), rather than shown as tokens only."""
    from watchdog import model_catalog, model_client
    from watchdog.pipeline import ingest_setup
    vault = _make_vault(tmp_path)
    _stage_finalize_corpus(vault, {"sha1": "a" * 4000})

    est = finalize_cost_estimate(vault, backend="claude-api", model="haiku")

    real = _real(vault, est["est_tokens"], "haiku")
    low_r, high_r = ingest_setup._POST_OUTPUT_RATIO
    model_id = model_client.resolve_model_id("haiku")
    assert est["basis"] == "list-price" and est["runs_used"] == 0
    assert est["cost_low"] == pytest.approx(model_catalog.list_price(model_id, real, real * low_r))
    assert est["cost_high"] == pytest.approx(model_catalog.list_price(model_id, real, real * high_r))


def test_finalize_cost_estimate_takes_its_rate_from_post_processing_rows(tmp_path):
    """The rate is dollars per input token over a run's post-processing rows only, whether or not
    the run also extracted (D403): the extraction rows of a mixed run are left out of it."""
    vault = _make_vault(tmp_path)
    _stage_finalize_corpus(vault, {"sha1": "a" * 4000})   # 2008 est tokens
    _write_usage_file(vault, "20260101T000000Z", 0, None, calls=[
        _row("extract", 999_999, 999.0), _row("reconcile", 600, 0.6), _row("briefing", 400, 0.4)])
    _write_usage_file(vault, "20260102T000000Z", 0, None, calls=[
        _row("reconcile", 1000, 2.0)])

    est = finalize_cost_estimate(vault, backend="claude-api")

    real = _real(vault, 2008)
    assert est["est_tokens"] == 2008 and est["basis"] == "history" and est["runs_used"] == 2
    assert est["cost_low"] == pytest.approx(real * 0.001)
    assert est["cost_high"] == pytest.approx(real * 0.002)


def test_finalize_cost_estimate_skips_runs_with_no_cost(tmp_path):
    """A subscription run records no dollars, so it is no rate."""
    vault = _make_vault(tmp_path)
    _stage_finalize_corpus(vault, {"sha1": "a" * 4000})
    _write_usage_file(vault, "20260101T000000Z", 0, None, calls=[_row("reconcile", 1000, None)])

    assert finalize_cost_estimate(vault, backend="claude-api")["basis"] == "list-price"


def test_finalize_cost_estimate_usage_files_param_overrides_vault_history(tmp_path):
    """Same override as `cost_estimate`'s own (issue #478), for the standalone path."""
    vault = _make_vault(tmp_path)
    _stage_finalize_corpus(vault, {"sha1": "a" * 4000})
    _write_usage_file(vault, "20260101T000000Z", 0, None, calls=[_row("reconcile", 1, 999.0)])
    other = tmp_path / "elsewhere"
    other.mkdir()
    uf = other / "usage-20260102T000000Z.json"
    uf.write_text(json.dumps({"calls": [_row("reconcile", 1000, 1.0)], "totals": {}}))

    est = finalize_cost_estimate(vault, backend="claude-api", usage_files=[uf])

    assert est["runs_used"] == 1
    assert est["cost_low"] == est["cost_high"] == pytest.approx(_real(vault, 2008) * 0.001)


# ── the Add documents estimate: two lines (D403, #820) ──────────────────────────

def _queue_doc(vault, sha="abc123", chars=4000):
    (vault / ".watchdog" / "queue" / f"{sha}.json").write_text(json.dumps({
        "filename": "x.pdf", "page_count": 3, "sha256": sha,
        "pages": [{"page": 1, "markdown": "word " * (chars // 5)}]}))


def test_run_cost_estimate_first_run_is_at_list_price_on_whole_prompts(tmp_path, monkeypatch):
    """A first run gets a dollar figure (D403): both lines at list price, extraction on the
    planned whole prompts (far more than the document text of a short file), post-processing
    projected from extraction's expected output plus its fixed prompts."""
    from watchdog import model_catalog, model_client
    from watchdog.pipeline import ingest_setup
    vault = _make_vault(tmp_path)
    _queue_doc(vault)
    monkeypatch.setattr(ingest_setup, "planned_prompts", lambda *a, **k: [[9000.0, 3000.0]])

    est = ingest_setup.run_cost_estimate(vault, scan_queue(vault), "claude-api", model="haiku",
                                         post_backend="claude-api", post_model="haiku")

    ex, post = est["extraction"], est["post"]
    model_id = model_client.resolve_model_id("haiku")
    low_r, high_r = ingest_setup._EXTRACT_OUTPUT_RATIO
    assert ex["tokens_high"] == 12000.0 and est["basis"] == ex["basis"] == post["basis"] == "list-price"
    assert ex["cost_low"] == pytest.approx(model_catalog.list_price(model_id, 12000, 12000 * low_r))
    assert ex["cost_high"] == pytest.approx(model_catalog.list_price(model_id, 12000, 12000 * high_r))
    output = 12000 * (low_r + high_r) / 2
    fixed = ingest_setup._post_fixed_est() * model_client.tokenizer_ratio("haiku", "claude-api", vault)
    k_low, k_high = ingest_setup._POST_PER_OUTPUT_TOKEN
    assert post["tokens_low"] == pytest.approx(fixed + output * k_low)
    assert post["tokens_high"] == pytest.approx(fixed + output * k_high)
    assert est["cost_low"] == pytest.approx(ex["cost_low"] + post["cost_low"])
    assert est["cost_high"] == pytest.approx(ex["cost_high"] + post["cost_high"])


def test_run_cost_estimate_uses_each_stages_own_history(tmp_path, monkeypatch):
    """With priced runs, each line takes its own rate from its own rows, and post-processing's
    size follows this investigation's measured input per extraction output token."""
    from watchdog.pipeline import ingest_setup
    vault = _make_vault(tmp_path)
    _queue_doc(vault)
    monkeypatch.setattr(ingest_setup, "planned_prompts", lambda *a, **k: [[10_000.0]])
    # One run: extraction sent 1,000 tokens and wrote 200 for $1 (classification's $0.50 rides
    # in extraction's dollars); post-processing sent 800 tokens for $0.40.
    _write_usage_file(vault, "20260101T000000Z", 0, None, calls=[
        _row("classify", 300, 0.5), _row("extract", 1000, 1.0, output_tokens=200),
        _row("reconcile", 800, 0.4)])

    est = ingest_setup.run_cost_estimate(vault, scan_queue(vault), "claude-api", model="haiku",
                                         post_backend="claude-api", post_model="haiku")

    assert est["basis"] == "history" and est["runs_used"] == 1
    assert est["extraction"]["cost_low"] == est["extraction"]["cost_high"] == pytest.approx(10_000 * 0.0015)
    post_tokens = 10_000 * 0.2 * (800 / 200)
    assert est["post"]["tokens_low"] == est["post"]["tokens_high"] == pytest.approx(post_tokens)
    assert est["post"]["cost_high"] == pytest.approx(post_tokens * 0.0005)
    assert est["cost_high"] == pytest.approx(15.0 + post_tokens * 0.0005)


def test_run_cost_estimate_subscription_shows_no_dollars(tmp_path, monkeypatch):
    from watchdog.pipeline import ingest_setup
    vault = _make_vault(tmp_path)
    _queue_doc(vault)
    monkeypatch.setattr(ingest_setup, "planned_prompts", lambda *a, **k: [[10_000.0]])

    est = ingest_setup.run_cost_estimate(vault, scan_queue(vault), "claude-agent-sdk",
                                         post_backend="claude-agent-sdk")

    assert est["subscription"] is True and est["cost_low"] is None and est["cost_high"] is None
    assert est["extraction"]["cost_low"] is None and est["post"]["cost_low"] is None


def test_run_cost_estimate_leaves_a_subscription_post_processing_unpriced(tmp_path, monkeypatch):
    """Extraction on a key and post-processing on a subscription: the total is extraction's."""
    from watchdog.ops.ingest import _format_cost_estimate
    from watchdog.pipeline import ingest_setup
    vault = _make_vault(tmp_path)
    _queue_doc(vault)
    monkeypatch.setattr(ingest_setup, "planned_prompts", lambda *a, **k: [[10_000.0]])

    monkeypatch.setattr(ingest_setup, "planned_prompts", lambda *a, **k: [[10_000_000.0]])
    est = ingest_setup.run_cost_estimate(vault, scan_queue(vault), "claude-api", model="haiku",
                                         post_backend="claude-agent-sdk")

    assert est["post"]["cost_low"] is None
    assert est["cost_high"] == pytest.approx(est["extraction"]["cost_high"])
    text = _format_cost_estimate(est)
    assert "Extraction about $" in text and "Post-processing is not priced" in text
    assert "at list price" in text


def test_format_cost_estimate_says_each_basis_when_they_differ():
    from watchdog.ops.ingest import _format_cost_estimate
    est = {"documents": 1, "pages": 2, "est_tokens": 1, "cost_low": 3.0, "cost_high": 5.004,
           "runs_used": 1, "basis": "list-price", "subscription": False,
           "extraction": {"tokens_high": 9000, "cost_low": 3.0, "cost_high": 5.0,
                          "basis": "history", "runs_used": 1},
           "post": {"cost_low": 0.001, "cost_high": 0.004, "basis": "list-price", "runs_used": 0}}
    assert _format_cost_estimate(est).splitlines()[1] == (
        "  Extraction about $3.00-5.00 (based on your last run) · post-processing under $0.01 "
        "(at list price, with no finished runs here to measure from; a likely ceiling) · "
        "total about $3.00-5.00")


def test_format_cost_estimate_shows_both_lines_and_the_total():
    from watchdog.ops.ingest import _format_cost_estimate
    est = {"documents": 18, "pages": 410, "est_tokens": 1, "cost_low": 11.1, "cost_high": 16.4,
           "runs_used": 3, "basis": "history", "subscription": False,
           "extraction": {"tokens_low": 2_100_000, "tokens_high": 2_100_000, "cost_low": 9.0,
                          "cost_high": 12.0, "basis": "history", "runs_used": 3},
           "post": {"tokens_low": 1, "tokens_high": 2, "cost_low": 2.1, "cost_high": 4.4,
                    "basis": "history", "runs_used": 3}}
    assert _format_cost_estimate(est).splitlines() == [
        "  18 documents · ~410 pages · est. ~2.1M tokens in for extraction",
        "  Extraction about $9.00-12 · post-processing about $2.10-4.40 · total about $11-16 "
        "(based on your last 3 runs)"]


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


def _flat_prices(monkeypatch):
    """Pin every model's time-of-day price multiplier to 1.0 (D217) for a test that is about
    something else — without this, any assertion naming a DeepSeek dollar figure passes or fails
    depending on what time of day the suite runs."""
    from watchdog import model_catalog
    monkeypatch.setattr(model_catalog, "price_multiplier", lambda *_a, **_k: 1.0)


def test_cost_estimate_all_models_projects_every_catalog_model(tmp_path, monkeypatch):
    from watchdog.model_catalog import all_models
    _flat_prices(monkeypatch)
    vault = _make_vault(tmp_path)
    _write_usage_file(vault, "20260101T000000Z", input_tokens=1000, cost_usd=1.0, output_tokens=500)

    rows = cost_estimate_all_models(vault, est_tokens=2000)

    catalog = all_models()
    assert {r["id"] for r in rows} == {m["id"] for m in catalog}
    assert [r["cost"] for r in rows] == sorted(r["cost"] for r in rows)   # cheapest first
    # output ratio 500/1000 = 0.5 -> est_output = 1000; cost = 2000*input + 1000*output per model,
    # each scaled by that model's own tokenizer_ratio (0.81 for DeepSeek V4 — see the dedicated
    # ratio test below; this one is about the projection).
    by_id = {r["id"]: r["cost"] for r in rows}
    ds = next(m for m in catalog if m["id"] == "deepseek-flash")
    assert by_id["deepseek-flash"] == pytest.approx(
        2000 * 0.81 * ds["input"] + 1000 * 0.81 * ds["output"])


def test_cost_estimate_all_models_ignores_runs_with_no_output_tokens(tmp_path, monkeypatch):
    """A run with zero recorded output tokens can't contribute a ratio — must be skipped, not
    treated as an output:input ratio of 0 (which would zero out every model's output cost)."""
    _flat_prices(monkeypatch)
    vault = _make_vault(tmp_path)
    _write_usage_file(vault, "20260101T000000Z", input_tokens=1000, cost_usd=1.0, output_tokens=0)
    _write_usage_file(vault, "20260102T000000Z", input_tokens=1000, cost_usd=1.0, output_tokens=1000)

    rows = cost_estimate_all_models(vault, est_tokens=1000)

    # only the second run's 1:1 ratio counts -> est_output = 1000, both sides then scaled by
    # DeepSeek V4's measured 0.81 tokenizer_ratio.
    by_id = {r["id"]: r["cost"] for r in rows}
    from watchdog.model_catalog import all_models
    ds = next(m for m in all_models() if m["id"] == "deepseek-flash")
    assert by_id["deepseek-flash"] == pytest.approx(
        1000 * 0.81 * ds["input"] + 1000 * 0.81 * ds["output"])


def test_cost_estimate_all_models_scales_by_each_models_tokenizer_ratio(tmp_path, monkeypatch):
    # Every catalogued model's measured tokenizer_ratio (#574, remeasured #617) is applied to its
    # projected input AND output tokens, so each is priced against its own real token count for
    # the same text. The correction runs both directions: Sonnet 5.5's 1.235 prices it up, Gemini's
    # 0.91 prices it down, and only a model with no declared ratio is projected as-is.
    from watchdog.model_catalog import all_models
    _flat_prices(monkeypatch)
    vault = _make_vault(tmp_path)
    _write_usage_file(vault, "20260101T000000Z", input_tokens=1000, cost_usd=1.0, output_tokens=500)

    rows = cost_estimate_all_models(vault, est_tokens=2000)

    by_id = {r["id"]: r["cost"] for r in rows}
    catalog = {m["id"]: m for m in all_models()}

    def projected(model_id, ratio):
        m = catalog[model_id]
        scaled_in = 2000 * ratio
        scaled_out = scaled_in * 0.5   # output_ratio (500/1000), applied after scaling
        return scaled_in * m["input"] + scaled_out * m["output"]

    assert by_id["claude-sonnet-5-5"] == pytest.approx(projected("claude-sonnet-5-5", 1.235))
    assert by_id["claude-haiku-5-5"] == pytest.approx(projected("claude-haiku-5-5", 1.235))
    assert by_id["gemini-3.8-flash"] == pytest.approx(projected("gemini-3.8-flash", 0.91))
    assert by_id["gpt-6-luna"] == pytest.approx(projected("gpt-6-luna", 0.80))
    assert by_id["deepseek-flash"] == pytest.approx(projected("deepseek-flash", 0.81))


def test_cost_estimate_all_models_prices_a_scheduled_model_at_the_current_rate(tmp_path,
                                                                               monkeypatch):
    """A model whose rates vary by the clock (D217) is projected at the rate in force right now,
    and the row carries the multiplier that produced it so the caller can label the figure rather
    than leaving it looking like a catalog error."""
    from watchdog import model_catalog
    vault = _make_vault(tmp_path)
    _write_usage_file(vault, "20260101T000000Z", input_tokens=1000, cost_usd=1.0, output_tokens=500)

    monkeypatch.setattr(model_catalog, "price_multiplier", lambda *_a, **_k: 1.0)
    off_peak = {r["id"]: r for r in cost_estimate_all_models(vault, est_tokens=2000)}
    monkeypatch.setattr(model_catalog, "price_multiplier",
                        lambda model_id, *_a, **_k: 2.0 if model_id.startswith("deepseek") else 1.0)
    peak = {r["id"]: r for r in cost_estimate_all_models(vault, est_tokens=2000)}

    assert peak["deepseek-flash"]["cost"] == pytest.approx(
        2 * off_peak["deepseek-flash"]["cost"])
    assert peak["deepseek-flash"]["price_multiplier"] == 2.0
    assert peak["claude-sonnet-5-5"]["cost"] == pytest.approx(
        off_peak["claude-sonnet-5-5"]["cost"])          # a flat-priced model never moves
    assert peak["claude-sonnet-5-5"]["price_multiplier"] == 1.0


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


# ── the estimate a run keeps (D404) ─────────────────────────────────────────────

def _held(vault, documents=3, entities=5):
    reg = vault / ".watchdog" / "registry"
    reg.mkdir(parents=True, exist_ok=True)
    (reg / "documents.json").write_text(json.dumps({f"d{i}": {} for i in range(documents)}))
    (reg / "entities.json").write_text(json.dumps({f"e{i}": {} for i in range(entities)}))


def test_run_cost_estimate_warns_when_the_registry_is_not_in_the_forecast(tmp_path, monkeypatch):
    """An investigation that already holds documents, with no earlier run that did both stages,
    is told its post-processing line may be low; a first batch and a measured ratio are not."""
    from watchdog.ops.ingest import _format_cost_estimate
    from watchdog.pipeline import ingest_setup
    vault = _make_vault(tmp_path)
    _queue_doc(vault)
    monkeypatch.setattr(ingest_setup, "planned_prompts", lambda *a, **k: [[10_000_000.0]])
    args = dict(model="haiku", post_backend="claude-api", post_model="haiku")

    first = ingest_setup.run_cost_estimate(vault, scan_queue(vault), "claude-api", **args)
    assert first["post_may_be_low"] is False
    assert "may cost more" not in _format_cost_estimate(first)

    _held(vault)
    held = ingest_setup.run_cost_estimate(vault, scan_queue(vault), "claude-api", **args)
    assert held["post_may_be_low"] is True
    assert "Post-processing may cost more than shown" in _format_cost_estimate(held)

    _write_usage_file(vault, "20260101T000000Z", 0, None, calls=[
        _row("extract", 1000, 1.0, output_tokens=200), _row("reconcile", 800, 0.4)])
    measured = ingest_setup.run_cost_estimate(vault, scan_queue(vault), "claude-api", **args)
    assert measured["post_may_be_low"] is False


def test_estimate_record_keeps_both_lines_and_the_registry_size(tmp_path, monkeypatch):
    from watchdog.pipeline import ingest_setup
    vault = _make_vault(tmp_path)
    _queue_doc(vault)
    _held(vault, documents=3, entities=5)
    monkeypatch.setattr(ingest_setup, "planned_prompts", lambda *a, **k: [[10_000.0]])
    est = ingest_setup.run_cost_estimate(vault, scan_queue(vault), "claude-api", model="haiku",
                                         post_backend="claude-api", post_model="haiku")

    rec = ingest_setup.estimate_record(vault, est)

    assert rec["registry"] == {"entities": 5, "documents": 3} and rec["documents"] == 1
    assert rec["extraction"]["tokens_high"] == 10_000.0 and rec["extraction"]["basis"] == "list-price"
    assert rec["post"]["cost_high"] == est["post"]["cost_high"] and rec["post_may_be_low"] is True
    json.dumps(rec)   # it is stored as JSON
    assert ingest_setup.estimate_record(vault, {"extraction": None}) is None


def test_a_run_keeps_its_estimate_with_its_usage_and_in_telemetry(tmp_path, monkeypatch):
    """The estimate lands in the run's usage file and in the telemetry store under the run's id,
    and leaves the store with the investigation when it is moved, then purged."""
    import sqlite3
    from watchdog import telemetry_db
    from watchdog.pipeline import orchestrate
    vault = _make_vault(tmp_path)
    monkeypatch.setattr(telemetry_db, "db_path", lambda: tmp_path / "telemetry.db")
    monkeypatch.setattr(telemetry_db, "enabled", lambda: True)
    monkeypatch.setattr("watchdog.auth.use_investigation", lambda v: None)
    estimate = {"version": 1, "extraction": {"cost_high": 2.0}, "registry": {"entities": 5, "documents": 3}}
    try:
        orchestrate._begin_usage_run(vault, estimate=estimate)
        run_id = orchestrate._run.run_id
        orchestrate._run.usage.append(_row("extract", 1000, 1.0))
        path, _ = orchestrate._end_usage_run(vault)

        assert json.loads((vault / path).read_text())["estimate"] == estimate
        assert orchestrate._run.estimate is None
        rows = sqlite3.connect(tmp_path / "telemetry.db").execute(
            "SELECT run_id, vault_name, estimate_json FROM run_estimates").fetchall()
        assert [(r[0], r[1], json.loads(r[2])) for r in rows] == [(run_id, vault.name, estimate)]

        moved = tmp_path / "moved"
        assert telemetry_db.relocate_vault(vault.resolve(), moved) == 0   # no call rows to move
        assert sqlite3.connect(tmp_path / "telemetry.db").execute(
            "SELECT vault_name FROM run_estimates").fetchall() == [("moved",)]
        telemetry_db.purge_vault(moved)
        telemetry_db.close()
        assert sqlite3.connect(tmp_path / "telemetry.db").execute(
            "SELECT count(*) FROM run_estimates").fetchone()[0] == 0
    finally:
        telemetry_db.close()


def test_a_run_without_an_estimate_writes_none(tmp_path, monkeypatch):
    from watchdog import telemetry_db
    from watchdog.pipeline import orchestrate
    vault = _make_vault(tmp_path)
    monkeypatch.setattr(telemetry_db, "enabled", lambda: False)
    monkeypatch.setattr("watchdog.auth.use_investigation", lambda v: None)
    orchestrate._begin_usage_run(vault)
    orchestrate._run.usage.append(_row("reconcile", 1000, 1.0))
    path, _ = orchestrate._end_usage_run(vault)
    assert "estimate" not in json.loads((vault / path).read_text())
