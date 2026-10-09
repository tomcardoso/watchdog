"""The session primer (D285): a deterministic, budgeted picture of the whole investigation that the
vault's SessionStart hook prints into every Ask Claude session, replacing the model-written hot.md."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from watchdog.cmd import primer
from watchdog.vault_paths import SESSION_HOOK_COMMAND, ensure_current_layout, migrate_folder_names

from tests.test_write_vault import make_vault

SRC = Path(__file__).resolve().parent.parent / "src"
_ENV = {**os.environ, "PYTHONPATH": str(SRC), "NO_COLOR": "1"}


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    root = tmp_path_factory.mktemp("primer-demo")
    vault, home = root / "vault", root / "home"
    proc = subprocess.run([sys.executable, "-m", "watchdog.gui.demo", str(vault), "--home", str(home)],
                          env=_ENV, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    return vault, home


def _cli(cwd: Path, home: Path | None = None) -> subprocess.CompletedProcess:
    env = {**_ENV, **({"WATCHDOG_HOME": str(home)} if home else {})}
    return subprocess.run([sys.executable, "-c", "from watchdog.cli import main; main()",
                           "session-primer"], cwd=cwd, env=env, capture_output=True, text=True,
                          timeout=60)


def test_empty_vault_primer_is_short_and_says_so(tmp_path):
    vault = make_vault(tmp_path)
    text = primer.build(vault)
    assert text.startswith("# vault: where the investigation stands\n")
    assert "No documents have been added yet." in text
    assert "`context.md` lists no questions yet." in text
    assert "## Citing" in text and "#^f-" in text
    for absent in ("## Most-mentioned entities", "## Waiting on the reporter", "## Recent briefings"):
        assert absent not in text
    assert len(text) < 1200


def test_demo_primer_covers_the_whole_investigation(demo):
    vault, _ = demo
    text = primer.build(vault)
    assert text == primer.build(vault)                       # deterministic
    assert len(text) <= primer.BUDGET_CHARS
    assert text.startswith("# Port Calder Waterfront: where the investigation stands\n")
    # The reporter's questions, from context.md.
    assert "Investigating: I want to understand how Port Calder City Council" in text
    assert "- Who owns 7714882 Holdings Ltd." in text
    # Counts and verification progress over every document, not the last batch.
    assert "14 documents (" in text and "54 entities" in text
    assert "verified" in text and "1 disputed" in text
    # The most-mentioned entities, linked to their notes.
    assert "- [[entities/public-body/city-of-port-calder|City of Port Calder]], public body: 11 documents" in text
    # What is waiting on the reporter.
    for heading in ("**Open contradictions (6)**", "**Open leads (", "**Documents to request (",
                    "**Possible same entities, not merged (1)**", "**Facts the reporter disputes (1)**"):
        assert heading in text, heading
    assert "|p. 1, disputed]]" in text                      # a disputed fact, cited and labelled
    # The last briefings, newest first (a second briefing in one minute is the newer).
    first, second = [ln for ln in text.splitlines() if ln.startswith("- [[briefings/")]
    assert "-2|" in first and "Fourteen documents now document" in first
    assert "The first four documents show" in second
    assert not (vault / "hot.md").exists()


def test_lists_shrink_to_fit_the_budget(demo):
    vault, _ = demo
    full = primer.build(vault)
    small = primer.build(vault, budget=3700)
    assert len(small) <= 3700 < len(full)
    assert "…and 5 more (app: Review)" in small            # 6 contradictions, one shown
    smallest = primer.build(vault, budget=100)               # lists can't shrink further
    assert "**Open contradictions (6)**: see app: Review" in smallest and "\n- 14 Dockside" not in smallest
    assert "## Citing" in small and "## Recent briefings" in small


def test_context_questions_reads_question_headings_or_question_bullets(tmp_path):
    vault = make_vault(tmp_path)
    (vault / "context.md").write_text(
        "# Probe — context\n\n## What I'm investigating\n\n<!-- hint -->\nThe land deal.\n\n"
        "## Key questions I'm trying to answer\n\n- Who signed?\n- \n- What did it cost\n\n"
        "## Entities\n\n- Acme?\n")
    assert primer.context_questions(vault) == ("The land deal.", ["Who signed?", "What did it cost"])
    (vault / "context.md").write_text("# Notes\n\n- Did Acme pay?\n- background only\n")
    assert primer.context_questions(vault) == (None, ["Did Acme pay?"])
    assert primer._name(vault) == "vault"
    (vault / "context.md").write_text("# Probe - context\n")
    assert primer._name(vault) == "Probe"


def test_session_primer_command(demo, tmp_path):
    vault, home = demo
    out = _cli(vault, home)
    assert out.returncode == 0 and out.stdout == primer.build(vault)
    # Outside an investigation it prints nothing and never fails.
    elsewhere = _cli(tmp_path)
    assert elsewhere.returncode == 0 and elsewhere.stdout == ""


def test_session_primer_never_fails_a_session(tmp_path, monkeypatch, capsys):
    vault = make_vault(tmp_path)
    monkeypatch.setattr(primer, "gather", lambda v: 1 / 0)
    primer.cmd_session_primer(vault)
    assert "could not build this investigation's primer (ZeroDivisionError)" in capsys.readouterr().out


def _legacy_settings(vault: Path) -> Path:
    path = vault / ".claude" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"hooks": {
        "SessionStart": [{"matcher": "startup|resume|compact",
                          "hooks": [{"type": "command", "command": "[ -f hot.md ] && cat hot.md || true"}]}],
        "UserPromptSubmit": [{"matcher": "", "hooks": [{"type": "command", "command": "watchdog prompt-status"}]}],
    }}, indent=2))
    return path


def test_an_older_vaults_hot_md_hook_becomes_the_primer_and_hot_md_is_kept(tmp_path):
    vault = make_vault(tmp_path)
    path = _legacy_settings(vault)
    (vault / "hot.md").write_text("# Hot cache\n\nthe reporter's old file\n")
    changes = ensure_current_layout(vault)
    assert ".claude/settings.json updated" in changes
    hooks = json.loads(path.read_text())["hooks"]
    assert hooks["SessionStart"][0]["hooks"][0]["command"] == SESSION_HOOK_COMMAND
    assert hooks["UserPromptSubmit"][0]["hooks"][0]["command"] == "watchdog prompt-status"
    # The session instructions were refreshed with it, and no longer mention hot.md.
    claude_md = (vault / ".claude" / "CLAUDE.md").read_text()
    assert "hot.md" not in claude_md and "primer" in claude_md
    # The old file is the reporter's: left exactly as it was.
    assert (vault / "hot.md").read_text() == "# Hot cache\n\nthe reporter's old file\n"
    assert migrate_folder_names(vault) == []                 # idempotent
