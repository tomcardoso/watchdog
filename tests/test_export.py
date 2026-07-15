import argparse
import csv
import json
import re
from pathlib import Path

import pytest

from watchdog.cmd.export import (
    _cypher_label,
    _forward_edges,
    _write_csv,
    _write_cypher,
    cmd_export,
)


def _strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


def _entities():
    """Two profiled entities plus a dangling target and a reverse edge."""
    return {
        "alice": {
            "id": "alice", "name": "Alice Smith", "type": "person",
            "appears_in": ["sha1", "sha2"],
            "roles": [
                {"relationship": "Director", "target_id": "acme", "target_type": "company",
                 "target_name": "Acme Ltd", "page": 3, "basis": "stated",
                 "date_range": "2019–2023", "source_sha256": "sha1", "is_reverse": False},
                # target never profiled -> dangling, must be dropped
                {"relationship": "Associate", "target_id": "ghost", "page": None,
                 "basis": "inferred", "date_range": None, "source_sha256": "sha1",
                 "is_reverse": False},
            ],
        },
        "acme": {
            "id": "acme", "name": "Acme Ltd", "type": "company",
            "appears_in": ["sha1"],
            "roles": [
                # auto-generated reverse of alice->acme, must be dropped
                {"relationship": "Director", "target_id": "alice", "target_type": "person",
                 "target_name": "Alice Smith", "page": 3, "basis": "stated",
                 "date_range": "2019–2023", "source_sha256": "sha1", "is_reverse": True},
            ],
        },
    }


# ── _forward_edges ──────────────────────────────────────────────────────────────

def test_forward_edges_excludes_reverse_and_dangling():
    edges, dangling = _forward_edges(_entities())
    assert dangling == 1                       # alice -> ghost
    assert len(edges) == 1                      # only alice -> acme survives
    e = edges[0]
    assert (e["start"], e["end"], e["type"]) == ("alice", "acme", "Director")
    assert e["basis"] == "stated"
    assert e["date_range"] == "2019–2023"


# ── CSV output ──────────────────────────────────────────────────────────────────

def test_write_csv_contents(tmp_path):
    entities = _entities()
    edges, _ = _forward_edges(entities)
    _write_csv(entities, edges, tmp_path)

    nodes = list(csv.reader((tmp_path / "nodes.csv").open(encoding="utf-8")))
    assert nodes[0] == [":ID", "name", ":LABEL", "type", "doc_count:int"]
    by_id = {r[0]: r for r in nodes[1:]}
    assert by_id["alice"] == ["alice", "Alice Smith", "person", "person", "2"]
    assert by_id["acme"] == ["acme", "Acme Ltd", "company", "company", "1"]

    rels = list(csv.reader((tmp_path / "relationships.csv").open(encoding="utf-8")))
    assert rels[0] == [":START_ID", ":END_ID", ":TYPE", "source_page:int", "basis", "date_range"]
    assert len(rels) == 2                                  # header + one edge
    assert rels[1] == ["alice", "acme", "Director", "3", "stated", "2019–2023"]


def test_write_csv_null_page_is_blank(tmp_path):
    entities = {
        "a": {"id": "a", "name": "A", "type": "person", "appears_in": ["s"],
              "roles": [{"relationship": "Knows", "target_id": "b", "page": None,
                         "basis": "stated", "date_range": None, "is_reverse": False}]},
        "b": {"id": "b", "name": "B", "type": "person", "appears_in": ["s"], "roles": []},
    }
    edges, _ = _forward_edges(entities)
    _write_csv(entities, edges, tmp_path)
    rels = list(csv.reader((tmp_path / "relationships.csv").open(encoding="utf-8")))
    assert rels[1] == ["a", "b", "Knows", "", "stated", ""]


# ── Cypher output ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("type_, expected", [
    ("person", "person"),
    ("non-profit org", "non_profit_org"),
    ("", "Entity"),
    ("123", "123"),
])
def test_cypher_label(type_, expected):
    assert _cypher_label(type_) == expected


def test_write_cypher(tmp_path):
    entities = _entities()
    edges, _ = _forward_edges(entities)
    path = _write_cypher(entities, edges, tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "MERGE (n:`person` {id: 'alice'})" in text
    assert "n.doc_count = 2" in text
    assert "MERGE (a)-[r:`DIRECTOR`]->(b)" in text
    assert "date_range: '2019–2023'" in text
    # reverse + dangling edges are not emitted
    assert text.count("MERGE (a)-[r:") == 1


def test_write_cypher_escapes_quotes(tmp_path):
    entities = {"x": {"id": "x", "name": "O'Brien", "type": "person",
                      "appears_in": ["s"], "roles": []}}
    path = _write_cypher(entities, [], tmp_path)
    assert r"n.name = 'O\'Brien'" in path.read_text(encoding="utf-8")


# ── cmd_export integration ──────────────────────────────────────────────────────

def _vault_with_entities(tmp_path, entities) -> Path:
    vault = tmp_path / "vault"
    reg = vault / ".watchdog" / "registry"
    reg.mkdir(parents=True)
    (reg / "entities.json").write_text(json.dumps(entities), encoding="utf-8")
    return vault


def _args(**kw):
    return argparse.Namespace(**{"project": None, "output": None, "format": "csv", **kw})


def test_cmd_export_writes_files(tmp_path, monkeypatch, capsys):
    vault = _vault_with_entities(tmp_path, _entities())
    monkeypatch.chdir(vault)
    out = tmp_path / "out"
    cmd_export(_args(output=str(out)))

    assert (out / "nodes.csv").exists()
    assert (out / "relationships.csv").exists()
    printed = _strip_ansi(capsys.readouterr().out)
    assert "2 nodes" in printed and "1 relationship" in printed
    assert "Skipped 1 relationship" in printed


def test_cmd_export_cypher_format(tmp_path, monkeypatch):
    vault = _vault_with_entities(tmp_path, _entities())
    monkeypatch.chdir(vault)
    out = tmp_path / "out"
    cmd_export(_args(output=str(out), format="cypher"))
    assert (out / "graph.cypher").exists()
    assert not (out / "nodes.csv").exists()


def test_cmd_export_empty_registry_exits(tmp_path, monkeypatch):
    vault = _vault_with_entities(tmp_path, {})
    monkeypatch.chdir(vault)
    with pytest.raises(SystemExit):
        cmd_export(_args(output=str(tmp_path / "out")))


def test_cmd_export_missing_registry_exits(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / ".watchdog").mkdir(parents=True)
    monkeypatch.chdir(vault)
    with pytest.raises(SystemExit):
        cmd_export(_args(output=str(tmp_path / "out")))
