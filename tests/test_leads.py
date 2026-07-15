import argparse
import json
import re
from pathlib import Path


from watchdog.pipeline import leads
from watchdog.cmd.leads import cmd_leads


def _strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


def _registry():
    """Alice -> Director of unprofiled 'Shell Co'; Bob also names Shell Co; an isolated
    frequent entity; an entity carrying a contradiction flag; and an entity carrying
    inferred (basis: inferred) roles/timeline events."""
    return {
        "alice": {
            "id": "alice", "name": "Alice Smith", "type": "person", "appears_in": ["d1", "d2"],
            "roles": [
                {"relationship": "Director", "target_id": "shell-co", "target_name": "Shell Co",
                 "source_sha256": "d1", "is_reverse": False},
            ],
        },
        "bob": {
            "id": "bob", "name": "Bob Jones", "type": "person", "appears_in": ["d2"],
            "roles": [
                {"relationship": "Owner", "target_id": "shell-co", "target_name": "Shell Co",
                 "source_sha256": "d2", "is_reverse": False},
                # a profiled target -> not a lead
                {"relationship": "Knows", "target_id": "alice", "target_name": "Alice Smith",
                 "source_sha256": "d2", "is_reverse": False},
            ],
        },
        "john": {
            "id": "john", "name": "John Roe", "type": "person",
            "appears_in": ["d1", "d2", "d3", "d4"], "roles": [],
        },
        "acme": {
            "id": "acme", "name": "Acme Ltd", "type": "company", "appears_in": ["d1"],
            "note_path": "entities/company/acme", "roles": [],
            "contradictions": ["address differs from d3", "director count differs"],
        },
        "carol": {
            "id": "carol", "name": "Carol White", "type": "person", "appears_in": ["d5"],
            "note_path": "entities/person/carol",
            "roles": [
                {"relationship": "Advisor", "target_id": "alice", "target_name": "Alice Smith",
                 "source_sha256": "d5", "is_reverse": False, "basis": "inferred"},
                {"relationship": "Knows", "target_id": "acme", "target_name": "Acme Ltd",
                 "source_sha256": "d5", "is_reverse": False, "basis": "stated"},
            ],
            "timeline_events": [
                {"date": "2020-01-01", "event": "Joined the board", "basis": "inferred",
                 "source_sha256": "d5"},
                {"date": "2021-01-01", "event": "Resigned", "basis": "stated",
                 "source_sha256": "d5"},
            ],
        },
    }


# ── find_leads ──────────────────────────────────────────────────────────────────

def test_unprofiled_aggregates_across_mentions():
    data = leads.find_leads(_registry())
    assert len(data["unprofiled"]) == 1
    u = data["unprofiled"][0]
    assert u["name"] == "Shell Co"
    assert u["mentioned_by"] == ["Alice Smith", "Bob Jones"]
    assert u["doc_count"] == 2                       # d1 + d2


def test_profiled_target_is_not_a_lead():
    # bob -> alice is a role to a profiled entity; alice must never appear as unprofiled
    data = leads.find_leads(_registry())
    assert all(u["id"] != "alice" for u in data["unprofiled"])


def test_isolated_requires_threshold():
    data = leads.find_leads(_registry())
    ids = {i["id"] for i in data["isolated"]}
    assert "john" in ids          # 4 docs, no roles
    assert "acme" not in ids      # only 1 doc -> below threshold
    assert "alice" not in ids     # has a role


def test_contradictions_listed():
    data = leads.find_leads(_registry())
    assert len(data["contradictions"]) == 1
    c = data["contradictions"][0]
    assert c["name"] == "Acme Ltd"
    assert c["count"] == 2
    assert c["note_path"] == "entities/company/acme"


def test_inferred_listed():
    data = leads.find_leads(_registry())
    assert len(data["inferred"]) == 1
    i = data["inferred"][0]
    assert i["name"] == "Carol White"
    assert i["note_path"] == "entities/person/carol"
    assert i["claims"] == ["Advisor → Alice Smith", "2020-01-01: Joined the board"]


def test_inferred_excludes_stated_only_entities():
    # bob and acme carry only basis:"stated" (or unset) roles/events -> never a lead
    data = leads.find_leads(_registry())
    ids = {i["id"] for i in data["inferred"]}
    assert "bob" not in ids
    assert "acme" not in ids


def test_total_and_empty():
    assert leads.total(leads.find_leads(_registry())) == 4
    assert leads.total(leads.find_leads({})) == 0


# ── writer -> sweep contract (#252) ──────────────────────────────────────────
#
# The tests above fabricate a registry entry with a hand-built "contradictions" key.
# That masked a real bug: nothing in write_vault ever persisted that key, so the signal
# could never fire against a real vault. This test drives the actual writer instead, to
# prove entities.json really ends up in the shape find_leads expects.

def test_write_vault_persists_contradictions_findable_by_leads(tmp_path):
    from watchdog.pipeline import write_vault
    from tests.test_write_vault import make_vault, make_extraction

    vault = make_vault(tmp_path)
    (vault / "_INCOMING" / "test-doc.pdf").write_text("dummy")
    callout = "> [!contradiction] Address differs from prior filing"
    overrides = {"entities": [{"id": "alice-smith", "name": "Alice Smith", "type": "Person",
                               "contradictions": [callout]}]}

    write_vault.run(make_extraction(tmp_path, overrides), vault)

    entities_reg = json.loads(
        (vault / ".watchdog" / "registry" / "entities.json").read_text()
    )
    data = leads.find_leads(entities_reg)
    assert len(data["contradictions"]) == 1
    assert data["contradictions"][0]["id"] == "alice-smith"
    assert data["contradictions"][0]["count"] == 1


def test_dangling_without_target_name_falls_back_to_id():
    reg = {"x": {"id": "x", "name": "X", "type": "person", "appears_in": ["d1"],
                 "roles": [{"relationship": "Linked", "target_id": "ghost-123",
                            "source_sha256": "d1", "is_reverse": False}]}}
    data = leads.find_leads(reg)
    assert data["unprofiled"][0]["name"] == "ghost-123"


# ── write_leads ─────────────────────────────────────────────────────────────────

def test_write_leads_snapshot(tmp_path):
    vault = tmp_path
    relpath = leads.write_leads(vault, leads.find_leads(_registry()))
    assert relpath.startswith("briefings/leads-")
    body = (vault / relpath).read_text(encoding="utf-8")
    assert "Named but never profiled" in body
    assert "Shell Co" in body
    assert "Mentioned often but unconnected" in body
    assert "Unresolved contradictions" in body
    assert "[[entities/company/acme|Acme Ltd]]" in body
    assert "Inferred facts to verify" in body
    assert "[[entities/person/carol|Carol White]]" in body
    assert "Advisor → Alice Smith" in body


def test_write_leads_empty_writes_nothing(tmp_path):
    assert leads.write_leads(tmp_path, leads.find_leads({})) is None
    assert not (tmp_path / "briefings").exists()


def test_write_leads_overwrites_not_appends(tmp_path):
    leads.write_leads(tmp_path, leads.find_leads(_registry()))
    relpath = leads.write_leads(tmp_path, leads.find_leads(_registry()))
    body = (tmp_path / relpath).read_text(encoding="utf-8")
    assert body.count("# Investigative leads") == 1


# ── scan + cmd_leads ────────────────────────────────────────────────────────────

def _vault(tmp_path, registry) -> Path:
    vault = tmp_path / "vault"
    reg = vault / ".watchdog" / "registry"
    reg.mkdir(parents=True)
    (reg / "entities.json").write_text(json.dumps(registry), encoding="utf-8")
    return vault


def _args(**kw):
    return argparse.Namespace(**{"project": None, **kw})


def test_scan_reads_registry(tmp_path):
    vault = _vault(tmp_path, _registry())
    assert leads.total(leads.scan(vault)) == 4


def test_cmd_leads_prints_sections(tmp_path, monkeypatch, capsys):
    vault = _vault(tmp_path, _registry())
    monkeypatch.chdir(vault)
    cmd_leads(_args())
    out = _strip_ansi(capsys.readouterr().out)
    assert "Named but never profiled  (1)" in out
    assert "Shell Co" in out
    assert "Mentioned often but unconnected  (1)" in out
    assert "Unresolved contradictions  (1)" in out
    assert "Inferred facts to verify  (1)" in out
    assert "Carol White" in out
    assert "Advisor → Alice Smith" in out


def test_cmd_leads_empty(tmp_path, monkeypatch, capsys):
    vault = _vault(tmp_path, {})
    monkeypatch.chdir(vault)
    cmd_leads(_args())
    out = _strip_ansi(capsys.readouterr().out)
    assert "No leads" in out


# ── resolution filtering (#266) ─────────────────────────────────────────────────

def test_resolved_leads_drop_out_of_active_list():
    reg = _registry()
    resolved = frozenset({leads.resolutions.lead_id("isolated", "john"),
                          leads.resolutions.lead_id("inferred", "carol"),
                          leads.resolutions.lead_id("unprofiled", "shell-co")})
    data = leads.find_leads(reg, resolved)
    assert data["isolated"] == []
    assert data["inferred"] == []
    assert data["unprofiled"] == []
    # The contradiction entity is untouched (its callouts weren't resolved).
    assert len(data["contradictions"]) == 1


def test_resolving_one_contradiction_callout_leaves_the_other():
    reg = _registry()
    resolved = frozenset({leads.resolutions.contradiction_id("address differs from d3")})
    data = leads.find_leads(reg, resolved)
    c = data["contradictions"][0]
    assert c["count"] == 1
    assert [x["summary"] for x in c["callouts"]] == ["director count differs"]


def test_resolving_all_callouts_drops_the_entity():
    reg = _registry()
    resolved = frozenset({
        leads.resolutions.contradiction_id("address differs from d3"),
        leads.resolutions.contradiction_id("director count differs"),
    })
    assert leads.find_leads(reg, resolved)["contradictions"] == []


def test_every_active_item_carries_a_rid():
    data = leads.find_leads(_registry())
    assert all("rid" in u for u in data["unprofiled"])
    assert all("rid" in i for i in data["isolated"])
    assert all("rid" in i for i in data["inferred"])
    assert all("rid" in x for c in data["contradictions"] for x in c["callouts"])


def test_format_renders_checkboxes_with_wid_markers():
    import datetime
    body = leads._format(leads.find_leads(_registry()), datetime.datetime(2025, 1, 1))
    assert "- [ ] **John Roe**" in body
    assert "<!--wid:lead:isolated:john-->" in body
    assert "<!--wid:lead:unprofiled:shell-co-->" in body
    assert "<!--wid:contradiction:" in body


def test_scan_honors_resolutions_json(tmp_path):
    vault = _vault(tmp_path, _registry())
    from watchdog.pipeline import resolutions
    resolutions.resolve(vault, [resolutions.lead_id("isolated", "john")])
    assert not any(i["id"] == "john" for i in leads.scan(vault)["isolated"])
