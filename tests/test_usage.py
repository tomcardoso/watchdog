"""Tests for `watchdog usage` (#319) — the per-call token/cost/latency breakdown that folds
the former standalone `scripts/analyze-session` dev tool into the CLI."""

import argparse
import json
from pathlib import Path

import pytest

from watchdog.cmd.usage import cmd_usage


def _call(task="extract", filename="doc.pdf", detail="pages 1–1", model="claude-sonnet-4-6",
          cost_usd=0.01, input_tokens=100, output_tokens=20, latency_s=1.5,
          effort=None, auth_mode="api-key", attempts=1, end_ts=None):
    return {
        "task": task, "model": model, "backend": "claude-api",
        "input_tokens": input_tokens, "output_tokens": output_tokens,
        "cache_read_tokens": 0, "cache_write_tokens": 0,
        "cost_usd": cost_usd, "attempts": attempts, "latency_s": latency_s, "effort": effort,
        "auth_mode": auth_mode, "filename": filename, "detail": detail, "end_ts": end_ts,
    }


def _build_vault(tmp_path: Path, *, runs: dict[str, list[dict]], documents=None) -> Path:
    """A vault with one usage-<stem>.json per entry in `runs` (stem -> list of call dicts)."""
    vault = tmp_path / "vault"
    registry = vault / ".watchdog" / "Registry"
    registry.mkdir(parents=True)
    for stem, calls in runs.items():
        (registry / f"{stem}.json").write_text(json.dumps({"calls": calls}), encoding="utf-8")
    if documents is not None:
        (registry / "documents.json").write_text(json.dumps(documents), encoding="utf-8")
    return vault


def _args(project=None, all_runs=False, run=None):
    return argparse.Namespace(project=project, all=all_runs, run=run)


def test_cmd_usage_no_runs_yet(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / ".watchdog" / "Registry").mkdir(parents=True)
    monkeypatch.chdir(vault)
    with pytest.raises(SystemExit, match="no ingest runs recorded"):
        cmd_usage(_args())


def test_cmd_usage_defaults_to_latest_run(tmp_path, monkeypatch, capsys):
    vault = _build_vault(tmp_path, runs={
        "usage-2026-01-01T00-00-00": [_call(task="classify", cost_usd=0.001, latency_s=0.5)],
        "usage-2026-02-01T00-00-00": [_call(task="extract", cost_usd=0.02, latency_s=3.0)],
    })
    monkeypatch.chdir(vault)

    cmd_usage(_args())

    out = capsys.readouterr().out
    assert "usage-2026-02-01T00-00-00" in out   # the later run, not the earlier one
    assert "usage-2026-01-01T00-00-00" not in out
    assert "EXTRACTOR" in out
    assert "doc.pdf" in out and "pages 1–1" in out
    assert "3.0s" in out   # latency surfaced
    assert "$0.0200" in out


def test_cmd_usage_shows_auth_mode_and_retry_marker(tmp_path, monkeypatch, capsys):
    """#319: auth_mode (which billing lane paid for a call) and a retry marker for any call
    that needed more than one attempt are both surfaced per call."""
    vault = _build_vault(tmp_path, runs={
        "usage-2026-01-01T00-00-00": [
            _call(filename="sub.pdf", auth_mode="subscription", attempts=1, cost_usd=0.01),
            _call(filename="key.pdf", auth_mode="api-key", attempts=3, cost_usd=0.02),
        ],
    })
    monkeypatch.chdir(vault)

    cmd_usage(_args())

    out = capsys.readouterr().out
    assert "Auth" in out
    assert "sub" in out and "key" in out
    assert "×3" in out   # the retried call is flagged inline


def test_cmd_usage_shows_wall_clock_elapsed_for_concurrent_stage(tmp_path, monkeypatch, capsys):
    """When a stage's calls overlapped in time, the summed Latency column overstates the stage's
    real duration — the wall-clock span (max end − min start) is shown alongside it (#317
    follow-up). Three 3s calls finishing at t=100/101/102 span only 5s of wall time, not 9s."""
    vault = _build_vault(tmp_path, runs={
        "usage-2026-01-01T00-00-00": [
            _call(filename="a.pdf", latency_s=3.0, end_ts=100.0),
            _call(filename="b.pdf", latency_s=3.0, end_ts=101.0),
            _call(filename="c.pdf", latency_s=3.0, end_ts=102.0),
        ],
    })
    monkeypatch.chdir(vault)

    cmd_usage(_args())

    out = capsys.readouterr().out
    assert "9.0s" in out                       # summed call time (subtotal)
    assert "5.0s elapsed" in out               # wall-clock span, per stage
    assert "concurrently" in out
    assert "5.0s elapsed" in out.split("TOTAL")[1]   # and on the TOTAL line


def test_cmd_usage_omits_wall_clock_when_no_end_ts(tmp_path, monkeypatch, capsys):
    """Usage files written before end_ts was recorded have no wall-clock data — the elapsed
    line is omitted rather than guessed, and the summed call time still prints."""
    vault = _build_vault(tmp_path, runs={
        "usage-2026-01-01T00-00-00": [
            _call(filename="a.pdf", latency_s=3.0),
            _call(filename="b.pdf", latency_s=3.0),
        ],
    })
    monkeypatch.chdir(vault)

    cmd_usage(_args())

    out = capsys.readouterr().out
    assert "elapsed" not in out
    assert "call time" in out


def test_cmd_usage_all_compares_every_run(tmp_path, monkeypatch, capsys):
    vault = _build_vault(tmp_path, runs={
        "usage-2026-01-01T00-00-00": [_call(cost_usd=0.01, latency_s=1.0)],
        "usage-2026-02-01T00-00-00": [_call(cost_usd=0.02, latency_s=2.0)],
    })
    monkeypatch.chdir(vault)

    cmd_usage(_args(all_runs=True))

    out = capsys.readouterr().out
    assert "usage-2026-01-01T00-00-00" in out
    assert "usage-2026-02-01T00-00-00" in out
    assert "TOTAL" in out
    assert "0.0300" in out   # grand total cost


def test_cmd_usage_run_flag_picks_specific_run(tmp_path, monkeypatch, capsys):
    vault = _build_vault(tmp_path, runs={
        "usage-2026-01-01T00-00-00": [_call(filename="jan.pdf", cost_usd=0.01)],
        "usage-2026-02-01T00-00-00": [_call(filename="feb.pdf", cost_usd=0.02)],
    })
    monkeypatch.chdir(vault)

    cmd_usage(_args(run="2026-01-01"))

    out = capsys.readouterr().out
    assert "jan.pdf" in out
    assert "feb.pdf" not in out


def test_cmd_usage_shows_full_model_name_next_to_stage(tmp_path, monkeypatch, capsys):
    """The model column used to abbreviate by splitting on '-' and taking the second segment,
    which worked for 'claude-sonnet-4-6' -> 'sonnet' but mangled non-Claude ids like
    'gemini-3.1-flash-lite' into '3.1'. The full model name is now printed once next to the
    stage header instead, and there's no more per-row Model column."""
    vault = _build_vault(tmp_path, runs={
        "usage-2026-01-01T00-00-00": [
            _call(task="classify", model="gemini-3.1-flash-lite", cost_usd=0.001),
        ],
    })
    monkeypatch.chdir(vault)

    cmd_usage(_args())

    out = capsys.readouterr().out
    assert "CLASSIFIER" in out and "model: gemini-3.1-flash-lite" in out
    assert "  Model  " not in out   # no more per-row Model column


def test_cmd_usage_reconcile_task_groups_under_finalizer(tmp_path, monkeypatch, capsys):
    """The reconcile call (#381/D118) is a post-ingest step like synthesis/timeline/briefing, so
    it must group under FINALIZER — not render as its own stray stage section — and its cost must
    count toward the finalizer column in the `--all` comparison."""
    vault = _build_vault(tmp_path, runs={
        "usage-2026-01-01T00-00-00": [
            _call(task="reconcile", filename="reconcile", detail="2 entities · 1 pairs",
                  cost_usd=0.01),
            _call(task="entity-synthesis", filename="entity-synthesis", cost_usd=0.02),
        ],
    })
    monkeypatch.chdir(vault)

    cmd_usage(_args())
    out = capsys.readouterr().out
    assert "FINALIZER" in out
    assert "RECONCILE" not in out

    capsys.readouterr()
    cmd_usage(_args(all_runs=True))
    out = capsys.readouterr().out
    # Run row: Run | Calls | Classifier | Extractor | Finalizer | ... | Cost — the Finalizer
    # column must carry both calls' cost (0.03), not just entity-synthesis's (0.02), or the
    # stage columns stop summing to the Cost column on the right.
    run_row = next(line for line in out.splitlines() if "usage-2026-01-01" in line)
    dollars = [float(v) for v in run_row.replace("$", "").split()[2:5]]
    assert dollars == [0.0, 0.0, 0.03]   # classifier, extractor, finalizer


def test_cmd_usage_run_flag_no_match(tmp_path, monkeypatch):
    vault = _build_vault(tmp_path, runs={"usage-2026-01-01T00-00-00": [_call()]})
    monkeypatch.chdir(vault)
    with pytest.raises(SystemExit, match="no run matching"):
        cmd_usage(_args(run="2099-01-01"))


def test_cmd_usage_run_flag_ambiguous(tmp_path, monkeypatch):
    vault = _build_vault(tmp_path, runs={
        "usage-2026-01-01T00-00-00": [_call()],
        "usage-2026-01-01T00-00-01": [_call()],
    })
    monkeypatch.chdir(vault)
    with pytest.raises(SystemExit, match="Ambiguous run"):
        cmd_usage(_args(run="2026-01-01"))


def test_cmd_usage_cost_per_page_from_documents_registry(tmp_path, monkeypatch, capsys):
    vault = _build_vault(
        tmp_path,
        runs={"usage-2026-01-01T00-00-00": [_call(cost_usd=1.0)]},
        documents={"sha1": {"page_count": 10}, "sha2": {"page_count": 10}},
    )
    monkeypatch.chdir(vault)

    cmd_usage(_args())

    out = capsys.readouterr().out
    assert "20 pages across 2 documents" in out
    assert "$0.0500" in out   # 1.0 / 20 pages
