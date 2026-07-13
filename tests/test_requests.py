"""Document requests (#365): the ledger, the rendered checklist, and the surfaces that read it."""

import json
from pathlib import Path

from watchdog.pipeline import merge, requests, resolutions


SHA = "a1b2c3d4e5f6" + "0" * 52   # 64 chars, like a real sha256


def _vault(tmp_path: Path) -> Path:
    (tmp_path / ".watchdog" / "Registry").mkdir(parents=True)
    return tmp_path


def _req(what="Hearing transcript, 14 March 2024", **kw):
    return {"type": "Hearing transcript", "what": what,
            "why_it_matters": "The order relies on testimony not otherwise on the record",
            "likely_source": "Court registry", **kw}


def _record(vault: Path, items, sha=SHA):
    return requests.record(vault, items, sha256=sha, filename="order.pdf",
                           document_note="documents/order")


# ── Ledger ──────────────────────────────────────────────────────────────────────

def test_record_stamps_id_and_provenance(tmp_path):
    vault = _vault(tmp_path)
    added = _record(vault, [_req()])

    assert added == [resolutions.request_id(SHA, "Hearing transcript, 14 March 2024")]
    entry = requests.load(vault)["requests"][added[0]]
    assert entry["what"] == "Hearing transcript, 14 March 2024"
    assert entry["type"] == "Hearing transcript"
    assert entry["likely_source"] == "Court registry"
    # Provenance is Python's to stamp, not the model's (§I1).
    assert entry["source_sha256"] == SHA
    assert entry["source_filename"] == "order.pdf"
    assert entry["document_note"] == "documents/order"
    assert entry["added"]


def test_record_is_idempotent_across_a_repair_retry(tmp_path):
    """write_vault re-runs on a repair retry — the same request must not land twice."""
    vault = _vault(tmp_path)
    _record(vault, [_req()])
    # Same request, re-recorded with cosmetically different whitespace/case.
    added = _record(vault, [_req(what="  hearing   transcript, 14 MARCH 2024 ")])

    assert added == []
    assert len(requests.load(vault)["requests"]) == 1


def test_record_skips_malformed_entries_without_raising(tmp_path):
    """A bad request must never fail an extraction that is otherwise good."""
    vault = _vault(tmp_path)
    added = _record(vault, ["not a dict", {"what": "   "}, {"type": "Order"}, _req()])

    assert len(added) == 1
    assert len(requests.load(vault)["requests"]) == 1


def test_record_of_the_same_text_from_two_documents_keeps_both(tmp_path):
    """Requests are keyed on the source document too — two orders each citing the transcript
    are two separate to-get items, each traceable to the document that named it."""
    vault = _vault(tmp_path)
    _record(vault, [_req()])
    _record(vault, [_req()], sha="f" * 64)

    assert len(requests.load(vault)["requests"]) == 2


def test_load_tolerates_a_corrupt_ledger(tmp_path):
    vault = _vault(tmp_path)
    (vault / ".watchdog" / "Registry" / "requests.json").write_text("{not json", encoding="utf-8")
    assert requests.load(vault) == {"schema_version": 1, "requests": {}}


# ── Open set (resolution overlay) ───────────────────────────────────────────────

def test_open_requests_drops_resolved_ones(tmp_path):
    vault = _vault(tmp_path)
    _record(vault, [_req(), _req(what="Docket sheet, case CV-2024-118")])
    rid = resolutions.request_id(SHA, "Hearing transcript, 14 March 2024")

    resolutions.resolve(vault, [rid], label="manual")

    open_ = requests.open_requests(vault)
    assert [r["what"] for r in open_] == ["Docket sheet, case CV-2024-118"]
    # The ledger keeps the resolved entry — unresolving restores it (render-time overlay).
    assert len(requests.load(vault)["requests"]) == 2
    resolutions.unresolve(vault, [rid])
    assert len(requests.open_requests(vault)) == 2


# ── Rendered checklist ──────────────────────────────────────────────────────────

def test_write_requests_renders_groups_and_checkbox_markers(tmp_path):
    vault = _vault(tmp_path)
    _record(vault, [_req(), _req(type="Regulation", what="Enabling regulation O. Reg. 4/22",
                                 likely_source="Ontario e-Laws")])

    relpath = requests.write_requests(vault)

    assert relpath == "requests.md"
    body = (vault / "requests.md").read_text(encoding="utf-8")
    assert "## Hearing transcript" in body and "## Regulation" in body
    rid = resolutions.request_id(SHA, "Hearing transcript, 14 March 2024")
    assert f"- [ ] **Hearing transcript, 14 March 2024** <!--wid:{rid}-->" in body
    assert "  - Why: The order relies on testimony" in body
    assert "  - Likely source: Court registry" in body
    assert "  - Referenced in [[documents/order|order.pdf]]" in body


def test_write_requests_omits_a_missing_likely_source(tmp_path):
    vault = _vault(tmp_path)
    _record(vault, [_req(likely_source="")])
    requests.write_requests(vault)
    assert "Likely source" not in (vault / "requests.md").read_text(encoding="utf-8")


def test_write_requests_removes_the_file_once_everything_is_resolved(tmp_path):
    """requests.md is a current-state view, not an event log — a fully-cleared queue leaves
    no stale checklist behind."""
    vault = _vault(tmp_path)
    _record(vault, [_req()])
    requests.write_requests(vault)
    assert (vault / "requests.md").exists()

    resolutions.resolve(vault, [resolutions.request_id(SHA, _req()["what"])], label="manual")

    assert requests.write_requests(vault) is None
    assert not (vault / "requests.md").exists()


# ── Checkbox sync from the vault-root requests.md ───────────────────────────────

def test_sync_picks_up_a_ticked_request_checkbox(tmp_path):
    vault = _vault(tmp_path)
    _record(vault, [_req()])
    requests.write_requests(vault)
    rid = resolutions.request_id(SHA, _req()["what"])

    # The journalist obtained the transcript and ticked the box.
    md = vault / "requests.md"
    md.write_text(md.read_text(encoding="utf-8").replace("- [ ]", "- [x]"), encoding="utf-8")
    added, removed = resolutions.sync_from_briefings(vault)

    assert added == [rid]
    assert requests.open_requests(vault) == []


def test_sync_unticking_a_request_reopens_it(tmp_path):
    vault = _vault(tmp_path)
    _record(vault, [_req()])
    rid = resolutions.request_id(SHA, _req()["what"])
    resolutions.resolve(vault, [rid], label="manual")
    # requests.md still renders it only if unresolved, so write the un-ticked line by hand —
    # exactly what a journalist undoing a resolution in the file does.
    (vault / "requests.md").write_text(f"- [ ] **x** <!--wid:{rid}-->\n", encoding="utf-8")

    added, removed = resolutions.sync_from_briefings(vault)

    assert removed == [rid]
    assert len(requests.open_requests(vault)) == 1


# ── Section merge ───────────────────────────────────────────────────────────────

def test_merge_unions_and_dedups_document_requests_across_sections(tmp_path):
    sections = [
        {"entities": [], "document": {"key_facts": []},
         "document_requests": [_req()]},
        {"entities": [],
         "document_requests": [_req(what="hearing   transcript, 14 march 2024"),   # same request
                               _req(type="Regulation", what="Enabling regulation O. Reg. 4/22")]},
    ]

    merged = merge.merge_extractions(sections)

    whats = [r["what"] for r in merged["document_requests"]]
    assert whats == ["Hearing transcript, 14 March 2024", "Enabling regulation O. Reg. 4/22"]


def test_merge_omits_the_key_when_no_section_names_a_request(tmp_path):
    merged = merge.merge_extractions([{"entities": [], "document": {"key_facts": []}}])
    assert "document_requests" not in merged


# ── Write-vault integration (the ledger is written under the registry lock) ──────

def test_write_vault_records_requests_from_an_extraction(tmp_path):
    from watchdog.pipeline import write_vault

    vault = _vault(tmp_path)
    (vault / "_INCOMING").mkdir()
    (vault / "_INCOMING" / "order.pdf").write_text("x", encoding="utf-8")
    extraction = {
        "document": {"sha256": SHA, "filename": "order.pdf", "title": "Order",
                     "document_type": "Court order", "summary": "An order.", "key_facts": []},
        "entities": [{"id": "acme", "name": "Acme Ltd", "type": "organization"}],
        "morgue_entity_id": "acme", "morgue_document_type": "court-order",
        "document_requests": [_req()],
    }
    path = vault / ".watchdog" / "tmp" / "ex.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(extraction), encoding="utf-8")

    write_vault.run(extraction_path=path, vault_path=vault, quiet=True)

    open_ = requests.open_requests(vault)
    assert len(open_) == 1
    assert open_[0]["source_filename"] == "order.pdf"
    # Provenance points at the document note the same run wrote.
    assert (vault / f"{open_[0]['document_note']}.md").exists()


# ── Briefing pointer (deterministic, never a model input) ───────────────────────

def test_briefing_points_at_new_requests(tmp_path):
    from watchdog.pipeline import orchestrate

    vault = _vault(tmp_path)
    briefing = {"investigation_status": "Early.", "what_was_ingested": ["order.pdf — a court order"]}
    results = [{"sha256": SHA, "filename": "order.pdf"}]

    relpath = orchestrate._write_briefing(vault, briefing, results, [], [], 2)

    body = (vault / relpath).read_text(encoding="utf-8")
    assert "## Document requests" in body
    assert "- 2 new document requests — see [[requests|requests.md]]" in body


def test_briefing_says_nothing_when_the_run_produced_no_requests(tmp_path):
    from watchdog.pipeline import orchestrate

    vault = _vault(tmp_path)
    relpath = orchestrate._write_briefing(
        vault, {"investigation_status": "Early.", "what_was_ingested": []}, [], [], [], 0)

    assert "Document requests" not in (vault / relpath).read_text(encoding="utf-8")
