"""The Review list (`watchdog.review`) and the near-duplicate stamp it reads (D252)."""

import json

import pytest

import watchdog.review as review
from watchdog.pipeline import orchestrate, resolutions

_CALLOUT = "> [!contradiction] Date of incorporation\n> 2019 per the filing vs 2020 per the registry"
_ALERTS = """
## 2026-10-03 16:12 — 2 matches (1 term, 2 documents)

### `offshore` — 2 documents

- [ ] **[[documents/memo|memo.pdf]]** <!--wid:alert:aaaaaaa:t1-->
  - p. 3: paid through an offshore account
- [ ] **[[documents/letter|letter.pdf]]** <!--wid:alert:bbbbbbb:t1-->
  - p. 1: an offshore subsidiary
"""


@pytest.fixture
def vault(tmp_path, monkeypatch):
    v = tmp_path / "probe"
    reg = v / ".watchdog" / "registry"
    reg.mkdir(parents=True)
    (v / "briefings").mkdir()
    (reg / "entities.json").write_text(json.dumps({
        "acme": {"name": "Acme", "note_path": "entities/organization/acme",
                 "roles": [{"target_id": "ghost", "target_name": "Ghost Co"}],
                 "contradictions": [_CALLOUT], "appears_in": ["s1"]}}))
    (reg / "documents.json").write_text(json.dumps({
        "a" * 64: {"filename": "memo-copy.pdf", "document_note": "documents/memo-copy",
                   "near_duplicate_of": "[[documents/memo|memo.pdf]]"},
        "b" * 64: {"filename": "memo.pdf", "document_note": "documents/memo",
                   "near_duplicate_of": None}}))
    (v / "briefings" / "alerts-2026-10-03.md").write_text(_ALERTS)
    monkeypatch.chdir(v)
    return v


def test_open_items_covers_every_kind(vault):
    items = review.open_items(vault)
    by_kind = {k: [i for i in items if i["kind"] == k] for k in review.KINDS}
    (c,) = by_kind["contradictions"]
    assert c["title"] == "Acme — Date of incorporation"
    assert c["detail"] == ["2019 per the filing vs 2020 per the registry"]
    assert c["note"] == "entities/organization/acme"
    assert c["rid"] == resolutions.contradiction_id(_CALLOUT)

    (lead,) = by_kind["leads"]
    assert lead["title"].startswith("Ghost Co") and lead["rid"] == "lead:unprofiled:ghost"

    a1, a2 = by_kind["alerts"]
    assert (a1["title"], a1["note"], a1["detail"]) == (
        "`offshore` in memo.pdf", "documents/memo", ["p. 3: paid through an offshore account"])
    assert a2["rid"] == "alert:bbbbbbb:t1"

    (d,) = by_kind["duplicates"]
    assert d["title"] == "memo-copy.pdf" and d["note"] == "documents/memo-copy"
    assert "Closely matches memo.pdf" in d["detail"][0]
    assert d["rid"] == resolutions.duplicate_id("a" * 64)


def test_resolved_items_drop_out(vault):
    resolutions.resolve(vault, ["alert:aaaaaaa:t1", resolutions.duplicate_id("a" * 64),
                                resolutions.contradiction_id(_CALLOUT)])
    items = review.open_items(vault)
    assert [i["rid"] for i in items] == ["lead:unprofiled:ghost", "alert:bbbbbbb:t1"]
    assert review.count_open_duplicates(vault) == 0


def test_an_alert_repeated_across_files_appears_once(vault):
    (vault / "briefings" / "alerts-2026-10-04.md").write_text(_ALERTS)
    assert len(review.open_items(vault, ("alerts",))) == 2


# ── near_duplicate_of stamp ──────────────────────────────────────────────────

@pytest.mark.parametrize("near_dup, expected", [
    (None, None),
    ({"near_duplicates": []}, None),
    ({"near_duplicates": [
        {"sha256": "1" * 64, "filename": "a.pdf", "similarity": 0.86, "document_note": "documents/a"},
        {"sha256": "2" * 64, "filename": "b.pdf", "similarity": 0.97, "document_note": "documents/b"},
    ]}, "[[documents/b|b.pdf]]"),
    ({"near_duplicates": [{"sha256": "3" * 64, "filename": "same|batch.pdf",
                           "similarity": 0.9, "document_note": ""}]}, "same-batch.pdf"),
])
def test_near_duplicate_of_names_the_closest_match(near_dup, expected):
    assert orchestrate._near_duplicate_of(near_dup) == expected


def test_stamp_document_records_the_near_duplicate():
    extraction = {"document": {}}
    pf = {"filename": "copy.pdf", "pages": ["x"], "near_dup": {"near_duplicates": [
        {"sha256": "1" * 64, "filename": "orig.pdf", "similarity": 0.9,
         "document_note": "documents/orig"}]}}
    orchestrate._stamp_document(extraction, sha="c" * 64, pf=pf, skill_label="generic")
    assert extraction["document"]["near_duplicate_of"] == "[[documents/orig|orig.pdf]]"


def test_an_unprofiled_lead_shows_who_names_it_and_where(vault):
    reg = vault / ".watchdog" / "registry" / "entities.json"
    reg.write_text(json.dumps({"harbour": {"name": "Harbour Holdings Ltd", "roles": [
        {"relationship": "director", "target_id": "a", "target_name": "a",
         "source_sha256": "b" * 64, "page": 3}]}}))
    (lead,) = review.open_items(vault, ("leads",))
    assert lead["title"] == "a — named but never profiled"
    assert lead["detail"][0] == "Harbour Holdings Ltd — director → a (p. 3)"
    assert "only as the id “a”" in lead["detail"][1]
    assert lead["cited"] == [{"sha": "b" * 64, "filename": "memo.pdf", "note": "documents/memo"}]


def test_a_named_unprofiled_lead_has_no_id_note(vault):
    (lead,) = review.open_items(vault, ("leads",))
    assert lead["detail"] == ["Acme → Ghost Co"]
    assert lead["cited"] == []
