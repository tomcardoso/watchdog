"""The session primer (D285): a deterministic, budgeted picture of the whole investigation that the
app adds to every Ask Claude session's system prompt (D299), replacing the model-written hot.md."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from watchdog.cmd import primer

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
    # The last briefings, newest first. The demo writes both within seconds, so the second is
    # either a later minute or the same minute with a "-2" suffix, depending on the clock.
    first, second = [ln for ln in text.splitlines() if ln.startswith("- [[briefings/")]
    assert "Fourteen documents now document" in first
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


def test_session_text_is_the_primer_and_empty_outside_an_investigation(demo, tmp_path):
    vault, _ = demo
    assert primer.session_text(vault) == primer.build(vault)
    assert primer.session_text(tmp_path) == ""


def test_session_text_never_fails_a_session(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    monkeypatch.setattr(primer, "gather", lambda v: 1 / 0)
    assert "could not build this investigation's primer (ZeroDivisionError)" in primer.session_text(vault)


def test_the_app_shows_the_same_primer(demo):
    """Briefings → Current state reads `vault.sessionPrimer`, the function sessions start with (I10)."""
    from tests.gui_support import call
    vault, _ = demo
    out = call("vault.sessionPrimer", vault=str(vault))
    assert out["text"] == primer.build(vault)
    assert out["chars"] == len(out["text"]) <= out["budget"] == primer.BUDGET_CHARS


# ── Briefings → Current state: the same records, written for the reporter ────────────────────

def test_current_state_for_the_reporter_drops_model_instructions(demo):
    from watchdog.gui import current_state
    vault, _ = demo
    text = current_state.build(vault)
    for absent in ("## Citing", "watchdog ", "--json", "`", "context.md", "requests.md",
                   "merges.md", "verification.md", "Never invent", "the reporter"):
        assert absent not in text, absent
    # The same numbers as the primer Claude gets, under the reader's headings.
    claude = primer.build(vault)
    assert "14 documents (" in text and "54 entities" in text and "14 documents (" in claude
    assert "## Your questions" in text and "- Who owns 7714882 Holdings Ltd." in text
    assert "## Waiting on you" in text and "Review → " in text
    assert "## Recent briefings" in text and "[[briefings/" in text


def test_current_state_empty_vault(tmp_path):
    from watchdog.gui import current_state
    text = current_state.build(make_vault(tmp_path))
    assert "No documents have been added yet." in text
    assert "No questions yet. Add them in Investigation context" in text
    assert "`" not in text and "watchdog " not in text
