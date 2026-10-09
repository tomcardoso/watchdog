"""The verification ledger (D271): stable fact ids, marks that survive re-processing only when the
fact's words do, the generated verification.md, format versions, the CLI command, the RPC methods
and the export."""

import argparse
import csv
import json

import pytest

from watchdog.pipeline import verification as V
from tests.gui_support import SHA1, SHA2, call, call_error
from tests.test_write_vault import make_vault

pytest_plugins = ["tests.gui_support"]   # the rich_vault / wdg_home fixtures

SHA = "ab" * 32


def _vault(tmp_path, facts, sha=SHA):
    vault = make_vault(tmp_path)
    (vault / ".watchdog" / "registry" / "documents.json").write_text(json.dumps({sha: {
        "sha256": sha, "filename": "minutes.pdf", "title": "Council minutes",
        "document_note": "documents/minutes"}}))
    _stage(vault, facts, sha)
    return vault


def _stage(vault, facts, sha=SHA):
    d = vault / ".watchdog" / "extracted"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sha}.json").write_text(json.dumps({"document": {"sha256": sha, "key_facts": facts}}))


FACTS = [{"fact": "Council paid $1.2 million.", "page": 2, "entities": ["city"]},
         {"fact": "The Mayor signed the contract.", "page": 3}]


# ── identity ──────────────────────────────────────────────────────────────────────────

def test_ids_are_stable_and_depend_on_document_page_and_words():
    a = V.fact_ids(SHA, FACTS)
    assert a == V.fact_ids(SHA, [dict(f) for f in FACTS])
    assert a[0].startswith(f"fact:{V.ID_SCHEME}:{SHA[:12]}:") and V.sha_prefix(a[0]) == SHA[:12]
    # order, entities, basis and annotations don't matter; case and spacing don't either
    assert V.fact_ids(SHA, [{**FACTS[0], "entities": ["other"], "basis": "inferred",
                             "passage": "x", "fact": "council  PAID $1.2 million."}]) == a[:1]
    assert V.fact_ids(SHA, list(reversed(FACTS))) == list(reversed(a))
    # a figure, a page or a document is a different fact
    assert V.fact_ids(SHA, [{**FACTS[0], "fact": "Council paid $12 million."}]) != a[:1]
    assert V.fact_ids(SHA, [{**FACTS[0], "page": 3}]) != a[:1]
    assert V.fact_ids("cd" * 32, FACTS[:1]) != a[:1]


def test_exact_duplicates_get_numbered_ids():
    ids = V.fact_ids(SHA, [FACTS[0], FACTS[0], FACTS[1]])
    assert ids[1] == ids[0] + ":2" and len(set(ids)) == 3


def test_an_id_from_another_scheme_is_not_read():
    assert V.sha_prefix("fact:9:abc:def") == "" and V.sha_prefix("lead:x:y") == ""


# ── marking ───────────────────────────────────────────────────────────────────────────

def test_mark_records_who_when_and_renders_the_note(tmp_path, wdg_home):
    vault = _vault(tmp_path, FACTS)
    fid = V.fact_ids(SHA, FACTS)[0]
    entry = V.mark(vault, fid, "disputed", note="  Finance says $1.1M. ", by="Jordan Ellis")
    assert entry["status"] == "disputed" and entry["note"] == "Finance says $1.1M."
    assert entry["by"] == "Jordan Ellis" and entry["at"] and entry["fact"] == FACTS[0]["fact"]
    stored = json.loads((vault / ".watchdog/registry/verification.json").read_text())
    assert stored["schema_version"] == 1 and stored["marks"][fid]["page"] == 2
    md = (vault / "verification.md").read_text()
    assert "format_version: 1" in md
    assert "**0 of 2 facts verified** · 1 disputed" in md
    assert "## Disputed (1)" in md and "[[documents/minutes|Council minutes]]" in md
    assert "- p. 2: Council paid $1.2 million." in md and "Finance says $1.1M." in md


def test_changing_a_mark_keeps_history_and_clear_keeps_it_too(tmp_path, wdg_home):
    vault = _vault(tmp_path, FACTS)
    fid = V.fact_ids(SHA, FACTS)[1]
    V.mark(vault, fid, "unverifiable", by="A")
    V.mark(vault, fid, "verified", note="Signed copy seen", by="B")
    V.mark(vault, fid, None, by="B")
    entry = V.load(vault)["marks"][fid]
    assert entry["status"] is None
    assert [h["status"] for h in entry["history"]] == ["unverifiable", "verified"]
    assert V.marks(vault) == {}
    assert "No facts have been marked yet." in (vault / "verification.md").read_text()


def test_marking_an_unknown_fact_or_bad_status_fails(tmp_path, wdg_home):
    vault = _vault(tmp_path, FACTS)
    with pytest.raises(LookupError):
        V.mark(vault, f"fact:1:{SHA[:12]}:0000000000", "verified")
    with pytest.raises(ValueError):
        V.parse_status("probably")
    assert V.parse_status("cant-verify") == "unverifiable" and V.parse_status("clear") is None
    assert V.parse_status("Can't verify") == "unverifiable"


def test_reporter_name_setting_and_neutral_default(tmp_path, wdg_home):
    # The default is never the computer account's name (it travels with the investigation).
    assert V.reporter_name() == "Journalist" == V.default_reporter_name()
    (wdg_home / "config.json").write_text(json.dumps({"reporter_name": "Sam Lee"}))
    assert V.reporter_name() == "Sam Lee"


# ── re-processing ─────────────────────────────────────────────────────────────────────

def test_a_mark_survives_reprocessing_that_keeps_the_words(tmp_path, wdg_home):
    vault = _vault(tmp_path, FACTS)
    fid = V.fact_ids(SHA, FACTS)[0]
    V.mark(vault, fid, "verified", by="A")
    # re-extracted: new order, new entity tags, a new fact, passages added
    _stage(vault, [{"fact": "A new fact.", "page": 1}, FACTS[1],
                   {**FACTS[0], "entities": ["city", "council"], "passage_method": "matched"}])
    s = V.summary(vault)
    assert s["verified"] == 1 and s["orphaned"] == 0 and s["facts"] == 3


def test_a_reworded_fact_orphans_its_mark_and_never_moves_it(tmp_path, wdg_home):
    vault = _vault(tmp_path, FACTS)
    fid = V.fact_ids(SHA, FACTS)[0]
    V.mark(vault, fid, "verified", by="A")
    _stage(vault, [{"fact": "Council paid $1.25 million.", "page": 2}, FACTS[1]])
    s = V.summary(vault)
    assert s["verified"] == 0 and s["orphaned"] == 1 and s["unmarked"] == 2
    (row,) = V.entries(vault)
    assert row["orphaned"] and row["fact"] == "Council paid $1.2 million."   # as it read when marked
    V.render(vault)
    md = (vault / "verification.md").read_text()
    assert "No longer matching a current fact (1)" in md and "**0 of 2 facts verified**" in md
    with pytest.raises(LookupError):
        V.mark(vault, fid, "disputed")      # can't re-mark words that no longer exist
    V.mark(vault, fid, None)                # but the stale mark can be cleared
    assert V.summary(vault)["orphaned"] == 0


def test_a_mark_attaches_only_to_the_words_it_was_made_on(tmp_path, wdg_home):
    """Even an id that matches (a collision, or a future scheme mistake) can't carry a mark to
    different words: the snapshot is re-checked."""
    vault = _vault(tmp_path, FACTS)
    fid = V.fact_ids(SHA, FACTS)[0]
    V.mark(vault, fid, "verified", by="A")
    data = V.load(vault)
    data["marks"][fid]["fact"] = "Something else entirely."
    (vault / ".watchdog/registry/verification.json").write_text(json.dumps(data))
    assert V.summary(vault)["verified"] == 0 and V.summary(vault)["orphaned"] == 1
    assert V.attach(data["marks"][fid], FACTS[0]) is None
    assert V.attach({**data["marks"][fid], "fact": FACTS[0]["fact"]}, FACTS[0]) is not None


def test_entity_merges_do_not_touch_ids(tmp_path, wdg_home):
    before = V.fact_ids(SHA, FACTS)
    merged = [{**f, "entities": ["merged-entity"]} for f in FACTS]
    assert V.fact_ids(SHA, merged) == before


# ── format versions ───────────────────────────────────────────────────────────────────

def test_unknown_fields_are_kept_on_rewrite(tmp_path, wdg_home):
    vault = _vault(tmp_path, FACTS)
    fid = V.fact_ids(SHA, FACTS)[0]
    V.mark(vault, fid, "verified", by="A")
    data = V.load(vault)
    data["future"] = {"x": 1}
    data["marks"][fid]["evidence_url"] = "https://example.org/a"
    (vault / ".watchdog/registry/verification.json").write_text(json.dumps(data))
    V.mark(vault, fid, "disputed", by="B")
    again = V.load(vault)
    assert again["future"] == {"x": 1} and again["marks"][fid]["evidence_url"] == "https://example.org/a"


def test_a_newer_ledger_is_shown_but_never_written(tmp_path, wdg_home):
    vault = _vault(tmp_path, FACTS)
    fid = V.fact_ids(SHA, FACTS)[0]
    path = vault / ".watchdog/registry/verification.json"
    path.write_text(json.dumps({"schema_version": 2, "marks": {fid: {
        "status": "verified", "by": "A", "at": "2027-01-01T00:00:00", "fact": FACTS[0]["fact"], "page": 2}}}))
    before = path.read_text()
    assert V.summary(vault)["verified"] == 1 and V.summary(vault)["read_only"] is True
    with pytest.raises(V.LedgerTooNew):
        V.mark(vault, fid, "disputed")
    assert path.read_text() == before


def test_passages_of_a_newer_format_are_ignored(tmp_path, wdg_home):
    vault = make_vault(tmp_path)
    d = vault / ".watchdog" / "extracted"
    d.mkdir(parents=True)
    (d / f"{SHA}.json").write_text(json.dumps({"document": {"passages_version": 99, "key_facts": [
        {"fact": "x", "page": 1, "passage": "y", "passage_method": "matched"}]}}))
    (fact,) = V.document_facts(vault, SHA, {})
    assert "passage" not in fact and "passage_method" not in fact and fact["fact"] == "x"


# ── CLI ───────────────────────────────────────────────────────────────────────────────

def _args(**kw):
    return argparse.Namespace(**{"id": None, "status": None, "note": None, "by": None, "list": False, **kw})


def test_verify_fact_command(tmp_path, wdg_home, monkeypatch, capsys):
    from watchdog.cmd.verify import cmd_verify_fact
    vault = _vault(tmp_path, FACTS)
    fid = V.fact_ids(SHA, FACTS)[1]
    monkeypatch.chdir(vault)
    cmd_verify_fact(_args(id=fid, status="cant-verify", note="No signed copy", by="Jordan Ellis"))
    out = capsys.readouterr().out
    assert "Marked" in out and "Can't verify" in out and "verification.md" in out
    assert V.marks(vault)[fid]["status"] == "unverifiable"
    cmd_verify_fact(_args(list=True))
    out = capsys.readouterr().out
    assert "0 of 2 facts verified" in out and fid in out and "No signed copy" in out
    with pytest.raises(SystemExit, match="Unknown status"):
        cmd_verify_fact(_args(id=fid, status="maybe"))
    with pytest.raises(SystemExit, match="No current fact"):
        cmd_verify_fact(_args(id=f"fact:1:{SHA[:12]}:0000000000", status="verified"))
    with pytest.raises(SystemExit, match="--status is required"):
        cmd_verify_fact(_args(id=fid))
    cmd_verify_fact(_args(id=fid, status="clear"))
    assert V.marks(vault) == {}


def test_verify_fact_is_a_hidden_maintenance_command():
    from watchdog import cli
    from watchdog.cmd import groups
    assert "verify-fact" in [c for c, _ in groups.MAINTENANCE]
    assert "verify-fact" in cli._subparsers(cli.build_parser())


def test_sessions_are_not_pre_authorized_to_mark_facts():
    from watchdog.cmd.base import _VAULT_PERMISSIONS
    assert not any("verify-fact" in p for p in _VAULT_PERMISSIONS)


# ── RPC and export ────────────────────────────────────────────────────────────────────

def test_rpc_mark_and_facts(rich_vault, wdg_home):
    v = str(rich_vault)
    doc = call("vault.document", vault=v, sha=SHA1)
    fact = doc["facts"][0]
    assert fact["id"].startswith(f"fact:1:{SHA1[:12]}:") and fact["mark"] is None
    assert set(fact) >= {"passage", "passage_page", "passage_method", "passage_score", "mark"}
    saved = call("verify.mark", vault=v, id=fact["id"], status="verified", note="Checked the filing")
    assert saved["status"] == "verified" and saved["note"] == "Checked the filing" and saved["by"]
    again = call("vault.document", vault=v, sha=SHA1)["facts"][0]
    assert again["mark"]["status"] == "verified" and again["mark"]["note"] == "Checked the filing"
    # the note-only document's facts get ids too, and can be marked
    note_fact = call("vault.document", vault=v, sha=SHA2)["facts"][1]
    call("verify.mark", vault=v, id=note_fact["id"], status="disputed")
    r = call("verify.facts", vault=v)
    assert r["summary"]["verified"] == 1 and r["summary"]["disputed"] == 1
    assert r["summary"]["facts"] == len(r["facts"]) and r["orphaned"] == [] and r["reporter"]
    assert {f["id"]: f["mark"]["status"] for f in r["facts"] if f["mark"]} == {
        fact["id"]: "verified", note_fact["id"]: "disputed"}
    assert call("vault.summary", vault=v)["verification"]["verified"] == 1
    call("verify.mark", vault=v, id=fact["id"], status=None)
    assert call("vault.document", vault=v, sha=SHA1)["facts"][0]["mark"] is None
    json.dumps(r)


def test_rpc_mark_errors(rich_vault, wdg_home):
    v = str(rich_vault)
    assert call_error("verify.mark", vault=v, id="nonsense", status="verified")["code"] == "bad_params"
    assert call_error("verify.mark", vault=v, id=f"fact:1:{SHA1[:12]}:0000000000", status="verified")["code"] == "not_found"
    fid = call("vault.document", vault=v, sha=SHA1)["facts"][0]["id"]
    assert call_error("verify.mark", vault=v, id=fid, status="maybe")["code"] == "bad_value"


def test_export_writes_facts_with_passages_and_marks(rich_vault, wdg_home, monkeypatch, capsys):
    from watchdog.cmd.export import cmd_export
    v = str(rich_vault)
    fid = call("vault.document", vault=v, sha=SHA1)["facts"][0]["id"]
    call("verify.mark", vault=v, id=fid, status="verified", note="ok")
    monkeypatch.setattr("watchdog.cmd.export._resolve_vault", lambda p: ("rich", {"name": "Rich"}, rich_vault))
    out = rich_vault / "out"
    cmd_export(argparse.Namespace(project=None, output=str(out), format="csv"))
    rows = list(csv.DictReader((out / "facts.csv").open(encoding="utf-8")))
    marked = [r for r in rows if r["fact_id"] == fid]
    assert marked and marked[0]["status"] == "Verified" and marked[0]["note"] == "ok"
    assert all(r["status"] == "" for r in rows if r["fact_id"] != fid)
    assert "facts.csv" in capsys.readouterr().out


def test_settings_offer_your_name_with_a_neutral_default(wdg_home):
    keys = {k["key"]: k for s in call("settings.schema")["sections"] for k in s["keys"]}
    assert keys["reporter_name"]["default"] == "Journalist" and keys["reporter_name"]["kind"] == "text"
    assert call("settings.set", key="reporter_name", value="  Sam   Lee ")["value"] == "Sam Lee"
    assert V.reporter_name() == "Sam Lee"
    assert call("settings.set", key="reporter_name", value="")["value"] is None
    assert V.reporter_name() == "Journalist"
