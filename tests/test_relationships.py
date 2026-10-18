"""Relationship wordings grouped per pair of entities (D291): code normalization, the model's
groups checked and logged by code, distinct relationships kept apart, the reporter's split."""

import asyncio
import json

import pytest

from watchdog.pipeline import entity_notes, history, relationships, schemas
from watchdog.pipeline.relationships import normalize


@pytest.mark.parametrize("a, b", [
    ("Director of", "director of"),
    ("the director of", "Director of"),
    ("Director, of", "director  of"),
    ("is a partner at", "Partner at"),
    ("Partners at", "partner at"),
    ("Director of Procurement & Real Property at", "Director of Procurement and Real Property at"),
    ("Sister-in-law of", "sister in law of"),
])
def test_normalize_folds_only_trivial_differences(a, b):
    assert normalize(a) == normalize(b)


@pytest.mark.parametrize("a, b", [
    ("counsel to", "counsel for"),
    ("counsel for", "counsel against"),
    ("director of", "former director of"),
    ("director of", "was director of"),
    ("partner at", "senior partner at"),
    ("lender to", "borrower from"),
    ("address of", "addressee of"),
])
def test_normalize_keeps_real_differences(a, b):
    assert normalize(a) != normalize(b)


S1, S2, S3 = "1" * 64, "2" * 64, "3" * 64


def _role(label, target, sha, page=1, reverse=False):
    return {"relationship": label, "target_id": target, "page": page, "basis": "stated",
            "source_sha256": sha, "is_reverse": reverse}


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "v"
    reg = v / ".watchdog" / "registry"
    reg.mkdir(parents=True)
    ents = {
        "ann-vale": {"id": "ann-vale", "name": "Ann Vale", "type": "person",
                     "appears_in": [S1, S2, S3], "note_path": "entities/person/ann-vale",
                     "roles": [_role("Counsel with", "orme-lake", S1, 2),
                               _role("lawyer at", "orme-lake", S2, 4),
                               _role("articling student at", "orme-lake", S3, 1)]},
        "orme-lake": {"id": "orme-lake", "name": "Orme Lake LLP", "type": "organization",
                      "appears_in": [S1, S2, S3], "note_path": "entities/organization/orme-lake",
                      "roles": [_role("Counsel with", "ann-vale", S1, 2, reverse=True)]},
    }
    docs = {s: {"sha256": s, "title": f"Doc {i}", "filename": f"d{i}.pdf",
                "document_note": f"documents/d{i}"} for i, s in enumerate((S1, S2, S3), 1)}
    (reg / "entities.json").write_text(json.dumps(ents))
    (reg / "documents.json").write_text(json.dumps(docs))
    return v


def _item(view):
    items = view.pending([S2])
    assert len(items) == 1
    return items


def test_pending_lists_touched_pairs_with_two_or_more_wordings(vault):
    view = relationships.View(vault)
    items = view.pending([S2])
    assert [(i["from"], i["to"]) for i in items] == [("ann-vale", "orme-lake")]
    assert sorted(lab["key"] for lab in items[0]["labels"]) == [
        "articling student at", "counsel with", "lawyer at"]
    assert view.pending(["9" * 64]) == []          # a pair no document of the batch touched


def test_a_model_group_is_checked_logged_and_shown_under_its_canonical_label(vault):
    items = relationships.View(vault).pending([S2])
    nums = {lab["key"]: n for n, lab in enumerate(items[0]["labels"], 1)}
    answer = {"groups": [{"labels": [nums["counsel with"], nums["lawyer at"]],
                          "canonical": nums["lawyer at"], "reason": "same job"}]}
    res = relationships.apply(vault, items, {0: answer}, model="m", run="r1")
    assert len(res["grouped"]) == 1 and res["entities"] == {"ann-vale", "orme-lake"}

    log = relationships.load(vault)
    assert log["schema_version"] == relationships.SCHEMA_VERSION
    g = log["groups"][0]
    assert g["keys"] == ["counsel with", "lawyer at"] and g["canonical"] == "lawyer at"
    assert g["decided_by"] == "model" and g["model"] == "m" and g["run"] == "r1"
    assert g["status"] == "active" and g["reason"] == "same job"
    assert sorted(log["evaluated"]["ann-vale|orme-lake"]) == [
        "articling student at", "counsel with", "lawyer at"]

    view = relationships.View(vault)
    rows = view.for_entity("ann-vale")
    assert [(r["label"], r["direction"]) for r in rows] == [
        ("lawyer at", "out"), ("articling student at", "out")]
    lawyer = rows[0]
    assert sorted(w["text"] for w in lawyer["wordings"]) == ["Counsel with", "lawyer at"]
    assert lawyer["docs"] == [S1, S2] and lawyer["group"] == g["id"]
    assert view.canonical("ann-vale", "orme-lake", "Counsel With") == "lawyer at"
    assert view.pending([S2]) == []                # every wording has been put to the model


@pytest.mark.parametrize("answer", [
    None,                                                              # call failed: no answer
    {},                                                                # nothing to group
    {"groups": [{"labels": [1], "canonical": 1, "reason": ""}]},       # one label
    {"groups": [{"labels": [1, 9], "canonical": 1, "reason": ""}]},    # out of range
    {"groups": [{"labels": [1, 2], "canonical": 3, "reason": ""}]},    # canonical not a member
    {"groups": [{"labels": ["1", "2"], "canonical": 1, "reason": ""}]},
])
def test_distinct_stays_distinct_when_the_model_is_unsure_or_wrong(vault, answer):
    items = relationships.View(vault).pending([S2])
    answers = {} if answer is None else {0: answer}
    res = relationships.apply(vault, items, answers)
    assert res["grouped"] == []
    rows = relationships.View(vault).for_entity("ann-vale")
    assert len(rows) == 3 and all(r["group"] is None for r in rows)
    # A failed call is asked again next run; an answer, even an empty one, is recorded.
    assert bool(relationships.View(vault).pending([S2])) == (answer is None)


def test_a_reporter_split_keeps_wordings_apart_for_good(vault):
    items = relationships.View(vault).pending([S2])
    nums = {lab["key"]: n for n, lab in enumerate(items[0]["labels"], 1)}
    pair = [nums["counsel with"], nums["lawyer at"]]
    relationships.apply(vault, items, {0: {"groups": [{"labels": pair, "canonical": pair[0],
                                                        "reason": ""}]}})
    gid = relationships.load(vault)["groups"][0]["id"]
    entry = relationships.split(vault, gid, by="Reporter")
    assert entry["status"] == "split" and entry["split_by"] == "Reporter" and entry["split_at"]
    assert all(r["group"] is None for r in relationships.View(vault).for_entity("ann-vale"))
    with pytest.raises(LookupError):
        relationships.split(vault, gid)
    # A later model answer joining the same two wordings is refused by code.
    res = relationships.apply(vault, items, {0: {"groups": [{"labels": pair, "canonical": pair[1],
                                                              "reason": ""}]}})
    assert res["grouped"] == [] and res["dropped"] == 1
    assert history.label({"kind": "relationship_split"}).startswith("Relationship wordings")


def test_a_new_decision_supersedes_the_models_earlier_group(vault):
    items = relationships.View(vault).pending([S2])
    nums = {lab["key"]: n for n, lab in enumerate(items[0]["labels"], 1)}
    pair = [nums["counsel with"], nums["lawyer at"]]
    relationships.apply(vault, items, {0: {"groups": [{"labels": pair, "canonical": pair[0],
                                                        "reason": ""}]}})
    relationships.apply(vault, items, {0: {}})
    groups = relationships.load(vault)["groups"]
    assert [g["status"] for g in groups] == ["superseded"] and groups[0]["superseded_at"]


def test_a_newer_log_is_read_but_never_written(vault):
    path = relationships.path(vault)
    path.write_text(json.dumps({"schema_version": relationships.SCHEMA_VERSION + 1,
                                "groups": [], "evaluated": {}}))
    items = relationships.View(vault).pending([S2])
    res = relationships.apply(vault, items, {0: {}})
    assert res.get("too_new") and json.loads(path.read_text())["evaluated"] == {}
    assert "relationships.json" in history.REGISTRY_FILES


def test_merged_ids_follow_the_merge_log(vault):
    """A group recorded under an id later merged away still applies to the survivor."""
    reg = vault / ".watchdog" / "registry"
    (reg / relationships.LOG_FILE).write_text(json.dumps({
        "schema_version": 1, "evaluated": {},
        "groups": [{"id": "rel:a", "from": "a-vale", "to": "orme-lake", "status": "active",
                    "keys": ["counsel with", "lawyer at"], "canonical": "lawyer at"}]}))
    (reg / "merges.json").write_text(json.dumps({"schema_version": 1, "candidates": {}, "merges": [
        {"keep": {"id": "ann-vale"}, "merged": {"id": "a-vale"}}]}))
    rows = relationships.View(vault).for_entity("ann-vale")
    assert rows[0]["label"] == "lawyer at" and len(rows) == 2


def test_note_lists_one_line_per_counterpart_and_meaning(vault):
    items = relationships.View(vault).pending([S2])
    nums = {lab["key"]: n for n, lab in enumerate(items[0]["labels"], 1)}
    pair = [nums["counsel with"], nums["lawyer at"]]
    relationships.apply(vault, items, {0: {"groups": [{"labels": pair, "canonical": pair[1],
                                                        "reason": ""}]}})
    entity_notes.rebuild(vault, documents_too=False, index_search=False)
    text = (vault / "entities" / "person" / "ann-vale.md").read_text()
    section = text.split("## Relationships", 1)[1].split("## Notes", 1)[0]
    lines = [ln for ln in section.splitlines() if ln.startswith("- ")]
    assert len(lines) == 2
    assert lines[0].startswith("- lawyer at [[entities/organization/orme-lake|Orme Lake LLP]]")
    assert "[[documents/d1|Doc 1]], p. 2; [[documents/d2|Doc 2]], p. 4" in lines[0]
    assert 'as written: "Counsel with", "lawyer at"' in lines[0]
    assert lines[1].startswith("- articling student at") and "as written" not in lines[1]
    org = (vault / "entities" / "organization" / "orme-lake.md").read_text()
    assert "- [[entities/person/ann-vale|Ann Vale]] — lawyer at" in org


def test_finalizer_step_groups_and_logs_and_survives_a_failed_call(vault, monkeypatch):
    from watchdog import model_client
    from watchdog.pipeline import orchestrate

    seen = []

    async def fake(**kw):
        seen.append(kw)
        assert kw["task"] == "relationship-labels" and kw["schema"] is schemas.RELATIONSHIP_LABELS
        text = kw["prompt"]
        assert 'P1: Ann Vale (person) → Orme Lake LLP (organization)' in text
        nums = {}
        for line in text.split("\nPairs:\n", 1)[1].splitlines():
            if line.startswith("  "):
                n, label = line.strip().split(". ", 1)
                nums[label.split('"')[1]] = int(n)
        return model_client.ModelResult(
            parsed={"pairs": [{"pair": 1, "groups": [
                {"labels": [nums["Counsel with"], nums["lawyer at"]],
                 "canonical": nums["lawyer at"], "reason": "same"}]}]},
            text="", model="fake-model", backend="claude-api", auth_mode="api-key",
            usage={"input_tokens": 10, "output_tokens": 5}, cost_usd=0.0, latency_s=0.1)

    monkeypatch.setattr(orchestrate, "_call_model", fake)
    n = asyncio.run(orchestrate._relationship_labels(vault, [S2], "haiku", None, None))
    assert n == 1 and len(seen) == 1
    assert relationships.load(vault)["groups"][0]["model"] == "fake-model"
    # Nothing new to compare: no call at all.
    assert asyncio.run(orchestrate._relationship_labels(vault, [S2], "haiku", None, None)) == 0
    assert len(seen) == 1


def test_finalizer_step_failure_leaves_the_pair_for_the_next_run(vault, monkeypatch):
    from watchdog import model_client
    from watchdog.pipeline import orchestrate

    async def boom(**kw):
        raise model_client.ModelError("down")

    monkeypatch.setattr(orchestrate, "_call_model", boom)
    assert asyncio.run(orchestrate._relationship_labels(vault, [S2], "haiku", None, None)) == 0
    assert relationships.load(vault)["evaluated"] == {}
    assert relationships.View(vault).pending([S2])


def test_schema_shape():
    item = schemas.RELATIONSHIP_LABELS["properties"]["pairs"]["items"]
    assert item["required"] == ["pair", "groups"]
    assert item["properties"]["groups"]["items"]["required"] == ["labels", "canonical", "reason"]
