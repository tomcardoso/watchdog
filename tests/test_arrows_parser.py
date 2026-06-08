import json
import pytest
from pathlib import Path

from watchdog.pipeline.arrows_parser import slugify, parse_arrows


def _write(tmp_path, nodes, relationships=None):
    data = {"nodes": nodes, "relationships": relationships or []}
    p = tmp_path / "graph.json"
    p.write_text(json.dumps(data))
    return p


# ── slugify ──────────────────────────────────────────────────────────────────

def test_slugify_basic():
    assert slugify("John Smith") == "john-smith"

def test_slugify_special_chars():
    assert slugify("Acme Corp.") == "acme-corp"

def test_slugify_empty():
    assert slugify("") == ""


# ── deduplication ────────────────────────────────────────────────────────────

def test_duplicate_captions_get_unique_ids(tmp_path):
    nodes = [
        {"id": "n1", "caption": "John Smith", "labels": ["Person"], "properties": {}},
        {"id": "n2", "caption": "John Smith", "labels": ["Person"], "properties": {}},
    ]
    result = parse_arrows(_write(tmp_path, nodes))
    ids = [e["id"] for e in result["entities"]]
    assert len(set(ids)) == 2
    assert "john-smith" in ids
    assert "john-smith-1" in ids


def test_triple_duplicate_captions(tmp_path):
    nodes = [
        {"id": "n1", "caption": "Alice", "labels": [], "properties": {}},
        {"id": "n2", "caption": "Alice", "labels": [], "properties": {}},
        {"id": "n3", "caption": "Alice", "labels": [], "properties": {}},
    ]
    result = parse_arrows(_write(tmp_path, nodes))
    ids = [e["id"] for e in result["entities"]]
    assert len(set(ids)) == 3
    assert "alice" in ids
    assert "alice-1" in ids
    assert "alice-2" in ids


def test_no_duplicate_no_suffix(tmp_path):
    nodes = [
        {"id": "n1", "caption": "Alice", "labels": [], "properties": {}},
        {"id": "n2", "caption": "Bob",   "labels": [], "properties": {}},
    ]
    result = parse_arrows(_write(tmp_path, nodes))
    ids = [e["id"] for e in result["entities"]]
    assert "alice" in ids
    assert "bob" in ids
    assert not any("-1" in i for i in ids)


# ── relationships ─────────────────────────────────────────────────────────────

def test_relationships_use_resolved_ids(tmp_path):
    nodes = [
        {"id": "n1", "caption": "Alice", "labels": [], "properties": {}},
        {"id": "n2", "caption": "Bob",   "labels": [], "properties": {}},
    ]
    rels = [{"fromId": "n1", "toId": "n2", "type": "KNOWS", "properties": {}}]
    result = parse_arrows(_write(tmp_path, nodes, rels))
    assert result["relationships"][0]["from_id"] == "alice"
    assert result["relationships"][0]["to_id"] == "bob"


def test_relationship_with_duplicate_ids_still_resolves(tmp_path):
    nodes = [
        {"id": "n1", "caption": "John Smith", "labels": [], "properties": {}},
        {"id": "n2", "caption": "John Smith", "labels": [], "properties": {}},
    ]
    rels = [{"fromId": "n1", "toId": "n2", "type": "RELATED_TO", "properties": {}}]
    result = parse_arrows(_write(tmp_path, nodes, rels))
    rel = result["relationships"][0]
    assert rel["from_id"] != rel["to_id"]


def test_unknown_relationship_ids_skipped(tmp_path):
    nodes = [{"id": "n1", "caption": "Alice", "labels": [], "properties": {}}]
    rels = [{"fromId": "n1", "toId": "n99", "type": "KNOWS", "properties": {}}]
    result = parse_arrows(_write(tmp_path, nodes, rels))
    assert result["relationships"] == []
