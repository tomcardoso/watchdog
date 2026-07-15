"""Tests for the post-ingest reconciliation pass (#381/D118) — the finalizer stage that does
entity resolution + contradiction detection, the two jobs stateless extraction can no longer do.

The heavy fixture (`make_vault`, a realistic four-entity registry with notes and documents) is
borrowed from the merge-entities tests, since `reconcile._apply_merges` drives the very same
`merge_entities.run` surgery those tests already exercise. What is tested here is the layer on top:
blocking candidate pairs, building the bundle, and translating a model's answer into those
deterministic writes safely.
"""

import json
from pathlib import Path

from watchdog.pipeline import reconcile

from tests.test_merge_entities import make_vault


# ── _overlap / _tokens: the blocking signal ───────────────────────────────────

def test_overlap_strict_subset_scores_highest():
    """The abbreviation/partial-name case is the strongest signal short of an exact match."""
    assert reconcile._overlap("Laurentian University", "Laurentian University of Sudbury") == 1.0
    assert reconcile._overlap("Chief Justice Morawetz", "Chief Justice G.B. Morawetz") == 1.0


def test_overlap_identical_token_sets_score_highest():
    """`normalize_entity_name` is order- and stopword-sensitive, so write_vault's exact-match pass
    never folds an inverted person name ("Tom Cardoso" / "Cardoso, Tom" — very common in court and
    registry documents) or a stopword variant ("The Acme Group" / "Acme Group"). Those are exactly
    the shapes this pass exists to catch, so blocking must send them to the model rather than
    scoring them 0 and letting them fall through both tiers."""
    assert reconcile._overlap("Tom Cardoso", "Cardoso, Tom") == 1.0
    assert reconcile._overlap("The Acme Group", "Acme Group") == 1.0


def test_candidate_pairs_sends_identical_token_set_names():
    reg = _reg("Tom Cardoso", "Person", "Cardoso, Tom", "Person")
    pairs = reconcile.candidate_pairs(reg, touched={"a", "b"})
    assert len(pairs) == 1
    assert {pairs[0]["a"]["id"], pairs[0]["b"]["id"]} == {"a", "b"}


def test_overlap_partial_token_overlap_is_jaccard():
    # {acme, holdings} vs {acme, holdings, international} → 2/3
    assert reconcile._overlap("Acme Holdings", "Acme Holdings International") == 1.0  # strict subset
    # non-subset overlap falls to Jaccard: {acme, holdings} vs {acme, ventures} = 1/3
    assert abs(reconcile._overlap("Acme Holdings", "Acme Ventures") - 1 / 3) < 1e-9


def test_overlap_stopwords_carry_no_signal():
    """Structural words are dropped before comparison, so inserting 'of'/'the' changes nothing —
    the score rests only on the content tokens."""
    with_stop = reconcile._overlap("Bank of Nova Scotia", "Bank of Montreal")
    without_stop = reconcile._overlap("Bank Nova Scotia", "Bank Montreal")
    assert with_stop == without_stop


def test_overlap_below_floor_is_not_blocked():
    """Two unrelated 'University of X' names share only 'university' after stopword removal (1/3),
    which is below the send floor — so they never reach the model as a candidate pair."""
    reg = _reg("University of Toronto", "Company", "University of Waterloo", "Company")
    assert reconcile._overlap("University of Toronto", "University of Waterloo") < reconcile._JACCARD_MIN
    assert reconcile.candidate_pairs(reg, touched={"a", "b"}) == []


# ── candidate_pairs: the blocking pass ────────────────────────────────────────

def _reg(a_name, a_type, b_name, b_type, a_id="a", b_id="b"):
    return {
        a_id: {"id": a_id, "name": a_name, "type": a_type, "aliases": []},
        b_id: {"id": b_id, "name": b_name, "type": b_type, "aliases": []},
    }


def test_candidate_pairs_blocks_across_types():
    """A person and an organization are never the same real-world thing, whatever their names."""
    reg = _reg("Acme Holdings", "Person", "Acme Holdings International", "Company")
    assert reconcile.candidate_pairs(reg, touched={"a", "b"}) == []


def test_candidate_pairs_emits_a_plausible_same_type_pair():
    reg = _reg("Laurentian University", "Company", "Laurentian University of Sudbury", "Company")
    pairs = reconcile.candidate_pairs(reg, touched={"a"})
    assert len(pairs) == 1
    assert {pairs[0]["a"]["id"], pairs[0]["b"]["id"]} == {"a", "b"}
    assert pairs[0]["index"] == 0


def test_candidate_pairs_requires_a_touched_side():
    """An ingest reconciles the entities it touched against the whole vault — but two entities that
    were *both* untouched this run are not re-litigated on every run."""
    reg = _reg("Laurentian University", "Company", "Laurentian University of Sudbury", "Company")
    assert reconcile.candidate_pairs(reg, touched=set()) == []
    assert len(reconcile.candidate_pairs(reg, touched={"b"})) == 1


def test_candidate_pairs_ranked_and_capped(monkeypatch):
    """When more pairs qualify than the cap allows, the strongest overlaps survive — a vault where
    a common token blocks thousands of pairs cannot blow the context window."""
    monkeypatch.setattr(reconcile, "_MAX_PAIRS", 1)
    reg = {
        "a": {"id": "a", "name": "Acme Holdings", "type": "Company", "aliases": []},
        "b": {"id": "b", "name": "Acme Holdings International", "type": "Company", "aliases": []},
        "c": {"id": "c", "name": "Acme Ventures", "type": "Company", "aliases": []},
    }
    pairs = reconcile.candidate_pairs(reg, touched={"a", "b", "c"})
    assert len(pairs) == 1
    # a/b is a strict subset (1.0) and must win over a/c's partial Jaccard.
    assert {pairs[0]["a"]["id"], pairs[0]["b"]["id"]} == {"a", "b"}


# ── build_bundle ──────────────────────────────────────────────────────────────

def _queue(vault: Path, *ids: str) -> None:
    """Mark entities as touched-this-run in the fragment queue reconcile reads."""
    frag = vault / ".watchdog" / "tmp" / "entity-fragments"
    frag.mkdir(parents=True, exist_ok=True)
    (frag / "_queue.json").write_text(json.dumps({eid: {"shas": []} for eid in ids}))


def test_build_bundle_gates_contradiction_candidates_on_recurrence(tmp_path):
    """Only entities that appear in >= 2 documents can hold a contradiction — one document cannot
    contradict itself. acme-corp appears in two; the single-doc people do not."""
    vault = make_vault(tmp_path)
    _queue(vault, "alice-smith", "acme-corp")
    bundle = reconcile.build_bundle(vault)
    ids = {e["entity_id"] for e in bundle["entities"]}
    assert "acme-corp" in ids            # appears_in sha-a + sha-b
    assert "alice-smith" not in ids      # appears_in only sha-a


def test_build_bundle_claims_come_from_the_analysis_ledger(tmp_path):
    """The per-entity claim record the pass reasons over is the note's ## Analysis section —
    already source-attributed by document, exactly what a contradiction check needs."""
    vault = make_vault(tmp_path)
    acme_note = vault / "entities" / "company" / "acme-corp.md"
    # write_vault appends one *<date>, via [[documents/<slug>|<title>]]:* block per document,
    # followed by that document's claim bullets — reproduce that shape directly on the note.
    acme_note.write_text(
        acme_note.read_text().replace(
            "## Notes",
            "## Analysis\n\n*2024-01-01, via [[documents/doc-a|Doc A]]:*\n"
            "- Acme Corp reported $10M revenue. (p. 3)\n\n## Notes",
        )
    )
    _queue(vault, "acme-corp")
    bundle = reconcile.build_bundle(vault)
    acme = next(e for e in bundle["entities"] if e["entity_id"] == "acme-corp")
    assert acme["roles"]                 # roles digest is carried for role-vs-role conflicts
    assert "via [[documents/doc-a|Doc A]]" in acme["claims"]
    assert "Acme Corp reported $10M revenue." in acme["claims"]


def test_build_bundle_empty_on_fresh_vault(tmp_path):
    vault = make_vault(tmp_path)   # no fragment queue → nothing touched
    bundle = reconcile.build_bundle(vault)
    assert bundle == {"entities": [], "pairs": []}


# ── _apply_merges: driving merge_entities safely ──────────────────────────────

def _pairs_for(a_id, b_id):
    return [{"index": 0, "a": {"id": a_id}, "b": {"id": b_id}}]


def test_apply_merges_folds_confirmed_duplicate(tmp_path):
    vault = make_vault(tmp_path)
    pairs = _pairs_for("alice-smith", "a-smith-duplicate")
    merges = [{"pair": 0, "keep_id": "alice-smith", "reason": "same person"}]
    applied, remap = reconcile._apply_merges(vault, merges, pairs, warn=lambda m: None)

    assert remap == {"a-smith-duplicate": "alice-smith"}
    assert applied[0]["keep_id"] == "alice-smith"
    reg = json.loads((vault / ".watchdog" / "registry" / "entities.json").read_text())
    assert "a-smith-duplicate" not in reg          # folded away
    assert "sha-b" in reg["alice-smith"]["appears_in"]   # its document carried over


def test_apply_merges_skips_bad_pair_index_with_warning(tmp_path):
    vault = make_vault(tmp_path)
    warnings = []
    applied, remap = reconcile._apply_merges(
        vault, [{"pair": 9, "keep_id": "alice-smith"}], _pairs_for("alice-smith", "a-smith-duplicate"),
        warn=warnings.append)
    assert applied == [] and remap == {}
    assert warnings and "not in the candidate list" in warnings[0]
    # nothing was merged
    reg = json.loads((vault / ".watchdog" / "registry" / "entities.json").read_text())
    assert "a-smith-duplicate" in reg


def test_apply_merges_skips_keep_id_not_in_pair(tmp_path):
    vault = make_vault(tmp_path)
    warnings = []
    applied, _ = reconcile._apply_merges(
        vault, [{"pair": 0, "keep_id": "bob-jones"}], _pairs_for("alice-smith", "a-smith-duplicate"),
        warn=warnings.append)
    assert applied == []
    assert warnings and "not one of pair" in warnings[0]


def test_apply_merges_chains_through_prior_merge(tmp_path):
    """A merges into B, then B is named in a later merge — the second must follow B to its
    survivor rather than fail on a now-deleted id."""
    vault = make_vault(tmp_path)
    pairs = [
        {"index": 0, "a": {"id": "a-smith-duplicate"}, "b": {"id": "alice-smith"}},
        {"index": 1, "a": {"id": "alice-smith"}, "b": {"id": "bob-jones"}},
    ]
    merges = [
        {"pair": 0, "keep_id": "alice-smith", "reason": "x"},
        {"pair": 1, "keep_id": "alice-smith", "reason": "y"},   # bob folds into the survivor
    ]
    applied, remap = reconcile._apply_merges(vault, merges, pairs, warn=lambda m: None)
    assert len(applied) == 2
    reg = json.loads((vault / ".watchdog" / "registry" / "entities.json").read_text())
    assert "bob-jones" not in reg and "a-smith-duplicate" not in reg


def test_apply_merges_returns_flattened_remap(tmp_path):
    """A chain (a-smith-duplicate -> alice-smith, then alice-smith -> bob-jones) must leave every
    key pointing straight at the *final* survivor — a consumer that follows the map only one step
    (both do) would otherwise resolve a-smith-duplicate to alice-smith, an id that no longer
    exists."""
    vault = make_vault(tmp_path)
    pairs = [
        {"index": 0, "a": {"id": "a-smith-duplicate"}, "b": {"id": "alice-smith"}},
        {"index": 1, "a": {"id": "alice-smith"}, "b": {"id": "bob-jones"}},
    ]
    merges = [
        {"pair": 0, "keep_id": "alice-smith", "reason": "same person"},
        {"pair": 1, "keep_id": "bob-jones", "reason": "same person again"},
    ]
    _, remap = reconcile._apply_merges(vault, merges, pairs, warn=lambda m: None)
    assert remap == {"a-smith-duplicate": "bob-jones", "alice-smith": "bob-jones"}


# ── _fold_fragments: keeping synthesis from losing a merged entity ────────────

def test_fold_fragments_moves_losing_file_and_unions_queue(tmp_path):
    vault = make_vault(tmp_path)
    frag = vault / ".watchdog" / "tmp" / "entity-fragments"
    frag.mkdir(parents=True)
    (frag / "alice-smith.md").write_text("### Doc A\nAlice claim.\n")
    (frag / "a-smith-duplicate.md").write_text("### Doc B\nSmith claim.\n")
    (frag / "_queue.json").write_text(json.dumps({
        "alice-smith": {"name": "Alice Smith", "note_path": "entities/person/alice-smith",
                        "shas": ["sha-a"]},
        "a-smith-duplicate": {"name": "A. Smith", "note_path": "entities/person/a-smith-duplicate",
                              "shas": ["sha-b"]},
    }))
    # Perform the registry merge so the survivor exists, then fold the in-flight fragments.
    from watchdog.pipeline import merge_entities
    merge_entities.run(vault, "alice-smith", "a-smith-duplicate")

    reconcile._fold_fragments(vault, {"a-smith-duplicate": "alice-smith"})

    assert not (frag / "a-smith-duplicate.md").exists()
    survived = (frag / "alice-smith.md").read_text()
    assert "Alice claim." in survived and "Smith claim." in survived
    queue = json.loads((frag / "_queue.json").read_text())
    assert "a-smith-duplicate" not in queue
    assert set(queue["alice-smith"]["shas"]) == {"sha-a", "sha-b"}


def test_fold_fragments_chain_unions_shas_onto_final_survivor(tmp_path):
    """A chained remap (a-smith-duplicate -> bob-jones, alice-smith -> bob-jones, both flattened
    to the final survivor) must carry every entity's fragments and shas onto that one survivor —
    not stall halfway at an intermediate id that no longer exists in the registry."""
    vault = make_vault(tmp_path)
    frag = vault / ".watchdog" / "tmp" / "entity-fragments"
    frag.mkdir(parents=True)
    (frag / "alice-smith.md").write_text("### Doc A\nAlice claim.\n")
    (frag / "a-smith-duplicate.md").write_text("### Doc B\nSmith claim.\n")
    (frag / "bob-jones.md").write_text("### Doc C\nBob claim.\n")
    (frag / "_queue.json").write_text(json.dumps({
        "alice-smith": {"name": "Alice Smith", "note_path": "entities/person/alice-smith",
                        "shas": ["sha-a"]},
        "a-smith-duplicate": {"name": "A. Smith", "note_path": "entities/person/a-smith-duplicate",
                              "shas": ["sha-b"]},
        "bob-jones": {"name": "Bob Jones", "note_path": "entities/person/bob-jones",
                      "shas": ["sha-c"]},
    }))

    from watchdog.pipeline import merge_entities
    merge_entities.run(vault, "alice-smith", "a-smith-duplicate")
    merge_entities.run(vault, "bob-jones", "alice-smith")
    remap = {"a-smith-duplicate": "bob-jones", "alice-smith": "bob-jones"}   # flattened, as _apply_merges returns

    reconcile._fold_fragments(vault, remap)

    assert not (frag / "alice-smith.md").exists()
    assert not (frag / "a-smith-duplicate.md").exists()
    survived = (frag / "bob-jones.md").read_text()
    assert "Alice claim." in survived and "Smith claim." in survived and "Bob claim." in survived
    queue = json.loads((frag / "_queue.json").read_text())
    assert "alice-smith" not in queue and "a-smith-duplicate" not in queue
    assert set(queue["bob-jones"]["shas"]) == {"sha-a", "sha-b", "sha-c"}


# ── _apply_contradictions: driving contradiction.run safely ───────────────────

def test_apply_contradictions_writes_callout_via_the_deterministic_writer(tmp_path):
    vault = make_vault(tmp_path)
    items = [{
        "entity_id": "acme-corp", "label": "Director count",
        "a_value": "one director", "a_doc": "doc-a", "a_page": 2,
        "b_value": "two directors", "b_doc": "doc-b", "b_page": 5,
    }]
    applied = reconcile._apply_contradictions(vault, items, remap={}, warn=lambda m: None)
    assert applied and applied[0]["entity_id"] == "acme-corp"
    note = (vault / "entities" / "company" / "acme-corp.md").read_text()
    assert "[!contradiction] Director count" in note
    assert "documents/doc-a" in note and "documents/doc-b" in note


def test_apply_contradictions_skips_unknown_document_with_warning(tmp_path):
    """The deterministic writer validates both slugs, so a hallucinated document reference is
    dropped with a warning instead of landing a fabricated citation in a note."""
    vault = make_vault(tmp_path)
    warnings = []
    applied = reconcile._apply_contradictions(vault, [{
        "entity_id": "acme-corp", "label": "x",
        "a_value": "v", "a_doc": "doc-a", "b_value": "w", "b_doc": "does-not-exist",
    }], remap={}, warn=warnings.append)
    assert applied == []
    assert warnings and "does-not-exist" in warnings[0]


def test_apply_contradictions_follows_a_merge_remap(tmp_path):
    """A contradiction may name an entity that a merge, moments earlier, folded away — it must be
    filed on the survivor, not dropped."""
    vault = make_vault(tmp_path)
    from watchdog.pipeline import merge_entities
    merge_entities.run(vault, "alice-smith", "a-smith-duplicate")
    applied = reconcile._apply_contradictions(vault, [{
        "entity_id": "a-smith-duplicate", "label": "Role",
        "a_value": "director", "a_doc": "doc-a", "b_value": "officer", "b_doc": "doc-b",
    }], remap={"a-smith-duplicate": "alice-smith"}, warn=lambda m: None)
    assert applied and applied[0]["entity_id"] == "alice-smith"


def test_apply_contradictions_follows_a_chained_remap(tmp_path):
    """A chained batch (a-smith-duplicate -> alice-smith, then alice-smith -> bob-jones) must file
    a contradiction on a-smith-duplicate at the *final* survivor, bob-jones — not at alice-smith,
    an id `_apply_merges`'s flattened remap no longer even names as a key's value along the way."""
    vault = make_vault(tmp_path)
    bundle = {
        "pairs": [
            {"index": 0, "a": {"id": "a-smith-duplicate"}, "b": {"id": "alice-smith"}},
            {"index": 1, "a": {"id": "alice-smith"}, "b": {"id": "bob-jones"}},
        ],
        "entities": [],
    }
    parsed = {
        "merges": [
            {"pair": 0, "keep_id": "alice-smith", "reason": "same person"},
            {"pair": 1, "keep_id": "bob-jones", "reason": "same person again"},
        ],
        "contradictions": [{
            "entity_id": "a-smith-duplicate", "label": "Role",
            "a_value": "director", "a_doc": "doc-a", "b_value": "officer", "b_doc": "doc-b",
        }],
    }
    out = reconcile.apply(vault, parsed, bundle, warn=lambda m: None)
    assert out["contradictions"][0]["entity_id"] == "bob-jones"
    note = (vault / "entities" / "person" / "bob-jones.md").read_text()
    assert "[!contradiction] Role" in note


# ── apply: ordering ───────────────────────────────────────────────────────────

def test_apply_runs_merges_before_contradictions(tmp_path):
    """Merges first, so a contradiction the model reported against the merged-away id lands on the
    survivor's note (which now holds both documents' claims)."""
    vault = make_vault(tmp_path)
    bundle = {"pairs": _pairs_for("alice-smith", "a-smith-duplicate"), "entities": []}
    parsed = {
        "merges": [{"pair": 0, "keep_id": "alice-smith", "reason": "same"}],
        "contradictions": [{
            "entity_id": "a-smith-duplicate", "label": "Role",
            "a_value": "director", "a_doc": "doc-a", "b_value": "officer", "b_doc": "doc-b",
        }],
    }
    out = reconcile.apply(vault, parsed, bundle, warn=lambda m: None)
    assert out["merged"][0]["merge_id"] == "a-smith-duplicate"
    assert out["contradictions"][0]["entity_id"] == "alice-smith"
    note = (vault / "entities" / "person" / "alice-smith.md").read_text()
    assert "[!contradiction] Role" in note
