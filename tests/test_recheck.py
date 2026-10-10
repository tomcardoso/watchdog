"""Re-check contradictions (D287): every pair of an entity's stored facts is compared at least
once, findings are filed like any contradiction and never repeat a recorded or handled one."""

import json
import os
import re
import shutil
import subprocess
import sys
from itertools import combinations
from pathlib import Path

import pytest

from tests.gui_support import call, call_error, wdg_home  # noqa: F401  (fixture)
from watchdog import model_client
from watchdog.pipeline import chunking, entity_facts, history, recheck, resolutions
from watchdog.vault_paths import processing_lock

SRC = Path(__file__).resolve().parent.parent / "src"


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    root = tmp_path_factory.mktemp("demo-recheck")
    vault = root / "vault"
    env = {**os.environ, "PYTHONPATH": str(SRC), "NO_COLOR": "1"}
    proc = subprocess.run([sys.executable, "-m", "watchdog.gui.demo", str(vault), "--home", str(root / "home")],
                          env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    return vault


@pytest.fixture
def vault(demo, tmp_path, monkeypatch):
    copy = tmp_path / "v"
    shutil.copytree(demo, copy)
    entity_facts.clear_cache()
    monkeypatch.setattr("watchdog.gui.vaultio.require_granted", lambda p: None)
    monkeypatch.setattr("watchdog.pipeline.entity_notes.index_notes", lambda *a, **k: None)
    return copy


def _entities(vault):
    return json.loads((vault / ".watchdog" / "registry" / "entities.json").read_text())


def _data(prompt: str) -> str:
    return prompt.split("ENTITIES (", 1)[1]


def _refs(text: str) -> set[str]:
    return set(re.findall(r"\[(f:[0-9a-f]+(?::\d+)?)\]", text))


class FakeModel:
    """The reconcile model, answering each call with `answer(entities)` and keeping the prompts."""

    def __init__(self, answer=lambda ents: []):
        self.answer = answer
        self.prompts = []

    async def __call__(self, *, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        assert task == "reconcile"
        self.prompts.append(prompt)
        ents = json.loads(_data(prompt).split(":\n", 1)[1])
        return model_client.ModelResult(parsed={"merges": [], "contradictions": self.answer(ents)},
                                        text="", model="claude-sonnet-5-5", backend="claude-api",
                                        auth_mode="api-key", cost_usd=0.002,
                                        usage={"input_tokens": 1000, "output_tokens": 20})


def _first_last(entity: dict, label="Contract value", **extra) -> dict:
    """A finding citing the entity's first and last listed facts."""
    text = entity["new_facts"] + "\n" + entity["stored_facts"]
    docs = re.findall(r"^\*\[\[documents/([^|\]]+)\|", text, re.M)
    refs = re.findall(r"^- \[(f:[0-9a-f]+(?::\d+)?)\]", text, re.M)
    return {"entity_id": entity["entity_id"], "label": label, "a_value": "one", "a_doc": docs[0],
            "a_page": None, "a_fact": refs[0], "b_value": "two", "b_doc": docs[-1], "b_page": None,
            "b_fact": refs[-1], **extra}


def _run(vault, monkeypatch, fake, ids):
    monkeypatch.setattr(model_client, "acomplete_json", fake)
    return recheck.run(vault, ids, model="sonnet", backend="claude-api", say=lambda m: None,
                       warn=lambda m: None)


# ── chunking ─────────────────────────────────────────────────────────────────────────

def test_a_small_entity_goes_whole_as_new_facts_in_one_call(vault):
    p = recheck.plan(vault, ["pier-9"], "sonnet", None)
    assert [r["id"] for r in p["entities"]] == ["pier-9"] and len(p["calls"]) == 1
    ent = p["calls"][0]["entities"][0]
    assert ent["stored_facts"] == "" and len(_refs(ent["new_facts"])) == p["entities"][0]["facts"] == 25


@pytest.mark.parametrize("budget", [7000, 9000, 12000])
def test_every_pair_of_facts_is_seen_together_at_least_once(vault, monkeypatch, budget):
    monkeypatch.setattr(chunking, "prompt_budget_chars", lambda *a, **k: budget)
    eid = "northgate-civil-works"
    p = recheck.plan(vault, [eid], "sonnet", None)
    calls = [c["entities"][0] for c in p["calls"]]
    assert len(calls) > 2
    every = set().union(*(_refs(c["new_facts"]) | _refs(c["stored_facts"]) for c in calls))
    assert len(every) == p["entities"][0]["facts"] == 36
    seen = set()
    for c in calls:
        new, stored = _refs(c["new_facts"]), _refs(c["stored_facts"])
        # What the prompt compares: new with stored, new with new — never stored with stored.
        seen |= {frozenset(x) for x in combinations(sorted(new), 2)}
        seen |= {frozenset((a, b)) for a in new for b in stored}
        assert len(json.dumps(c, ensure_ascii=False)) <= budget * 1.1
    assert seen == {frozenset(x) for x in combinations(sorted(every), 2)}


def test_an_entity_too_large_to_check_every_pair_is_left_out_and_said_so(vault, monkeypatch):
    monkeypatch.setattr(chunking, "prompt_budget_chars", lambda *a, **k: 6000)
    monkeypatch.setattr(recheck, "MAX_ENTITY_CALLS", 3)
    p = recheck.plan(vault, ["northgate-civil-works", "pier-9"], "sonnet", None)
    big = next(s for s in p["skipped"] if s["id"] == "northgate-civil-works")
    assert big["reason"] == "too_large" and big["facts"] == 36 and big["calls"] > 3


def test_the_whole_investigation_packs_small_entities_together(vault):
    p = recheck.plan(vault, None, "sonnet", None)
    reg = _entities(vault)
    planned = {r["id"] for r in p["entities"]}
    assert len(planned) > 10 and len(p["calls"]) < len(p["entities"])
    # An entity named in one document is checked too, when it has two or more facts.
    single = {r["id"] for r in p["entities"] if r["documents"] == 1}
    assert single, "the demo has single-document entities with several facts"
    assert all(len(reg[e].get("appears_in") or []) == 1 for e in single)


# ── what is sent ─────────────────────────────────────────────────────────────────────

def test_disputed_facts_are_sent_labelled_and_recorded_contradictions_listed(vault, monkeypatch):
    from watchdog.pipeline import verification
    disputed = [fid for fid, m in verification.marks(vault).items() if m.get("status") == "disputed"]
    assert disputed
    owner = next(eid for eid in _entities(vault)
                 if any(f["id"] in disputed for f in entity_facts.FactIndex(vault).facts_for(eid))
                 and len(_entities(vault)[eid].get("appears_in") or []) >= 2)
    fake = FakeModel()
    _run(vault, monkeypatch, fake, [owner, "northgate-civil-works"])
    data = _data(fake.prompts[0])
    assert "disputed by the reporter" in data
    assert "Value of the Pier 9 contract" in data         # recorded ones, so they are not repeated


# ── filing and de-duplication ────────────────────────────────────────────────────────

def test_a_finding_is_filed_like_any_contradiction_with_its_fact_links(vault, monkeypatch):
    fake = FakeModel(lambda ents: [_first_last(e) for e in ents if e["entity_id"] == "pier-9"])
    out = _run(vault, monkeypatch, fake, ["pier-9"])
    assert [f["label"] for f in out["filed"]] == ["Contract value"]
    callouts = _entities(vault)["pier-9"]["contradictions"]
    new = next(c for c in callouts if "Contract value" in c)
    assert new.count("#^f-") == 2
    rid = resolutions.contradiction_id(new)
    items = call("review.items", vault=str(vault), kinds=["contradictions"])["items"]
    assert rid in {i["rid"] for i in items}


def test_a_handled_contradiction_never_comes_back_however_it_is_worded(vault, monkeypatch):
    # The demo's recorded conflict on Northgate, marked handled by the reporter.
    reg = _entities(vault)
    old = reg["northgate-civil-works"]["contradictions"][0]
    resolutions.resolve(vault, [resolutions.contradiction_id(old)], label="review")
    blocks = re.findall(r"#\^(f-[0-9a-f]+)", old)
    facts = entity_facts.FactIndex(vault).facts_for("northgate-civil-works")
    refs = entity_facts.short_refs(facts)
    from watchdog.pipeline.citations import block_id
    by_block = {block_id(f["id"]): f for f in facts}
    a, b = by_block[blocks[0]], by_block[blocks[1]]
    slug = lambda f: f["note"].removeprefix("documents/")  # noqa: E731

    def answer(ents):
        return [
            # The same two facts, reworded and reversed: not filed again.
            {"entity_id": "northgate-civil-works", "label": "Contract amount", "a_value": "$52.34M",
             "a_doc": slug(b), "a_page": None, "a_fact": refs[b["id"]], "b_value": "$48.6M cap",
             "b_doc": slug(a), "b_page": None, "b_fact": refs[a["id"]]},
            # The same pair by document pages only, no fact refs: not filed again either.
            {"entity_id": "northgate-civil-works", "label": "Price", "a_value": "x", "a_doc": slug(a),
             "a_page": a["page"], "a_fact": None, "b_value": "y", "b_doc": slug(b),
             "b_page": b["page"], "b_fact": None},
        ]
    out = _run(vault, monkeypatch, FakeModel(answer), ["northgate-civil-works"])
    assert out["filed"] == [] and out["repeats"] == 2
    assert _entities(vault)["northgate-civil-works"]["contradictions"] == [old]
    assert call("review.items", vault=str(vault), kinds=["contradictions"])["counts"]["contradictions"] == \
        sum(1 for i in call("review.items", vault=str(vault))["items"] if i["kind"] == "contradictions")


def test_the_same_conflict_found_by_two_calls_is_filed_once(vault, monkeypatch):
    monkeypatch.setattr(chunking, "prompt_budget_chars", lambda *a, **k: 7000)
    first = {}

    def answer(ents):
        e = ents[0]
        text = e["new_facts"]
        docs = re.findall(r"^\*\[\[documents/([^|\]]+)\|", text, re.M)
        refs = re.findall(r"^- \[(f:[0-9a-f]+)\]", text, re.M)
        if not first:                     # the first block's first two facts, seen in every call it leads
            first.update(a=(docs[0], refs[0]), b=(docs[0], refs[1]))
        if first["a"][1] in refs and first["b"][1] in refs:
            return [{"entity_id": e["entity_id"], "label": f"Conflict {len(refs)}", "a_value": "a",
                     "a_doc": first["a"][0], "a_page": None, "a_fact": first["a"][1], "b_value": "b",
                     "b_doc": first["b"][0], "b_page": None, "b_fact": first["b"][1]}]
        return []
    fake = FakeModel(answer)
    out = _run(vault, monkeypatch, fake, ["northgate-civil-works"])
    assert len(fake.prompts) > 2 and out["found"] >= 2
    assert len(out["filed"]) == 1 and out["repeats"] == out["found"] - 1


def test_a_finding_about_an_entity_the_call_was_not_about_is_dropped(vault, monkeypatch):
    fake = FakeModel(lambda ents: [{**_first_last(ents[0]), "entity_id": "leonard-pike"}])
    out = _run(vault, monkeypatch, fake, ["pier-9"])
    assert out["filed"] == [] and out["rejected"] == 1


# ── locks, history, usage, stopping ──────────────────────────────────────────────────

def test_refused_while_a_run_holds_the_vault(vault, monkeypatch, wdg_home):  # noqa: F811
    lock = processing_lock(vault)
    lock.write_text("pid: cli\nstarted_at: 2099-01-01T00:00:00Z\n")
    with pytest.raises(recheck.Busy):
        _run(vault, monkeypatch, FakeModel(), ["pier-9"])
    assert lock.exists()                  # someone else's lock is left alone
    assert call("contradictions.estimate", vault=str(vault), ids=["pier-9"])["busy"] is True
    err = call_error("jobs.recheckContradictions", vault=str(vault), ids=["pier-9"])
    assert err["code"] == "busy" and "being added" in err["message"]


def test_one_history_version_with_a_plain_cause_and_the_lock_released(vault, monkeypatch):
    fake = FakeModel(lambda ents: [_first_last(e) for e in ents if e["entity_id"] == "pier-9"])
    _run(vault, monkeypatch, fake, ["pier-9"])
    v = history.versions(vault, limit=1)["versions"][0]
    assert v["label"] == "Contradictions re-checked: Pier 9"
    assert {c["path"] for c in v["changes"]} >= {".watchdog/registry/entities.json"}
    assert not processing_lock(vault).exists()
    assert history.label({"kind": "recheck", "all": True}) == "Contradictions re-checked: the whole investigation"
    assert history.label({"kind": "recheck", "names": ["A", "B", "C"], "count": 5}) == \
        "Contradictions re-checked: A, B, C and 2 more"


def test_calls_are_recorded_in_the_usage_files(vault, monkeypatch):
    from watchdog.pipeline import orchestrate
    before = set(orchestrate.usage_files(vault))
    _run(vault, monkeypatch, FakeModel(), ["pier-9", "northgate-civil-works"])
    new = [p for p in orchestrate.usage_files(vault) if p not in before]
    assert len(new) == 1
    data = json.loads(new[0].read_text())
    assert [c["task"] for c in data["calls"]] == ["reconcile"]
    assert data["totals"]["cost_usd"] == 0.002


def test_a_stop_files_what_the_finished_calls_found(vault, monkeypatch):
    monkeypatch.setattr(chunking, "prompt_budget_chars", lambda *a, **k: 7000)
    fake = FakeModel(lambda ents: [_first_last(ents[0])])
    calls = {"n": 0}

    async def stopping(**kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt
        return await fake(**kw)
    monkeypatch.setattr(model_client, "acomplete_json", stopping)
    out = recheck.run(vault, ["northgate-civil-works"], model="sonnet", backend="claude-api",
                      say=lambda m: None, warn=lambda m: None)
    assert out["stopped"] and out["calls_done"] == 1 and len(out["filed"]) == 1
    assert history.versions(vault, limit=1)["versions"][0]["cause"].get("incomplete") is True
    assert not processing_lock(vault).exists()
    assert "Stopped before every call finished" in recheck.summary(out)


# ── the app ──────────────────────────────────────────────────────────────────────────

def test_the_estimate_prices_the_calls_on_the_finalizer_model(vault, monkeypatch, wdg_home):  # noqa: F811
    monkeypatch.setattr("watchdog.cmd.auth.resolve_auth", lambda *a, **k: {"mode": "api-key"})
    est = call("contradictions.estimate", vault=str(vault), ids=["northgate-civil-works"])
    assert est["calls"] == 1 and est["facts"] == 36 and est["busy"] is False
    assert est["model"]["backend"] == "claude-api" and est["model"]["name"]
    assert 0 < est["cost_low"] <= est["cost_high"] and est["est_tokens"] > 1000
    assert est["auth"]["ok"] is True
    whole = call("contradictions.estimate", vault=str(vault), all=True)
    assert whole["scope"] == "all" and whole["cost_high"] > est["cost_high"]
    monkeypatch.setattr("watchdog.cmd.auth.resolve_auth", lambda *a, **k: {"mode": "subscription"})
    sub = call("contradictions.estimate", vault=str(vault), ids=["pier-9"])
    assert sub["subscription"] is True and sub["cost_low"] is None and sub["real_tokens"] > 0


def test_the_app_starts_it_as_a_job_and_keeps_the_gates(vault, monkeypatch, wdg_home):  # noqa: F811
    from watchdog.gui import jobs
    started = []
    monkeypatch.setattr(jobs.MANAGER, "start", lambda v, op, params, label, kind=None: (
        started.append((op, label, params)) or type("J", (), {"to_dict": lambda self: {"id": "j"}})()))
    monkeypatch.setattr("watchdog.cmd.auth.resolve_auth", lambda *a, **k: {"mode": "none", "reason": "No key."})
    assert call_error("jobs.recheckContradictions", vault=str(vault), ids=["pier-9"])["code"] == "auth_required"
    monkeypatch.setattr("watchdog.cmd.auth.resolve_auth", lambda *a, **k: {"mode": "api-key"})
    assert call("jobs.recheckContradictions", vault=str(vault), ids=["pier-9"]) == {"id": "j"}
    op, label, params = started[-1]
    assert label == "Re-check contradictions: Pier 9"
    assert (op, params) == ("recheck-contradictions", {"ids": ["pier-9"]})
    call("jobs.recheckContradictions", vault=str(vault), all=True)
    assert started[-1][2] == {"all": True}
    monkeypatch.setenv("WATCHDOG_ENGINE_PENDING", "1")
    assert call_error("jobs.recheckContradictions", vault=str(vault), all=True)["code"] == "engine_not_ready"
    assert call_error("contradictions.estimate", vault=str(vault))["code"] == "bad_params"


def test_the_operation_refuses_a_folder_that_is_not_an_investigation(tmp_path):
    from watchdog import ops
    with pytest.raises(SystemExit) as e:
        ops.run("recheck-contradictions", {"all": True}, ops.CollectingReporter(), tmp_path)
    assert "not a Watchdog investigation" in str(e.value.code)


def test_a_failed_first_call_says_nothing_was_checked(vault, monkeypatch):
    async def failing(**kw):
        raise model_client.ProviderAuthError("Anthropic rejected the API key")
    monkeypatch.setattr(model_client, "acomplete_json", failing)
    out = recheck.run(vault, ["pier-9"], model="sonnet", backend="claude-api",
                      say=lambda m: None, warn=lambda m: None)
    assert out["error"] and out["calls_done"] == 0 and not processing_lock(vault).exists()
    assert recheck.summary(out).endswith("Nothing was checked, and nothing was filed.")
