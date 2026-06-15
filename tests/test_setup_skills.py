"""Tests for skill installation and the generated records index (setup_cmd.py)."""

from watchdog.setup_cmd import install_skills, regenerate_records_index, _skill_descriptor


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


def test_regenerate_index_includes_user_added_skill(tmp_path):
    install_skills(tmp_path)
    records = tmp_path / "records"
    # A journalist drops their own skill straight into the vault — not via the package.
    (records / "my-beat.md").write_text(
        "# Domain knowledge — My beat\n\n"
        "Loaded by `/ingest` when the document type is a widget inspection report.\n",
        encoding="utf-8",
    )
    regenerate_records_index(records)

    body = (records / "_index.md").read_text(encoding="utf-8")
    assert "- `my-beat.md` — a widget inspection report" in body


def test_regenerate_index_drops_removed_skill(tmp_path):
    install_skills(tmp_path)
    records = tmp_path / "records"
    (records / "corporate-filings.md").unlink()
    regenerate_records_index(records)

    assert "corporate-filings.md" not in (records / "_index.md").read_text(encoding="utf-8")
