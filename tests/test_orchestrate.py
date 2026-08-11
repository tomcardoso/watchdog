"""Integration test for the Python orchestrator: the per-document flow runs through
the REAL preflight/postflight/write_vault with the model mocked."""

import asyncio
import hashlib
import json
import time
from pathlib import Path

import pytest

from watchdog import model_client, telemetry_db
from watchdog.cmd import auth as auth_module
from watchdog.pipeline import batch_extract, orchestrate, schemas, timeline

from tests.test_write_vault import make_extraction, make_vault

_flat = model_client._flatten_prompt   # extract/section prompts are content-block lists (A1)


def _queue_doc(vault, sha="abc123", filename="test-doc.pdf", text="Acme Corp filed an annual report.",
              sidecar=None, page_count=1):
    """`sidecar`, if given, stands in for what chew would already have filtered into the queue
    JSON (pipeline/sidecar.py, D121) — pass already-allowlisted text, not a raw sidecar file."""
    qdir = vault / ".watchdog" / "queue"
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / f"{sha}.json").write_text(json.dumps({
        "sha256": sha, "filename": filename, "source_path": f"_INCOMING/{filename}",
        "page_count": page_count, "pages": [{"page": 1, "markdown": text}],
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


def test_call_model_records_failed_usage_and_reraises(tmp_path, monkeypatch):
    """#412/D125: a ModelError carrying usage/cost (the JSON-validation-failure/truncation
    paths — the only ones where an attempt actually reached the model) must still be recorded,
    flagged `failed=True`, before propagating — the failed attempt's tokens were real spend."""
    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        raise model_client.ModelError(
            "task 't' failed JSON validation after 2 attempt(s) on claude-api: bad json",
            usage={"input_tokens": 20}, cost_usd=0.05, attempts=2,
            model="claude-sonnet-4-6", backend="claude-api", auth_mode="api-key")
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    orchestrate._usage = []
    try:
        with pytest.raises(model_client.ModelError):
            asyncio.run(orchestrate._call_model(task="extract", prompt="p", schema=schemas.EXTRACTION,
                                                filename="broke.pdf"))
        assert len(orchestrate._usage) == 1
        record = orchestrate._usage[0]
        assert record["failed"] is True
        assert record["input_tokens"] == 20
        assert record["cost_usd"] == 0.05
        assert record["attempts"] == 2
        assert record["filename"] == "broke.pdf"
    finally:
        orchestrate._usage = None


def test_call_model_prompt_hash_stable_and_differs(tmp_path, monkeypatch):
    """#611: `_call_model` hashes the actual prompt text sent — identical prompts hash
    identically, and a changed prompt hashes differently, since that hash is what lets a later
    telemetry query tell "the model changed" apart from "the prompt changed"."""
    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        return model_client.ModelResult(
            parsed={"name": "Acme"}, text="", model="m", backend="claude-api",
            auth_mode="api-key", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    orchestrate._usage = []
    try:
        asyncio.run(orchestrate._call_model(task="extract", prompt="hello world",
                                            schema=schemas.EXTRACTION))
        asyncio.run(orchestrate._call_model(task="extract", prompt="hello world",
                                            schema=schemas.EXTRACTION))
        asyncio.run(orchestrate._call_model(task="extract", prompt="a different prompt",
                                            schema=schemas.EXTRACTION))
        hashes = [r["prompt_hash"] for r in orchestrate._usage]
        assert hashes[0] == hashes[1]
        assert hashes[0] != hashes[2]
        assert hashes[0] == hashlib.sha256("hello world".encode("utf-8")).hexdigest()
    finally:
        orchestrate._usage = None


def test_call_model_records_usage_to_telemetry_db(tmp_path, monkeypatch):
    """#611: a real vault + an active run tags the call's telemetry row with this run's
    benchmark id and config snapshot, in addition to the JSON usage file `_usage` already gets."""
    from watchdog import telemetry_db
    import sqlite3

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        return model_client.ModelResult(
            parsed={"name": "Acme"}, text="", model="m", backend="claude-api",
            auth_mode="api-key", cost_usd=0.0, usage={"input_tokens": 10, "output_tokens": 2})
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    vault = tmp_path / "vault"
    vault.mkdir()
    orchestrate._begin_usage_run(vault, benchmark_arm_id="arm-7",
                                 config_snapshot={"extract_model": "sonnet"})
    try:
        asyncio.run(orchestrate._call_model(task="extract", prompt="p", schema=schemas.EXTRACTION,
                                            vault=vault))
        conn = sqlite3.connect(telemetry_db.DB_PATH)
        try:
            row = conn.execute(
                "SELECT benchmark_arm_id, config_json, run_id FROM calls").fetchone()
        finally:
            conn.close()
        assert row[0] == "arm-7"
        assert json.loads(row[1]) == {"extract_model": "sonnet"}
        assert row[2] == orchestrate._run_id
    finally:
        orchestrate._end_usage_run(vault)


def test_call_model_telemetry_db_failure_logged_not_raised(tmp_path, monkeypatch):
    """#611: a telemetry write failure (locked db, disk full) must not break the ingest call
    it's observing — `_record_usage` catches it and logs a WARN to the vault's own ingest.log,
    same posture as every other side channel `_call_model`/`_record_usage` already has."""
    from watchdog import telemetry_db as telemetry_db_mod

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        return model_client.ModelResult(
            parsed={"name": "Acme"}, text="", model="m", backend="claude-api",
            auth_mode="api-key", cost_usd=0.0, usage={"input_tokens": 10, "output_tokens": 2})
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    def boom(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(telemetry_db_mod, "record_call", boom)

    vault = tmp_path / "vault"
    vault.mkdir()
    orchestrate._begin_usage_run(vault)
    try:
        r = asyncio.run(orchestrate._call_model(task="extract", prompt="p", schema=schemas.EXTRACTION,
                                                vault=vault))
        assert r.parsed == {"name": "Acme"}   # the call itself still succeeded
        log = (vault / ".watchdog" / "registry" / "ingest.log").read_text(encoding="utf-8")
        assert "WARN telemetry_db write failed for task=extract" in log
    finally:
        orchestrate._end_usage_run(vault)


def test_call_model_does_not_record_usage_for_error_without_usage(tmp_path, monkeypatch):
    """A ModelError raised before any attempt reached the model (e.g. auth/backend resolution
    failures) carries no usage/cost — `_call_model` must not synthesize a bogus failed record
    for it."""
    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        raise model_client.ModelError("no auth configured")
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    orchestrate._usage = []
    try:
        with pytest.raises(model_client.ModelError):
            asyncio.run(orchestrate._call_model(task="extract", prompt="p", schema=schemas.EXTRACTION))
        assert orchestrate._usage == []
    finally:
        orchestrate._usage = None


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


def test_compact_result_omits_est_input_tokens_when_not_given():
    """Existing call sites that don't pass `est_input_tokens` (e.g. this test file's other
    fixtures) keep the pre-#417 result shape exactly — no fabricated key."""
    r = orchestrate._compact_result("sha1", "doc.pdf", {"document": {}, "entities": []}, {}, 0.01, {})
    assert "est_input_tokens" not in r


def test_compact_result_carries_est_input_tokens_when_given():
    """#417: the naive chars/4 estimate for this document's own pages rides alongside cost_usd,
    so a run's usage totals can later compare estimate to actual."""
    r = orchestrate._compact_result("sha1", "doc.pdf", {"document": {}, "entities": []}, {}, 0.01, {},
                                    est_input_tokens=250)
    assert r["est_input_tokens"] == 250


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


# ── Document-request dedup (#416): model groups indices, Python performs the merge ────────────

def _seed_open_requests(vault, whats):
    """Record one open request per `what` string, each from its own document, and return the
    `open_requests`-shaped list `_apply_request_dedup` expects (rid + what, index-aligned)."""
    from watchdog.pipeline import requests as requests_module
    for i, what in enumerate(whats):
        requests_module.record(
            vault, [{"type": "Affidavit", "what": what}],
            sha256=f"{i:064d}", filename=f"doc-{i}.pdf", document_note=f"documents/doc-{i}")
    return requests_module.open_requests(vault)


def test_apply_request_dedup_folds_the_named_duplicates(tmp_path):
    vault = make_vault(tmp_path)
    open_ = _seed_open_requests(vault, [
        "Affidavit of Dr. Robert Haché sworn January 30, 2021",
        "Haché Affidavit, sworn January 30, 2021, with exhibits",
        "Second Report of the Monitor dated March 11, 2021",
    ])
    # open_requests() sorts newest-added-first, so pin indices by `what` rather than assuming order.
    by_what = {r["what"]: i for i, r in enumerate(open_)}
    hache_a = by_what["Affidavit of Dr. Robert Haché sworn January 30, 2021"]
    hache_b = by_what["Haché Affidavit, sworn January 30, 2021, with exhibits"]
    monitor = by_what["Second Report of the Monitor dated March 11, 2021"]

    from watchdog.pipeline import requests as requests_module
    folded = orchestrate._apply_request_dedup(vault, open_, [
        {"keep": hache_a, "duplicates": [hache_b]},
        {"keep": monitor, "duplicates": []},
    ])

    assert folded == 1
    still_open = {r["what"] for r in requests_module.open_requests(vault)}
    assert still_open == {
        "Affidavit of Dr. Robert Haché sworn January 30, 2021",
        "Second Report of the Monitor dated March 11, 2021",
    }


def test_apply_request_dedup_falls_back_to_nothing_on_bad_input(tmp_path):
    vault = make_vault(tmp_path)
    open_ = _seed_open_requests(vault, ["Affidavit of Dr. Robert Haché sworn January 30, 2021"])

    assert orchestrate._apply_request_dedup(vault, open_, None) == 0
    assert orchestrate._apply_request_dedup(vault, open_, []) == 0
    # out-of-range keep → group skipped, nothing folded
    assert orchestrate._apply_request_dedup(vault, open_, [{"keep": 9, "duplicates": [0]}]) == 0


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
    """A reconcile failure must not leave the batch looking clean — but #403 phase 3 moved
    reconciliation BEFORE the commit pass, over the staged batch, so "leaving it finalizable" now
    means nothing in this batch commits at all: every staged extraction artifact is the durable
    input, still pending, and a later `watchdog finalize` retries the whole
    fold -> reconcile -> commit sequence from scratch rather than committing half-reconciled state
    a retry could never revisit (there would be nothing left pending to reconcile against)."""
    fails = {"reconcile": True}

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
            if fails["reconcile"]:
                raise model_client.ModelError("reconcile boom")
            parsed = {"merges": [], "contradictions": []}
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
    # Flag that lets the CLI say "nothing written yet" rather than the post-commit "documents are
    # saved" message — the commit pass never ran.
    assert summary["post_ingest"]["commit_skipped"] is True
    # Nothing committed — both staged artifacts are still pending.
    assert orchestrate._pending_commits(vault) == ["sha-one", "sha-two"]
    assert json.loads((vault / ".watchdog" / "registry" / "entities.json").read_text()) == {}

    # Once reconciliation stops failing, a later finalize completes the deferred commit.
    fails["reconcile"] = False
    out = asyncio.run(orchestrate.finalize(vault, post_model="haiku"))
    assert not out.get("error")
    assert orchestrate._pending_commits(vault) == []
    entities = json.loads((vault / ".watchdog" / "registry" / "entities.json").read_text())
    assert set(entities["acme-corp"]["appears_in"]) == {"sha-one", "sha-two"}


def test_orchestrator_reports_failed_on_postflight_rejection(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    broken = _extraction(valid=False)          # missing morgue_entity_id
    broken["entities"] = []                    # ...and no entity to derive it from (#505)
    _mock(monkeypatch, extraction=broken)

    summary = asyncio.run(orchestrate.run(vault))

    assert summary["failed"] == 1 and summary["extracted"] == 0
    assert summary["results"][0]["status"] == "failed"
    assert "post-flight rejected" in summary["results"][0]["reason"]
    # abort cleanup: queue file moved to _failed/ (preserved, not auto-retried), failure logged
    assert not (vault / ".watchdog" / "queue" / "abc123.json").exists()
    assert (vault / ".watchdog" / "queue" / "_failed" / "abc123.json").exists()
    assert "FAILED" in (vault / ".watchdog" / "registry" / "ingest.log").read_text()


def test_simple_extract_repairs_empty_key_facts_on_substantive_document(tmp_path, monkeypatch):
    """#507/#510: sonnet-high billed a real call and returned an empty key_facts list on a
    17-page document, and the pipeline shipped it as a silent OK. Post-flight now rejects an
    empty-key_facts extraction on a substantive document, feeding _simple_extract's existing
    repair-retry loop — if the retry comes back with real facts, the document still succeeds."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, page_count=17, text=" ".join(["word"] * 500))
    empty = _extraction()
    empty["document"]["key_facts"] = []
    repaired = _extraction()

    calls = {"extract": 0}

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        parsed = {
            "classify": {"skill": "general-records.md"},
            "entity-synthesis": {"entity_syntheses": []},
            "timeline-dedup": {"groups": []},
            "briefing": {"investigation_status": "x", "what_was_ingested": [], "new_entities": []},
        }.get(task)
        if parsed is None:
            calls["extract"] += 1
            parsed = empty if calls["extract"] == 1 else repaired
        return model_client.ModelResult(parsed=parsed, text="", model="m",
                                        backend="claude-agent-sdk", auth_mode="subscription", cost_usd=0.01)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    summary = asyncio.run(orchestrate.run(vault))

    assert calls["extract"] == 2               # initial attempt + exactly one repair retry
    assert summary["extracted"] == 1 and summary["failed"] == 0
    assert summary["results"][0]["key_facts"]   # the repaired, non-empty facts made it through


def test_simple_extract_fails_loudly_when_repair_still_empty(tmp_path, monkeypatch):
    """If the repair retry still comes back with zero key_facts, the document must fail loudly
    (status: failed, logged as FAILED) rather than shipping a silent OK with nothing in it."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, page_count=17, text=" ".join(["word"] * 500))
    empty = _extraction()
    empty["document"]["key_facts"] = []
    _mock(monkeypatch, extraction=empty)

    summary = asyncio.run(orchestrate.run(vault))

    assert summary["failed"] == 1 and summary["extracted"] == 0
    assert summary["results"][0]["status"] == "failed"
    assert "key_facts is empty" in summary["results"][0]["reason"]
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


# ── finalizer_overrides: per-stage model/backend overrides (#433) ─────────────

def test_reconcile_pre_commit_stage_override_routes_reconcile_call(tmp_path, monkeypatch):
    """`finalizer_overrides["reconciliation_model"/"reconciliation_backend"]` routes the
    reconcile call away from post_model/post_backend, which a plain post_model/post_backend run
    would otherwise use."""
    vault = make_vault(tmp_path)
    monkeypatch.setattr(orchestrate.reconcile, "build_bundle",
                        lambda vault, shas: {"entities": [{"id": "e1"}], "pairs": [{"a": "e1", "b": "e2"}]})
    monkeypatch.setattr(orchestrate.reconcile, "apply_merges",
                        lambda vault, shas, parsed, bundle, warn:
                        {"merged": [], "remap": {}, "contradictions": []})

    seen = []

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        seen.append((task, model, backend))
        return model_client.ModelResult(parsed={"merges": [], "contradictions": []}, text="",
                                        model=model or "?", backend=backend or "b",
                                        auth_mode="subscription", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    asyncio.run(orchestrate._reconcile_pre_commit(
        vault, ["sha1"], "haiku", None, None,
        finalizer_overrides={"reconciliation_model": "opus", "reconciliation_backend": "claude-api"}))

    assert seen == [("reconcile", "opus", "claude-api")]


def test_reconcile_pre_commit_falls_back_to_post_model_when_unoverridden(tmp_path, monkeypatch):
    """No `finalizer_overrides` (or a dict missing the reconciliation keys) reconciles on
    post_model/post_backend, unchanged from before #433."""
    vault = make_vault(tmp_path)
    monkeypatch.setattr(orchestrate.reconcile, "build_bundle",
                        lambda vault, shas: {"entities": [{"id": "e1"}], "pairs": [{"a": "e1", "b": "e2"}]})
    monkeypatch.setattr(orchestrate.reconcile, "apply_merges",
                        lambda vault, shas, parsed, bundle, warn:
                        {"merged": [], "remap": {}, "contradictions": []})

    seen = []

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        seen.append((task, model, backend))
        return model_client.ModelResult(parsed={"merges": [], "contradictions": []}, text="",
                                        model=model or "?", backend=backend or "b",
                                        auth_mode="subscription", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    asyncio.run(orchestrate._reconcile_pre_commit(vault, ["sha1"], "haiku", None, "openai"))

    assert seen == [("reconcile", "haiku", "openai")]


def test_post_ingest_stage_overrides_route_synthesis_timeline_briefing(tmp_path, monkeypatch):
    """`finalizer_overrides`' synthesis/timeline/briefing keys each route their own stage away
    from post_model/post_backend — `_seed_collision` forces a real timeline-dedup call, and a
    mocked non-empty synthesis bundle forces a real entity-synthesis call."""
    vault = make_vault(tmp_path)
    _seed_collision(vault)
    (vault / ".watchdog" / "tmp").mkdir(parents=True, exist_ok=True)
    results = [orchestrate._compact_result(
        "sha1", "doc.pdf",
        {"document": {"key_facts": [{"fact": "a fact"}]}, "entities": []},
        {}, 0.01, {})]

    monkeypatch.setattr(orchestrate.synthesis_bundle, "build_bundle",
                        lambda vault, shas: {"entities": [{"id": "e1", "name": "E1"}]})
    monkeypatch.setattr(orchestrate.synthesis_bundle, "apply_bundle",
                        lambda res_path, vault: {"applied": ["e1"]})

    seen = []

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        seen.append((task, model, backend))
        parsed = {
            "entity-synthesis": {"entity_syntheses": []},
            "timeline-dedup": {"groups": [{"keep": 0, "duplicates": [1]}]},
            "briefing": {"investigation_status": "x", "what_was_ingested": []},
        }.get(task, {})
        return model_client.ModelResult(parsed=parsed, text="", model=model or "?",
                                        backend=backend or "b", auth_mode="subscription", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    overrides = {
        "synthesis_model": "opus", "synthesis_backend": "claude-api",
        "timeline_model": "gpt-5-mini", "timeline_backend": "openai",
        "briefing_model": "gemini-2.5-flash", "briefing_backend": "gemini",
    }
    asyncio.run(orchestrate._post_ingest(vault, results, None, "haiku",
                                         finalizer_overrides=overrides))

    by_task = {t: (m, b) for t, m, b in seen}
    assert by_task["entity-synthesis"] == ("opus", "claude-api")
    assert by_task["timeline-dedup"] == ("gpt-5-mini", "openai")
    assert by_task["briefing"] == ("gemini-2.5-flash", "gemini")


def _fake_model(seen, extra_parsed=None):
    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        seen.append((task, model, backend))
        parsed = {
            "briefing": {"investigation_status": "x", "what_was_ingested": []},
            **(extra_parsed or {}),
        }.get(task, {})
        return model_client.ModelResult(parsed=parsed, text="", model=model or "?",
                                        backend=backend or "b", auth_mode="subscription", cost_usd=0.0)
    return fake


def test_post_ingest_folds_duplicate_requests_via_model_pass(tmp_path, monkeypatch):
    """5b (#416): this run adds a request that paraphrases an already-open one. The dedup call
    fires, and its `groups` verdict is what actually merges the ledger entries — Python performs
    no text comparison of its own."""
    vault = make_vault(tmp_path)
    from watchdog.pipeline import requests as requests_module
    requests_module.record(vault, [{"type": "Affidavit", "what": "Haché Affidavit, sworn Jan 30"}],
                           sha256="sha1", filename="doc.pdf", document_note="documents/doc")
    requests_module.record(
        vault, [{"type": "Affidavit", "what": "Affidavit of Dr. Robert Haché sworn January 30"}],
        sha256="preexisting" + "0" * 52, filename="other.pdf", document_note="documents/other")
    open_ = requests_module.open_requests(vault)
    by_what = {r["what"]: i for i, r in enumerate(open_)}
    new_idx = by_what["Haché Affidavit, sworn Jan 30"]
    old_idx = by_what["Affidavit of Dr. Robert Haché sworn January 30"]

    seen = []
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", _fake_model(
        seen, {"request-dedup": {"groups": [{"keep": old_idx, "duplicates": [new_idx]}]}}))

    results = [orchestrate._compact_result(
        "sha1", "doc.pdf", {"document": {"key_facts": []}, "entities": []}, {}, 0.0, {})]
    out = asyncio.run(orchestrate._post_ingest(vault, results, None, "haiku"))

    assert ("request-dedup", "haiku", None) in seen
    assert out["requests_folded"] == 1
    still_open = {r["what"] for r in requests_module.open_requests(vault)}
    assert still_open == {"Affidavit of Dr. Robert Haché sworn January 30"}


def test_post_ingest_skips_request_dedup_when_this_run_added_nothing_new(tmp_path, monkeypatch):
    """Two open requests already exist, but neither's source is in this run's committed shas —
    nothing changed, so the (paid) dedup call must not fire."""
    vault = make_vault(tmp_path)
    from watchdog.pipeline import requests as requests_module
    requests_module.record(vault, [{"type": "Affidavit", "what": "Request A"}],
                           sha256="old1" + "0" * 60, filename="a.pdf", document_note="documents/a")
    requests_module.record(vault, [{"type": "Affidavit", "what": "Request B"}],
                           sha256="old2" + "0" * 60, filename="b.pdf", document_note="documents/b")

    seen = []
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", _fake_model(seen))

    results = [orchestrate._compact_result(
        "sha1", "doc.pdf", {"document": {"key_facts": []}, "entities": []}, {}, 0.0, {})]
    out = asyncio.run(orchestrate._post_ingest(vault, results, None, "haiku"))

    assert not any(t == "request-dedup" for t, _, _ in seen)
    assert "requests_folded" not in out


def test_post_ingest_skips_request_dedup_with_only_one_open_request(tmp_path, monkeypatch):
    """Nothing to compare against — must not fire even though this run's request is new."""
    vault = make_vault(tmp_path)
    from watchdog.pipeline import requests as requests_module
    requests_module.record(vault, [{"type": "Affidavit", "what": "Request A"}],
                           sha256="sha1", filename="doc.pdf", document_note="documents/doc")

    seen = []
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", _fake_model(seen))

    results = [orchestrate._compact_result(
        "sha1", "doc.pdf", {"document": {"key_facts": []}, "entities": []}, {}, 0.0, {})]
    out = asyncio.run(orchestrate._post_ingest(vault, results, None, "haiku"))

    assert not any(t == "request-dedup" for t, _, _ in seen)
    assert "requests_folded" not in out


def test_post_ingest_stage_override_routes_request_dedup_call(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    from watchdog.pipeline import requests as requests_module
    requests_module.record(vault, [{"type": "Affidavit", "what": "Request A"}],
                           sha256="sha1", filename="doc.pdf", document_note="documents/doc")
    requests_module.record(vault, [{"type": "Affidavit", "what": "Request B"}],
                           sha256="old" + "0" * 61, filename="b.pdf", document_note="documents/b")

    seen = []
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", _fake_model(
        seen, {"request-dedup": {"groups": []}}))

    results = [orchestrate._compact_result(
        "sha1", "doc.pdf", {"document": {"key_facts": []}, "entities": []}, {}, 0.0, {})]
    asyncio.run(orchestrate._post_ingest(
        vault, results, None, "haiku",
        finalizer_overrides={"request_dedup_model": "opus", "request_dedup_backend": "claude-api"}))

    by_task = {t: (m, b) for t, m, b in seen}
    assert by_task["request-dedup"] == ("opus", "claude-api")


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


def test_sectioned_repairs_missing_morgue_entity_id_with_no_entities_to_fall_back_on(
        tmp_path, monkeypatch):
    """morgue_document_type turns out to have its own unconditional fallback already
    (_stamp_document derives it from document_type, defaulting to "document" — it can never
    actually reach post-flight empty), and #505 now gives morgue_entity_id a merge-time fallback
    too whenever the document has at least one entity. The section-1-only repair retry (#505/
    #506) is still needed for the one case neither fallback can cover: every section — including
    section 1 — comes back with zero entities, so there is nothing to derive morgue_entity_id
    from. The repair asks section 1 again, hoping for at least one entity this time."""
    vault = make_vault(tmp_path)
    monkeypatch.setattr(orchestrate.section, "_config_get", lambda k, d: d)
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

    sec_first_broken = {
        "document": {"sha256": "abc123", "filename": "test-doc.pdf", "title": "Acme AR",
                     "document_type": "Annual Report", "summary": "Acme report.",
                     "key_facts": [{"fact": "x", "basis": "stated"}]},
        "entities": [],
        "morgue_entity_id": None, "morgue_document_type": "annual-report",
        "observations": "sec1",
    }
    sec_first_repaired = {
        **sec_first_broken,
        "entities": [{"id": "acme-corp", "name": "Acme Corp", "type": "Company",
                      "timeline_events": [], "roles": []}],
        "morgue_entity_id": "acme-corp",
    }
    sec_later = {"entities": [], "observations": "sec2"}   # also empty — nothing to fall back on
    calls = {"extract": 0, "section1": 0, "later": 0}

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "classify":
            parsed = {"skill": "general-records.md"}
        elif task == "extract":
            calls["extract"] += 1
            parsed = _extraction(valid=False)                 # whole-doc → postflight rejects
        elif task == "extract-section":
            if "This is SECTION 1" in _flat(prompt):
                calls["section1"] += 1
                parsed = sec_first_broken if calls["section1"] == 1 else sec_first_repaired
            else:
                calls["later"] += 1
                parsed = sec_later
        elif task == "briefing":
            parsed = {"investigation_status": "x", "what_was_ingested": []}
        else:
            parsed = {"entity_syntheses": []} if task == "entity-synthesis" else {"events": []}
        return model_client.ModelResult(parsed=parsed, text="", model="m",
                                        backend="b", auth_mode="subscription", cost_usd=0.01)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    summary = asyncio.run(orchestrate.run(vault))
    assert calls["section1"] == 2               # initial attempt + exactly one repair retry
    assert calls["later"] == 1                  # the later section is never re-called on repair
    assert summary["extracted"] == 1 and summary["failed"] == 0
    assert (vault / "entities" / "organization" / "acme-corp.md").exists()


def test_sectioned_repair_gives_up_after_one_attempt(tmp_path, monkeypatch):
    """If every section still comes back with zero entities after the repair retry, the document
    fails cleanly — no infinite retry loop (mirrors _simple_extract's capped repair)."""
    vault = make_vault(tmp_path)
    monkeypatch.setattr(orchestrate.section, "_config_get", lambda k, d: d)
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

    sec_first_broken = {
        "document": {"sha256": "abc123", "filename": "test-doc.pdf", "title": "Acme AR",
                     "document_type": "Annual Report", "summary": "Acme report.",
                     "key_facts": [{"fact": "x", "basis": "stated"}]},
        "entities": [],
        "morgue_entity_id": None, "morgue_document_type": "annual-report",
        "observations": "sec1",
    }
    sec_later = {"entities": [], "observations": "sec2"}
    calls = {"section1": 0, "later": 0}

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "classify":
            parsed = {"skill": "general-records.md"}
        elif task == "extract":
            parsed = _extraction(valid=False)
        elif task == "extract-section":
            if "This is SECTION 1" in _flat(prompt):
                calls["section1"] += 1
                parsed = sec_first_broken                      # still broken on the repair try
            else:
                calls["later"] += 1
                parsed = sec_later
        elif task == "briefing":
            parsed = {"investigation_status": "x", "what_was_ingested": []}
        else:
            parsed = {"entity_syntheses": []} if task == "entity-synthesis" else {"events": []}
        return model_client.ModelResult(parsed=parsed, text="", model="m",
                                        backend="b", auth_mode="subscription", cost_usd=0.01)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    summary = asyncio.run(orchestrate.run(vault))
    assert calls["section1"] == 2               # initial attempt + exactly one repair retry, then stop
    assert summary["extracted"] == 0 and summary["failed"] == 1


def test_sectioned_extraction_falls_back_when_morgue_entity_id_missing(tmp_path, monkeypatch):
    """#505: when section 1 leaves morgue_entity_id empty but still supplies an entity, merge.py's
    deterministic fallback fills it in before post-flight ever runs — no repair retry needed."""
    vault = make_vault(tmp_path)
    monkeypatch.setattr(orchestrate.section, "_config_get", lambda k, d: d)
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

    sec_first = {
        "document": {"sha256": "abc123", "filename": "test-doc.pdf", "title": "Acme AR",
                     "document_type": "Annual Report", "summary": "Acme report.",
                     "key_facts": [{"fact": "x", "basis": "stated"}]},
        "entities": [{"id": "acme-corp", "name": "Acme Corp", "type": "Company",
                      "timeline_events": [], "roles": []}],
        "morgue_entity_id": None, "morgue_document_type": "annual-report",
        "observations": "sec1",
    }
    sec_later = {"entities": [{"id": "acme-corp", "name": "Acme Corp", "type": "Company",
                              "timeline_events": [], "roles": []}], "observations": "sec2"}
    calls = {"extract": 0, "section1": 0, "later": 0}

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "classify":
            parsed = {"skill": "general-records.md"}
        elif task == "extract":
            calls["extract"] += 1
            parsed = _extraction(valid=False)                 # whole-doc → postflight rejects
        elif task == "extract-section":
            if "This is SECTION 1" in _flat(prompt):
                calls["section1"] += 1
                parsed = sec_first
            else:
                calls["later"] += 1
                parsed = sec_later
        elif task == "briefing":
            parsed = {"investigation_status": "x", "what_was_ingested": []}
        else:
            parsed = {"entity_syntheses": []} if task == "entity-synthesis" else {"events": []}
        return model_client.ModelResult(parsed=parsed, text="", model="m",
                                        backend="b", auth_mode="subscription", cost_usd=0.01)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    summary = asyncio.run(orchestrate.run(vault))
    assert calls["section1"] == 1               # no repair retry needed — the fallback resolved it
    assert summary["extracted"] == 1 and summary["failed"] == 0
    assert (vault / "morgue" / "acme-corp" / "annual-report" / "test-doc.pdf").exists()


def test_sectioned_does_not_repair_unrelated_postflight_errors(tmp_path, monkeypatch):
    """A postflight failure that ISN'T isolated to the morgue fields (#505) — here, an invalid
    key_facts[].basis value — must not trigger the section-1 repair retry; it was never going to
    fix that."""
    vault = make_vault(tmp_path)
    monkeypatch.setattr(orchestrate.section, "_config_get", lambda k, d: d)
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

    sec_first_bad_fact = {
        "document": {"sha256": "abc123", "filename": "test-doc.pdf", "title": "Acme AR",
                     "document_type": "Annual Report", "summary": "Acme report.",
                     "key_facts": [{"fact": "x", "basis": "maybe"}]},   # invalid basis — unrelated error
        "entities": [{"id": "acme-corp", "name": "Acme Corp", "type": "Company",
                      "timeline_events": [], "roles": []}],
        "morgue_entity_id": "acme-corp", "morgue_document_type": "annual-report",
        "observations": "sec1",
    }
    sec_later = {"entities": [{"id": "acme-corp", "name": "Acme Corp", "type": "Company",
                              "timeline_events": [], "roles": []}], "observations": "sec2"}
    calls = {"section1": 0, "later": 0}

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "classify":
            parsed = {"skill": "general-records.md"}
        elif task == "extract":
            parsed = _extraction(valid=False)
        elif task == "extract-section":
            if "This is SECTION 1" in _flat(prompt):
                calls["section1"] += 1
                parsed = sec_first_bad_fact
            else:
                calls["later"] += 1
                parsed = sec_later
        elif task == "briefing":
            parsed = {"investigation_status": "x", "what_was_ingested": []}
        else:
            parsed = {"entity_syntheses": []} if task == "entity-synthesis" else {"events": []}
        return model_client.ModelResult(parsed=parsed, text="", model="m",
                                        backend="b", auth_mode="subscription", cost_usd=0.01)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    summary = asyncio.run(orchestrate.run(vault))
    assert calls["section1"] == 1                # no repair attempt — the error isn't its to fix
    assert summary["extracted"] == 0 and summary["failed"] == 1


def test_single_page_failure_does_not_section(tmp_path, monkeypatch):
    """A rejection that no fallback can recover from — no entity for #505's merge-time fallback
    to derive morgue_entity_id from, and the force-sectioning retry hits the identical fixture on
    every section — just fails."""
    vault = make_vault(tmp_path)
    _queue_doc(vault)                                          # single page
    broken = _extraction(valid=False)
    broken["entities"] = []
    _mock(monkeypatch, extraction=broken)
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


# ── per-section checkpointing (#498) ──────────────────────────────────────────

def _checkpoint_plan_and_pf(vault, sha="abc123", filename="test-doc.pdf", n=3):
    tmpd = vault / ".watchdog" / "tmp"
    tmpd.mkdir(parents=True, exist_ok=True)
    sections = []
    for i in range(1, n + 1):
        path = tmpd / f"section_{sha}_{i:02d}.md"
        path.write_text(f"Section {i} text.", encoding="utf-8")
        sections.append({"index": i, "label": f"part {i} of {n}", "paginated": False,
                         "pages_path": f".watchdog/tmp/section_{sha}_{i:02d}.md"})
    plan = {"sectioned": True, "page_count": n, "sections": sections}
    pf = {"filename": filename, "existing_entities": [], "known_document_types": [],
         "page_count": n, "original_path": f"_INCOMING/{filename}"}
    return plan, pf


def _checkpoint_section_out(index, *, basis="stated"):
    if index == 1:
        return {
            "document": {"sha256": "abc123", "filename": "test-doc.pdf", "title": "Acme AR",
                        "document_type": "Annual Report",
                        "key_facts": [{"fact": f"Fact {index}", "basis": basis}]},
            "entities": [{"id": "acme-corp", "name": "Acme Corp", "type": "Company", "roles": []}],
            "morgue_entity_id": "acme-corp", "morgue_document_type": "annual-report",
            "observations": f"obs{index}",
        }
    return {
        "document": {"key_facts": [{"fact": f"Fact {index}", "basis": basis}]},
        "entities": [{"id": "acme-corp", "name": "Acme Corp", "type": "Company", "roles": []}],
        "observations": f"obs{index}",
    }


def test_extract_sectioned_resumes_from_existing_checkpoint(tmp_path, monkeypatch):
    """A section already checkpointed on disk (from a prior interrupted attempt) is replayed
    rather than re-called — the resumed run only pays for the sections still missing."""
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    plan, pf = _checkpoint_plan_and_pf(vault, n=3)
    orchestrate._write_section_checkpoint(vault, "abc123", plan["sections"][0],
                                          _checkpoint_section_out(1), 0.05)

    remaining = [_checkpoint_section_out(2), _checkpoint_section_out(3), {"summary": "digest"}]
    seen = []

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        seen.append((task, _flat(prompt)))
        return model_client.ModelResult(parsed=remaining[len(seen) - 1], text="", model="m",
                                        backend="b", auth_mode="subscription", cost_usd=0.02)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    extraction, scratchpad, cost, ok, errors, warnings = asyncio.run(
        orchestrate._extract_sectioned(vault, "abc123", pf, "SKILL", plan, "sonnet", "annual-report"))

    assert ok, errors
    assert len(seen) == 3                                    # sections 2, 3 + digest — not section 1
    assert all("Section 1 text." not in p for _, p in seen)
    assert {f["fact"] for f in extraction["document"]["key_facts"]} == {"Fact 1", "Fact 2", "Fact 3"}
    assert cost == pytest.approx(0.05 + 0.02 + 0.02 + 0.02)   # checkpoint + 2 sections + digest


def test_extract_sectioned_writes_a_checkpoint_per_section(tmp_path, monkeypatch):
    """Each section's result lands on disk as soon as that section's call completes, not just at
    the end of the loop — so an interruption after section 2 still leaves 1 and 2 on disk. Forces
    the failure via an unrelated post-flight error (not repairable, #505) so the run genuinely
    ends without ever reaching the success-path cleanup."""
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    plan, pf = _checkpoint_plan_and_pf(vault, n=3)
    outs = [_checkpoint_section_out(1, basis="maybe"),   # invalid basis — unrelated post-flight error
           _checkpoint_section_out(2), _checkpoint_section_out(3), {"summary": "digest"}]
    seen = []

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        seen.append(task)
        return model_client.ModelResult(parsed=outs[len(seen) - 1], text="", model="m",
                                        backend="b", auth_mode="subscription", cost_usd=0.01)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    extraction, scratchpad, cost, ok, errors, warnings = asyncio.run(
        orchestrate._extract_sectioned(vault, "abc123", pf, "SKILL", plan, "sonnet", "annual-report"))

    assert not ok                                             # invalid basis is a real, unfixable error
    for i in (1, 2, 3):
        assert orchestrate._section_checkpoint_path(vault, "abc123", i).exists()


def test_extract_sectioned_logs_a_section_line_before_each_model_call(tmp_path, monkeypatch):
    """#556: ingest.log used to go straight from a section's HARVEST line to the next
    section's, so the model call's multi-minute latency read as though the (sub-second)
    harvest caused it. A SECTION line, logged right before the call, attributes the gap to
    the call that actually owns it — one per section, each preceded by that section's own
    HARVEST line."""
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    plan, pf = _checkpoint_plan_and_pf(vault, n=2)
    outs = [_checkpoint_section_out(1), _checkpoint_section_out(2), {"summary": "digest"}]
    seen = []

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        seen.append(task)
        return model_client.ModelResult(parsed=outs[len(seen) - 1], text="", model="m",
                                        backend="b", auth_mode="subscription", cost_usd=0.01)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    _, _, _, ok, errors, _ = asyncio.run(
        orchestrate._extract_sectioned(vault, "abc123", pf, "SKILL", plan, "sonnet", "annual-report"))

    assert ok, errors
    log = (vault / ".watchdog" / "registry" / "ingest.log").read_text(encoding="utf-8")
    assert "SECTION test-doc.pdf [part 1 of 2]: extracting…" in log
    assert "SECTION test-doc.pdf [part 2 of 2]: extracting…" in log
    # each section's HARVEST line lands before that section's SECTION line, not after
    assert (log.index("HARVEST test-doc.pdf") < log.index("SECTION test-doc.pdf [part 1 of 2]")
           < log.index("SECTION test-doc.pdf [part 2 of 2]"))


def test_extract_sectioned_clears_checkpoints_once_postflight_succeeds(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    plan, pf = _checkpoint_plan_and_pf(vault, n=2)
    outs = [_checkpoint_section_out(1), _checkpoint_section_out(2), {"summary": "digest"}]
    seen = []

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        seen.append(task)
        return model_client.ModelResult(parsed=outs[len(seen) - 1], text="", model="m",
                                        backend="b", auth_mode="subscription", cost_usd=0.01)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    _, _, _, ok, errors, _ = asyncio.run(
        orchestrate._extract_sectioned(vault, "abc123", pf, "SKILL", plan, "sonnet", "annual-report"))

    assert ok, errors
    assert list((vault / ".watchdog" / "tmp").glob("section_ex_abc123_*.json")) == []


def test_finish_extraction_clears_orphaned_checkpoints_on_a_different_successful_path(
        tmp_path, monkeypatch):
    """A document can leave section checkpoints from a failed sectioned attempt, then later
    succeed via a completely different path on retry (e.g. whole-document extraction succeeds
    once the model behaves) — _finish_extraction's belt-and-braces cleanup must still sweep them,
    since _extract_sectioned's own cleanup never runs on this path at all."""
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    tmp = vault / ".watchdog" / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "section_ex_abc123_01.json").write_text("{}")   # orphaned from an earlier attempt

    _mock(monkeypatch, extraction=_extraction())            # whole-doc succeeds outright
    summary = asyncio.run(orchestrate.run(vault))

    assert summary["extracted"] == 1 and summary["failed"] == 0
    assert not (tmp / "section_ex_abc123_01.json").exists()


def test_extract_sectioned_ignores_a_checkpoint_whose_section_metadata_no_longer_matches(
        tmp_path, monkeypatch):
    """A checkpoint written against a different plan (e.g. the budget/model changed between
    attempts) must not be trusted — the section is re-extracted rather than risk merging content
    across a different section boundary than the one it was actually extracted from (#498)."""
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    plan, pf = _checkpoint_plan_and_pf(vault, n=2)
    stale_section = {**plan["sections"][0], "label": "a different boundary entirely"}
    orchestrate._write_section_checkpoint(vault, "abc123", stale_section,
                                          _checkpoint_section_out(1), 0.05)

    outs = [_checkpoint_section_out(1), _checkpoint_section_out(2), {"summary": "digest"}]
    seen = []

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        seen.append(task)
        return model_client.ModelResult(parsed=outs[len(seen) - 1], text="", model="m",
                                        backend="b", auth_mode="subscription", cost_usd=0.01)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    _, _, cost, ok, errors, _ = asyncio.run(
        orchestrate._extract_sectioned(vault, "abc123", pf, "SKILL", plan, "sonnet", "annual-report"))

    assert ok, errors
    assert len(seen) == 3                        # both sections re-extracted + digest — stale checkpoint unused
    assert cost == pytest.approx(0.03)            # no leftover 0.05 from the stale checkpoint


# ── section-level truncation re-split (#540) ─────────────────────────────────

def _resplit_plan_and_pf(vault, sha="abc123", filename="test-doc.pdf"):
    """A 2-section paginated plan — section 1 spans 4 pages (so it can itself be split in half on
    a page boundary), section 2 is a single page."""
    tmpd = vault / ".watchdog" / "tmp"
    tmpd.mkdir(parents=True, exist_ok=True)
    sec1_text = "\n\n---\n\n".join(
        f"<!-- PAGE {n} -->\n\nSection one page {n} text." for n in range(1, 5))
    (tmpd / f"section_{sha}_01.md").write_text(sec1_text, encoding="utf-8")
    (tmpd / f"section_{sha}_02.md").write_text(
        "<!-- PAGE 5 -->\n\nSection two text.", encoding="utf-8")
    plan = {"sectioned": True, "page_count": 5, "sections": [
        {"index": 1, "label": "pages 1-4", "paginated": True,
         "pages_path": f".watchdog/tmp/section_{sha}_01.md"},
        {"index": 2, "label": "pages 5", "paginated": True,
         "pages_path": f".watchdog/tmp/section_{sha}_02.md"},
    ]}
    pf = {"filename": filename, "existing_entities": [], "known_document_types": [],
         "page_count": 5, "original_path": f"_INCOMING/{filename}"}
    return plan, pf


_RESPLIT_ENTITY = {"id": "acme-corp", "name": "Acme Corp", "type": "Company", "roles": []}


def _resplit_section_out(label, *, first=False):
    out = {"entities": [_RESPLIT_ENTITY], "observations": f"obs {label}",
          "document": {"key_facts": [{"fact": f"Fact {label}", "basis": "stated"}]}}
    if first:
        out["document"].update({"sha256": "abc123", "filename": "test-doc.pdf",
                                "title": "Acme AR", "document_type": "Annual Report"})
        out["morgue_entity_id"] = "acme-corp"
        out["morgue_document_type"] = "annual-report"
    return out


def test_split_section_text_splits_multi_page_section_on_a_page_boundary():
    """A paginated section splits on its own `"\\n\\n---\\n\\n"` page separator, so a page is
    never cut mid-markdown — the two halves rejoin to exactly the original text."""
    text = "\n\n---\n\n".join(f"<!-- PAGE {n} -->\n\ntext {n}" for n in range(1, 5))
    half1, half2 = orchestrate._split_section_text(text, paginated=True)
    assert half1 == "\n\n---\n\n".join(f"<!-- PAGE {n} -->\n\ntext {n}" for n in (1, 2))
    assert half2 == "\n\n---\n\n".join(f"<!-- PAGE {n} -->\n\ntext {n}" for n in (3, 4))
    assert half1 + "\n\n---\n\n" + half2 == text


def test_split_section_text_falls_back_to_character_midpoint_for_a_single_page_section():
    """A single-page section has no page boundary to split on — falls back to a character
    midpoint rather than yielding one empty half."""
    text = "<!-- PAGE 1 -->\n\n" + ("x" * 100)
    half1, half2 = orchestrate._split_section_text(text, paginated=True)
    assert half1 + half2 == text
    assert half1 and half2


def test_split_section_text_splits_non_paginated_text_on_a_character_midpoint():
    text = "a" * 50 + "b" * 50
    half1, half2 = orchestrate._split_section_text(text, paginated=False)
    assert half1 == "a" * 50 and half2 == "b" * 50


def test_truncated_section_call_resplits_and_document_succeeds(tmp_path, monkeypatch):
    """A truncated `extract-section` call on an already-sectioned document used to fail the whole
    document outright — the pre-#540 truncation fallback only lived on `_extract_document`'s
    whole-document branch. Now the truncated section is split in half and both halves retried
    in order; the document still succeeds, both halves' facts land in the merged output in
    order, and the section's checkpointed cost is the sum of both halves."""
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    plan, pf = _resplit_plan_and_pf(vault)
    calls = []
    prompts = []

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        calls.append(task)
        if task == "extract-section":
            prompts.append(prompt)
            n = sum(1 for t in calls if t == "extract-section")
            if n == 1:
                # Section 1's whole (unsplit) attempt truncates.
                raise model_client.ModelError(
                    "output truncated at the model's max-token ceiling", truncated=True)
            outs = {2: _resplit_section_out("half1", first=True),
                   3: _resplit_section_out("half2"),
                   4: _resplit_section_out("sec2")}
            return model_client.ModelResult(parsed=outs[n], text="", model="m", backend="b",
                                            auth_mode="subscription", cost_usd=0.02)
        return model_client.ModelResult(parsed={"summary": "digest"}, text="", model="m",
                                        backend="b", auth_mode="subscription", cost_usd=0.01)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    # The checkpoint is cleared once post-flight succeeds (existing behaviour), so spy on the
    # write itself to inspect what was checkpointed for section 1 mid-run rather than racing the
    # cleanup — mirrors how a resumed run would have found it, before success wipes it away.
    written = []
    real_write = orchestrate._write_section_checkpoint

    def spy_write(vault, sha, sec, parsed, cost_usd, parts=None):
        written.append({"sec": sec, "cost_usd": cost_usd, "parts": parts})
        return real_write(vault, sha, sec, parsed, cost_usd, parts=parts)
    monkeypatch.setattr(orchestrate, "_write_section_checkpoint", spy_write)

    extraction, scratchpad, cost, ok, errors, warnings = asyncio.run(
        orchestrate._extract_sectioned(vault, "abc123", pf, "SKILL", plan, "sonnet", "annual-report"))

    assert ok, errors
    facts = [f["fact"] for f in extraction["document"]["key_facts"]]
    assert facts == ["Fact half1", "Fact half2", "Fact sec2"]   # merge order == halves-then-section-2
    assert cost == pytest.approx(0.02 + 0.02 + 0.02 + 0.01)     # 2 halves + section 2 + digest

    # Both halves checkpointed together under the ORIGINAL section 1 dict — plan identity (#498)
    # must stay byte-identical so a later resume's `data.get("section") != sec` check still matches.
    assert written[0]["sec"] == plan["sections"][0]
    assert len(written[0]["parts"]) == 2
    assert written[0]["cost_usd"] == pytest.approx(0.04)

    # Carry-forward threads through both halves in order: section 2's prompt carries only the
    # immediately preceding half's observations, not the first half's (mirrors the normal
    # per-section loop's own carry-forward rule).
    section2_prompt = _flat(prompts[-1])
    assert "obs half2" in section2_prompt
    assert "obs half1" not in section2_prompt


def test_resume_after_resplit_replays_both_parts_without_recalling_the_model(tmp_path, monkeypatch):
    """A resumed run finds section 1's re-split checkpoint (both halves under `parts`) and replays
    it outright — only section 2 and the digest are still called."""
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    plan, pf = _resplit_plan_and_pf(vault)
    parts = [_resplit_section_out("half1", first=True), _resplit_section_out("half2")]
    orchestrate._write_section_checkpoint(vault, "abc123", plan["sections"][0],
                                          parts[0], 0.04, parts=parts)

    seen = []

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        seen.append(task)
        if task == "extract-section":
            return model_client.ModelResult(parsed=_resplit_section_out("sec2"), text="",
                                            model="m", backend="b", auth_mode="subscription",
                                            cost_usd=0.02)
        return model_client.ModelResult(parsed={"summary": "digest"}, text="", model="m",
                                        backend="b", auth_mode="subscription", cost_usd=0.01)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    extraction, scratchpad, cost, ok, errors, warnings = asyncio.run(
        orchestrate._extract_sectioned(vault, "abc123", pf, "SKILL", plan, "sonnet", "annual-report"))

    assert ok, errors
    assert seen == ["extract-section", "digest"]      # section 1's 2 parts replayed for free
    facts = [f["fact"] for f in extraction["document"]["key_facts"]]
    assert facts == ["Fact half1", "Fact half2", "Fact sec2"]
    assert cost == pytest.approx(0.04 + 0.02 + 0.01)


def test_old_format_checkpoint_without_parts_still_replays(tmp_path, monkeypatch):
    """A checkpoint written before #540 (single `parsed`, no `parts` key) must still replay —
    `_extract_sectioned` rebuilds `parts`/`entities_seen`/`carry` with `c.get("parts") or
    [c["parsed"]]`, which degrades to the pre-#540 single-part shape when `parts` is absent."""
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    plan, pf = _resplit_plan_and_pf(vault)
    orchestrate._write_section_checkpoint(vault, "abc123", plan["sections"][0],
                                          _resplit_section_out("sec1", first=True), 0.05)

    seen = []

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        seen.append(task)
        if task == "extract-section":
            return model_client.ModelResult(parsed=_resplit_section_out("sec2"), text="",
                                            model="m", backend="b", auth_mode="subscription",
                                            cost_usd=0.02)
        return model_client.ModelResult(parsed={"summary": "digest"}, text="", model="m",
                                        backend="b", auth_mode="subscription", cost_usd=0.01)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    extraction, scratchpad, cost, ok, errors, warnings = asyncio.run(
        orchestrate._extract_sectioned(vault, "abc123", pf, "SKILL", plan, "sonnet", "annual-report"))

    assert ok, errors
    assert seen == ["extract-section", "digest"]
    facts = [f["fact"] for f in extraction["document"]["key_facts"]]
    assert facts == ["Fact sec1", "Fact sec2"]
    assert cost == pytest.approx(0.05 + 0.02 + 0.01)


def test_non_truncation_model_error_on_a_section_still_propagates(tmp_path, monkeypatch):
    """#540's re-split is scoped to `ModelError.truncated` specifically — a rate limit, auth
    failure, or genuine schema-validation failure on a section call must still fail the document
    outright, exactly as before this fix."""
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    plan, pf = _resplit_plan_and_pf(vault)
    calls = {"n": 0}

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "extract-section":
            calls["n"] += 1
            raise model_client.ModelError("task 'extract-section' failed JSON validation",
                                          truncated=False)
        return model_client.ModelResult(parsed={"summary": "digest"}, text="", model="m",
                                        backend="b", auth_mode="subscription", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    with pytest.raises(model_client.ModelError, match="failed JSON validation"):
        asyncio.run(orchestrate._extract_sectioned(
            vault, "abc123", pf, "SKILL", plan, "sonnet", "annual-report"))

    assert calls["n"] == 1   # no re-split attempted — it propagated on the very first section call


def test_a_half_that_also_truncates_fails_the_document(tmp_path, monkeypatch):
    """Bounded to a single re-split (#540): if one of the two halves also truncates, it fails the
    document exactly as before this fix — no recursive re-splitting, and no partial checkpoint is
    left behind for the section that never finished."""
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    plan, pf = _resplit_plan_and_pf(vault)
    calls = {"n": 0}

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "extract-section":
            calls["n"] += 1
            raise model_client.ModelError("output truncated at the model's max-token ceiling",
                                          truncated=True)
        return model_client.ModelResult(parsed={"summary": "digest"}, text="", model="m",
                                        backend="b", auth_mode="subscription", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    with pytest.raises(model_client.ModelError):
        asyncio.run(orchestrate._extract_sectioned(
            vault, "abc123", pf, "SKILL", plan, "sonnet", "annual-report"))

    assert calls["n"] == 2   # the whole-section-1 attempt + one half attempt — no further recursion
    assert not orchestrate._section_checkpoint_path(vault, "abc123", 1).exists()


def test_run_end_to_end_recovers_a_truncated_section_via_orchestrate_run(tmp_path, monkeypatch):
    """End-to-end through `orchestrate.run`: a document whose up-front plan already sections it
    (not the whole-doc-overrun fallback path) recovers from a section-level truncation and the
    document is extracted, not failed."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, text="a very long document ...")
    plan, _ = _resplit_plan_and_pf(vault)
    monkeypatch.setattr(orchestrate.section, "run", lambda v, s, **kw: plan)
    calls = []

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        calls.append(task)
        if task == "extract-section":
            n = sum(1 for t in calls if t == "extract-section")
            if n == 1:
                raise model_client.ModelError(
                    "output truncated at the model's max-token ceiling", truncated=True)
            outs = {2: _resplit_section_out("half1", first=True),
                   3: _resplit_section_out("half2"),
                   4: _resplit_section_out("sec2")}
            return model_client.ModelResult(parsed=outs[n], text="", model="m", backend="b",
                                            auth_mode="subscription", cost_usd=0.02)
        parsed = {
            "classify": {"skill": "general-records.md"},
            "digest": {"summary": "digest text"},
            "entity-synthesis": {"entity_syntheses": []},
            "timeline-dedup": {"groups": []},
            "briefing": {"investigation_status": "x", "what_was_ingested": []},
        }.get(task, {})
        return model_client.ModelResult(parsed=parsed, text="", model="m", backend="b",
                                        auth_mode="subscription", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    summary = asyncio.run(orchestrate.run(vault))
    assert summary["extracted"] == 1 and summary["failed"] == 0


# ── reasoning-starvation recovery (#558) ──────────────────────────────────────

def test_lower_effort_steps_down_one_level():
    assert orchestrate._lower_effort("high") == "medium"
    assert orchestrate._lower_effort("medium") == "low"


def test_lower_effort_returns_none_at_the_bottom_or_with_no_effort_knob():
    # "low" has nowhere lower to go; a model with no effort control at all (Haiku, DeepSeek)
    # always calls in with effort=None — neither should trigger a retry.
    assert orchestrate._lower_effort("low") is None
    assert orchestrate._lower_effort(None) is None


def test_starved_section_call_retries_at_lower_effort_and_document_succeeds(tmp_path, monkeypatch):
    """A starved (not truncated-by-density) `extract-section` call must not go through #540's
    re-split — re-splitting the input does nothing for a failure that isn't input-driven (#558).
    Instead the SAME section is retried once at the next effort level down, and the document
    still succeeds without ever being split in two."""
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    plan, pf = _resplit_plan_and_pf(vault)
    efforts_seen = []

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "extract-section":
            efforts_seen.append(effort)
            n = len(efforts_seen)
            if n == 1:
                raise model_client.ModelError(
                    "the model used its entire 96,000-token output budget on internal "
                    "reasoning and returned no answer — try a lower extractor_effort",
                    truncated=True, starved=True)
            outs = {2: _resplit_section_out("sec1", first=True), 3: _resplit_section_out("sec2")}
            return model_client.ModelResult(parsed=outs[n], text="", model="m", backend="b",
                                            auth_mode="subscription", cost_usd=0.02)
        return model_client.ModelResult(parsed={"summary": "digest"}, text="", model="m",
                                        backend="b", auth_mode="subscription", cost_usd=0.01)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    extraction, scratchpad, cost, ok, errors, warnings = asyncio.run(
        orchestrate._extract_sectioned(vault, "abc123", pf, "SKILL", plan, "sonnet",
                                       "annual-report", effort="high"))

    assert ok, errors
    facts = [f["fact"] for f in extraction["document"]["key_facts"]]
    assert facts == ["Fact sec1", "Fact sec2"]                # never split into halves
    # Section 1: failed at "high", retried and succeeded at "medium". Section 2 is a fresh loop
    # iteration and runs at the original "high" — only the retried section drops effort.
    assert efforts_seen == ["high", "medium", "high"]
    assert cost == pytest.approx(0.02 + 0.02 + 0.01)          # section-1 retry + section 2 + digest


def test_starved_section_call_at_lowest_effort_fails_like_no_recovery_existed(tmp_path, monkeypatch):
    """Bounded exactly like #540's re-split: with nowhere lower to retry (`effort="low"`), a
    starved section call fails the document outright rather than looping."""
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    plan, pf = _resplit_plan_and_pf(vault)
    calls = {"n": 0}

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "extract-section":
            calls["n"] += 1
            raise model_client.ModelError(
                "the model used its entire output budget on internal reasoning",
                truncated=True, starved=True)
        return model_client.ModelResult(parsed={"summary": "digest"}, text="", model="m",
                                        backend="b", auth_mode="subscription", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    with pytest.raises(model_client.ModelError, match="internal reasoning"):
        asyncio.run(orchestrate._extract_sectioned(
            vault, "abc123", pf, "SKILL", plan, "sonnet", "annual-report", effort="low"))

    assert calls["n"] == 1   # no retry attempted — "low" has nowhere lower to go


def test_starved_whole_doc_extraction_retries_at_lower_effort_and_succeeds(tmp_path, monkeypatch):
    """The whole-document path (`_simple_extract`) gets the same starvation recovery as sectioned
    extraction (#558) — falling back to sectioning would only repeat the starvation on smaller
    input, since the failure was never about input size."""
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    ext = _extraction()
    efforts_seen = []

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "extract":
            efforts_seen.append(effort)
            if len(efforts_seen) == 1:
                raise model_client.ModelError(
                    "the model used its entire output budget on internal reasoning",
                    truncated=True, starved=True)
            return model_client.ModelResult(parsed=ext, text="", model="m", backend="b",
                                            auth_mode="subscription", cost_usd=0.02)
        parsed = {"classify": {"skill": "general-records.md"},
                 "entity-synthesis": {"entity_syntheses": []},
                 "timeline-dedup": {"groups": []},
                 "briefing": {"investigation_status": "x", "what_was_ingested": []}}.get(task, {})
        return model_client.ModelResult(parsed=parsed, text="", model="m", backend="b",
                                        auth_mode="subscription", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    summary = asyncio.run(orchestrate.run(vault, extract_effort="high"))

    assert summary["extracted"] == 1 and summary["failed"] == 0
    assert efforts_seen == ["high", "medium"]


def test_ingest_retry_resumes_sectioned_extraction_after_a_crash(tmp_path, monkeypatch):
    """End-to-end: a document that crashes mid-sectioned-extraction fails the run but keeps its
    completed sections' checkpoints (the automatic-failure path preserves them, #498); moving the
    queue file back and re-running `orchestrate.run` resumes from section 2 instead of re-paying
    for section 1."""
    vault = make_vault(tmp_path)
    sha = "abc123"
    pages = [{"page": 1, "markdown": "Acme part one " * 50},
             {"page": 2, "markdown": "Acme part two " * 50}]
    qdir = vault / ".watchdog" / "queue"
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / f"{sha}.json").write_text(json.dumps({
        "sha256": sha, "filename": "test-doc.pdf", "source_path": "_INCOMING/test-doc.pdf",
        "page_count": 2, "pages": pages,
        "near_dup": {"near_duplicates": [], "top_similarity": 0.0},
    }))
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    monkeypatch.setattr(orchestrate.section, "_config_get", lambda k, d: d)

    sec1 = {
        "document": {"sha256": sha, "filename": "test-doc.pdf", "title": "Acme AR",
                     "document_type": "Annual Report", "summary": "Acme report.",
                     "key_facts": [{"fact": "x", "basis": "stated"}]},
        "entities": [{"id": "acme-corp", "name": "Acme Corp", "type": "Company",
                      "timeline_events": [], "roles": []}],
        "morgue_entity_id": "acme-corp", "morgue_document_type": "annual-report",
        "observations": "sec1",
    }
    sec2 = {"entities": [{"id": "acme-corp", "name": "Acme Corp", "type": "Company",
                         "timeline_events": [], "roles": []}], "observations": "sec2"}
    broken_whole_doc = _extraction(valid=False)
    broken_whole_doc["entities"] = []          # forces the whole-doc attempt to fail outright,
                                                # triggering the force-sectioned fallback (#343)
    section1_calls = {"n": 0}

    async def crashes_on_section2(*, task, prompt, schema, model=None, backend=None,
                                  max_retries=1, effort=None):
        if task == "classify":
            parsed = {"skill": "general-records.md"}
        elif task == "extract":
            parsed = broken_whole_doc
        elif task == "extract-section":
            if "This is SECTION 1" in _flat(prompt):
                section1_calls["n"] += 1
                parsed = sec1
            else:
                raise RuntimeError("simulated crash mid-section")
        else:
            raise AssertionError(f"unexpected task before crash: {task}")
        return model_client.ModelResult(parsed=parsed, text="", model="m",
                                        backend="b", auth_mode="subscription", cost_usd=0.01)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", crashes_on_section2)

    first = asyncio.run(orchestrate.run(vault))
    assert first["failed"] == 1 and first["extracted"] == 0
    assert section1_calls["n"] == 1
    assert (qdir / "_failed" / f"{sha}.json").exists()
    assert orchestrate._section_checkpoint_path(vault, sha, 1).exists()   # survives the failure

    (qdir / "_failed" / f"{sha}.json").replace(qdir / f"{sha}.json")      # re-queue, per abort.py

    async def succeeds(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "classify":
            parsed = {"skill": "general-records.md"}
        elif task == "extract":
            parsed = broken_whole_doc
        elif task == "extract-section":
            if "This is SECTION 1" in _flat(prompt):
                section1_calls["n"] += 1
                parsed = sec1
            else:
                parsed = sec2
        elif task == "digest":
            parsed = {"summary": "digest text"}
        elif task == "briefing":
            parsed = {"investigation_status": "x", "what_was_ingested": []}
        else:
            parsed = {"entity_syntheses": []} if task == "entity-synthesis" else {"events": []}
        return model_client.ModelResult(parsed=parsed, text="", model="m",
                                        backend="b", auth_mode="subscription", cost_usd=0.01)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", succeeds)

    second = asyncio.run(orchestrate.run(vault))
    assert second["extracted"] == 1 and second["failed"] == 0
    assert section1_calls["n"] == 1                # section 1 was never re-called on resume
    assert list((vault / ".watchdog" / "tmp").glob(f"section_ex_{sha}_*.json")) == []


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
    assert "watchdog dig --skill general-records" in capsys.readouterr().out


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
    assert "watchdog dig --skill" not in capsys.readouterr().out


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

    # #611: the same calls also land in the global telemetry store, tagged with this vault and
    # carrying the provenance fields the JSON usage file doesn't (prompt hash, codebase version,
    # config snapshot) — additive, not a replacement for the JSON file asserted on above.
    import sqlite3
    conn = sqlite3.connect(telemetry_db.DB_PATH)
    try:
        rows = conn.execute(
            "SELECT task, vault_path, prompt_hash, codebase_version, config_json FROM calls"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == n_calls
    db_tasks = [r[0] for r in rows]
    assert "classify" in db_tasks and "extract" in db_tasks and "briefing" in db_tasks
    for _task, vault_path, prompt_hash, codebase_version, config_json in rows:
        assert vault_path == str(vault.resolve())
        assert prompt_hash and len(prompt_hash) == 64   # sha256 hex digest
        assert codebase_version
        assert json.loads(config_json)["extract_model"] == "sonnet"


def test_usage_totals_carry_est_input_tokens_for_extraction_runs(tmp_path, monkeypatch):
    """#417: a run that actually extracts documents records the naive chars/4 estimate for
    those documents' pages onto its usage totals, both in the persisted file and the returned
    summary — the input a later `--estimate` calibrates against."""
    vault = make_vault(tmp_path)
    _queue_doc(vault)   # default text: "Acme Corp filed an annual report." → 8 est_tokens

    _mock(monkeypatch, extraction=_extraction())
    summary = asyncio.run(orchestrate.run(vault))
    assert summary["extracted"] == 1

    data = json.loads((vault / summary["usage_path"]).read_text())
    assert data["totals"]["est_input_tokens"] == 8
    assert summary["usage"]["est_input_tokens"] == 8


def test_usage_totals_omit_est_input_tokens_when_nothing_extracted(tmp_path, monkeypatch):
    """A run where every queued document fails post-flight extracted nothing — its usage file
    (if any calls were made at all) must not carry a fabricated zero calibration point."""
    vault = make_vault(tmp_path)
    _queue_doc(vault)

    broken = _extraction(valid=False)          # rejected by post-flight
    broken["entities"] = []                    # ...and no entity for #505's fallback to use
    _mock(monkeypatch, extraction=broken)
    summary = asyncio.run(orchestrate.run(vault))
    assert summary["extracted"] == 0

    if summary.get("usage_path"):
        data = json.loads((vault / summary["usage_path"]).read_text())
        assert "est_input_tokens" not in data["totals"]

    # #407: a clean run leaves no orphaned partial behind for the next run to trip over.
    usage_dir = vault / ".watchdog" / "registry" / "usage"
    assert list(usage_dir.glob("usage-*.partial.jsonl")) == []


def test_record_usage_appends_to_partial_file_immediately(tmp_path):
    """#407: `_record_usage` persists each call as it completes, not just at end-of-run — so a
    crash between calls still leaves the earlier ones on disk."""
    vault = tmp_path / "vault"
    vault.mkdir()
    orchestrate._begin_usage_run(vault)
    try:
        partial = orchestrate._usage_partial_path
        assert partial is not None and not partial.exists()   # not created until the first record

        orchestrate._record_usage("extract", model="m", backend="claude-api",
                                  usage={"input_tokens": 10, "output_tokens": 5}, cost_usd=0.001)

        lines = partial.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["task"] == "extract" and record["input_tokens"] == 10

        orchestrate._record_usage("briefing", model="m", backend="claude-api",
                                  usage={"input_tokens": 3, "output_tokens": 1}, cost_usd=0.0002)
        lines = partial.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
    finally:
        orchestrate._end_usage_run(vault)


def test_aborted_run_partial_is_consolidated_at_next_run_start(tmp_path):
    """#407: a run that never reaches its own `_end_usage_run` (simulated crash) leaves a
    `.partial.jsonl` with every call recorded so far; the next top-level run consolidates it
    into a real `usage-<ts>.json` before starting its own accumulation, so `watchdog usage`
    doesn't lose the aborted run's spend."""
    vault = tmp_path / "vault"
    vault.mkdir()
    usage_dir = vault / ".watchdog" / "registry" / "usage"

    orchestrate._begin_usage_run(vault)
    orphaned_partial = orchestrate._usage_partial_path
    orchestrate._record_usage("extract", model="m", backend="claude-api",
                              usage={"input_tokens": 42, "output_tokens": 7}, cost_usd=0.01)
    # Simulate a crash: neither `_end_usage_run` nor any cleanup runs, so the partial and the
    # module globals are left exactly as an aborted process would leave them.
    assert orphaned_partial.exists()
    orchestrate._usage = None
    orchestrate._usage_partial_path = None

    try:
        orchestrate._begin_usage_run(vault)   # the "next run"

        assert not orphaned_partial.exists()   # folded and removed
        consolidated = [p for p in usage_dir.glob("usage-*.json")]
        assert len(consolidated) == 1
        data = json.loads(consolidated[0].read_text(encoding="utf-8"))
        assert len(data["calls"]) == 1
        assert data["calls"][0]["input_tokens"] == 42

        assert orchestrate.latest_usage(vault)["input_tokens"] == 42
    finally:
        orchestrate._end_usage_run(vault)


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


def test_record_usage_reads_cache_read_tokens_from_openai_usage_shape():
    """#495: OpenAI nests its cache-hit count under `prompt_tokens_details.cached_tokens`
    rather than Anthropic's flat `cache_read_input_tokens` — before this fix, an OpenAI call's
    real cache hits (already billed at the discounted rate in `cost_usd`) were silently logged
    as `cache_read_tokens: 0`."""
    orchestrate._usage = []
    try:
        orchestrate._record_usage(
            "extract-section", model="gpt-5.4-nano", backend="openai",
            usage={"prompt_tokens": 20000, "completion_tokens": 5000,
                  "prompt_tokens_details": {"cached_tokens": 5900}},
            cost_usd=0.01, latency_s=1.0)
        assert orchestrate._usage[0]["cache_read_tokens"] == 5900
    finally:
        orchestrate._usage = None


def test_record_usage_reads_cache_read_tokens_from_deepseek_usage_shape():
    """#495: DeepSeek reports its cache-hit count as a flat `prompt_cache_hit_tokens` field —
    a third shape distinct from both Anthropic's and OpenAI's."""
    orchestrate._usage = []
    try:
        orchestrate._record_usage(
            "extract-section", model="deepseek-v4-flash", backend="deepseek",
            usage={"prompt_tokens": 20000, "completion_tokens": 5000,
                  "prompt_cache_hit_tokens": 3200},
            cost_usd=0.01, latency_s=1.0)
        assert orchestrate._usage[0]["cache_read_tokens"] == 3200
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


def test_record_usage_includes_rate_limit_when_present():
    """#563: the provider's own rate-limit headers ride onto the record so a run's usage file
    carries ground truth for what the provider counted, alongside the tokens we logged."""
    orchestrate._usage = []
    try:
        orchestrate._record_usage(
            "extract", model="gpt-5.4-mini", backend="openai",
            usage={"prompt_tokens": 100, "completion_tokens": 20}, cost_usd=0.01,
            rate_limit={"limit_tokens": 150000, "remaining_tokens": 149800, "reset_tokens": "6m0s"})
        assert orchestrate._usage[0]["rate_limit"] == {
            "limit_tokens": 150000, "remaining_tokens": 149800, "reset_tokens": "6m0s"}
    finally:
        orchestrate._usage = None


def test_record_usage_omits_rate_limit_key_when_absent():
    orchestrate._usage = []
    try:
        orchestrate._record_usage(
            "extract", model="claude-sonnet-4-6", backend="claude-api",
            usage={"input_tokens": 100, "output_tokens": 20}, cost_usd=0.01)
        assert "rate_limit" not in orchestrate._usage[0]
    finally:
        orchestrate._usage = None


def test_record_usage_includes_reasoning_tokens_from_openai_usage_shape():
    """#354: `completion_tokens_details.reasoning_tokens` had been arriving on every OpenAI
    reasoning-model call since D108 and thrown away — it's the field that diagnosed the
    reasoning-starvation failure, so it now rides onto the record."""
    orchestrate._usage = []
    try:
        orchestrate._record_usage(
            "extract-section", model="gpt-5.4-mini", backend="openai",
            usage={"prompt_tokens": 21058, "completion_tokens": 48000,
                  "completion_tokens_details": {"reasoning_tokens": 48000}},
            cost_usd=0.01, latency_s=1.0)
        assert orchestrate._usage[0]["reasoning_tokens"] == 48000
    finally:
        orchestrate._usage = None


def test_record_usage_omits_reasoning_tokens_key_for_anthropic_shaped_usage():
    """An Anthropic-shaped usage dict has no `completion_tokens_details` — the persisted record
    must not grow a `reasoning_tokens` key (even as null) for it, matching the `api_ms`/
    `num_turns` convention of only adding fields the provider actually reports."""
    orchestrate._usage = []
    try:
        orchestrate._record_usage(
            "extract", model="claude-sonnet-4-6", backend="claude-api",
            usage={"input_tokens": 100, "output_tokens": 20}, cost_usd=0.01)
        assert "reasoning_tokens" not in orchestrate._usage[0]
    finally:
        orchestrate._usage = None


def test_record_usage_omits_reasoning_tokens_key_when_provider_reports_zero():
    """OpenAI sends `completion_tokens_details.reasoning_tokens: 0` for its chat models. A 0
    says nothing the key's absence doesn't, so it must not land on the record — otherwise every
    gpt-4o row in `watchdog usage` grows a "· reasoning 0" note."""
    orchestrate._usage = []
    try:
        orchestrate._record_usage(
            "extract", model="gpt-4o", backend="openai",
            usage={"prompt_tokens": 100, "completion_tokens": 20,
                  "completion_tokens_details": {"reasoning_tokens": 0}},
            cost_usd=0.01)
        assert "reasoning_tokens" not in orchestrate._usage[0]
    finally:
        orchestrate._usage = None


def test_usage_totals_sums_reasoning_tokens_and_tolerates_missing_key():
    """`_usage_totals` sums `reasoning_tokens` across records, using a 0 default so older
    records and non-reasoning backends (which never carry the key at all) don't raise a
    KeyError."""
    records = [
        {"input_tokens": 100, "output_tokens": 20, "cache_read_tokens": 0,
         "cache_write_tokens": 0, "cost_usd": 0.01, "reasoning_tokens": 48000},
        {"input_tokens": 100, "output_tokens": 20, "cache_read_tokens": 0,
         "cache_write_tokens": 0, "cost_usd": 0.01},   # no reasoning_tokens key at all
    ]
    totals = orchestrate._usage_totals(records)
    assert totals["reasoning_tokens"] == 48000


def test_recent_token_rate_sums_only_calls_within_the_trailing_window():
    """#563: a rolling-window tokens/min figure — calls outside the window (here, one call 90s
    before the most recent) must not inflate the reported rate."""
    records = [
        {"input_tokens": 50_000, "output_tokens": 10_000, "end_ts": 1000.0},   # 90s before latest
        {"input_tokens": 20_000, "output_tokens": 5_000, "end_ts": 1080.0},    # 10s before latest
        {"input_tokens": 30_000, "output_tokens": 5_000, "end_ts": 1090.0},    # latest
    ]
    assert orchestrate._recent_token_rate(records, window_s=60.0) == (20_000 + 5_000 + 30_000 + 5_000)


def test_recent_token_rate_empty_list_is_zero():
    assert orchestrate._recent_token_rate([]) == 0


# ── admission control (#563) ────────────────────────────────────────────────────

def test_current_token_budget_override_wins_over_discovered():
    orchestrate._usage = [{"rate_limit": {"limit_tokens": 999}}]
    try:
        assert orchestrate._current_token_budget(500) == 500
    finally:
        orchestrate._usage = None


def test_current_token_budget_discovers_from_most_recent_record_with_rate_limit():
    orchestrate._usage = [
        {"rate_limit": {"limit_tokens": 100}},
        {"input_tokens": 10},               # no rate_limit key at all — must be skipped over
        {"rate_limit": {"limit_tokens": 200}},
    ]
    try:
        assert orchestrate._current_token_budget(None) == 200
    finally:
        orchestrate._usage = None


def test_current_token_budget_none_when_usage_is_none():
    orchestrate._usage = None
    assert orchestrate._current_token_budget(None) is None


def test_current_token_budget_none_when_usage_empty():
    orchestrate._usage = []
    try:
        assert orchestrate._current_token_budget(None) is None
    finally:
        orchestrate._usage = None


def test_current_token_budget_none_when_no_record_carries_rate_limit():
    orchestrate._usage = [{"input_tokens": 10}, {"input_tokens": 20}]
    try:
        assert orchestrate._current_token_budget(None) is None
    finally:
        orchestrate._usage = None


def test_admit_noop_when_budget_none():
    # Would hang forever if this actually polled — budget=None must return immediately.
    asyncio.run(orchestrate._admit("sha1", 1000, None, asyncio.Event()))


def test_admit_noop_when_estimate_alone_exceeds_margin():
    # 1000 >= 1000 * 0.85 — nothing to wait for; the reactive RateLimitError is the backstop.
    orchestrate._usage = []
    try:
        asyncio.run(orchestrate._admit("sha1", 1000, 1000, asyncio.Event()))
    finally:
        orchestrate._usage = None


def test_admit_returns_immediately_when_already_under_budget():
    orchestrate._usage = []
    try:
        asyncio.run(orchestrate._admit("sha1", 10, 1000, asyncio.Event()))
    finally:
        orchestrate._usage = None


def test_admit_counts_other_documents_reservations_not_just_own(monkeypatch):
    """The property `_admission_reserved` exists for: two documents dispatched in the same burst
    both see empty `_usage` on their first check (neither has completed a real call yet), so a
    check against `_usage` alone would admit an entire over-budget burst. `_admit` must also add
    in whatever *other* shas are already reserved."""
    monkeypatch.setattr(orchestrate, "_ADMISSION_MAX_WAIT_S", 0.05)
    monkeypatch.setattr(orchestrate, "_ADMISSION_POLL_INTERVAL_S", 0.01)
    orchestrate._usage = []
    orchestrate._admission_reserved["other-doc"] = 5000
    try:
        # budget=10000, margin=0.85 -> ceiling=8500. other's reservation (5000) + this doc's own
        # estimate (4000) = 9000 > 8500 — must not pass immediately; only the max-wait force-admit
        # lets it through, proving the other document's reservation was actually consulted.
        start = time.monotonic()
        asyncio.run(orchestrate._admit("this-doc", 4000, 10000, asyncio.Event()))
        assert time.monotonic() - start >= 0.05
    finally:
        orchestrate._usage = None
        orchestrate._admission_reserved.clear()


def test_admit_polls_until_the_rate_drops_below_budget(monkeypatch):
    monkeypatch.setattr(orchestrate, "_ADMISSION_POLL_INTERVAL_S", 0.01)
    calls = {"n": 0}

    def fake_rate(records):
        calls["n"] += 1
        return 900 if calls["n"] < 3 else 100   # over budget twice, then clears

    monkeypatch.setattr(orchestrate, "_recent_token_rate", fake_rate)
    orchestrate._usage = []
    try:
        asyncio.run(orchestrate._admit("sha1", 10, 1000, asyncio.Event()))
    finally:
        orchestrate._usage = None
    assert calls["n"] >= 3


def test_admit_force_admits_past_the_max_wait(monkeypatch, capsys):
    # #563: `_recent_token_rate`'s window only advances when a new usage record lands — if
    # every document were ever simultaneously waiting here, nothing would ever produce one, so
    # a permanently-over-budget rate must not stall forever.
    monkeypatch.setattr(orchestrate, "_ADMISSION_POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(orchestrate, "_ADMISSION_MAX_WAIT_S", 0.03)
    monkeypatch.setattr(orchestrate, "_recent_token_rate", lambda records: 999_999)
    orchestrate._usage = []
    try:
        asyncio.run(orchestrate._admit("sha1", 10, 1000, asyncio.Event()))
    finally:
        orchestrate._usage = None
    assert "Proceeding past the token budget" in capsys.readouterr().out


def test_admit_returns_promptly_when_cancelled(monkeypatch):
    monkeypatch.setattr(orchestrate, "_ADMISSION_POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(orchestrate, "_recent_token_rate", lambda records: 999_999)
    cancelled = asyncio.Event()
    orchestrate._usage = []

    async def _run():
        task = asyncio.ensure_future(orchestrate._admit("sha1", 10, 1000, cancelled))
        await asyncio.sleep(0.02)
        cancelled.set()
        await asyncio.wait_for(task, timeout=1.0)

    try:
        asyncio.run(_run())
    finally:
        orchestrate._usage = None


def test_admit_not_called_for_an_already_extracted_document(tmp_path, monkeypatch):
    """A free skip (already extracted, not --force) must never sit in `_admit`'s wait for
    budget it doesn't need."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="sha1", filename="a.pdf")
    real_preflight_run = orchestrate.preflight.run

    def fake_preflight(v, s):
        if s == "sha1":
            return {"already_extracted": True, "filename": "a.pdf", "pages": []}
        return real_preflight_run(v, s)
    monkeypatch.setattr(orchestrate.preflight, "run", fake_preflight)

    admit_calls = []

    async def spy_admit(sha, est_tokens, budget, cancelled):
        admit_calls.append(sha)
    monkeypatch.setattr(orchestrate, "_admit", spy_admit)
    _mock(monkeypatch, extraction=_extraction())

    summary = asyncio.run(orchestrate.run(vault, concurrency=1))
    assert admit_calls == []
    assert summary["skipped"] == 1


def test_admission_budget_from_config_reaches_admit(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="sha1", filename="a.pdf")
    _mock(monkeypatch, extraction=_extraction())

    admit_calls = []

    async def spy_admit(sha, est_tokens, budget, cancelled):
        admit_calls.append((sha, budget))
    monkeypatch.setattr(orchestrate, "_admit", spy_admit)

    asyncio.run(orchestrate.run(vault, concurrency=1, extract_token_budget=12345))
    assert admit_calls == [("sha1", 12345)]


def test_admission_budget_none_by_default(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="sha1", filename="a.pdf")
    _mock(monkeypatch, extraction=_extraction())

    admit_calls = []

    async def spy_admit(sha, est_tokens, budget, cancelled):
        admit_calls.append((sha, budget))
    monkeypatch.setattr(orchestrate, "_admit", spy_admit)

    asyncio.run(orchestrate.run(vault, concurrency=1))
    assert admit_calls == [("sha1", None)]


def test_admission_reserves_in_flight_documents_not_just_recorded_usage(tmp_path, monkeypatch, capsys):
    """Every queued document's dispatch fires at once in `run()`, and asyncio runs each one's
    synchronous prefix — including its first `_admit` check — back to back, all before any of
    them has completed a real model call. A budget check against `_usage` alone would therefore
    see every concurrently-dispatched document as if it were the only one running, letting an
    entire over-budget burst straight through. `_admission_reserved` is what makes a *second*
    document in the same burst see the *first* one's estimated cost the moment its own turn
    comes, forcing it to wait rather than passing for free."""
    monkeypatch.setattr(orchestrate, "_ADMISSION_POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(orchestrate, "_ADMISSION_MAX_WAIT_S", 0.5)
    # `_candidates_checklist` runs local NER (GLiNER) on a real background thread
    # (`asyncio.to_thread`) — genuine, variable-duration work between classify and extract that
    # would otherwise make this test's timing nondeterministic. Stubbed to keep the only real
    # `await` in the whole flow the one this test controls (extract's `asyncio.sleep` below).
    monkeypatch.setattr(orchestrate, "_candidates_checklist", lambda text, **kw: "")
    vault = make_vault(tmp_path)
    # 8,500 est tokens each (chars/4) — individually under a 16,000*0.85=13,600 margin, but two
    # together (17,000) clearly exceed it, so the second must wait on the first if — and only
    # if — in-flight reservations are consulted, not just completed usage.
    text = "Acme Corp filed an annual report. " * 1000
    _queue_doc(vault, sha="doc0", filename="doc0.pdf", text=text)
    _queue_doc(vault, sha="doc1", filename="doc1.pdf", text=text)

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "classify":
            return model_client.ModelResult(parsed={"skill": "general-records.md"}, text="",
                                            model="m", backend="openai", auth_mode="api-key")
        if task == "extract":
            await asyncio.sleep(0.05)   # a real gap between admission and this doc's usage landing
            return model_client.ModelResult(
                parsed=_extraction(sha="doc0"), text="", model="m", backend="openai",
                auth_mode="api-key", usage={"input_tokens": 8200, "output_tokens": 300},
                rate_limit={"limit_tokens": 16000, "remaining_tokens": 5000, "reset_tokens": "10s"})
        return model_client.ModelResult(parsed={}, text="", model="m", backend="openai", auth_mode="api-key")
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    summary = asyncio.run(orchestrate.run(vault, concurrency=2, extract_token_budget=16000,
                                          skip_finalize=True))
    assert "Holding new documents" in capsys.readouterr().out
    assert summary["extracted"] == 2


def test_admission_reserved_cleared_after_a_document_finishes(tmp_path, monkeypatch):
    """A finished document's reservation must not linger and needlessly block a later one."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="sha1", filename="a.pdf")
    _mock(monkeypatch, extraction=_extraction())
    orchestrate._admission_reserved.clear()
    asyncio.run(orchestrate.run(vault, concurrency=1))
    assert orchestrate._admission_reserved == {}


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


def test_rate_limit_resume_message_uses_resume_hint(tmp_path, monkeypatch, capsys):
    """The extraction rate-limit stop notice names `run`'s `resume_hint` — the surface that
    launched the run — not a hardcoded `watchdog dig`, so a guided bare-`watchdog` walk points
    back at `watchdog`, not `dig` (#441, D138)."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="aaa111", filename="one.pdf")

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "classify":
            return model_client.ModelResult(parsed={"skill": "general-records.md"}, text="",
                                            model="m", backend="claude-agent-sdk", auth_mode="subscription")
        raise model_client.RateLimitError("session limit")
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    asyncio.run(orchestrate.run(vault, concurrency=1, resume_hint="watchdog"))
    out = capsys.readouterr().out
    assert "Re-run" in out
    assert "once it resets to continue" in out
    assert "watchdog dig" not in out


def test_rate_limit_stop_reports_observed_rate_and_provider_headers(tmp_path, monkeypatch, capsys):
    """#563: the stop message grounds the "lower extract_concurrency" advice in real numbers —
    this run's own observed tokens/min, plus the provider's last-seen remaining/limit off the
    429 itself — rather than leaving the advice a guess."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="aaa111", filename="one.pdf")

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "classify":
            return model_client.ModelResult(parsed={"skill": "general-records.md"}, text="",
                                            model="m", backend="claude-agent-sdk",
                                            auth_mode="subscription",
                                            usage={"input_tokens": 500, "output_tokens": 50})
        raise model_client.RateLimitError(
            "session limit",
            rate_limit={"limit_tokens": 150_000, "remaining_tokens": 200, "reset_tokens": "6m0s"})
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    asyncio.run(orchestrate.run(vault, concurrency=1))
    out = capsys.readouterr().out
    assert "550 tokens/min observed" in out
    assert "200/150,000 tokens remaining" in out
    assert "extract_concurrency" in out


def test_rate_limit_stop_omits_provider_line_when_backend_sends_no_headers(tmp_path, monkeypatch, capsys):
    """claude-agent-sdk's RateLimitError carries no `rate_limit` (its session-limit detection
    reads a CLI transcript, not HTTP headers) — the stop message must not crash or print a
    bogus "None/None tokens remaining" line for it."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="aaa111", filename="one.pdf")

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "classify":
            return model_client.ModelResult(parsed={"skill": "general-records.md"}, text="",
                                            model="m", backend="claude-agent-sdk",
                                            auth_mode="subscription")
        raise model_client.RateLimitError("session limit")
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    asyncio.run(orchestrate.run(vault, concurrency=1))
    out = capsys.readouterr().out
    assert "tokens remaining" not in out


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
    """A rate limit (or model error) must not crash: the run returns a summary cleanly. Both docs
    share an entity, so pre-commit reconciliation (#403 phase 3) is the first post-extraction model
    call and the one that hits the rate limit here — which defers the commit itself (see
    `test_reconcile_failure_leaves_batch_finalizable`), so synthesis never runs either."""
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


def test_post_ingest_skip_briefing_makes_no_briefing_call(tmp_path, monkeypatch):
    """`skip_briefing=True` (#410) never calls the model for the briefing task — synthesis and
    the timeline-dedup calls still happen — and records the skip as `briefing_skipped` rather
    than `briefing_error`, so the caller doesn't treat it as a failure."""
    vault = make_vault(tmp_path)
    results = [orchestrate._compact_result(
        "sha1", "doc.pdf",
        {"document": {"key_facts": [{"fact": "a fact"}]}, "entities": []},
        {}, 0.01, {})]

    calls = []

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        calls.append(task)
        if task == "briefing":
            raise AssertionError("briefing must not be called when skip_briefing=True")
        parsed = {"groups": []} if task == "timeline-dedup" else {}
        return model_client.ModelResult(parsed=parsed, text="", model="m",
                                        backend="claude-agent-sdk", auth_mode="subscription", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    out = asyncio.run(orchestrate._post_ingest(vault, results, None, "haiku", skip_briefing=True))

    assert "briefing" not in calls
    assert out.get("briefing") is None
    assert out.get("briefing_error") is None
    assert out.get("briefing_skipped") is True
    assert not (vault / "briefings").exists()   # no briefing file written
    assert not (vault / "hot.md").exists()      # hot.md is only written by _write_briefing


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
    # The staged extraction artifact (#403 phase 1) is what a later finalize's synthesis reads
    # from (#403 phase 4) — it is written at extraction time, before finalize ever runs.
    assert (vault / ".watchdog" / "extracted" / "aaa.json").exists()


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
    assert not list((vault / ".watchdog" / "tmp").glob("result_*.json"))
    assert orchestrate.has_pending_finalization(vault) is False


def test_pending_finalization_uses_registry_appears_in_gate(tmp_path):
    """Entity count reflects the registry's `appears_in >= 2` gate (D26), read over the entities
    mentioned in this batch's staged extractions (#403 phase 4) — not a fragment-queue count."""
    vault = make_vault(tmp_path)
    tmp = vault / ".watchdog" / "tmp"
    _stage_extracted(vault, tmp_path / "a", "sha-a", "doc-a.pdf", overrides={
        "entities": [
            {"id": "acme-corp", "name": "Acme Corp", "type": "Company", "aliases": [],
             "summary": None, "timeline_events": [], "roles": []},
            {"id": "beta-llc", "name": "Beta LLC", "type": "Company", "aliases": [],
             "summary": None, "timeline_events": [], "roles": []},
        ],
        "morgue_entity_id": "acme-corp",
        "morgue_document_type": "filing",
    })
    (vault / ".watchdog" / "registry" / "entities.json").write_text(json.dumps({
        "acme-corp": {"appears_in": ["doc1", "doc2"]},   # recurs project-wide → eligible
        "beta-llc": {"appears_in": ["doc1"]},            # single-document → not eligible
    }))
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "result_sha-a.json").write_text("{}")

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
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "result_old.json").write_text("{}")
    (tmp / "notes_old.md").write_text("obs")
    lock = vault / ".watchdog" / "registry" / ".ingest-lock"

    # merge: inputs preserved so this run finalizes together with the pending batch
    ingest_setup.run(vault, wipe_pending=False)
    assert (tmp / "result_old.json").exists() and (tmp / "notes_old.md").exists()

    # default: inputs wiped for a fresh batch
    lock.unlink(missing_ok=True)                               # release the lock from the prior call
    ingest_setup.run(vault, wipe_pending=True)
    assert not (tmp / "result_old.json").exists() and not (tmp / "notes_old.md").exists()


def test_ingest_setup_discard_snapshots_before_wiping(tmp_path):
    """#270: the discard choice (wipe_pending=True with leftover residue from a prior
    unfinalized batch) is irreversible — back up result_*.json and notes_*.md before
    deleting them."""
    from watchdog.pipeline import ingest_setup
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="new1", filename="new.pdf")
    tmp = vault / ".watchdog" / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "result_old.json").write_text('{"old": true}')
    (tmp / "notes_old.md").write_text("scratchpad notes")

    state = ingest_setup.run(vault, wipe_pending=True)

    assert state["backup_dir"] is not None
    backup_dir = Path(state["backup_dir"])
    assert (backup_dir / ".watchdog" / "tmp" / "result_old.json").read_text() == '{"old": true}'
    assert (backup_dir / ".watchdog" / "tmp" / "notes_old.md").read_text() == "scratchpad notes"
    # And the originals are still gone — the backup doesn't block the wipe.
    assert not (tmp / "result_old.json").exists()


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

    extraction, scratchpad, cost, ok, errors, warnings = asyncio.run(orchestrate._extract_sectioned(
        vault, "abc123", pf, "SKILL TEXT", plan, "sonnet", "annual-report", backend="claude-api",
        brief="CHASE THE FRAUD"))

    digest_calls = [c for c in calls if c["task"] == "digest"]
    assert len(digest_calls) == 1
    assert digest_calls[0]["schema"] is schemas.DIGEST
    assert digest_calls[0]["model"] == "sonnet"        # extractor tier, not finalizer
    assert digest_calls[0]["backend"] == "claude-api"  # same backend the sections used
    # Extractor-tier context parity (#279): the digest prompt carries the skill + brief.
    digest_text = model_client._flatten_prompt(digest_calls[0]["prompt"])
    assert "SKILL TEXT" in digest_text
    assert "CHASE THE FRAUD" in digest_text
    assert "test-doc.pdf" in digest_text
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

    extraction, scratchpad, cost, ok, errors, warnings = asyncio.run(orchestrate._extract_sectioned(
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

    extraction, scratchpad, cost, ok, errors, warnings = asyncio.run(orchestrate._extract_sectioned(
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

def test_run_batch_needs_no_pinned_skill(tmp_path, monkeypatch):
    """D144: an unpinned batch is legal — `_run_batch` used to raise before doing anything.
    With no shas queued it should now fall straight through to a clean empty submission."""
    vault = make_vault(tmp_path)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    out = asyncio.run(orchestrate._run_batch(vault, [], None, "sonnet", None, None, 5,
                                             "haiku", 5, None))
    assert out == {"results": [], "batch_pending": False}


def test_batch_skill_prefers_per_sha_map_then_legacy_then_run_pin(tmp_path):
    """`_batch_skill` resolves a collected document's skill from the per-sha map (D144), falling
    back to the pre-D144 single `skill_label` so a batch submitted by an older version is still
    collectable after an upgrade — a batch can sit in flight for up to 24h."""
    skill = tmp_path / "pinned.md"
    skill.write_text("RUN-PIN SKILL", encoding="utf-8")

    # per-sha map wins
    state = {"skills": {"sha1": "bankruptcy"}, "skill_label": "financial-statements"}
    assert orchestrate._batch_skill(state, "sha1", str(skill))[1] == "bankruptcy"
    # legacy single label, for a sha the map doesn't name
    assert orchestrate._batch_skill(state, "sha2", str(skill))[1] == "financial-statements"
    # neither: fall back to this run's own pin
    assert orchestrate._batch_skill({}, "sha3", str(skill)) == ("RUN-PIN SKILL", "pinned")
    # a label that no longer resolves to a skill file (a pre-D144 path stem, or a user-local
    # skill deleted while the batch was in flight) also falls back rather than failing a batch
    # that has already been paid for
    assert orchestrate._batch_skill({"skill_label": "no-such-skill"}, "sha5", str(skill))[1] == "pinned"
    # nothing usable at all is a clear error, not a silently wrong skill
    with pytest.raises(model_client.ModelError, match="no usable skill"):
        orchestrate._batch_skill({}, "sha4", None)


def test_submit_batch_resolves_skill_per_document(tmp_path, monkeypatch):
    """A mixed-type batch: each document's own sidecar `skill:` pin is honoured (D120/D144),
    so one submission carries two different skills and the state records which is which."""
    vault = make_vault(tmp_path)
    for sha, skill in (("sha1", "bankruptcy"), ("sha2", "financial-statements")):
        _queue_doc(vault, sha, sidecar=f"skill: {skill}")

    captured = {}

    async def _fake_submit(vault, docs, *, model, effort, skills, api_key, backend=None):
        captured["docs"] = docs
        captured["skills"] = skills
        return "batch_x"

    monkeypatch.setattr(orchestrate.batch_extract, "submit", _fake_submit)
    monkeypatch.setattr(orchestrate.skills_catalog, "read_skill", lambda n: f"TEXT {n}")

    out = asyncio.run(orchestrate._submit_batch(
        vault, ["sha1", "sha2"], None, "sonnet", None, None, 5, "haiku", 5, None, "sk-x"))

    assert out["batch_pending"] is True
    assert captured["skills"] == {"sha1": "bankruptcy", "sha2": "financial-statements"}
    # Sorted by skill so adjacent same-skill requests share the cached prefix.
    assert [d["skill_label"] for d in captured["docs"]] == ["bankruptcy", "financial-statements"]


def test_run_batch_requires_api_key_auth(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "subscription"})
    with pytest.raises(model_client.ModelError, match="api-key auth"):
        asyncio.run(orchestrate._run_batch(vault, [], None, "sonnet", "/tmp/skill.md", None, 5,
                                           "haiku", 5, None))


# ── openai-batch (#530) ───────────────────────────────────────────────────────

def test_run_batch_openai_uses_stored_openai_key_not_claude_auth(tmp_path, monkeypatch):
    """openai-batch has no subscription/api-key split to check (OpenAI has no subscription mode
    in this codebase at all) — it just needs its own stored key, the same resolution a live
    `openai` call uses."""
    vault = make_vault(tmp_path)

    def _unexpected_resolve_auth():
        raise AssertionError("openai-batch must not consult Claude's own auth mode")
    monkeypatch.setattr(auth_module, "resolve_auth", _unexpected_resolve_auth)
    monkeypatch.setattr(auth_module, "get_api_key", lambda provider: "sk-oai" if provider == "openai" else None)

    out = asyncio.run(orchestrate._run_batch(vault, [], None, "gpt-5.6-luna", None, None, 5,
                                             "haiku", 5, None, backend="openai-batch"))
    assert out == {"results": [], "batch_pending": False}


def test_run_batch_openai_requires_an_api_key(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    monkeypatch.setattr(auth_module, "get_api_key", lambda provider: None)
    with pytest.raises(model_client.ModelError, match="openai-batch backend needs an API key"):
        asyncio.run(orchestrate._run_batch(vault, [], None, "gpt-5.6-luna", None, None, 5,
                                           "haiku", 5, None, backend="openai-batch"))


def test_submit_batch_openai_sections_via_openai_not_claude_api(tmp_path, monkeypatch):
    """A sectioned document under an openai-batch run must fall back to the `openai` single-call
    backend, not `claude-api` — a vault routed entirely to OpenAI must never need Claude
    credentials for this to work (D37)."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="big", filename="big.pdf", text="a very long document ...")
    monkeypatch.setattr(orchestrate.section, "run", lambda v, s, **kw: {"sectioned": True})

    sectioned_calls = []
    async def fake_extract_document(vault, sha, brief, extract_model, classify_model,
                                    classify_pages, pinned_skill, extract_effort,
                                    extract_backend, classify_backend, force=False):
        sectioned_calls.append(extract_backend)
        return {"sha256": sha, "filename": "big.pdf", "status": "ok", "record_skill": "s"}
    monkeypatch.setattr(orchestrate, "_extract_document", fake_extract_document)

    out = asyncio.run(orchestrate._submit_batch(
        vault, ["big"], None, "gpt-5.6-luna", None, None, 5, "haiku", 5, None,
        api_key="sk-oai", backend="openai-batch"))

    assert sectioned_calls == ["openai"]
    assert out["batch_pending"] is False   # nothing left to submit — the only doc was sectioned


def test_finish_batch_item_repairs_invalid_result_via_openai_with_the_batch_model(tmp_path, monkeypatch):
    """The repair call for an openai-batch item must stay on the `openai` backend with the
    batch's own model — `backend=None` (claude-batch's repair default) would resolve to Claude's
    default tier, which is meaningless routed through the `openai` backend."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="sha1", filename="a.pdf")

    seen = []
    async def fake_acomplete(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        seen.append((backend, model))
        return model_client.ModelResult(parsed=_extraction(sha="sha1", filename="a.pdf"),
                                        text="", model="gpt-5.6-luna", backend="openai",
                                        auth_mode="api-key", cost_usd=0.03)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake_acomplete)

    item = {"ok": False, "parsed": None, "usage": None, "cost_usd": None,
           "error": "batch response was not valid JSON"}
    result = asyncio.run(orchestrate._finish_batch_item(
        vault, "sha1", item, "SKILL BODY", "annual-report", None, "sk-oai",
        model="gpt-5.6-luna", backend="openai-batch"))

    assert result["status"] == "ok"
    assert seen == [("openai", "gpt-5.6-luna")]


def test_resume_batch_threads_backend_to_status_and_collect(tmp_path, monkeypatch):
    """A state file persisted by an openai-batch submit must resume against the OpenAI path,
    not silently default back to Anthropic."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="sha1", filename="a.pdf")
    state = {"batch_id": "b1", "shas": ["sha1"], "model": "gpt-5.6-luna",
            "skill_label": "financial-statements", "effort": None, "backend": "openai-batch"}
    batch_extract.write_state(vault, state)

    seen_backends = []

    async def fake_status(batch_id, api_key, backend=None):
        seen_backends.append(("status", backend))
        return {"processing_status": "ended", "request_counts": {"succeeded": 1}}
    monkeypatch.setattr(orchestrate.batch_extract, "status", fake_status)

    async def fake_collect(batch_id, api_key, model_id, backend=None):
        seen_backends.append(("collect", backend))
        return {"sha1": {"ok": True, "parsed": _extraction(sha="sha1", filename="a.pdf"),
                         "usage": {}, "cost_usd": 0.02, "error": None}}
    monkeypatch.setattr(orchestrate.batch_extract, "collect", fake_collect)

    skill_file = tmp_path / "pinned.md"
    skill_file.write_text("SKILL")
    out = asyncio.run(orchestrate._resume_batch(vault, state, str(skill_file), None, "sk-oai"))

    assert out["batch_pending"] is False
    assert seen_backends == [("status", "openai-batch"), ("collect", "openai-batch")]


def test_resume_batch_defaults_missing_backend_to_claude_batch(tmp_path, monkeypatch):
    """State persisted before #530 (no `backend` field at all) must still resume against
    Anthropic — an in-flight batch across an upgrade shouldn't strand."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="sha1", filename="a.pdf")
    state = {"batch_id": "b1", "shas": ["sha1"], "model": "claude-sonnet-4-6",
            "skill_label": "financial-statements", "effort": None}   # no "backend" key
    batch_extract.write_state(vault, state)

    seen_backends = []

    async def fake_status(batch_id, api_key, backend=None):
        seen_backends.append(backend)
        return {"processing_status": "in_progress", "request_counts": {}}
    monkeypatch.setattr(orchestrate.batch_extract, "status", fake_status)

    skill_file = tmp_path / "pinned.md"
    skill_file.write_text("SKILL")
    asyncio.run(orchestrate._resume_batch(vault, state, str(skill_file), None, "sk-x"))

    assert seen_backends == ["claude-batch"]


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
                                    extract_backend, classify_backend, force=False):
        sectioned_calls.append({"sha": sha, "extract_backend": extract_backend})
        return {"sha256": sha, "filename": f"{sha}.pdf", "status": "ok", "record_skill": "s"}
    monkeypatch.setattr(orchestrate, "_extract_document", fake_extract_document)

    submitted = {}
    async def fake_submit(vault, docs, *, model, effort, skills, api_key, backend=None):
        submitted["docs"] = docs
        submitted["skills"] = skills
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

    async def fake_status(batch_id, api_key, backend=None):
        return {"processing_status": "in_progress", "request_counts": {"processing": 1, "succeeded": 1}}
    monkeypatch.setattr(orchestrate.batch_extract, "status", fake_status)

    out = asyncio.run(orchestrate._resume_batch(vault, state, "/tmp/x.md", None, "sk-x"))
    assert out == {"results": [], "batch_pending": True}
    assert "still processing" in capsys.readouterr().out


def test_resume_batch_collects_and_clears_state_when_ended(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="sha1", filename="a.pdf")
    # Pre-D144 state shape (one run-wide `skill_label`, no per-sha map) — still collectable.
    state = {"batch_id": "b1", "shas": ["sha1"], "model": "claude-sonnet-4-6",
            "skill_label": "financial-statements", "effort": None}
    batch_extract.write_state(vault, state)

    async def fake_status(batch_id, api_key, backend=None):
        return {"processing_status": "ended", "request_counts": {"succeeded": 1}}
    monkeypatch.setattr(orchestrate.batch_extract, "status", fake_status)

    async def fake_collect(batch_id, api_key, model_id, backend=None):
        return {"sha1": {"ok": True, "parsed": _extraction(sha="sha1", filename="a.pdf"),
                         "usage": {}, "cost_usd": 0.02, "error": None}}
    monkeypatch.setattr(orchestrate.batch_extract, "collect", fake_collect)

    skill_file = tmp_path / "pinned.md"
    skill_file.write_text("SKILL")

    out = asyncio.run(orchestrate._resume_batch(vault, state, str(skill_file), None, "sk-x"))
    assert out["batch_pending"] is False
    assert out["results"][0]["status"] == "ok"
    assert batch_extract.read_state(vault) is None   # state cleared on a clean collection


def test_fmt_span_formats_seconds_minutes_and_hours():
    fmt = orchestrate._BATCH_TS_FMT
    t0 = "2026-07-29T02:54:46Z"
    assert orchestrate._fmt_span(t0, "2026-07-29T02:54:51Z") == "5s"
    assert orchestrate._fmt_span(t0, "2026-07-29T03:36:02Z") == "41m16s"
    assert orchestrate._fmt_span(t0, "2026-07-29T04:55:02Z") == "2h00m16s"
    assert fmt == "%Y-%m-%dT%H:%M:%SZ"   # sanity: the format both sides actually use


def test_fmt_span_is_none_when_either_timestamp_is_missing():
    assert orchestrate._fmt_span(None, "2026-07-29T02:54:51Z") is None
    assert orchestrate._fmt_span("2026-07-29T02:54:46Z", None) is None


def test_resume_batch_logs_lifecycle_line_to_ingest_log(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="sha1", filename="a.pdf")
    state = {"batch_id": "b1", "shas": ["sha1"], "model": "claude-sonnet-4-6",
            "skill_label": "financial-statements", "effort": None,
            "submitted_at": "2026-07-29T02:54:46Z"}
    batch_extract.write_state(vault, state)

    async def fake_status(batch_id, api_key, backend=None):
        return {"processing_status": "ended", "request_counts": {"succeeded": 1, "errored": 0},
               "ended_at": "2026-07-29T03:36:02Z"}
    monkeypatch.setattr(orchestrate.batch_extract, "status", fake_status)

    async def fake_collect(batch_id, api_key, model_id, backend=None):
        return {"sha1": {"ok": True, "parsed": _extraction(sha="sha1", filename="a.pdf"),
                         "usage": {}, "cost_usd": 0.02, "error": None}}
    monkeypatch.setattr(orchestrate.batch_extract, "collect", fake_collect)

    skill_file = tmp_path / "pinned.md"
    skill_file.write_text("SKILL")
    asyncio.run(orchestrate._resume_batch(vault, state, str(skill_file), None, "sk-x"))

    log = (vault / ".watchdog" / "registry" / "ingest.log").read_text(encoding="utf-8")
    assert "BATCH b1" in log
    assert "submitted 2026-07-29T02:54:46Z" in log
    assert "ended 2026-07-29T03:36:02Z (processed 41m16s)" in log
    assert "collected" in log
    assert "1 succeeded" in log


def test_finish_batch_item_records_batch_lifecycle_when_batch_meta_given(tmp_path):
    """`_resume_batch` threads `batch_meta` through so a batch item's usage row carries its own
    submitted/ended/collected lifecycle — otherwise that only ever lived in the transient
    `batch-pending.json` state, deleted the moment collection succeeds."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="sha1", filename="a.pdf")
    item = {"ok": True, "parsed": _extraction(sha="sha1", filename="a.pdf"),
           "usage": {"input_tokens": 500, "output_tokens": 80}, "cost_usd": 0.015, "error": None}
    batch_meta = {"batch_id": "b1", "submitted_at": "2026-07-29T02:54:46Z",
                 "ended_at": "2026-07-29T03:36:02Z", "collected_at": "2026-07-29T04:10:00Z"}

    orchestrate._usage = []
    try:
        asyncio.run(orchestrate._finish_batch_item(
            vault, "sha1", item, "SKILL BODY", "annual-report", None, "sk-x",
            model="claude-sonnet-4-6", batch_meta=batch_meta))
        calls = [c for c in orchestrate._usage if c["task"] == "extract"]
        assert calls[0]["batch_id"] == "b1"
        assert calls[0]["batch_submitted_at"] == "2026-07-29T02:54:46Z"
        assert calls[0]["batch_ended_at"] == "2026-07-29T03:36:02Z"
        assert calls[0]["batch_collected_at"] == "2026-07-29T04:10:00Z"
    finally:
        orchestrate._usage = None


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
            # #606 Part B: the naive chars/4 estimate for _queue_doc's default text ("Acme Corp
            # filed an annual report." — 34 chars // 4 = 8), now recorded per call so a
            # model-scoped tokenizer calibration can compare estimate to actual.
            "est_input_tokens": 8,
        }
    finally:
        orchestrate._usage = None


def test_finish_batch_item_stamps_extraction_provenance(tmp_path):
    """The claude-batch path (#214) has its own extraction call sequence — separate from
    _simple_extract/_extract_sectioned — so it needs its own coverage that record_skill_hash/
    extract_model/extract_effort (#268) reach the staged artifact from here too (#403 phase 1:
    _finish_batch_item only stages now; write_vault, and so documents.json, comes from the
    commit pass, exercised here via _commit_extracted directly)."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="sha1", filename="a.pdf")

    item = {"ok": True, "parsed": _extraction(sha="sha1", filename="a.pdf"),
           "usage": None, "cost_usd": 0.015, "error": None}
    result = asyncio.run(orchestrate._finish_batch_item(
        vault, "sha1", item, "SKILL BODY", "annual-report", None, "sk-x",
        model="claude-sonnet-4-6", effort="medium"))

    assert result["status"] == "ok"
    staged = json.loads((vault / ".watchdog" / "extracted" / "sha1.json").read_text())
    doc = staged["document"]
    assert doc["record_skill_hash"] == hashlib.sha256(b"SKILL BODY").hexdigest()[:12]
    assert doc["extract_model"] == "claude-sonnet-4-6"
    assert doc["extract_effort"] == "medium"

    orchestrate._commit_extracted(vault, "sha1")
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

    async def fake_status(batch_id, api_key, backend=None):
        return {"processing_status": "in_progress", "request_counts": {}}
    monkeypatch.setattr(orchestrate.batch_extract, "status", fake_status)

    summary = asyncio.run(orchestrate.run(vault, extract_backend="claude-batch",
                                          pinned_skill=str(skill_file)))
    assert summary["batch_pending"] is True
    assert not submit_calls   # resumed the pending batch instead of submitting a new one


# ── #403 phase 1: staged extraction + commit pass ────────────────────────────────────────────

def test_already_staged_document_skips_extraction_with_no_model_calls(tmp_path, monkeypatch):
    """Sha-only extraction idempotence (#403 phase 1): once `.watchdog/extracted/<sha>.json`
    exists, re-running extraction for that sha must not call the model again — independent of
    whether the document has been committed to the vault yet (`--no-finalize` deliberately leaves
    it uncommitted here)."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="abc123", filename="test-doc.pdf")
    _mock(monkeypatch, extraction=_extraction())

    asyncio.run(orchestrate.run(vault, skip_finalize=True))
    assert (vault / ".watchdog" / "extracted" / "abc123.json").exists()
    assert not json.loads((vault / ".watchdog" / "registry" / "documents.json").read_text())
    queue_file = vault / ".watchdog" / "queue" / "abc123.json"
    assert queue_file.exists()   # not yet committed — still needed

    def fail_if_called(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        raise AssertionError(f"model must not be called for an already-staged document ({task})")
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fail_if_called)

    result = asyncio.run(orchestrate._extract_document(vault, "abc123", None, "sonnet", "haiku"))

    assert result["status"] == "skipped"
    assert queue_file.exists()   # still not touched — the commit pass owns its removal


def test_commit_pass_writes_vault_from_staged_artifact_when_registry_missing_entry(tmp_path, monkeypatch):
    """The resume case (#403 phase 1): an extraction artifact on disk with no matching
    `registry/documents.json` entry gets committed the next time finalize runs — covering a
    resumed run after a rate-limit stop or a standalone `watchdog finalize`, not just the tail of
    the same `watchdog ingest` invocation that produced it."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="abc123", filename="test-doc.pdf")
    _mock(monkeypatch, extraction=_extraction())

    asyncio.run(orchestrate.run(vault, skip_finalize=True))
    assert not json.loads((vault / ".watchdog" / "registry" / "documents.json").read_text())
    assert not (vault / "entities" / "organization" / "acme-corp.md").exists()

    out = asyncio.run(orchestrate.finalize(vault, post_model="haiku"))

    assert not out.get("error") and not out.get("briefing_error")
    docs = json.loads((vault / ".watchdog" / "registry" / "documents.json").read_text())
    assert "abc123" in docs
    assert (vault / "entities" / "organization" / "acme-corp.md").exists()


def test_commit_pass_processes_staged_artifacts_in_sorted_sha_order(tmp_path, monkeypatch):
    """Determinism (#403 phase 1, D126): the commit pass replays write_vault in sorted sha order,
    not discovery/extraction order — this is what removes the appears_in/fragment-order race that
    used to leak from concurrent extraction (tests/test_golden_vault.py verifies the end-to-end
    consequence; this pins the ordering mechanism directly)."""
    from watchdog.pipeline import write_vault as write_vault_module

    vault = make_vault(tmp_path)
    extracted_dir = vault / ".watchdog" / "extracted"
    extracted_dir.mkdir(parents=True)
    for sha in ("cccc", "aaaa", "bbbb"):
        (extracted_dir / f"{sha}.json").write_text(
            json.dumps(_extraction(sha=sha, filename=f"{sha}.pdf")), encoding="utf-8")

    order = []

    def fake_wv_run(*, extraction_path, vault_path, neardup_data=None, neardup_file=None, quiet=False):
        order.append(extraction_path.stem)
        return {"new_entities": [], "updated_entities": []}
    monkeypatch.setattr(write_vault_module, "run", fake_wv_run)

    result = orchestrate._commit_pending(vault)

    assert result["committed"] == 3
    assert order == ["aaaa", "bbbb", "cccc"]


def test_commit_pass_survives_one_artifact_failing_to_commit(tmp_path, monkeypatch, capsys):
    """One malformed/corrupt staged artifact must not sink the whole commit pass — mirrors the
    'one bad document must not sink the batch' posture extraction already has (`_fail`), and
    replaces the try/except postflight.run used to wrap this same write_vault call before #403
    phase 1 moved it here. The failing sha's artifact and queue file are left in place so a later
    finalize retries it, instead of losing it silently."""
    from watchdog.pipeline import write_vault as write_vault_module

    vault = make_vault(tmp_path)
    extracted_dir = vault / ".watchdog" / "extracted"
    extracted_dir.mkdir(parents=True)
    for sha in ("aaaa", "bbbb"):
        (extracted_dir / f"{sha}.json").write_text(
            json.dumps(_extraction(sha=sha, filename=f"{sha}.pdf")), encoding="utf-8")
    queue_dir = vault / ".watchdog" / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    (queue_dir / "aaaa.json").write_text("{}")

    def fake_wv_run(*, extraction_path, vault_path, neardup_data=None, neardup_file=None, quiet=False):
        if extraction_path.stem == "aaaa":
            raise ValueError("boom")
        return {"new_entities": ["bbbb"], "updated_entities": []}
    monkeypatch.setattr(write_vault_module, "run", fake_wv_run)

    result = orchestrate._commit_pending(vault)

    # The good one still commits...
    assert result["written"]["bbbb"] == {"new_entities": ["bbbb"], "updated_entities": []}
    # ...the bad one is reported, not silently dropped, and left for a later retry.
    assert "bbbb" in result["written"] and "aaaa" not in result["written"]
    assert (extracted_dir / "aaaa.json").exists()
    assert (queue_dir / "aaaa.json").exists()
    assert "boom" in capsys.readouterr().out


def test_commit_pass_writes_morgue_markdown_from_surviving_queue_file(tmp_path, monkeypatch):
    """§5 of the #403 phase 1 spec: the queue file is not deleted at extraction time —
    write_vault._write_morgue_markdown reads its page markdown at commit time to write the
    morgue .md sibling, and is best-effort-silent if the file is already gone. Deleted only once
    committed."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="abc123", filename="test-doc.pdf", text="The quick brown fox.")
    _mock(monkeypatch, extraction=_extraction())

    asyncio.run(orchestrate.run(vault, skip_finalize=True))
    queue_file = vault / ".watchdog" / "queue" / "abc123.json"
    assert queue_file.exists()   # survives extraction — still needed at commit

    asyncio.run(orchestrate.finalize(vault, post_model="haiku"))

    assert not queue_file.exists()   # consumed once committed
    md_files = list((vault / "morgue").rglob("*.md"))
    assert len(md_files) == 1
    assert "The quick brown fox." in md_files[0].read_text(encoding="utf-8")


# ── #403 phase 2: batch-wide exact-name fold before commit ──────────────────────────────────
#
# The fold that used to run per-document inside write_vault.run's registry lock
# (write_vault._reconcile_entity_ids) is now a single pass over every staged-but-uncommitted
# extraction, run once up front by _commit_pending (orchestrate._batch_exact_fold). These tests
# used to drive the fold via two `write_vault.run()` calls directly (tests/test_write_vault.py);
# now they stage two extraction artifacts under `.watchdog/extracted/` — as extraction itself
# would leave them — and drive the commit pass that folds and then writes them.

def _stage_extracted(vault, tmp_path, sha, filename, overrides=None):
    """Write a staged extraction artifact (`.watchdog/extracted/<sha>.json`), the on-disk form
    `_pending_commits`/`_commit_pending` consume — built from test_write_vault's extraction
    fixture so the document/entity shape stays identical to what real extraction produces.
    `tmp_path` just needs to be a scratch dir distinct per call (make_extraction always writes
    to `<tmp_path>/extraction.json`)."""
    overrides = dict(overrides or {})
    doc_overrides = {"sha256": sha, "filename": filename, "original_path": f"_INCOMING/{filename}"}
    doc_overrides.update(overrides.pop("document", {}))
    overrides["document"] = doc_overrides
    (tmp_path).mkdir(parents=True, exist_ok=True)
    src = make_extraction(tmp_path, overrides)
    dest_dir = vault / ".watchdog" / "extracted"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{sha}.json"
    dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return dest


def _stage_company(vault, tmp_path, sha, filename, eid, name, entity_type="Company"):
    return _stage_extracted(vault, tmp_path, sha, filename, overrides={
        "entities": [{
            "id": eid, "name": name, "type": entity_type,
            "aliases": [], "summary": None, "timeline_events": [], "roles": [],
        }],
        "morgue_entity_id": eid,
        "morgue_document_type": "annual-report",
    })


def test_parallel_slug_variants_reconciled(tmp_path):
    """Two docs coining different slugs for the same entity must collapse to one, folded before
    either commits (sha-a < sha-b, so sha-a's slug is the one that survives, per D126)."""
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "doc-a.pdf").write_text("dummy")
    (vault / "_INCOMING" / "doc-b.pdf").write_text("dummy")

    # First-sorted-sha wins the slug; the other coins a near-duplicate id + name variant.
    _stage_company(vault, tmp_path / "a", "sha-a", "doc-a.pdf",
                   "ernst-and-young-inc", "Ernst & Young Inc.")
    _stage_company(vault, tmp_path / "b", "sha-b", "doc-b.pdf",
                   "ernst-young-inc", "Ernst and Young Inc")

    orchestrate._commit_pending(vault)

    entities = json.loads((vault / ".watchdog" / "registry" / "entities.json").read_text())
    assert "ernst-and-young-inc" in entities
    assert "ernst-young-inc" not in entities          # reconciled away, not a duplicate
    ey = entities["ernst-and-young-inc"]
    assert "sha-a" in ey["appears_in"] and "sha-b" in ey["appears_in"]
    assert "Ernst and Young Inc" in ey["aliases"]     # variant folded in as alias


def test_reconcile_remaps_role_target_in_same_document(tmp_path):
    """A role pointing at a reconciled entity in the same extraction is remapped too."""
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "doc-a.pdf").write_text("dummy")
    (vault / "_INCOMING" / "doc-b.pdf").write_text("dummy")

    # Doc A establishes the canonical company slug.
    _stage_company(vault, tmp_path / "a", "sha-a", "doc-a.pdf",
                   "ernst-and-young-inc", "Ernst & Young Inc.")

    # Doc B re-coins the company under a different slug AND references it from a person.
    _stage_extracted(vault, tmp_path / "b", "sha-b", "doc-b.pdf", overrides={
        "entities": [
            {"id": "jane-doe", "name": "Jane Doe", "type": "Person",
             "aliases": [], "summary": None, "timeline_events": [],
             "roles": [{
                 "relationship": "Partner at", "target_id": "ernst-young-inc",
                 "target_type": "Company", "target_name": "Ernst and Young Inc",
                 "page": 1, "basis": "stated", "date_range": None,
             }]},
            {"id": "ernst-young-inc", "name": "Ernst and Young Inc", "type": "Company",
             "aliases": [], "summary": None, "timeline_events": [], "roles": []},
        ],
        "morgue_entity_id": "jane-doe",
        "morgue_document_type": "filing",
    })

    orchestrate._commit_pending(vault)

    entities = json.loads((vault / ".watchdog" / "registry" / "entities.json").read_text())
    # Person's role now points at the canonical slug, not the orphaned one.
    role = entities["jane-doe"]["roles"][0]
    assert role["target_id"] == "ernst-and-young-inc"
    # Reverse role landed on the canonical company entity.
    assert any(r["target_id"] == "jane-doe" for r in entities["ernst-and-young-inc"]["roles"])


def test_reconcile_matches_against_existing_alias(tmp_path):
    """A new slug matching an existing entity's *alias* (not its name) reconciles."""
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "doc-a.pdf").write_text("dummy")
    (vault / "_INCOMING" / "doc-b.pdf").write_text("dummy")

    # Doc A establishes the entity with an alias.
    _stage_extracted(vault, tmp_path / "a", "sha-a", "doc-a.pdf", overrides={
        "entities": [{"id": "ibm", "name": "IBM", "type": "Company",
                      "aliases": ["International Business Machines"], "summary": None,
                      "timeline_events": [], "roles": []}],
        "morgue_entity_id": "ibm", "morgue_document_type": "annual-report",
    })

    # Doc B coins a new slug whose name matches the *alias* above.
    _stage_company(vault, tmp_path / "b", "sha-b", "doc-b.pdf",
                   "international-business-machines", "International Business Machines")

    orchestrate._commit_pending(vault)

    entities = json.loads((vault / ".watchdog" / "registry" / "entities.json").read_text())
    assert "international-business-machines" not in entities   # reconciled onto the alias match
    assert "sha-b" in entities["ibm"]["appears_in"]


def test_reconcile_does_not_merge_across_types(tmp_path):
    """Same normalized name but different entity types must stay separate."""
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "doc-a.pdf").write_text("dummy")
    (vault / "_INCOMING" / "doc-b.pdf").write_text("dummy")

    # A Person and a Company that normalize to the same key.
    _stage_extracted(vault, tmp_path / "a", "sha-a", "doc-a.pdf", overrides={
        "entities": [{"id": "morgan-person", "name": "Morgan", "type": "Person",
                      "aliases": [], "summary": None, "timeline_events": [], "roles": []}],
        "morgue_entity_id": "morgan-person", "morgue_document_type": "filing",
    })
    _stage_company(vault, tmp_path / "b", "sha-b", "doc-b.pdf", "morgan-company", "Morgan")

    orchestrate._commit_pending(vault)

    entities = json.loads((vault / ".watchdog" / "registry" / "entities.json").read_text())
    assert "morgan-person" in entities and "morgan-company" in entities  # type-scoped, not merged


def test_drifting_type_synonyms_reconcile_to_one_entity(tmp_path):
    """#335: the same real-world entity labelled with drifting near-synonyms across two
    documents (``Company`` in one, ``Financial Institution`` in the next) must reconcile onto
    a single id/folder instead of forking — both collapse to the ``organization`` bucket, so
    the reconciliation key matches where free-text types used to miss."""
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "doc-a.pdf").write_text("dummy")
    (vault / "_INCOMING" / "doc-b.pdf").write_text("dummy")

    # Doc A: the bank labelled a plain "Company".
    _stage_company(vault, tmp_path / "a", "sha-a", "doc-a.pdf",
                   "td-bank", "Toronto-Dominion Bank")

    # Doc B: same bank, a different slug AND a drifting type label the old code would have forked.
    _stage_company(vault, tmp_path / "b", "sha-b", "doc-b.pdf",
                   "toronto-dominion-bank", "Toronto-Dominion Bank",
                   entity_type="Financial Institution")

    orchestrate._commit_pending(vault)

    entities = json.loads((vault / ".watchdog" / "registry" / "entities.json").read_text())
    assert "td-bank" in entities
    assert "toronto-dominion-bank" not in entities        # reconciled, not forked (#335)
    td = entities["td-bank"]
    assert "sha-a" in td["appears_in"] and "sha-b" in td["appears_in"]
    assert td["type"] == "organization"                   # stored type is the canonical bucket


def test_batch_fold_collapses_staged_duplicates_before_commit(tmp_path):
    """The fold itself, isolated from the commit: two staged artifacts naming the same entity
    under different (normalized-identical) names/ids must carry one canonical id — the
    earliest-sha document's — in the staged JSON on disk *before* `_batch_exact_fold` returns,
    i.e. before either has been committed to the vault."""
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "doc-a.pdf").write_text("dummy")
    (vault / "_INCOMING" / "doc-b.pdf").write_text("dummy")

    _stage_company(vault, tmp_path / "a", "sha-a", "doc-a.pdf",
                   "ernst-and-young-inc", "Ernst & Young Inc.")
    _stage_company(vault, tmp_path / "b", "sha-b", "doc-b.pdf",
                   "ernst-young-inc", "Ernst and Young Inc")

    orchestrate._batch_exact_fold(vault, ["sha-a", "sha-b"])

    # Nothing committed yet — the registry is untouched.
    assert not json.loads((vault / ".watchdog" / "registry" / "entities.json").read_text())

    staged_a = json.loads((vault / ".watchdog" / "extracted" / "sha-a.json").read_text())
    staged_b = json.loads((vault / ".watchdog" / "extracted" / "sha-b.json").read_text())
    assert staged_a["entities"][0]["id"] == "ernst-and-young-inc"   # untouched — it's the winner
    assert staged_b["entities"][0]["id"] == "ernst-and-young-inc"   # remapped onto the winner
    assert "Ernst and Young Inc" in staged_b["entities"][0]["aliases"]   # variant name preserved


def test_batch_fold_remaps_morgue_entity_id_and_key_facts_entities(tmp_path):
    """#513: the losing entity's id doesn't only live in `entities[].id` — a document can also be
    filed under it (`morgue_entity_id`) or have a fact tagged against it
    (`document.key_facts[].entities`). Both must follow the same remap the exact-name fold applies
    to `entities[].id`, or doc-b ends up filed at `morgue/ernst-young-inc/...` even though that
    entity's note now lives under the winning id."""
    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "doc-a.pdf").write_text("dummy")
    (vault / "_INCOMING" / "doc-b.pdf").write_text("dummy")

    _stage_company(vault, tmp_path / "a", "sha-a", "doc-a.pdf",
                   "ernst-and-young-inc", "Ernst & Young Inc.")
    _stage_extracted(vault, tmp_path / "b", "sha-b", "doc-b.pdf", overrides={
        "entities": [{"id": "ernst-young-inc", "name": "Ernst and Young Inc", "type": "Company",
                      "aliases": [], "summary": None, "timeline_events": [], "roles": []}],
        "morgue_entity_id": "ernst-young-inc", "morgue_document_type": "annual-report",
        "document": {"key_facts": [
            {"fact": "Audited the accounts.", "page": 1, "basis": "stated",
             "entities": ["ernst-young-inc"]},
        ]},
    })

    orchestrate._batch_exact_fold(vault, ["sha-a", "sha-b"])

    staged_b = json.loads((vault / ".watchdog" / "extracted" / "sha-b.json").read_text())
    assert staged_b["morgue_entity_id"] == "ernst-and-young-inc"
    assert staged_b["document"]["key_facts"][0]["entities"] == ["ernst-and-young-inc"]


def test_submit_batch_classifies_unpinned_docs_but_not_pinned_ones(tmp_path, monkeypatch):
    """D144's cost note, made checkable: an unpinned document costs one classify call before the
    batch is built; a sidecar-pinned one costs none. That is what makes `--skill` still worth
    passing on a genuinely homogeneous batch."""
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="pinned", filename="p.pdf", sidecar="skill: bankruptcy\n")
    _queue_doc(vault, sha="loose", filename="l.pdf")

    classified = []

    async def _fake_classify(excerpt, model, backend=None, **kw):
        classified.append(kw.get("filename"))
        return "court-documents.md"

    monkeypatch.setattr(orchestrate, "_classify", _fake_classify)

    async def _fake_submit(vault, docs, *, model, effort, skills, api_key, backend=None):
        return "batch_x"
    monkeypatch.setattr(orchestrate.batch_extract, "submit", _fake_submit)

    asyncio.run(orchestrate._submit_batch(
        vault, ["pinned", "loose"], None, "sonnet", None, None, 5, "haiku", 5, None, "sk-x"))

    assert classified == ["l.pdf"]   # only the unpinned document paid for a classify call


# ── the verification pass (#535) ──────────────────────────────────────────────

def _verify_mock(monkeypatch, *, extraction, missing_facts, fail_verify=None, calls=None):
    """Mock every model call, capturing the extract/verify prompts so a test can assert on the
    shared prefix. `calls` collects `(task, prompt)` pairs in order."""
    seen = calls if calls is not None else []

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        seen.append((task, prompt, effort))
        if task == "verify":
            if fail_verify is not None:
                raise fail_verify
            parsed = {"missing_facts": missing_facts}
        else:
            parsed = {
                "classify": {"skill": "general-records.md"},
                "entity-synthesis": {"entity_syntheses": []},
                "timeline-dedup": {"groups": []},
                "briefing": {"investigation_status": "x", "what_was_ingested": [],
                             "new_entities": []},
            }.get(task, extraction)
        return model_client.ModelResult(parsed=parsed, text="", model="m", backend="b",
                                        auth_mode="subscription", cost_usd=0.01)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)
    return seen


def _staged(vault, sha="abc123"):
    return json.loads((vault / ".watchdog" / "extracted" / f"{sha}.json").read_text())


def test_no_verify_call_is_made_unless_the_pass_is_turned_on(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    calls = _verify_mock(monkeypatch, extraction=_extraction(),
                         missing_facts=[{"fact": "Should never be asked for."}])

    asyncio.run(orchestrate.run(vault))

    assert not [t for t, _, _ in calls if t == "verify"]
    assert len(_staged(vault)["document"]["key_facts"]) == 1


def test_verified_facts_are_staged_alongside_the_extractors_own(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    _verify_mock(monkeypatch, extraction=_extraction(), missing_facts=[
        {"fact": "The auditor issued its opinion on 30 June 2024.", "page": 1,
         "entities": ["acme-corp"]},
        {"fact": "Filed in 2024"},                       # restatement — suppressed in code
    ])

    summary = asyncio.run(orchestrate.run(vault, verify=True))

    assert summary["extracted"] == 1
    facts = _staged(vault)["document"]["key_facts"]
    assert [f["fact"] for f in facts] == ["Filed in 2024",
                                          "The auditor issued its opinion on 30 June 2024."]
    assert facts[1]["added_by"] == "verify"
    # The added fact went through the same entity fan-out every other fact does — there is no
    # second class of fact downstream.
    assert facts[1]["entities"] == ["acme-corp"]


def test_the_verify_call_resends_the_extraction_prompt_unchanged(tmp_path, monkeypatch):
    """The cost case for the pass, asserted end-to-end: the verifier's prompt is the extraction
    call's own blocks plus a tail, so the document text is re-read at the cached rate."""
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    calls = _verify_mock(monkeypatch, extraction=_extraction(), missing_facts=[])

    asyncio.run(orchestrate.run(vault, verify=True))

    extract_prompt = next(p for t, p, _ in calls if t == "extract")
    verify_prompt = next(p for t, p, _ in calls if t == "verify")
    assert verify_prompt[:len(extract_prompt)] == extract_prompt
    assert len(verify_prompt) == len(extract_prompt) + 1
    # ...and the document block carries a cache breakpoint, which it doesn't without the pass.
    assert "cache_control" in extract_prompt[2]


def test_the_verify_call_runs_at_low_effort_by_default(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    calls = _verify_mock(monkeypatch, extraction=_extraction(), missing_facts=[])

    asyncio.run(orchestrate.run(vault, verify=True, extract_effort="high"))

    assert next(e for t, _, e in calls if t == "extract") == "high"
    assert next(e for t, _, e in calls if t == "verify") == "low"


def test_verifier_effort_is_configurable(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"verifier_effort": "medium"}))
    monkeypatch.setattr("watchdog.cmd.base.CONFIG_FILE", config)
    calls = _verify_mock(monkeypatch, extraction=_extraction(), missing_facts=[])

    asyncio.run(orchestrate.run(vault, verify=True))

    assert next(e for t, _, e in calls if t == "verify") == "medium"


@pytest.mark.parametrize("failure", [
    model_client.ModelError("verifier returned no valid JSON"),
    TimeoutError("read timed out"),            # not a ModelError — the catch is broad on purpose
])
def test_a_failed_verification_leaves_the_document_extracted_and_intact(tmp_path, monkeypatch,
                                                                        failure):
    """The pass adds recall to a result that is already complete — letting a new optional call
    fail a document that already extracted cleanly would trade the thing being measured for the
    thing that already works."""
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    _verify_mock(monkeypatch, extraction=_extraction(), missing_facts=[], fail_verify=failure)

    summary = asyncio.run(orchestrate.run(vault, verify=True))

    assert summary["extracted"] == 1 and summary["failed"] == 0
    assert [f["fact"] for f in _staged(vault)["document"]["key_facts"]] == ["Filed in 2024"]
    assert "verification pass failed" in (vault / ".watchdog" / "registry" / "ingest.log").read_text()


def test_a_rate_limit_during_verification_stops_the_run(tmp_path, monkeypatch):
    """The one failure the pass must not swallow: a session-wide limit has to stop the batch
    cleanly, exactly as it would from any other call."""
    vault = make_vault(tmp_path)
    _queue_doc(vault)

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        if task == "verify":
            raise model_client.RateLimitError("session limit reached")
        parsed = {"classify": {"skill": "general-records.md"}}.get(task, _extraction())
        return model_client.ModelResult(parsed=parsed, text="", model="m", backend="b",
                                        auth_mode="subscription", cost_usd=0.01)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    summary = asyncio.run(orchestrate.run(vault, verify=True))

    assert summary.get("rate_limited")
    assert summary["extracted"] == 0


def test_the_verify_calls_cost_is_billed_to_the_document(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    _verify_mock(monkeypatch, extraction=_extraction(), missing_facts=[])

    with_verify = asyncio.run(orchestrate.run(vault, verify=True))

    _queue_doc(vault)
    _verify_mock(monkeypatch, extraction=_extraction(), missing_facts=[])
    without = asyncio.run(orchestrate.run(vault, verify=False, force=True))

    assert (with_verify["results"][0]["cost_usd"]
            == pytest.approx(without["results"][0]["cost_usd"] + 0.01))


@pytest.mark.parametrize("backend", ["claude-batch", "openai-batch"])
def test_run_refuses_the_verification_pass_on_a_batch_backend(tmp_path, backend):
    """cmd_ingest already refuses this, but a programmatic caller that skips CLI validation — a
    benchmark arm pinning both — must not silently get an unverified run labelled a verified one."""
    vault = make_vault(tmp_path)
    _queue_doc(vault)

    with pytest.raises(ValueError, match="not supported with " + backend):
        asyncio.run(orchestrate.run(vault, extract_backend=backend, verify=True))


def test_each_section_is_verified_against_its_own_text(tmp_path, monkeypatch):
    """Sectioned documents verify per section, not once over the merged result: a section's text
    is the only thing the verifier can be handed a byte-identical prefix for."""
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    plan, pf = _checkpoint_plan_and_pf(vault, n=2)
    outs = {1: _checkpoint_section_out(1), 2: _checkpoint_section_out(2)}
    seen = []

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        seen.append((task, _flat(prompt)))
        if task == "verify":
            n = sum(1 for t, _ in seen if t == "verify")
            parsed = {"missing_facts": [{"fact": f"Missed in section {n}."}]}
        elif task == "extract-section":
            parsed = outs[sum(1 for t, _ in seen if t == "extract-section")]
        else:
            parsed = {"summary": "digest"}
        return model_client.ModelResult(parsed=parsed, text="", model="m", backend="b",
                                        auth_mode="subscription", cost_usd=0.02)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    extraction, _, cost, ok, errors, _ = asyncio.run(
        orchestrate._extract_sectioned(vault, "abc123", pf, "SKILL", plan, "sonnet",
                                       "annual-report", verify_pass=True))

    assert ok, errors
    assert [t for t, _ in seen] == ["extract-section", "verify", "extract-section", "verify",
                                    "digest"]
    # Each verify call saw only its own section's text.
    assert "Section 1 text." in seen[1][1] and "Section 2 text." not in seen[1][1]
    assert "Section 2 text." in seen[3][1] and "Section 1 text." not in seen[3][1]
    assert {f["fact"] for f in extraction["document"]["key_facts"]} == {
        "Fact 1", "Fact 2", "Missed in section 1.", "Missed in section 2."}
    assert cost == pytest.approx(0.02 * 5)


def test_a_resumed_section_is_not_verified_again(tmp_path, monkeypatch):
    """Verification cost is folded into the section's checkpoint, so replaying a checkpointed
    section replays its verified facts rather than paying for a second look."""
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    plan, pf = _checkpoint_plan_and_pf(vault, n=2)
    checkpointed = _checkpoint_section_out(1)
    checkpointed["document"]["key_facts"].append(
        {"fact": "Missed in section 1.", "added_by": "verify"})
    orchestrate._write_section_checkpoint(vault, "abc123", plan["sections"][0], checkpointed, 0.05)
    seen = []

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        seen.append(task)
        parsed = ({"missing_facts": []} if task == "verify"
                  else _checkpoint_section_out(2) if task == "extract-section"
                  else {"summary": "digest"})
        return model_client.ModelResult(parsed=parsed, text="", model="m", backend="b",
                                        auth_mode="subscription", cost_usd=0.02)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    extraction, _, cost, ok, errors, _ = asyncio.run(
        orchestrate._extract_sectioned(vault, "abc123", pf, "SKILL", plan, "sonnet",
                                       "annual-report", verify_pass=True))

    assert ok, errors
    assert seen == ["extract-section", "verify", "digest"]     # section 1 not re-called or re-verified
    assert "Missed in section 1." in {f["fact"] for f in extraction["document"]["key_facts"]}
    assert cost == pytest.approx(0.05 + 0.02 * 3)


def test_section1_repair_after_a_resplit_replaces_both_halves(tmp_path, monkeypatch):
    """The #505 section-1 repair re-runs the *whole* of section 1, so its result supersedes every
    part that section contributed — including the second half, when section 1 had been re-split by
    #540. Tracking results per planned section (rather than as a flat list where `parts[0] = …`
    replaces only the first) is what makes that expressible: otherwise the superseded half stays
    in the merge and section 1's tail is extracted twice into the same document."""
    vault = make_vault(tmp_path)
    _queue_doc(vault)
    plan, pf = _resplit_plan_and_pf(vault)
    calls = []

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        calls.append(task)
        if task != "extract-section":
            return model_client.ModelResult(parsed={"summary": "digest"}, text="", model="m",
                                            backend="b", auth_mode="subscription", cost_usd=0.01)
        n = sum(1 for t in calls if t == "extract-section")
        if n == 1:
            raise model_client.ModelError("output truncated", truncated=True)
        if n in (2, 3, 4):
            # No section supplies morgue_entity_id, and no entities either — merge's #505 fallback
            # derives one from the entity list when it can, so the list has to be empty for
            # post-flight to reject on the morgue id alone and the repair path to be reached.
            label = {2: "half1", 3: "half2", 4: "sec2"}[n]
            out = _resplit_section_out(label, first=(n == 2))
            out["entities"] = []
            out.pop("morgue_entity_id", None)
        else:                                       # the repair call, re-running all of section 1
            out = _resplit_section_out("repaired", first=True)
        return model_client.ModelResult(parsed=out, text="", model="m", backend="b",
                                        auth_mode="subscription", cost_usd=0.02)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    extraction, scratchpad, cost, ok, errors, warnings = asyncio.run(
        orchestrate._extract_sectioned(vault, "abc123", pf, "SKILL", plan, "sonnet",
                                       "annual-report"))

    assert ok, errors
    facts = [f["fact"] for f in extraction["document"]["key_facts"]]
    # The repaired whole-section result stands in for both halves; "Fact half2" must not survive
    # alongside it, and "Fact half1" is superseded too.
    assert facts == ["Fact repaired", "Fact sec2"]


def test_half_label_names_the_pages_a_half_actually_holds():
    """A re-split half inherits nothing from its parent's label: "pages 1–4" split in two must
    report "pages 1–2" and "pages 3–4", not the parent range twice. The label reaches the prompt's
    own section_label, the candidate-harvest flow label, and the usage row's `detail` field — the
    last is what a later cost or truncation investigation reads, so an overstated range there is a
    diagnostic trap of exactly the kind #547 cost time on."""
    first = "\n\n---\n\n".join(f"<!-- PAGE {n} -->\n\ntext {n}" for n in (1, 2))
    second = "\n\n---\n\n".join(f"<!-- PAGE {n} -->\n\ntext {n}" for n in (3, 4))
    assert orchestrate._half_label(first, "pages 1–4", 1, 2) == "pages 1–2"
    assert orchestrate._half_label(second, "pages 1–4", 2, 2) == "pages 3–4"
    # A half holding one page says "page", not a degenerate "pages 3–3".
    assert orchestrate._half_label("<!-- PAGE 3 -->\n\ntext", "pages 3–4", 1, 2) == "page 3"
    # No markers to read (a character-split, non-paginated section) — fall back to the parent.
    assert orchestrate._half_label("plain text", "part 2 of 5", 1, 2) == "part 2 of 5 (part 1/2)"
