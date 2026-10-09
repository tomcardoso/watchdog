"""The merge log's readers: Review's "possible same" pairs and merges.md."""


def test_possible_same_facts_carry_the_disputed_label(tmp_path):
    """D285: the facts shown with a "possible same" pair, in Review and in merges.md, are labelled
    when the reporter disputes them; the others are not."""
    import json as _json
    from watchdog.cmd.review import _same_entities
    from watchdog.pipeline import merge_log as _ml
    vault = tmp_path / "v"
    reg = vault / ".watchdog" / "registry"
    reg.mkdir(parents=True)
    (reg / "entities.json").write_text(_json.dumps({
        "a": {"name": "Jo Roe", "type": "person", "appears_in": ["s1"]},
        "b": {"name": "Jo Roe", "type": "person", "appears_in": ["s2"]}}))
    (reg / "documents.json").write_text("{}")
    side = lambda eid, fid, text: {"id": eid, "name": "Jo Roe", "type": "person", "aliases": [],  # noqa: E731
                                   "documents": [], "roles": [],
                                   "facts": [{"id": fid, "fact": text, "page": 2, "document": "D", "sha": "s"}]}
    (reg / "merges.json").write_text(_json.dumps({"schema_version": 1, "merges": [], "candidates": {
        "same:a:b": {"kind": "candidate", "id": "same:a:b", "status": "open", "tier": "low",
                     "rule": "r", "reason": "Same name.",
                     "a": side("a", "fact:1:s:1", "Jo Roe signed."), "b": side("b", "fact:1:s:2", "Jo Roe paid.")}}}))
    (reg / "verification.json").write_text(_json.dumps({"schema_version": 1, "marks": {
        "fact:1:s:1": {"status": "disputed", "fact": "Jo Roe signed.", "page": 2}}}))
    item = _same_entities(vault, frozenset())[0]
    assert [f["disputed"] for f in item["pair"]["a"]["facts"]] == [True]
    assert [f["disputed"] for f in item["pair"]["b"]["facts"]] == [False]
    text = _ml.render(vault).read_text(encoding="utf-8")
    assert "Jo Roe signed. (D, p. 2) · ✗ disputed" in text and "Jo Roe paid. (D, p. 2)\n" in text
