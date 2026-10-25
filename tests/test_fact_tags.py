"""Fact tags that name no entity of their document (D333): the checked model chore, before the
fold, and how a re-tagged fact reaches the entity's page and timeline. Every name is invented."""

import asyncio
import json

from watchdog import model_client
from watchdog.pipeline import fact_tags, orchestrate, postflight, timeline
from watchdog.pipeline.entity_facts import FactIndex

from tests.test_orchestrate import _stage_extracted
from tests.test_write_vault import make_vault

SHA = "e" * 64


def _result(parsed):
    return model_client.ModelResult(parsed=parsed, text="", model="fake", backend="claude-api",
                                    auth_mode="api-key", usage={"input_tokens": 1, "output_tokens": 1},
                                    cost_usd=0.0, latency_s=0.0)


def _stage(vault, tmp_path):
    entities = [
        {"id": "calder-valley-university", "name": "Calder Valley University", "type": "organization",
         "aliases": []},
        {"id": "wynn-ostrander", "name": "Wynn Ostrander", "type": "person", "aliases": []}]
    facts = [
        {"fact": "CVU signed the lease on 3 March 2021.", "page": 1, "date": "2021-03-03",
         "entities": ["cvu", "wynn-ostrander"]},
        {"fact": "CVU paid the first instalment.", "page": 2, "entities": ["cvu"]},
        {"fact": "The trust was wound up.", "page": 2, "entities": ["the-trust"]},
        {"fact": "A bursar resigned.", "page": 3, "entities": ["bursar-office"]},
    ]
    art = _stage_extracted(vault, tmp_path / "x", SHA, "lease.pdf", overrides={
        "entities": entities, "morgue_entity_id": "calder-valley-university",
        "morgue_document_type": "lease", "document": {"key_facts": facts}})
    # As post-flight leaves it: the dangling tags dropped from the fan-out, timeline staged.
    ext = json.loads((vault / ".watchdog" / "extracted" / f"{SHA}.json").read_text(encoding="utf-8"))
    for e in ext["entities"]:
        e.pop("evidence_fragments", None)
        e.pop("timeline_events", None)
    assert len(postflight.explode_key_facts(ext)) == 4
    (vault / ".watchdog" / "extracted" / f"{SHA}.json").write_text(json.dumps(ext), encoding="utf-8")
    timeline.stage_timeline_events(vault, ext)
    return art


def _art(vault):
    return json.loads((vault / ".watchdog" / "extracted" / f"{SHA}.json").read_text(encoding="utf-8"))


def _answers(prompt):
    import re
    nums = {m.group(2): int(m.group(1)) for m in re.finditer(r'^(\d+)\. tag: "(.*)"$', prompt, re.M)}
    return {"answers": [
        {"item": nums["cvu"], "match": "calder-valley-university"},
        {"item": nums["the-trust"], "match": "some-trust-never-defined"},     # not in the document
        {"item": nums["bursar-office"], "match": ""},
    ]}


def test_the_pass_retags_rejects_and_leaves_none(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _stage(vault, tmp_path)
    seen = []

    async def fake(**kw):
        seen.append(kw)
        assert kw["task"] == "fact-tags" and kw["schema"] is fact_tags.SCHEMA
        assert '"CVU paid the first instalment."' in kw["prompt"]
        assert "calder-valley-university — \"Calder Valley University\"" in kw["prompt"]
        return _result(_answers(kw["prompt"]))

    monkeypatch.setattr(orchestrate, "_call_model", fake)
    out = asyncio.run(orchestrate._fact_tag_pass(vault, [SHA], None, "haiku", None, None))
    assert len(seen) == 1
    assert (out["tags"], out["dangling"], out["retagged"], out["facts"], out["rejected"], out["dropped"]) \
        == (5, 4, 1, 2, 1, 2)

    signed, paid, trust, bursar = _art(vault)["document"]["key_facts"]
    assert signed["entities"] == ["calder-valley-university", "wynn-ostrander"]
    assert signed["extracted_entities"] == ["cvu", "wynn-ostrander"]
    assert signed["entities_inferred"] == ["calder-valley-university"]
    assert paid["entities"] == ["calder-valley-university"]
    assert trust["entities"] == ["the-trust"] and trust["tags_unmatched"] == ["the-trust"]
    assert bursar["tags_unmatched"] == ["bursar-office"]

    # The re-tagged facts reach the entity's views and the staged timeline.
    uni = next(e for e in _art(vault)["entities"] if e["id"] == "calder-valley-university")
    assert {f["claim"] for f in uni["evidence_fragments"]} == {signed["fact"], paid["fact"]}
    assert [e["event"] for e in uni["timeline_events"]] == [signed["fact"]]
    raw = (vault / ".watchdog" / "timeline" / f"2021-03-03_{SHA[:7]}.ndjson").read_text(encoding="utf-8")
    assert "calder-valley-university" in json.loads(raw.splitlines()[0])["entity_ids"]

    log = (vault / ".watchdog" / "registry" / "processing.log").read_text(encoding="utf-8")
    assert "FACT TAGS 4 of 5 (80.0%) named no entity of their document; 1 matched by the model, 2 dropped" in log

    # Looked at already: the next finalize of the batch asks nothing.
    asyncio.run(orchestrate._fact_tag_pass(vault, [SHA], None, "haiku", None, None))
    assert len(seen) == 1


def test_a_retagged_fact_shows_its_link_as_inferred_on_the_entity_page(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _stage(vault, tmp_path)

    async def fake(**kw):
        return _result(_answers(kw["prompt"]))

    monkeypatch.setattr(orchestrate, "_call_model", fake)
    asyncio.run(orchestrate._fact_tag_pass(vault, [SHA], None, "haiku", None, None))
    orchestrate._batch_exact_fold(vault, [SHA])
    orchestrate._commit_pending(vault, [SHA])

    facts = FactIndex(vault).facts_for("calder-valley-university")
    assert {f["fact"] for f in facts} == {"CVU signed the lease on 3 March 2021.", "CVU paid the first instalment."}
    assert all(f["entities_inferred"] == ["calder-valley-university"] for f in facts)
    person = FactIndex(vault).facts_for("wynn-ostrander")
    assert person and person[0]["entities_inferred"] == ["calder-valley-university"]

    from watchdog.pipeline import entity_notes
    entity_notes.refresh(vault, {"calder-valley-university", "wynn-ostrander"})
    uni = next((vault / "entities").rglob("calder-valley-university.md")).read_text(encoding="utf-8")
    assert uni.count("*(link to this entity inferred)*") == 2
    who = next((vault / "entities").rglob("wynn-ostrander.md")).read_text(encoding="utf-8")
    assert "link to this entity inferred" not in who      # his own tag was in the document


def test_validate_accepts_only_a_defined_id():
    item = {"offered": ["a-co"]}
    assert fact_tags.validate(item, {"match": "a-co"}) == "a-co"
    assert fact_tags.validate(item, {"match": " "}) == ""
    assert fact_tags.validate(item, {"match": "b-co"}) is None
    assert fact_tags.validate(item, {"match": None}) is None


def test_no_call_when_no_tag_dangles(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _stage_extracted(vault, tmp_path / "x", SHA, "lease.pdf", overrides={
        "entities": [{"id": "a-co", "name": "A Co", "type": "organization"}],
        "morgue_entity_id": "a-co", "morgue_document_type": "lease",
        "document": {"key_facts": [{"fact": "A Co filed.", "page": 1, "entities": ["a-co"]}]}})

    async def never(**kw):
        raise AssertionError("no call expected")

    monkeypatch.setattr(orchestrate, "_call_model", never)
    out = asyncio.run(orchestrate._fact_tag_pass(vault, [SHA], None, "haiku", None, None))
    assert (out["tags"], out["dangling"], out["dropped"]) == (1, 0, 0)


def test_a_failed_call_leaves_the_tags_for_the_next_run(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _stage(vault, tmp_path)

    async def boom(**kw):
        raise model_client.ModelError("down")

    monkeypatch.setattr(orchestrate, "_call_model", boom)
    out = asyncio.run(orchestrate._fact_tag_pass(vault, [SHA], None, "haiku", None, None))
    assert out["retagged"] == 0 and out["unanswered"] == 3
    assert not any(f.get("tags_unmatched") for f in _art(vault)["document"]["key_facts"])


def test_the_pass_counts_towards_the_finalize_estimate(tmp_path):
    from watchdog.pipeline import ingest_setup
    vault = make_vault(tmp_path)
    _stage(vault, tmp_path)
    est = ingest_setup.housekeeping_estimate(vault, [SHA])
    assert est["items"] == 3 and est["calls"] == 1 and est["est_tokens"] > 0
    assert "fact-tags" in orchestrate.FINALIZE_TASKS
