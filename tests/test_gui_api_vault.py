"""`vault.*` handlers against a hand-built rich vault (the pipeline's own renderers) and against a
vault produced end to end by the real ingest."""

import json
import os
import sys

import pytest

from watchdog.pipeline import resolutions
from watchdog.pipeline.write_vault import _extract_notes_section, build_entity_note

from tests.gui_support import (  # noqa: F401
    CALLOUT, SHA1, SHA2, SHA3, call, call_error, make_rich_vault, register, rich_vault, wdg_home,
)
from tests.test_golden_vault import _run_fixture_ingest
from tests.test_write_vault import make_vault


def V(vault):
    return str(vault)


# ── validation ───────────────────────────────────────────────────────────────────

def test_not_a_vault_errors(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    for bad in (str(plain), str(tmp_path / "missing"), "relative/path", "", None):
        err = call_error("vault.summary", vault=bad)
        assert err["code"] == "not_a_vault"


def test_global_config_dir_is_not_a_vault(tmp_path):
    home = tmp_path / ".watchdog"
    home.mkdir()
    (home / "config.json").write_text("{}")
    assert call_error("vault.documents", vault=str(tmp_path))["code"] == "not_a_vault"


# ── summary ──────────────────────────────────────────────────────────────────────

def test_summary_of_rich_vault(rich_vault, wdg_home):
    register(wdg_home, rich_vault)
    s = call("vault.summary", vault=V(rich_vault))
    assert s["name"] == "Rich Case" and s["path"] == V(rich_vault)
    assert s["briefing"] == {"path": "briefings/2026-03-03-08-00.md", "name": "2026-03-03-08-00",
                             "date": "2026-03-03T08:00:00"}
    assert s["headline"] == "Three reports in; one date conflict."
    assert (s["contradictions"], s["leads"], s["near_duplicates"], s["alerts"]) == (1, 2, 1, 1)
    assert (s["incoming"], s["awaiting_dig"], s["awaiting_bark"], s["failed"]) == (2, 1, 1, 1)
    assert s["research_urls"] == 2 and s["has_work"] is True and s["pending_finalize"] is True
    assert s["context_unseeded"] is False
    assert s["totals"] == {"documents": 4, "entities": 3, "pages": 10, "events": 3}
    assert [d["filename"] for d in s["recent_documents"]][:2] == ["report-one-copy.pdf", "dup-original.pdf"]
    assert [e["id"] for e in s["top_entities"]] == ["acme-corp", "bob-roe", "jane-doe"]
    json.dumps(s)


def test_summary_name_falls_back_to_folder_name(rich_vault, wdg_home):
    assert call("vault.summary", vault=V(rich_vault))["name"] == rich_vault.name


def test_summary_of_new_empty_vault(tmp_path):
    vault = make_vault(tmp_path)
    s = call("vault.summary", vault=V(vault))
    assert s["briefing"] is None and s["headline"] is None
    assert s["totals"] == {"documents": 0, "entities": 0, "pages": 0, "events": 0}
    assert s["recent_documents"] == [] and s["top_entities"] == [] and s["has_work"] is False


def test_methods_tolerate_a_bare_vault_directory(tmp_path):
    """A folder with only `.watchdog/queue` — no registry files, notes, briefings or morgue."""
    vault = tmp_path / "bare"
    (vault / ".watchdog" / "queue").mkdir(parents=True)
    v = V(vault)
    assert call("vault.documents", vault=v) == []
    assert call("vault.entities", vault=v) == []
    assert call("vault.graph", vault=v) == {"nodes": [], "edges": []}
    assert call("vault.timeline", vault=v) == {"events": []}
    assert call("vault.briefings", vault=v) == []
    assert call("vault.requests", vault=v) == {"open": [], "resolved_count": 0}
    assert call("vault.contextFiles", vault=v) == []
    assert call("vault.readFile", vault=v, path="hot.md") == {"text": "", "exists": False}
    p = call("vault.pipeline", vault=v)
    assert p["incoming"] == [] and p["queued"] == [] and p["pending_finalization"] is None
    call("vault.summary", vault=v)


def test_old_vault_without_morgue_path(rich_vault):
    docs_path = rich_vault / ".watchdog" / "registry" / "documents.json"
    docs = json.loads(docs_path.read_text())
    for rec in docs.values():
        rec.pop("morgue_path", None)
    docs_path.write_text(json.dumps(docs))
    row = call("vault.documents", vault=V(rich_vault))[0]
    assert row["original"] is None and row["fulltext"] is None
    detail = call("vault.document", vault=V(rich_vault), sha=SHA1)
    assert detail["pages"] == []


# ── documents ────────────────────────────────────────────────────────────────────

def test_documents_newest_first_with_links(rich_vault):
    rows = call("vault.documents", vault=V(rich_vault))
    assert [r["filename"] for r in rows] == ["report-one-copy.pdf", "dup-original.pdf",
                                             "report-two.pdf", "report-one.pdf"]
    one = rows[-1]
    assert one["sha"] == SHA1 and one["title"] == "Report One" and one["ext"] == "pdf"
    assert one["note"] == "documents/report-one"
    assert one["original"] == "morgue/acme-corp/annual-report/report-one.pdf"
    assert one["fulltext"] == "morgue/acme-corp/annual-report/report-one.md"
    assert one["date_of_document"] == "2024-01-15" and one["obtained"] == "2026-02-20"
    assert one["source"] == "Registry" and one["summary"] == "Summary of Report One."
    assert one["entity_count"] == 2 and one["page_count"] == 3
    assert rows[0]["near_duplicate_of"] == "Report One"


def test_document_detail(rich_vault):
    d = call("vault.document", vault=V(rich_vault), sha=SHA1)
    assert d["title"] == "Report One" and d["frontmatter"]["file"] == "report-one.pdf"
    assert "## Key facts" in d["body"]
    facts = d["facts"]
    assert facts[0]["fact"] == "Jane Doe was appointed director"
    assert facts[0]["page"] == 2 and facts[0]["basis"] == "stated" and facts[0]["date"] == "2019-03-04"
    assert facts[0]["quote"] == "appointed director in March 2019"
    assert [e["id"] for e in facts[0]["entities"]] == ["jane-doe", "acme-corp"]
    assert facts[0]["figure_note"] is None and facts[0]["added_by"] is None
    assert facts[1]["basis"] == "inferred" and facts[1]["added_by"] == "verify"
    assert "4,500,000" in facts[1]["figure_note"] and "not found in the document" in facts[1]["figure_note"]
    assert "quote not found on cited page" in facts[1]["quote_note"]
    roles = {e["id"]: e for e in d["entities"]}
    assert roles["jane-doe"]["role"] == "director of Acme Corp"
    assert roles["acme-corp"]["role"] is None and roles["jane-doe"]["type"] == "person"
    assert d["pages"] == [{"page": 1, "text": "Page one of Report One."},
                          {"page": 2, "text": "Jane Doe was appointed director in March 2019."}]
    assert d["file_metadata"] == {"author": "Acme Finance"}
    assert d["sidecar"] == {"source": "Registry", "title": "Filing"}
    assert d["metadata"] is None
    assert (d["extract_model"], d["extract_effort"], d["record_skill_hash"]) == (
        "claude-sonnet-5-5", "medium", "abc123def456")
    assert [x["sha"] for x in d["duplicates"]] == [SHA3]


def test_document_duplicates_both_ways(rich_vault):
    copy = call("vault.document", vault=V(rich_vault), sha=SHA3)
    assert [x["sha"] for x in copy["duplicates"]] == [SHA1]
    assert copy["duplicates"][0]["note"] == "documents/report-one"
    assert call("vault.document", vault=V(rich_vault), sha=SHA2)["duplicates"] == []


def test_document_facts_fall_back_to_the_note_without_an_extraction(rich_vault):
    d = call("vault.document", vault=V(rich_vault), sha=SHA2)   # no staged extraction for report two
    assert [f["fact"] for f in d["facts"]] == ["Jane Doe was appointed director",
                                               "Revenue was $4,500,000"]
    assert d["facts"][0]["page"] == 2 and d["facts"][1]["basis"] == "inferred"


def test_document_queue_metadata(rich_vault):
    reg = rich_vault / ".watchdog" / "registry" / "documents.json"
    docs = json.loads(reg.read_text())
    docs[SHA1]["file_metadata"] = {}
    reg.write_text(json.dumps(docs))
    (rich_vault / ".watchdog" / "queue" / f"{SHA1}.json").write_text(
        json.dumps({"metadata": {"ocr_used": True}, "sidecar": "source: x"}))
    detail = call("vault.document", vault=V(rich_vault), sha=SHA1)
    assert detail["metadata"] == {"ocr_used": True}
    assert detail["file_metadata"] == {"author": "Acme Finance"}   # falls back to the extraction


def test_document_by_unique_prefix_and_unknown(rich_vault):
    assert call("vault.document", vault=V(rich_vault), sha=SHA1[:8])["sha"] == SHA1
    assert call_error("vault.document", vault=V(rich_vault), sha="deadbeef")["code"] == "not_found"
    assert call_error("vault.document", vault=V(rich_vault), sha="1")["code"] == "not_found"


# ── entities ─────────────────────────────────────────────────────────────────────

def test_entities_sorted_by_doc_count_then_name(rich_vault):
    rows = call("vault.entities", vault=V(rich_vault))
    assert [r["id"] for r in rows] == ["acme-corp", "bob-roe", "jane-doe"]
    jane = rows[2]
    assert jane["aliases"] == ["J. Doe"] and jane["doc_count"] == 2 and jane["role_count"] == 1
    assert jane["contradiction_count"] == 1 and jane["has_summary"] is True
    assert jane["summary"] == "Director of Acme Corp."          # first paragraph only
    assert jane["note"] == "entities/person/jane-doe" and jane["first_seen"] == "2026-03-01"
    assert rows[1]["has_summary"] is False and rows[1]["summary"] is None


def test_entity_detail(rich_vault):
    e = call("vault.entity", vault=V(rich_vault), id="jane-doe")
    assert e["name"] == "Jane Doe" and e["frontmatter"]["id"] == "jane-doe"
    assert e["sections"]["summary"].startswith("Director of Acme Corp.")
    assert "chairs the board" in e["sections"]["analysis"]
    assert "[!contradiction]" in e["sections"]["contradictions"]
    assert "### 2019" in e["sections"]["timeline"] and "director of" in e["sections"]["relationships"]
    assert e["sections"]["notes"].startswith("<!--")
    assert [d["sha"] for d in e["documents"]] == [SHA2, SHA1]
    assert e["relationships"] == [{
        "role": "director of", "target_id": "acme-corp", "target_name": "Acme Corp",
        "target_type": "organization", "direction": "out", "docs": [SHA1]}]
    c = e["contradictions"][0]
    assert c["summary"] == "Start date of Jane Doe's directorship" and c["resolved"] is False
    assert c["rid"] == resolutions.contradiction_id(CALLOUT) and c["text"] == CALLOUT
    assert e["timeline"][0]["text"] == "Jane Doe appointed director"
    assert e["timeline"][0]["precision"] == "day" and e["timeline"][0]["note"] == "documents/report-one"


def test_entity_relationships_in_and_unprofiled_targets(rich_vault):
    rels = call("vault.entity", vault=V(rich_vault), id="acme-corp")["relationships"]
    by_role = {(r["role"], r["target_id"]): r for r in rels}
    assert by_role[("director of", "jane-doe")]["direction"] == "in"
    ghost = by_role[("subsidiary of", "ghost-ltd")]
    assert ghost["direction"] == "out" and ghost["target_name"] == "Ghost Ltd"
    assert ghost["target_type"] == "organization"


def test_entity_contradiction_resolved_flag(rich_vault):
    rid = resolutions.contradiction_id(CALLOUT)
    resolutions.resolve(rich_vault, [rid], label="review")
    e = call("vault.entity", vault=V(rich_vault), id="jane-doe")
    assert e["contradictions"][0]["resolved"] is True and e["contradiction_count"] == 0


def test_unknown_entity(rich_vault):
    assert call_error("vault.entity", vault=V(rich_vault), id="nobody")["code"] == "not_found"


# ── graph and timeline ───────────────────────────────────────────────────────────

def test_graph_keeps_stated_edges_between_profiled_entities(rich_vault):
    g = call("vault.graph", vault=V(rich_vault))
    assert {n["id"] for n in g["nodes"]} == {"jane-doe", "acme-corp", "bob-roe"}
    assert g["edges"] == [{"source": "jane-doe", "target": "acme-corp", "role": "director of",
                           "docs": [SHA1]}]   # no reverse copy, nothing to unprofiled ghost-ltd


def test_graph_merges_repeated_edges(rich_vault):
    path = rich_vault / ".watchdog" / "registry" / "entities.json"
    ents = json.loads(path.read_text())
    ents["jane-doe"]["roles"].append({**ents["jane-doe"]["roles"][0], "source_sha256": SHA2})
    path.write_text(json.dumps(ents))
    edges = call("vault.graph", vault=V(rich_vault))["edges"]
    assert len(edges) == 1 and edges[0]["docs"] == [SHA1, SHA2]


def test_timeline_sorted_and_committed_only(rich_vault):
    events = call("vault.timeline", vault=V(rich_vault))["events"]
    assert [e["date"] for e in events] == ["2019-03-04", "2021", "2024-01"]
    assert [e["precision"] for e in events] == ["day", "year", "month"]
    first = events[0]
    assert first["sha"] == SHA1 and first["filename"] == "report-one.pdf" and first["page"] == 2
    assert [x["id"] for x in first["entities"]] == ["jane-doe", "acme-corp", "ghost-ltd"]
    assert "Never committed" not in [e["text"] for e in events]


# ── notes ────────────────────────────────────────────────────────────────────────

def test_note_reads_with_or_without_extension(rich_vault):
    a = call("vault.note", vault=V(rich_vault), path="entities/person/jane-doe")
    b = call("vault.note", vault=V(rich_vault), path="entities/person/jane-doe.md")
    assert a == b and a["exists"] is True and a["kind"] == "entity"
    assert a["path"] == "entities/person/jane-doe.md" and a["title"] == "Jane Doe"
    assert a["frontmatter"]["type"] == "person" and a["body"].lstrip().startswith("# Jane Doe")
    kinds = {"documents/report-one": "document", "briefings/2026-03-03-08-00": "briefing",
             "hot": "other", "context": "other"}
    for path, kind in kinds.items():
        assert call("vault.note", vault=V(rich_vault), path=path)["kind"] == kind


def test_note_missing_returns_exists_false(rich_vault):
    n = call("vault.note", vault=V(rich_vault), path="queries/nothing-here")
    assert n == {"path": "queries/nothing-here.md", "exists": False, "frontmatter": {}, "body": "",
                 "title": None, "kind": "query"}


@pytest.mark.parametrize("bad", ["../outside.md", "entities/../../outside", "/etc/passwd",
                                 "C:/Windows/win.ini", "entities/person/../../../x", ".watchdog/registry/documents.json",
                                 ".claude/settings.json", "morgue/acme-corp/annual-report/report-one.pdf", ""])
def test_note_refuses_escapes_hidden_and_non_notes(rich_vault, bad):
    assert call_error("vault.note", vault=V(rich_vault), path=bad)["code"] == "bad_path"


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_note_refuses_symlink_out_of_vault(rich_vault, tmp_path):
    outside = tmp_path / "secret.md"
    outside.write_text("# secret\n")
    (rich_vault / "wiki").mkdir(exist_ok=True)
    (rich_vault / "wiki" / "link.md").symlink_to(outside)
    (rich_vault / "wiki" / "dirlink").symlink_to(tmp_path, target_is_directory=True)
    assert call_error("vault.note", vault=V(rich_vault), path="wiki/link")["code"] == "bad_path"
    assert call_error("vault.note", vault=V(rich_vault), path="wiki/dirlink/secret")["code"] == "bad_path"
    assert call_error("vault.readFile", vault=V(rich_vault), path="wiki/link.md")["code"] == "bad_path"


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_write_refuses_symlinked_context_file(rich_vault, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("untouched")
    (rich_vault / "context.md").unlink()
    (rich_vault / "context.md").symlink_to(outside)
    assert call_error("vault.writeFile", vault=V(rich_vault), path="context.md", text="x")["code"] == "bad_path"
    assert outside.read_text() == "untouched"


def test_save_notes_replaces_only_the_notes_body(rich_vault):
    path = rich_vault / "entities" / "person" / "jane-doe.md"
    before = path.read_text()
    text = "Called her office.\n\n## A heading typed by the journalist\n\nMore."
    assert call("vault.saveNotes", vault=V(rich_vault), path="entities/person/jane-doe", text=text) == {"ok": True}
    after = path.read_text()
    head = before[:before.index("## Notes")]
    assert after.startswith(head) and after.endswith("## Notes\n\nCalled her office.\n\n"
                                                    "## A heading typed by the journalist\n\nMore.\n")
    assert not [p for p in path.parent.iterdir() if p.name.endswith(".tmp")]
    # The pipeline preserves the section verbatim when it next rewrites the note.
    ents = json.loads((rich_vault / ".watchdog" / "registry" / "entities.json").read_text())
    docs = json.loads((rich_vault / ".watchdog" / "registry" / "documents.json").read_text())
    rebuilt = build_entity_note(ents["jane-doe"], _extract_notes_section(path), docs, "S", "", "")
    assert "Called her office." in rebuilt and "A heading typed by the journalist" in rebuilt
    note = call("vault.note", vault=V(rich_vault), path="entities/person/jane-doe")
    assert note["frontmatter"]["name"] == "Jane Doe"
    entity = call("vault.entity", vault=V(rich_vault), id="jane-doe")
    assert entity["sections"]["notes"].startswith("Called her office.")
    assert "heading typed by the journalist" in entity["sections"]["notes"]
    assert entity["sections"]["summary"].startswith("Director of Acme Corp.")


def test_save_notes_empty_keeps_the_placeholder(rich_vault):
    path = rich_vault / "documents" / "report-one.md"
    original = path.read_text()
    call("vault.saveNotes", vault=V(rich_vault), path="documents/report-one", text="a thought")
    assert "a thought" in path.read_text() and "Reserved for journalist" not in path.read_text()
    call("vault.saveNotes", vault=V(rich_vault), path="documents/report-one", text="  \n ")
    assert path.read_text() == original      # the default placeholder is back, byte for byte


def test_save_notes_empty_keeps_an_existing_custom_comment(rich_vault):
    path = rich_vault / "documents" / "report-two.md"
    text = path.read_text().replace("<!-- Reserved for journalist annotations — never overwritten by ingestion. -->",
                                    "<!-- my own marker -->")
    path.write_text(text)
    call("vault.saveNotes", vault=V(rich_vault), path="documents/report-two", text="")
    assert "<!-- my own marker -->" in path.read_text()


def test_save_notes_adds_the_section_when_missing(rich_vault):
    path = rich_vault / "entities" / "person" / "bob-roe.md"
    path.write_text(path.read_text().split("## Notes")[0].rstrip() + "\n")
    call("vault.saveNotes", vault=V(rich_vault), path="entities/person/bob-roe", text="hello")
    assert path.read_text().endswith("\n\n## Notes\n\nhello\n")


def test_save_notes_ignores_a_notes_heading_inside_a_code_fence(rich_vault):
    path = rich_vault / "documents" / "report-one.md"
    body = path.read_text().replace("## Summary", "```\n## Notes\nnot a heading\n```\n\n## Summary")
    path.write_text(body)
    call("vault.saveNotes", vault=V(rich_vault), path="documents/report-one", text="real")
    after = path.read_text()
    assert "not a heading" in after and after.endswith("## Notes\n\nreal\n")


@pytest.mark.parametrize("path, code", [
    ("briefings/2026-03-03-08-00", "forbidden"), ("hot", "forbidden"), ("context", "forbidden"),
    ("entities", "forbidden"), ("entities/person/nobody", "not_found"), ("../x", "bad_path"),
    (".watchdog/registry/documents", "bad_path"),
])
def test_save_notes_is_limited_to_existing_entity_and_document_notes(rich_vault, path, code):
    assert call_error("vault.saveNotes", vault=V(rich_vault), path=path, text="x")["code"] == code
    assert not (rich_vault / "x.md").exists()


# ── resolveLink ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("target, expected", [
    ("documents/report-one", {"path": "documents/report-one.md", "kind": "document", "sha": SHA1, "page": None}),
    ("documents/report-one|Report One", {"path": "documents/report-one.md", "kind": "document", "sha": SHA1, "page": None}),
    ("entities/person/jane-doe", {"path": "entities/person/jane-doe.md", "kind": "entity", "sha": None, "page": None}),
    ("morgue/acme-corp/annual-report/report-one.pdf#page=3",
     {"path": "morgue/acme-corp/annual-report/report-one.pdf", "kind": "original", "sha": SHA1, "page": 3}),
    ("morgue/acme-corp/annual-report/report-two.md",
     {"path": "morgue/acme-corp/annual-report/report-two.md", "kind": "fulltext", "sha": SHA2, "page": None}),
    ("briefings/2026-03-03-08-00", {"path": "briefings/2026-03-03-08-00.md", "kind": "briefing", "sha": None, "page": None}),
    ("hot", {"path": "hot.md", "kind": "note", "sha": None, "page": None}),
    ("Jane Doe", {"path": "entities/person/jane-doe.md", "kind": "entity", "sha": None, "page": None}),
    ("J. Doe", {"path": "entities/person/jane-doe.md", "kind": "entity", "sha": None, "page": None}),
    ("Report Two", {"path": "documents/report-two.md", "kind": "document", "sha": SHA2, "page": None}),
    ("nothing/here", {"path": None, "kind": "missing", "sha": None, "page": None}),
    ("../../etc/passwd", {"path": None, "kind": "missing", "sha": None, "page": None}),
    ("/etc/passwd", {"path": None, "kind": "missing", "sha": None, "page": None}),
    (".watchdog/registry/documents.json", {"path": None, "kind": "missing", "sha": None, "page": None}),
    ("", {"path": None, "kind": "missing", "sha": None, "page": None}),
])
def test_resolve_link(rich_vault, target, expected):
    assert call("vault.resolveLink", vault=V(rich_vault), target=target) == expected


# ── pipeline state ───────────────────────────────────────────────────────────────

def test_pipeline_state(rich_vault):
    p = call("vault.pipeline", vault=V(rich_vault))
    assert [(f["name"], f["path"], f["sidecar"]) for f in p["incoming"]] == [
        ("new-file.pdf", "_INCOMING/new-file.pdf", True), ("nested.docx", "_INCOMING/sub/nested.docx", False)]
    assert p["incoming"][0]["size"] == 8 and p["incoming"][0]["modified"]
    assert p["chew_failed"] == [{"name": "bad.pdf", "path": "_INCOMING/_FAILED/bad.pdf", "size": 3}]
    assert [(s["name"], s["reason"]) for s in p["skipped"]] == [("dup.pdf", "already ingested"),
                                                                ("empty.pdf", None)]
    assert [(q["filename"], q["staged"], q["page_count"]) for q in p["queued"]] == [
        ("queued-staged.pdf", True, 4), ("queued-new.pdf", False, 4)]
    assert p["failed"] == [{"sha": "c" * 64, "filename": "broken.pdf",
                            "reason": "model returned invalid JSON"}]
    assert p["pending_finalization"] == {"docs": 0, "entities": 0}
    assert p["locks"] == {"chew": False, "ingest": False} and p["research_urls"] == 2
    assert p["batch_pending"] is None


def test_pipeline_locks_and_batch(rich_vault):
    (rich_vault / ".watchdog" / ".chew-lock").write_text("pid: cli\n")
    (rich_vault / ".watchdog" / "registry" / ".ingest-lock").write_text("pid: cli\n")
    (rich_vault / ".watchdog" / "batch-pending.json").write_text("{}")
    from watchdog.pipeline import batch_extract
    batch_extract.write_state(rich_vault, {"batch_id": "b1", "shas": [SHA1]})
    p = call("vault.pipeline", vault=V(rich_vault))
    assert p["locks"] == {"chew": True, "ingest": True}
    assert p["batch_pending"]["batch_id"] == "b1"


def test_pipeline_failure_reason_uses_the_latest_log_line(rich_vault):
    log = rich_vault / ".watchdog" / "registry" / "ingest.log"
    log.write_text(log.read_text() + "[2026-03-02T10:01:00Z] FAILED broken.pdf: rate limited\n")
    assert call("vault.pipeline", vault=V(rich_vault))["failed"][0]["reason"] == "rate limited"


# ── briefings, files, requests, context ─────────────────────────────────────────

def test_briefings_newest_first_with_kinds(rich_vault):
    rows = call("vault.briefings", vault=V(rich_vault))
    assert [(b["name"], b["kind"]) for b in rows][1:] == [
        ("2026-03-03-08-00", "briefing"), ("leads-2026-03-03", "leads"),
        ("2026-03-01-10-30", "briefing"), ("research-2026-02-01", "research")]
    assert rows[0]["kind"] == "alerts" and rows[0]["path"].startswith("briefings/alerts-")
    assert rows[1]["title"] == "Ingest briefing — 2026-03-03" and rows[1]["date"] == "2026-03-03T08:00:00"
    assert rows[2]["date"] == "2026-03-03"


def test_read_file_allow_list(rich_vault):
    ok = ["context.md", "watchlist.md", "requests.md", "hot.md", "log.md", "timeline.md", "index.md",
          "briefings/leads-2026-03-03.md", "queries/x.md", "wiki/y.md"]
    for path in ok:
        r = call("vault.readFile", vault=V(rich_vault), path=path)
        assert set(r) == {"text", "exists"}
    assert call("vault.readFile", vault=V(rich_vault), path="context.md") == {
        "text": "# Context\n\nAbout Acme.\n", "exists": True}
    assert call("vault.readFile", vault=V(rich_vault), path="index.md")["exists"] is False
    for bad in ("entities/person/jane-doe.md", ".watchdog/registry/documents.json", "../x", "/etc/passwd",
                "briefings/../context.md/../hot2.md", "documents/report-one.md"):
        assert call_error("vault.readFile", vault=V(rich_vault), path=bad)["code"] in ("forbidden", "bad_path")


def test_write_file_only_context_and_watchlist(rich_vault):
    call("vault.writeFile", vault=V(rich_vault), path="context.md", text="New context\n")
    call("vault.writeFile", vault=V(rich_vault), path="watchlist.md", text="Term\n")
    assert (rich_vault / "context.md").read_text() == "New context\n"
    assert (rich_vault / "watchlist.md").read_text() == "Term\n"
    assert not [p for p in rich_vault.iterdir() if p.name.endswith(".tmp")]
    for bad in ("hot.md", "requests.md", "entities/person/jane-doe.md", "../context.md", "briefings/x.md",
                "/tmp/evil"):
        assert call_error("vault.writeFile", vault=V(rich_vault), path=bad, text="x")["code"] in (
            "forbidden", "bad_path")
    assert (rich_vault / "hot.md").read_text().startswith("# Hot cache")


def test_write_file_creates_a_missing_context(rich_vault):
    (rich_vault / "context.md").unlink()
    call("vault.writeFile", vault=V(rich_vault), path="context.md", text="fresh")
    assert call("vault.readFile", vault=V(rich_vault), path="context.md")["text"] == "fresh"


def test_requests(rich_vault):
    r = call("vault.requests", vault=V(rich_vault))
    assert r["resolved_count"] == 1 and len(r["open"]) == 1
    item = r["open"][0]
    assert item["what"] == "The 2019 board minutes" and item["why"] == "Would fix the appointment date"
    assert item["likely_source"] == "Registry" and item["rid"].startswith("request:")
    assert item["cited_in"] == [{"sha": SHA1, "filename": "report-one.pdf", "note": "documents/report-one"}]


def test_context_files_skip_hidden(rich_vault):
    (rich_vault / "_CONTEXT" / "sub").mkdir()
    (rich_vault / "_CONTEXT" / "sub" / "b.pdf").write_bytes(b"1234")
    rows = call("vault.contextFiles", vault=V(rich_vault))
    assert [r["name"] for r in rows] == ["background.txt", "sub/b.pdf"]
    assert rows[1]["size"] == 4 and rows[1]["modified"]


# ── a vault produced by the real ingest ─────────────────────────────────────────

def test_real_pipeline_vault(tmp_path, monkeypatch):
    vault = _run_fixture_ingest(tmp_path, monkeypatch)
    v = V(vault)
    s = call("vault.summary", vault=v)
    assert s["totals"]["documents"] == 2 and s["totals"]["entities"] == 1
    assert s["briefing"]["path"].startswith("briefings/") and s["headline"] == "Early days."
    docs = call("vault.documents", vault=v)
    assert {d["filename"] for d in docs} == {"alpha.pdf", "beta.pdf"}
    alpha = next(d for d in docs if d["filename"] == "alpha.pdf")
    assert alpha["title"] == "Acme Annual Report" and alpha["date_of_document"] == "2024-01-15"
    assert alpha["original"].endswith("alpha.pdf") and alpha["fulltext"].endswith("alpha.md")
    detail = call("vault.document", vault=v, sha=alpha["sha"])
    assert detail["facts"][0]["fact"] == "Filed in 2024" and detail["facts"][0]["page"] == 1
    assert detail["pages"][0]["text"].startswith("Acme Corp filed an annual report")
    assert [e["id"] for e in detail["entities"]] == ["acme-corp"]
    ents = call("vault.entities", vault=v)
    assert ents[0]["id"] == "acme-corp" and ents[0]["doc_count"] == 2
    assert ents[0]["type"] == "organization" and ents[0]["has_summary"] is True
    ent = call("vault.entity", vault=v, id="acme-corp")
    assert len(ent["documents"]) == 2 and ent["sections"]["notes"].startswith("<!--")
    assert call("vault.graph", vault=v)["nodes"][0]["id"] == "acme-corp"
    assert call("vault.timeline", vault=v) == {"events": []}
    assert len(call("vault.briefings", vault=v)) == 1
    note = call("vault.note", vault=v, path=ent["note"])
    assert note["kind"] == "entity" and note["title"] == "Acme Corp"
    assert call("vault.resolveLink", vault=v, target=alpha["note"])["sha"] == alpha["sha"]
    # Annotating through the app survives the pipeline's own rewrite of the note.
    call("vault.saveNotes", vault=v, path=ent["note"], text="Checked the filing.")
    assert _extract_notes_section(vault / f"{ent['note']}.md").strip().endswith("Checked the filing.")
    assert call("vault.pipeline", vault=v)["queued"] == []
    assert os.path.isdir(vault)
