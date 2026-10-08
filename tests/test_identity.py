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
