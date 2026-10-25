"""Where document requests can be got, grouped across wordings (D334): the checked model chore,
the mapping Review groups by, and a reporter's split surviving later runs. Every name is invented."""

import asyncio
import json
import re

from watchdog import model_client
from watchdog.pipeline import history, orchestrate, request_sources, requests

from tests.gui_support import call
from tests.test_write_vault import make_vault


def _result(parsed):
    return model_client.ModelResult(parsed=parsed, text="", model="fake-model", backend="claude-api",
                                    auth_mode="api-key", usage={"input_tokens": 1, "output_tokens": 1},
                                    cost_usd=0.0, latency_s=0.0)


def _record(vault, sha, *pairs):
    requests.record(vault, [{"type": "Record", "what": what, "why_it_matters": "x", "likely_source": src}
                            for what, src in pairs],
                    sha256=sha, filename=f"{sha}.pdf", document_note=f"documents/{sha}")


def _labeller(same: dict[str, str]):
    """A canned model: the label `same` gives an item whose text contains a key, else its own."""
    def answer(prompt):
        out = []
        for m in re.finditer(r'^(\d+)\. "(.*?)" \(', prompt, re.M):
            label = next((v for k, v in same.items() if k in m.group(2).lower()), m.group(2))
            out.append({"item": int(m.group(1)), "label": label})
        return {"answers": out}
    return answer


def _run(vault, monkeypatch, answer, seen=None):
    async def fake(**kw):
        assert kw["task"] == "request-sources" and kw["schema"] is request_sources.SCHEMA
        if seen is not None:
            seen.append(kw["prompt"])
        return _result(answer(kw["prompt"]))
    monkeypatch.setattr(orchestrate, "_call_model", fake)
    out: dict = {}
    asyncio.run(orchestrate._request_source_pass(vault, out, "haiku", None, None))
    return out


def _groups(vault):
    rows, groups = requests.worklist(requests.open_requests(vault),
                                     request_sources.mapping(request_sources.load(vault)))
    return rows, groups


def _vault(tmp_path):
    vault = make_vault(tmp_path)
    _record(vault, "sha1",
            ("Deed of transfer 41", "Westmarch Land Titles Office"),
            ("Survey plan 9", "Land titles registry (Westmarch), by search"),
            ("Minutes of 3 May", "Town Clerk"))
    return vault


def test_the_pass_groups_sources_and_review_uses_the_mapping(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    before = json.loads(requests._path(vault).read_text(encoding="utf-8"))
    seen = []
    out = _run(vault, monkeypatch, _labeller({"land titles": "Westmarch Land Titles Office"}), seen)
    assert len(seen) == 1 and '"Town Clerk"' in seen[0]
    assert out["request_source_groups"] == 1

    (g,) = request_sources.load(vault)["groups"]
    assert g["keys"] == ["land titles registry", "westmarch land titles office"]
    assert (g["label"], g["decided_by"], g["model"], g["status"]) == \
        ("Westmarch Land Titles Office", "model", "fake-model", "active")
    # The requests' own text is never rewritten.
    assert json.loads(requests._path(vault).read_text(encoding="utf-8")) == before

    rows, groups = _groups(vault)
    by_what = {r["what"]: r["source_key"] for r in rows}
    assert by_what["Deed of transfer 41"] == by_what["Survey plan 9"] == g["id"]
    top = next(x for x in groups if x["key"] == g["id"])
    assert (top["label"], top["count"], top["grouped_by"]) == ("Westmarch Land Titles Office", 2, "model")
    assert set(top["wordings"]) == {"Westmarch Land Titles Office", "Land titles registry"}
    clerk = next(x for x in groups if x["key"] == "town clerk")
    assert clerk["grouped_by"] is None

    # Every source has been put to the model: no call until a new one arrives.
    _run(vault, monkeypatch, _labeller({}), seen)
    assert len(seen) == 1


def test_a_malformed_answer_groups_nothing(tmp_path, monkeypatch):
    vault = _vault(tmp_path)

    def bad(prompt):
        n = len(re.findall(r'^\d+\. ', prompt, re.M))
        return {"answers": [{"item": 1, "label": "x" * 200}, {"item": 2, "label": ["Land"]},
                            {"item": n, "label": "[[link]]"}, {"item": 99, "label": "Town Clerk"}]}

    out = _run(vault, monkeypatch, bad)
    assert "request_source_groups" not in out
    assert request_sources.load(vault)["groups"] == []
    log = (vault / ".watchdog" / "registry" / "processing.log").read_text(encoding="utf-8")
    assert "request sources: 4 answer(s) failed the checks" in log


def test_validate():
    assert request_sources.validate({}, {"label": "  Town   Clerk "}) == "Town Clerk"
    assert request_sources.validate({}, {"label": ""}) is None
    assert request_sources.validate({}, {"label": None}) is None
    assert request_sources.validate({}, {"label": "a|b"}) is None


def test_a_reporter_split_survives_the_next_run(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    _run(vault, monkeypatch, _labeller({"land titles": "Westmarch Land Titles Office"}))
    (g,) = request_sources.load(vault)["groups"]

    r = call("vault.requestSourceSplit", vault=str(vault), group=g["id"])
    assert r["status"] == "split" and r["group"] == g["id"]
    assert history.label({"kind": "request_source_split"}).startswith("Request sources shown apart")
    _, groups = _groups(vault)
    assert not any(x["grouped_by"] for x in groups)

    # A new source arrives; the model would put all three together again, which joins two sources
    # the reporter split, so that group is dropped and the other one it proposes stands.
    _record(vault, "sha2", ("Charge 12", "Westmarch land titles, via online search"),
            ("By-law 7", "Clerk of the Town"))
    seen = []
    _run(vault, monkeypatch, _labeller({"land titles": "Land Titles", "clerk": "Town Clerk"}), seen)
    assert len(seen) == 1
    active = [x for x in request_sources.load(vault)["groups"] if x["status"] == "active"]
    assert [x["keys"] for x in active] == [["clerk of the town", "town clerk"]]
    log = (vault / ".watchdog" / "registry" / "processing.log").read_text(encoding="utf-8")
    assert "joined sources you showed apart" in log


def test_a_new_source_joins_an_existing_group_which_it_supersedes(tmp_path, monkeypatch):
    vault = _vault(tmp_path)
    _run(vault, monkeypatch, _labeller({"land titles": "Westmarch Land Titles Office"}))
    _record(vault, "sha2", ("Charge 12", "Westmarch Land Titles, online search"))
    seen = []
    _run(vault, monkeypatch, _labeller({"land titles": "Westmarch Land Titles Office"}), seen)
    # The existing group is shown once, under its label; the new source first.
    assert seen[0].index('"Westmarch Land Titles"') < seen[0].index('"Westmarch Land Titles Office"')
    groups = request_sources.load(vault)["groups"]
    assert [x["status"] for x in groups] == ["superseded", "active"]
    assert groups[1]["keys"] == ["land titles registry", "westmarch land titles",
                                 "westmarch land titles office"]


def test_a_failed_call_leaves_the_sources_for_the_next_run(tmp_path, monkeypatch):
    vault = _vault(tmp_path)

    async def boom(**kw):
        raise model_client.ModelError("down")

    monkeypatch.setattr(orchestrate, "_call_model", boom)
    asyncio.run(orchestrate._request_source_pass(vault, {}, "haiku", None, None))
    data = request_sources.load(vault)
    assert data["groups"] == [] and data["evaluated"] == []


def test_no_call_with_a_single_source(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _record(vault, "sha1", ("Deed 1", "Town Clerk"), ("Deed 2", "the Town Clerk, by request"))

    async def never(**kw):
        raise AssertionError("no call expected")

    monkeypatch.setattr(orchestrate, "_call_model", never)
    asyncio.run(orchestrate._request_source_pass(vault, {}, "haiku", None, None))


def test_the_pass_counts_towards_the_finalize_estimate(tmp_path):
    from watchdog.pipeline import ingest_setup
    vault = _vault(tmp_path)
    est = ingest_setup.housekeeping_estimate(vault, [])
    assert est["items"] == 3 and est["calls"] == 1 and est["est_tokens"] > 0
    assert "request-sources" in orchestrate.FINALIZE_TASKS
