"""The desktop app's demo vault (`python -m watchdog.gui.demo`): built end to end by the real
pipeline with canned model answers, in a scratch HOME. The build runs once per module as a
subprocess, exactly as the app's developers run it."""

import json
import os
import re
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
    for name in ("index.md", "dashboard.base", "context.md", "watchlist.md", "log.md",
                 "timeline.md", "requests.md", ".claude/CLAUDE.md", ".claude/settings.json"):
        assert (vault / name).exists(), name
    assert not (vault / "hot.md").exists()          # retired (D285)
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
    assert "[!contradiction]" in note and "## Summary" in note and "## Facts" in note
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
    requests_md = (vault / "requests.md").read_text(encoding="utf-8")
    assert "[ ]" not in requests_md and "<!--wid:request:" in requests_md


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


def test_deleted_notes_rebuild_identically_with_no_model(demo_vault, tmp_path, monkeypatch):
    """D280: every entity and document note is a view of stored data, including the AI-written
    summaries (kept in the registry) and the reporter's marks: delete them all, rebuild, and get
    the same files back, with no model call."""
    import shutil
    from watchdog import model_client
    from watchdog.pipeline import entity_notes
    vault, _, _ = demo_vault
    copy = tmp_path / "copy"
    shutil.copytree(vault, copy)
    before = {p.relative_to(copy): p.read_text(encoding="utf-8")
              for d in ("entities", "documents") for p in (copy / d).rglob("*.md")}
    assert any("✓ verified ^f-" in t for t in before.values())       # marks are in the notes
    assert any("## Summary" in t for t in before.values())
    for rel in before:
        (copy / rel).unlink()

    def no_model(**_kw):
        raise AssertionError("a rebuild must not call a model")
    monkeypatch.setattr(model_client, "acomplete_json", no_model)
    out = entity_notes.rebuild(copy, index_search=False)
    after = {p.relative_to(copy): p.read_text(encoding="utf-8")
             for d in ("entities", "documents") for p in (copy / d).rglob("*.md")}
    assert out["entities"] == len(_registry(vault, "entities.json"))
    assert after == before


def test_entity_note_lists_its_facts_under_a_plain_summary(demo_vault):
    vault, _, _ = demo_vault
    note = (vault / "entities" / "person" / "leonard-pike.md").read_text(encoding="utf-8")
    assert "\n## Summary\n" in note
    summary = note.split("## Summary", 1)[1].split("\n## ", 1)[0]
    assert "Written by" not in summary and "It can be wrong" not in summary   # no byline (D284)
    facts = note.split("## Facts", 1)[1].split("\n## ", 1)[0]
    lines = [ln for ln in facts.splitlines() if ln.startswith("- ")]
    assert len(lines) >= 10                                  # every document's facts, not just one
    assert all("[[morgue/" in ln and "#page=" in ln for ln in lines)
    entry = _registry(vault, "entities.json")["leonard-pike"]
    assert entry["synthesis"]["by"] == "model" and entry["synthesis"]["fact_refs"]


def test_demo_summaries_cite_facts_and_every_citation_resolves(demo_vault):
    """D283: the canned syntheses cite facts the way a model does; every citation renders as a link
    to an existing fact line, and the one on the disputed payment is labelled."""
    from watchdog.pipeline import citations
    vault, _, _ = demo_vault
    ents = _registry(vault, "entities.json")
    stats = [e["synthesis"]["citations"] for e in ents.values()
             if isinstance(e.get("synthesis"), dict) and e["synthesis"].get("citations")]
    assert sum(s["linked"] for s in stats) >= 10
    assert all(s["unknown"] == 0 and s["missing"] == 0 for s in stats)
    note = (vault / "entities" / "organization" / "7714882-holdings-ltd.md").read_text(encoding="utf-8")
    summary = note.split("## Summary", 1)[1].split("\n## Facts", 1)[0]
    assert "[f:" not in summary and "p. 1, disputed]]" in summary
    report = citations.check_text(summary, citations.Resolver(vault))
    assert report["citations"] and report["not_found"] == 0 and report["disputed"] == 1


def test_app_resolves_citations_and_renders_the_linked_summary(demo_vault):
    """D283: `vault.entity` gives the summary with its citations linked, and `vault.citations`
    resolves each link a page holds: found with the fact and its mark, or not found."""
    from tests.gui_support import call
    vault, _, _ = demo_vault
    e = call("vault.entity", vault=str(vault), id="7714882-holdings-ltd")
    assert "[f:" not in e["synthesis"]["summary_md"] and "#^f-" in e["synthesis"]["summary_md"]
    assert e["synthesis"]["citations"]["missing"] == 0
    key = next(iter(re.findall(r"\[\[([^\]|]+#\^f-[0-9a-f]+)", e["synthesis"]["summary_md"])))
    out = call("vault.citations", vault=str(vault),
               links=[key, "documents/council-minutes-2022-02-08#^f-0000000000", "nonsense"])
    assert out[key]["status"] == "found" and out[key]["fact"]["sha"]
    assert out["documents/council-minutes-2022-02-08#^f-0000000000"]["status"] == "not_found"
    assert "nonsense" not in out
    report = call("vault.checkCitations", vault=str(vault))
    assert report["citations"] > 0 and report["not_found"] == 0


def test_a_disputed_fact_is_shown_labelled_on_every_surface(demo_vault, tmp_path):
    """D285 (the owner's call, I14): a fact the reporter marked Disputed is never hidden or
    dropped; wherever a fact is shown it carries the "disputed" label."""
    from tests.gui_support import call
    from watchdog.ops.export import _write_facts_csv
    from watchdog.cmd.vault import _page_facts
    from watchdog.pipeline import verification
    vault, _, _ = demo_vault
    disputed = [e for e in verification.entries(vault) if e["status"] == "disputed"]
    assert len(disputed) == 1
    d = disputed[0]
    words = d["fact"]
    # timeline.md and the app's Timeline, and the entity's own timeline.
    lines = [ln for ln in (vault / "timeline.md").read_text(encoding="utf-8").splitlines() if "disputed" in ln]
    assert len(lines) == 1 and lines[0].endswith("· ✗ disputed")
    events = call("vault.timeline", vault=str(vault))["events"]
    assert [e["text"] for e in events if e["disputed"]] == [words]
    ent = call("vault.entity", vault=str(vault), id="lot-14-dockside-road")
    assert [e["text"] for e in ent["timeline"] if e["disputed"]] == [words]
    assert [f["fact"] for f in ent["facts"] if (f.get("mark") or {}).get("status") == "disputed"] == [words]
    # The document reader's facts and the document note.
    doc = call("vault.document", vault=str(vault), sha=d["sha256"])
    assert [f["fact"] for f in doc["facts"] if (f.get("mark") or {}).get("status") == "disputed"] == [words]
    note = (vault / f"{d['note_path']}.md").read_text(encoding="utf-8")
    assert [ln for ln in note.splitlines() if "✗ disputed" in ln][0].startswith(f"- {words}")
    # Search's fact lists, the export and the Overview.
    facts_on, _ = _page_facts(vault)
    assert any(f["mark"] == "disputed" and "disputed]]" in f["cite"] for f in facts_on(d["sha256"], d["page"]))
    path, n, _ = _write_facts_csv(vault, tmp_path)
    rows = path.read_text(encoding="utf-8").splitlines()
    assert len(rows) == n + 1 and sum(1 for r in rows if ",Disputed," in r) == 1
    s = call("vault.summary", vault=str(vault))
    assert s["verification"]["disputed"] == 1 and s["headline"].startswith("Fourteen documents")


def test_demo_scans_carry_saved_text_positions(demo_vault):
    """D289: the scanned letter (an image) and the access decision's scanned cover page (a PDF
    page with no text layer) have OCR line positions, as pre-processing saves them."""
    import pypdf
    from watchdog.pipeline import text_positions
    vault, _, _ = demo_vault
    documents = _registry(vault, "documents.json")
    by_name = {Path(d["filename"]).name: sha for sha, d in documents.items()}
    letter = by_name["whitcombe-letter-to-integrity-commissioner.png"]
    foi = by_name["foi-response-lot-14-appraisal.pdf"]
    assert text_positions.available_pages(vault, letter) == [1]
    assert text_positions.available_pages(vault, foi) == [4]
    page = text_positions.read(vault, foi, [4])["boxes"]["4"]
    assert (page["width"], page["height"]) == (612.0, 792.0)
    assert any("BUDGETING PURPOSES" in line[4] for line in page["lines"])
    reader = pypdf.PdfReader(str(vault / documents[foi]["morgue_path"]))
    assert len(reader.pages) == 4 and not (reader.pages[3].extract_text() or "").strip()
    others = [sha for sha in documents if sha not in (letter, foi)]
    assert all(text_positions.available_pages(vault, sha) == [] for sha in others)
