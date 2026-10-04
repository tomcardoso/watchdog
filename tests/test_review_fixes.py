"""Fixes from the post-merge review of `add`, auto-approve and `review` (D256)."""

import argparse
import json

import pytest

import watchdog.cmd.ingest as ing
import watchdog.cmd.review as review
from watchdog.pipeline import ingest_setup, resolutions


# ── the gate's cost figure comes only from runs on the models configured now ──────────────────

def _usage(vault, name, calls):
    d = vault / ".watchdog" / "registry" / "usage"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"usage-{name}.json").write_text(json.dumps({"calls": calls, "totals": {}}))


def _call(task, model, tokens, cost, backend="claude-api", **extra):
    return {"task": task, "model": model, "backend": backend, "input_tokens": tokens,
            "cost_usd": cost, **extra}


@pytest.fixture
def vault(tmp_path, monkeypatch):
    v = tmp_path / "probe"
    (v / ".watchdog" / "queue").mkdir(parents=True)
    (v / "_INCOMING").mkdir()
    monkeypatch.chdir(v)
    return v


def _matched(vault, tokens=1_000_000, extractor="opus"):
    return ingest_setup.matched_cost_high(vault, tokens, classifier="haiku", extractor=extractor,
                                          finalizers={"sonnet"})


def test_history_on_another_extractor_is_not_used(vault):
    _usage(vault, "2026-10-01", [_call("extract", "haiku", 1_000_000, 0.10),
                                 _call("briefing", "sonnet", 10, 0.0)])
    assert _matched(vault) is None


def test_a_complete_run_on_the_same_models_prices_the_queue(vault):
    _usage(vault, "2026-10-01", [_call("classify", "haiku", 100_000, 0.1),
                                 _call("extract", "opus", 800_000, 4.0),
                                 _call("briefing", "sonnet", 100_000, 0.9)])
    assert _matched(vault, tokens=2_000_000) == pytest.approx(2_000_000 * 5.0 / 1_000_000)


def test_an_extraction_only_run_is_not_enough(vault):
    _usage(vault, "2026-10-01", [_call("extract", "opus", 1_000_000, 5.0)])
    assert _matched(vault) is None


def test_cached_tokens_are_not_double_counted_for_other_providers(vault):
    # OpenAI reports cached tokens inside prompt_tokens; summing them would halve the rate.
    _usage(vault, "2026-10-01", [
        _call("extract", "gpt-5.6-luna", 1_000_000, 2.0, backend="openai", cache_read_tokens=1_000_000),
        _call("briefing", "gpt-5.6-luna", 0, 0.0, backend="openai")])
    high = ingest_setup.matched_cost_high(vault, 1_000_000, classifier="x", extractor="gpt-5.6-luna",
                                          finalizers={"gpt-5.6-luna"})
    assert high == pytest.approx(2.0)


def test_no_tokens_means_no_figure(vault):
    _usage(vault, "2026-10-01", [_call("extract", "opus", 1, 1.0), _call("briefing", "sonnet", 1, 1.0)])
    assert _matched(vault, tokens=0) is None


def _gate(vault, **kw):
    base = dict(auth_mode="api-key", classify=(None, "haiku"), extract=(None, "opus"),
                finalizers=[(None, "sonnet")], finishes_pending=False)
    base.update(kw)
    return ing._auto_approve_estimate(vault, {"est_tokens": 1000}, **base)


def test_a_pending_batch_always_asks(vault):
    assert "earlier run" in _gate(vault, finishes_pending=True)["blocker"]


def test_subscription_only_when_every_stage_is_on_it(vault):
    assert _gate(vault, auth_mode="subscription") == {"subscription": True}
    mixed = _gate(vault, auth_mode="subscription", finalizers=[("openai", "gpt-5.6-luna")])
    assert "subscription" not in mixed and "no past run" in mixed["blocker"]


def test_gate_prints_the_blocker_and_asks(monkeypatch, capsys):
    asked = []
    monkeypatch.setattr(ing.interactive, "pick", lambda *a, **k: asked.append(1) or 0)
    assert ing._confirm_public_records(2, est={"blocker": "a reason"}, limit=5.0) is True
    assert asked and "a reason, so the auto-approve limit can't apply" in capsys.readouterr().out


# ── `add` never copies the investigation's own files ──────────────────────────────────────────

def test_a_path_inside_the_vault_is_refused(vault):
    (vault / "documents").mkdir()
    with pytest.raises(SystemExit, match="inside this investigation"):
        ing._expand_paths([str(vault / "documents")], vault)
    with pytest.raises(SystemExit, match="inside this investigation"):
        ing._expand_paths(["."], vault)


def test_incoming_is_accepted_without_copying(vault):
    (vault / "_INCOMING" / "a.pdf").write_bytes(b"x")
    assert ing._expand_paths([str(vault / "_INCOMING")], vault) == []


def test_a_folder_holding_the_vault_leaves_its_files_out(vault, tmp_path, capsys):
    (vault / "hot.md").write_text("notes")
    (tmp_path / "memo.txt").write_text("memo")
    found = ing._expand_paths([str(tmp_path)], vault)
    assert [f.name for f in found] == ["memo.txt"]
    assert "Leaving out" in capsys.readouterr().out


def test_hidden_files_and_folders_are_skipped(vault, tmp_path):
    src = tmp_path / "drop"
    (src / ".git").mkdir(parents=True)
    (src / ".git" / "config.txt").write_text("x")
    (src / "._report.pdf").write_bytes(b"x")
    (src / "report.pdf").write_bytes(b"%PDF")
    (tmp_path / ".DS_Store").write_bytes(b"x")
    found = ing._expand_paths([str(src), str(tmp_path / ".DS_Store")], vault)
    assert [f.name for f in found] == ["report.pdf"]


def test_add_estimate_changes_nothing(vault, tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(ing, "_run_preprocess", lambda *a, **k: calls.append("chew"))
    monkeypatch.setattr(ing, "_requeue_failed", lambda *a, **k: calls.append("retry"))
    monkeypatch.setattr(ing, "cmd_ingest", lambda a, **k: calls.append("estimate"))
    doc = tmp_path / "a.pdf"
    doc.write_bytes(b"%PDF")
    (vault / "_INCOMING" / "waiting.pdf").write_bytes(b"x")
    ing.cmd_add(argparse.Namespace(paths=[str(doc)], retry=True, estimate=True))
    assert calls == ["estimate"]
    assert not (vault / "_INCOMING" / "a.pdf").exists()
    assert "covers the current queue only" in capsys.readouterr().out


# ── review: filenames with `]` or `**`, and checkboxes that match the store ───────────────────

def test_alert_titles_survive_brackets_and_asterisks(vault):
    (vault / "briefings").mkdir()
    (vault / "briefings" / "alerts-2026-10-03.md").write_text(
        "### `offshore` — 2 documents\n\n"
        "- [ ] **[[documents/exhibit-a|Exhibit [A].pdf]]** · known entity [[entities/x|X]] (2 matches) "
        "<!--wid:alert:aaaaaaa:t1-->\n"
        "- [ ] **2**3 memo.pdf** <!--wid:alert:bbbbbbb:t1-->\n")
    a, b = review.open_items(vault, ("alerts",))
    assert (a["title"], a["note"]) == ("`offshore` in Exhibit [A].pdf", "documents/exhibit-a")
    assert b["title"] == "`offshore` in 2**3 memo.pdf"


def test_handled_in_review_stays_handled_after_sync(vault, monkeypatch):
    (vault / "briefings").mkdir()
    f = vault / "briefings" / "alerts-2026-10-03.md"
    f.write_text("- [ ] **memo.pdf** <!--wid:alert:aaaaaaa:t1-->\n")
    monkeypatch.setattr(review.interactive, "pick", lambda items, *a, **k: items.index("Mark as handled"))
    monkeypatch.setattr(review.sys.stdin, "isatty", lambda: True)
    review.cmd_review(argparse.Namespace(kind="alerts"))
    assert f.read_text().startswith("- [x]")
    resolutions.sync_from_briefings(vault)
    assert "alert:aaaaaaa:t1" in resolutions.resolved_ids(vault)


def test_unresolve_clears_the_box(vault):
    (vault / "briefings").mkdir()
    f = vault / "briefings" / "leads-2026-10-03.md"
    f.write_text("- [x] **Acme** <!--wid:lead:isolated:acme-->\n- [x] **B** <!--wid:lead:isolated:b-->\n")
    assert resolutions.tick_in_briefings(vault, ["lead:isolated:acme"], ticked=False) == 1
    assert f.read_text() == "- [ ] **Acme** <!--wid:lead:isolated:acme-->\n- [x] **B** <!--wid:lead:isolated:b-->\n"
