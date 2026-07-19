"""Integration test for the Python orchestrator: the per-document flow runs through
the REAL preflight/postflight/write_vault with the model mocked."""

import asyncio
import hashlib
import json
from pathlib import Path

import pytest

from watchdog import model_client
from watchdog.cmd import auth as auth_module
from watchdog.pipeline import batch_extract, orchestrate, schemas, timeline

from tests.test_write_vault import make_vault

_flat = model_client._flatten_prompt   # extract/section prompts are content-block lists (A1)


def _queue_doc(vault, sha="abc123", filename="test-doc.pdf", text="Acme Corp filed an annual report.",
              sidecar=None):
    """`sidecar`, if given, stands in for what chew would already have filtered into the queue
    JSON (pipeline/sidecar.py, D121) — pass already-allowlisted text, not a raw sidecar file."""
    qdir = vault / ".watchdog" / "queue"
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / f"{sha}.json").write_text(json.dumps({
        "sha256": sha, "filename": filename, "source_path": f"_INCOMING/{filename}",
        "page_count": 1, "pages": [{"page": 1, "markdown": text}],
        "near_dup": {"near_duplicates": [], "top_similarity": 0.0},
        "sidecar": sidecar,
    }))
    (vault / "_INCOMING" / filename).write_text("dummy source bytes")


def _extraction(sha="abc123", filename="test-doc.pdf", *, valid=True):
    ext = {
        "document": {
            "sha256": sha, "filename": filename, "original_path": f"_INCOMING/{filename}",
            "title": "Acme Annual Report", "document_type": "Annual Report",
            "date_of_document": "2024-01-15", "page_count": 1, "source": None, "obtained": None,
            "near_duplicate_of": None, "summary": "Acme's annual report.",
            "key_facts": [{"fact": "Filed in 2024", "page": 1, "basis": "stated"}],
        },
        "entities": [{
            "id": "acme-corp", "name": "Acme Corp", "type": "Company", "aliases": [],
            "summary": "A company that filed an annual report.", "timeline_events": [], "roles": [],
        }],
        "morgue_entity_id": "acme-corp", "morgue_document_type": "annual-report",
        "scratchpad": "# notes\n- filed 2024",
    }
    if not valid:
        del ext["morgue_entity_id"]   # post-flight rejects this
    return ext


def _mock(monkeypatch, *, extraction):
    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        parsed = {
            "classify": {"skill": "general-records.md"},
            "extract": extraction,
            "entity-synthesis": {"entity_syntheses": []},
            "timeline-dedup": {"groups": []},
            "briefing": {"investigation_status": "Early days.",
                         "what_was_ingested": ["test-doc.pdf — Annual Report"],
                         "new_entities": ["Acme Corp"]},
        }.get(task, extraction)
        return model_client.ModelResult(parsed=parsed, text="", model="m",
                                         backend="claude-agent-sdk", auth_mode="subscription", cost_usd=0.01)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)


def test_postflight_quote_warning_prints_after_this_documents_ok_line(tmp_path, monkeypatch, capsys):
    """A post-flight warning (quote-verify, entity-id/date sanitization) must land *after* its
    own document's OK line, not whenever post-flight happened to run — otherwise it reads as
    belonging to whichever document is concurrently in flight at that moment (#333 follow-up)."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, text="Acme Corp filed an annual report.")
    ext = _extraction()
    ext["document"]["key_facts"][0]["quote"] = "this exact sentence never appears in the document"
    _mock(monkeypatch, extraction=ext)

    asyncio.run(orchestrate.run(vault))

    out = capsys.readouterr().out
    ok_index = out.index("OK")
    warn_index = out.index("quote not found")
    assert ok_index < warn_index


def test_ingest_log_records_start_before_ok(tmp_path, monkeypatch):
    """A per-document START line is logged when extraction begins, ahead of its OK line —
    with concurrent extraction the completion-ordered log otherwise hides the staggered
    starts (#317 follow-up)."""
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    _mock(monkeypatch, extraction=_extraction())

    asyncio.run(orchestrate.run(vault))

    log = (vault / ".watchdog" / "registry" / "ingest.log").read_text(encoding="utf-8")
    assert "START test-doc.pdf" in log
    assert log.index("START test-doc.pdf") < log.index("OK test-doc.pdf")


def test_call_model_logs_pruned_keys_to_ingest_log(tmp_path, monkeypatch):
    """#412/D124: when `model_client.acomplete_json` reports pruned keys and a vault is
    passed, `_call_model` writes a WARN line to ingest.log naming them."""
    vault = make_vault(tmp_path)

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        return model_client.ModelResult(
            parsed={"name": "Acme"}, text="", model="m", backend="claude-api",
            auth_mode="api-key", cost_usd=0.0,
            pruned=["extra_field", "entities[0].roles[0].date"])
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    asyncio.run(orchestrate._call_model(task="extract", prompt="p", schema=schemas.EXTRACTION,
                                        filename="doc.pdf", vault=vault))

    log = (vault / ".watchdog" / "registry" / "ingest.log").read_text(encoding="utf-8")
    assert ("WARN doc.pdf: pruned unexpected JSON key(s) from model output: "
           "extra_field, entities[0].roles[0].date") in log


def test_call_model_without_vault_does_not_log(tmp_path, monkeypatch):
    """No vault in scope (genuinely out of scope call sites) — pruning is still recorded on
    the result, but there is nowhere to log to, so `_call_model` must not raise."""
    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        return model_client.ModelResult(
            parsed={"name": "Acme"}, text="", model="m", backend="claude-api",
            auth_mode="api-key", cost_usd=0.0, pruned=["extra_field"])
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    r = asyncio.run(orchestrate._call_model(task="extract", prompt="p", schema=schemas.EXTRACTION))
    assert r.pruned == ["extra_field"]


def test_briefing_facts_projects_fact_and_date_only():
    """The briefing projection (#150) keeps the fact text and a date when present, and drops
    page/basis/entities/quote — narrative noise the briefing doesn't need."""
    doc = {"key_facts": [
        {"fact": "Filed in 2024", "page": 3, "basis": "stated", "entities": ["acme"]},
        {"fact": "Order issued", "date": "2024-01-15", "quote": "It is ordered…", "page": 1},
    ]}
    assert orchestrate._briefing_facts(doc) == [
        {"fact": "Filed in 2024"},
        {"fact": "Order issued", "date": "2024-01-15"},
    ]


def test_briefing_facts_empty_when_no_key_facts():
    assert orchestrate._briefing_facts({}) == []


def test_compact_result_carries_key_facts_for_the_briefing():
    """key_facts ride along on the compact result so the briefing (and a standalone finalize,
    which reads only result_*.json) can draw figures + chronology from them (#150)."""
    extraction = {
        "document": {"document_type": "Annual Report", "date_of_document": "2024-01-15",
                     "key_facts": [{"fact": "Revenue was $5M", "page": 2, "basis": "stated"},
                                   {"fact": "Merger closed", "date": "2024-03-01"}]},
        "entities": [{"id": "acme", "name": "Acme", "type": "Company"}],
    }
    r = orchestrate._compact_result("sha1", "doc.pdf", extraction, {}, 0.01,
                                    {"new_entities": ["acme"], "updated_entities": []})
    assert r["key_facts"] == [{"fact": "Revenue was $5M"},
                              {"fact": "Merger closed", "date": "2024-03-01"}]
    # new/updated split comes from the writer's report, not from a model-emitted field (#381/D118)
    assert r["new_entities"] == ["acme"]
    assert r["updated_entities"] == []
    assert "contradictions" not in r        # a single document cannot flag one


def test_write_briefing_resolves_entity_ids_to_display_names(tmp_path):
    """#342: on backends that echo the internal id rather than the display name for a new
    entity, the briefing and hot.md must still show the display name — resolved deterministically
    against the registry manifest for this batch, rather than trusting the model."""
    vault = make_vault(tmp_path)
    (vault / ".watchdog" / "registry" / "manifest.json").write_text(json.dumps({
        "andrew-hanrahan": {"name": "Andrew Hanrahan", "type": "person", "aliases": [],
                            "note_path": "entities/person/andrew-hanrahan"},
        "fsra": {"name": "Financial Services Regulatory Authority", "type": "public-body",
                 "aliases": [], "note_path": "entities/public-body/fsra"},
    }))
    b = {
        "investigation_status": "Early days.",
        "what_was_ingested": ["doc.pdf — Annual Report"],
        "new_entities": ["andrew-hanrahan", "fsra"],
    }
    slug_path = orchestrate._write_briefing(vault, b, [], [], [])

    briefing_text = (vault / slug_path).read_text(encoding="utf-8")
    hot_text = (vault / "hot.md").read_text(encoding="utf-8")
    for text in (briefing_text, hot_text):
        assert "Andrew Hanrahan" in text
        assert "Financial Services Regulatory Authority" in text
        assert "andrew-hanrahan" not in text
        assert "fsra" not in text


def test_write_briefing_leaves_unmatched_items_unchanged(tmp_path):
    """Prose sentences and unknown slugs are not exact matches against the manifest, so they
    pass through unchanged — the resolver only fires on a whole-item match (#342); it must not
    rewrite substrings inside prose, which risks corrupting legitimate text."""
    vault = make_vault(tmp_path)
    (vault / ".watchdog" / "registry" / "manifest.json").write_text(json.dumps({
        "andrew-hanrahan": {"name": "Andrew Hanrahan", "type": "person", "aliases": [],
                            "note_path": "entities/person/andrew-hanrahan"},
    }))
    b = {
        "investigation_status": "Early days.",
        "what_was_ingested": ["doc.pdf — Annual Report"],
        "new_entities": ["some-unknown-slug"],
        "connections": ["Mentions andrew-hanrahan in passing alongside a numbered company."],
    }
    slug_path = orchestrate._write_briefing(vault, b, [], [], [])

    briefing_text = (vault / slug_path).read_text(encoding="utf-8")
    assert "some-unknown-slug" in briefing_text                    # unknown id: untouched
    assert "Mentions andrew-hanrahan in passing" in briefing_text  # id inside prose: untouched


def test_write_briefing_handles_missing_manifest(tmp_path):
    """No manifest.json yet (a vault's very first ingest) must not crash the briefing — items
    just pass through unresolved (#342)."""
    vault = make_vault(tmp_path)
    assert not (vault / ".watchdog" / "registry" / "manifest.json").exists()
    b = {
        "investigation_status": "Early days.",
        "what_was_ingested": ["doc.pdf — Annual Report"],
        "new_entities": ["acme-corp"],
    }
    slug_path = orchestrate._write_briefing(vault, b, [], [], [])   # must not raise

    assert "acme-corp" in (vault / slug_path).read_text(encoding="utf-8")


def test_select_kept_keeps_survivors_in_original_order():
    """timeline-dedup returns `groups`; Python re-selects the authoritative originals (which carry
    source_sha256/page/basis), order-preserving, dropping each group's folded duplicates."""
    events = [
        {"event": "A", "source_sha256": "sha-a", "page": 1},
        {"event": "B", "source_sha256": "sha-b", "page": 2},
        {"event": "C", "source_sha256": "sha-c", "page": 3},
    ]
    kept = orchestrate._select_kept(
        events, [{"keep": 0, "duplicates": [1]}, {"keep": 2, "duplicates": []}])
    assert [e["event"] for e in kept] == ["A", "C"]
    assert kept[0]["source_sha256"] == "sha-a"   # authoritative original carried through


def test_select_kept_unions_entity_tags_of_dropped_duplicates():
    """A collapsed group's survivor carries the union of its own and its dropped duplicates'
    entity_ids (#237) — attribution survives regardless of which restatement the model kept.
    The originals are left unmutated."""
    events = [
        {"event": "Acme filed", "entity_ids": ["acme"]},
        {"event": "Acme filed for bankruptcy", "entity_ids": ["acme", "alice"]},
    ]
    kept = orchestrate._select_kept(events, [{"keep": 0, "duplicates": [1]}])
    assert len(kept) == 1
    assert kept[0]["entity_ids"] == ["acme", "alice"]   # unioned, order-preserving, deduped
    assert events[0]["entity_ids"] == ["acme"]          # original untouched


def test_select_kept_preserves_events_the_model_never_placed():
    """Dedup must never lose events: an index the model omits from every group stays kept."""
    events = [{"event": "A"}, {"event": "B"}, {"event": "C"}]
    kept = orchestrate._select_kept(events, [{"keep": 0, "duplicates": []}])
    assert [e["event"] for e in kept] == ["A", "B", "C"]


def test_select_kept_falls_back_to_all_on_bad_input():
    events = [{"event": "A"}, {"event": "B"}]
    assert orchestrate._select_kept(events, None) == events   # missing/non-list → keep all
    assert orchestrate._select_kept(events, []) == events     # no groups → nothing placed, keep all
    # an out-of-range keep skips the whole group, so its members stay unplaced and are kept
    assert orchestrate._select_kept(events, [{"keep": 9, "duplicates": [0]}]) == events


def test_select_kept_never_empties_a_date_on_all_invalid_groups(tmp_path):
    """A dedup response whose groups are entirely unusable (out-of-range keeps, garbage members)
    must leave every event standing — never an empty `kept` that the collision loop would write
    back as an emptied canonical, silently wiping the date (#250, G2)."""
    events = [{"event": "A"}, {"event": "B"}, {"event": "C"}]
    kept = orchestrate._select_kept(events, [
        {"keep": 42, "duplicates": [0, 1]},   # out-of-range keep → group skipped
        {"keep": -1, "duplicates": [2]},      # negative keep → group skipped
        {"keep": "x", "duplicates": None},    # non-int keep → group skipped
        "not-a-dict",                          # ignored
    ])
    assert kept == events   # nothing placed → nothing dropped → all survive


def test_stamp_document_overwrites_model_identity():
    """Identity fields are stamped from Python, overriding whatever the model emitted."""
    pf = {"filename": "real.pdf", "original_path": "_INCOMING/real.pdf",
          "page_count": 7, "pages": [{}]}
    ext = {"document": {"sha256": "WRONGSHA", "filename": "wrong.pdf", "page_count": 999}}
    orchestrate._stamp_document(ext, sha="realsha", pf=pf, skill_label="court-documents")
    d = ext["document"]
    assert d["sha256"] == "realsha"
    assert d["filename"] == "real.pdf"
    assert d["original_path"] == "_INCOMING/real.pdf"
    assert d["page_count"] == 7
    assert d["record_skill"] == "court-documents"


def test_stamp_document_derives_morgue_type_from_document_type():
    """morgue_document_type is slugify(document_type), derived in Python — the model's value
    (if any) is overridden."""
    pf = {"filename": "f.pdf", "original_path": None, "page_count": 1, "pages": [{}]}
    ext = {"document": {"document_type": "CCAA Initial Order"}, "morgue_document_type": "WRONG"}
    orchestrate._stamp_document(ext, sha="s", pf=pf, skill_label="court-documents")
    assert ext["morgue_document_type"] == "ccaa-initial-order"


def test_stamp_document_morgue_type_falls_back_when_no_type():
    pf = {"filename": "f.pdf", "original_path": None, "page_count": 1, "pages": [{}]}
    ext = {"document": {}}
    orchestrate._stamp_document(ext, sha="s", pf=pf, skill_label="general-records")
    assert ext["morgue_document_type"] == "document"


def test_stamp_document_slugifies_morgue_entity_id_with_spaces():
    """morgue_entity_id is used raw as a morgue path segment (write_vault) — a model value with
    spaces must be slugified so it doesn't produce a broken morgue directory (#262)."""
    pf = {"filename": "f.pdf", "original_path": None, "page_count": 1, "pages": [{}]}
    ext = {"document": {}, "morgue_entity_id": "Acme Corp"}
    orchestrate._stamp_document(ext, sha="s", pf=pf, skill_label="general-records")
    assert ext["morgue_entity_id"] == "acme-corp"


def test_stamp_document_slugifies_morgue_entity_id_with_embedded_slash():
    """An embedded path separator (e.g. from the model nesting a subsidiary name) must not
    survive into the morgue path segment (#262)."""
    pf = {"filename": "f.pdf", "original_path": None, "page_count": 1, "pages": [{}]}
    ext = {"document": {}, "morgue_entity_id": "acme/subsidiary"}
    orchestrate._stamp_document(ext, sha="s", pf=pf, skill_label="general-records")
    assert "/" not in ext["morgue_entity_id"]


def test_stamp_document_records_extraction_provenance():
    """record_skill_hash/extract_model/extract_effort (#268) are stamped alongside record_skill
    so a vault can later tell which skill content/model/effort produced a given extraction."""
    pf = {"filename": "f.pdf", "original_path": None, "page_count": 1, "pages": [{}]}
    ext = {"document": {}}
    orchestrate._stamp_document(ext, sha="s", pf=pf, skill_label="general-records",
                                skill_text="SKILL BODY", extract_model="sonnet", extract_effort="low")
    d = ext["document"]
    assert d["extract_model"] == "claude-sonnet-4-6"   # resolved from the tier name
    assert d["extract_effort"] == "low"
    assert d["record_skill_hash"] == hashlib.sha256(b"SKILL BODY").hexdigest()[:12]


def test_stamp_document_provenance_defaults_to_none_when_not_supplied():
    """The three new params are optional, so existing call sites/tests that omit them keep
    working — the fields are simply stamped null rather than left off the document."""
    pf = {"filename": "f.pdf", "original_path": None, "page_count": 1, "pages": [{}]}
    ext = {"document": {}}
    orchestrate._stamp_document(ext, sha="s", pf=pf, skill_label="general-records")
    d = ext["document"]
    assert d["record_skill_hash"] is None
    assert d["extract_model"] is None
    assert d["extract_effort"] is None


def test_stamp_document_stamps_file_metadata_from_preflight():
    """file_metadata (#369) is stamped from pf, the values the pipeline already holds — never
    asked of the model — same posture as sha256/filename above it."""
    fm = {"author": "Jane Doe", "producer": "Acrobat Distiller"}
    pf = {"filename": "f.pdf", "original_path": None, "page_count": 1, "pages": [{}],
          "file_metadata": fm}
    ext = {"document": {}}
    orchestrate._stamp_document(ext, sha="s", pf=pf, skill_label="general-records")
    assert ext["document"]["file_metadata"] == fm


def test_stamp_document_defaults_file_metadata_to_empty_dict():
    pf = {"filename": "f.pdf", "original_path": None, "page_count": 1, "pages": [{}]}
    ext = {"document": {}}
    orchestrate._stamp_document(ext, sha="s", pf=pf, skill_label="general-records")
    assert ext["document"]["file_metadata"] == {}


def test_sidecar_skill_resolves_known_name():
    """The sidecar text handed in here is already filtered (pipeline/sidecar.py, D121) — chew's
    own filtering is tested separately in tests/test_sidecar.py."""
    resolved = orchestrate._sidecar_skill("skill: bankruptcy\n", filename="doc.pdf")
    assert resolved is not None and Path(resolved).stem == "bankruptcy"


def test_sidecar_skill_absent_or_malformed():
    assert orchestrate._sidecar_skill(None, filename="missing.pdf") is None
    assert orchestrate._sidecar_skill("just a string, not a map\n", filename="bad.pdf") is None
    assert orchestrate._sidecar_skill("source: https://x\n", filename="nokey.pdf") is None


def test_sidecar_skill_unknown_name_warns_and_falls_back(capsys):
    assert orchestrate._sidecar_skill("skill: not-a-real-skill\n", filename="doc.pdf") is None
    assert "not-a-real-skill" in capsys.readouterr().out


def test_stamp_document_applies_sidecar_provenance():
    pf = {"filename": "real.pdf", "original_path": "_INCOMING/real.pdf", "page_count": 1,
          "pages": [{}], "sidecar": "source: FOI A-2026-001\nobtained: 2026-06-05\n"}
    ext = {"document": {}}   # model emitted no source/obtained
    orchestrate._stamp_document(ext, sha="s", pf=pf, skill_label="foi-responses")
    assert ext["document"]["source"] == "FOI A-2026-001"
    assert ext["document"]["obtained"] == "2026-06-05"
    assert ext["document"]["sidecar"] == pf["sidecar"]


def test_orchestrator_extracts_and_writes_vault(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    _mock(monkeypatch, extraction=_extraction())

    summary = asyncio.run(orchestrate.run(vault))

    assert summary["extracted"] == 1 and summary["failed"] == 0
    # real write_vault produced the notes
    assert (vault / "entities" / "organization" / "acme-corp.md").exists()
    assert list((vault / "documents").glob("*.md"))
    # housekeeping: queue file consumed; post-ingest finalized and cleaned its per-run inputs
    # (the scratchpad is consumed by the briefing, then removed on a clean finalize)
    assert not (vault / ".watchdog" / "queue" / "abc123.json").exists()
    assert "post_ingest" in summary
    assert not (vault / ".watchdog" / "tmp" / "notes_abc123.md").exists()
    # compact result block
    r = summary["results"][0]
    assert r["status"] == "ok" and r["entity_count"] == 1
    assert r["new_entities"] == ["acme-corp"] and r["document_type"] == "Annual Report"

    # post-ingest ran: briefing + hot.md + log.md + timeline written
    assert "post_ingest" in summary
    assert list((vault / "briefings").glob("*.md"))
    assert (vault / "hot.md").exists()
    assert "— Ingest" in (vault / "log.md").read_text()
    assert (vault / "timeline.md").exists()


def test_extraction_prompt_is_invariant_to_vault_entity_state(tmp_path, monkeypatch):
    """AC #381/D118: extraction is a pure function of the document, so its prompt must be
    byte-identical whether the vault is empty or already full of entities. A registry that changes
    the prompt is exactly what made extraction depend on ingest order and concurrency wave — the
    same document could get a different prompt depending on what had landed before it.

    (`known_document_types` is the one deliberate registry read that survives; both vaults here
    have an empty documents.json, so it is held constant and the test isolates entity state.)"""
    captured: list[str] = []

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "extract":
            captured.append(_flat(prompt))
        return model_client.ModelResult(
            parsed={"classify": {"skill": "general-records.md"},
                    "extract": _extraction()}.get(task, {"entity_syntheses": [], "groups": [],
                                                         "merges": [], "contradictions": [],
                                                         "investigation_status": "x",
                                                         "what_was_ingested": []}),
            text="", model="m", backend="claude-agent-sdk", auth_mode="subscription", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    # Empty vault.
    v1 = make_vault(tmp_path / "empty")
    _queue_doc(v1, text="Acme Corp filed an annual report.")
    asyncio.run(orchestrate.run(v1))

    # A vault already carrying a populated entity registry — the thing that used to change the prompt.
    v2 = make_vault(tmp_path / "populated")
    (v2 / ".watchdog" / "registry" / "entities.json").write_text(json.dumps({
        "acme-corp": {"id": "acme-corp", "name": "Acme Corp", "type": "organization",
                      "aliases": ["ACME"], "appears_in": ["old-sha"],
                      "note_path": "entities/organization/acme-corp", "roles": [],
                      "timeline_events": [{"date": "2019-01-01", "event": "Prior event"}]},
    }))
    (v2 / ".watchdog" / "registry" / "manifest.json").write_text(json.dumps({
        "acme-corp": {"name": "Acme Corp", "type": "organization", "aliases": ["ACME"],
                      "note_path": "entities/organization/acme-corp"},
    }))
    _queue_doc(v2, text="Acme Corp filed an annual report.")
    asyncio.run(orchestrate.run(v2))

    assert len(captured) == 2
    assert captured[0] == captured[1]                 # byte-identical despite the populated registry
    assert "EXISTING_ENTITIES" not in captured[0]     # and no vault state rode along at all


def test_cross_document_contradiction_caught_and_fed_to_briefing(tmp_path, monkeypatch):
    """AC #381/D118: a contradiction between two documents about one entity is caught by the
    finalizer's reconciliation pass — the only stage that sees both claims — annotated on the
    entity's note, and counted in the briefing's contradiction flags. Neither document's own
    extraction could ever have seen the other's claim."""
    contradiction_item = {
        "entity_id": "acme-corp", "label": "Insolvency date",
        "a_value": "insolvent as of 2023-01-01", "a_doc": "doc-one", "a_page": 1,
        "b_value": "insolvent as of 2024-06-01", "b_doc": "doc-two", "b_page": 1,
    }

    def _ext(sha, filename, fact):
        return {
            "document": {"sha256": sha, "filename": filename,
                         "original_path": f"_INCOMING/{filename}",
                         "title": filename, "document_type": "Filing",
                         "date_of_document": "2024-01-15", "page_count": 1,
                         "source": None, "obtained": None, "near_duplicate_of": None,
                         "summary": "A filing.",
                         "key_facts": [{"fact": fact, "page": 1, "basis": "stated",
                                        "entities": ["acme-corp"]}]},
            "entities": [{"id": "acme-corp", "name": "Acme Corp", "type": "Company",
                          "aliases": [], "roles": []}],
            "morgue_entity_id": "acme-corp", "morgue_document_type": "filing",
            "scratchpad": "",
        }

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        flat = _flat(prompt)
        if task == "classify":
            parsed = {"skill": "general-records.md"}
        elif task == "extract":
            if "INSOLVENT-A" in flat:
                parsed = _ext("sha-one", "doc-one.pdf", "Acme was INSOLVENT-A as of 2023-01-01")
            else:
                parsed = _ext("sha-two", "doc-two.pdf", "Acme was INSOLVENT-B as of 2024-06-01")
        elif task == "reconcile":
            parsed = {"merges": [], "contradictions": [contradiction_item]}
        elif task == "entity-synthesis":
            parsed = {"entity_syntheses": []}
        elif task == "timeline-dedup":
            parsed = {"groups": []}
        elif task == "briefing":
            parsed = {"investigation_status": "x", "what_was_ingested": []}
        else:
            parsed = {}
        return model_client.ModelResult(parsed=parsed, text="", model="m",
                                        backend="claude-agent-sdk", auth_mode="subscription",
                                        cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="sha-one", filename="doc-one.pdf",
               text="Acme was INSOLVENT-A as of 2023-01-01")
    _queue_doc(vault, sha="sha-two", filename="doc-two.pdf",
               text="Acme was INSOLVENT-B as of 2024-06-01")

    summary = asyncio.run(orchestrate.run(vault))
    assert summary["extracted"] == 2

    # The two documents collapsed onto one entity (exact-name reconcile in write_vault), so the
    # finalizer had both claims in one ledger to compare.
    note = (vault / "entities" / "organization" / "acme-corp.md").read_text()
    assert "[!contradiction] Insolvency date" in note
    assert "documents/doc-one" in note and "documents/doc-two" in note

    # And it reached the briefing's flagged count — fed by reconciliation, not by any single doc.
    assert summary["post_ingest"]["contradictions"]
    assert "Contradictions flagged:** 1" in (vault / "log.md").read_text()


def test_reconcile_failure_leaves_batch_finalizable(tmp_path, monkeypatch):
    """A reconcile failure must not leave the batch looking clean: `finalize()` only clears the
    fragment queue when `out` has neither `error` nor `briefing_error`, so if reconcile fails but
    synthesis and briefing succeed, `out["error"]` must still be set — otherwise the queue
    `reconcile._touched_ids` reads is deleted and the documented recovery (`watchdog finalize`)
    silently no-ops."""
    def _ext(sha, filename, fact):
        return {
            "document": {"sha256": sha, "filename": filename,
                         "original_path": f"_INCOMING/{filename}",
                         "title": filename, "document_type": "Filing",
                         "date_of_document": "2024-01-15", "page_count": 1,
                         "source": None, "obtained": None, "near_duplicate_of": None,
                         "summary": "A filing.",
                         "key_facts": [{"fact": fact, "page": 1, "basis": "stated",
                                        "entities": ["acme-corp"]}]},
            "entities": [{"id": "acme-corp", "name": "Acme Corp", "type": "Company",
                          "aliases": [], "roles": []}],
            "morgue_entity_id": "acme-corp", "morgue_document_type": "filing",
            "scratchpad": "",
        }

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        flat = _flat(prompt)
        if task == "classify":
            parsed = {"skill": "general-records.md"}
        elif task == "extract":
            if "ONE" in flat:
                parsed = _ext("sha-one", "doc-one.pdf", "Acme filed ONE")
            else:
                parsed = _ext("sha-two", "doc-two.pdf", "Acme filed TWO")
        elif task == "reconcile":
            raise model_client.ModelError("reconcile boom")
        elif task == "entity-synthesis":
            parsed = {"entity_syntheses": []}
        elif task == "timeline-dedup":
            parsed = {"groups": []}
        elif task == "briefing":
            parsed = {"investigation_status": "x", "what_was_ingested": []}
        else:
            parsed = {}
        return model_client.ModelResult(parsed=parsed, text="", model="m",
                                        backend="claude-agent-sdk", auth_mode="subscription",
                                        cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="sha-one", filename="doc-one.pdf", text="Acme filed ONE")
    _queue_doc(vault, sha="sha-two", filename="doc-two.pdf", text="Acme filed TWO")

    summary = asyncio.run(orchestrate.run(vault))
    assert summary["extracted"] == 2

    assert "reconcile boom" in summary["post_ingest"]["error"]
    assert (vault / ".watchdog" / "tmp" / "entity-fragments" / "_queue.json").exists()


def test_orchestrator_reports_failed_on_postflight_rejection(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    _mock(monkeypatch, extraction=_extraction(valid=False))   # missing morgue_entity_id

    summary = asyncio.run(orchestrate.run(vault))

    assert summary["failed"] == 1 and summary["extracted"] == 0
    assert summary["results"][0]["status"] == "failed"
    assert "post-flight rejected" in summary["results"][0]["reason"]
    # abort cleanup: queue file moved to _failed/ (preserved, not auto-retried), failure logged
    assert not (vault / ".watchdog" / "queue" / "abc123.json").exists()
    assert (vault / ".watchdog" / "queue" / "_failed" / "abc123.json").exists()
    assert "FAILED" in (vault / ".watchdog" / "registry" / "ingest.log").read_text()


def test_orchestrator_empty_queue(tmp_path):
    vault = make_vault(tmp_path)
    summary = asyncio.run(orchestrate.run(vault))
    assert summary == {"results": [], "extracted": 0, "skipped": 0, "failed": 0}


def test_orchestrator_threads_configured_models(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    seen = []

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        seen.append((task, model))
        parsed = {
            "classify": {"skill": "general-records.md"},
            "extract": _extraction(),
            "entity-synthesis": {"entity_syntheses": []},
            "timeline-dedup": {"groups": []},
            "briefing": {"investigation_status": "x", "what_was_ingested": []},
        }.get(task, _extraction())
        return model_client.ModelResult(parsed=parsed, text="", model=model or "?",
                                        backend="b", auth_mode="subscription", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    asyncio.run(orchestrate.run(vault, extract_model="opus", post_model="haiku", classify_model="haiku"))
    by_task = dict(seen)
    assert by_task["classify"] == "haiku"
    assert by_task["extract"] == "opus"
    assert by_task["briefing"] == "haiku"


def test_orchestrator_threads_configured_efforts(tmp_path, monkeypatch):
    """extract_effort / post_effort reach the right stages; classify gets no effort (D34)."""
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    seen = []

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        seen.append((task, effort))
        parsed = {
            "classify": {"skill": "general-records.md"},
            "extract": _extraction(),
            "entity-synthesis": {"entity_syntheses": []},
            "timeline-dedup": {"groups": []},
            "briefing": {"investigation_status": "x", "what_was_ingested": []},
        }.get(task, _extraction())
        return model_client.ModelResult(parsed=parsed, text="", model=model or "?",
                                        backend="b", auth_mode="subscription", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    asyncio.run(orchestrate.run(vault, extract_effort="low", post_effort="medium"))
    by_task = dict(seen)
    assert by_task["classify"] is None      # classify never gets an effort
    assert by_task["extract"] == "low"
    assert by_task["briefing"] == "medium"


def test_orchestrator_threads_configured_backends(tmp_path, monkeypatch):
    """extract_backend / post_backend / classify_backend reach the right stages (D37)."""
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    seen = []

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        seen.append((task, backend))
        parsed = {
            "classify": {"skill": "general-records.md"},
            "extract": _extraction(),
            "entity-synthesis": {"entity_syntheses": []},
            "timeline-dedup": {"groups": []},
            "briefing": {"investigation_status": "x", "what_was_ingested": []},
        }.get(task, _extraction())
        return model_client.ModelResult(parsed=parsed, text="", model=model or "?",
                                        backend=backend or "b", auth_mode="api-key", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    asyncio.run(orchestrate.run(vault, extract_backend="deepseek", post_backend="openai",
                                classify_backend="claude-api"))
    by_task = dict(seen)
    assert by_task["classify"] == "claude-api"
    assert by_task["extract"] == "deepseek"
    assert by_task["briefing"] == "openai"


def test_orchestrator_updates_graph_colours(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    (vault / ".obsidian").mkdir()
    (vault / ".obsidian" / "graph.json").write_text(json.dumps({"colorGroups": []}))
    _mock(monkeypatch, extraction=_extraction())

    asyncio.run(orchestrate.run(vault))

    graph = json.loads((vault / ".obsidian" / "graph.json").read_text())
    queries = [g["query"] for g in graph["colorGroups"]]
    assert "path:entities/organization" in queries     # Acme Corp → entities/organization/


def test_classifier_sees_only_first_n_pages(tmp_path, monkeypatch):
    """classify_pages bounds the classifier excerpt to the first N pages (page-aware)."""
    vault = make_vault(tmp_path)
    qdir = vault / ".watchdog" / "queue"
    qdir.mkdir(parents=True, exist_ok=True)
    pages = [{"page": i, "markdown": f"distinctword{i}"} for i in (1, 2, 3)]
    (qdir / "abc123.json").write_text(json.dumps({
        "sha256": "abc123", "filename": "test-doc.pdf", "source_path": "_INCOMING/test-doc.pdf",
        "page_count": 3, "pages": pages,
        "near_dup": {"near_duplicates": [], "top_similarity": 0.0},
    }))
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")

    seen = {}
    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "classify":
            seen["prompt"] = prompt
        parsed = {
            "classify": {"skill": "general-records.md"},
            "extract": _extraction(),
            "entity-synthesis": {"entity_syntheses": []},
            "briefing": {"investigation_status": "x", "what_was_ingested": []},
        }.get(task, {"events": []})
        return model_client.ModelResult(parsed=parsed, text="", model="m",
                                        backend="b", auth_mode="subscription", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    asyncio.run(orchestrate.run(vault, classify_pages=2))
    assert "distinctword1" in seen["prompt"] and "distinctword2" in seen["prompt"]
    assert "distinctword3" not in seen["prompt"]   # page 3 excluded


def test_classifier_sees_the_sidecar(tmp_path, monkeypatch):
    """The document's sidecar — already filtered into the queue JSON at chew time (D121) —
    is passed to the classify call."""
    vault = make_vault(tmp_path)
    qdir = vault / ".watchdog" / "queue"
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / "abc123.json").write_text(json.dumps({
        "sha256": "abc123", "filename": "test-doc.pdf", "source_path": "_INCOMING/test-doc.pdf",
        "page_count": 1, "pages": [{"page": 1, "markdown": "opaque table"}],
        "near_dup": {"near_duplicates": [], "top_similarity": 0.0},
        "sidecar": "source: https://example.gov/lobby-registry\nnotes: sidecarhint\n",
    }))
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")

    seen = {}
    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "classify":
            seen["prompt"] = prompt
        parsed = {
            "classify": {"skill": "general-records.md"},
            "extract": _extraction(),
            "entity-synthesis": {"entity_syntheses": []},
            "briefing": {"investigation_status": "x", "what_was_ingested": []},
        }.get(task, {"events": []})
        return model_client.ModelResult(parsed=parsed, text="", model="m",
                                        backend="b", auth_mode="subscription", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    asyncio.run(orchestrate.run(vault))
    assert "sidecarhint" in seen["prompt"]
    assert "lobby-registry" in seen["prompt"]


def test_extractor_sees_file_metadata_and_processing_facts(tmp_path, monkeypatch):
    """file_metadata (#369), captured at chew time and threaded through preflight, must reach
    the extract call's prompt — along with the ocr_used/source_type processing facts the
    FILE_METADATA block's trust caveat depends on."""
    vault = make_vault(tmp_path)
    qdir = vault / ".watchdog" / "queue"
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / "abc123.json").write_text(json.dumps({
        "sha256": "abc123", "filename": "test-doc.pdf", "source_path": "_INCOMING/test-doc.pdf",
        "page_count": 1, "pages": [{"page": 1, "markdown": "Acme Corp filed an annual report."}],
        "near_dup": {"near_duplicates": [], "top_similarity": 0.0},
        "metadata": {"ocr_used": True, "source_type": "docling"},
        "file_metadata": {"author": "Jane Doe", "producer": "Acrobat Distiller"},
    }))
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")

    seen = {}
    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "extract":
            seen["prompt"] = _flat(prompt)
        parsed = {
            "classify": {"skill": "general-records.md"},
            "extract": _extraction(),
            "entity-synthesis": {"entity_syntheses": []},
            "briefing": {"investigation_status": "x", "what_was_ingested": []},
        }.get(task, {"events": []})
        return model_client.ModelResult(parsed=parsed, text="", model="m",
                                        backend="b", auth_mode="subscription", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    asyncio.run(orchestrate.run(vault))
    assert "FILE_METADATA" in seen["prompt"]
    assert "Jane Doe" in seen["prompt"] and "Acrobat Distiller" in seen["prompt"]
    assert "ocr_used=True" in seen["prompt"] and "source_type='docling'" in seen["prompt"]


def test_whole_doc_failure_falls_back_to_sectioning(tmp_path, monkeypatch):
    """A multi-page doc whose whole-doc extraction is rejected is re-extracted in sections."""
    vault = make_vault(tmp_path)
    monkeypatch.setattr(orchestrate.section, "_config_get", lambda k, d: d)   # deterministic defaults
    qdir = vault / ".watchdog" / "queue"
    qdir.mkdir(parents=True, exist_ok=True)
    pages = [{"page": 1, "markdown": "Acme part one " * 50},
             {"page": 2, "markdown": "Acme part two " * 50}]
    (qdir / "abc123.json").write_text(json.dumps({
        "sha256": "abc123", "filename": "test-doc.pdf", "source_path": "_INCOMING/test-doc.pdf",
        "page_count": 2, "pages": pages,
        "near_dup": {"near_duplicates": [], "top_similarity": 0.0},
    }))
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")

    calls = {"extract": 0, "section": 0}
    sec_first = {
        "document": {"sha256": "abc123", "filename": "test-doc.pdf", "title": "Acme AR",
                     "document_type": "Annual Report", "summary": "Acme report.",
                     "key_facts": [{"fact": "x", "basis": "stated"}]},
        "entities": [{"id": "acme-corp", "name": "Acme Corp", "type": "Company",
                      "timeline_events": [], "roles": []}],
        "morgue_entity_id": "acme-corp", "morgue_document_type": "annual-report",
        "observations": "sec1",
    }
    sec_later = {"entities": [{"id": "acme-corp", "name": "Acme Corp", "type": "Company",
                              "timeline_events": [], "roles": []}], "observations": "sec2"}

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "classify":
            parsed = {"skill": "general-records.md"}
        elif task == "extract":
            calls["extract"] += 1
            parsed = _extraction(valid=False)                 # whole-doc → postflight rejects
        elif task == "extract-section":
            calls["section"] += 1
            parsed = sec_first if "This is SECTION 1" in _flat(prompt) else sec_later
        elif task == "briefing":
            parsed = {"investigation_status": "x", "what_was_ingested": []}
        else:
            parsed = {"entity_syntheses": []} if task == "entity-synthesis" else {"events": []}
        return model_client.ModelResult(parsed=parsed, text="", model="m",
                                        backend="b", auth_mode="subscription", cost_usd=0.01)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    summary = asyncio.run(orchestrate.run(vault))
    assert calls["extract"] >= 1 and calls["section"] >= 2     # whole-doc tried, then sectioned
    assert summary["extracted"] == 1 and summary["failed"] == 0
    assert (vault / "entities" / "organization" / "acme-corp.md").exists()


def test_single_page_failure_does_not_section(tmp_path, monkeypatch):
    """A 1-page doc can't be split, so a rejection just fails (no fallback loop)."""
    vault = make_vault(tmp_path)
    _queue_doc(vault)                                          # single page
    _mock(monkeypatch, extraction=_extraction(valid=False))
    summary = asyncio.run(orchestrate.run(vault))
    assert summary["failed"] == 1 and summary["extracted"] == 0


def test_large_single_page_failure_falls_back_to_char_sectioning(tmp_path, monkeypatch):
    """A big single-page doc (e.g. a long text file) whose whole-doc extraction is rejected — for
    an openai/gemini backend this is where a truncated response lands (#343) — is re-extracted by
    splitting its text into character windows, not just given up on."""
    vault = make_vault(tmp_path)
    monkeypatch.setattr(orchestrate.section, "_config_get", lambda k, d: d)   # deterministic defaults
    # One page long enough that _FALLBACK_SECTION_TOKENS splits it into ≥2 character windows.
    long_text = "Acme Corp disclosures. " * 6000                             # ~138K chars
    _queue_doc(vault, text=long_text)

    calls = {"extract": 0, "section": 0}
    sec_first = {
        "document": {"sha256": "abc123", "filename": "test-doc.pdf", "title": "Acme AR",
                     "document_type": "Annual Report", "summary": "Acme report.",
                     "key_facts": [{"fact": "x", "basis": "stated"}]},
        "entities": [{"id": "acme-corp", "name": "Acme Corp", "type": "Company",
                      "timeline_events": [], "roles": []}],
        "morgue_entity_id": "acme-corp", "morgue_document_type": "annual-report",
        "observations": "sec1",
    }
    sec_later = {"entities": [{"id": "acme-corp", "name": "Acme Corp", "type": "Company",
                              "timeline_events": [], "roles": []}], "observations": "sec2"}

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "classify":
            parsed = {"skill": "general-records.md"}
        elif task == "extract":
            calls["extract"] += 1
            parsed = _extraction(valid=False)                 # whole-doc → postflight rejects
        elif task == "extract-section":
            calls["section"] += 1
            parsed = sec_first if "This is SECTION 1" in _flat(prompt) else sec_later
        elif task == "briefing":
            parsed = {"investigation_status": "x", "what_was_ingested": []}
        else:
            parsed = {"entity_syntheses": []} if task == "entity-synthesis" else {"events": []}
        return model_client.ModelResult(parsed=parsed, text="", model="m",
                                        backend="b", auth_mode="subscription", cost_usd=0.01)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    summary = asyncio.run(orchestrate.run(vault))
    assert calls["extract"] >= 1 and calls["section"] >= 2     # whole-doc tried, then sectioned
    assert summary["extracted"] == 1 and summary["failed"] == 0


def test_pinned_skill_skips_classification(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    tasks = []
    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        tasks.append(task)
        parsed = {
            "extract": _extraction(),
            "entity-synthesis": {"entity_syntheses": []},
            "briefing": {"investigation_status": "x", "what_was_ingested": []},
        }.get(task, {"events": []})
        return model_client.ModelResult(parsed=parsed, text="", model="m", backend="b",
                                        auth_mode="subscription", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    skill_file = tmp_path / "pinned.md"
    skill_file.write_text("PINNED SKILL BODY")
    summary = asyncio.run(orchestrate.run(vault, pinned_skill=str(skill_file)))
    assert summary["extracted"] == 1
    assert "classify" not in tasks          # classification skipped entirely


def test_pinned_skill_is_injected_into_extraction(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    skill_file = tmp_path / "corporate-filings.md"
    skill_file.write_text("CORPORATE FILINGS SKILL BODY")
    seen = {}
    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "extract":
            seen["prompt"] = prompt
        parsed = {
            "extract": _extraction(),
            "entity-synthesis": {"entity_syntheses": []},
            "briefing": {"investigation_status": "x", "what_was_ingested": []},
        }.get(task, {"events": []})
        return model_client.ModelResult(parsed=parsed, text="", model="m", backend="b",
                                        auth_mode="subscription", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    asyncio.run(orchestrate.run(vault, pinned_skill=str(skill_file)))
    assert "CORPORATE FILINGS SKILL BODY" in _flat(seen["prompt"])


def test_sidecar_skill_pins_per_document_without_global_flag(tmp_path, monkeypatch):
    """Two documents, two different sidecar skill pins, no --skill: classification is skipped
    for both, and each lands on its own pinned skill rather than a run-wide one (D120)."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="aaa", filename="a.pdf", sidecar="skill: bankruptcy\n")
    _queue_doc(vault, sha="bbb", filename="b.pdf", sidecar="skill: court-documents\n")
    tasks = []
    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        tasks.append(task)
        # _stamp_document overwrites sha256/filename from the queue entry regardless of what the
        # mocked extraction returns, so both docs can safely share one fixture body here.
        parsed = {
            "extract": _extraction(sha="aaa", filename="a.pdf"),
            "entity-synthesis": {"entity_syntheses": []},
            "timeline-dedup": {"groups": []},
            "briefing": {"investigation_status": "x", "what_was_ingested": []},
        }.get(task, {})
        return model_client.ModelResult(parsed=parsed, text="", model="m",
                                        backend="b", auth_mode="subscription", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    summary = asyncio.run(orchestrate.run(vault, concurrency=2))
    assert "classify" not in tasks
    skills = {r["filename"]: r["record_skill"] for r in summary["results"]}
    assert skills == {"a.pdf": "bankruptcy", "b.pdf": "court-documents"}


def test_sidecar_skill_overrides_run_wide_pinned_skill(tmp_path, monkeypatch):
    """A document's own sidecar pin is more specific than --skill/default_skill, so it wins."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, sidecar="skill: bankruptcy\n")
    _mock(monkeypatch, extraction=_extraction())

    skill_file = tmp_path / "pinned.md"
    skill_file.write_text("PINNED SKILL BODY")
    summary = asyncio.run(orchestrate.run(vault, pinned_skill=str(skill_file)))
    assert summary["results"][0]["record_skill"] == "bankruptcy"


def test_record_skill_provenance_is_persisted(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    _mock(monkeypatch, extraction=_extraction())     # classify mock returns general-records.md
    asyncio.run(orchestrate.run(vault))              # extract_model defaults to "sonnet"

    from watchdog import skills_catalog
    expected_hash = hashlib.sha256(
        skills_catalog.read_skill("general-records.md").encode("utf-8")).hexdigest()[:12]

    docs = json.loads((vault / ".watchdog" / "registry" / "documents.json").read_text())
    entry = next(iter(docs.values()))
    assert entry["record_skill"] == "general-records"
    assert entry["record_skill_hash"] == expected_hash
    assert entry["extract_model"] == "claude-sonnet-4-6"
    assert entry["extract_effort"] is None

    note = next((vault / "documents").glob("*.md")).read_text(encoding="utf-8")
    assert "record_skill: general-records" in note
    assert f"record_skill_hash: {expected_hash}" in note
    assert "extract_model: claude-sonnet-4-6" in note


def test_nudge_skill_pin_fires_when_batch_is_homogeneous(capsys):
    orchestrate._nudge_skill_pin([
        {"status": "ok", "record_skill": "general-records"},
        {"status": "ok", "record_skill": "general-records"},
    ])
    assert "watchdog ingest --skill general-records" in capsys.readouterr().out


def test_nudge_skill_pin_silent_when_mixed_or_single_or_failed(capsys):
    orchestrate._nudge_skill_pin([
        {"status": "ok", "record_skill": "general-records"},
        {"status": "ok", "record_skill": "court-documents"},
    ])
    assert capsys.readouterr().out == ""                       # mixed skills

    orchestrate._nudge_skill_pin([{"status": "ok", "record_skill": "general-records"}])
    assert capsys.readouterr().out == ""                       # only one document

    orchestrate._nudge_skill_pin([
        {"status": "ok", "record_skill": "general-records"},
        {"status": "failed", "record_skill": None},
    ])
    assert capsys.readouterr().out == ""                       # only one succeeded


def test_skill_pin_nudge_silent_when_run_was_pinned(tmp_path, monkeypatch, capsys):
    """The nudge only makes sense when classification ran at all."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="aaa", filename="a.pdf")
    _queue_doc(vault, sha="bbb", filename="b.pdf")
    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        # _stamp_document overwrites sha256/filename from the queue entry regardless of what the
        # mocked extraction returns, so both docs can safely share one fixture body here.
        parsed = {
            "extract": _extraction(sha="aaa", filename="a.pdf"),
            "entity-synthesis": {"entity_syntheses": []},
            "timeline-dedup": {"groups": []},
            "briefing": {"investigation_status": "x", "what_was_ingested": []},
        }.get(task, {})
        return model_client.ModelResult(parsed=parsed, text="", model="m",
                                        backend="b", auth_mode="subscription", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)
    skill_file = tmp_path / "pinned.md"
    skill_file.write_text("PINNED SKILL BODY")

    asyncio.run(orchestrate.run(vault, concurrency=2, pinned_skill=str(skill_file)))
    assert "watchdog ingest --skill" not in capsys.readouterr().out


def test_usage_telemetry_persisted_after_ingest(tmp_path, monkeypatch):
    """A2: every model call's usage is accumulated and written to a per-run usage file, with
    totals surfaced on the run summary — `ModelResult.usage` was previously discarded."""
    vault = make_vault(tmp_path)
    _queue_doc(vault)

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        parsed = {
            "classify": {"skill": "general-records.md"},
            "extract": _extraction(),
            "briefing": {"investigation_status": "x", "what_was_ingested": []},
        }.get(task, {"entity_syntheses": []} if task == "entity-synthesis" else {"groups": []})
        return model_client.ModelResult(
            parsed=parsed, text="", model="claude-sonnet-4-6", backend="claude-api",
            auth_mode="api-key", cost_usd=0.01, usage={"input_tokens": 100, "output_tokens": 20},
            latency_s=2.5)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    summary = asyncio.run(orchestrate.run(vault))
    assert summary["extracted"] == 1

    usage_path = summary["usage_path"]
    assert usage_path and (vault / usage_path).exists()
    assert usage_path.startswith(".watchdog/registry/usage/usage-")   # #319: moved out of the flat Registry dir
    data = json.loads((vault / usage_path).read_text())
    tasks = [c["task"] for c in data["calls"]]
    assert "classify" in tasks and "extract" in tasks and "briefing" in tasks
    assert all(c["input_tokens"] == 100 for c in data["calls"])

    # #317: every call's wall-clock duration is recorded alongside its token/cost usage.
    n_calls = len(data["calls"])
    assert all(c["latency_s"] == 2.5 for c in data["calls"])
    assert round(data["totals"]["latency_s"], 3) == round(2.5 * n_calls, 3)
    assert round(summary["usage"]["latency_s"], 3) == round(2.5 * n_calls, 3)

    # #247: extraction/classification calls carry the document filename (and, for extraction,
    # a page-range detail) so a usage file can attribute cost to a specific document.
    by_task = {c["task"]: c for c in data["calls"]}
    assert by_task["classify"]["filename"] == "test-doc.pdf"
    assert by_task["extract"]["filename"] == "test-doc.pdf"
    assert by_task["extract"]["detail"] == "pages 1–1"
    assert by_task["briefing"]["filename"] is None   # corpus-wide call, nothing to attribute

    assert data["totals"]["input_tokens"] == 100 * n_calls
    assert data["totals"]["output_tokens"] == 20 * n_calls
    assert summary["usage"]["input_tokens"] == 100 * n_calls
    assert round(summary["usage"]["cost_usd"], 4) == round(0.01 * n_calls, 4)


def test_record_usage_carries_agent_sdk_harness_timing():
    """#402: a `claude-agent-sdk` usage dict carrying `duration_api_ms`/`num_turns` (harness
    timing) surfaces as `api_ms`/`num_turns` on the persisted call record — the signal that
    tells a throttled call (long gap between wall-clock latency and API time) apart from a
    genuinely slow one."""
    orchestrate._usage = []
    try:
        orchestrate._record_usage(
            "extract", model="claude-sonnet-4-6", backend="claude-agent-sdk",
            usage={"input_tokens": 100, "output_tokens": 20,
                  "duration_api_ms": 12345, "num_turns": 3},
            cost_usd=0.01, latency_s=60.0)
        assert len(orchestrate._usage) == 1
        record = orchestrate._usage[0]
        assert record["api_ms"] == 12345
        assert record["num_turns"] == 3
    finally:
        orchestrate._usage = None


def test_record_usage_omits_harness_timing_keys_for_other_backends():
    """A raw-API backend's usage dict has no `duration_api_ms`/`num_turns` — the persisted
    record must not grow `api_ms`/`num_turns` keys (even as null) for it, so existing
    `usage-<ts>.json` consumers see byte-identical records to before #402."""
    orchestrate._usage = []
    try:
        orchestrate._record_usage(
            "extract", model="claude-sonnet-4-6", backend="claude-api",
            usage={"input_tokens": 100, "output_tokens": 20},
            cost_usd=0.01, latency_s=1.0)
        assert len(orchestrate._usage) == 1
        record = orchestrate._usage[0]
        assert "api_ms" not in record
        assert "num_turns" not in record
    finally:
        orchestrate._usage = None


def test_record_usage_includes_pruned_keys_when_present():
    """#412/D124: pruned key paths ride along on the usage record so schema drift stays
    visible in `watchdog usage`, not just ingest.log."""
    orchestrate._usage = []
    try:
        orchestrate._record_usage(
            "extract", model="claude-sonnet-4-6", backend="claude-api",
            usage={"input_tokens": 100, "output_tokens": 20}, cost_usd=0.01,
            pruned=["extra_field"])
        assert orchestrate._usage[0]["pruned"] == ["extra_field"]
    finally:
        orchestrate._usage = None


def test_record_usage_omits_pruned_key_when_absent():
    orchestrate._usage = []
    try:
        orchestrate._record_usage(
            "extract", model="claude-sonnet-4-6", backend="claude-api",
            usage={"input_tokens": 100, "output_tokens": 20}, cost_usd=0.01)
        assert "pruned" not in orchestrate._usage[0]
    finally:
        orchestrate._usage = None


def test_log_md_ingest_entry_includes_usage_line(tmp_path, monkeypatch):
    """F5/#222: the log.md entry for an ingest carries the run's token/cost totals, the
    user-facing half of A2's telemetry."""
    vault = make_vault(tmp_path)
    _queue_doc(vault)

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        parsed = {
            "classify": {"skill": "general-records.md"},
            "extract": _extraction(),
            "briefing": {"investigation_status": "x", "what_was_ingested": []},
        }.get(task, {"entity_syntheses": []} if task == "entity-synthesis" else {"groups": []})
        return model_client.ModelResult(
            parsed=parsed, text="", model="claude-sonnet-4-6", backend="claude-api",
            auth_mode="api-key", cost_usd=0.01, usage={"input_tokens": 100, "output_tokens": 20})
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    asyncio.run(orchestrate.run(vault))
    log = (vault / "log.md").read_text()
    assert "**Usage:**" in log
    assert "in /" in log and "out tokens" in log
    assert "$0.0" in log   # non-zero cost rendered


def test_latest_usage_none_before_any_ingest(tmp_path):
    vault = make_vault(tmp_path)
    assert orchestrate.latest_usage(vault) is None


def test_latest_usage_returns_the_most_recent_run(tmp_path):
    vault = make_vault(tmp_path)
    reg = vault / ".watchdog" / "registry"
    (reg / "usage-20260101T000000Z.json").write_text(
        json.dumps({"calls": [], "totals": {"input_tokens": 1, "output_tokens": 1,
                                            "cache_read_tokens": 0, "cache_write_tokens": 0,
                                            "cost_usd": 0.001}}))
    (reg / "usage-20260102T000000Z.json").write_text(
        json.dumps({"calls": [], "totals": {"input_tokens": 999, "output_tokens": 999,
                                            "cache_read_tokens": 0, "cache_write_tokens": 0,
                                            "cost_usd": 0.05}}))
    totals = orchestrate.latest_usage(vault)
    assert totals["input_tokens"] == 999


def test_usage_files_merges_new_subfolder_with_legacy_flat_location(tmp_path):
    """#319: usage-<ts>.json moved from the flat Registry dir into a `usage/` subfolder, but a
    vault ingested before that move still has real history sitting in the old flat location —
    `usage_files` (and everything built on it) must keep seeing both, in chronological order."""
    vault = make_vault(tmp_path)
    reg = vault / ".watchdog" / "registry"
    (reg / "usage-20260101T000000Z.json").write_text("{}")   # legacy (pre-move) location
    usage_dir = reg / "usage"
    usage_dir.mkdir(parents=True)
    (usage_dir / "usage-20260102T000000Z.json").write_text("{}")   # current (post-move) location

    files = orchestrate.usage_files(vault)
    assert [f.name for f in files] == ["usage-20260101T000000Z.json", "usage-20260102T000000Z.json"]


def test_latest_usage_prefers_new_subfolder_over_legacy_when_newer(tmp_path):
    vault = make_vault(tmp_path)
    reg = vault / ".watchdog" / "registry"
    (reg / "usage-20260101T000000Z.json").write_text(
        json.dumps({"calls": [], "totals": {"input_tokens": 1, "output_tokens": 1,
                                            "cache_read_tokens": 0, "cache_write_tokens": 0,
                                            "cost_usd": 0.001}}))
    usage_dir = reg / "usage"
    usage_dir.mkdir(parents=True)
    (usage_dir / "usage-20260102T000000Z.json").write_text(
        json.dumps({"calls": [], "totals": {"input_tokens": 999, "output_tokens": 999,
                                            "cache_read_tokens": 0, "cache_write_tokens": 0,
                                            "cost_usd": 0.05}}))
    totals = orchestrate.latest_usage(vault)
    assert totals["input_tokens"] == 999


def test_orchestrator_cancels_gracefully_on_sigint(tmp_path, monkeypatch):
    """Ctrl+C during extraction → cancelled summary, no traceback, unfinished docs keep
    their queue file, and post-ingest is skipped."""
    import os
    import signal

    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="aaa111", filename="one.pdf")
    _queue_doc(vault, sha="bbb222", filename="two.pdf")

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "extract":
            # Simulate the user pressing Ctrl+C mid-extraction. The loop's SIGINT
            # handler cancels the in-flight tasks; the sleep below is interrupted.
            os.kill(os.getpid(), signal.SIGINT)
            await asyncio.sleep(5)
        parsed = {"classify": {"skill": "general-records.md"}}.get(task, _extraction())
        return model_client.ModelResult(parsed=parsed, text="", model="m",
                                        backend="claude-agent-sdk", auth_mode="subscription", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    summary = asyncio.run(orchestrate.run(vault, concurrency=2))

    assert summary["cancelled"] is True
    assert summary["extracted"] == 0
    assert "post_ingest" not in summary                      # post-ingest skipped on cancel
    # both queue files survive for a clean resume
    assert (vault / ".watchdog" / "queue" / "aaa111.json").exists()
    assert (vault / ".watchdog" / "queue" / "bbb222.json").exists()


def test_orchestrator_survives_unavailable_signal_handler(tmp_path, monkeypatch):
    """On platforms where asyncio can't install a SIGINT handler at all — e.g. Windows'
    Proactor event loop, whose add_signal_handler always raises NotImplementedError — the
    batch must still run to completion instead of crashing. The graceful finish-current-writes
    path (the other sigint test above) simply isn't available there; a bare Ctrl+C falls
    through to cmd_ingest's plain `except KeyboardInterrupt` instead (issue #258)."""
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    _mock(monkeypatch, extraction=_extraction())

    def _unsupported(self, *a, **kw):
        raise NotImplementedError("add_signal_handler is not supported on this platform")
    monkeypatch.setattr(asyncio.unix_events._UnixSelectorEventLoop, "add_signal_handler", _unsupported)

    summary = asyncio.run(orchestrate.run(vault))

    assert summary["cancelled"] is False
    assert summary["extracted"] == 1 and summary["failed"] == 0


def test_rate_limit_stops_batch_keeps_queue(tmp_path, monkeypatch):
    """A provider rate limit stops the batch cleanly: the summary carries the reason,
    nothing is quarantined, and every queue file is kept for resume."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="aaa111", filename="one.pdf")
    _queue_doc(vault, sha="bbb222", filename="two.pdf")

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "classify":
            return model_client.ModelResult(parsed={"skill": "general-records.md"}, text="",
                                            model="m", backend="claude-agent-sdk", auth_mode="subscription")
        raise model_client.RateLimitError("You've hit your session limit · resets 6:10pm")
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    summary = asyncio.run(orchestrate.run(vault, concurrency=2))

    assert summary["rate_limited"] is True
    assert "session limit" in summary["stop_message"]
    assert summary["extracted"] == 0
    assert summary["quarantined"] == 0
    assert "post_ingest" not in summary                      # skipped when the batch stops
    # neither doc is quarantined; both stay queued for a clean resume
    assert {p.name for p in (vault / ".watchdog" / "queue").glob("*.json")} == {"aaa111.json", "bbb222.json"}


def test_rate_limit_resets_at_reaches_summary(tmp_path, monkeypatch):
    """`RateLimitError.resets_at` (only populated on the claude-agent-sdk backend) must reach
    the summary so `watchdog ingest --wait` (#271) knows when to resume."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="aaa111", filename="one.pdf")

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "classify":
            return model_client.ModelResult(parsed={"skill": "general-records.md"}, text="",
                                            model="m", backend="claude-agent-sdk", auth_mode="subscription")
        raise model_client.RateLimitError("session limit", resets_at=1_700_000_000)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    summary = asyncio.run(orchestrate.run(vault, concurrency=1))

    assert summary["rate_limit_resets_at"] == 1_700_000_000


def test_rate_limit_resets_at_is_none_when_backend_omits_it(tmp_path, monkeypatch):
    """The claude-api / OpenAI-compatible backends raise RateLimitError with no resets_at —
    the summary must carry None rather than error, so --wait falls back to its fixed interval."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="aaa111", filename="one.pdf")

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "classify":
            return model_client.ModelResult(parsed={"skill": "general-records.md"}, text="",
                                            model="m", backend="claude-agent-sdk", auth_mode="subscription")
        raise model_client.RateLimitError("session limit")
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    summary = asyncio.run(orchestrate.run(vault, concurrency=1))

    assert summary["rate_limit_resets_at"] is None


def test_rate_limit_message_reflects_wait_flag(tmp_path, monkeypatch, capsys):
    """The in-run notice text differs between plain and --wait mode: the former tells the user
    to re-run ingest manually, the latter says it'll resume on its own (#271)."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="aaa111", filename="one.pdf")

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "classify":
            return model_client.ModelResult(parsed={"skill": "general-records.md"}, text="",
                                            model="m", backend="claude-agent-sdk", auth_mode="subscription")
        raise model_client.RateLimitError("session limit")
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    asyncio.run(orchestrate.run(vault, concurrency=1, wait=True))
    out = capsys.readouterr().out
    assert "Waiting to resume automatically" in out
    assert "Re-run" not in out


def test_failed_doc_is_named_and_quarantine_surfaced(tmp_path, monkeypatch):
    """A genuine (non-rate-limit) error names the file rather than a bare sha, quarantines
    it to _failed/, and the summary reports the quarantined count."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="ccc333", filename="boom.pdf")

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "classify":
            return model_client.ModelResult(parsed={"skill": "general-records.md"}, text="",
                                            model="m", backend="claude-agent-sdk", auth_mode="subscription")
        raise RuntimeError("kaboom")
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    summary = asyncio.run(orchestrate.run(vault, concurrency=1))

    assert summary["failed"] == 1
    assert summary["quarantined"] == 1
    failed = next(r for r in summary["results"] if r["status"] == "failed")
    assert failed["filename"] == "boom.pdf"                   # resolved, not a bare sha
    assert (vault / ".watchdog" / "queue" / "_failed" / "ccc333.json").exists()
    assert not (vault / ".watchdog" / "queue" / "ccc333.json").exists()


def test_post_ingest_model_failure_degrades_without_crashing(tmp_path, monkeypatch):
    """A rate limit (or model error) during post-ingest must not crash: the extracted docs
    are already saved, synthesis is skipped, and the run returns a summary cleanly."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="aaa", filename="a.pdf", text="Acme Corp filed.")
    _queue_doc(vault, sha="bbb", filename="b.pdf", text="Acme Corp again.")

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "classify":
            return model_client.ModelResult(parsed={"skill": "general-records.md"}, text="",
                                            model="m", backend="claude-agent-sdk", auth_mode="subscription")
        if task == "extract":
            sha = "aaa" if "Acme Corp filed." in prompt else "bbb"   # share an entity across both docs
            return model_client.ModelResult(parsed=_extraction(sha=sha, filename=f"{sha}.pdf"),
                                            text="", model="m", backend="claude-agent-sdk", auth_mode="subscription")
        raise model_client.RateLimitError("You've hit your session limit · resets 7pm")
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    summary = asyncio.run(orchestrate.run(vault, concurrency=2))   # must not raise

    assert summary["extracted"] == 2
    assert summary["post_ingest"]["synthesized"] == 0
    assert "error" in summary["post_ingest"]                       # synthesis degraded, recorded


def test_post_ingest_unexpected_crash_is_contained(tmp_path, monkeypatch):
    """An unforeseen error in post-ingest is caught at the batch level — the saved
    extraction is reported and the CLI does not crash."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="aaa", filename="a.pdf")
    _mock(monkeypatch, extraction=_extraction())

    def boom(*a, **k):
        raise RuntimeError("kaboom in post-ingest")
    monkeypatch.setattr(orchestrate.synthesis_bundle, "build_bundle", boom)

    summary = asyncio.run(orchestrate.run(vault, concurrency=1))   # must not raise

    assert summary["extracted"] == 1
    assert "post_ingest_error" in summary


# ── _post_ingest timeline-collision loop (#250) ──────────────────────────────

def _seed_collision(vault, date="2020-03-15"):
    """Pre-seed the timeline dir with a canonical {date}.ndjson and one raw {date}_<sha7>.ndjson
    for the same date, so `timeline.collisions()` reports one real collision for `_post_ingest`
    to resolve. The two events share (date, event) so a dedup can fold them into one. Returns
    (canonical_path, raw_path)."""
    td = vault / ".watchdog" / "timeline"
    td.mkdir(parents=True, exist_ok=True)
    canonical = td / f"{date}.ndjson"
    canonical.write_text(json.dumps({
        "date": date, "event": "Appointed director", "source_sha256": "oldoldold0000",
        "page": 1, "entity_ids": ["alice"], "basis": "stated"}) + "\n", encoding="utf-8")
    raw = td / f"{date}_newdoc1.ndjson"
    raw.write_text(json.dumps({
        "date": date, "event": "Appointed director", "source_sha256": "newnewnew1111",
        "page": 4, "entity_ids": ["bob"], "basis": "stated"}) + "\n", encoding="utf-8")
    return canonical, raw


def _mock_post_ingest(monkeypatch, *, timeline_dedup):
    """Drive `_post_ingest` with only the timeline-dedup call under test controlled.
    `timeline_dedup` is a zero-arg callable invoked for the timeline-dedup task — it may return a
    parsed dict (success) or raise (failure). Briefing is deliberately failed (its error is caught)
    so the run completes without needing a full briefing fixture."""
    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "timeline-dedup":
            parsed = timeline_dedup()   # may raise
        elif task == "briefing":
            raise model_client.RateLimitError("briefing skipped for this test")
        else:
            parsed = {}
        return model_client.ModelResult(parsed=parsed, text="", model="m",
                                        backend="claude-agent-sdk", auth_mode="subscription", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)


def test_post_ingest_fails_loudly_on_briefing_model_error_without_retrying(tmp_path, monkeypatch):
    """A briefing ModelError (e.g. an output-cap truncation on a large batch) is recorded as a
    briefing_error and NOT retried — an identical re-run would fail the same deterministic way
    (#296). The remedy is a smaller batch, surfaced to the user; the briefing is simply absent."""
    vault = make_vault(tmp_path)
    results = [orchestrate._compact_result(
        "sha1", "doc.pdf",
        {"document": {"key_facts": [{"fact": f"fact {i}"} for i in range(20)]}, "entities": []},
        {}, 0.01, {})]

    briefing_calls = []

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "briefing":
            briefing_calls.append(prompt)
            raise model_client.ModelError("response was not valid JSON")
        elif task == "timeline-dedup":
            parsed = {"groups": []}
        else:
            parsed = {}
        return model_client.ModelResult(parsed=parsed, text="", model="m",
                                        backend="claude-agent-sdk", auth_mode="subscription", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    out = asyncio.run(orchestrate._post_ingest(vault, results, None, "haiku"))

    assert len(briefing_calls) == 1                   # called once, no doomed retry
    assert out.get("briefing") is None                # no briefing written
    assert out.get("briefing_error")                  # failure recorded so the caller can surface it


def test_post_ingest_leaves_collision_untouched_when_dedup_fails(tmp_path, monkeypatch):
    """A rate limit during timeline dedup must leave BOTH the canonical and its raw untouched, so
    the next ingest retries the collision cleanly. The pre-#250 bug wrote the canonical+raw union
    back and deleted the raw, baking in a duplicate row that compounded on every later run."""
    vault = make_vault(tmp_path)
    canonical, raw = _seed_collision(vault)
    canonical_before = canonical.read_text(encoding="utf-8")

    def boom():
        raise model_client.RateLimitError("You've hit your session limit")
    _mock_post_ingest(monkeypatch, timeline_dedup=boom)

    out = asyncio.run(orchestrate._post_ingest(vault, [], None, "haiku"))   # must not raise

    assert out["timeline_collisions"] == 1
    assert canonical.read_text(encoding="utf-8") == canonical_before   # not rewritten as a union
    assert raw.exists()                                                # raw retained for retry
    assert len(timeline.collisions(vault)) == 1                        # still a live collision


def test_post_ingest_consumes_raws_after_successful_dedup(tmp_path, monkeypatch):
    """A successful dedup writes the merged canonical and deletes the consumed raw, so a second
    run finds no collision and makes zero timeline-dedup calls (#250). The two seeded events share
    (date, event), so the model folds them into one surviving row with unioned attribution."""
    vault = make_vault(tmp_path)
    canonical, raw = _seed_collision(vault)

    calls = {"timeline-dedup": 0}
    def dedup():
        calls["timeline-dedup"] += 1
        return {"groups": [{"keep": 0, "duplicates": [1]}]}   # fold the raw restatement into the canonical
    _mock_post_ingest(monkeypatch, timeline_dedup=dedup)

    asyncio.run(orchestrate._post_ingest(vault, [], None, "haiku"))

    assert calls["timeline-dedup"] == 1
    assert not raw.exists()                                   # raw consumed
    recs = [json.loads(line) for line in canonical.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(recs) == 1                                     # folded to a single row
    assert recs[0]["entity_ids"] == ["alice", "bob"]          # attribution unioned across the merge
    assert timeline.collisions(vault) == []                   # no re-collision → future runs are silent


def test_finalize_completes_an_interrupted_run(tmp_path, monkeypatch):
    """A rate limit during post-ingest leaves the batch finalizable; a later finalize
    completes synthesis + briefing and clears the per-run inputs."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="aaa", filename="a.pdf", text="Acme Corp filed.")
    _queue_doc(vault, sha="bbb", filename="b.pdf", text="Acme Corp again.")
    state = {"synthesis_ok": False}

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        def res(parsed):
            return model_client.ModelResult(parsed=parsed, text="", model="m",
                                            backend="claude-agent-sdk", auth_mode="subscription")
        if task == "classify":
            return res({"skill": "general-records.md"})
        if task == "extract":
            sha = "aaa" if "Acme Corp filed." in prompt else "bbb"   # share one entity across both docs
            return res(_extraction(sha=sha, filename=f"{sha}.pdf"))
        if task == "entity-synthesis":
            if not state["synthesis_ok"]:
                raise model_client.RateLimitError("You've hit your session limit · resets 7pm")
            return res({"entity_syntheses": [{"entity_id": "acme-corp", "summary": "Synthesized prose.", "analysis": ""}]})
        if task == "timeline-dedup":
            return res({"groups": []})
        return res({"investigation_status": "x", "what_was_ingested": ["a.pdf", "b.pdf"]})
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    # Phase 1: ingest extracts both docs but the rate limit interrupts synthesis.
    summary = asyncio.run(orchestrate.run(vault, concurrency=2))
    assert summary["extracted"] == 2
    assert "error" in summary["post_ingest"]                 # synthesis degraded
    assert orchestrate.has_pending_finalization(vault) is True

    # Phase 2: the limit has reset — watchdog finalize completes the batch.
    state["synthesis_ok"] = True
    out = asyncio.run(orchestrate.finalize(vault, post_model="haiku"))

    assert out["synthesized"] == 1
    assert "error" not in out
    assert "Synthesized prose." in (vault / "entities" / "organization" / "acme-corp.md").read_text()
    # a clean finalize clears the per-run inputs, so there is nothing left pending
    assert not (vault / ".watchdog" / "tmp" / "entity-fragments").exists()
    assert not list((vault / ".watchdog" / "tmp").glob("result_*.json"))
    assert orchestrate.has_pending_finalization(vault) is False


def test_skip_finalize_stops_after_extraction_with_no_post_ingest_calls(tmp_path, monkeypatch):
    """`--no-finalize` (#384): `orchestrate.run(..., skip_finalize=True)` extracts the queue but
    never calls post-ingest — no reconciliation/synthesis/timeline-dedup/briefing model call —
    and leaves the batch pending finalization, with the per-doc result and fragment inputs on
    disk for a later `watchdog finalize`."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="aaa", filename="a.pdf")

    calls = []

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        calls.append(task)
        if task == "classify":
            parsed = {"skill": "general-records.md"}
        elif task == "extract":
            parsed = _extraction(sha="aaa", filename="a.pdf")
        else:
            raise AssertionError(f"unexpected post-ingest call with skip_finalize=True: {task}")
        return model_client.ModelResult(parsed=parsed, text="", model="m",
                                        backend="claude-agent-sdk", auth_mode="subscription")
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    summary = asyncio.run(orchestrate.run(vault, skip_finalize=True))

    assert summary["extracted"] == 1
    assert summary["finalize_skipped"] is True
    assert "post_ingest" not in summary
    assert calls == ["classify", "extract"]     # no entity-synthesis/timeline-dedup/briefing/reconcile
    assert orchestrate.has_pending_finalization(vault) is True
    assert (vault / ".watchdog" / "tmp" / "result_aaa.json").exists()
    assert (vault / ".watchdog" / "tmp" / "entity-fragments" / "_queue.json").exists()


def test_finalize_after_skip_finalize_consumes_staged_inputs(tmp_path, monkeypatch):
    """The inputs an extract-only run (`skip_finalize=True`) leaves on disk are exactly what a
    later, standalone `orchestrate.finalize()` needs — it completes synthesis + briefing from
    them and clears them once done, the same as finishing an interrupted run (#384)."""
    def _ext(sha, filename, fact):
        return {
            "document": {"sha256": sha, "filename": filename,
                         "original_path": f"_INCOMING/{filename}",
                         "title": filename, "document_type": "Filing",
                         "date_of_document": "2024-01-15", "page_count": 1,
                         "source": None, "obtained": None, "near_duplicate_of": None,
                         "summary": "A filing.",
                         "key_facts": [{"fact": fact, "page": 1, "basis": "stated",
                                        "entities": ["acme-corp"]}]},
            "entities": [{"id": "acme-corp", "name": "Acme Corp", "type": "Company",
                          "aliases": [], "roles": []}],
            "morgue_entity_id": "acme-corp", "morgue_document_type": "filing",
            "scratchpad": "",
        }

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        flat = _flat(prompt)
        if task == "classify":
            parsed = {"skill": "general-records.md"}
        elif task == "extract":
            parsed = _ext("sha-one", "doc-one.pdf", "Acme filed ONE") if "ONE" in flat \
                else _ext("sha-two", "doc-two.pdf", "Acme filed TWO")
        elif task == "entity-synthesis":
            parsed = {"entity_syntheses": [{"entity_id": "acme-corp",
                                            "summary": "Synthesized prose.", "analysis": ""}]}
        elif task == "timeline-dedup":
            parsed = {"groups": []}
        elif task == "briefing":
            parsed = {"investigation_status": "x", "what_was_ingested": ["doc-one.pdf", "doc-two.pdf"]}
        else:
            parsed = {}
        return model_client.ModelResult(parsed=parsed, text="", model="m",
                                        backend="claude-agent-sdk", auth_mode="subscription")
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="sha-one", filename="doc-one.pdf", text="Acme filed ONE")
    _queue_doc(vault, sha="sha-two", filename="doc-two.pdf", text="Acme filed TWO")

    # Phase 1: extract-only.
    summary = asyncio.run(orchestrate.run(vault, skip_finalize=True))
    assert summary["extracted"] == 2
    assert "post_ingest" not in summary
    assert orchestrate.has_pending_finalization(vault) is True

    # Phase 2: a standalone finalize (e.g. `watchdog finalize --finalizer-model ...`) consumes
    # exactly the staged inputs — no re-extraction, no "extract" call this phase.
    calls = []
    orig_fake = fake

    async def counting_fake(*, task, **kw):
        calls.append(task)
        return await orig_fake(task=task, **kw)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", counting_fake)

    out = asyncio.run(orchestrate.finalize(vault, post_model="haiku"))

    assert "extract" not in calls
    assert out["synthesized"] == 1
    assert "error" not in out
    assert "Synthesized prose." in (vault / "entities" / "organization" / "acme-corp.md").read_text()
    assert not (vault / ".watchdog" / "tmp" / "entity-fragments").exists()
    assert not list((vault / ".watchdog" / "tmp").glob("result_*.json"))
    assert orchestrate.has_pending_finalization(vault) is False


def test_pending_finalization_uses_registry_appears_in_gate(tmp_path):
    """Entity count reflects the registry's `appears_in >= 2` gate (D26), not the fragment
    queue's `count`, which is only a touched-set marker post-D26."""
    vault = make_vault(tmp_path)
    tmp = vault / ".watchdog" / "tmp"
    frag = tmp / "entity-fragments"
    frag.mkdir(parents=True, exist_ok=True)
    (frag / "_queue.json").write_text(json.dumps({
        "acme-corp": {"count": 1},   # touched once this run...
        "beta-llc": {"count": 1},
    }))
    (vault / ".watchdog" / "registry" / "entities.json").write_text(json.dumps({
        "acme-corp": {"appears_in": ["doc1", "doc2"]},   # ...but recurs project-wide → eligible
        "beta-llc": {"appears_in": ["doc1"]},            # single-document → not eligible
    }))
    (tmp / "result_a.json").write_text("{}")

    result = orchestrate.pending_finalization(vault)
    assert result["docs"] == 1
    assert result["entities"] == 1   # only acme-corp crosses appears_in >= 2


def test_ingest_setup_wipe_pending_controls_cleanup(tmp_path):
    """wipe_pending=False (the merge choice) keeps a prior batch's post-ingest inputs;
    the default clears them."""
    from watchdog.pipeline import ingest_setup
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="new1", filename="new.pdf")          # a queued doc → total > 0
    tmp = vault / ".watchdog" / "tmp"
    frag = tmp / "entity-fragments"
    frag.mkdir(parents=True, exist_ok=True)
    (frag / "_queue.json").write_text('{"acme-corp": {"count": 2}}')
    (tmp / "result_old.json").write_text("{}")
    (tmp / "notes_old.md").write_text("obs")
    lock = vault / ".watchdog" / "registry" / ".ingest-lock"

    # merge: inputs preserved so this run finalizes together with the pending batch
    ingest_setup.run(vault, wipe_pending=False)
    assert (frag / "_queue.json").exists()
    assert (tmp / "result_old.json").exists() and (tmp / "notes_old.md").exists()

    # default: inputs wiped for a fresh batch
    lock.unlink(missing_ok=True)                               # release the lock from the prior call
    ingest_setup.run(vault, wipe_pending=True)
    assert not (frag / "_queue.json").exists()
    assert not (tmp / "result_old.json").exists() and not (tmp / "notes_old.md").exists()


def test_ingest_setup_discard_snapshots_before_wiping(tmp_path):
    """#270: the discard choice (wipe_pending=True with leftover residue from a prior
    unfinalized batch) is irreversible — back up entity-fragments/, result_*.json, and
    notes_*.md before deleting them."""
    from watchdog.pipeline import ingest_setup
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="new1", filename="new.pdf")
    tmp = vault / ".watchdog" / "tmp"
    frag = tmp / "entity-fragments"
    frag.mkdir(parents=True, exist_ok=True)
    (frag / "_queue.json").write_text('{"acme-corp": {"count": 2}}')
    (tmp / "result_old.json").write_text('{"old": true}')
    (tmp / "notes_old.md").write_text("scratchpad notes")

    state = ingest_setup.run(vault, wipe_pending=True)

    assert state["backup_dir"] is not None
    backup_dir = Path(state["backup_dir"])
    assert (backup_dir / ".watchdog" / "tmp" / "entity-fragments" / "_queue.json").read_text() \
        == '{"acme-corp": {"count": 2}}'
    assert (backup_dir / ".watchdog" / "tmp" / "result_old.json").read_text() == '{"old": true}'
    assert (backup_dir / ".watchdog" / "tmp" / "notes_old.md").read_text() == "scratchpad notes"
    # And the originals are still gone — the backup doesn't block the wipe.
    assert not (frag / "_queue.json").exists()


def test_ingest_setup_ordinary_run_leaves_no_backup(tmp_path):
    """A routine ingest with nothing left over from a prior unfinalized batch is a
    no-op for the wipe step, so it must not leave an empty backup directory behind."""
    from watchdog.pipeline import ingest_setup
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="new1", filename="new.pdf")

    state = ingest_setup.run(vault, wipe_pending=True)

    assert state["backup_dir"] is None
    assert not (vault / ".watchdog" / "backups").exists()


def test_requeue_moves_failed_back(tmp_path, monkeypatch):
    """watchdog requeue moves quarantined queue files back into the active queue."""
    from watchdog.cmd.ingest import cmd_requeue
    vault = make_vault(tmp_path)
    failed = vault / ".watchdog" / "queue" / "_failed"
    failed.mkdir(parents=True, exist_ok=True)
    (failed / "ddd444.json").write_text("{}")
    monkeypatch.chdir(vault)

    cmd_requeue(None)

    assert (vault / ".watchdog" / "queue" / "ddd444.json").exists()
    assert not (failed / "ddd444.json").exists()


def test_orchestrator_sectioned_path(tmp_path, monkeypatch):
    """Large doc → section.run plans sections → per-section extract → merge → vault."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, text="a very long document ...")
    tmpd = vault / ".watchdog" / "tmp"
    tmpd.mkdir(parents=True, exist_ok=True)
    (tmpd / "section_abc123_01.md").write_text("<!-- PAGE 1 -->\n\nAcme part 1")
    (tmpd / "section_abc123_02.md").write_text("<!-- PAGE 2 -->\n\nAcme part 2")

    monkeypatch.setattr(orchestrate.section, "run", lambda v, s, **kw: {
        "sectioned": True, "page_count": 2, "sections": [
            {"index": 1, "label": "pages 1–1", "paginated": True, "pages_path": ".watchdog/tmp/section_abc123_01.md"},
            {"index": 2, "label": "pages 2–2", "paginated": True, "pages_path": ".watchdog/tmp/section_abc123_02.md"},
        ]})

    sec1 = {
        "document": {"sha256": "abc123", "filename": "test-doc.pdf", "title": "Acme AR",
                     "document_type": "Annual Report", "summary": "Acme report.",
                     "key_facts": [{"fact": "x", "basis": "stated"}]},
        "entities": [{"id": "acme-corp", "name": "Acme Corp", "type": "Company",
                      "timeline_events": [], "roles": []}],
        "morgue_entity_id": "acme-corp", "morgue_document_type": "annual-report",
        "observations": "section 1 obs",
    }
    sec2 = {"entities": [{"id": "acme-corp", "name": "Acme Corporation", "type": "Company",
                          "timeline_events": [], "roles": []}],
            "observations": "section 2 obs"}

    captured: dict = {}

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "classify":
            parsed = {"skill": "general-records.md"}
        elif task == "extract-section":
            parsed = sec1 if "This is SECTION 1" in _flat(prompt) else sec2
        elif task == "briefing":
            captured["briefing_prompt"] = prompt
            parsed = {"investigation_status": "x", "what_was_ingested": ["test-doc.pdf"]}
        else:
            parsed = {"entity_syntheses": []} if task == "entity-synthesis" else {"events": []}
        return model_client.ModelResult(parsed=parsed, text="", model="m",
                                        backend="claude-agent-sdk", auth_mode="subscription", cost_usd=0.02)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    summary = asyncio.run(orchestrate.run(vault))
    assert summary["extracted"] == 1 and summary["failed"] == 0
    assert (vault / "entities" / "organization" / "acme-corp.md").exists()
    # carry-forward merged the two sections into one entity
    note = (vault / "entities" / "organization" / "acme-corp.md").read_text()
    assert "Acme Corporation" in note   # merge kept the longer surface form
    # the two sections' observations were merged into the scratchpad and fed to the briefing
    assert "section 1 obs" in captured["briefing_prompt"]
    assert "section 2 obs" in captured["briefing_prompt"]
    # and the compact result's key_facts reach the briefing too (#150)
    assert '"key_facts"' in captured["briefing_prompt"]


def test_sectioned_carry_forward_dedupes_entities_and_caps_observations(tmp_path, monkeypatch):
    """A5/A6: across 3 sections, an entity present in every section is carried forward once
    (not once per section it already appeared in), only the immediately preceding section's
    observations are carried (not every prior section's concatenated), and the investigation
    brief reaches every section's prompt."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, text="a very long document ...")
    tmpd = vault / ".watchdog" / "tmp"
    tmpd.mkdir(parents=True, exist_ok=True)
    for i in (1, 2, 3):
        (tmpd / f"section_abc123_0{i}.md").write_text(f"<!-- PAGE {i} -->\n\npart {i}")
    plan = {"sectioned": True, "page_count": 3, "sections": [
        {"index": i, "label": f"pages {i}", "paginated": True,
         "pages_path": f".watchdog/tmp/section_abc123_0{i}.md"} for i in (1, 2, 3)
    ]}
    pf = {"filename": "test-doc.pdf", "existing_entities": [], "known_document_types": [],
          "page_count": 3}

    acme_entity = {"id": "acme-corp", "name": "Acme Corp", "type": "Company",
                   "timeline_events": [], "roles": []}
    sections_out = [
        {"document": {"sha256": "abc123", "filename": "test-doc.pdf", "title": "Acme AR",
                      "document_type": "Annual Report",
                      "key_facts": [{"fact": "x", "basis": "stated"}]},
         "entities": [acme_entity], "morgue_entity_id": "acme-corp",
         "morgue_document_type": "annual-report", "observations": "section 1 obs"},
        {"entities": [acme_entity], "observations": "section 2 obs"},
        {"entities": [acme_entity], "observations": "section 3 obs"},
        {"summary": "digest text"},   # the post-merge digest call (#279)
    ]
    seen_prompts = []

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        seen_prompts.append(prompt)
        return model_client.ModelResult(parsed=sections_out[len(seen_prompts) - 1], text="",
                                        model="m", backend="b", auth_mode="subscription", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    asyncio.run(orchestrate._extract_sectioned(
        vault, "abc123", pf, "SKILL", plan, "sonnet", "annual-report",
        brief="INVESTIGATE THE FRAUD"))

    assert len(seen_prompts) == 4   # 3 section calls + 1 post-merge digest call
    flat_prompts = [_flat(p) for p in seen_prompts[:3]]
    # section 2's prompt carries section 1's single entity line, once (not duplicated)
    assert flat_prompts[1].count("acme-corp | Acme Corp | Company") == 1
    assert "section 1 obs" in flat_prompts[1]
    # section 3's prompt still lists the entity exactly once — no per-section duplication
    assert flat_prompts[2].count("acme-corp | Acme Corp | Company") == 1
    # only the immediately preceding section's observations are carried forward
    assert "section 2 obs" in flat_prompts[2]
    assert "section 1 obs" not in flat_prompts[2]
    # A6: the investigation brief reaches every section's prompt
    for p in flat_prompts:
        assert "INVESTIGATE THE FRAUD" in p


# ── whole-document digest for sectioned extraction (#279) ───────────────────────

def _sectioned_plan_and_pf(vault, sha="abc123", filename="test-doc.pdf"):
    tmpd = vault / ".watchdog" / "tmp"
    tmpd.mkdir(parents=True, exist_ok=True)
    (tmpd / f"section_{sha}_01.md").write_text("<!-- PAGE 1 -->\n\npart 1")
    plan = {"sectioned": True, "page_count": 1, "sections": [
        {"index": 1, "label": "pages 1", "paginated": True,
         "pages_path": f".watchdog/tmp/section_{sha}_01.md"},
    ]}
    pf = {"filename": filename, "existing_entities": [], "known_document_types": [],
          "page_count": 1, "original_path": f"_INCOMING/{filename}"}
    return plan, pf


_SEC1 = {
    "document": {"sha256": "abc123", "filename": "test-doc.pdf", "title": "Acme AR",
                 "document_type": "Annual Report",
                 "key_facts": [{"fact": "Filed in 2024", "basis": "stated"}]},
    "entities": [{"id": "acme-corp", "name": "Acme Corp", "type": "Company", "roles": []}],
    "morgue_entity_id": "acme-corp", "morgue_document_type": "annual-report",
    "observations": "",
}


def test_extract_sectioned_composes_digest_after_merge(tmp_path, monkeypatch):
    """Exactly one additional _call_model runs after the section calls, with task="digest" and
    schema=schemas.DIGEST; it runs on the extractor model/backend (the same that read the
    sections, #279), its summary lands in document.summary and its cost is added."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, text="a very long document ...")
    plan, pf = _sectioned_plan_and_pf(vault)
    calls = []

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        calls.append({"task": task, "model": model, "backend": backend, "schema": schema,
                      "prompt": prompt})
        parsed = _SEC1 if task == "extract-section" else {"summary": "Composed digest text."}
        return model_client.ModelResult(parsed=parsed, text="", model=model or "m",
                                        backend=backend or "b", auth_mode="subscription",
                                        cost_usd=0.01)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    extraction, scratchpad, cost, ok, errors, warnings, _written = asyncio.run(orchestrate._extract_sectioned(
        vault, "abc123", pf, "SKILL TEXT", plan, "sonnet", "annual-report", backend="claude-api",
        brief="CHASE THE FRAUD"))

    digest_calls = [c for c in calls if c["task"] == "digest"]
    assert len(digest_calls) == 1
    assert digest_calls[0]["schema"] is schemas.DIGEST
    assert digest_calls[0]["model"] == "sonnet"        # extractor tier, not finalizer
    assert digest_calls[0]["backend"] == "claude-api"  # same backend the sections used
    # Extractor-tier context parity (#279): the digest prompt carries the skill + brief.
    assert "SKILL TEXT" in digest_calls[0]["prompt"]
    assert "CHASE THE FRAUD" in digest_calls[0]["prompt"]
    assert "test-doc.pdf" in digest_calls[0]["prompt"]
    assert extraction["document"]["summary"] == "Composed digest text."
    assert ok, errors
    assert cost == pytest.approx(0.02)   # one section call + one digest call


def test_digest_model_error_falls_back_to_deterministic_stitch(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault, text="a very long document ...")
    plan, pf = _sectioned_plan_and_pf(vault)

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "extract-section":
            return model_client.ModelResult(parsed=_SEC1, text="", model="m", backend="b",
                                            auth_mode="subscription", cost_usd=0.01)
        raise model_client.ModelError("boom")
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    extraction, scratchpad, cost, ok, errors, warnings, _written = asyncio.run(orchestrate._extract_sectioned(
        vault, "abc123", pf, "SKILL", plan, "sonnet", "annual-report"))

    summary = extraction["document"]["summary"]
    assert "Acme AR" in summary and "Filed in 2024" in summary
    assert ok, errors


def test_digest_empty_response_falls_back_to_deterministic_stitch(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault, text="a very long document ...")
    plan, pf = _sectioned_plan_and_pf(vault)

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        parsed = _SEC1 if task == "extract-section" else {"summary": ""}
        return model_client.ModelResult(parsed=parsed, text="", model="m", backend="b",
                                        auth_mode="subscription", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    extraction, scratchpad, cost, ok, errors, warnings, _written = asyncio.run(orchestrate._extract_sectioned(
        vault, "abc123", pf, "SKILL", plan, "sonnet", "annual-report"))

    summary = extraction["document"]["summary"]
    assert "Acme AR" in summary and "Filed in 2024" in summary
    assert ok, errors


def test_run_sectioned_path_composes_digest_on_extractor_tier(tmp_path, monkeypatch):
    """run()'s sectioned path composes the digest on the extractor model/backend (#279) — the
    digest is extraction output, not a finalizer task, so it rides extract_model, not post_model."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, text="a very long document ...")
    plan, _ = _sectioned_plan_and_pf(vault)
    monkeypatch.setattr(orchestrate.section, "run", lambda v, s, **kw: plan)
    seen = []

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        seen.append((task, model, backend))
        parsed = {
            "classify": {"skill": "general-records.md"},
            "extract-section": _SEC1,
            "digest": {"summary": "digest text"},
            "entity-synthesis": {"entity_syntheses": []},
            "timeline-dedup": {"groups": []},
            "briefing": {"investigation_status": "x", "what_was_ingested": []},
        }.get(task, {})
        return model_client.ModelResult(parsed=parsed, text="", model=model or "m",
                                        backend=backend or "b", auth_mode="subscription", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    asyncio.run(orchestrate.run(vault, extract_model="sonnet", extract_backend="claude-api",
                                post_model="haiku", post_backend="claude-api"))

    digest_calls = [c for c in seen if c[0] == "digest"]
    assert digest_calls == [("digest", "sonnet", "claude-api")]   # extractor tier, not post_model


def test_stitch_digest_with_title_type_pages_and_facts():
    doc = {"title": "Acme AR", "document_type": "Annual Report",
           "key_facts": [{"fact": "Filed in 2024."}, {"fact": "Revenue grew"}]}
    s = orchestrate._stitch_digest(doc, 12)
    assert s.startswith("Acme AR — Annual Report, 12 pages.")
    assert "Filed in 2024." in s
    assert "Revenue grew." in s


def test_stitch_digest_without_title_or_type_or_pages():
    assert orchestrate._stitch_digest({}, None) == "Untitled document."


def test_stitch_digest_empty_facts_yields_orientation_line_alone():
    doc = {"title": "Acme AR", "document_type": "Annual Report"}
    assert orchestrate._stitch_digest(doc, 5) == "Acme AR — Annual Report, 5 pages."


def test_stitch_digest_caps_at_eight_facts():
    facts = [{"fact": f"Fact {i}"} for i in range(12)]
    s = orchestrate._stitch_digest({"title": "T", "key_facts": facts}, None)
    assert s.count("Fact ") == 8


def test_stitch_digest_skips_blank_facts():
    """Empty/whitespace-only or missing fact text is dropped, not rendered as a bare '.'."""
    doc = {"title": "T", "key_facts": [{"fact": "Real fact"}, {"fact": "  "}, {}, {"fact": ""}]}
    assert orchestrate._stitch_digest(doc, None) == "T. Real fact."


# ── claude-batch (#214) ─────────────────────────────────────────────────────────

def test_run_batch_requires_pinned_skill(tmp_path):
    vault = make_vault(tmp_path)
    with pytest.raises(model_client.ModelError, match="pinned skill"):
        asyncio.run(orchestrate._run_batch(vault, [], None, "sonnet", None, None, 5,
                                           "haiku", 5, None))


def test_run_batch_requires_api_key_auth(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "subscription"})
    with pytest.raises(model_client.ModelError, match="api-key auth"):
        asyncio.run(orchestrate._run_batch(vault, [], None, "sonnet", "/tmp/skill.md", None, 5,
                                           "haiku", 5, None))


def test_submit_batch_splits_sectioned_and_whole_doc(tmp_path, monkeypatch):
    """A sectioned doc is routed to the normal synchronous _extract_document (forced onto
    claude-api — a batch request can't carry sequential section carry-forward); a non-sectioned
    doc's prompt is handed to batch_extract.submit instead of extracted synchronously."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="small", filename="small.pdf", text="short doc")
    _queue_doc(vault, sha="big", filename="big.pdf", text="a very long document ...")
    monkeypatch.setattr(orchestrate.section, "run", lambda v, s, **kw: {"sectioned": s == "big"})

    sectioned_calls = []
    async def fake_extract_document(vault, sha, brief, extract_model, classify_model,
                                    classify_pages, pinned_skill, extract_effort,
                                    extract_backend, classify_backend):
        sectioned_calls.append({"sha": sha, "extract_backend": extract_backend})
        return {"sha256": sha, "filename": f"{sha}.pdf", "status": "ok", "record_skill": "s"}
    monkeypatch.setattr(orchestrate, "_extract_document", fake_extract_document)

    submitted = {}
    async def fake_submit(vault, docs, *, model, effort, skill_label, api_key):
        submitted["docs"] = docs
        return "batch_xyz"
    monkeypatch.setattr(orchestrate.batch_extract, "submit", fake_submit)

    skill_file = tmp_path / "pinned.md"
    skill_file.write_text("SKILL BODY")

    out = asyncio.run(orchestrate._submit_batch(
        vault, ["small", "big"], None, "sonnet", str(skill_file), None, 5, "haiku", 5, None,
        api_key="sk-x"))

    assert out["batch_pending"] is True
    assert sectioned_calls == [{"sha": "big", "extract_backend": "claude-api"}]
    assert any(r.get("sha256") == "big" for r in out["results"])
    assert [d["sha"] for d in submitted["docs"]] == ["small"]
    assert submitted["docs"][0]["prompt"][1]["cache_control"]["ttl"] == "1h"


def test_extract_document_skips_already_extracted_and_unlinks_queue_file(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="sha1", filename="a.pdf")
    monkeypatch.setattr(orchestrate.preflight, "run",
                        lambda v, s: {"already_extracted": True, "filename": "a.pdf"})

    result = asyncio.run(orchestrate._extract_document(vault, "sha1", None, "sonnet", "haiku"))

    assert result["status"] == "skipped"
    assert not (vault / ".watchdog" / "queue" / "sha1.json").exists()


def test_submit_batch_skips_already_extracted_and_preflight_errors(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="done", filename="done.pdf")
    monkeypatch.setattr(orchestrate.preflight, "run", lambda v, s: (
        {"error": "not found"} if s == "gone" else
        {"already_extracted": True, "filename": "done.pdf"}))
    skill_file = tmp_path / "pinned.md"
    skill_file.write_text("SKILL")

    out = asyncio.run(orchestrate._submit_batch(
        vault, ["gone", "done"], None, "sonnet", str(skill_file), None, 5, "haiku", 5, None,
        api_key="sk-x"))

    statuses = {r["sha256"]: r["status"] for r in out["results"]}
    assert statuses == {"gone": "failed", "done": "skipped"}
    assert out["batch_pending"] is False   # nothing left to submit
    # A queue file for an already-extracted doc is a leftover from a crash in the narrow
    # pre-unlink window — clean it up so it doesn't phantom-report "skipping" forever (#265).
    assert not (vault / ".watchdog" / "queue" / "done.json").exists()


def test_resume_batch_reports_progress_when_not_ended(tmp_path, monkeypatch, capsys):
    vault = make_vault(tmp_path)
    state = {"batch_id": "b1", "shas": ["a", "b"], "model": "claude-sonnet-4-6",
            "skill_label": "s", "effort": None}

    async def fake_status(batch_id, api_key):
        return {"processing_status": "in_progress", "request_counts": {"processing": 1, "succeeded": 1}}
    monkeypatch.setattr(orchestrate.batch_extract, "status", fake_status)

    out = asyncio.run(orchestrate._resume_batch(vault, state, "/tmp/x.md", None, "sk-x"))
    assert out == {"results": [], "batch_pending": True}
    assert "still processing" in capsys.readouterr().out


def test_resume_batch_collects_and_clears_state_when_ended(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="sha1", filename="a.pdf")
    state = {"batch_id": "b1", "shas": ["sha1"], "model": "claude-sonnet-4-6",
            "skill_label": "annual-report", "effort": None}
    batch_extract.write_state(vault, state)

    async def fake_status(batch_id, api_key):
        return {"processing_status": "ended", "request_counts": {"succeeded": 1}}
    monkeypatch.setattr(orchestrate.batch_extract, "status", fake_status)

    async def fake_collect(batch_id, api_key, model_id):
        return {"sha1": {"ok": True, "parsed": _extraction(sha="sha1", filename="a.pdf"),
                         "usage": {}, "cost_usd": 0.02, "error": None}}
    monkeypatch.setattr(orchestrate.batch_extract, "collect", fake_collect)

    skill_file = tmp_path / "pinned.md"
    skill_file.write_text("SKILL")

    out = asyncio.run(orchestrate._resume_batch(vault, state, str(skill_file), None, "sk-x"))
    assert out["batch_pending"] is False
    assert out["results"][0]["status"] == "ok"
    assert batch_extract.read_state(vault) is None   # state cleared on a clean collection


def test_finish_batch_item_repairs_invalid_result_via_claude_api(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="sha1", filename="a.pdf")

    seen_backends = []
    async def fake_acomplete(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        seen_backends.append(backend)
        return model_client.ModelResult(parsed=_extraction(sha="sha1", filename="a.pdf"),
                                        text="", model="m", backend="claude-api",
                                        auth_mode="api-key", cost_usd=0.03)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake_acomplete)

    item = {"ok": False, "parsed": None, "usage": None, "cost_usd": None,
           "error": "batch response was not valid JSON"}
    result = asyncio.run(orchestrate._finish_batch_item(
        vault, "sha1", item, "SKILL BODY", "annual-report", None, "sk-x"))

    assert result["status"] == "ok"
    assert seen_backends == ["claude-api"]   # repaired synchronously, not re-batched


def test_finish_batch_item_fails_when_result_missing(tmp_path):
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="sha1", filename="a.pdf")
    result = asyncio.run(orchestrate._finish_batch_item(
        vault, "sha1", None, "SKILL", "s", None, "sk-x"))
    assert result["status"] == "failed"


def test_finish_batch_item_skips_already_extracted_and_unlinks_queue_file(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="sha1", filename="a.pdf")
    monkeypatch.setattr(orchestrate.preflight, "run",
                        lambda v, s: {"already_extracted": True, "filename": "a.pdf"})

    result = asyncio.run(orchestrate._finish_batch_item(
        vault, "sha1", None, "SKILL", "s", None, "sk-x"))

    assert result["status"] == "skipped"
    assert not (vault / ".watchdog" / "queue" / "sha1.json").exists()


def test_finish_batch_item_records_usage_for_the_batch_call_itself(tmp_path):
    """D64: a batch-collected item that already passed validation never calls `_call_model` —
    without recording it directly in `_finish_batch_item`, its real token spend would silently
    never reach `usage-<ts>.json`, unlike every synchronous extraction path."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="sha1", filename="a.pdf")

    item = {"ok": True, "parsed": _extraction(sha="sha1", filename="a.pdf"),
           "usage": {"input_tokens": 500, "output_tokens": 80}, "cost_usd": 0.015, "error": None}

    orchestrate._usage = []
    try:
        result = asyncio.run(orchestrate._finish_batch_item(
            vault, "sha1", item, "SKILL BODY", "annual-report", None, "sk-x",
            model="claude-sonnet-4-6"))
        assert result["status"] == "ok"
        calls = [c for c in orchestrate._usage if c["task"] == "extract"]
        assert len(calls) == 1
        assert calls[0].pop("end_ts") > 0   # completion timestamp stamped at record time
        assert calls[0] == {
            "task": "extract", "model": "claude-sonnet-4-6", "backend": "claude-batch",
            "input_tokens": 500, "output_tokens": 80, "cache_read_tokens": 0, "cache_write_tokens": 0,
            "cost_usd": 0.015, "attempts": 1, "latency_s": 0.0, "effort": None, "auth_mode": "api-key",
            "filename": "a.pdf", "detail": "pages 1–1",
        }
    finally:
        orchestrate._usage = None


def test_finish_batch_item_stamps_extraction_provenance(tmp_path):
    """The claude-batch path (#214) has its own extraction call sequence — separate from
    _simple_extract/_extract_sectioned — so it needs its own coverage that record_skill_hash/
    extract_model/extract_effort (#268) reach documents.json from here too."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="sha1", filename="a.pdf")

    item = {"ok": True, "parsed": _extraction(sha="sha1", filename="a.pdf"),
           "usage": None, "cost_usd": 0.015, "error": None}
    result = asyncio.run(orchestrate._finish_batch_item(
        vault, "sha1", item, "SKILL BODY", "annual-report", None, "sk-x",
        model="claude-sonnet-4-6", effort="medium"))

    assert result["status"] == "ok"
    docs = json.loads((vault / ".watchdog" / "registry" / "documents.json").read_text())
    entry = docs["sha1"]
    assert entry["record_skill_hash"] == hashlib.sha256(b"SKILL BODY").hexdigest()[:12]
    assert entry["extract_model"] == "claude-sonnet-4-6"
    assert entry["extract_effort"] == "medium"


def test_run_dispatches_to_batch_and_merges_batch_pending_into_summary(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="sha1", filename="a.pdf")
    skill_file = tmp_path / "pinned.md"
    skill_file.write_text("SKILL")

    async def fake_run_batch(*args, **kwargs):
        return {"results": [], "batch_pending": True}
    monkeypatch.setattr(orchestrate, "_run_batch", fake_run_batch)

    summary = asyncio.run(orchestrate.run(vault, extract_backend="claude-batch",
                                          pinned_skill=str(skill_file)))
    assert summary["batch_pending"] is True
    assert summary["extracted"] == 0
    assert "post_ingest" not in summary   # nothing extracted this run → no finalize


def test_run_resumes_pending_batch_even_with_empty_queue(tmp_path, monkeypatch):
    """A pending batch must be checked even when nothing is newly queued — mirrors
    has_pending_finalization's 'resolve the pending thing first' precedent."""
    vault = make_vault(tmp_path)
    skill_file = tmp_path / "pinned.md"
    skill_file.write_text("SKILL")
    batch_extract.write_state(vault, {"batch_id": "b1", "shas": [], "model": "claude-sonnet-4-6",
                                      "skill_label": "s", "effort": None})
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})

    submit_calls = []
    async def fake_submit(*a, **k):
        submit_calls.append(1)
        return "should-not-be-called"
    monkeypatch.setattr(orchestrate.batch_extract, "submit", fake_submit)

    async def fake_status(batch_id, api_key):
        return {"processing_status": "in_progress", "request_counts": {}}
    monkeypatch.setattr(orchestrate.batch_extract, "status", fake_status)

    summary = asyncio.run(orchestrate.run(vault, extract_backend="claude-batch",
                                          pinned_skill=str(skill_file)))
    assert summary["batch_pending"] is True
    assert not submit_calls   # resumed the pending batch instead of submitting a new one
