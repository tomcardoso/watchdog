"""Fixes from the post-merge review of `add`, auto-approve and `review` (D263)."""

import argparse

import pytest

import watchdog.cmd.ingest as ing
import watchdog.cmd.review as review
from watchdog.pipeline import resolutions


@pytest.fixture
def vault(tmp_path, monkeypatch):
    v = tmp_path / "probe"
    (v / ".watchdog" / "queue").mkdir(parents=True)
    (v / "_INCOMING").mkdir()
    monkeypatch.chdir(v)
    return v


# ── auto-approve only when every stage runs on the subscription (D263) ─────────────────────────

@pytest.mark.parametrize("auth_mode, stages, approved", [
    ("subscription", [None, None, None], True),                    # every stage on the default route
    ("subscription", [None, "claude-agent-sdk", None], True),
    ("subscription", [None, None, "openai"], False),               # one finishing stage on a paid key
    ("subscription", ["claude-api", None, None], False),
    ("api-key", [None, None, None], False),                        # the default route is the paid API
    # on a paid key, even an explicit claude-agent-sdk stage is billed to the key
    ("api-key", ["claude-agent-sdk"] * 3, False),
    (None, [None, None, None], False),
])
def test_subscription_only_when_every_stage_is_on_it(auth_mode, stages, approved):
    verdict = ing._auto_approve_verdict(auth_mode=auth_mode, stages=stages)
    assert ("approve" in verdict) is approved
    if not approved:
        assert "paid API key" in verdict["blocker"]


def test_chew_with_auto_approve_hands_the_decision_to_cmd_ingest(vault, monkeypatch):
    calls = []
    monkeypatch.setattr(ing, "_preview_ingest", lambda *a, **k: None)
    monkeypatch.setattr(ing, "load_config", lambda: {"auto_approve": True})
    monkeypatch.setattr(ing, "_confirm_public_records", lambda *a, **k: pytest.fail("old gate used"))
    monkeypatch.setattr(ing, "cmd_ingest", lambda a, **k: calls.append(k))
    ing._offer_ingest(argparse.Namespace(command="chew"), vault)
    assert calls == [{"confirm": True, "skip_preview": True}]


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


def test_a_folder_of_investigations_leaves_every_vault_out(vault, tmp_path, capsys):
    other = tmp_path / "other-story"
    (other / ".watchdog").mkdir(parents=True)
    (other / "hot.md").write_text("another investigation's notes")
    (other / "briefings").mkdir()
    (other / "briefings" / "2026-10-01-09-00.md").write_text("briefing")
    (tmp_path / "loose.txt").write_text("a real document")
    found = ing._expand_paths([str(tmp_path)], vault)
    assert [f.name for f in found] == ["loose.txt"]
    assert "Leaving out" in capsys.readouterr().out


def test_ticking_keeps_crlf_and_undecodable_bytes(vault):
    (vault / "briefings").mkdir()
    f = vault / "briefings" / "alerts-2026-10-03.md"
    f.write_bytes(b"- [ ] **m\xe9mo.pdf** <!--wid:alert:aaaaaaa:t1-->\r\n- [ ] other\r\n")
    assert resolutions.tick_in_briefings(vault, ["alert:aaaaaaa:t1"]) == 1
    assert f.read_bytes() == b"- [x] **m\xe9mo.pdf** <!--wid:alert:aaaaaaa:t1-->\r\n- [ ] other\r\n"
    assert not list((vault / "briefings").glob("*.tmp"))
