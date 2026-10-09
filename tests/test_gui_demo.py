"""The desktop app's demo vault (`python -m watchdog.gui.demo`): built end to end by the real
pipeline with canned model answers, in a scratch HOME. The build runs once per module as a
subprocess, exactly as the app's developers run it."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from watchdog.gui import demo, demo_pdf
from watchdog.pipeline.timeline import _timeline_dir  # noqa: F401  (the vault's timeline folder)

SRC = Path(__file__).resolve().parent.parent / "src"


@pytest.fixture(scope="module")
def demo_vault(tmp_path_factory):
    root = tmp_path_factory.mktemp("demo")
    vault, home = root / "vault", root / "home"
    env = {**os.environ, "PYTHONPATH": str(SRC), "NO_COLOR": "1"}
    proc = subprocess.run([sys.executable, "-m", "watchdog.gui.demo", str(vault), "--home", str(home)],
                          env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    return vault, home, proc.stdout


def _registry(vault: Path, name: str) -> dict:
    return json.loads((vault / ".watchdog" / "registry" / name).read_text(encoding="utf-8"))


def test_vault_is_a_real_vault_with_the_template_files(demo_vault):
    vault, _, _ = demo_vault
    for name in ("index.md", "dashboard.base", "context.md", "watchlist.md", "hot.md", "log.md",
                 "timeline.md", "requests.md", ".claude/CLAUDE.md", ".claude/settings.json"):
        assert (vault / name).exists(), name
    assert "Strathmore Public Affairs" in (vault / "watchlist.md").read_text(encoding="utf-8")


def test_documents_and_morgue_files(demo_vault):
    vault, _, _ = demo_vault
    documents = _registry(vault, "documents.json")
    assert len(documents) == 14
    kinds = {Path(d["filename"]).suffix for d in documents.values()}
    assert {".pdf", ".png"} <= kinds
    pdfs = list((vault / "morgue").rglob("*.pdf"))
    assert len(pdfs) == 13
    for pdf in pdfs:
        assert pdf.read_bytes().startswith(b"%PDF")
    # Every document has a note, and a full-text sibling next to its original.
    for entry in documents.values():
        assert (vault / f"{entry['document_note']}.md").exists()
        assert (vault / entry["morgue_path"]).exists()
        assert (vault / entry["morgue_path"]).with_suffix(".md").exists()


def test_pdf_text_layer_matches_the_page_text_the_pipeline_saw(demo_vault):
    import pypdf
    vault, _, _ = demo_vault
    documents = _registry(vault, "documents.json")
    entry = next(d for d in documents.values() if d["filename"] == "parcel-register-14-dockside-road.pdf")
    reader = pypdf.PdfReader(str(vault / entry["morgue_path"]))
    assert len(reader.pages) == 2
    text = " ".join(reader.pages[0].extract_text().split())
    assert "Instrument PC-447119, registered February 28, 2022" in text
    sibling = (vault / entry["morgue_path"]).with_suffix(".md").read_text(encoding="utf-8")
    assert "<!-- PAGE 2 -->" in sibling and "$4,350,000" in sibling


def test_entities_cover_all_six_types(demo_vault):
    vault, _, _ = demo_vault
    entities = _registry(vault, "entities.json")
    types = {e["type"] for e in entities.values()}
    assert types == {"person", "organization", "public-body", "place", "asset", "proceeding"}
    assert 40 <= len(entities) <= 60
    assert any(e.get("aliases") for e in entities.values())
    assert any(e.get("roles") for e in entities.values())


def test_contradictions_synthesis_and_merges(demo_vault):
    vault, _, _ = demo_vault
    entities = _registry(vault, "entities.json")
    flagged = [e for e in entities.values() if e.get("contradictions")]
    assert len(flagged) >= 2
    note = (vault / "entities" / "person" / "dana-whitcombe.md").read_text(encoding="utf-8")
    assert "[!contradiction]" in note and "## Summary (AI-written)" in note and "## Facts" in note
    # Two reconciliation merges folded the variant ids away.
    assert "planning-procurement-committee" not in entities
    assert "port-calder-land-registry" not in entities
    log = (vault / ".watchdog" / "registry" / "processing.log").read_text(encoding="utf-8")
    assert "MERGED" in log


def test_timeline_briefings_and_review_queues(demo_vault):
    vault, _, _ = demo_vault
    timeline = (vault / "timeline.md").read_text(encoding="utf-8")
    assert "## 2019" in timeline and "## 2023" in timeline
    # The two releases' same-day restatements were folded into one event.
    assert timeline.count("announced on May 2, 2022") == 1
    briefings = sorted((vault / "briefings").glob("20*.md"))
    assert len(briefings) == 2
    assert "Near-duplicate alerts" in briefings[-1].read_text(encoding="utf-8") or \
        "Near-duplicate alerts" in briefings[0].read_text(encoding="utf-8")
    assert list((vault / "briefings").glob("alerts-*.md")) and list((vault / "briefings").glob("leads-*.md"))
    documents = _registry(vault, "documents.json")
    assert sum(1 for d in documents.values() if d.get("near_duplicate_of")) == 1
    assert "[ ]" in (vault / "requests.md").read_text(encoding="utf-8")


def test_pipeline_state_left_for_the_app(demo_vault):
    vault, home, stdout = demo_vault
    assert sorted(p.name for p in (vault / "incoming").iterdir()) == [
        "pier-9-change-order-3.pdf", "site-meeting-notes-2022-09-14.docx"]
    assert len(list((vault / ".watchdog" / "queue" / "_failed").glob("*.json"))) == 1
    log = (vault / ".watchdog" / "registry" / "processing.log").read_text(encoding="utf-8")
    assert "FAILED scanned-memo-illegible.pdf" in log
    assert len(list((vault / "context").iterdir())) >= 2
    queue = (vault / ".watchdog" / "research" / "queue.tsv").read_text(encoding="utf-8")
    assert queue.startswith("https://")
    assert len(list((vault / ".watchdog" / "registry" / "usage").glob("usage-*.json"))) >= 2
    assert "14 documents" in stdout


def test_passages_and_the_verification_ledger(demo_vault):
    """Most demo facts arrive without a locator and get a matched passage; a few have none on
    their page; the demo reporter's checks are in the ledger and verification.md (D270, D271)."""
    vault, _, _ = demo_vault
    methods = []
    for path in (vault / ".watchdog" / "extracted").glob("*.json"):
        doc = json.loads(path.read_text(encoding="utf-8"))["document"]
        assert doc["passages_version"] == 1
        methods += [f["passage_method"] for f in doc["key_facts"]]
    assert {m: methods.count(m) > 0 for m in ("quote", "matched", "unlocated")} == \
        {"quote": True, "matched": True, "unlocated": True}
    assert methods.count("matched") > methods.count("unlocated")
    ledger = json.loads((vault / ".watchdog" / "registry" / "verification.json").read_text(encoding="utf-8"))
    statuses = sorted(m["status"] for m in ledger["marks"].values())
    assert statuses == ["disputed", "unverifiable"] + ["verified"] * 5
    assert {m["by"] for m in ledger["marks"].values()} == {"Jordan Ellis"}
    assert "**5 of 116 facts verified**" in (vault / "verification.md").read_text(encoding="utf-8")


def test_home_holds_the_registry_and_config(demo_vault):
    vault, home, _ = demo_vault
    projects = json.loads((home / ".watchdog" / "projects.json").read_text(encoding="utf-8"))
    assert [p["path"] for p in projects.values()] == [str(vault.resolve())]
    assert (home / ".watchdog" / "config.json").exists()


def test_story_is_self_consistent():
    docs = demo.all_docs()
    assert len(docs) == 14
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as tmp:
        demo.render_files(docs, Path(tmp))
        assert demo.check_story(docs) == []


def test_pdf_writer_makes_a_readable_multipage_pdf(tmp_path):
    import pypdf
    path = tmp_path / "t.pdf"
    md = demo_pdf.write_pdf(
        path, [[("title", "A Title"), ("p", "First page paragraph (with parens).")],
               [("h", "Second"), ("table", [["A", "B"], ["1", "2"]], [100, 100])]],
        title="T", footer="Page {n} of {total}")
    reader = pypdf.PdfReader(str(path))
    assert len(reader.pages) == 2
    assert "with parens" in reader.pages[0].extract_text()
    assert "Page 2 of 2" in reader.pages[1].extract_text()
    assert md[0].startswith("# A Title") and "| A | B |" in md[1] and md[1].endswith("Page 2 of 2")


def test_pdf_writer_refuses_an_overflowing_page(tmp_path):
    with pytest.raises(ValueError):
        demo_pdf.render_pdf([[("p", "word " * 4000)]], title="x")
