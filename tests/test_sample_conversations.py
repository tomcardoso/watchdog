"""Prepared Ask Claude conversations in the sample investigation (D309).

Every prepared answer cites facts the freshly built sample holds, before and after its two
waiting files are added; the conversations come back in the chat stream's own shape; the one that
writes stops at the real permission card and changes the sample only when allowed, through the
session tool; and nothing constructs a Claude Agent SDK client or imports the SDK at all."""

import json
import re
import sys
from pathlib import Path

import pytest

from tests.gui_support import call, call_error
from tests.test_sample import _create, _home
from tests.test_sample_run import _guard
from watchdog import ops, telemetry_db
from watchdog.gui import chat, demo_conversations
from watchdog.ops import CollectingReporter
from watchdog.pipeline import citations

pytest_plugins = ["tests.gui_support"]

IDS = [q["id"] for q in demo_conversations.questions()]


def _no_client(*_a, **_k):
    raise AssertionError("a prepared conversation constructed a Claude client")


def _play_all(vault: Path) -> dict:
    """Every conversation, played to its end; the writing one allowed."""
    out = {}
    for cid in IDS:
        r = call("sample.conversation", vault=str(vault), id=cid)
        events = list(r["events"])
        if r["waiting"]:
            events += call("sample.conversationAnswer", vault=str(vault), id=cid,
                           request_id=r["waiting"], allow=True)["events"]
        out[cid] = {**r, "all": events}
    return out


@pytest.fixture(scope="module")
def played(tmp_path_factory):
    root = tmp_path_factory.mktemp("sample-ask")
    mp = pytest.MonkeyPatch()
    try:
        _home(mp, root)
        _guard(mp)
        # No SDK during playback: importing it fails, and so would making a client.
        mp.setitem(sys.modules, "claude_agent_sdk", None)
        mp.setattr(chat, "make_client", _no_client)
        result, _ = _create()
        vault = Path(result["path"])
        watch_before = (vault / "watchlist.md").read_text(encoding="utf-8")
        before = _play_all(vault)
        again = call("sample.conversationAnswer", vault=str(vault), id="watch",
                     request_id=before["watch"]["waiting"], allow=True)["events"]
        ops.run("add", {}, CollectingReporter(), vault)
        after = _play_all(vault)
        yield {"vault": vault, "before": before, "after": after, "again": again,
               "watch_before": watch_before}
    finally:
        telemetry_db.close()
        mp.undo()


def _messages(events: list[dict], role: str) -> list[dict]:
    return [e["data"]["message"] for e in events
            if e["event"] == "chat.message" and e["data"]["message"]["role"] == role]


def _answer(events: list[dict]) -> str:
    return _messages(events, "assistant")[-1]["text"]


def _links(text: str) -> list[str]:
    return [f"{m.group(1)}#^{m.group(2)}" for m in citations.FACT_LINK_RE.finditer(text)]


@pytest.mark.parametrize("when", ["before", "after"])
def test_every_citation_resolves_in_the_sample(played, when):
    vault = played["vault"]
    for cid in IDS:
        events = played[when][cid]["all"]
        texts = [m["text"] for m in _messages(events, "assistant")]
        texts += [m["tool"]["result"] or "" for m in _messages(events, "tool")]
        links = sorted({k for t in texts for k in _links(t)})
        if cid != "watch":
            assert len(links) >= 4, cid
        status = call("vault.citations", vault=str(vault), links=links)
        missing = [k for k in links if status[k]["status"] != "found"]
        assert not missing, f"{cid} ({when}) cites facts the sample lacks: {missing}"
        # The answer's links are written as a session writes them: in brackets, the page as text.
        for m in citations.FACT_LINK_RE.finditer(_answer(events)):
            assert re.fullmatch(r"p\. \d+(, disputed)?", m.group(3)), m.group(0)


def test_the_answers_read_what_the_sample_holds(played):
    before, after = played["before"], played["after"]
    # Before the waiting files are added: no change order, and the answer says files are waiting.
    assert "holds no change order" in _answer(before["change-orders"]["all"])
    assert "2 files are still waiting" in _answer(before["change-orders"]["all"])
    assert "pier-9-change-order-3" not in _answer(before["change-orders"]["all"])
    # After: Change Order 3 is cited, and the site meeting about paying Tideway directly.
    assert "documents/pier-9-change-order-3#^f-" in _answer(after["change-orders"]["all"])
    assert "documents/site-meeting-notes-2022-09-14#^f-" in _answer(after["tideway"]["all"])
    assert "site-meeting" not in _answer(before["tideway"]["all"])
    # The disputed payment is cited as disputed, as the citation checker labels it.
    assert "p. 1, disputed]]" in _answer(before["dockside"]["all"])


def test_conversations_come_in_the_chat_streams_shape(played):
    for cid in IDS:
        r = played["before"][cid]
        assert r["session"] == f"sample-{cid}"
        events = r["events"]
        assert {e["event"] for e in events} <= {"chat.message", "chat.delta", "chat.status", "chat.permission"}
        assert all(e["data"]["session"] == r["session"] and e["after_ms"] >= 0 for e in events)
        first = events[0]["data"]["message"]
        q = next(q for q in demo_conversations.questions() if q["id"] == cid)
        assert first["role"] == "user" and first["text"] == q["question"]
        assert events[1]["event"] == "chat.status" and events[1]["data"]["state"] == "thinking"
        # Claude's text streams in, then arrives whole under the same id.
        deltas = [e["data"] for e in events if e["event"] == "chat.delta"]
        streamed = {}
        for d in deltas:
            streamed[d["message_id"]] = streamed.get(d["message_id"], "") + d["text"]
        for m in _messages(events, "assistant"):
            assert streamed[m["id"]] == m["text"]
        # Each tool row shows a call and then its result.
        tools = _messages(r["all"], "tool")
        last = {t["id"]: t for t in tools}
        assert tools and all(t["tool"]["result"] for t in last.values())
        assert all(t["tool"]["result"] is None for t in tools[:1])
        if not q["writes"]:
            assert r["waiting"] is None
            assert events[-1]["data"]["state"] == "idle"
            assert any(t["tool"]["name"] == "mcp__watchdog__search" and t["tool"]["input"]["query"] for t in tools)
            # A few seconds of playback, not a minute.
            assert 2_000 < sum(e["after_ms"] for e in events) < 12_000


def test_the_write_stops_at_the_real_permission_card_then_applies_through_the_tool(played):
    vault = played["vault"]
    r = played["before"]["watch"]
    perm = r["events"][-1]
    assert perm["event"] == "chat.permission" and perm["data"]["request_id"] == r["waiting"]
    ask = chat.permission_request(vault, "mcp__watchdog__watchlist_add", {"terms": ["Tideway Marine Services"]})
    for k in ("title", "description", "scope", "once_only", "note"):
        assert perm["data"][k] == ask[k]
    assert "Tideway Marine Services" not in played["watch_before"]
    assert "Tideway Marine Services" in (vault / "watchlist.md").read_text(encoding="utf-8")
    result = json.loads(_messages(r["all"], "tool")[-1]["tool"]["result"])
    assert result == {"added": ["Tideway Marine Services"], "skipped": 0}
    assert "is on the watch list" in _answer(r["all"])
    # The second part's messages never replace the first part's (the ids differ).
    ids = [m["id"] for m in _messages(r["all"], "user") + _messages(r["all"], "assistant")]
    assert len(ids) == len(set(ids)) == 3
    # The change is a version of the watch list's history, as a session's would be.
    hist = call("history.file", vault=str(vault), path="watchlist.md")
    assert any((v.get("cause") or {}).get("tool") == "watchlist_add" for v in hist["versions"])
    # Asked again, nothing changes.
    assert "already on the watch list" in _answer(played["again"])


def test_declining_changes_nothing(played, monkeypatch):
    vault = played["vault"]
    before = (vault / "watchlist.md").read_text(encoding="utf-8")
    events = call("sample.conversationAnswer", vault=str(vault), id="watch",
                  request_id="sample-watch-ask", allow=False)["events"]
    tool = _messages(events, "tool")[-1]["tool"]
    assert tool["is_error"] and "declined" in tool["result"]
    assert "haven't changed the watch list" in _answer(events)
    assert (vault / "watchlist.md").read_text(encoding="utf-8") == before


def test_only_in_the_sample_and_only_known_conversations(played, tmp_path):
    vault = played["vault"]
    assert call_error("sample.conversation", vault=str(vault), id="nope")["code"] == "not_found"
    assert call_error("sample.conversationAnswer", vault=str(vault), id="tideway",
                      request_id="x", allow=True)["code"] == "not_found"
    marker = vault / ".watchdog" / "sample.json"
    saved = marker.read_bytes()
    marker.unlink()
    try:
        for m, extra in (("sample.questions", {}), ("sample.conversation", {"id": "tideway"})):
            assert call_error(m, vault=str(vault), **extra)["code"] == "not_sample"
    finally:
        marker.write_bytes(saved)


def test_a_real_session_is_still_refused_in_the_sample(played):
    from watchdog.ops import sample
    err = call_error("chat.start", vault=str(played["vault"]), mode="ask", prompt="Who is Pike?")
    assert err["code"] == "sample" and err["message"] == sample.CHAT_REFUSAL


def test_a_missing_fact_is_reported_not_shown(played, monkeypatch):
    real = demo_conversations.Facts.find

    def without_contract(self, doc, phrase):
        return None if doc == demo_conversations.CONTRACT else real(self, doc, phrase)
    monkeypatch.setattr(demo_conversations.Facts, "find", without_contract)
    err = call_error("sample.conversation", vault=str(played["vault"]), id="tideway")
    assert err["code"] == "not_found" and "Rebuild the sample" in err["message"]
