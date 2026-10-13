"""The ingest operations' library (`watchdog.ops.ingest`): stage and effort resolution, the run
options' plan, the processing and post-processing runs' guards and loops, the sleep guard and the
summaries. Ported from the command-line tests when the command line was removed (D300)."""

import contextlib
import json

import pytest

from watchdog.ops import ingest as ing
from watchdog.ops.ingest import RunOptions

pytest_plugins = ["tests.gui_support"]   # the wdg_home fixture


class _FakeProc:
    """Stand-in for `subprocess.Popen`'s return value, shared by the caffeinate tests below —
    tracks `terminate()`/`kill()`/`wait()` calls without touching a real process."""
    def __init__(self, pid=99999):
        self.pid = pid
        self.terminated = False
        self.killed = False
        self.waited = False

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        self.waited = True


def _fake_catalog(monkeypatch, tmp_path, names=("general-records", "corporate-filings")):
    """Point the global skill catalog at a controlled package dir, no user dir."""
    from watchdog import skills_catalog as sc
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    for n in names:
        (pkg / f"{n}.md").write_text(f"# {n}\n")
    monkeypatch.setattr(sc, "_package_records", lambda: pkg)
    monkeypatch.setattr(sc, "USER_SKILLS_DIR", tmp_path / "nouser")
    return pkg


def _vault_with_queued_doc(tmp_path):
    from tests.test_write_vault import make_vault
    vault = make_vault(tmp_path)
    qdir = vault / ".watchdog" / "queue"
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / "sha1.json").write_text(json.dumps({
        "sha256": "sha1", "filename": "a.pdf", "page_count": 1,
        "pages": [{"page": 1, "markdown": "text"}],
        "near_dup": {"near_duplicates": [], "top_similarity": 0.0},
    }))
    (vault / "incoming").mkdir(exist_ok=True)
    return vault


def test_resolve_stage_claude_tier_has_no_backend():
    from watchdog.ops.ingest import resolve_stage as _resolve_stage
    assert _resolve_stage(None, None, default="sonnet") == (None, "sonnet")
    assert _resolve_stage("opus", None) == (None, "opus")
    assert _resolve_stage(None, "haiku") == (None, "haiku")        # from config


def test_resolve_stage_explicit_claude_backend():
    from watchdog.ops.ingest import resolve_stage as _resolve_stage
    assert _resolve_stage("claude-api:opus", None) == ("claude-api", "opus")


def test_resolve_stage_claude_batch_backend():
    """claude-batch (#214) parses as a Claude-tier backend, same as claude-api/claude-agent-sdk."""
    from watchdog.ops.ingest import resolve_stage as _resolve_stage
    assert _resolve_stage("claude-batch:sonnet", None) == ("claude-batch", "sonnet")


def test_resolve_stage_non_claude_backend_keeps_raw_model():
    from watchdog.ops.ingest import resolve_stage as _resolve_stage
    assert _resolve_stage("deepseek:deepseek-flash", None) == ("deepseek", "deepseek-flash")
    assert _resolve_stage("openai:gpt-5-mini", None) == ("openai", "gpt-5-mini")


def test_resolve_stage_flag_beats_config():
    from watchdog.ops.ingest import resolve_stage as _resolve_stage
    assert _resolve_stage("openai:gpt-5-mini", "sonnet") == ("openai", "gpt-5-mini")


def test_resolve_stage_unknown_backend_exits():
    from watchdog.ops.ingest import resolve_stage as _resolve_stage
    with pytest.raises(SystemExit, match="unknown backend"):
        _resolve_stage("groq:llama", None)


def test_resolve_stage_non_claude_requires_model():
    from watchdog.ops.ingest import resolve_stage as _resolve_stage
    with pytest.raises(SystemExit, match="needs a model id"):
        _resolve_stage("deepseek:", None)


def test_resolve_stage_bare_non_tier_is_treated_as_claude_and_rejected():
    from watchdog.ops.ingest import resolve_stage as _resolve_stage
    # No backend prefix → interpreted as a Claude tier → invalid (use openai:gpt-5-mini instead).
    with pytest.raises(SystemExit, match="unknown model"):
        _resolve_stage("gpt-5-mini", None)


def test_effort_flag_beats_config():
    from watchdog.ops.ingest import effort as _effort
    assert _effort("low", "high") == "low"


def test_effort_explicit_choice_passes_through_even_if_model_unknown():
    """An explicit --extractor-effort/config choice is the user's own call — it's returned as-is
    regardless of backend/model, and model_client._resolve_effort is what fails loud downstream
    if the resolved model can't actually take it (unlike the model-aware `default` below)."""
    from watchdog.ops.ingest import effort as _effort
    assert _effort(None, "xhigh", backend=None, model="haiku") == "xhigh"


def test_effort_unknown_value_exits():
    from watchdog.ops.ingest import effort as _effort
    with pytest.raises(SystemExit, match="unknown effort"):
        _effort("ludicrous", None)


def test_effort_default_applied_when_model_supports_it():
    from watchdog.ops.ingest import effort as _effort
    assert _effort(None, None, default="medium", backend=None, model="sonnet") == "medium"


def test_effort_default_skipped_when_model_does_not_support_it():
    """Regression guard (#518): routing `extractor_model` to a model with no `medium` level
    (DeepSeek, either mode) must not turn the implicit `extractor_effort=medium` default (D26)
    into a hard failure for a stage nobody configured — only an *explicit* effort choice should
    ever hit model_client's loud-fail path."""
    from watchdog.ops.ingest import effort as _effort
    assert _effort(None, None, default="medium", backend="deepseek",
                   model="deepseek-flash-thinking") is None
    assert _effort(None, None, default="medium", backend="deepseek", model="deepseek-flash") is None


def test_effort_no_default_stays_unset():
    from watchdog.ops.ingest import effort as _effort
    assert _effort(None, None, backend=None, model="haiku") is None


def test_resolve_finalizer_overrides_falls_back_to_finalizer_model_when_unset():
    """No per-stage flags/config: every stage resolves to exactly the aggregate finalizer's
    own (backend, model), not a hardcoded default."""
    from watchdog.ops.ingest import resolve_finalizer_overrides as _resolve_finalizer_overrides
    overrides = _resolve_finalizer_overrides(RunOptions(), {}, "openai", "gpt-5-mini")
    assert overrides == {
        "reconciliation_backend": "openai", "reconciliation_model": "gpt-5-mini",
        "synthesis_backend": "openai", "synthesis_model": "gpt-5-mini",
        "timeline_backend": "openai", "timeline_model": "gpt-5-mini",
        "briefing_backend": "openai", "briefing_model": "gpt-5-mini",
    }


def test_resolve_finalizer_overrides_flag_overrides_one_stage():
    """A single `--finalizer-<stage>-model` flag overrides only that stage; the others still
    fall back to the aggregate finalizer."""
    from watchdog.ops.ingest import resolve_finalizer_overrides as _resolve_finalizer_overrides
    overrides = _resolve_finalizer_overrides(
        RunOptions(finalizer_briefing_model="claude-api:opus"), {}, None, "haiku")
    assert overrides["briefing_backend"] == "claude-api"
    assert overrides["briefing_model"] == "opus"
    assert overrides["reconciliation_backend"] is None
    assert overrides["reconciliation_model"] == "haiku"
    assert overrides["synthesis_backend"] is None
    assert overrides["synthesis_model"] == "haiku"
    assert overrides["timeline_backend"] is None
    assert overrides["timeline_model"] == "haiku"


def test_resolve_finalizer_overrides_config_key_and_flag_precedence():
    """A config-file `finalizer_<stage>_model` sets the stage's default; a flag for the same
    stage still wins, matching `_resolve_stage`'s own flag-beats-config rule."""
    from watchdog.ops.ingest import resolve_finalizer_overrides as _resolve_finalizer_overrides
    config = {"finalizer_synthesis_model": "gemini:gemini-2.5-flash"}
    overrides = _resolve_finalizer_overrides(RunOptions(), config, None, "haiku")
    assert overrides["synthesis_backend"] == "gemini"
    assert overrides["synthesis_model"] == "gemini-2.5-flash"

    overrides = _resolve_finalizer_overrides(
        RunOptions(finalizer_synthesis_model="opus"), config, None, "haiku")
    assert overrides["synthesis_backend"] is None
    assert overrides["synthesis_model"] == "opus"


def test_process_does_not_require_claude_auth_when_all_stages_non_claude(wdg_home, tmp_path, monkeypatch):
    """A vault fully configured on another provider (e.g. Gemini) for all three stages must be
    able to ingest even when Claude itself has no auth configured at all."""
    from watchdog import auth as auth_module
    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth",
                         lambda: {"mode": "none", "reason": "api-key mode is set but no key is configured"})
    (wdg_home / "config.json").write_text(json.dumps({
        "classifier_model": "gemini:gemini-2.5-flash",
        "extractor_model": "gemini:gemini-2.5-flash",
        "finalizer_model": "gemini:gemini-2.5-flash",
    }))

    class _Stop(Exception):
        pass

    monkeypatch.setattr("watchdog.pipeline.ingest_setup.run", lambda *a, **k: (_ for _ in ()).throw(_Stop()))

    with pytest.raises(_Stop):
        ing.process(vault, RunOptions(skip_warning=True), finalize=True)


def test_process_still_requires_claude_auth_when_a_stage_uses_claude(wdg_home, tmp_path, monkeypatch):
    """The auth gate must still fire when at least one of the three stages is Claude-routed."""
    from watchdog import auth as auth_module
    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth",
                         lambda: {"mode": "none", "reason": "auth not configured"})
    (wdg_home / "config.json").write_text(json.dumps({
        "classifier_model": "gemini:gemini-2.5-flash",
        "extractor_model": "sonnet",
        "finalizer_model": "gemini:gemini-2.5-flash",
    }))

    with pytest.raises(SystemExit, match="auth not configured"):
        ing.process(vault, RunOptions(skip_warning=True), finalize=True)


def test_finish_does_not_require_claude_auth_when_finalizer_non_claude(wdg_home, tmp_path, monkeypatch):
    from watchdog import auth as auth_module
    from watchdog.pipeline import orchestrate as orch_module
    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth",
                         lambda: {"mode": "none", "reason": "api-key mode is set but no key is configured"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: True)
    (wdg_home / "config.json").write_text(json.dumps({"finalizer_model": "gemini:gemini-2.5-flash"}))

    class _Stop(Exception):
        pass

    monkeypatch.setattr(ing, "_run_finalize", lambda *a, **k: (_ for _ in ()).throw(_Stop()))

    with pytest.raises(_Stop):
        ing.finish(vault, RunOptions())


def test_finish_threads_skip_briefing_to_run_finalize(wdg_home, tmp_path, monkeypatch):
    """`watchdog finalize --skip-briefing` (#410) reaches `_run_finalize` as `skip_briefing=True`,
    same as `ingest`'s own plumbing."""
    from watchdog import auth as auth_module
    from watchdog.pipeline import orchestrate as orch_module
    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: True)

    calls = []
    monkeypatch.setattr(ing, "_run_finalize",
                        lambda *a, **k: (calls.append(k), {"synthesized": 0})[1])

    ing.finish(vault, RunOptions(skip_briefing=True))

    assert len(calls) == 1
    assert calls[0].get("skip_briefing") is True


def test_process_claude_batch_needs_no_pinned_skill(wdg_home, tmp_path, monkeypatch):
    """D144: claude-batch no longer requires a run-wide --skill. Each document resolves its own
    skill before the batch is built, so an unpinned mixed-type queue is legal — this used to
    exit with a "pinned skill" error before reaching the orchestrator at all."""
    from watchdog import auth as auth_module
    from watchdog.pipeline import orchestrate as orch_module
    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    (wdg_home / "config.json").write_text(json.dumps({"extractor_model": "claude-batch:sonnet"}))

    seen = {}

    async def _fake_run(vault, **kwargs):
        seen.update(kwargs)
        return {"results": [], "extracted": 0, "skipped": 0, "failed": 0, "batch_pending": True}

    monkeypatch.setattr(orch_module, "run", _fake_run)
    ing.process(vault, RunOptions(skip_warning=True), finalize=True)
    assert seen, "orchestrate.run was never reached — an earlier guard rejected the run"
    assert seen.get("pinned_skill") is None


def test_process_claude_batch_requires_api_key_auth(wdg_home, tmp_path, monkeypatch):
    from watchdog import auth as auth_module
    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "subscription"})
    (wdg_home / "config.json").write_text(json.dumps({"extractor_model": "claude-batch:sonnet"}))
    skill_file = tmp_path / "pinned.md"
    skill_file.write_text("SKILL")

    with pytest.raises(SystemExit, match="needs a Claude API key"):
        ing.process(vault, RunOptions(skip_warning=True, skill=str(skill_file)), finalize=True)


def test_process_rejects_claude_batch_for_classifier_or_finalizer(wdg_home, tmp_path, monkeypatch):
    from watchdog import auth as auth_module
    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    (wdg_home / "config.json").write_text(json.dumps({"classifier_model": "claude-batch:sonnet"}))

    with pytest.raises(SystemExit, match="can only extract"):
        ing.process(vault, RunOptions(skip_warning=True), finalize=True)


def test_process_openai_batch_needs_no_pinned_skill(wdg_home, tmp_path, monkeypatch):
    """openai-batch (#530) gets the same D144 treatment as claude-batch: no run-wide --skill
    required, each document resolves its own before the batch is built."""
    from watchdog import auth as auth_module
    from watchdog.pipeline import orchestrate as orch_module
    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    # classifier_model/finalizer_model stay on their Claude default (unset) even though the
    # extractor is routed to openai-batch — D37's "Claude by default, other providers opt-in per
    # stage" — so Claude auth is still resolved for those two stages.
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(auth_module, "get_api_key", lambda provider: "sk-oai" if provider == "openai" else None)
    (wdg_home / "config.json").write_text(json.dumps({"extractor_model": "openai-batch:gpt-5.6-luna"}))

    seen = {}

    async def _fake_run(vault, **kwargs):
        seen.update(kwargs)
        return {"results": [], "extracted": 0, "skipped": 0, "failed": 0, "batch_pending": True}

    monkeypatch.setattr(orch_module, "run", _fake_run)
    ing.process(vault, RunOptions(skip_warning=True), finalize=True)
    assert seen, "orchestrate.run was never reached — an earlier guard rejected the run"
    assert seen.get("pinned_skill") is None
    assert seen.get("extract_backend") == "openai-batch"


def test_process_rejects_openai_batch_for_classifier_or_finalizer(wdg_home, tmp_path, monkeypatch):
    from watchdog import auth as auth_module
    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(auth_module, "get_api_key", lambda provider: "sk-oai")
    (wdg_home / "config.json").write_text(json.dumps({"classifier_model": "openai-batch:gpt-5.6-luna"}))

    with pytest.raises(SystemExit, match="can only extract"):
        ing.process(vault, RunOptions(skip_warning=True), finalize=True)


def test_process_wait_rejects_openai_batch(wdg_home, tmp_path, monkeypatch):
    from watchdog import auth as auth_module
    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(auth_module, "get_api_key", lambda provider: "sk-oai" if provider == "openai" else None)
    (wdg_home / "config.json").write_text(json.dumps({"extractor_model": "openai-batch:gpt-5.6-luna"}))
    skill_file = tmp_path / "pinned.md"
    skill_file.write_text("SKILL")

    with pytest.raises(SystemExit, match="isn't supported with openai-batch"):
        ing.process(vault, RunOptions(skip_warning=True, skill=str(skill_file), wait=True), finalize=True)


def test_wait_seconds_uses_resets_at_plus_buffer(monkeypatch):
    from watchdog.ops.ingest import _wait_seconds, _WAIT_BUFFER_SECONDS
    now = 1_000_000
    monkeypatch.setattr("time.time", lambda: now)
    seconds, exact = _wait_seconds(now + 100)
    assert exact is True
    assert seconds == 100 + _WAIT_BUFFER_SECONDS


def test_wait_seconds_falls_back_when_resets_at_missing():
    from watchdog.ops.ingest import _wait_seconds, _WAIT_FALLBACK_SECONDS
    seconds, exact = _wait_seconds(None)
    assert exact is False
    assert seconds == _WAIT_FALLBACK_SECONDS


def test_wait_for_rate_limit_refreshes_lock_in_chunks(tmp_path, monkeypatch):
    """A wait longer than one refresh chunk sleeps in pieces, refreshing the held lock after
    each — so a wait that outlasts the 30-min staleness window never looks abandoned."""
    from watchdog.pipeline import locks

    lock_file = tmp_path / ".processing-lock"
    lock_file.write_text("pid: cli\nstarted_at: 2000-01-01T00:00:00Z\n")

    slept = []
    monkeypatch.setattr(ing.time, "sleep", lambda s: slept.append(s))
    refreshed = []
    monkeypatch.setattr(locks, "refresh_lock", lambda lf: refreshed.append(lf))
    monkeypatch.setattr(ing, "_WAIT_REFRESH_SECONDS", 100)
    monkeypatch.setattr(ing, "_WAIT_FALLBACK_SECONDS", 250)

    ing._wait_for_rate_limit(lock_file, None)   # no resets_at -> fallback sleep

    assert slept == [100, 100, 50]
    assert refreshed == [lock_file, lock_file, lock_file]


def test_merge_summary_accumulates_across_cycles():
    from watchdog.ops.ingest import _merge_summary
    first = {"results": [{"sha256": "a", "status": "cancelled"}, {"sha256": "b", "status": "ok"}],
             "extracted": 1, "skipped": 0, "failed": 0,
             "usage": {"input_tokens": 100, "output_tokens": 50, "cost_usd": 0.01}}
    second = {"results": [{"sha256": "a", "status": "ok"}],
              "extracted": 1, "skipped": 0, "failed": 0, "rate_limited": False,
              "usage": {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.002}}
    merged = _merge_summary(first, second)
    assert merged["extracted"] == 2
    assert len(merged["results"]) == 2
    assert merged["usage"]["input_tokens"] == 110
    assert merged["usage"]["cost_usd"] == pytest.approx(0.012)
    assert merged["rate_limited"] is False   # non-accumulated fields reflect only the latest cycle


def test_merge_summary_dedupes_a_doc_retried_after_being_cancelled():
    """A doc cancelled by one cycle's rate limit keeps its queue file and is retried by the
    next cycle — its stale "cancelled" stub must not survive alongside its real outcome, or
    the final summary double-counts it (e.g. reporting a fully-extracted doc as also
    "not started")."""
    from watchdog.ops.ingest import _merge_summary
    first = {"results": [{"sha256": "a", "status": "cancelled"}],
             "extracted": 0, "skipped": 0, "failed": 0}
    second = {"results": [{"sha256": "a", "status": "ok"}],
              "extracted": 1, "skipped": 0, "failed": 0}
    merged = _merge_summary(first, second)
    assert merged["results"] == [{"sha256": "a", "status": "ok"}]


def test_merge_summary_first_call_returns_new_unchanged():
    from watchdog.ops.ingest import _merge_summary
    new = {"results": [], "extracted": 0, "skipped": 0, "failed": 0}
    assert _merge_summary(None, new) is new


def test_process_wait_rejects_claude_batch(wdg_home, tmp_path, monkeypatch):
    """--wait is submit-and-poll's opposite (block until done) — claude-batch already runs
    in the background, so the two don't compose (#271)."""
    from watchdog import auth as auth_module
    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    (wdg_home / "config.json").write_text(json.dumps({"extractor_model": "claude-batch:sonnet"}))
    skill_file = tmp_path / "pinned.md"
    skill_file.write_text("SKILL")

    with pytest.raises(SystemExit, match="isn't supported with claude-batch"):
        ing.process(vault, RunOptions(skip_warning=True, skill=str(skill_file), wait=True), finalize=True)


def test_process_wait_loops_until_rate_limit_clears(wdg_home, tmp_path, monkeypatch, capsys):
    """--wait resumes automatically after a rate limit: orchestrate.run is re-invoked once the
    (stubbed) wait completes, the lock is released at the end, and the printed summary reflects
    the total across both cycles, not just the last one."""
    from watchdog import auth as auth_module
    from watchdog.pipeline import orchestrate as orch_module
    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: False)
    summaries = [
        {"results": [{"sha256": "sha1", "filename": "a.pdf", "status": "cancelled"}],
         "extracted": 0, "skipped": 0, "failed": 0, "cancelled": True,
         "rate_limited": True, "stop_message": "limit", "rate_limit_resets_at": None,
         "quarantined": 0},
        {"results": [{"sha256": "sha1", "filename": "a.pdf", "status": "ok", "entity_count": 2}],
         "extracted": 1, "skipped": 0, "failed": 0, "cancelled": False,
         "rate_limited": False, "stop_message": None, "rate_limit_resets_at": None,
         "quarantined": 0},
    ]
    calls = []

    async def fake_run(*a, **k):
        calls.append(k)
        return summaries[len(calls) - 1]
    monkeypatch.setattr(orch_module, "run", fake_run)

    waited = []
    monkeypatch.setattr(ing, "_wait_for_rate_limit",
                        lambda lock_file, resets_at: waited.append(resets_at))

    ing.process(vault, RunOptions(skip_warning=True, wait=True), finalize=True)

    assert len(calls) == 2
    assert all(k.get("wait") is True for k in calls)
    assert waited == [None]
    assert not (vault / ".watchdog" / "registry" / ".processing-lock").exists()
    out = capsys.readouterr().out
    assert "1" in out and "extracted" in out   # merged total, not the first cycle's 0
    assert "not started" not in out            # sha1's cycle-1 "cancelled" stub must not survive
                                                # alongside its cycle-2 "ok" outcome


def test_process_no_wait_stops_on_rate_limit_without_looping(wdg_home, tmp_path, monkeypatch):
    """Processing without `wait` (the default), a rate-limited summary is reported once and orchestrate.run
    is not re-invoked — the opt-in flag must not change today's behavior (I4 spirit)."""
    from watchdog import auth as auth_module
    from watchdog.pipeline import orchestrate as orch_module

    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: False)

    calls = []

    async def fake_run(*a, **k):
        calls.append(k)
        return {"results": [], "extracted": 0, "skipped": 0, "failed": 0, "cancelled": True,
                "rate_limited": True, "stop_message": "limit", "rate_limit_resets_at": None,
                "quarantined": 0}
    monkeypatch.setattr(orch_module, "run", fake_run)
    monkeypatch.setattr(ing, "_wait_for_rate_limit",
                        lambda *a, **k: pytest.fail("must not wait when --wait is absent"))

    ing.process(vault, RunOptions(skip_warning=True), finalize=False)

    assert len(calls) == 1
    assert calls[0].get("wait") is False


def test_process_wait_stops_after_max_rate_limit_waits(wdg_home, tmp_path, monkeypatch):
    """`max_rate_limit_waits` (#559) bounds --wait's resume loop — an internal-only knob (no
    user-facing flag or configure key) for a caller that can't tolerate an unbounded stall, e.g.
    the benchmark harness. A run that keeps getting rate-limited must give up after N waits
    rather than loop forever, and the returned summary must still say `rate_limited: True` so the
    caller can tell it stopped short rather than completed."""
    from watchdog import auth as auth_module
    from watchdog.pipeline import orchestrate as orch_module

    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: False)

    calls = []

    async def fake_run(*a, **k):
        calls.append(k)
        # Never clears — every cycle comes back rate-limited, so the only thing that can stop
        # this loop is the bound itself.
        return {"results": [], "extracted": 0, "skipped": 0, "failed": 0, "cancelled": True,
                "rate_limited": True, "stop_message": "limit", "rate_limit_resets_at": None,
                "quarantined": 0}
    monkeypatch.setattr(orch_module, "run", fake_run)

    waited = []
    monkeypatch.setattr(ing, "_wait_for_rate_limit",
                        lambda lock_file, resets_at: waited.append(resets_at))

    summary = ing.process(vault, RunOptions(skip_warning=True, wait=True, max_rate_limit_waits=2), finalize=True)

    assert len(calls) == 3           # the initial attempt plus exactly 2 resumed waits
    assert len(waited) == 2
    assert summary["rate_limited"] is True
    assert summary["wait_count"] == 2


def test_process_wait_without_max_rate_limit_waits_is_unbounded(wdg_home, tmp_path, monkeypatch):
    """`max_rate_limit_waits` is never set on a real --wait run (no CLI flag reaches it) — `args()`
    here carries no such attribute, so `getattr(..., None)` must resolve to None and the loop must
    keep resuming past however many waits `test_process_wait_stops_after_max_rate_limit_waits`
    bounds at, exactly like `--wait` behaved before this knob existed."""
    from watchdog import auth as auth_module
    from watchdog.pipeline import orchestrate as orch_module

    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: False)

    limited = {"results": [], "extracted": 0, "skipped": 0, "failed": 0, "cancelled": True,
              "rate_limited": True, "stop_message": "limit", "rate_limit_resets_at": None,
              "quarantined": 0}
    cleared = {"results": [], "extracted": 0, "skipped": 0, "failed": 0, "cancelled": False,
              "rate_limited": False, "stop_message": None, "rate_limit_resets_at": None,
              "quarantined": 0}
    calls = []

    async def fake_run(*a, **k):
        calls.append(k)
        # Clears on the 4th attempt — past the bound the sibling test above would stop at (2
        # waits, 3 attempts), proving nothing implicitly capped this loop.
        return limited if len(calls) < 4 else cleared
    monkeypatch.setattr(orch_module, "run", fake_run)
    monkeypatch.setattr(ing, "_wait_for_rate_limit", lambda *a, **k: None)

    summary = ing.process(vault, RunOptions(skip_warning=True, wait=True), finalize=True)   # no max_rate_limit_waits attribute

    assert len(calls) == 4
    assert summary["rate_limited"] is False


def test_process_no_finalize_threads_skip_finalize_to_orchestrate_run(wdg_home, tmp_path, monkeypatch, capsys):
    """Setting `no_finalize` on args (as `cmd_extract` does) reaches `orchestrate.run` as
    `skip_finalize=True`, and the closing block tells the user how to finalize later instead of
    the usual "open a session" line."""
    from watchdog import auth as auth_module
    from watchdog.pipeline import orchestrate as orch_module

    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: False)

    calls = []

    async def fake_run(*a, **k):
        calls.append(k)
        return {"results": [{"sha256": "sha1", "filename": "a.pdf", "status": "ok", "entity_count": 1}],
                "extracted": 1, "skipped": 0, "failed": 0, "cancelled": False,
                "rate_limited": False, "stop_message": None, "rate_limit_resets_at": None,
                "quarantined": 0, "finalize_skipped": True}
    monkeypatch.setattr(orch_module, "run", fake_run)

    ing.process(vault, RunOptions(skip_warning=True), finalize=False)

    assert len(calls) == 1
    assert calls[0].get("skip_finalize") is True
    out = capsys.readouterr().out
    assert "Post-processing" in out


def test_process_wait_and_no_finalize_stops_once_queue_drains(wdg_home, tmp_path, monkeypatch):
    """`--wait` loops on a rate-limited *extraction*, not on finalize — with `no_finalize` set,
    finalize never runs at all, so once the queue finishes extracting cleanly the loop must stop
    after a single call rather than waiting for a finalize that will never happen."""
    from watchdog import auth as auth_module
    from watchdog.pipeline import orchestrate as orch_module

    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: False)

    calls = []

    async def fake_run(*a, **k):
        calls.append(k)
        return {"results": [{"sha256": "sha1", "filename": "a.pdf", "status": "ok", "entity_count": 1}],
                "extracted": 1, "skipped": 0, "failed": 0, "cancelled": False,
                "rate_limited": False, "stop_message": None, "rate_limit_resets_at": None,
                "quarantined": 0, "finalize_skipped": True}
    monkeypatch.setattr(orch_module, "run", fake_run)
    monkeypatch.setattr(ing, "_wait_for_rate_limit",
                        lambda *a, **k: pytest.fail("must not wait — nothing rate-limited"))

    ing.process(vault, RunOptions(skip_warning=True, wait=True), finalize=False)

    assert len(calls) == 1
    assert calls[0].get("wait") is True
    assert calls[0].get("skip_finalize") is True


def test_process_skip_briefing_threads_to_orchestrate_run(wdg_home, tmp_path, monkeypatch, capsys):
    """`--skip-briefing` reaches `orchestrate.run` as `skip_briefing=True` — synthesis and the
    timeline still run (`finalize_skipped` stays False), only the briefing model call is
    skipped, so the closing block shows the usual "open a session" line."""
    from watchdog import auth as auth_module
    from watchdog.pipeline import orchestrate as orch_module

    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: False)

    calls = []

    async def fake_run(*a, **k):
        calls.append(k)
        return {"results": [{"sha256": "sha1", "filename": "a.pdf", "status": "ok", "entity_count": 1}],
                "extracted": 1, "skipped": 0, "failed": 0, "cancelled": False,
                "rate_limited": False, "stop_message": None, "rate_limit_resets_at": None,
                "quarantined": 0, "finalize_skipped": False,
                "post_ingest": {"synthesized": 0, "timeline_collisions": 0, "briefing": None,
                                "briefing_skipped": True, "merged": [], "contradictions": []}}
    monkeypatch.setattr(orch_module, "run", fake_run)

    ing.process(vault, RunOptions(skip_warning=True, skip_briefing=True), finalize=True)

    assert len(calls) == 1
    assert calls[0].get("skip_briefing") is True
    out = capsys.readouterr().out
    assert "Briefing skipped" in out


def test_dig_limit_extracts_only_the_next_n_unextracted(wdg_home, tmp_path, monkeypatch, capsys):
    """`watchdog dig --limit N` (#696) hands orchestrate.run the next N queued documents that still
    need extracting — already-staged ones are skipped, not counted against the limit."""
    from watchdog import auth as auth_module
    from watchdog.pipeline import orchestrate as orch_module

    vault = _vault_with_queued_doc(tmp_path)
    qdir = vault / ".watchdog" / "queue"
    for sha in ("sha2", "sha3", "sha4"):
        (qdir / f"{sha}.json").write_text((qdir / "sha1.json").read_text().replace("sha1", sha))
    (vault / ".watchdog" / "extracted").mkdir(parents=True)
    (vault / ".watchdog" / "extracted" / "sha1.json").write_text("{}")   # already staged
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: False)
    calls = []

    async def fake_run(*a, **k):
        calls.append(k)
        return {"results": [], "extracted": 0, "skipped": 0, "failed": 0, "cancelled": False,
                "rate_limited": False, "stop_message": None, "rate_limit_resets_at": None,
                "quarantined": 0, "finalize_skipped": True}
    monkeypatch.setattr(orch_module, "run", fake_run)

    ing.process(vault, RunOptions(skip_warning=True, limit=2), finalize=False)

    assert calls[0]["only_shas"] == ["sha2", "sha3"]
    assert "extracting 2 of 4 queued documents" in (capsys.readouterr().out)


def test_limit_queue_force_ignores_staged_state(tmp_path):
    from watchdog.pipeline.ingest_setup import limit_queue
    queue = [{"sha256": s} for s in ("a", "b", "c")]
    (tmp_path / ".watchdog" / "extracted").mkdir(parents=True)
    (tmp_path / ".watchdog" / "extracted" / "a.json").write_text("{}")
    (tmp_path / ".watchdog" / "registry").mkdir(parents=True)
    (tmp_path / ".watchdog" / "registry" / "documents.json").write_text(json.dumps({"b": {}}))
    assert limit_queue(tmp_path, queue, 5) == [{"sha256": "c"}]          # staged + committed skipped
    assert limit_queue(tmp_path, queue, 2, force=True) == queue[:2]
    assert limit_queue(tmp_path, queue, None) == queue


def test_resolve_stage_keeps_colons_inside_the_model_id():
    """Ollama-style ids are `name:tag` — `local:qwen3:32b` must split at the first colon."""
    from watchdog.ops.ingest import resolve_stage as _resolve_stage
    assert _resolve_stage("local:qwen3:32b", None) == ("local", "qwen3:32b")
    assert _resolve_stage("openrouter:anthropic/claude-3.5-sonnet", None) == (
        "openrouter", "anthropic/claude-3.5-sonnet")


def test_process_and_finish_agree_on_finalizer_default(wdg_home, tmp_path, monkeypatch):
    """An unconfigured vault must finalize on the same model whether ingest finishes the
    batch itself or a separate post-processing completes it (#253)."""
    from watchdog import auth as auth_module
    from watchdog.pipeline import orchestrate as orch_module

    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})

    finalizer_defaults = {}
    orig_resolve = ing.resolve_stage

    class _Stop(Exception):
        pass

    def _boom(*a, **k):
        raise _Stop()

    # process: skip past the pending-finalization prompt, stop right after model
    # resolution (before the heavy ingest_setup/orchestrate pipeline runs).
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: False)
    calls = []
    monkeypatch.setattr(ing, "resolve_stage",
                         lambda *a, **k: calls.append(orig_resolve(*a, **k)) or calls[-1])
    monkeypatch.setattr("watchdog.pipeline.ingest_setup.run", _boom)
    with pytest.raises(_Stop):
        ing.process(vault, RunOptions(skip_warning=True), finalize=True)
    # resolve_plan resolves the extractor, the finalizer, then the classifier.
    finalizer_defaults["ingest"] = calls[1]

    # finish: needs a pending finalization to proceed past its early-return guard.
    calls.clear()
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: True)
    monkeypatch.setattr(ing, "_run_finalize", _boom)
    with pytest.raises(_Stop):
        ing.finish(vault, RunOptions())
    finalizer_defaults["finalize"] = calls[1]

    assert finalizer_defaults["ingest"] == finalizer_defaults["finalize"] == (None, "haiku")


def test_format_models_line_shows_concurrency_only_when_explicitly_set(monkeypatch):
    """`--concurrency` was silently dropped from the pre-run summary (#456) — it should only
    appear when the user actually passed it, not for a run left at the default/config value."""

    without = ing._format_models_line("haiku", "haiku", None, "sonnet", None, "haiku",
                                      extract_effort="medium", concurrency=None)
    assert "concurrency" not in without

    with_value = ing._format_models_line("haiku", "haiku", None, "sonnet", None, "haiku",
                                         extract_effort="medium", concurrency=2)
    assert "concurrency" in with_value
    assert "2" in with_value.splitlines()[-1]


def test_format_models_line_omits_finalizer_for_dig(monkeypatch):
    """processing always stops before finalization in the same run (#456) — showing which
    model would finalize is irrelevant noise there, unlike the bare guided walk or `ingest`,
    which do finalize inline and still need the row."""

    for_dig = ing._format_models_line("haiku", "haiku", None, "sonnet", None, "haiku",
                                      extract_effort="medium", is_dig=True)
    assert "finalizer" not in for_dig

    not_dig = ing._format_models_line("haiku", "haiku", None, "sonnet", None, "haiku",
                                      extract_effort="medium", is_dig=False)
    assert "finalizer" in not_dig


def test_format_models_line_shows_classify_effort_suffix():
    """D221: classify gained an effort knob once its default model (Luna) could actually take
    one — the classifier row must carry an "(effort: …)" suffix just like extractor/finalizer."""

    out = ing._format_models_line("openai:gpt-5.6-luna", "gpt-5.6-luna", None, "sonnet",
                                   None, "haiku", classify_effort="low")
    lines = {line.split()[0]: line for line in out.splitlines()}
    assert "(effort: low)" in lines["classifier"]


def test_process_keyboard_interrupt_mentions_quarantined_docs(wdg_home, tmp_path, monkeypatch, capsys):
    """A Ctrl+C that propagates as a real `KeyboardInterrupt` — the only path a Ctrl+C during
    finalize's sequential post-processing takes (#406), since `orchestrate.run`'s own SIGINT
    handling only covers concurrent extraction — must still mention a document already
    quarantined earlier in the same run. Previously only the normal-completion summary did."""
    from watchdog import auth as auth_module
    from watchdog.pipeline import orchestrate as orch_module

    vault = _vault_with_queued_doc(tmp_path)
    failed_dir = vault / ".watchdog" / "queue" / "_failed"
    failed_dir.mkdir(parents=True, exist_ok=True)
    (failed_dir / "shafail.json").write_text(json.dumps({"sha256": "shafail", "filename": "bad.pdf"}))
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: False)

    async def boom(*a, **k):
        raise KeyboardInterrupt()
    monkeypatch.setattr(orch_module, "run", boom)

    with pytest.raises(SystemExit) as exc_info:
        ing.process(vault, RunOptions(skip_warning=True), finalize=True)
    assert exc_info.value.code == 130

    out = (capsys.readouterr().out)
    assert "Stopped" in out
    assert "Requeue failed documents" in out


def test_caffeinate_spawns_on_darwin_when_available(monkeypatch):

    monkeypatch.setattr(ing.sys, "platform", "darwin")
    monkeypatch.setattr(ing.shutil, "which",
                        lambda name: "/usr/bin/caffeinate" if name == "caffeinate" else None)

    fake_proc = _FakeProc()
    calls = []

    def fake_popen(cmd, **kwargs):
        calls.append(cmd)
        return fake_proc
    monkeypatch.setattr(ing.subprocess, "Popen", fake_popen)

    with ing._caffeinate():
        assert calls == [["caffeinate", "-i", "-w", str(ing.os.getpid())]]
        assert not fake_proc.terminated
    assert fake_proc.terminated
    assert fake_proc.waited


def test_caffeinate_spawns_systemd_inhibit_on_linux_when_available(monkeypatch):
    """Linux has no macOS-style "watch this pid" flag, so `systemd-inhibit` wraps a placeholder
    command instead (#415) — killing the whole process group on the way out, not just the
    `systemd-inhibit` process itself, so that placeholder isn't left running as an orphan."""

    monkeypatch.setattr(ing.sys, "platform", "linux")
    monkeypatch.setattr(ing.shutil, "which",
                        lambda name: "/usr/bin/systemd-inhibit" if name == "systemd-inhibit" else None)

    fake_proc = _FakeProc(pid=54321)
    calls = []

    def fake_popen(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return fake_proc
    monkeypatch.setattr(ing.subprocess, "Popen", fake_popen)
    killpg_calls = []
    monkeypatch.setattr(ing.os, "killpg", lambda pid, sig: killpg_calls.append((pid, sig)))

    with ing._caffeinate():
        assert len(calls) == 1
        cmd, kwargs = calls[0]
        assert cmd[0] == "systemd-inhibit"
        assert kwargs.get("start_new_session") is True
        assert not killpg_calls

    assert killpg_calls == [(54321, ing.signal.SIGTERM)]
    assert fake_proc.waited
    assert not fake_proc.terminated   # cleanup goes through the process group, not proc.terminate()


def test_caffeinate_systemd_inhibit_sigkills_the_group_on_timeout(monkeypatch):
    """If the group doesn't die from SIGTERM in time, the SIGKILL fallback must also target the
    whole process group — not just `proc.kill()` on the `systemd-inhibit` process itself, which
    would risk leaving the `sleep infinity` placeholder running as an orphan (PR #437 review)."""

    monkeypatch.setattr(ing.sys, "platform", "linux")
    monkeypatch.setattr(ing.shutil, "which",
                        lambda name: "/usr/bin/systemd-inhibit" if name == "systemd-inhibit" else None)

    class _StuckFakeProc(_FakeProc):
        def __init__(self, pid):
            super().__init__(pid=pid)
            self.wait_calls = 0

        def wait(self, timeout=None):
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise ing.subprocess.TimeoutExpired(cmd="systemd-inhibit", timeout=timeout)

    fake_proc = _StuckFakeProc(pid=54321)
    monkeypatch.setattr(ing.subprocess, "Popen", lambda *a, **k: fake_proc)
    killpg_calls = []
    monkeypatch.setattr(ing.os, "killpg", lambda pid, sig: killpg_calls.append((pid, sig)))

    with ing._caffeinate():
        pass

    assert killpg_calls == [(54321, ing.signal.SIGTERM), (54321, ing.signal.SIGKILL)]
    assert not fake_proc.killed   # the group kill replaces proc.kill(), not alongside it
    assert fake_proc.wait_calls == 2


def test_caffeinate_noop_on_windows(monkeypatch):
    """Neither darwin's nor Linux's tool applies on Windows — there's no CLI equivalent worth
    shelling out to (#415), so this stays a pure no-op there."""

    monkeypatch.setattr(ing.sys, "platform", "win32")
    monkeypatch.setattr(ing.subprocess, "Popen",
                        lambda *a, **k: pytest.fail("must not spawn anything on Windows"))

    with ing._caffeinate():
        pass


def test_caffeinate_noop_on_linux_without_systemd_inhibit(monkeypatch):

    monkeypatch.setattr(ing.sys, "platform", "linux")
    monkeypatch.setattr(ing.shutil, "which", lambda name: None)
    monkeypatch.setattr(ing.subprocess, "Popen",
                        lambda *a, **k: pytest.fail("must not spawn when systemd-inhibit isn't on PATH"))

    with ing._caffeinate():
        pass


def test_caffeinate_noop_when_binary_missing(monkeypatch):

    monkeypatch.setattr(ing.sys, "platform", "darwin")
    monkeypatch.setattr(ing.shutil, "which", lambda name: None)
    monkeypatch.setattr(ing.subprocess, "Popen",
                        lambda *a, **k: pytest.fail("must not spawn when caffeinate isn't on PATH"))

    with ing._caffeinate():
        pass


def test_caffeinate_terminates_even_if_the_block_raises(monkeypatch):
    """The context manager's cleanup must run on an exception path too (e.g. the KeyboardInterrupt
    that stops an ingest, #415) — not just on a clean exit, and it must not swallow that
    exception (a bare `return` inside `finally` would)."""

    monkeypatch.setattr(ing.sys, "platform", "darwin")
    monkeypatch.setattr(ing.shutil, "which", lambda name: "/usr/bin/caffeinate")

    fake_proc = _FakeProc()
    monkeypatch.setattr(ing.subprocess, "Popen", lambda *a, **k: fake_proc)

    with pytest.raises(KeyboardInterrupt):
        with ing._caffeinate():
            raise KeyboardInterrupt()
    assert fake_proc.terminated


def test_caffeinate_noop_does_not_swallow_exception(monkeypatch):
    """Same exception-safety property as above, but for the no-op path (`proc is None`) — this
    is the branch a bare `return` in `finally` would have silently broken."""

    monkeypatch.setattr(ing.sys, "platform", "win32")

    with pytest.raises(ValueError):
        with ing._caffeinate():
            raise ValueError("boom")


def test_run_finalize_wraps_model_call_in_caffeinate(tmp_path, monkeypatch):
    """#467: post-processing had no `_caffeinate()` wrap at all, unlike `cmd_ingest`'s extraction
    loop — a bark run stopped none of the machine sleeping mid-call, the same failure mode #415
    guarded extraction against. `_run_finalize`'s model call must run inside the same guard."""
    from watchdog.pipeline import orchestrate as orch_module
    from tests.test_write_vault import make_vault
    vault = make_vault(tmp_path)

    calls = []

    @contextlib.contextmanager
    def fake_caffeinate():
        calls.append("enter")
        yield
        calls.append("exit")

    monkeypatch.setattr(ing, "_caffeinate", fake_caffeinate)

    async def fake_finalize(*a, **k):
        assert calls == ["enter"]   # the model call must run inside the caffeinate block
        return {"synthesized": 0}
    monkeypatch.setattr(orch_module, "finalize", fake_finalize)

    ing._run_finalize(vault, "haiku")

    assert calls == ["enter", "exit"]


def test_resolve_pinned_skill_from_flag(tmp_path, monkeypatch):
    pkg = _fake_catalog(monkeypatch, tmp_path)
    assert ing._resolve_pinned_skill(RunOptions(skill="corporate-filings"), {}) == str(pkg / "corporate-filings.md")


def test_resolve_pinned_skill_strips_md_suffix(tmp_path, monkeypatch):
    pkg = _fake_catalog(monkeypatch, tmp_path)
    assert ing._resolve_pinned_skill(RunOptions(skill="corporate-filings.md"), {}) == str(pkg / "corporate-filings.md")


def test_resolve_pinned_skill_unknown_exits(tmp_path, monkeypatch):
    _fake_catalog(monkeypatch, tmp_path)
    with pytest.raises(SystemExit, match="no record skill"):
        ing._resolve_pinned_skill(RunOptions(skill="nope"), {})


def test_resolve_pinned_skill_from_config(tmp_path, monkeypatch):
    pkg = _fake_catalog(monkeypatch, tmp_path)
    assert ing._resolve_pinned_skill(RunOptions(), {"default_skill": "general-records"}) == str(pkg / "general-records.md")


def test_resolve_pinned_skill_none_classifies(tmp_path, monkeypatch):
    _fake_catalog(monkeypatch, tmp_path)
    assert ing._resolve_pinned_skill(RunOptions(), {}) is None


def test_resolve_pinned_skill_explicit_file_path(tmp_path, monkeypatch):
    _fake_catalog(monkeypatch, tmp_path)
    custom = tmp_path / "custom-skill.md"
    custom.write_text("body")
    assert ing._resolve_pinned_skill(RunOptions(skill=str(custom)), {}) == str(custom.resolve())


def _dig_run_kwargs(tmp_path, monkeypatch, capsys, **opts):
    """Run processing with the orchestrator stubbed, returning the kwargs it was called with."""
    from watchdog import auth as auth_module
    from watchdog.pipeline import orchestrate as orch_module

    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: False)
    calls = []

    async def fake_run(*a, **k):
        calls.append(k)
        return {"results": [], "extracted": 0, "skipped": 0, "failed": 0, "cancelled": False,
                "rate_limited": False, "stop_message": None, "rate_limit_resets_at": None,
                "quarantined": 0, "finalize_skipped": True}
    monkeypatch.setattr(orch_module, "run", fake_run)

    ing.process(vault, RunOptions(skip_warning=True, **opts), finalize=False)
    capsys.readouterr()
    return calls[0]


def test_verification_pass_is_off_unless_asked_for(wdg_home, tmp_path, monkeypatch, capsys):
    assert _dig_run_kwargs(tmp_path, monkeypatch, capsys)["verify"] is False


def test_verify_flag_turns_the_pass_on(wdg_home, tmp_path, monkeypatch, capsys):
    assert _dig_run_kwargs(tmp_path, monkeypatch, capsys, verify=True)["verify"] is True


def test_verify_extraction_config_key_turns_the_pass_on(wdg_home, tmp_path, monkeypatch, capsys):
    (wdg_home / "config.json").write_text(json.dumps({"verify_extraction": True}))
    assert _dig_run_kwargs(tmp_path, monkeypatch, capsys)["verify"] is True


def test_no_verify_flag_beats_the_config_key(wdg_home, tmp_path, monkeypatch, capsys):
    (wdg_home / "config.json").write_text(json.dumps({"verify_extraction": True}))
    assert _dig_run_kwargs(tmp_path, monkeypatch, capsys, verify=False)["verify"] is False


def test_verify_is_refused_with_a_claude_batch_extractor(wdg_home, tmp_path, monkeypatch, capsys):
    """A batch's results come back hours later in a separate process — there is no extraction to
    verify at the time the pass would run, and no cached prefix left to read."""
    from watchdog import auth as auth_module
    from watchdog.pipeline import orchestrate as orch_module

    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: False)

    with pytest.raises(SystemExit) as e:
        ing.process(vault, RunOptions(skip_warning=True, verify=True, extractor_model="claude-batch:sonnet"), finalize=False)
    assert "verification isn't supported with claude-batch" in str(e.value)


def test_the_batch_refusal_names_the_config_key_when_that_is_what_turned_it_on(
        wdg_home, tmp_path, monkeypatch, capsys):
    """Verification turned on in Settings is refused with a batch extractor too, and the message
    says how to turn it off."""
    from watchdog import auth as auth_module
    from watchdog.pipeline import orchestrate as orch_module

    (wdg_home / "config.json").write_text(json.dumps({"verify_extraction": True}))
    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.chdir(vault)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: False)

    with pytest.raises(SystemExit) as e:
        ing.process(vault, RunOptions(skip_warning=True, extractor_model="claude-batch:sonnet"), finalize=False)
    message = (str(e.value))
    assert "verification isn't supported with claude-batch" in message
    assert "Turn verification off" in message


@pytest.mark.parametrize("result, expected", [
    (None, 0),
    ("not a dict", 0),
    ([], 0),
    ({"extracted": 1, "skipped": 0, "failed": 0, "results": []}, 0),
    ({"extracted": 1, "results": [], "rate_limited": True}, 2),
    ({"extracted": 0, "results": [], "batch_pending": True}, 2),
    ({"extracted": 1, "results": [], "cancelled": True}, 2),
    ({"extracted": 1, "results": [{"sha256": "a", "status": "cancelled"}]}, 2),
    ({"extracted": 1, "results": [], "post_ingest_error": "rate limited"}, 2),
    ({"extracted": 1, "results": [], "post_ingest": {"error": "rate limited"}}, 2),
    ({"synthesized": 0, "error": "rate limited"}, 2),
    ({"synthesized": 0, "briefing_error": "rate limited"}, 2),
    ({"extracted": 1, "results": [], "quarantined": 3, "failed": 2}, 0),
    # An auth/billing stop is not resumable by re-running, wherever it surfaced (D259).
    ({"synthesized": 0, "error": "refused", "auth_error": "refused"}, 1),
    ({"extracted": 1, "results": [], "post_ingest": {"error": "x", "auth_error": "x"}}, 1),
    ({"extracted": 0, "results": [], "batch_pending": True, "auth_error": "x"}, 1),
])
def test_exit_code_for(result, expected):
    assert ing.exit_code_for(result) == expected




def test_adding_with_nothing_new_finishes_a_pending_batch(wdg_home, tmp_path, monkeypatch):
    """A batch staged earlier is never thrown away (D242): adding documents with nothing new to
    extract finishes it now."""
    from watchdog import auth as auth_module
    from watchdog.pipeline import orchestrate as orch_module
    from watchdog.pipeline import ingest_setup
    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: True)
    monkeypatch.setattr(orch_module, "pending_finalization", lambda v: {"docs": 1, "entities": 0})
    monkeypatch.setattr(ingest_setup, "needs_extraction", lambda v, q: [])
    calls = []
    monkeypatch.setattr(ing, "_run_finalize", lambda *a, **k: calls.append(k) or {"synthesized": 0})
    monkeypatch.setattr(orch_module, "run", lambda *a, **k: pytest.fail("nothing to extract"))
    ing.process(vault, RunOptions(skip_warning=True, skip_briefing=True), finalize=True)
    assert len(calls) == 1 and calls[0]["skip_briefing"] is True


def test_processing_only_with_nothing_new_leaves_a_pending_batch_for_later(wdg_home, tmp_path, monkeypatch):
    from watchdog import auth as auth_module
    from watchdog.pipeline import orchestrate as orch_module
    from watchdog.pipeline import ingest_setup
    vault = _vault_with_queued_doc(tmp_path)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orch_module, "has_pending_finalization", lambda v: True)
    monkeypatch.setattr(orch_module, "pending_finalization", lambda v: {"docs": 1, "entities": 0})
    monkeypatch.setattr(ingest_setup, "needs_extraction", lambda v, q: [])
    monkeypatch.setattr(ing, "_run_finalize", lambda *a, **k: pytest.fail("processing only never finalizes"))
    assert ing.process(vault, RunOptions(skip_warning=True), finalize=False) is None
