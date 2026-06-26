"""Prompt templates load from watchdog/prompts/*.md and stay out of the skills catalog."""

import pytest

from watchdog import skills_catalog
from watchdog.pipeline import prompts

_TEMPLATES = ["classify", "extract_instructions", "section_intro",
              "synthesis", "briefing", "timeline_dedup"]


@pytest.mark.parametrize("name", _TEMPLATES)
def test_template_loads_nonempty(name):
    assert prompts._text(name).strip()


def test_render_substitutes_tokens():
    out = prompts._render("timeline_dedup", date="2021-02-01")
    assert "2021-02-01" in out
    assert "{{date}}" not in out


def test_render_leaves_single_braces_untouched():
    # A template may contain literal { } (e.g. JSON examples); only {{key}} is special.
    prompts._text.cache_clear()
    assert prompts._render("classify") == prompts._text("classify")


def test_section_prompt_renders_label():
    p = prompts.build_section_prompt(
        pages_text="x", existing_entities=[], skill_text="", carry_forward="",
        section_label="pp.1-10", is_first=True, known_document_types=[])
    assert "pp.1-10" in p and "{{" not in p


def test_extract_prompt_includes_instructions_and_data():
    p = prompts.build_extract_prompt(
        pages_text="DOCBODY", existing_entities=[{"id": "x"}], skill_text="SKILL",
        sidecar=None, brief=None, known_document_types=[])
    assert "key_facts" in p              # instruction prose loaded
    assert "DOCBODY" in p and "SKILL" in p   # data assembled in
    # identity fields are no longer asked of the model (stamped in Python)
    assert "Set document.sha256" not in p


def test_extract_prompt_lists_known_document_types():
    p = prompts.build_extract_prompt(
        pages_text="x", existing_entities=[], skill_text="", sidecar=None, brief=None,
        known_document_types=["Annual Report", "Affidavit"])
    assert "KNOWN_DOCUMENT_TYPES" in p
    assert "- Annual Report" in p and "- Affidavit" in p
    assert "reuse one verbatim" in p


def test_extract_prompt_handles_no_known_types():
    p = prompts.build_extract_prompt(
        pages_text="x", existing_entities=[], skill_text="", sidecar=None, brief=None,
        known_document_types=[])
    assert "none yet" in p


def test_prompt_templates_not_in_skills_catalog():
    """The user-facing guarantee: prompt templates are invisible to the classifier."""
    catalog = set(skills_catalog.catalog())
    assert not (catalog & set(_TEMPLATES))
    index = skills_catalog.build_index()
    assert "extract_instructions" not in index
    assert "briefing" not in index
