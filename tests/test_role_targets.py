"""Relationship targets named by an id no entity has (D332): code's exact matches, the checked
model chore, and how a model-made match is shown. Every name here is invented."""

import asyncio
import json

from watchdog import model_client
from watchdog.pipeline import leads, orchestrate, relationships, role_targets

from tests.test_orchestrate import _stage_extracted
from tests.test_write_vault import make_vault


def _result(parsed):
    return model_client.ModelResult(parsed=parsed, text="", model="fake", backend="claude-api",
                                    auth_mode="api-key", usage={"input_tokens": 1, "output_tokens": 1},
                                    cost_usd=0.0, latency_s=0.0)


def _ent(eid, name, typ, aliases=(), roles=()):
    return {"id": eid, "name": name, "type": typ, "aliases": list(aliases), "roles": list(roles)}


def _stage(vault, tmp_path, sha, entities):
    return _stage_extracted(vault, tmp_path / sha, sha, f"{sha}.pdf", overrides={
        "entities": entities, "morgue_entity_id": entities[0]["id"], "morgue_document_type": "filing",
        "document": {"key_facts": [{"fact": f"{e['name']} is named.", "page": 1, "entities": [e["id"]]}
                                   for e in entities]}})


def _art(vault, sha):
    return json.loads((vault / ".watchdog" / "extracted" / f"{sha}.json").read_text(encoding="utf-8"))


def _roles(vault, sha, eid):
    return next(e for e in _art(vault, sha)["entities"] if e["id"] == eid)["roles"]


def _commit(vault, shas):
    orchestrate._batch_exact_fold(vault, shas)
    orchestrate._commit_pending(vault, shas)


def _base(tmp_path):
    """A committed registry with a dredging company and a harbour board, then a staged document
    whose relationships name targets by ids no entity has."""
    vault = make_vault(tmp_path)
    _stage(vault, tmp_path, "sha-a", [
        _ent("larkspur-dredging-ltd", "Larkspur Dredging Ltd.", "organization", ["Larkspur"]),
        _ent("fenmoor-harbour-board", "Fenmoor Harbour Board", "public-body"),
        _ent("orrin-vale", "Orrin Vale", "person")])
    _commit(vault, ["sha-a"])
    _stage(vault, tmp_path, "sha-b", [
        _ent("tamsin-reed", "Tamsin Reed", "person", roles=[
            {"relationship": "director of", "target_id": "Larkspur Dredging Ltd"},     # exact, registry
            {"relationship": "partner at", "target_id": "QUILLON  LLP"},               # exact, own doc
            {"relationship": "reports to", "target_id": "harbour-brd"},                # for the model
            {"relationship": "advised", "target_id": "vale"},                          # wrong type
            {"relationship": "works with", "target_id": "unheard-of-co"},              # nothing
            {"relationship": "met", "target_id": "figure-3-label"}]),                  # unknown id
        _ent("quillon-llp", "Quillon LLP", "organization")])
    return vault


# ── code's own matches ───────────────────────────────────────────────────────────────────

def test_exact_matches_are_made_by_code_silently(tmp_path):
    vault = _base(tmp_path)
    batch = role_targets.Batch(vault, ["sha-b"])
    assert role_targets.resolve_exact(batch) == 2
    roles = _roles(vault, "sha-b", "tamsin-reed")
    assert roles[0]["target_id"] == "larkspur-dredging-ltd"
    assert roles[0]["extracted_target_id"] == "Larkspur Dredging Ltd"
    assert roles[1]["target_id"] == "quillon-llp"
    assert not any(r.get("target_basis") for r in roles)        # a tidy match is not a judgement
    left = {it["target"] for it in role_targets.pending_items(batch)}
    assert left == {"harbour-brd", "vale", "unheard-of-co", "figure-3-label"}


def test_an_id_matching_two_entities_is_left_for_the_model(tmp_path):
    vault = make_vault(tmp_path)
    _stage(vault, tmp_path, "sha-a", [
        _ent("wren-holdings", "Wren Holdings", "organization", ["Wren"]),
        _ent("wren-marine", "Wren Marine", "organization", ["Wren"]),
        _ent("ida-colt", "Ida Colt", "person", roles=[{"relationship": "owns", "target_id": "wren"}])])
    batch = role_targets.Batch(vault, ["sha-a"])
    assert role_targets.resolve_exact(batch) == 0
    (item,) = role_targets.pending_items(batch)
    assert {"wren-holdings", "wren-marine"} <= set(item["offered"])


def test_similarity_reads_shortened_forms_and_initials():
    ent = {"id": "fenmoor-harbour-board", "name": "Fenmoor Harbour Board", "aliases": []}
    assert role_targets.similarity("fenmoor", ent) == 1.0
    assert role_targets.similarity("fhb", ent) == 1.0
    assert role_targets.similarity("harbour-brd", ent) == 0.5
    assert role_targets.similarity("unheard-of-co", ent) == 0.0


# ── the model chore ──────────────────────────────────────────────────────────────────────

def _answers(prompt):
    """A canned model: the board for the shortened id, a person for a company id (wrong type),
    an id never offered for the diagram label, and nothing for the rest."""
    import re
    nums = {m.group(2): int(m.group(1)) for m in re.finditer(r'^(\d+)\. target id: "(.*)"$', prompt, re.M)}
    return {"answers": [
        {"item": nums["harbour-brd"], "match": "fenmoor-harbour-board", "kind": "public-body"},
        {"item": nums["vale"], "match": "orrin-vale", "kind": "organization"},
        {"item": nums["figure-3-label"], "match": "made-up-id", "kind": "organization"},
        {"item": nums["unheard-of-co"], "match": "", "kind": ""},
    ]}


def test_the_pass_maps_rejects_and_leaves_leads(tmp_path, monkeypatch):
    vault = _base(tmp_path)
    seen = []

    async def fake(**kw):
        seen.append(kw)
        assert kw["task"] == "role-targets" and kw["schema"] is role_targets.SCHEMA
        assert "Fenmoor Harbour Board" in kw["prompt"] and "named by: \"Tamsin Reed\"" in kw["prompt"]
        return _result(_answers(kw["prompt"]))

    monkeypatch.setattr(orchestrate, "_call_model", fake)
    out = asyncio.run(orchestrate._role_target_pass(vault, ["sha-b"], None, "haiku", None, None))
    assert len(seen) == 1
    assert (out["exact"], out["matched"], out["rejected"]) == (2, 1, 2)

    roles = {r["relationship"]: r for r in _roles(vault, "sha-b", "tamsin-reed")}
    board = roles["reports to"]
    assert (board["target_id"], board["extracted_target_id"], board["target_basis"]) == \
        ("fenmoor-harbour-board", "harbour-brd", "inferred")
    assert roles["advised"]["target_id"] == "vale"                  # a person is not an organization
    assert roles["met"]["target_id"] == "figure-3-label"           # an id it was never offered
    assert roles["works with"]["target_id"] == "unheard-of-co" and roles["works with"]["target_unmatched"]

    # Looked at already, rejected answers included: the next finalize of the batch asks nothing.
    assert all(roles[k].get("target_unmatched") for k in ("advised", "met"))
    asyncio.run(orchestrate._role_target_pass(vault, ["sha-b"], None, "haiku", None, None))
    assert len(seen) == 1

    orchestrate._commit_pending(vault, ["sha-b"])
    reg = json.loads((vault / ".watchdog" / "registry" / "entities.json").read_text(encoding="utf-8"))
    fwd = next(r for r in reg["tamsin-reed"]["roles"] if r["relationship"] == "reports to")
    assert fwd["target_name"] == "Fenmoor Harbour Board" and fwd["target_basis"] == "inferred"
    rev = next(r for r in reg["fenmoor-harbour-board"]["roles"] if r.get("is_reverse"))
    assert rev["target_basis"] == "inferred" and rev["extracted_target_id"] == "harbour-brd"

    unprofiled = {u["id"] for u in leads.scan(vault)["unprofiled"]}
    assert "harbour-brd" not in unprofiled
    assert {"vale", "unheard-of-co", "figure-3-label"} <= unprofiled

    row = next(r for r in relationships.View(vault).for_entity("tamsin-reed")
               if r["other"] == "fenmoor-harbour-board")
    assert row["target_inferred"] and row["target_texts"] == ["harbour-brd"]
    note = (vault / "entities" / "person" / "tamsin-reed.md").read_text(encoding="utf-8")
    assert "target inferred from “harbour-brd”" in note
    stated = next(r for r in relationships.View(vault).for_entity("tamsin-reed")
                  if r["other"] == "larkspur-dredging-ltd")
    assert not stated["target_inferred"]


def test_validate_checks_offer_self_and_type():
    item = {"offered": ["a-co", "b-person"], "target_type": None,
            "mentions": [{"id": "b-person"}],
            "document": [{"id": "a-co", "type": "Company"}, {"id": "b-person", "type": "person"}],
            "candidates": []}
    assert role_targets.validate(item, {"match": "a-co", "kind": "public-body"}) == "a-co"
    assert role_targets.validate(item, {"match": "a-co", "kind": "person"}) is None
    assert role_targets.validate(item, {"match": "a-co", "kind": "spaceship"}) is None
    assert role_targets.validate(item, {"match": "b-person", "kind": "person"}) is None   # itself
    assert role_targets.validate(item, {"match": "", "kind": ""}) == ""
    assert role_targets.validate(item, {"match": 3, "kind": ""}) is None
    typed = {**item, "target_type": "person"}
    assert role_targets.validate(typed, {"match": "a-co", "kind": "organization"}) is None


def test_a_failed_call_leaves_the_targets_for_the_next_run(tmp_path, monkeypatch):
    vault = _base(tmp_path)

    async def boom(**kw):
        raise model_client.ModelError("down")

    monkeypatch.setattr(orchestrate, "_call_model", boom)
    out = asyncio.run(orchestrate._role_target_pass(vault, ["sha-b"], None, "haiku", None, None))
    assert out["matched"] == 0 and out["unanswered"] == 4
    assert not any(r.get("target_unmatched") for r in _roles(vault, "sha-b", "tamsin-reed"))


def test_no_call_when_nothing_dangles(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _stage(vault, tmp_path, "sha-a", [
        _ent("ida-colt", "Ida Colt", "person", roles=[{"relationship": "owns", "target_id": "wren-co"}]),
        _ent("wren-co", "Wren Co", "organization")])

    async def never(**kw):
        raise AssertionError("no call expected")

    monkeypatch.setattr(orchestrate, "_call_model", never)
    out = asyncio.run(orchestrate._role_target_pass(vault, ["sha-a"], None, "haiku", None, None))
    assert out == {"exact": 0, "matched": 0, "unmatched": 0}


def test_the_pass_counts_towards_the_finalize_estimate(tmp_path):
    from watchdog.pipeline import ingest_setup
    vault = _base(tmp_path)
    est = ingest_setup.housekeeping_estimate(vault, ["sha-b"])
    assert est["items"] == 4 and est["calls"] == 1 and est["est_tokens"] > 0
    # An estimate writes nothing.
    assert _roles(vault, "sha-b", "tamsin-reed")[0]["target_id"] == "Larkspur Dredging Ltd"
    assert "role-targets" in orchestrate.FINALIZE_TASKS


def test_a_document_s_entities_are_listed_once_for_all_its_targets(tmp_path):
    vault = _base(tmp_path)
    batch = role_targets.Batch(vault, ["sha-b"])
    role_targets.resolve_exact(batch)
    items = role_targets.pending_items(batch)
    assert len(items) >= 2
    prompt = role_targets.build_prompt(items)
    assert prompt.count("Document — its entities:") == 1
    assert prompt.count("tamsin-reed —") == 1
    doc = [e["id"] for e in items[0]["document"]]
    for it in items:
        assert [e["id"] for e in it["document"]] == doc
        assert not set(doc) & {e["id"] for e in it["candidates"]}
        assert it["offered"][:len(doc)] == doc
