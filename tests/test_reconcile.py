"""Tests for the post-ingest reconciliation pass (#381/D118) — the finalizer stage that does
entity resolution + contradiction detection, the two jobs stateless extraction can no longer do.

The heavy fixture (`make_vault`, a realistic four-entity registry with notes and documents) is
borrowed from the merge-entities tests, since `reconcile.apply_merges` drives the very same
`merge_entities.run` surgery those tests already exercise. What is tested here is the layer on top:
blocking candidate pairs, building the bundle, and translating a model's answer into those
deterministic writes safely.
"""

import asyncio
import json
from pathlib import Path

from watchdog import model_client
from watchdog.pipeline import orchestrate, reconcile

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

def _stage(vault: Path, sha: str, filename: str, entities: list[dict], *, date="2024-06-01") -> None:
    """Write a minimal staged extraction artifact (`.watchdog/extracted/<sha>.json`) — the
    pre-commit, post-exact-fold shape `build_bundle`/`apply_merges` read — marking `entities` as
    touched-this-run. Each entity dict is already in post-postflight shape (id/name/type/aliases/
    roles/evidence_fragments/timeline_events)."""
    (vault / ".watchdog" / "extracted").mkdir(parents=True, exist_ok=True)
    artifact = {
        "document": {"sha256": sha, "filename": filename, "title": filename, "date_of_document": date},
        "entities": entities,
    }
    (vault / ".watchdog" / "extracted" / f"{sha}.json").write_text(json.dumps(artifact))


def _touch(entity_id: str, name: str, entity_type: str, claim: str | None = None) -> dict:
    """A minimal staged entity dict for `_stage` — just enough to register as touched, plus one
    evidence fragment when `claim` is given (so the claim ledger has something to render)."""
    fragments = [{"claim": claim, "page": 1}] if claim else []
    return {"id": entity_id, "name": name, "type": entity_type, "aliases": [], "roles": [],
            "evidence_fragments": fragments, "timeline_events": []}


def test_build_bundle_gates_contradiction_candidates_on_recurrence(tmp_path):
    """Only entities with claims in >= 2 documents can hold a contradiction — one document cannot
    contradict itself. alice-smith already has one committed document (sha-a) and this batch adds
    a second; carol-jones is brand new to this batch and has only the one."""
    vault = make_vault(tmp_path)
    _stage(vault, "sha-c", "doc-c.pdf", [_touch("alice-smith", "Alice Smith", "Person")])
    _stage(vault, "sha-d", "doc-d.pdf", [_touch("carol-jones", "Carol Jones", "Person")])
    bundle = reconcile.build_bundle(vault, ["sha-c", "sha-d"])
    ids = {e["entity_id"] for e in bundle["entities"]}
    assert "alice-smith" in ids          # appears_in sha-a (committed) + sha-c (this batch)
    assert "carol-jones" not in ids      # appears_in only sha-d (this batch)


def test_build_bundle_claims_come_from_the_analysis_ledger(tmp_path):
    """The per-entity claim record the pass reasons over is reconstructed from the already-
    committed note's ## Analysis section (source-attributed, exactly what a contradiction check
    needs) plus this batch's own staged claim, rendered in the same shape."""
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
    _stage(vault, "sha-c", "doc-c.pdf",
          [_touch("acme-corp", "Acme Corp", "Company", "Acme Corp opened a new office.")])
    bundle = reconcile.build_bundle(vault, ["sha-c"])
    acme = next(e for e in bundle["entities"] if e["entity_id"] == "acme-corp")
    assert acme["roles"]                 # roles digest is carried for role-vs-role conflicts
    assert "via [[documents/doc-a|Doc A]]" in acme["claims"]         # pre-existing (committed)
    assert "Acme Corp reported $10M revenue." in acme["claims"]     # pre-existing (committed)
    assert "Acme Corp opened a new office." in acme["claims"]       # this batch's own staged claim


def test_build_bundle_empty_when_nothing_staged(tmp_path):
    vault = make_vault(tmp_path)   # a populated registry, but no staged batch → nothing touched
    bundle = reconcile.build_bundle(vault, [])
    assert bundle == {"entities": [], "pairs": []}


# ── apply_merges: driving merge_entities / the staged rewrite safely ──────────
#
# `apply_merges(vault, shas, parsed, bundle, warn)` replaces `_apply_merges` — these tests drive
# it directly with `shas=[]` (no staged batch involved), which exercises exactly the taxonomy
# branch `_apply_merges` used to cover on its own: both sides already committed (existing <->
# existing) → full `merge_entities.run` surgery. The other two taxonomy branches (batch <-> batch,
# batch <-> existing) need a staged artifact to fold, so they are covered further down, driven
# through `finalize` with a reconcile mock.

def _pairs_for(a_id, b_id):
    return [{"index": 0, "a": {"id": a_id}, "b": {"id": b_id}}]


def test_apply_merges_folds_confirmed_duplicate(tmp_path):
    vault = make_vault(tmp_path)
    pairs = _pairs_for("alice-smith", "a-smith-duplicate")
    parsed = {"merges": [{"pair": 0, "keep_id": "alice-smith", "reason": "same person"}]}
    out = reconcile.apply_merges(vault, [], parsed, {"pairs": pairs}, warn=lambda m: None)

    assert out["remap"] == {"a-smith-duplicate": "alice-smith"}
    assert out["merged"][0]["keep_id"] == "alice-smith"
    reg = json.loads((vault / ".watchdog" / "registry" / "entities.json").read_text())
    assert "a-smith-duplicate" not in reg          # folded away
    assert "sha-b" in reg["alice-smith"]["appears_in"]   # its document carried over


def test_apply_merges_skips_bad_pair_index_with_warning(tmp_path):
    vault = make_vault(tmp_path)
    warnings = []
    parsed = {"merges": [{"pair": 9, "keep_id": "alice-smith"}]}
    out = reconcile.apply_merges(
        vault, [], parsed, {"pairs": _pairs_for("alice-smith", "a-smith-duplicate")},
        warn=warnings.append)
    assert out["merged"] == [] and out["remap"] == {}
    assert warnings and "not in the candidate list" in warnings[0]
    # nothing was merged
    reg = json.loads((vault / ".watchdog" / "registry" / "entities.json").read_text())
    assert "a-smith-duplicate" in reg


def test_apply_merges_skips_keep_id_not_in_pair(tmp_path):
    vault = make_vault(tmp_path)
    warnings = []
    parsed = {"merges": [{"pair": 0, "keep_id": "bob-jones"}]}
    out = reconcile.apply_merges(
        vault, [], parsed, {"pairs": _pairs_for("alice-smith", "a-smith-duplicate")},
        warn=warnings.append)
    assert out["merged"] == []
    assert warnings and "not one of pair" in warnings[0]


def test_apply_merges_chains_through_prior_merge(tmp_path):
    """A merges into B, then B is named in a later merge — the second must follow B to its
    survivor rather than fail on a now-deleted id."""
    vault = make_vault(tmp_path)
    pairs = [
        {"index": 0, "a": {"id": "a-smith-duplicate"}, "b": {"id": "alice-smith"}},
        {"index": 1, "a": {"id": "alice-smith"}, "b": {"id": "bob-jones"}},
    ]
    parsed = {"merges": [
        {"pair": 0, "keep_id": "alice-smith", "reason": "x"},
        {"pair": 1, "keep_id": "alice-smith", "reason": "y"},   # bob folds into the survivor
    ]}
    out = reconcile.apply_merges(vault, [], parsed, {"pairs": pairs}, warn=lambda m: None)
    assert len(out["merged"]) == 2
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
    parsed = {"merges": [
        {"pair": 0, "keep_id": "alice-smith", "reason": "same person"},
        {"pair": 1, "keep_id": "bob-jones", "reason": "same person again"},
    ]}
    out = reconcile.apply_merges(vault, [], parsed, {"pairs": pairs}, warn=lambda m: None)
    assert out["remap"] == {"a-smith-duplicate": "bob-jones", "alice-smith": "bob-jones"}


def test_apply_merges_carries_contradictions_through_unapplied(tmp_path):
    """`apply_merges` never applies contradictions itself (that needs the committed vault, which
    doesn't exist yet pre-commit) — it just passes the model's raw items through for the caller to
    apply later, post-commit."""
    vault = make_vault(tmp_path)
    parsed = {"merges": [], "contradictions": [{"entity_id": "acme-corp", "label": "x"}]}
    out = reconcile.apply_merges(vault, [], parsed, {"pairs": []}, warn=lambda m: None)
    assert out["contradictions"] == [{"entity_id": "acme-corp", "label": "x"}]


# ── apply_merges taxonomy, driven through finalize (#403 phase 3) ─────────────
#
# The three ways a confirmed merge can resolve, per Tom's normalization rule (the already-
# committed side always survives): both sides new to this batch (1), a new batch entity against an
# already-committed one (2), and both sides already committed (3). Each is driven through the real
# `orchestrate.finalize` — fold, reconcile, commit — with the reconcile model call mocked to
# confirm the one candidate pair, mirroring tests/test_golden_merge.py's approach.

def _stage_touch(vault: Path, sha: str, filename: str, entity_id: str, name: str, entity_type: str,
                 claim: str) -> None:
    _stage(vault, sha, filename, [_touch(entity_id, name, entity_type, claim)])


def _mock_reconcile_confirm(monkeypatch, merges):
    """Post-ingest model mock: reconcile confirms `merges`; every other post-ingest call is an
    empty/canned no-op — mirrors test_golden_merge.py's `_mock_reconcile_merge`."""
    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        parsed = {
            "reconcile": {"merges": merges, "contradictions": []},
            "entity-synthesis": {"entity_syntheses": []},
            "timeline-dedup": {"groups": []},
            "briefing": {"investigation_status": "x", "what_was_ingested": []},
        }.get(task, {})
        return model_client.ModelResult(parsed=parsed, text="", model="m",
                                        backend="claude-agent-sdk", auth_mode="subscription",
                                        cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)


def test_taxonomy_batch_batch_folds_without_surgery(tmp_path, monkeypatch):
    """Branch 1: two brand-new entities coined by two documents in the SAME batch. Neither ever
    had a note, so there is nothing for `merge_entities.run` to operate on — the whole merge is a
    staged id rewrite, and write_vault commits the result as one entity naturally. See also
    tests/test_golden_merge.py, which pins this exact case's full observable vault output."""
    vault = make_vault(tmp_path)
    _stage_touch(vault, "sha-g", "doc-g.pdf", "dee-co", "Dee Co", "Company",
                "Dee Co filed its charter.")
    _stage_touch(vault, "sha-h", "doc-h.pdf", "dee-co-holdings", "Dee Co Holdings", "Company",
                "Dee Co Holdings raised capital.")
    _mock_reconcile_confirm(monkeypatch, [{"pair": 0, "keep_id": "dee-co", "reason": "same company"}])

    asyncio.run(orchestrate.finalize(vault, post_model="haiku"))

    entities = json.loads((vault / ".watchdog" / "registry" / "entities.json").read_text())
    assert "dee-co" in entities and "dee-co-holdings" not in entities
    assert set(entities["dee-co"]["appears_in"]) >= {"sha-g", "sha-h"}
    assert "Dee Co Holdings" in entities["dee-co"]["aliases"]
    # canonical_type("Company") == "organization" — a fresh entity's folder, unlike an entity
    # already on disk (whose note_path was set once and never recomputed).
    assert not (vault / "entities" / "organization" / "dee-co-holdings.md").exists()   # no stub
    assert not (vault / ".watchdog" / "backups").exists()                             # no surgery
    note = (vault / "entities" / "organization" / "dee-co.md").read_text()
    assert "Dee Co filed its charter." in note
    assert "Dee Co Holdings raised capital." in note


def test_taxonomy_batch_existing_survives_on_the_committed_side(tmp_path, monkeypatch):
    """Branch 2: a brand-new batch-only entity turns out to be a token-variant of an already-
    committed one. The committed entity survives even though the mock's `keep_id` names the new
    one — Tom's normalization rule (the already-written entity always survives, its name stays
    primary). No stub/backup for the batch-only id: it was never committed, so there was never
    anything for merge_entities.run to operate on."""
    vault = make_vault(tmp_path)   # acme-corp already committed at entities/company/acme-corp.md
    _stage_touch(vault, "sha-e", "doc-e.pdf", "acme-corp-intl", "Acme Corp International", "Company",
                "Acme Corp International signed a new lease.")
    _mock_reconcile_confirm(
        monkeypatch, [{"pair": 0, "keep_id": "acme-corp-intl", "reason": "same company"}])

    asyncio.run(orchestrate.finalize(vault, post_model="haiku"))

    entities = json.loads((vault / ".watchdog" / "registry" / "entities.json").read_text())
    assert "acme-corp" in entities and "acme-corp-intl" not in entities
    assert entities["acme-corp"]["name"] == "Acme Corp"             # committed name stays primary
    assert "Acme Corp International" in entities["acme-corp"]["aliases"]
    assert "sha-e" in entities["acme-corp"]["appears_in"]
    assert not (vault / ".watchdog" / "backups").exists()           # no surgery — nothing to back up
    note = (vault / "entities" / "company" / "acme-corp.md").read_text()
    assert "Acme Corp International signed a new lease." in note


def test_taxonomy_existing_existing_gets_full_merge_entities_surgery(tmp_path, monkeypatch):
    """Branch 3: both sides of a confirmed merge are already committed (alice-smith touched by a
    new document this batch, a-smith-duplicate untouched but named by the candidate pair). The
    full `merge_entities.run` surgery still happens — stub + backup + "Merged from" provenance —
    exactly as before phase 3, since both entities really existed."""
    vault = make_vault(tmp_path)
    _stage_touch(vault, "sha-f", "doc-f.pdf", "alice-smith", "Alice Smith", "Person",
                "Alice Smith signed a new lease.")
    _mock_reconcile_confirm(
        monkeypatch, [{"pair": 0, "keep_id": "alice-smith", "reason": "same person"}])

    asyncio.run(orchestrate.finalize(vault, post_model="haiku"))

    entities = json.loads((vault / ".watchdog" / "registry" / "entities.json").read_text())
    assert "alice-smith" in entities and "a-smith-duplicate" not in entities
    assert "sha-f" in entities["alice-smith"]["appears_in"]          # this batch's new document
    assert "sha-b" in entities["alice-smith"]["appears_in"]          # the merged-away entity's own doc

    stub = vault / "entities" / "person" / "a-smith-duplicate.md"
    assert stub.exists()
    assert "merged_into: alice-smith" in stub.read_text()
    assert list((vault / ".watchdog" / "backups").glob("*-merge-entities"))   # a real backup snapshot

    note = (vault / "entities" / "person" / "alice-smith.md").read_text()
    assert "Merged from" in note
    assert "Alice Smith signed a new lease." in note


# ── apply_contradictions: driving contradiction.run safely ────────────────────

def test_apply_contradictions_writes_callout_via_the_deterministic_writer(tmp_path):
    vault = make_vault(tmp_path)
    items = [{
        "entity_id": "acme-corp", "label": "Director count",
        "a_value": "one director", "a_doc": "doc-a", "a_page": 2,
        "b_value": "two directors", "b_doc": "doc-b", "b_page": 5,
    }]
    applied = reconcile.apply_contradictions(vault, items, remap={}, warn=lambda m: None)
    assert applied and applied[0]["entity_id"] == "acme-corp"
    note = (vault / "entities" / "company" / "acme-corp.md").read_text()
    assert "[!contradiction] Director count" in note
    assert "documents/doc-a" in note and "documents/doc-b" in note


def test_apply_contradictions_skips_unknown_document_with_warning(tmp_path):
    """The deterministic writer validates both slugs, so a hallucinated document reference is
    dropped with a warning instead of landing a fabricated citation in a note."""
    vault = make_vault(tmp_path)
    warnings = []
    applied = reconcile.apply_contradictions(vault, [{
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
    applied = reconcile.apply_contradictions(vault, [{
        "entity_id": "a-smith-duplicate", "label": "Role",
        "a_value": "director", "a_doc": "doc-a", "b_value": "officer", "b_doc": "doc-b",
    }], remap={"a-smith-duplicate": "alice-smith"}, warn=lambda m: None)
    assert applied and applied[0]["entity_id"] == "alice-smith"


def test_apply_contradictions_follows_a_chained_remap(tmp_path):
    """A chained batch (a-smith-duplicate -> alice-smith, then alice-smith -> bob-jones) must file
    a contradiction on a-smith-duplicate at the *final* survivor, bob-jones — not at alice-smith,
    an id `apply_merges`'s flattened remap no longer even names as a key's value along the way.
    Merges run first (via `apply_merges`), exactly as `finalize` now does pre-commit; the
    contradiction is applied afterward, against the remap that produced — mirroring `finalize`'s
    real merges-then-contradictions order without the composite `apply()` this used to go through
    (retired in #403 phase 3, since the two now run on opposite sides of the commit pass)."""
    vault = make_vault(tmp_path)
    bundle = {
        "pairs": [
            {"index": 0, "a": {"id": "a-smith-duplicate"}, "b": {"id": "alice-smith"}},
            {"index": 1, "a": {"id": "alice-smith"}, "b": {"id": "bob-jones"}},
        ],
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
    merged = reconcile.apply_merges(vault, [], parsed, bundle, warn=lambda m: None)
    applied = reconcile.apply_contradictions(
        vault, merged["contradictions"], merged["remap"], warn=lambda m: None)
    assert applied[0]["entity_id"] == "bob-jones"
    note = (vault / "entities" / "person" / "bob-jones.md").read_text()
    assert "[!contradiction] Role" in note


def test_apply_merges_then_contradictions_lands_on_the_survivors_note(tmp_path):
    """The same merge-then-contradiction ordering `finalize` relies on (a contradiction the model
    reported against the merged-away id must land on the survivor's note, which now holds both
    documents' claims), driven directly through `apply_merges` + `apply_contradictions`."""
    vault = make_vault(tmp_path)
    bundle = {"pairs": _pairs_for("alice-smith", "a-smith-duplicate")}
    parsed = {
        "merges": [{"pair": 0, "keep_id": "alice-smith", "reason": "same"}],
        "contradictions": [{
            "entity_id": "a-smith-duplicate", "label": "Role",
            "a_value": "director", "a_doc": "doc-a", "b_value": "officer", "b_doc": "doc-b",
        }],
    }
    merged = reconcile.apply_merges(vault, [], parsed, bundle, warn=lambda m: None)
    applied = reconcile.apply_contradictions(
        vault, merged["contradictions"], merged["remap"], warn=lambda m: None)
    assert merged["merged"][0]["merge_id"] == "a-smith-duplicate"
    assert applied[0]["entity_id"] == "alice-smith"
    note = (vault / "entities" / "person" / "alice-smith.md").read_text()
    assert "[!contradiction] Role" in note
