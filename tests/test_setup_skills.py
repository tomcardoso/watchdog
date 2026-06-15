"""Tests for skill installation and the generated records index (setup_cmd.py)."""

from watchdog.setup_cmd import install_skills, _skill_descriptor


def test_skill_descriptor_strips_boilerplate():
    assert _skill_descriptor(
        "# Domain knowledge — Corporate filings\n\n"
        "This skill is loaded by `/ingest` when the document type is an annual report, "
        "or similar corporate record.\n"
    ) == "an annual report, or similar corporate record"

    # The other lead-in variant used across the skills.
    assert _skill_descriptor(
        "# Title\n\nLoaded by `/ingest` when the document type is a court order.\n"
    ) == "a court order"


def test_skill_descriptor_falls_back_to_raw_first_line():
    assert _skill_descriptor("# Title\n\nCovers police occurrence reports.\n") == \
        "Covers police occurrence reports"


def test_install_skills_generates_index(tmp_path):
    install_skills(tmp_path)
    records = tmp_path / "records"

    installed = {p.name for p in records.glob("*.md") if not p.name.startswith("_")}
    assert "corporate-filings.md" in installed  # sanity: skills were copied

    index = records / "_index.md"
    assert index.exists()
    body = index.read_text(encoding="utf-8")

    # One entry per installed skill, and no underscore-prefixed source files leaked in.
    entry_lines = [ln for ln in body.splitlines() if ln.startswith("- `")]
    assert len(entry_lines) == len(installed)
    assert "- `corporate-filings.md` —" in body
    assert "_index.md" not in "\n".join(entry_lines)
