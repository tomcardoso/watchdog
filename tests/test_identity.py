"""Entity identity: merges are never silent, and people are never merged on weak evidence (D285).

The first two tests reproduce the design review's experiment: before D285 the exact-name fold
(`orchestrate._batch_exact_fold`) folded an incoming "J. Smith" into "John Smith" through an alias,
and merged a second, different "John Smith" outright because it shared a slug — in code, with no
model, no human and no record."""

import json

from watchdog.pipeline import orchestrate

from tests.test_orchestrate import _stage_extracted
from tests.test_write_vault import make_vault


def _person(eid, name, aliases=(), roles=()):
    return {"id": eid, "name": name, "type": "Person", "aliases": list(aliases),
            "summary": None, "timeline_events": [], "roles": list(roles)}


def _org(eid, name, aliases=()):
    return {"id": eid, "name": name, "type": "Company", "aliases": list(aliases),
            "summary": None, "timeline_events": [], "roles": []}


def _stage(vault, tmp_path, sha, entities, facts=None):
    """Stage one document naming `entities`; each fact is (text, [entity ids])."""
    facts = facts if facts is not None else [
        (f"{e['name']} is named in {sha}.", [e["id"]]) for e in entities]
    return _stage_extracted(vault, tmp_path / sha, sha, f"{sha}.pdf", overrides={
        "entities": entities,
        "morgue_entity_id": entities[0]["id"],
        "morgue_document_type": "filing",
        "document": {"key_facts": [
            {"fact": text, "page": 1, "basis": "stated", "entities": tags}
            for text, tags in facts]},
    })


def _registry(vault):
    return json.loads((vault / ".watchdog" / "registry" / "entities.json").read_text())


def _commit(vault, shas):
    orchestrate._batch_exact_fold(vault, shas)
    orchestrate._commit_pending(vault, shas)


# ── the review's reproduction ────────────────────────────────────────────────────────────────

def test_initials_are_not_folded_into_a_full_name_through_an_alias(tmp_path):
    vault = make_vault(tmp_path)
    _stage(vault, tmp_path, "sha-a", [_person("john-smith", "John Smith", ["J. Smith", "Mr. Smith"])])
    _commit(vault, ["sha-a"])

    _stage(vault, tmp_path, "sha-b", [_person("j-smith", "J. Smith")])
    _commit(vault, ["sha-b"])

    reg = _registry(vault)
    assert "j-smith" in reg, "an initialled name must not be folded into a full name by code"
    assert reg["john-smith"]["appears_in"] == ["sha-a"]


def test_a_second_john_smith_with_the_same_slug_is_not_merged_on_name_alone(tmp_path):
    vault = make_vault(tmp_path)
    _stage(vault, tmp_path, "sha-a", [_person("john-smith", "John Smith")])
    _commit(vault, ["sha-a"])

    _stage(vault, tmp_path, "sha-b", [_person("john-smith", "John Smith")])
    _commit(vault, ["sha-b"])

    reg = _registry(vault)
    smiths = [eid for eid, e in reg.items() if e["name"] == "John Smith"]
    assert len(smiths) == 2, "two people who share only a name must stay two entities"
    assert reg["john-smith"]["appears_in"] == ["sha-a"]


# ── the tiers, rule by rule ───────────────────────────────────────────────────────────────────

from watchdog.pipeline import identity, merge_log, merge_entities, resolutions  # noqa: E402
import asyncio  # noqa: E402
from watchdog import model_client  # noqa: E402


def _p(eid, name, etype="person", aliases=(), relations=(), identifiers=None):
    p = identity.Profile(eid, name, etype)
    for a in aliases:
        p.add_surface(a)
    for tid, rel, tname, ttype in relations:
        p.add_relation(tid, rel, tname, ttype)
    p.identifiers = {k: set(v) for k, v in (identifiers or {}).items()}
    return p


def _tier(a, b, dismissed=frozenset()):
    v = identity.classify(a, b, dismissed)
    return (v["tier"], v["rule"]) if v else None


def test_person_same_full_name_alone_is_medium():
    assert _tier(_p("a", "John Smith"), _p("b", "John Smith")) == ("medium", "same-name")


def test_person_same_full_name_in_another_order_or_with_a_title_is_still_the_same_name():
    assert _tier(_p("a", "Smith, John"), _p("b", "Mayor John Smith"))[1] == "same-name"


def test_person_same_full_name_and_shared_employer_is_high():
    acme = ("acme", "director of", "Acme Ltd.", "organization")
    v = identity.classify(_p("a", "John Smith", relations=[acme]),
                          _p("b", "John Smith", relations=[("acme", "Director", "Acme Ltd.", "organization")]))
    assert (v["tier"], v["rule"]) == ("high", "same-name-shared-attribute")
    assert v["evidence"]["shared"][0]["target_id"] == "acme"


def test_person_shared_street_address_is_high_but_a_shared_city_is_not():
    home = ("14-dock", "resides at", "14 Dockside Road", "place")
    city = ("pc", "resident of", "Port Calder", "place")
    assert _tier(_p("a", "John Smith", relations=[home]), _p("b", "John Smith", relations=[home]))[0] == "high"
    assert _tier(_p("a", "John Smith", relations=[city]), _p("b", "John Smith", relations=[city]))[0] == "medium"


def test_person_initials_and_partial_names_are_low():
    assert _tier(_p("a", "J. Smith"), _p("b", "John Smith")) == ("low", "partial-name")
    assert _tier(_p("a", "John Smith"), _p("b", "John A. Smith")) == ("low", "partial-name")
    assert _tier(_p("a", "Justice Okafor"), _p("b", "Justice R.T. Okafor")) == ("low", "partial-name")
    assert _tier(_p("a", "Mr. Pike"), _p("b", "Mr. Pike")) == ("low", "same-partial-name")
    assert _tier(_p("a", "John Smith"), _p("b", "John Smith Jr.")) == ("low", "partial-name")


def test_different_people_are_not_a_pair():
    assert _tier(_p("a", "John Smith"), _p("b", "Jane Smith")) is None
    assert _tier(_p("a", "John Smith Sr."), _p("b", "John Smith Jr.")) is None
    assert _tier(_p("a", "John Smith"), _p("b", "John Smith", etype="organization")) is None


def test_organization_distinctive_name_is_high_generic_name_is_medium():
    org = "organization"
    assert _tier(_p("a", "Northgate Civil Works Ltd.", org), _p("b", "Northgate Civil Works Ltd", org)) == \
        ("high", "distinctive-name")
    assert _tier(_p("a", "Acme Ltd.", org), _p("b", "Acme Ltd.", org))[0] == "high"
    assert _tier(_p("a", "Toronto-Dominion Bank", org), _p("b", "Toronto-Dominion Bank", org))[0] == "high"
    assert _tier(_p("a", "the City", "public-body"), _p("b", "The City", "public-body")) == \
        ("medium", "generic-name")
    # A record that calls itself only "Council" matches the specific one only through a generic
    # word: the model decides.
    assert _tier(_p("a", "Council", "public-body"),
                 _p("b", "Port Calder City Council", "public-body", aliases=["Council"])) == \
        ("medium", "generic-name")
    assert _tier(_p("a", "Port Calder City Council", "public-body", aliases=["Council"]),
                 _p("b", "Port Calder City Council", "public-body")) == ("high", "distinctive-name")


def test_a_generic_alias_alone_is_not_enough_for_a_high_merge():
    a = _p("a", "Council", "public-body")
    b = _p("b", "Westbrook Town Council", "public-body", aliases=["Council"])
    a2 = _p("a2", "Council", "public-body")
    assert _tier(a, a2)[0] == "medium"
    assert identity.classify(a, b)["evidence"]["surface"] in ("Council", "Westbrook Town Council")


def test_same_identifier_is_high_and_a_conflicting_one_rules_the_pair_out():
    org = "organization"
    a = _p("a", "Harbour Point Holdings", org, identifiers={"registration": {"BC1234567"}})
    b = _p("b", "Harbour Point Holdings Inc.", org, identifiers={"registration": {"BC1234567"}})
    c = _p("c", "Harbour Point Holdings Inc.", org, identifiers={"registration": {"BC7654321"}})
    assert _tier(a, b) == ("high", "same-identifier")
    assert _tier(b, c) is None


def test_identifiers_are_harvested_only_from_facts_about_one_entity():
    facts = [{"fact": "Acme Ltd. holds incorporation number BC1234567.", "entities": ["acme"]},
             {"fact": "Acme and Beta share registration no. 999999.", "entities": ["acme", "beta"]},
             {"fact": "Licence number 4471-A was issued to Acme.", "entities": ["acme"]}]
    ids = identity.harvest_identifiers("organization", ["Acme Ltd."], facts)
    assert ids == {"registration": {"BC1234567"}, "licence": {"4471A"}}
    assert identity.harvest_identifiers("person", ["X"], facts) == {"licence": {"4471A"}}
    assert identity.harvest_identifiers("proceeding", ["Court File JR-2022-0481"], []) == \
        {"court-file": {"JR20220481"}}


def test_a_pair_the_reporter_marked_not_the_same_is_never_a_candidate():
    a, b = _p("a", "John Smith"), _p("b", "John Smith")
    assert _tier(a, b, dismissed={identity.pair_id("b", "a")}) is None


# ── through the fold and the commit ───────────────────────────────────────────────────────────

def _log(vault):
    return json.loads((vault / ".watchdog" / "registry" / "merges.json").read_text())


def test_high_merge_is_logged_with_rule_evidence_and_facts(tmp_path):
    vault = make_vault(tmp_path)
    acme = _org("acme-ltd", "Acme Ltd.")
    role = {"relationship": "Director of", "target_id": "acme-ltd", "page": 1}
    _stage(vault, tmp_path, "sha-a", [_person("john-smith", "John Smith", roles=[role]), acme])
    _stage(vault, tmp_path, "sha-b", [_person("john-smith", "John Smith", roles=[role]), dict(acme)])
    _commit(vault, ["sha-a", "sha-b"])

    reg = _registry(vault)
    assert reg["john-smith"]["appears_in"] == ["sha-a", "sha-b"]
    entries = {(m["keep"]["id"], m["rule"]): m for m in _log(vault)["merges"]}
    person = entries[("john-smith", "same-name-shared-attribute")]
    assert (person["tier"], person["decided_by"]) == ("high", "rule")
    occ = person["occurrences"][0]
    assert occ["sha"] == "sha-b" and occ["documents"] == ["sha-b"]
    assert occ["facts"] and occ["facts"][0].startswith("fact:1:")
    assert occ["shared"][0]["target_id"] == "acme-ltd"
    assert ("acme-ltd", "distinctive-name") in entries
    assert "John Smith" in (vault / "merges.md").read_text()


def test_alias_fold_keeps_the_extracted_id_and_original_fact_tags(tmp_path):
    vault = make_vault(tmp_path)
    _stage(vault, tmp_path, "sha-a", [_org("ey", "Ernst & Young LLP", ["EY"])])
    _stage(vault, tmp_path, "sha-b", [_org("ernst-young", "Ernst and Young LLP")])
    orchestrate._batch_exact_fold(vault, ["sha-a", "sha-b"])
    staged = json.loads((vault / ".watchdog" / "extracted" / "sha-b.json").read_text())
    assert staged["entities"][0]["id"] == "ey"
    assert staged["entities"][0]["extracted_id"] == "ernst-young"
    fact = staged["document"]["key_facts"][0]
    assert fact["entities"] == ["ey"] and fact["extracted_entities"] == ["ernst-young"]
    assert staged["identity"]["log"][0]["merged"]["id"] == "ernst-young"


def test_fold_and_commit_are_idempotent_across_reruns(tmp_path):
    """A reconcile failure leaves the batch pending and the next finalize folds it again: the
    second pass must neither re-slug again nor log a merge twice."""
    vault = make_vault(tmp_path)
    _stage(vault, tmp_path, "sha-a", [_org("acme-ltd", "Acme Ltd.")])
    _commit(vault, ["sha-a"])
    _stage(vault, tmp_path, "sha-b", [_org("acme-ltd", "Acme Ltd."), _person("john-smith", "John Smith")])
    _stage(vault, tmp_path, "sha-c", [_person("john-smith", "John Smith")])
    orchestrate._batch_exact_fold(vault, ["sha-b", "sha-c"])
    first = {s: json.loads((vault / ".watchdog" / "extracted" / f"{s}.json").read_text()) for s in ("sha-b", "sha-c")}
    orchestrate._batch_exact_fold(vault, ["sha-b", "sha-c"])
    second = {s: json.loads((vault / ".watchdog" / "extracted" / f"{s}.json").read_text()) for s in ("sha-b", "sha-c")}
    assert first == second
    assert second["sha-c"]["entities"][0]["id"] == "john-smith-2"
    orchestrate._commit_pending(vault, ["sha-b", "sha-c"])
    log = _log(vault)
    acme = [m for m in log["merges"] if m["keep"]["id"] == "acme-ltd"]
    assert len(acme) == 1 and len(acme[0]["occurrences"]) == 1
    # Recording the same commit again adds nothing.
    merge_log.record(vault, second["sha-b"]["identity"]["log"])
    assert _log(vault) == log


def _fake_model(monkeypatch, merge_same_name: bool, seen: list):
    async def fake(*, task, prompt, schema, **kw):
        seen.append((task, prompt))
        parsed = {}
        if task == "reconcile":
            pairs = json.loads(prompt[prompt.rindex("CANDIDATE PAIRS (possible"):]
                               .split("\n\nENTITIES (each", 1)[0].split("\n", 1)[1])
            merges = [{"pair": p["index"], "keep_id": p["a"]["id"], "reason": "same facts"}
                      for p in pairs if merge_same_name and p.get("same_name")]
            parsed = {"merges": merges, "contradictions": []}
        return model_client.ModelResult(parsed=parsed, text="", model="m", backend="claude-agent-sdk",
                                        auth_mode="subscription", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)


def test_medium_pair_goes_to_the_model_with_both_sides_facts_and_a_yes_merges(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _stage(vault, tmp_path, "sha-a", [_person("john-smith", "John Smith")],
           facts=[("John Smith applied for a permit.", ["john-smith"])])
    _commit(vault, ["sha-a"])
    _stage(vault, tmp_path, "sha-b", [_person("john-smith", "John Smith")],
           facts=[("John Smith spoke against the permit.", ["john-smith"])])
    seen = []
    _fake_model(monkeypatch, True, seen)
    asyncio.run(orchestrate.finalize(vault, post_model="haiku"))

    prompt = next(p for t, p in seen if t == "reconcile")
    assert '"same_name": true' in prompt
    assert "John Smith applied for a permit." in prompt and "John Smith spoke against the permit." in prompt
    reg = _registry(vault)
    assert "john-smith-2" not in reg and reg["john-smith"]["appears_in"] == ["sha-a", "sha-b"]
    m = next(m for m in _log(vault)["merges"] if m["decided_by"] == "model")
    assert (m["tier"], m["rule"], m["model"], m["merged"]["id"]) == ("medium", "same-name", "haiku", "john-smith-2")


def test_medium_pair_the_model_declines_stays_apart_and_becomes_a_review_item(tmp_path, monkeypatch):
    from watchdog.cmd.review import open_items
    vault = make_vault(tmp_path)
    _stage(vault, tmp_path, "sha-a", [_person("john-smith", "John Smith")])
    _commit(vault, ["sha-a"])
    _stage(vault, tmp_path, "sha-b", [_person("john-smith", "John Smith")])
    _fake_model(monkeypatch, False, [])
    asyncio.run(orchestrate.finalize(vault, post_model="haiku"))

    reg = _registry(vault)
    assert {"john-smith", "john-smith-2"} <= set(reg)
    items = open_items(vault, ("merges",))
    assert len(items) == 1
    item = items[0]
    assert item["rid"] == identity.pair_id("john-smith", "john-smith-2")
    assert item["title"] == "John Smith and John Smith — possible same person"
    assert item["pair"]["model_declined"] and item["pair"]["tier"] == "medium"
    assert {item["pair"]["a"]["id"], item["pair"]["b"]["id"]} == {"john-smith", "john-smith-2"}
    assert item["pair"]["a"]["facts"]


def test_not_the_same_stops_every_later_automatic_merge(tmp_path, monkeypatch):
    from watchdog.cmd.review import open_items
    vault = make_vault(tmp_path)
    _stage(vault, tmp_path, "sha-a", [_person("john-smith", "John Smith")])
    _commit(vault, ["sha-a"])
    _stage(vault, tmp_path, "sha-b", [_person("john-smith", "John Smith")])
    _fake_model(monkeypatch, False, [])
    asyncio.run(orchestrate.finalize(vault, post_model="haiku"))
    rid = identity.pair_id("john-smith", "john-smith-2")
    resolutions.resolve(vault, [rid], label="review")
    assert open_items(vault, ("merges",)) == []

    # A third document names John Smith again; the model would now say yes to anything, but the
    # dismissed pair is never put to it.
    _stage(vault, tmp_path, "sha-c", [_org("acme-ltd", "Acme Ltd."), _person("john-smith-2", "John Smith")])
    seen = []
    _fake_model(monkeypatch, True, seen)
    asyncio.run(orchestrate.finalize(vault, post_model="haiku"))
    reg = _registry(vault)
    assert {"john-smith", "john-smith-2"} <= set(reg)


def test_reporter_merge_from_review_is_logged_and_closes_the_item(tmp_path, monkeypatch):
    from watchdog.cmd.review import open_items
    vault = make_vault(tmp_path)
    _stage(vault, tmp_path, "sha-a", [_person("john-smith", "John Smith")])
    _commit(vault, ["sha-a"])
    _stage(vault, tmp_path, "sha-b", [_person("j-smith", "J. Smith")])
    _fake_model(monkeypatch, False, [])
    asyncio.run(orchestrate.finalize(vault, post_model="haiku"))
    assert len(open_items(vault, ("merges",))) == 1
    monkeypatch.setattr("watchdog.pipeline.verification.reporter_name", lambda: "A. Reporter")

    merge_entities.run(vault, "john-smith", "j-smith")

    assert open_items(vault, ("merges",)) == []
    m = next(m for m in _log(vault)["merges"] if m["decided_by"] == "reporter")
    assert (m["tier"], m["rule"], m["reporter"]) == ("low", "partial-name", "A. Reporter")
    assert m["merged"] == {"id": "j-smith", "name": "J. Smith", "type": "person"}
    assert m["undo"]["entry"]["appears_in"] == ["sha-b"]
    assert m["undo"]["backup"].startswith(".watchdog/backups/")
    assert m["occurrences"][0]["documents"] == ["sha-b"]


def test_merge_log_newer_schema_is_never_rewritten(tmp_path):
    vault = make_vault(tmp_path)
    p = merge_log.path(vault)
    p.write_text(json.dumps({"schema_version": 99, "merges": [], "candidates": {}}))
    assert merge_log.record(vault, [{"kind": "merge", "id": "merge:x", "occurrences": []}]) is False
    assert json.loads(p.read_text())["schema_version"] == 99
