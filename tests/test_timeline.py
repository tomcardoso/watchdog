import json
from pathlib import Path

from watchdog.pipeline.timeline import (
    stage_timeline_events,
    cmd_timeline_collisions,
    cmd_rebuild_timeline,
)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _vault(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    (v / ".watchdog").mkdir(parents=True)
    return v


def _extraction(entities: list[dict], sha: str = "abcdef1234567") -> dict:
    return {"document": {"sha256": sha}, "entities": entities}


def _read_ndjson(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


# ── stage_timeline_events ───────────────────────────────────────────────────

def test_stage_writes_one_file_per_date(tmp_path):
    vault = _vault(tmp_path)
    n = stage_timeline_events(vault, _extraction([
        {"id": "alice", "timeline_events": [
            {"date": "2020-03-15", "event": "Appointed director", "page": 2, "confidence": "high"},
            {"date": "2021", "event": "Resigned", "page": 5, "confidence": "medium"},
        ]},
    ], sha="abcdef1234567"))

    assert n == 2
    td = vault / ".watchdog" / "timeline"
    f1 = td / "2020-03-15_abcdef1.ndjson"
    f2 = td / "2021_abcdef1.ndjson"
    assert f1.exists() and f2.exists()
    assert _read_ndjson(f1)[0] == {
        "date": "2020-03-15",
        "event": "Appointed director",
        "source_sha256": "abcdef1234567",
        "entity_ids": ["alice"],
        "confidence": "high",
    }


def test_stage_dedups_same_event_across_entities(tmp_path):
    vault = _vault(tmp_path)
    stage_timeline_events(vault, _extraction([
        {"id": "alice", "timeline_events": [
            {"date": "2020-03-15", "event": "Acme filed for bankruptcy", "confidence": "high"}]},
        {"id": "acme", "timeline_events": [
            {"date": "2020-03-15", "event": "Acme filed for bankruptcy", "confidence": "high"}]},
    ], sha="sha12340000"))

    recs = _read_ndjson(vault / ".watchdog" / "timeline" / "2020-03-15_sha1234.ndjson")
    assert len(recs) == 1
    assert sorted(recs[0]["entity_ids"]) == ["acme", "alice"]


def test_stage_keeps_distinct_events_on_same_date(tmp_path):
    vault = _vault(tmp_path)
    stage_timeline_events(vault, _extraction([
        {"id": "alice", "timeline_events": [
            {"date": "2020-03-15", "event": "Event A", "confidence": "high"},
            {"date": "2020-03-15", "event": "Event B", "confidence": "low"}]},
    ], sha="zzzzzzz9999"))

    recs = _read_ndjson(vault / ".watchdog" / "timeline" / "2020-03-15_zzzzzzz.ndjson")
    assert {r["event"] for r in recs} == {"Event A", "Event B"}


def test_stage_skips_events_without_date_or_text(tmp_path):
    vault = _vault(tmp_path)
    n = stage_timeline_events(vault, _extraction([
        {"id": "alice", "timeline_events": [
            {"date": "", "event": "no date", "confidence": "high"},
            {"date": "2020", "event": "", "confidence": "high"},
            {"event": "missing date key", "confidence": "high"},
        ]},
    ]))
    assert n == 0
    td = vault / ".watchdog" / "timeline"
    assert not td.exists() or not list(td.glob("*.ndjson"))


def test_stage_returns_zero_without_sha(tmp_path):
    vault = _vault(tmp_path)
    assert stage_timeline_events(vault, {"document": {}, "entities": []}) == 0


# ── Integration with the collisions / rebuild flow ──────────────────────────

def test_stage_then_collisions_promotes_and_rebuild_renders(tmp_path, capsys):
    vault = _vault(tmp_path)
    stage_timeline_events(vault, _extraction([
        {"id": "alice", "timeline_events": [
            {"date": "2020-03-15", "event": "Appointed director", "confidence": "high"}]},
    ], sha="doc1xxxxxxx"))

    cmd_timeline_collisions(vault)
    assert capsys.readouterr().out.strip() == "[]"  # no canonical existed → promotion, no collision

    td = vault / ".watchdog" / "timeline"
    assert (td / "2020-03-15.ndjson").exists()  # canonical created from the raw file

    cmd_rebuild_timeline(vault)
    timeline_md = (vault / "timeline.md").read_text(encoding="utf-8")
    assert "2020-03-15" in timeline_md
    assert "Appointed director" in timeline_md


def test_stage_collision_reported_when_canonical_exists(tmp_path, capsys):
    vault = _vault(tmp_path)
    td = vault / ".watchdog" / "timeline"
    td.mkdir(parents=True)
    (td / "2020-03-15.ndjson").write_text(
        json.dumps({"date": "2020-03-15", "event": "Existing", "source_sha256": "old",
                    "entity_ids": [], "confidence": "high"}) + "\n",
        encoding="utf-8",
    )

    stage_timeline_events(vault, _extraction([
        {"id": "bob", "timeline_events": [
            {"date": "2020-03-15", "event": "New event", "confidence": "high"}]},
    ], sha="newdoc12345"))

    cmd_timeline_collisions(vault)
    collisions = json.loads(capsys.readouterr().out)
    assert len(collisions) == 1
    assert collisions[0]["date"] == "2020-03-15"


# ── post-flight wiring ──────────────────────────────────────────────────────

def _full_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    reg = vault / ".watchdog" / "Registry"
    reg.mkdir(parents=True)
    (vault / ".watchdog" / "tmp").mkdir()
    (vault / "_INCOMING").mkdir()
    (vault / "documents").mkdir()
    (reg / "entities.json").write_text("{}\n")
    (reg / "documents.json").write_text("{}\n")
    (reg / "registry.json").write_text(json.dumps({"document_count": 0, "entity_count": 0}) + "\n")
    (reg / "ingest.log").write_text("")
    return vault


def test_postflight_stages_timeline_files(tmp_path):
    from watchdog.pipeline.postflight import run as postflight_run

    vault = _full_vault(tmp_path)
    extraction = {
        "document": {
            "sha256": "post123abc",
            "filename": "doc.pdf",
            "original_path": "_INCOMING/doc.pdf",
            "title": "Doc",
            "document_type": "Report",
            "date_of_document": "2024-01-15",
            "page_count": 1,
            "summary": "x",
            "key_facts": [],
        },
        "entities": [
            {"id": "alice", "name": "Alice", "type": "Person", "aliases": [],
             "timeline_events": [{"date": "2020-03-15", "event": "Appointed", "confidence": "high"}],
             "roles": []},
        ],
        "morgue_entity_id": "alice",
        "morgue_document_type": "report",
    }
    ext_path = vault / ".watchdog" / "tmp" / "wdg_ex_post123abc.json"
    ext_path.write_text(json.dumps(extraction), encoding="utf-8")

    result = postflight_run(vault, ext_path)
    assert result.get("ok"), result

    raw = vault / ".watchdog" / "timeline" / "2020-03-15_post123.ndjson"
    assert raw.exists()
    assert _read_ndjson(raw)[0]["entity_ids"] == ["alice"]
