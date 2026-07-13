import json
import tempfile
from pathlib import Path

from watchdog.pipeline import preflight


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    reg = vault / ".watchdog" / "Registry"
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


def _write_registry(vault: Path) -> None:
    reg = vault / ".watchdog" / "Registry"
    manifest = {
        "alice-smith": {"name": "Alice Smith", "type": "Person",
                        "aliases": ["A. Smith"], "note_path": "entities/person/alice-smith"},
        "bob-jones": {"name": "Bob Jones", "type": "Person",
                      "aliases": [], "note_path": "entities/person/bob-jones"},
    }
    (reg / "manifest.json").write_text(json.dumps(manifest))
    entities = {
        "alice-smith": {
            "id": "alice-smith", "name": "Alice Smith", "type": "Person",
            "timeline_events": [
                {"date": "2020-03-15", "event": "Appointed director",
                 "page": 2, "basis": "stated", "source_sha256": "x"},
            ],
            "roles": [
                {"relationship": "Director of", "target_id": "acme",
                 "target_name": "Acme Corp", "target_type": "Company",
                 "date_range": "2020–", "basis": "stated",
                 "source_sha256": "x", "is_reverse": False, "page": 2},
            ],
        },
    }
    (reg / "entities.json").write_text(json.dumps(entities))
    (reg / "documents.json").write_text("{}")

    note = vault / "entities" / "person" / "alice-smith.md"
    note.parent.mkdir(parents=True)
    note.write_text(
        "---\nid: alice-smith\n---\n\n# Alice Smith\n\n"
        "## Summary\n\nA director.\n\n"
        "## Analysis\n\n> [!contradiction] role mismatch\n> - foo\n\n"
        "## Timeline\n\n- timeline stuff\n"
    )


def test_candidate_enriched_with_digest_and_analysis(tmp_path):
    vault = _vault(tmp_path)
    _write_registry(vault)
    _write_queue(vault, "doc1", "Alice Smith is a director of Acme Corp.")

    result = preflight.run(vault, "doc1")
    by_id = {e["id"]: e for e in result["existing_entities"]}

    assert "alice-smith" in by_id          # name appears in text
    assert "bob-jones" not in by_id        # name absent from text

    a = by_id["alice-smith"]
    # timeline events are hoisted out of the per-candidate digest into a shared list
    assert "timeline_events" not in a
    assert result["existing_timeline"] == [
        {"date": "2020-03-15", "event": "Appointed director", "basis": "stated",
         "entities": ["alice-smith"]}
    ]
    # roles trimmed to comparison fields
    assert a["roles"] == [
        {"relationship": "Director of", "target_name": "Acme Corp",
         "target_type": "Company", "date_range": "2020–", "basis": "stated"}
    ]
    # analysis carries prior contradiction callouts, scoped to the Analysis section
    assert "[!contradiction] role mismatch" in a["analysis"]
    assert "timeline stuff" not in a["analysis"]


def test_candidate_without_registry_entry_has_empty_digest(tmp_path):
    vault = _vault(tmp_path)
    reg = vault / ".watchdog" / "Registry"
    (reg / "manifest.json").write_text(json.dumps({
        "ghost": {"name": "Ghost Co", "type": "Company", "aliases": [],
                  "note_path": "entities/company/ghost"},
    }))
    (reg / "entities.json").write_text("{}")
    (reg / "documents.json").write_text("{}")
    _write_queue(vault, "doc2", "Ghost Co appears here.")

    result = preflight.run(vault, "doc2")
    a = result["existing_entities"][0]
    assert a["id"] == "ghost"
    assert a["roles"] == []
    assert a["analysis"] == ""
    assert result["existing_timeline"] == []


def test_hoisted_timeline_dedups_event_shared_across_candidates(tmp_path):
    """`postflight.explode_key_facts` fans one dated key_fact onto every tagged entity's
    registry timeline, so a fact tagging N candidates would otherwise repeat N times in the
    prompt. Pre-flight hoists it into one shared entry tagging every candidate id it concerns,
    while an event unique to one entity stays its own single-id entry."""
    vault = _vault(tmp_path)
    reg = vault / ".watchdog" / "Registry"
    manifest = {
        "alice-smith": {"name": "Alice Smith", "type": "Person", "aliases": [], "note_path": ""},
        "bob-jones": {"name": "Bob Jones", "type": "Person", "aliases": [], "note_path": ""},
    }
    (reg / "manifest.json").write_text(json.dumps(manifest))
    entities = {
        "alice-smith": {"id": "alice-smith", "timeline_events": [
            {"date": "2020-03-15", "event": "Signed the merger agreement", "basis": "stated"},
            {"date": "2021-01-01", "event": "Alice-only event", "basis": "stated"},
        ]},
        "bob-jones": {"id": "bob-jones", "timeline_events": [
            {"date": "2020-03-15", "event": "Signed the merger agreement", "basis": "stated"},
        ]},
    }
    (reg / "entities.json").write_text(json.dumps(entities))
    (reg / "documents.json").write_text("{}")
    _write_queue(vault, "doc1", "Alice Smith and Bob Jones signed the deal.")

    result = preflight.run(vault, "doc1")
    by_key = {(e["date"], e["event"]): e for e in result["existing_timeline"]}

    shared = by_key[("2020-03-15", "Signed the merger agreement")]
    assert sorted(shared["entities"]) == ["alice-smith", "bob-jones"]

    unique = by_key[("2021-01-01", "Alice-only event")]
    assert unique["entities"] == ["alice-smith"]

    assert len(result["existing_timeline"]) == 2


def test_hoisted_timeline_keeps_same_text_different_dates_separate(tmp_path):
    """Dedup key is (date, event text) — the same wording on two different dates stays two
    entries, not one."""
    vault = _vault(tmp_path)
    reg = vault / ".watchdog" / "Registry"
    manifest = {
        "alice-smith": {"name": "Alice Smith", "type": "Person", "aliases": [], "note_path": ""},
    }
    (reg / "manifest.json").write_text(json.dumps(manifest))
    entities = {
        "alice-smith": {"id": "alice-smith", "timeline_events": [
            {"date": "2020-03-15", "event": "Filed the annual report", "basis": "stated"},
            {"date": "2021-03-15", "event": "Filed the annual report", "basis": "stated"},
        ]},
    }
    (reg / "entities.json").write_text(json.dumps(entities))
    (reg / "documents.json").write_text("{}")
    _write_queue(vault, "doc1", "Alice Smith filed again.")

    result = preflight.run(vault, "doc1")
    assert len(result["existing_timeline"]) == 2
    assert {e["date"] for e in result["existing_timeline"]} == {"2020-03-15", "2021-03-15"}


def test_known_document_types_collected_from_registry(tmp_path):
    """preflight surfaces the distinct document_types already in the vault so the extractor
    can reuse them (deduped, sorted; missing/empty types ignored)."""
    vault = _vault(tmp_path)
    _write_queue(vault, "sha-new", "Some new document text.")
    (vault / ".watchdog" / "Registry" / "documents.json").write_text(json.dumps({
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


# ── Candidate matching: word boundaries + alias floor (#216) ─────────────────────

def _manifest_vault(tmp_path: Path, manifest: dict, text: str, sha: str = "d") -> Path:
    # Fresh vault per call so a test can build several without colliding on the Registry dir.
    root = tempfile.mkdtemp(dir=tmp_path)
    vault = Path(root) / "vault"
    reg = vault / ".watchdog" / "Registry"
    reg.mkdir(parents=True)
    (vault / ".watchdog" / "queue").mkdir()
    (reg / "manifest.json").write_text(json.dumps(manifest))
    (reg / "entities.json").write_text("{}")
    (reg / "documents.json").write_text("{}")
    _write_queue(vault, sha, text)
    return vault


def _ids(pf: dict) -> set:
    return {e["id"] for e in pf["existing_entities"]}


def test_name_matches_only_on_word_boundary(tmp_path):
    """A short canonical name must match as a whole token, not buried inside a longer word —
    'Lee' appears in 'asleep' as a substring but is not a mention (#216)."""
    manifest = {"lee": {"name": "Lee", "type": "Person", "aliases": [], "note_path": ""}}
    assert _ids(preflight.run(_manifest_vault(tmp_path, manifest, "The guard was asleep."), "d")) == set()
    assert _ids(preflight.run(_manifest_vault(tmp_path, manifest, "Then Lee arrived."), "d")) == {"lee"}


def test_short_canonical_name_still_matches(tmp_path):
    """The floor is aliases-only: a real short *name* (BP, GE, 3M) matches at any length, on a
    boundary — 'BP' matches 'BP plc' but not 'subprime' or 'BPD'."""
    manifest = {"bp": {"name": "BP", "type": "Company", "aliases": [], "note_path": ""}}
    assert _ids(preflight.run(_manifest_vault(tmp_path, manifest, "Filed by BP plc today."), "d")) == {"bp"}
    assert _ids(preflight.run(_manifest_vault(tmp_path, manifest, "A subprime BPD matter."), "d")) == set()


def test_short_alias_below_floor_is_ignored(tmp_path):
    """A 2-char alias ('AG', initials for the entity) is below the default floor of 3 and never
    matches — even as a whole token — so it stops dragging the digest in on noisy hits."""
    manifest = {"ag": {"name": "Andrew Gordon", "type": "Person",
                       "aliases": ["AG"], "note_path": ""}}
    # 'AG' present as a whole token, canonical name absent → no candidate (alias below floor)
    assert _ids(preflight.run(_manifest_vault(tmp_path, manifest, "Signed by AG on Tuesday."), "d")) == set()
    # canonical name present → matches regardless of the alias floor
    assert _ids(preflight.run(_manifest_vault(tmp_path, manifest, "Andrew Gordon signed."), "d")) == {"ag"}


def test_alias_at_floor_matches_only_on_boundary(tmp_path):
    """An alias at/above the floor still has to clear the word-boundary test: 'Ana' matches
    'Ana Silva' but not 'banana'."""
    manifest = {"ana": {"name": "Ana Silva", "type": "Person",
                        "aliases": ["Ana"], "note_path": ""}}
    assert _ids(preflight.run(_manifest_vault(tmp_path, manifest, "We ate a banana."), "d")) == set()
    assert _ids(preflight.run(_manifest_vault(tmp_path, manifest, "Ana testified."), "d")) == {"ana"}


def test_alias_floor_override_via_param(tmp_path):
    """alias_min_length lets a caller (or config) lower the floor; at 1 a 2-char alias matches."""
    manifest = {"ag": {"name": "Andrew Gordon", "type": "Person",
                       "aliases": ["AG"], "note_path": ""}}
    vault = _manifest_vault(tmp_path, manifest, "Signed by AG on Tuesday.")
    assert _ids(preflight.run(vault, "d", alias_min_length=1)) == {"ag"}
    assert _ids(preflight.run(vault, "d", alias_min_length=3)) == set()


def test_non_latin_name_falls_back_to_substring(tmp_path):
    """Regex word boundaries can't segment unspaced scripts, so a non-ASCII-edged name falls back
    to plain substring — it must not match *less* than the old behavior for e.g. a CJK name in
    continuous text."""
    manifest = {"xi": {"name": "习近平", "type": "Person", "aliases": [], "note_path": ""}}
    vault = _manifest_vault(tmp_path, manifest, "国家主席习近平出席了会议。")
    assert _ids(preflight.run(vault, "d")) == {"xi"}


def test_digest_size_telemetry_reported(tmp_path):
    """preflight surfaces the prior-entity digest byte size and candidate count so cap sizes can
    be chosen from real data on a mature vault (#216)."""
    vault = _vault(tmp_path)
    _write_registry(vault)
    _write_queue(vault, "doc1", "Alice Smith is a director of Acme Corp.")

    pf = preflight.run(vault, "doc1")
    assert pf["existing_entities_count"] == len(pf["existing_entities"]) == 1
    expected = (len(json.dumps(pf["existing_entities"], ensure_ascii=False))
                + len(json.dumps(pf["existing_timeline"], ensure_ascii=False)))
    assert pf["existing_entities_bytes"] == expected
    assert pf["existing_entities_bytes"] > 0


def test_digest_size_telemetry_zero_when_no_candidates(tmp_path):
    vault = _vault(tmp_path)
    _write_registry(vault)
    _write_queue(vault, "doc1", "A document mentioning nobody in the registry.")

    pf = preflight.run(vault, "doc1")
    assert pf["existing_entities_count"] == 0
    assert pf["existing_entities_bytes"] == len(json.dumps([])) + len(json.dumps([]))


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
