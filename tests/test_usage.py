"""Tests for `watchdog usage` (#319) — the per-call token/cost/latency breakdown that folds
the former standalone `scripts/analyze-session` dev tool into the CLI."""

import argparse
import json
from pathlib import Path

import pytest

from watchdog.cmd.usage import cmd_usage


def _call(task="extract", filename="doc.pdf", detail="pages 1–1", model="claude-sonnet-4-6",
          cost_usd=0.01, input_tokens=100, output_tokens=20, latency_s=1.5,
          effort=None, auth_mode="api-key", attempts=1, end_ts=None, api_ms=None, num_turns=None,
          failed=False, backend="claude-api"):
    call = {
        "task": task, "model": model, "backend": backend,
        "input_tokens": input_tokens, "output_tokens": output_tokens,
        "cache_read_tokens": 0, "cache_write_tokens": 0,
        "cost_usd": cost_usd, "attempts": attempts, "latency_s": latency_s, "effort": effort,
        "auth_mode": auth_mode, "filename": filename, "detail": detail, "end_ts": end_ts,
    }
    # api_ms/num_turns (#402) are only present on claude-agent-sdk records — omitted here by
    # default so a plain _call() matches a real raw-API record with no such keys at all.
    if api_ms is not None:
        call["api_ms"] = api_ms
    if num_turns is not None:
        call["num_turns"] = num_turns
    if failed:
        call["failed"] = True
    return call


def _build_vault(tmp_path: Path, *, runs: dict[str, list[dict]], documents=None) -> Path:
    """A vault with one usage-<stem>.json per entry in `runs` (stem -> list of call dicts)."""
    vault = tmp_path / "vault"
    registry = vault / ".watchdog" / "registry"
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
    (vault / ".watchdog" / "registry").mkdir(parents=True)
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


def test_cmd_usage_shows_api_time_alongside_latency_when_present(tmp_path, monkeypatch, capsys):
    """#402: a claude-agent-sdk call's harness timing (api_ms/num_turns) surfaces next to the
    wall-clock Latency figure — a large latency-vs-api gap is the throttling signature this
    exists to reveal. A call with no api_ms (e.g. a raw-API backend) shows nothing extra."""
    vault = _build_vault(tmp_path, runs={
        "usage-2026-01-01T00-00-00": [
            _call(filename="throttled.pdf", latency_s=2621.0, api_ms=726_000, num_turns=5),
            _call(filename="plain.pdf", latency_s=10.0),
        ],
    })
    monkeypatch.chdir(vault)

    cmd_usage(_args())

    out = capsys.readouterr().out
    assert "api 12.1m" in out       # 726_000ms == 12.1 minutes
    assert "5 turns" in out
    throttled_line = next(line for line in out.splitlines() if "throttled.pdf" in line)
    plain_line = next(line for line in out.splitlines() if "plain.pdf" in line)
    assert "api" in throttled_line
    assert "api" not in plain_line   # no harness timing on this call — nothing appended


def test_cmd_usage_omits_turns_note_when_a_single_turn(tmp_path, monkeypatch, capsys):
    """A single-turn call is the healthy baseline — noting `1 turns` on every row would just be
    noise, so the turns count is only appended when it deviates from 1."""
    vault = _build_vault(tmp_path, runs={
        "usage-2026-01-01T00-00-00": [_call(filename="healthy.pdf", api_ms=5000, num_turns=1)],
    })
    monkeypatch.chdir(vault)

    cmd_usage(_args())

    out = capsys.readouterr().out
    row = next(line for line in out.splitlines() if "healthy.pdf" in line)
    assert "api 5.0s" in row
    assert "turns" not in row


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
    assert "up to 3 concurrent" in out         # all three genuinely overlap at t=99 (#457)
    assert "5.0s elapsed" in out.split("TOTAL")[1]   # and on the TOTAL line


def test_cmd_usage_peak_concurrency_not_just_call_count(tmp_path, monkeypatch, capsys):
    """#457: the old message reported `len(calls)` as "N calls ran concurrently", which
    overstates it whenever a run was concurrency-capped below the stage's total call count —
    four calls that ran two at a time still extend the wall-clock span past one call's latency
    (so the line fires) and total 4, even though only 2 were ever in flight together. Four 2s
    calls in two back-to-back overlapping pairs (a/b end at t=10/11, c/d end at t=13/14) never
    have more than 2 overlapping at once."""
    vault = _build_vault(tmp_path, runs={
        "usage-2026-01-01T00-00-00": [
            _call(filename="a.pdf", latency_s=2.0, end_ts=10.0),
            _call(filename="b.pdf", latency_s=2.0, end_ts=11.0),
            _call(filename="c.pdf", latency_s=2.0, end_ts=13.0),
            _call(filename="d.pdf", latency_s=2.0, end_ts=14.0),
        ],
    })
    monkeypatch.chdir(vault)

    cmd_usage(_args())

    out = capsys.readouterr().out
    assert "4 calls, up to 2 concurrent" in out


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


def test_cmd_usage_shows_failed_marker_and_includes_in_subtotal(tmp_path, monkeypatch, capsys):
    """#412/D125: a call that never returned valid JSON is recorded with `failed: true` rather
    than vanishing from telemetry — it gets a visibly distinct marker, and its tokens/cost still
    count toward the stage subtotal (it was real spend)."""
    vault = _build_vault(tmp_path, runs={
        "usage-2026-01-01T00-00-00": [
            _call(filename="ok.pdf", cost_usd=0.01, input_tokens=100),
            _call(filename="broke.pdf", cost_usd=0.02, input_tokens=200, attempts=2, failed=True),
        ],
    })
    monkeypatch.chdir(vault)

    cmd_usage(_args())

    out = capsys.readouterr().out
    failed_line = next(line for line in out.splitlines() if "broke.pdf" in line)
    ok_line = next(line for line in out.splitlines() if "ok.pdf" in line)
    assert "failed" in failed_line
    assert "failed" not in ok_line
    assert "$0.0300" in out   # subtotal includes the failed call's cost (0.01 + 0.02)


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


def test_cmd_usage_flags_local_backend_as_not_actually_free(tmp_path, monkeypatch, capsys):
    """#380: a `local` call's $0 cost is real but reads as "free" at a glance — the stage header
    gets a note pointing at Latency as the real cost signal. A stage with no local calls gets no
    such note."""
    vault = _build_vault(tmp_path, runs={
        "usage-2026-01-01T00-00-00": [
            _call(task="extract", model="llama-3.3-70b", backend="local", cost_usd=0.0),
            _call(task="classify", model="gemini-3.1-flash-lite", cost_usd=0.001),
        ],
    })
    monkeypatch.chdir(vault)

    cmd_usage(_args())

    out = capsys.readouterr().out
    assert "local model — no per-token cost; Latency is the real cost signal here" in out
    classifier_section, extractor_section = out.split("EXTRACTOR")
    assert "local model" not in classifier_section
    assert "local model" in extractor_section


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


def test_cmd_usage_cost_per_page_from_extracted_artifacts_without_registry(tmp_path, monkeypatch, capsys):
    """A dig-only vault (#457/#461) has never committed anything — no documents.json — but
    `watchdog dig` already staged `.watchdog/extracted/<sha>.json` for each document, carrying
    the same page_count. Cost/page must work from that alone rather than require a commit."""
    vault = _build_vault(tmp_path, runs={"usage-2026-01-01T00-00-00": [_call(cost_usd=1.0)]})
    extracted = vault / ".watchdog" / "extracted"
    extracted.mkdir(parents=True)
    (extracted / "sha1.json").write_text(json.dumps({"document": {"page_count": 10}}))
    (extracted / "sha2.json").write_text(json.dumps({"document": {"page_count": 10}}))
    monkeypatch.chdir(vault)

    cmd_usage(_args())

    out = capsys.readouterr().out
    assert "20 pages across 2 documents" in out
    assert "$0.0500" in out   # 1.0 / 20 pages


def test_cmd_usage_no_extracted_or_registry_reports_unavailable(tmp_path, monkeypatch, capsys):
    vault = _build_vault(tmp_path, runs={"usage-2026-01-01T00-00-00": [_call(cost_usd=1.0)]})
    monkeypatch.chdir(vault)

    cmd_usage(_args())

    out = capsys.readouterr().out
    assert "unavailable" in out


def test_cmd_usage_per_call_cost_per_page(tmp_path, monkeypatch, capsys):
    """#457: a "$/pg" column per row, parsed from that call's own page range in `detail` — not
    the whole-corpus figure. A digest call (no page range) shows "—" instead of a bogus value."""
    vault = _build_vault(tmp_path, runs={
        "usage-2026-01-01T00-00-00": [
            _call(filename="a.pdf", detail="pages 1–36", cost_usd=0.36),
            _call(filename="b.pdf", detail="digest", cost_usd=0.10),
        ],
    })
    monkeypatch.chdir(vault)

    cmd_usage(_args())

    out = capsys.readouterr().out
    lines = out.splitlines()
    a_row = next(line for line in lines if "a.pdf" in line)
    b_row = next(line for line in lines if "b.pdf" in line)
    assert "$0.0100" in a_row   # 0.36 / 36 pages
    assert "—" in b_row
