"""Fixtures shared by the `tests/test_gui_api_*.py` files.

`rich_vault` builds a small but fully-populated vault *through the pipeline's own renderers*
(`write_vault.build_entity_note`, `_build_document_note`, `requests.record`,
`watchlist.write_alerts`, …), so the GUI readers are tested against the real note and registry
formats rather than hand-copied approximations of them. A vault produced end to end by the real
ingest is covered separately by `tests/test_golden_vault.py::_run_fixture_ingest`.
"""

import hashlib
import json
from pathlib import Path

import pytest

import watchdog.cmd.base as _base
import watchdog.cmd.research as _research_cmd
import watchdog.cmd.setup as _setup
import watchdog.skills_catalog as _skills_catalog
from watchdog.gui import server
from watchdog.pipeline import requests as _requests
from watchdog.pipeline import resolutions
from watchdog.pipeline import watchlist as _watchlist
from watchdog.pipeline.write_vault import (
    _build_document_note, _update_manifest, build_entity_note,
)

from tests.test_write_vault import make_vault

SHA1, SHA2, SHA3 = "1" * 64, "2" * 64, "3" * 64


@pytest.fixture
def wdg_home(tmp_path, monkeypatch):
    """Every watchdog home path redirected into tmp_path (the `tests/test_cli.py` pattern, plus
    the other modules that bound `CONFIG_FILE`/`WATCHDOG_HOME` at import)."""
    home = tmp_path / "wdg-home"
    home.mkdir()
    for mod, names in ((_base, ("WATCHDOG_HOME", "PROJECTS_FILE", "CONFIG_FILE")),
                       (_setup, ("WATCHDOG_HOME", "CONFIG_FILE")),
                       (_research_cmd, ("CONFIG_FILE",))):
        for name in names:
            value = {"WATCHDOG_HOME": home, "PROJECTS_FILE": home / "projects.json",
                     "CONFIG_FILE": home / "config.json"}[name]
            monkeypatch.setattr(mod, name, value)
    monkeypatch.setattr("watchdog.config.CONFIG_FILE", home / "config.json")
    monkeypatch.setattr("watchdog.cmd.vault._obsidian_config_path", lambda: tmp_path / "obsidian.json")
    monkeypatch.setattr(_skills_catalog, "USER_SKILLS_DIR", home / "skills" / "records")
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "GEMINI_API_KEY",
                "LOCAL_API_KEY", "OPENROUTER_API_KEY", "LOCAL_BASE_URL", "OPENROUTER_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("watchdog.cmd.auth.claude_code_logged_in", lambda: True)
    return home


def register(home: Path, vault: Path, *, slug="rich", name="Rich Case", archived=False) -> None:
    path = home / "projects.json"
    projects = json.loads(path.read_text()) if path.exists() else {}
    projects[slug] = {"name": name, "path": str(vault), "description": "A test case",
                      "created_at": "2026-01-02T03:04:05", "archived": archived}
    path.write_text(json.dumps(projects))


def call(method: str, **params):
    """Call a handler through the server's dispatch, returning `result` (or raising on error)."""
    server.load_api()
    resp = server.handle({"id": 1, "method": method, "params": params})
    assert resp is not None and resp["id"] == 1
    if "error" in resp:
        raise AssertionError(f"{method} failed: {resp['error']}")
    return resp["result"]


def call_error(method: str, **params) -> dict:
    server.load_api()
    resp = server.handle({"id": 2, "method": method, "params": params})
    assert "error" in resp, f"{method} unexpectedly succeeded: {resp}"
    return resp["error"]


CALLOUT = ("> [!contradiction] Start date of Jane Doe's directorship\n"
           "> Report One says March 2019; Report Two says January 2020.")


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def make_rich_vault(tmp_path: Path) -> Path:
    vault = make_vault(tmp_path)
    reg = vault / ".watchdog" / "registry"

    def doc(sha, filename, title, note, morgue, *, ingested, near=None, pages=3,
            entities=("jane-doe", "acme-corp")):
        return {
            "sha256": sha, "filename": filename, "title": title,
            "original_path": f"incoming/{filename}", "document_note": note,
            "ingested_at": ingested, "page_count": pages, "document_type": "Annual Report",
            "record_skill": "general-records", "record_skill_hash": "abc123def456",
            "extract_model": "claude-sonnet-5-5", "extract_effort": "medium",
            "file_metadata": {"author": "Acme Finance"}, "coverage_gap": None,
            "entities_extracted": list(entities), "near_duplicate_of": near, "minhash": [],
            "morgue_path": morgue,
        }

    docs = {
        SHA1: doc(SHA1, "report-one.pdf", "Report One", "documents/report-one",
                  "morgue/acme-corp/annual-report/report-one.pdf", ingested="2026-03-01T10:30:00Z"),
        SHA2: doc(SHA2, "report-two.pdf", "Report Two", "documents/report-two",
                  "morgue/acme-corp/annual-report/report-two.pdf", ingested="2026-03-02T09:00:00Z",
                  pages=2),
        SHA3: doc(SHA3, "report-one-copy.pdf", "Report One (copy)", "documents/report-one-copy",
                  "morgue/acme-corp/annual-report/report-one-copy.pdf",
                  ingested="2026-03-03T08:00:00Z", near="[[documents/report-one|Report One]]",
                  entities=("acme-corp", "bob-roe")),
    }

    def role(rel, tid, tname, ttype, sha, *, reverse=False, basis="stated", page=2, dr=None):
        return {"relationship": rel, "target_id": tid, "target_name": tname, "target_type": ttype,
                "page": page, "basis": basis, "date_range": dr, "source_sha256": sha,
                "is_reverse": reverse}

    ents = {
        "jane-doe": {
            "id": "jane-doe", "name": "Jane Doe", "type": "person", "aliases": ["J. Doe"],
            "appears_in": [SHA1, SHA2], "note_path": "entities/person/jane-doe",
            "roles": [role("director of", "acme-corp", "Acme Corp", "organization", SHA1,
                           dr="2019–2023")],
            "timeline_events": [{"date": "2019-03-04", "event": "Jane Doe appointed director",
                                 "page": 2, "basis": "stated", "source_sha256": SHA1}],
            "contradictions": [CALLOUT],
            "date_first_seen": "2026-03-01", "date_last_updated": "2026-03-02",
        },
        "acme-corp": {
            "id": "acme-corp", "name": "Acme Corp", "type": "organization", "aliases": [],
            "appears_in": [SHA1, SHA2, SHA3], "note_path": "entities/organization/acme-corp",
            "roles": [role("director of", "jane-doe", "Jane Doe", "person", SHA1, reverse=True,
                           dr="2019–2023"),
                      role("subsidiary of", "ghost-ltd", "Ghost Ltd", "organization", SHA2,
                           basis="inferred", page=1)],
            "timeline_events": [{"date": "2024-01", "event": "Acme files its annual report",
                                 "page": 1, "basis": "stated", "source_sha256": SHA2}],
            "contradictions": [], "date_first_seen": "2026-03-01", "date_last_updated": "2026-03-03",
        },
        "bob-roe": {
            "id": "bob-roe", "name": "Bob Roe", "type": "person", "aliases": [],
            "appears_in": [SHA1, SHA2, SHA3], "note_path": "entities/person/bob-roe",
            "roles": [], "timeline_events": [], "contradictions": [],
            "date_first_seen": "2026-03-01", "date_last_updated": "2026-03-03",
        },
    }
    (reg / "documents.json").write_text(json.dumps(docs, indent=2))
    (reg / "entities.json").write_text(json.dumps(ents, indent=2))
    (reg / "registry.json").write_text(json.dumps(
        {"schema_version": "1", "document_count": 3, "entity_count": 3,
         "last_updated": "2026-03-03T08:00:00Z"}))
    _update_manifest(vault, ents)

    notes_default = "\n## Notes\n\n<!-- Journalist annotations — never overwritten by ingestion. -->\n"
    summaries = {"jane-doe": "Director of Acme Corp.\n\nSecond paragraph of the summary.",
                 "acme-corp": "A company that files annual reports.", "bob-roe": None}
    analysis = {"jane-doe": "*1 Mar 2026, via [[documents/report-one|Report One]]:* She chairs the board.",
                "acme-corp": "", "bob-roe": ""}
    for eid, e in ents.items():
        note = build_entity_note(e, notes_default, docs, summaries[eid], analysis[eid],
                                 CALLOUT if eid == "jane-doe" else "")
        _write(vault / f"{e['note_path']}.md", note)

    for sha, d in docs.items():
        entity_entries = [ents[i] for i in d["entities_extracted"]]
        body = _build_document_note(
            {"title": d["title"], "filename": d["filename"], "document_type": "Annual Report",
             "date_of_document": "2024-01-15" if sha == SHA1 else None, "source": "Registry",
             "obtained": "2026-02-20" if sha == SHA1 else None, "page_count": d["page_count"],
             "near_duplicate_of": d["near_duplicate_of"], "record_skill": "general-records",
             "record_skill_hash": "abc123def456", "extract_model": "claude-sonnet-5-5",
             "extract_effort": "medium", "summary": f"Summary of {d['title']}.",
             "key_facts": [{"fact": "Jane Doe was appointed director", "page": 2, "basis": "stated"},
                           {"fact": "Revenue was $4,500,000", "page": 1, "basis": "inferred"}]},
            entity_entries, d["morgue_path"])
        _write(vault / f"{d['document_note']}.md", body)
        morgue = vault / d["morgue_path"]
        morgue.parent.mkdir(parents=True, exist_ok=True)
        morgue.write_bytes(b"%PDF-1.4 dummy")
        _write(morgue.with_suffix(".md"),
               f"<!-- PAGE 1 -->\n\nPage one of {d['title']}.\n\n<!-- PAGE 2 -->\n\n"
               f"Jane Doe was appointed director in March 2019.\n")

    # Staged extraction for report one, with the annotations post-flight adds.
    extracted = vault / ".watchdog" / "extracted"
    extracted.mkdir(parents=True, exist_ok=True)
    (extracted / f"{SHA1}.json").write_text(json.dumps({"document": {
        "sha256": SHA1, "filename": "report-one.pdf", "title": "Report One",
        "summary": "Summary of Report One.", "sidecar": "source: Registry\ntitle: Filing",
        "file_metadata": {"author": "Acme Finance"},
        "key_facts": [
            {"fact": "Jane Doe was appointed director", "page": 2, "basis": "stated",
             "date": "2019-03-04", "quote": "appointed director in March 2019",
             "entities": ["jane-doe", "acme-corp"]},
            {"fact": "Revenue was $4,500,000 (derived)", "page": 1, "basis": "inferred",
             "entities": ["acme-corp"], "figures_unverified": ["4500000"], "added_by": "verify",
             "quote": "an invented quote", "quote_verified": False},
        ]}, "entities": [], "morgue_entity_id": "acme-corp"}))

    # Timeline: canonical files, a raw leftover of a committed document, and one of a document
    # that was never committed (must not show).
    td = vault / ".watchdog" / "timeline"
    td.mkdir(exist_ok=True)
    (td / "2019-03-04.ndjson").write_text(json.dumps(
        {"date": "2019-03-04", "event": "Jane Doe appointed director", "source_sha256": SHA1,
         "page": 2, "entity_ids": ["jane-doe", "acme-corp", "ghost-ltd"], "basis": "stated"}) + "\n")
    (td / "2024-01.ndjson").write_text(json.dumps(
        {"date": "2024-01", "event": "Acme files its annual report", "source_sha256": SHA2,
         "page": 1, "entity_ids": ["acme-corp"], "basis": "stated"}) + "\n")
    (td / "2021_2222222.ndjson").write_text(json.dumps(
        {"date": "2021", "event": "Raw leftover event", "source_sha256": SHA2, "page": None,
         "entity_ids": [], "basis": "inferred"}) + "\n")
    (td / "2022-06-01_9999999.ndjson").write_text(json.dumps(
        {"date": "2022-06-01", "event": "Never committed", "source_sha256": "9" * 64,
         "page": None, "entity_ids": [], "basis": "stated"}) + "\n")

    # Requests, resolutions.
    added = _requests.record(
        vault, [{"type": "Court filing", "what": "The 2019 board minutes",
                 "why_it_matters": "Would fix the appointment date", "likely_source": "Registry"},
                {"type": "Bank record", "what": "Ghost Ltd account statements"}],
        sha256=SHA1, filename="report-one.pdf", document_note="documents/report-one")
    resolutions.resolve(vault, [added[1]], label="manual")
    resolutions.resolve(vault, [resolutions.lead_id("isolated", "bob-roe")], label="review")
    (vault / "requests.md").write_text(_requests._format(_requests.open_requests(vault)))

    # Briefings: an ingest briefing, a leads sweep, alerts and a research memo.
    brief = vault / "briefings"
    brief.mkdir(exist_ok=True)
    _write(brief / "2026-03-01-10-30.md",
           "---\ndate: 2026-03-01T10:30:00\nfiles_ingested: 1\n---\n\n# Briefing — 2026-03-01\n\n"
           "- [ ] Check Ghost Ltd <!--wid:lead:unprofiled:ghost-ltd-->\n")
    _write(brief / "2026-03-03-08-00.md", "# Briefing — 2026-03-03\n\nNothing new.\n")
    _write(brief / "leads-2026-03-03.md", "# Investigative leads — 2026-03-03\n")
    _write(brief / "research-2026-02-01.md", "# Research memo\n")
    _watchlist.write_alerts(vault, [{
        "term": "Ghost Ltd", "filename": "report-two.pdf", "document_note": "documents/report-two",
        "entity": None, "rid": resolutions.alert_id(SHA2, "Ghost Ltd"), "page": 1,
        "snippet": "…a subsidiary of Ghost Ltd…"}])
    _write(vault / "hot.md", "# Hot cache\n\n## Investigation status\n\nThree reports in; one date conflict.\n")
    _write(vault / "watchlist.md", "# Watch list\n#\nGhost Ltd\n/Roe,?\\s+Bob/\n")
    _write(vault / "context.md", "# Context\n\nAbout Acme.\n")
    _write(vault / "log.md", "# Log\n")

    # Pipeline state: incoming files, a chew failure, a skipped duplicate, a queue, a failed doc.
    inc = vault / "incoming"
    (inc / "new-file.pdf").write_bytes(b"new file")
    (inc / "new-file.pdf.yml").write_text("source: somewhere\n")
    (inc / "sub").mkdir()
    (inc / "sub" / "nested.docx").write_bytes(b"nested")
    (inc / "failed").mkdir()
    (inc / "failed" / "bad.pdf").write_bytes(b"bad")
    (inc / "skipped").mkdir()
    dup_bytes = b"already in the registry"
    (inc / "skipped" / "dup.pdf").write_bytes(dup_bytes)
    dup_sha = hashlib.sha256(dup_bytes).hexdigest()
    docs[dup_sha] = {**docs[SHA2], "sha256": dup_sha, "filename": "dup-original.pdf",
                     "document_note": "documents/dup-original", "morgue_path": None}
    (reg / "documents.json").write_text(json.dumps(docs, indent=2))
    (inc / "skipped" / "empty.pdf").write_bytes(b"no text")

    queue = vault / ".watchdog" / "queue"
    queue.mkdir(exist_ok=True)
    for sha, name, staged in (("a" * 64, "queued-staged.pdf", True), ("b" * 64, "queued-new.pdf", False)):
        (queue / f"{sha}.json").write_text(json.dumps({
            "sha256": sha, "filename": name, "page_count": 4,
            "pages": [{"page": 1, "markdown": "x" * 400}], "metadata": {"ocr_used": False}}))
        if staged:
            (extracted / f"{sha}.json").write_text(json.dumps({"document": {"filename": name}}))
    (queue / "_failed").mkdir()
    (queue / "_failed" / f"{'c' * 64}.json").write_text(json.dumps(
        {"sha256": "c" * 64, "filename": "broken.pdf", "pages": []}))
    (reg / "ingest.log").write_text(
        "[2026-03-01T10:00:00Z] START broken.pdf\n"
        "[2026-03-01T10:01:00Z] FAILED broken.pdf: model returned invalid JSON\n"
        "[2026-03-01T10:02:00Z] OK report-one.pdf: 3p\n")
    (vault / ".watchdog" / "research").mkdir(exist_ok=True)
    (vault / ".watchdog" / "research" / "queue.tsv").write_text(
        "https://example.org/a\tA page\tnews\tcontext\nhttps://example.org/b\n")
    _write(vault / "context" / "background.txt", "background")
    (vault / "context" / ".hidden").write_text("x")
    return vault


@pytest.fixture
def rich_vault(tmp_path):
    return make_rich_vault(tmp_path)
