"""`usage.*` and `research.status` handlers."""

import json

import pytest

from tests.gui_support import call, call_error
from tests.test_write_vault import make_vault

pytest_plugins = ["tests.gui_support"]   # the rich_vault / wdg_home fixtures


def V(vault):
    return str(vault)


def call_rec(task, *, filename="a.pdf", detail=None, cost=0.1, inp=1000, out=200, backend="claude-agent-sdk",
             auth="subscription", model="claude-sonnet-5-5", latency=2.0, end=None, **extra):
    rec = {"task": task, "filename": filename, "detail": detail, "model": model, "backend": backend,
           "auth_mode": auth, "input_tokens": inp, "output_tokens": out, "cache_read_tokens": 10,
           "cache_write_tokens": 5, "cost_usd": cost, "latency_s": latency, "attempts": 1,
           "effort": "medium"}
    if end is not None:
        rec["end_ts"] = end
    rec.update(extra)
    return rec


def write_usage(vault, ts, calls):
    d = vault / ".watchdog" / "registry" / "usage"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"usage-{ts}.json").write_text(json.dumps({"calls": calls, "totals": {}}))


@pytest.fixture
def used(rich_vault):
    write_usage(rich_vault, "20260301T100000Z", [
        call_rec("classify", cost=0.01, model="claude-haiku-4-5", latency=1, end=1000),
        call_rec("extract", detail="pages 1–10", cost=0.50, end=1010, latency=10),
        call_rec("extract-section", filename="b.pdf", detail="pages 11–30", cost=0.30, end=1012, latency=10),
        call_rec("briefing", filename=None, cost=0.05, model="claude-haiku-4-5", end=1020, latency=3),
    ])
    write_usage(rich_vault, "20260302T100000Z", [
        call_rec("extract", cost=2.0, backend="openai", auth=None, model="gpt-5-mini", detail="pages 1–4"),
    ])
    ex = rich_vault / ".watchdog" / "extracted"
    for sha, pages in (("x" * 64, 30), ("y" * 64, 10)):
        (ex / f"{sha}.json").write_text(json.dumps({"document": {"page_count": pages}}))
    return rich_vault


def test_runs_newest_first(used):
    r = call("usage.runs", vault=V(used))
    assert [x["ts"] for x in r["runs"]] == ["20260302T100000Z", "20260301T100000Z"]
    old = r["runs"][1]
    assert old["file"] == "usage-20260301T100000Z.json" and old["calls"] == 4
    assert old["input_tokens"] == 4000 and old["output_tokens"] == 800
    assert old["cost_usd"] == pytest.approx(0.86)
    assert old["backends"] == "sdk/sub" and old["subscription"] is True
    assert old["stages"] == {"classifier": pytest.approx(0.01), "extractor": pytest.approx(0.8),
                             "finalizer": pytest.approx(0.05)}
    new = r["runs"][0]
    assert new["backends"] == "openai" and new["subscription"] is False
    assert r["corpus"] == {"documents": 4, "pages": 40}
    json.dumps(r)


def test_runs_with_no_history(rich_vault):
    r = call("usage.runs", vault=V(rich_vault))
    assert r["runs"] == [] and r["corpus"] == {"documents": 2, "pages": 0}
    assert call("usage.runs", vault=V(make_vault(rich_vault.parent / "empty")))["corpus"] is None


def test_run_defaults_to_the_latest(used):
    r = call("usage.run", vault=V(used))
    assert r["ts"] == "20260302T100000Z" and [s["stage"] for s in r["stages"]] == ["extractor"]
    assert r["subscription_note"] is None
    assert r["totals"]["cost_usd"] == pytest.approx(2.0) and r["totals"]["calls"] == 1


def test_run_breakdown_by_stage(used):
    r = call("usage.run", vault=V(used), ts="20260301T10")
    assert r["ts"] == "20260301T100000Z"
    assert [s["stage"] for s in r["stages"]] == ["classifier", "extractor", "finalizer"]
    ext = r["stages"][1]
    assert ext["model"] == "Claude Sonnet 5.5" and ext["backend"] == "claude-agent-sdk (subscription)"
    assert [c["cost_usd"] for c in ext["calls"]] == [0.5, 0.3]            # highest cost first
    first = ext["calls"][0]
    assert first["filename"] == "a.pdf" and first["detail"] == "pages 1–10"
    assert first["cost_per_page"] == pytest.approx(0.05) and first["effort"] == "medium"
    assert first["input_tokens"] == 1000 and first["cache_read_tokens"] == 10 and first["failed"] is False
    assert ext["totals"] == {"calls": 2, "input_tokens": 2000, "output_tokens": 400, "cache_read_tokens": 20,
                             "cache_write_tokens": 10, "cost_usd": pytest.approx(0.8), "latency_s": 20.0}
    assert ext["wall_seconds"] == pytest.approx(12.0) and ext["peak_concurrency"] == 2
    assert r["totals"]["calls"] == 4 and r["totals"]["cost_usd"] == pytest.approx(0.86)
    assert r["totals"]["wall_seconds"] == pytest.approx(21.0)
    assert "list-price equivalents" in r["subscription_note"]
    assert r["corpus"] == {"documents": 4, "pages": 40}
    assert r["cost_per_page"] == pytest.approx(0.86 / 40)
    assert ext["calls"][1]["cost_per_page"] == pytest.approx(0.3 / 20)


def test_run_tolerates_old_records_missing_fields(rich_vault):
    write_usage(rich_vault, "20250101T000000Z", [{"task": "extract"}, {"cost_usd": 1.0}, "junk",
                                                 {"task": "extract", "failed": True, "attempts": 3,
                                                  "batch_id": "b1", "batch_submitted_at": "2026-01-01T00:00:00Z"}])
    r = call("usage.run", vault=V(rich_vault))
    assert r["totals"]["calls"] == 4 - 1 and r["totals"]["cost_usd"] == 1.0
    stages = {s["stage"]: s for s in r["stages"]}
    assert set(stages) == {"extractor", "unknown"}
    failed = next(c for c in stages["extractor"]["calls"] if c["failed"])
    assert failed["attempts"] == 3 and "batch b1" in stages["extractor"]["batch_note"]
    assert call("usage.runs", vault=V(rich_vault))["runs"][0]["calls"] == 3


def test_run_errors(used, tmp_path):
    assert call_error("usage.run", vault=V(used), ts="2099")["code"] == "not_found"
    assert call_error("usage.run", vault=V(used), ts="T100000Z")["code"] == "ambiguous"
    assert call_error("usage.run", vault=V(make_vault(tmp_path / "e")))["code"] == "no_runs"


def test_a_corrupt_usage_file_reads_as_an_empty_run(rich_vault):
    d = rich_vault / ".watchdog" / "registry" / "usage"
    d.mkdir(parents=True)
    (d / "usage-20260101T000000Z.json").write_text("{nope")
    r = call("usage.runs", vault=V(rich_vault))
    assert r["runs"][0]["calls"] == 0
    assert call("usage.run", vault=V(rich_vault))["stages"] == []


# ── research.status ──────────────────────────────────────────────────────────────

def test_research_status(rich_vault, wdg_home):
    r = call("research.status", vault=V(rich_vault))
    assert r == {"queued": [
        {"url": "https://example.org/a", "title": "A page", "source_type": "news", "relevance": "context"},
        {"url": "https://example.org/b", "title": None, "source_type": None, "relevance": None}],
        "wayback_configured": False}


def test_research_status_wayback(rich_vault, wdg_home):
    cfg = wdg_home / "config.json"
    cfg.write_text(json.dumps({"wayback_save": True, "wayback_access_key": "a", "wayback_secret_key": "b"}))
    assert call("research.status", vault=V(rich_vault))["wayback_configured"] is True
    cfg.write_text(json.dumps({"wayback_save": True, "wayback_access_key": "a"}))
    assert call("research.status", vault=V(rich_vault))["wayback_configured"] is False


def test_research_status_with_nothing_queued(tmp_path, wdg_home):
    assert call("research.status", vault=V(make_vault(tmp_path)))["queued"] == []
