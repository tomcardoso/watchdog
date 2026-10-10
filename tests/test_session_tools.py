"""Watchdog's tools for Ask Claude sessions (`watchdog/session_tools.py`, D299), against the demo
investigation: what each returns, that none reaches outside its investigation, and that the ones
that write keep folder access and version history."""

import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from watchdog import session_tools
from watchdog.pipeline import embed, entity_facts, fulltext, history
from watchdog.session_tools import SessionTools, ToolError

SRC = Path(__file__).resolve().parent.parent / "src"


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    root = tmp_path_factory.mktemp("session-tools-demo")
    vault = root / "vault"
    env = {**os.environ, "PYTHONPATH": str(SRC), "NO_COLOR": "1"}
    proc = subprocess.run([sys.executable, "-m", "watchdog.gui.demo", str(vault), "--home", str(root / "home")],
                          env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    return vault


@pytest.fixture
def vault(demo, tmp_path):
    copy = tmp_path / "port-calder"
    shutil.copytree(demo, copy)
    entity_facts.clear_cache()
    return copy


@pytest.fixture
def tools(vault):
    return SessionTools(vault)


def _log(v: Path) -> list[dict]:
    return [json.loads(line) for line in (history.history_dir(v) / "log.jsonl").read_text().splitlines()]


def _last_cause(v: Path) -> dict:
    return _log(v)[-1]["cause"]


def _entity(v: Path, eid: str) -> dict:
    return json.loads((v / ".watchdog" / "registry" / "entities.json").read_text())[eid]


# ── read-only tools ───────────────────────────────────────────────────────────────────────

def test_search_returns_the_query_skills_json_with_citable_facts(tools, vault, monkeypatch):
    """The semantic lane needs fastembed, so it is faked; the exact lane runs on a real index."""
    sha = next(iter(json.loads((vault / ".watchdog" / "registry" / "documents.json").read_text())))
    doc = json.loads((vault / ".watchdog" / "registry" / "documents.json").read_text())[sha]
    fact = entity_facts.FactIndex(vault).document_facts(sha)[0]
    page = fact["page"]
    fulltext.add_document(vault, doc["filename"], sha, [{"page": page, "markdown": "Zanzibarquay sold twice."}])
    monkeypatch.setattr(embed, "index_stats", lambda v: {"passages": 1, "notes": 0, "total": 1})
    seen = []

    def fake(v, query, top_n=5, min_score=0.0, scope="all", rerank=True):
        seen.append((scope, top_n, min_score, rerank))
        return ([{"filename": doc["filename"], "page": page, "text": "Lot 14 sold twice.", "score": 0.81}]
                if scope == "corpus" else [])
    monkeypatch.setattr(embed, "search", fake)

    out = json.loads(tools.search("Zanzibarquay", top=3))
    assert seen == [("corpus", 3, -1.0, True), ("notes", 3, -1.0, True)]
    assert out["query"] == "Zanzibarquay" and out["passages"][0]["score"] == 0.81
    assert any(f["cite"].startswith(f"[[{doc['document_note']}#^f-") for f in out["passages"][0]["facts"])
    assert any(h["kind"] == "corpus" and h["page"] == page for h in out["exact"])


def test_search_on_an_unindexed_investigation_says_so(tools, monkeypatch):
    monkeypatch.setattr(embed, "index_stats", lambda v: {"passages": 0, "notes": 0, "total": 0})
    out = json.loads(tools.search("anything"))
    assert out["passages"] == [] and "no search index" in out["note"]
    with pytest.raises(ToolError):
        tools.search("  ")


def test_leads_are_the_sweep_without_callout_bodies(tools):
    out = json.loads(tools.leads())
    assert out["total"] > 0
    assert {u["id"] for u in out["unprofiled"]} >= {"audit-committee-of-council"}
    callout = out["contradictions"][0]["callouts"][0]
    assert set(callout) == {"summary", "rid"}


def test_check_citations_reports_only_what_needs_attention(tools, vault):
    out = json.loads(tools.check_citations())
    assert out["checked"] >= 1 and out["citations"] == out["found"] + out["not_found"]
    (vault / "queries" / "q.md").write_text("Paid [[documents/capital-payment-register-2022#^f-0000000000|p. 1]].")
    out = json.loads(tools.check_citations(["queries/q.md"]))
    assert out["checked"] == 1 and out["not_found"] == 1
    assert out["pages_with_problems"][0]["source_not_found"] == [
        "documents/capital-payment-register-2022#^f-0000000000"]


def test_research_seen_lists_addresses_or_says_none(tools):
    assert tools.research_seen() == "No sources captured yet."


# ── confinement ───────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("page", ["../elsewhere.md", "/etc/passwd", "{outside}", "queries", "nope.md"])
def test_check_citations_reads_only_files_in_its_investigation(tools, vault, tmp_path, page):
    outside = tmp_path / "elsewhere.md"
    outside.write_text("x")
    with pytest.raises(ToolError, match="not a file in this investigation"):
        tools.check_citations([page.format(outside=outside)])


def test_a_symlink_out_of_the_investigation_is_refused(tools, vault, tmp_path):
    secret = tmp_path / "credentials.json"
    secret.write_text('{"anthropic_api_key": "sk-not-real"}')
    (vault / "queries" / "link.md").symlink_to(secret)
    with pytest.raises(ToolError):
        tools.check_citations(["queries/link.md"])


def test_no_tool_takes_a_folder_project_or_file_outside_the_investigation():
    """The confinement the old commands enforced in code (D257) is structural here: a tool set is
    bound to one investigation, and the only path any tool takes is a page inside it."""
    for name in session_tools.TOOLS:
        props = session_tools.SCHEMAS[name][1]["properties"]
        assert not {"vault", "project", "path", "batch", "everywhere", "extraction"} & set(props)
        assert session_tools.SCHEMAS[name][1].get("additionalProperties") is False
    assert set(session_tools.SCHEMAS["check_citations"][1]["properties"]) == {"pages"}


def test_writes_are_refused_where_watchdog_has_no_folder_access(vault, tmp_path, monkeypatch):
    access = tmp_path / "access.json"
    access.write_text(json.dumps({"version": 1, "folders": []}))
    monkeypatch.setenv("WATCHDOG_ENFORCE_ACCESS", "1")
    monkeypatch.setenv("WATCHDOG_ACCESS_FILE", str(access))
    tools = SessionTools(vault)
    before = (vault / "watchlist.md").read_text()
    for call in (lambda: tools.watchlist_add(["Harbourgate"]), tools.timeline,
                 lambda: tools.write_entity("robert-delacroix", "x", []),
                 lambda: tools.contradiction_add("robert-delacroix", "l", "a", "x", "b", "y")):
        with pytest.raises(ToolError, match="hasn't been allowed"):
            call()
    assert (vault / "watchlist.md").read_text() == before
    json.loads(tools.leads())                         # reading is not a change


def test_a_folder_that_is_no_longer_an_investigation_is_refused(tmp_path):
    with pytest.raises(ToolError, match="no longer a Watchdog investigation"):
        SessionTools(tmp_path).leads()


# ── tools that write: the library function, the lock, a version of the history ────────────

def test_write_entity_stores_the_refresh_as_one_session_version(tools, vault):
    events = [{"date": "2022-02-08", "event": "Voted on the purchase", "source_sha256": None, "page": 3}]
    out = tools.write_entity("robert-delacroix", "A councillor named in the purchase records.", events)
    assert out.startswith("Refreshed: ") and "1 timeline events" in out
    entry = _entity(vault, "robert-delacroix")
    assert entry["synthesis"]["summary"] == "A councillor named in the purchase records."
    assert entry["synthesis"]["by"] == "session" and len(entry["timeline_events"]) == 1
    cause = _last_cause(vault)
    assert cause == {"kind": "session", "tool": "write_entity"}
    assert history.label(cause) == "Written in an Ask Claude session"
    changed = json.dumps(_log(vault)[-1])
    assert "entities/person/robert-delacroix.md" in changed and "entities.json" in changed


def test_write_entity_reports_an_unknown_entity(tools):
    with pytest.raises(ToolError, match="not found"):
        tools.write_entity("nobody-here", "x", [])


def test_watchlist_add_appends_dedupes_and_records(tools, vault):
    out = json.loads(tools.watchlist_add(["Harbourgate Surety", "harbourgate surety", "Pier 9"]))
    assert out == {"added": ["Harbourgate Surety", "Pier 9"], "skipped": 1}
    assert "Pier 9" in (vault / "watchlist.md").read_text()
    assert _last_cause(vault) == {"kind": "session", "tool": "watchlist_add"}


def test_contradiction_add_writes_the_callout_and_records(tools, vault):
    out = tools.contradiction_add("robert-delacroix", "Date of the vote", "8 February 2022",
                                  "council-minutes-2022-02-08", "15 February 2022",
                                  "capital-payment-register-2022", a_page=3)
    assert out.startswith("Added the contradiction to ")
    assert any("Date of the vote" in c for c in _entity(vault, "robert-delacroix")["contradictions"])
    assert _last_cause(vault) == {"kind": "session", "tool": "contradiction_add"}
    again = tools.contradiction_add("robert-delacroix", "Date of the vote", "8 February 2022",
                                    "council-minutes-2022-02-08", "15 February 2022",
                                    "capital-payment-register-2022", a_page=3)
    assert again.startswith("Already recorded")
    with pytest.raises(ToolError, match="not found"):
        tools.contradiction_add("robert-delacroix", "l", "a", "no-such-doc", "b", "capital-payment-register-2022")


def test_timeline_rebuilds_and_records(tools, vault):
    (vault / "timeline.md").unlink()
    assert tools.timeline().startswith("timeline.md rebuilt: ")
    assert (vault / "timeline.md").is_file()
    assert _last_cause(vault) == {"kind": "session", "tool": "timeline"}


# ── the in-process MCP server ─────────────────────────────────────────────────────────────

def test_the_server_is_named_watchdog_and_serves_every_tool(vault, monkeypatch):
    import claude_agent_sdk
    captured = {}

    def fake_server(name, version="1.0.0", tools=None):
        captured.update(name=name, tools=tools)
        return {"type": "sdk", "name": name}
    monkeypatch.setattr(claude_agent_sdk, "create_sdk_mcp_server", fake_server)
    assert session_tools.sdk_server(vault)["name"] == "watchdog"
    served = {t.name: t for t in captured["tools"]}
    assert set(served) == set(session_tools.TOOLS)
    for name, t in served.items():
        hints = t.annotations.model_dump(by_alias=True)
        assert hints["readOnlyHint"] is (name in session_tools.READ_TOOLS) and hints["destructiveHint"] is False
    out = asyncio.run(served["leads"].handler({}))
    assert "is_error" not in out and json.loads(out["content"][0]["text"])["total"] > 0
    err = asyncio.run(served["check_citations"].handler({"pages": ["../../x"]}))
    assert err["is_error"] is True and "not a file in this investigation" in err["content"][0]["text"]
    assert session_tools.tool_name("search") == "mcp__watchdog__search"


def test_the_real_server_builds(vault):
    server = session_tools.sdk_server(vault)
    assert server["type"] == "sdk" and server["name"] == "watchdog" and server["instance"] is not None
