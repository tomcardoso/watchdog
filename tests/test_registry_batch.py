"""Tests for the commit pass's in-memory registry (#696): `write_vault.RegistryBatch` writes the
registries once per flush rather than per document, without changing what lands on disk or what
a crash leaves replayable; `write_vault.NameIndex` keeps the exact-name fold's lookup current
instead of rebuilding it per document."""

import json
import random

import pytest

from watchdog.pipeline import write_vault
from watchdog.pipeline.entity_norm import normalize_entity_name
from watchdog.pipeline.entity_type import canonical_type

from tests.test_write_vault import make_extraction, make_vault


def _extraction(tmp_path, n):
    sub = tmp_path / f"x{n}"
    sub.mkdir()
    return make_extraction(sub, {"document": {"sha256": f"sha{n}", "filename": f"doc{n}.pdf",
                                              "original_path": f"_INCOMING/doc{n}.pdf",
                                              "title": f"Doc {n}"}})


def _registry(vault, name):
    return json.loads((vault / ".watchdog" / "registry" / name).read_text())


def test_batched_commit_writes_the_same_registries_as_one_at_a_time(tmp_path):
    plain, batched = make_vault(tmp_path / "a"), make_vault(tmp_path / "b")
    paths = [_extraction(tmp_path, n) for n in range(3)]
    for p in paths:
        write_vault.run(p, plain, quiet=True)
    with write_vault.RegistryBatch(batched) as batch:
        for p in paths:
            write_vault.run(p, batched, quiet=True, batch=batch)
            batch.committed()

    def _stable(reg):   # timestamps differ between the two runs by construction
        stamps = ("ingested_at", "date_first_seen", "date_last_updated")
        return json.loads(json.dumps(reg), object_hook=lambda d: {
            k: v for k, v in d.items() if k not in stamps})
    for name in ("entities.json", "documents.json", "manifest.json"):
        assert _stable(_registry(plain, name)) == _stable(_registry(batched, name))
    assert _registry(batched, "registry.json")["document_count"] == 3


def test_registry_reaches_disk_only_at_flush_and_after_flush_runs_then(tmp_path):
    vault = make_vault(tmp_path)
    unlinked = []
    with write_vault.RegistryBatch(vault, flush_every=2) as batch:
        write_vault.run(_extraction(tmp_path, 0), vault, quiet=True, batch=batch)
        batch.committed(after_flush=lambda: unlinked.append(0))
        assert _registry(vault, "documents.json") == {}       # not committed yet
        assert unlinked == []
        write_vault.run(_extraction(tmp_path, 1), vault, quiet=True, batch=batch)
        batch.committed(after_flush=lambda: unlinked.append(1))
        assert set(_registry(vault, "documents.json")) == {"sha0", "sha1"}
        assert unlinked == [0, 1]
        write_vault.run(_extraction(tmp_path, 2), vault, quiet=True, batch=batch)
        batch.committed(after_flush=lambda: unlinked.append(2))
    assert set(_registry(vault, "documents.json")) == {"sha0", "sha1", "sha2"}
    assert unlinked == [0, 1, 2]


def test_a_crash_between_flushes_commits_nothing_unflushed(tmp_path):
    """The registry persist is still the commit point (D67): an exception escaping the batch
    skips the final flush, so the unflushed document stays uncommitted and replayable."""
    vault = make_vault(tmp_path)
    unlinked = []
    with pytest.raises(RuntimeError):
        with write_vault.RegistryBatch(vault, flush_every=50) as batch:
            write_vault.run(_extraction(tmp_path, 0), vault, quiet=True, batch=batch)
            batch.committed(after_flush=lambda: unlinked.append(0))
            raise RuntimeError("crash")
    assert _registry(vault, "documents.json") == {}
    assert unlinked == []


def test_rollback_undoes_a_failed_documents_registry_edits(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    with write_vault.RegistryBatch(vault) as batch:
        write_vault.run(_extraction(tmp_path, 0), vault, quiet=True, batch=batch)
        batch.committed()
        before = json.loads(json.dumps((batch.entities, batch.documents)))

        def boom(*a, **k):
            raise OSError("disk full")
        monkeypatch.setattr(write_vault, "_build_document_note", boom)
        (tmp_path / "x9").mkdir()
        extra = make_extraction(tmp_path / "x9", {
            "document": {"sha256": "sha9", "filename": "doc9.pdf",
                         "original_path": "_INCOMING/doc9.pdf"},
            "entities": [{"id": "acme-corp", "name": "Acme Corp", "type": "organization",
                          "aliases": ["Acme New Alias"], "roles": []},
                         {"id": "bob-new", "name": "Bob New", "type": "Person",
                          "roles": [{"relationship": "Officer of", "target_id": "acme-corp"}]}]})
        with pytest.raises(OSError):
            write_vault.run(extra, vault, quiet=True, batch=batch)
        batch.rollback()
        assert json.loads(json.dumps((batch.entities, batch.documents))) == before


# ── NameIndex ─────────────────────────────────────────────────────────────────

def _rebuilt(entities_reg):
    index = {}
    for eid, entry in entities_reg.items():
        for n in [entry["name"], *entry.get("aliases", [])]:
            index.setdefault((normalize_entity_name(n), canonical_type(entry["type"])), eid)
    return index


def test_name_index_matches_a_rebuild_as_entities_are_added_and_gain_aliases():
    rng = random.Random(696)
    names = ["Acme Corp", "ACME", "Acme Corporation", "Bob Smith", "Smith, Bob", "Ontario",
             "Bank of Nova Scotia", "Scotiabank", "Jane Doe", "Doe Holdings"]
    types = ["Person", "organization", "company", "place"]
    reg: dict = {}
    index = write_vault.NameIndex(reg)
    for step in range(300):
        if reg and rng.random() < 0.4:
            eid = rng.choice(list(reg))
            reg[eid].setdefault("aliases", []).append(rng.choice(names))
        else:
            eid = f"e{step}"
            reg[eid] = {"name": rng.choice(names), "type": rng.choice(types), "aliases": []}
        index.add(eid, reg[eid])
        want = _rebuilt(reg)
        assert all(index.get(k) == v for k, v in want.items())
