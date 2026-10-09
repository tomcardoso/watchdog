"""Fact citations checked by code and rendered as links (D283)."""

import json
from pathlib import Path

from watchdog.pipeline import citations, entity_facts, entity_notes, resolutions, verification

SHA = "c" * 64
FACTS = [{"fact": "Council paid $4,350,000.", "page": 4, "entities": ["x"]},
         {"fact": "The appraisal said $1,420,000.", "page": 2, "entities": ["x"]},
         {"fact": "An undated remark.", "entities": ["x"]}]


def _vault(tmp_path: Path, synthesis: dict | None = None) -> Path:
    vault = tmp_path / "v"
    reg = vault / ".watchdog" / "registry"
    reg.mkdir(parents=True)
    (vault / ".watchdog" / "extracted").mkdir()
    (vault / ".watchdog" / "extracted" / f"{SHA}.json").write_text(json.dumps({
        "document": {"sha256": SHA, "key_facts": FACTS}, "entities": [{"id": "x"}]}))
    entry = {"id": "x", "name": "X Corp", "type": "organization", "appears_in": [SHA],
             "note_path": "entities/organization/x"}
    if synthesis is not None:
        entry["synthesis"] = synthesis
    (reg / "entities.json").write_text(json.dumps({"x": entry}))
    (reg / "documents.json").write_text(json.dumps({SHA: {
        "title": "Payment register", "filename": "reg.pdf", "document_note": "documents/reg",
        "morgue_path": "morgue/reg.pdf"}}))
    return vault


def _ids():
    return verification.fact_ids(SHA, FACTS)


def _facts(vault):
    return {f["id"]: f for f in entity_facts.FactIndex(vault).facts_for("x")}


def test_short_refs_become_links_to_the_document_notes_fact_line(tmp_path):
    vault = _vault(tmp_path)
    paid, appraisal, undated = _ids()
    refs = {"f:aaaa": paid, "f:bbbb": appraisal, "f:cccc": undated}
    text, stats = citations.render_short(
        "It paid twice the appraisal [f:aaaa][f:bbbb]. Remark [f:cccc]. Framing, uncited.",
        refs, _facts(vault).get)
    assert text == (f"It paid twice the appraisal ([[documents/reg#^{citations.block_id(paid)}|p. 4]]; "
                    f"[[documents/reg#^{citations.block_id(appraisal)}|p. 2]]). "
                    f"Remark ([[documents/reg#^{citations.block_id(undated)}|source]]). Framing, uncited.")
    assert stats == {"cited": 3, "linked": 3, "unknown": 0, "missing": 0, "disputed": 0}


def test_an_unknown_or_dangling_ref_is_dropped_and_counted_the_sentence_stays(tmp_path):
    vault = _vault(tmp_path)
    paid = _ids()[0]
    gone = "fact:1:" + SHA[:12] + ":0000000000"
    text, stats = citations.render_short(
        "Paid [f:aaaa, f:dead]. Invented [f:9999]. Gone [f:eeee].",
        {"f:aaaa": paid, "f:eeee": gone}, _facts(vault).get)
    assert text == f"Paid ([[documents/reg#^{citations.block_id(paid)}|p. 4]]). Invented. Gone."
    assert stats["cited"] == 4 and stats["linked"] == 1
    assert stats["unknown"] == 2 and stats["missing"] == 1


def test_a_disputed_fact_is_linked_and_labelled_never_hidden(tmp_path):
    vault = _vault(tmp_path)
    paid = _ids()[0]
    verification.mark(vault, paid, "disputed", by="R")
    text, stats = citations.render_short("Paid [f:aaaa].", {"f:aaaa": paid}, _facts(vault).get)
    assert text == f"Paid ([[documents/reg#^{citations.block_id(paid)}|p. 4, disputed]])."
    assert stats["disputed"] == 1


def test_the_entity_note_summary_renders_citations_and_counts_drops(tmp_path):
    paid, appraisal, _ = _ids()
    vault = _vault(tmp_path, {"summary": "X paid $4,350,000 [f:aaaa]. It says so [f:9999].",
                              "analysis": "The appraisal was lower [f:bbbb].", "by": "model",
                              "fact_refs": {"f:aaaa": paid, "f:bbbb": appraisal}})
    entity_notes.rebuild(vault, index_search=False)
    note = (vault / "entities" / "organization" / "x.md").read_text()
    summary = note.split("## Summary (AI-written)", 1)[1].split("\n## ", 1)[0]
    assert f"X paid $4,350,000 ([[documents/reg#^{citations.block_id(paid)}|p. 4]])." in summary
    assert "It says so." in summary and "[f:" not in summary
    assert f"lower ([[documents/reg#^{citations.block_id(appraisal)}|p. 2]])." in summary
    entry = json.loads((vault / ".watchdog/registry/entities.json").read_text())["x"]
    assert entry["synthesis"]["citations"] == {"cited": 3, "linked": 2, "unknown": 1, "missing": 0,
                                               "disputed": 0}
    # The document note carries the block id each link points at.
    doc = (vault / "documents" / "reg.md").read_text()
    assert f"Council paid $4,350,000. ([[morgue/reg.pdf#page=4|p. 4]]) ^{citations.block_id(paid)}" in doc


def test_a_citation_to_a_fact_the_entity_no_longer_has_is_dropped(tmp_path):
    """After a re-processed document or an undone merge, the cited fact is not among the entity's
    facts: the link is dropped rather than pointed at something else."""
    paid = _ids()[0]
    vault = _vault(tmp_path, {"summary": "X paid [f:aaaa].", "by": "model", "fact_refs": {"f:aaaa": paid}})
    art = vault / ".watchdog" / "extracted" / f"{SHA}.json"
    data = json.loads(art.read_text())
    data["document"]["key_facts"][0]["fact"] = "Council paid $4,530,000."      # re-worded
    art.write_text(json.dumps(data))
    entity_facts.clear_cache()
    entity_notes.rebuild(vault, index_search=False)
    note = (vault / "entities" / "organization" / "x.md").read_text()
    assert "X paid.\n" in note and "#^f-" not in note.split("## Facts")[0]
    entry = json.loads((vault / ".watchdog/registry/entities.json").read_text())["x"]
    assert entry["synthesis"]["citations"]["missing"] == 1


def test_session_written_fact_links_are_checked(tmp_path):
    paid, appraisal, _ = _ids()
    good = f"[[documents/reg#^{citations.block_id(paid)}|p. 4]]"
    bad = "[[documents/reg#^f-0123456789|p. 9]]"
    vault = _vault(tmp_path, {"summary": f"Paid {good}. Also {bad}. Both ({good}; {bad}).",
                              "by": "session", "fact_refs": {}})
    verification.mark(vault, paid, "disputed", by="R")
    entity_notes.rebuild(vault, index_search=False)
    summary = (vault / "entities/organization/x.md").read_text().split("## Summary (AI-written)")[1]
    disputed = f"[[documents/reg#^{citations.block_id(paid)}|p. 4, disputed]]"
    assert f"Paid {disputed}. Also. Both ({disputed})." in summary


def test_contradiction_sides_link_facts_and_label_a_disputed_side(tmp_path):
    paid, appraisal, _ = _ids()
    vault = _vault(tmp_path)
    resolver = citations.Resolver(vault)
    callout = ("> [!contradiction] Price\n"
               f"> - **$4,350,000** — [[documents/reg#^{citations.block_id(paid)}|Payment register]], p. 4\n"
               "> - **$1** — [[documents/reg#^f-0123456789|Payment register]], p. 9")
    verification.mark(vault, paid, "disputed", by="R")
    out = citations.annotate_callouts(callout, citations.Resolver(vault))
    assert out.splitlines()[1].endswith("p. 4 · *disputed*")
    assert "[[documents/reg|Payment register]], p. 9" in out.splitlines()[2]
    # Neither the fact link nor the label changes which contradiction it is.
    plain = callout.replace(f"#^{citations.block_id(paid)}", "").replace("#^f-0123456789", "")
    assert resolutions.contradiction_id(out) == resolutions.contradiction_id(callout) \
        == resolutions.contradiction_id(plain)
    assert resolver.resolve("documents/reg.md", citations.block_id(appraisal))["page"] == 2


def test_check_text_reports_found_not_found_and_disputed(tmp_path):
    paid, appraisal, _ = _ids()
    vault = _vault(tmp_path)
    verification.mark(vault, appraisal, "disputed", by="R")
    page = (f"Paid [[documents/reg#^{citations.block_id(paid)}|p. 4]], appraised "
            f"[[documents/reg#^{citations.block_id(appraisal)}|p. 2]], invented "
            "[[documents/nope#^f-0123456789|p. 1]], unmapped [f:abcd]. An uncited sentence.")
    r = citations.check_text(page, citations.Resolver(vault))
    assert (r["citations"], r["found"], r["not_found"], r["disputed"], r["unresolved_short"]) == (3, 2, 1, 1, 1)
    assert r["items"][0]["fact"]["fact"] == "Council paid $4,350,000."
    assert r["items"][0]["fact"]["title"] == "Payment register"
    assert r["items"][2]["status"] == "not_found"


def test_check_vault_scans_session_pages_without_changing_them(tmp_path):
    paid = _ids()[0]
    vault = _vault(tmp_path)
    (vault / "queries").mkdir()
    q = vault / "queries" / "who-paid.md"
    body = f"# Who paid\n\nThe City [[documents/reg#^{citations.block_id(paid)}|p. 4]]; [[documents/reg#^f-0000000000|p. 1]].\n"
    q.write_text(body)
    (vault / "wiki").mkdir()
    (vault / "wiki" / "plain.md").write_text("No citations here.\n")
    r = citations.check_vault(vault)
    assert r["checked"] == 2 and r["citations"] == 2 and r["not_found"] == 1
    assert [p["path"] for p in r["pages"]] == ["queries/who-paid.md"]
    assert q.read_text() == body


def test_a_contradiction_side_naming_its_fact_links_to_that_fact(tmp_path):
    """D283: reconcile may name each side's fact by its short id; the writer resolves it within the
    named document only, links the side to the fact's line and takes the fact's page. A ref that
    names no single fact there leaves the plain document citation."""
    from watchdog.pipeline import contradiction
    paid, appraisal, _ = _ids()
    vault = _vault(tmp_path)
    hexpart = paid.split(":")[3]
    out = contradiction.run(vault, "x", "Price", "$4,350,000", "reg", 9, "$1,420,000", "reg", 2,
                            a_fact=f"[f:{hexpart[:5]}]", b_fact="f:ffff")
    callout = json.loads((vault / ".watchdog/registry/entities.json").read_text())["x"]["contradictions"][0]
    assert f"[[documents/reg#^{citations.block_id(paid)}|Payment register]], p. 4" in callout
    assert "[[documents/reg|Payment register]], p. 2" in callout
    assert out["added"]
    again = contradiction.run(vault, "x", "Price", "$4,350,000", "reg", 4, "$1,420,000", "reg", 2)
    assert not again["added"]           # the same contradiction, with or without the fact link


def test_search_json_lists_each_hits_facts_with_a_citation_to_paste(tmp_path):
    """D283: `watchdog search --json` gives every passage and exact match in a document's text the
    facts recorded on that page, with their id and the link a session pastes to cite one."""
    from watchdog.cmd.vault import _build_search_json
    paid, appraisal, _ = _ids()
    vault = _vault(tmp_path)
    verification.mark(vault, paid, "disputed", by="R")
    out = _build_search_json(
        "paid", [{"filename": "reg.pdf", "page": 4, "text": "paid", "score": 0.5}], [],
        [{"kind": "corpus", "key": SHA, "title": "Payment register", "path": "morgue/reg.md",
          "page": 2, "text": "appraisal"},
         {"kind": "entity", "key": "x", "title": "X", "path": "entities/organization/x", "page": None,
          "text": "X"}], vault=vault)
    [fact] = out["passages"][0]["facts"]
    assert fact["id"] == paid and fact["mark"] == "disputed"
    assert fact["cite"] == f"[[documents/reg#^{citations.block_id(paid)}|p. 4, disputed]]"
    assert [f["id"] for f in out["exact"][0]["facts"]] == [appraisal]
    assert "facts" not in out["exact"][1]
    page = f"The City paid {fact['cite']}."
    assert citations.check_text(page, citations.Resolver(vault))["found"] == 1


def test_check_citations_command_reports_and_stays_in_its_vault(tmp_path, monkeypatch, capsys):
    import argparse

    import pytest

    from watchdog.cmd.citations import cmd_check_citations
    paid = _ids()[0]
    vault = _vault(tmp_path)
    (vault / "queries").mkdir()
    (vault / "queries" / "q.md").write_text(
        f"Paid [[documents/reg#^{citations.block_id(paid)}|p. 4]]; [[documents/reg#^f-0000000000|p. 1]].")
    monkeypatch.chdir(vault)
    monkeypatch.setattr("watchdog.cmd.citations.is_vault", lambda p: True)
    cmd_check_citations(argparse.Namespace(files=["queries/q.md"], json=True))
    report = json.loads(capsys.readouterr().out)
    assert (report["found"], report["not_found"]) == (1, 1)
    cmd_check_citations(argparse.Namespace(files=[], json=False))
    assert "source not found" in capsys.readouterr().out
    outside = tmp_path / "elsewhere.md"
    outside.write_text("x")
    with pytest.raises(SystemExit):
        cmd_check_citations(argparse.Namespace(files=[str(outside)], json=True))
