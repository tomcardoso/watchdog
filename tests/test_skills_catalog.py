"""Tests for the global record-skill catalog (skills_catalog.py)."""

from watchdog import skills_catalog as sc


def test_skill_descriptor_prefers_frontmatter():
    text = "---\ndescription: My custom description\n---\n# Title\n\nSome other first line.\n"
    assert sc._skill_descriptor(text) == "My custom description"


def test_skill_descriptor_falls_back_to_heuristic():
    assert sc._skill_descriptor(
        "# Title\n\nThis skill is loaded by `/ingest` when the document type is a court order.\n"
    ) == "a court order"
    assert sc._skill_descriptor("# Title\n\nCovers police reports.\n") == "Covers police reports"


def _fake_records(monkeypatch, tmp_path, package=("court-documents",), user=()):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    for n in package:
        (pkg / f"{n}.md").write_text(f"---\ndescription: {n} desc\n---\n# {n}\n")
    udir = tmp_path / "user" / "records"
    udir.mkdir(parents=True)
    for n in user:
        (udir / f"{n}.md").write_text(f"---\ndescription: {n} user desc\n---\n# {n}\n")
    monkeypatch.setattr(sc, "_package_records", lambda: pkg)
    monkeypatch.setattr(sc, "USER_SKILLS_DIR", udir)
    return pkg, udir


def test_catalog_merges_package_and_user(monkeypatch, tmp_path):
    _fake_records(monkeypatch, tmp_path, package=("court-documents",), user=("my-beat",))
    assert set(sc.catalog()) == {"court-documents", "my-beat"}


def test_user_skill_overrides_package(monkeypatch, tmp_path):
    _, udir = _fake_records(monkeypatch, tmp_path, package=("court-documents",), user=("court-documents",))
    assert sc.catalog()["court-documents"] == str(udir / "court-documents.md")   # user wins


def test_read_skill_and_index(monkeypatch, tmp_path):
    _fake_records(monkeypatch, tmp_path, package=("court-documents",), user=("my-beat",))
    assert "court-documents desc" in sc.read_skill("court-documents")
    assert sc.read_skill("court-documents.md")                 # accepts a .md suffix
    idx = sc.build_index()
    assert "- `court-documents.md` — court-documents desc" in idx
    assert "- `my-beat.md` — my-beat user desc" in idx


def test_read_skill_unknown_returns_empty(monkeypatch, tmp_path):
    _fake_records(monkeypatch, tmp_path)
    assert sc.read_skill("nonexistent") == ""


def test_github_skills_url():
    assert sc.github_skills_url().endswith("/src/watchdog/skills/records")
    one = sc.github_skills_url(name="court-documents")
    assert one.endswith("court-documents.md") and "/blob/" in one
