import json
from pathlib import Path

from watchdog.pipeline import preflight


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    reg = vault / ".watchdog" / "registry"
    reg.mkdir(parents=True)
    (vault / ".watchdog" / "queue").mkdir()
    return vault


def _write_queue(vault: Path, sha: str, text: str) -> None:
    queue = {
        "sha256": sha,
        "filename": "doc.pdf",
        "page_count": 1,
        "pages": [{"page": 1, "markdown": text}],
        "near_dup": {},
    }
    (vault / ".watchdog" / "queue" / f"{sha}.json").write_text(json.dumps(queue))


# Pre-flight reads no vault entity state (#381/D118): extraction is a pure function of the
# document, so there are no candidate-entity, digest-size, or timeline-hoist tests here any more —
# that behaviour, and the tests that covered it, moved to the finalizer (tests/test_reconcile.py).
# What remains is queue reading, the already-extracted short-circuit, the order-insensitive
# known_document_types read, and file-metadata/processing passthrough.


def test_missing_queue_file_returns_error(tmp_path):
    vault = _vault(tmp_path)
    result = preflight.run(vault, "nope")
    assert "error" in result


def test_pages_and_identity_surfaced_from_queue(tmp_path):
    vault = _vault(tmp_path)
    _write_queue(vault, "doc1", "Some text.")
    pf = preflight.run(vault, "doc1")
    assert pf["sha256"] == "doc1"
    assert pf["filename"] == "doc.pdf"
    assert pf["page_count"] == 1
    assert pf["pages"][0]["markdown"] == "Some text."
    assert pf["already_extracted"] is False


def test_already_extracted_flagged_when_sha_in_documents(tmp_path):
    vault = _vault(tmp_path)
    _write_queue(vault, "doc1", "Some text.")
    (vault / ".watchdog" / "registry" / "documents.json").write_text(
        json.dumps({"doc1": {"sha256": "doc1", "document_type": "Report"}}))
    assert preflight.run(vault, "doc1")["already_extracted"] is True


def test_known_document_types_collected_from_registry(tmp_path):
    """preflight surfaces the distinct document_types already in the vault so the extractor
    can reuse them (deduped, sorted; missing/empty types ignored)."""
    vault = _vault(tmp_path)
    _write_queue(vault, "sha-new", "Some new document text.")
    (vault / ".watchdog" / "registry" / "documents.json").write_text(json.dumps({
        "a": {"sha256": "a", "document_type": "Annual Report"},
        "b": {"sha256": "b", "document_type": "Affidavit"},
        "c": {"sha256": "c", "document_type": "Annual Report"},   # dup
        "d": {"sha256": "d"},                                     # no type
    }))
    pf = preflight.run(vault, "sha-new")
    assert pf["known_document_types"] == ["Affidavit", "Annual Report"]


def test_known_document_types_empty_on_fresh_vault(tmp_path):
    vault = _vault(tmp_path)
    _write_queue(vault, "sha-new", "First doc.")
    pf = preflight.run(vault, "sha-new")
    assert pf["known_document_types"] == []


# ── file_metadata / processing passthrough (#369) ────────────────────────────

def test_preflight_surfaces_file_metadata_from_queue(tmp_path):
    vault = _vault(tmp_path)
    _write_queue(vault, "doc1", "Some text.")
    queue_path = vault / ".watchdog" / "queue" / "doc1.json"
    queue = json.loads(queue_path.read_text())
    queue["file_metadata"] = {"author": "Jane Doe", "producer": "Acrobat"}
    queue_path.write_text(json.dumps(queue))

    pf = preflight.run(vault, "doc1")
    assert pf["file_metadata"] == {"author": "Jane Doe", "producer": "Acrobat"}


def test_preflight_defaults_file_metadata_to_empty_dict(tmp_path):
    vault = _vault(tmp_path)
    _write_queue(vault, "doc1", "Some text.")
    pf = preflight.run(vault, "doc1")
    assert pf["file_metadata"] == {}


def test_preflight_surfaces_processing_from_queue_metadata(tmp_path):
    vault = _vault(tmp_path)
    _write_queue(vault, "doc1", "Some text.")
    queue_path = vault / ".watchdog" / "queue" / "doc1.json"
    queue = json.loads(queue_path.read_text())
    queue["metadata"] = {"ocr_used": True, "source_type": "docling"}
    queue_path.write_text(json.dumps(queue))

    pf = preflight.run(vault, "doc1")
    assert pf["processing"] == {"ocr_used": True, "source_type": "docling"}
