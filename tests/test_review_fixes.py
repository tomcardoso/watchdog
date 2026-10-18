"""Fixes from the post-merge review of adding documents, auto-approve and Review (D263)."""

import pytest

import watchdog.ops.ingest as ing
import watchdog.review as review


@pytest.fixture
def vault(tmp_path, monkeypatch):
    v = tmp_path / "probe"
    (v / ".watchdog" / "queue").mkdir(parents=True)
    (v / "incoming").mkdir()
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


# ── `add` never copies the investigation's own files ──────────────────────────────────────────

def test_a_path_inside_the_vault_is_refused(vault):
    (vault / "documents").mkdir()
    with pytest.raises(SystemExit, match="inside this investigation"):
        ing._expand_paths([str(vault / "documents")], vault)
    with pytest.raises(SystemExit, match="inside this investigation"):
        ing._expand_paths([str(vault)], vault)


def test_incoming_is_accepted_without_copying(vault):
    (vault / "incoming" / "a.pdf").write_bytes(b"x")
    assert ing._expand_paths([str(vault / "incoming")], vault) == []


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


# ── review: filenames with `]` or `**` ───────────────────

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
