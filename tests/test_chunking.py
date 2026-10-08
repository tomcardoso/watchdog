"""Tests for size-bounded finalizer chunking (#696): the shared packer, and reconciliation's split
of one whole-batch call into several that each fit."""

import asyncio
import json

from watchdog import model_client
from watchdog.pipeline import chunking, orchestrate, prompts, reconcile

from tests.test_write_vault import make_vault


# ── pack ──────────────────────────────────────────────────────────────────────

def test_pack_keeps_order_and_respects_budget():
    chunks = chunking.pack(["aaaa", "bb", "cccc", "d"], budget=6, size=len)
    assert chunks == [["aaaa", "bb"], ["cccc", "d"]]


def test_pack_gives_an_oversized_item_its_own_chunk():
    chunks = chunking.pack(["a", "x" * 50, "b"], budget=10, size=len)
    assert chunks == [["a"], ["x" * 50], ["b"]]


def test_pack_caps_items_per_chunk():
    assert chunking.pack([1, 2, 3, 4, 5], budget=10**6, size=lambda _: 1, max_items=2) == \
        [[1, 2], [3, 4], [5]]


def test_pack_empty():
    assert chunking.pack([], budget=10) == []


def test_prompt_budget_scales_with_context_window(monkeypatch):
    monkeypatch.setattr(model_client, "long_context_input_cap", lambda m, b=None: None)
    monkeypatch.setattr(model_client, "tokenizer_ratio", lambda m, b=None, v=None: 1.0)
    monkeypatch.setattr(model_client, "context_window", lambda m, b=None: 200_000)
    small = chunking.prompt_budget_chars("m")
    monkeypatch.setattr(model_client, "context_window", lambda m, b=None: 1_000_000)
    assert chunking.prompt_budget_chars("m") == 5 * small
    assert small == int(200_000 * chunking._DATA_FRACTION * 4)


def test_prompt_budget_clamped_to_long_context_price_boundary(monkeypatch):
    monkeypatch.setattr(model_client, "context_window", lambda m, b=None: 1_000_000)
    monkeypatch.setattr(model_client, "tokenizer_ratio", lambda m, b=None, v=None: 1.0)
    monkeypatch.setattr(model_client, "long_context_input_cap", lambda m, b=None: 100_000)
    assert chunking.prompt_budget_chars("m") == 400_000


# ── reconcile.chunk_bundle / merge_chunk_results ──────────────────────────────

def _pair(i):
    return {"index": i, "a": {"id": f"a{i}", "name": f"A {i}"}, "b": {"id": f"b{i}", "name": f"B {i}"}}


def _entity(i, claims="c"):
    return {"entity_id": f"e{i}", "name": f"E {i}", "claims": claims}


def test_chunk_bundle_that_fits_is_one_identical_chunk():
    bundle = {"pairs": [_pair(0), _pair(1)], "entities": [_entity(0)], "pairs_dropped": 0}
    chunks = reconcile.chunk_bundle(bundle, budget=10**6)
    assert len(chunks) == 1
    assert chunks[0]["pairs"] == bundle["pairs"]
    assert chunks[0]["entities"] == bundle["entities"]
    assert chunks[0]["pair_index"] == [0, 1]


def test_chunk_bundle_renumbers_pairs_per_chunk_and_maps_back():
    pairs = [_pair(i) for i in range(5)]
    budget = 2 * chunking.json_size(pairs[0]) + 5
    chunks = reconcile.chunk_bundle({"pairs": pairs, "entities": []}, budget)
    assert [c["pair_index"] for c in chunks] == [[0, 1], [2, 3], [4]]
    assert [p["index"] for p in chunks[1]["pairs"]] == [0, 1]
    assert chunks[1]["pairs"][0]["a"]["id"] == "a2"
    # A model answering "pair 1" in chunk 2 means bundle pair 3.
    merged = reconcile.merge_chunk_results(chunks, [
        {"merges": [], "contradictions": [{"x": 1}]},
        {"merges": [{"pair": 1, "keep_id": "a3", "reason": "r"}], "contradictions": []},
        {"merges": [{"pair": 7, "keep_id": "a4", "reason": "r"}], "contradictions": [{"x": 2}]},
    ])
    assert merged["merges"][0]["pair"] == 3
    assert not isinstance(merged["merges"][1]["pair"], int)   # out of its chunk → apply_merges skips
    assert merged["contradictions"] == [{"x": 1}, {"x": 2}]


def test_chunk_bundle_trims_an_oversized_ledger_keeping_the_newest_claims():
    claims = "OLD " * 2000 + "NEWEST CLAIM"
    budget = 1000
    chunks = reconcile.chunk_bundle({"pairs": [], "entities": [_entity(0, claims)]}, budget)
    kept = chunks[0]["entities"][0]
    assert chunking.json_size(kept) <= budget
    assert kept["claims"].startswith(reconcile._TRIMMED)
    assert kept["claims"].endswith("NEWEST CLAIM")


# ── _reconcile_pre_commit: several calls, one atomic outcome ─────────────────

def _fake_bundle(n_pairs):
    return {"entities": [], "pairs": [_pair(i) for i in range(n_pairs)], "pairs_dropped": 0}


def test_reconcile_pre_commit_splits_and_applies_merges_once(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    bundle = _fake_bundle(4)
    monkeypatch.setattr(orchestrate.reconcile, "build_bundle", lambda vault, shas: bundle)
    monkeypatch.setattr(orchestrate.chunking, "prompt_budget_chars",
                        lambda *a, **k: 2 * chunking.json_size(_pair(0)) + 5)
    applied = []
    monkeypatch.setattr(orchestrate.reconcile, "apply_merges",
                        lambda vault, shas, parsed, b, warn, **kw:
                        applied.append(parsed) or {"merged": [], "remap": {}, "contradictions": []})
    calls = []

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        calls.append(prompt)
        return model_client.ModelResult(
            parsed={"merges": [{"pair": 0, "keep_id": "x", "reason": "r"}], "contradictions": []},
            text="", model="m", backend="b", auth_mode="api-key", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    result = asyncio.run(orchestrate._reconcile_pre_commit(vault, ["s"], "haiku", None, None))
    assert result["error"] is None
    assert len(calls) == 2
    assert len(applied) == 1
    assert [m["pair"] for m in applied[0]["merges"]] == [0, 2]


def test_reconcile_pre_commit_failed_chunk_applies_nothing(tmp_path, monkeypatch):
    """A failure in any chunk defers the whole batch — merges from earlier chunks are not applied,
    since committing half-reconciled state is what I7 forbids."""
    vault = make_vault(tmp_path)
    monkeypatch.setattr(orchestrate.reconcile, "build_bundle", lambda vault, shas: _fake_bundle(4))
    monkeypatch.setattr(orchestrate.chunking, "prompt_budget_chars",
                        lambda *a, **k: 2 * chunking.json_size(_pair(0)) + 5)
    applied = []
    monkeypatch.setattr(orchestrate.reconcile, "apply_merges",
                        lambda *a, **k: applied.append(1))
    calls = []

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        calls.append(1)
        if len(calls) == 2:
            raise model_client.RateLimitError("limit")
        return model_client.ModelResult(parsed={"merges": [], "contradictions": []}, text="",
                                        model="m", backend="b", auth_mode="api-key", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)

    result = asyncio.run(orchestrate._reconcile_pre_commit(vault, ["s"], "haiku", None, None))
    assert result["error"]
    assert applied == []


# ── entity synthesis: several calls, applied together ────────────────────────

def _post_ingest_with(vault, monkeypatch, n_entities, fail_call=None):
    (vault / ".watchdog" / "tmp").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(orchestrate.synthesis_bundle, "build_bundle", lambda vault, shas: {
        "entities": [{"entity_id": f"e{i}", "name": f"E{i}"} for i in range(n_entities)]})
    applied = []

    def fake_apply(res_path, vault):
        ids = [s["entity_id"] for s in json.loads(res_path.read_text())["entity_syntheses"]]
        applied.append(ids)
        return {"applied": ids}
    monkeypatch.setattr(orchestrate.synthesis_bundle, "apply_bundle", fake_apply)
    monkeypatch.setattr(orchestrate, "_SYNTHESIS_MAX_ENTITIES", 2)
    calls = []

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        parsed = {}
        if task == "entity-synthesis":
            calls.append(task)
            if len(calls) == fail_call:
                raise model_client.ModelError("too long")
            ents = json.loads(prompt.split("Entities:\n", 1)[1])
            parsed = {"entity_syntheses": [{"entity_id": e["entity_id"], "summary": "s"}
                                           for e in ents]}
        return model_client.ModelResult(parsed=parsed, text="", model="m", backend="b",
                                        auth_mode="api-key", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)
    out = asyncio.run(orchestrate._post_ingest(vault, [], None, "haiku", skip_briefing=True))
    return out, calls, applied


def test_synthesis_splits_by_entity_count_and_applies_once(tmp_path, monkeypatch):
    out, calls, applied = _post_ingest_with(make_vault(tmp_path), monkeypatch, 5)
    assert len(calls) == 3
    assert applied == [["e0", "e1", "e2", "e3", "e4"]]
    assert out["synthesized"] == 5


def test_synthesis_failed_chunk_keeps_the_others(tmp_path, monkeypatch):
    """Synthesis is enrichment: one oversized or failed call costs only its own entities."""
    out, calls, applied = _post_ingest_with(make_vault(tmp_path), monkeypatch, 5, fail_call=2)
    assert len(calls) == 3
    assert applied == [["e0", "e1", "e4"]]
    assert out["error"]


# ── briefing: input condensed to fit one call ────────────────────────────────

def _results(n, facts=10):
    return [{"sha256": f"s{i}", "filename": f"doc{i}.pdf",
             "document_type": "Affidavit" if i % 2 else "Annual Report",
             "date": f"2020-01-{i % 28 + 1:02d}", "entity_count": 3,
             "new_entities": ["x"], "updated_entities": [],
             "key_facts": [{"fact": f"fact {j} of doc {i} " * 3} for j in range(facts)]}
            for i in range(n)]


def test_briefing_inputs_untouched_when_they_fit():
    results, pads = _results(3), ["pad one", "pad two"]
    assert orchestrate._fit_briefing_inputs(results, pads, 10**7) == (results, pads, None)


def test_briefing_inputs_cap_key_facts_first():
    results = _results(20)
    budget = int(chunking.json_size(_results(20, facts=5)) / 0.75) + 10
    fitted, _, condensed = orchestrate._fit_briefing_inputs(results, [], budget)
    assert all(len(r["key_facts"]) == 5 for r in fitted)
    assert "first 5 key facts" in condensed["level"]
    assert chunking.json_size(fitted) <= budget


def test_briefing_inputs_fall_back_to_a_tally_for_a_huge_batch():
    results = _results(5000, facts=2)
    budget = 20_000
    fitted, pads, condensed = orchestrate._fit_briefing_inputs(results, ["p" * 50_000], budget)
    summary = fitted[0]["batch_summary"]
    assert summary["documents"] == 5000
    assert summary["by_document_type"] == {"Annual Report": 2500, "Affidavit": 2500}
    assert chunking.json_size(fitted) <= budget
    assert pads == []
    assert condensed["documents"] == 5000


def test_briefing_alerts_come_out_of_the_budget_and_are_capped():
    """#697 review: near-dup alerts and contradiction flags were sent unmeasured, so a large
    batch could overflow the call even after its results were condensed."""
    flags = [{"entity": f"Entity {i}", "label": "Date of incorporation"} for i in range(2000)]
    dups = [{"filename": f"doc-{i}.pdf", "similarity": 0.97} for i in range(10)]
    budget = 40_000
    fit_dups, fit_flags, left = orchestrate._fit_briefing_alerts(dups, flags, budget)
    used = chunking.json_size(fit_dups) + chunking.json_size(fit_flags)
    assert used <= budget // 4 + 200
    assert left == budget - used
    assert fit_flags[-1] == {"more_not_shown": 2000 - (len(fit_flags) - 1)}
    assert fit_dups == dups                       # a short list is kept whole


def test_briefing_alerts_drop_a_single_oversized_flag_to_a_count():
    flags = [{"entity": "E", "label": "x" * 20_000}] + [{"entity": "F", "label": "y"}]
    _, fit_flags, _ = orchestrate._fit_briefing_alerts([], flags, 40_000)
    assert fit_flags == [{"more_not_shown": 2}]


def test_briefing_alerts_untouched_when_small():
    flags = [{"entity": "Acme", "label": "x"}]
    assert orchestrate._fit_briefing_alerts([], flags, 40_000)[:2] == ([], flags)


def test_briefing_prompt_tells_the_model_its_view_is_partial():
    p = prompts.build_briefing_prompt(brief=None, results=[], scratchpads=[], neardup_alerts=[],
                                      contradiction_flags=[],
                                      condensed={"level": "no key facts", "documents": 4000})
    assert "4000 documents" in p and "partial view" in p
    plain = prompts.build_briefing_prompt(brief=None, results=[], scratchpads=[], neardup_alerts=[],
                                          contradiction_flags=[])
    assert "partial view" not in plain
