"""Entity notes rendered from stored facts (D280): ordering, caps, marks, merges, rebuild."""

import json
from pathlib import Path

from watchdog.pipeline import entity_facts, entity_notes, verification


def _fact(i, *, sha="a" * 64, date=None, doc_date="2022-01-01", page=1, mark=None, basis="stated",
          title="Doc", note="documents/doc"):
    fid = verification.fact_ids(sha, [{"fact": f"Fact {i}.", "page": page}])[0]
    return {"id": fid, "fact": f"Fact {i}.", "page": page, "basis": basis, "date": date,
            "doc_date": doc_date, "sha": sha, "index": i, "title": title, "note": note,
            "morgue": "morgue/x/doc.pdf", "mark": mark, "entities": ["e"]}


def test_chronological_by_fact_date_then_document_date():
    facts = [
        _fact(1, doc_date="2023-05-01"),                       # undated fact in a 2023 document
        _fact(2, date="2021-03", doc_date="2023-05-01"),        # dated fact, earlier
        _fact(3, sha="b" * 64, doc_date=None),                  # undated everything: last
        _fact(4, date="2022-07-09", sha="c" * 64, doc_date="2020-01-01"),
    ]
    order = [f["fact"] for f in entity_facts.chronological(facts)]
    assert order == ["Fact 2.", "Fact 4.", "Fact 1.", "Fact 3."]


def test_fact_line_carries_source_page_mark_flags_and_block_id():
    f = _fact(1, date="2022-04-26", basis="inferred",
              mark={"status": "disputed", "by": "R", "at": "x"})
    f["figures_unverified"] = ["11200000"]
    text = entity_notes.facts_section([f])
    assert text.startswith("- **26 Apr 2022** — *(inferred)* Fact 1. — [[documents/doc|Doc]], "
                           "[[morgue/x/doc.pdf#page=1|p. 1]]")
    assert "11,200,000 not found in the document" in text
    assert "· ✗ disputed ^f-" in text
    assert entity_notes.mark_label(None) == "not checked"


def test_short_list_is_flat_long_list_groups_by_document_with_marked_facts_kept():
    flat = [_fact(i) for i in range(entity_notes.FLAT_MAX)]
    assert "###" not in entity_notes.facts_section(flat)

    many = []
    for d in range(3):
        sha = chr(ord("a") + d) * 64
        for i in range(20):
            mark = {"status": "verified"} if (d, i) == (0, 17) else None
            many.append(_fact(i, sha=sha, doc_date=f"202{d}-01-01", title=f"Doc {d}",
                              note=f"documents/doc-{d}", mark=mark))
    text = entity_notes.facts_section(many)
    assert text.startswith(f"*{3 * entity_notes.GROUP_FACTS + 1} of 60 facts shown")
    assert text.index("### 1 Jan 2020 · Doc 0") < text.index("### 1 Jan 2022 · Doc 2")
    assert "Fact 17." in text                     # a marked fact is never folded away
    assert "[[documents/doc-1|15 more facts in this document]]" in text


def test_older_documents_fold_to_one_line_but_keep_disputed_facts(monkeypatch):
    monkeypatch.setattr(entity_notes, "FULL_GROUPS", 2)
    facts = []
    for d in range(5):
        sha = f"{d:x}" * 64
        for i in range(10):
            mark = {"status": "disputed"} if (d, i) == (0, 3) else None
            facts.append(_fact(i, sha=sha, doc_date=f"201{d}-01-01", title=f"Doc {d}",
                               note=f"documents/doc-{d}", mark=mark))
    text = entity_notes.facts_section(facts)
    early = text.split("### Earlier documents", 1)[1].split("\n### ", 1)[0]
    assert "[[documents/doc-0|1 Jan 2010 · Doc 0]] — 10 facts" in early
    assert "Fact 3." in early and "✗ disputed" in early
    assert text.count("\n### ") == 2 + 1            # two full groups after the earlier list


def test_short_refs_are_unique_prefixes_of_the_fact_ids():
    facts = [_fact(i) for i in range(200)]
    refs = entity_facts.short_refs(facts)
    assert len(set(refs.values())) == 200
    for f in facts:
        assert f["id"].split(":")[3].startswith(refs[f["id"]][2:].split(":")[0])
        assert len(refs[f["id"]]) >= 6


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "v"
    (vault / ".watchdog" / "registry").mkdir(parents=True)
    (vault / ".watchdog" / "extracted").mkdir()
    return vault


def _write(vault, name, data):
    (vault / ".watchdog" / "registry" / name).write_text(json.dumps(data))


def test_a_tag_left_under_a_merged_away_id_follows_the_merge_log(tmp_path):
    """An extraction committed before a merge of two committed records still tags the old id; the
    survivor's facts include it, and an undone merge is not followed."""
    vault = _vault(tmp_path)
    sha = "d" * 64
    (vault / ".watchdog" / "extracted" / f"{sha}.json").write_text(json.dumps({
        "document": {"sha256": sha, "key_facts": [{"fact": "Old fact.", "page": 2,
                                                   "entities": ["old-id"]}]},
        "entities": [{"id": "old-id"}]}))
    ents = {"keep": {"id": "keep", "name": "K", "type": "person", "appears_in": [sha],
                     "note_path": "entities/person/keep"}}
    docs = {sha: {"title": "T", "document_note": "documents/t"}}
    merges = {"merges": [{"keep": {"id": "keep"}, "merged": {"id": "old-id"}}]}
    idx = entity_facts.FactIndex(vault, ents, docs, merges=merges, marks={})
    assert [f["fact"] for f in idx.facts_for("keep")] == ["Old fact."]
    merges["merges"][0]["undone"] = {"at": "now"}
    idx = entity_facts.FactIndex(vault, ents, docs, merges=merges, marks={})
    assert idx.facts_for("keep") == []


def test_reporter_notes_survive_every_render(tmp_path):
    vault = _vault(tmp_path)
    sha = "e" * 64
    (vault / ".watchdog" / "extracted" / f"{sha}.json").write_text(json.dumps({
        "document": {"sha256": sha, "key_facts": [{"fact": "A fact.", "page": 1, "entities": ["x"]}]},
        "entities": [{"id": "x"}]}))
    ents = {"x": {"id": "x", "name": "X", "type": "person", "appears_in": [sha],
                  "note_path": "entities/person/x"}}
    docs = {sha: {"title": "T", "document_note": "documents/t", "morgue_path": "morgue/t.pdf"}}
    _write(vault, "entities.json", ents)
    _write(vault, "documents.json", docs)
    note = vault / "entities" / "person" / "x.md"
    entity_notes.rebuild(vault, index_search=False)
    note.write_text(note.read_text().replace(
        "<!-- Journalist annotations — never overwritten by ingestion. -->", "Call the clerk.\n\n## My own heading\nmore"))
    entity_notes.rebuild(vault, index_search=False)
    text = note.read_text()
    assert text.endswith("## Notes\n\nCall the clerk.\n\n## My own heading\nmore\n")
    assert "- A fact. — [[documents/t|T]], [[morgue/t.pdf#page=1|p. 1]] · not checked" in text


def _marked_vault(tmp_path):
    vault = _vault(tmp_path)
    sha = "f" * 64
    (vault / ".watchdog" / "extracted" / f"{sha}.json").write_text(json.dumps({
        "document": {"sha256": sha, "key_facts": [{"fact": "Paid $5.", "page": 2, "entities": ["x"]}]},
        "entities": [{"id": "x"}]}))
    _write(vault, "entities.json", {"x": {"id": "x", "name": "X", "type": "person",
                                          "appears_in": [sha], "note_path": "entities/person/x"}})
    _write(vault, "documents.json", {sha: {"title": "T", "filename": "t.pdf",
                                           "document_note": "documents/t"}})
    entity_notes.rebuild(vault, index_search=False)
    fid = verification.fact_ids(sha, [{"fact": "Paid $5.", "page": 2}])[0]
    return vault, fid


def test_a_mark_refreshes_the_entity_note(tmp_path):
    vault, fid = _marked_vault(tmp_path)
    note = vault / "entities" / "person" / "x.md"
    assert "· not checked" in note.read_text()
    verification.mark(vault, fid, "verified", by="R")
    assert "Paid $5. — [[documents/t|T]], p. 2 · ✓ verified" in note.read_text()


def test_a_mark_during_a_commit_waits_for_that_commit(tmp_path):
    """A mark never waits on the registry lock (D271): while a commit holds it, the entity is
    remembered and the commit's next flush renders its note."""
    from watchdog.pipeline.write_vault import RegistryBatch, _registry_lock
    vault, fid = _marked_vault(tmp_path)
    note = vault / "entities" / "person" / "x.md"
    with _registry_lock(vault / ".watchdog" / "registry"):
        verification.mark(vault, fid, "disputed", by="R")
    assert "· not checked" in note.read_text()
    assert json.loads((vault / ".watchdog/registry" / entity_notes.STALE_FILE).read_text()) == ["x"]
    with RegistryBatch(vault) as batch:
        batch._pending = 1
        batch.flush()
    assert "· ✗ disputed" in note.read_text()
    assert not (vault / ".watchdog/registry" / entity_notes.STALE_FILE).exists()


def test_an_older_vault_is_upgraded_once(tmp_path):
    vault, _ = _marked_vault(tmp_path)
    note = vault / "entities" / "person" / "x.md"
    note.write_text("---\nid: x\n---\n\n# X\n\n## Summary\n\nOld prose.\n\n## Notes\n\nMine.\n")
    _write(vault, "registry.json", {"schema_version": "1"})
    assert entity_notes.needs_upgrade(vault)
    entity_notes.rebuild(vault, index_search=False)
    assert not entity_notes.needs_upgrade(vault)
    text = note.read_text()
    assert "## Summary (AI-written)\n\nOld prose." in text and "Paid $5." in text
    assert text.endswith("## Notes\n\nMine.\n")


def test_main_rebuilds_the_current_vault(tmp_path, monkeypatch):
    vault, _ = _marked_vault(tmp_path)
    (vault / "entities" / "person" / "x.md").unlink()
    monkeypatch.setattr("watchdog.vault_paths.is_vault", lambda p: True)
    assert entity_notes.main(["--vault", str(vault)]) == 0
    assert (vault / "entities" / "person" / "x.md").exists()
