"""Setting a document's details from the app (#832, D381): the `.yml` sidecar stays the one store,
for a file waiting in `incoming/` and, once read, in every copy Watchdog keeps of it."""

import json
import shutil

import pytest
import yaml

from watchdog import ops
from watchdog.ops import CollectingReporter, OpError
from watchdog.pipeline import details, entity_facts, history, sidecar

from tests.demo_support import copy_demo
from tests.gui_support import call, call_error


@pytest.fixture(scope="module")
def demo(demo_build, tmp_path_factory):
    root, _ = demo_build
    vault, _home = copy_demo(root, tmp_path_factory.mktemp("demo-details") / "copy")
    return vault


@pytest.fixture
def vault(demo, tmp_path, monkeypatch):
    copy = tmp_path / "v"
    shutil.copytree(demo, copy)
    monkeypatch.setattr("watchdog.config.HOME", tmp_path / "home")
    entity_facts.clear_cache()
    return copy


def _set(vault, values, **target):
    return ops.run("set-details", {"values": values, **target}, CollectingReporter(), vault)


def _committed(vault):
    """A committed document with a stored extraction and a sidecar beside its original."""
    docs = json.loads((vault / ".watchdog" / "registry" / "documents.json").read_text())
    for sha, rec in sorted(docs.items()):
        if (vault / f"{rec['morgue_path']}.yml").exists() and \
                (vault / ".watchdog" / "extracted" / f"{sha}.json").exists():
            return sha, rec
    raise AssertionError("the demo has no committed document with a sidecar")


def _waiting(vault):
    files = sorted(p for p in (vault / "incoming").iterdir() if p.is_file() and p.suffix != ".yml")
    return files[0].relative_to(vault).as_posix()


def _queue(vault, sha, sidecar_text=None, *, kept=False):
    d = vault / ".watchdog" / "queue" / ("_kept" if kept else "")
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sha}.json").write_text(json.dumps(
        {"filename": "memo.pdf", "pages": [{"page": 1, "text": "x"}], "sidecar": sidecar_text}))
    return d / f"{sha}.json"


# ── the sidecar text ──────────────────────────────────────────────────────────────────────

def test_edit_sets_and_removes_fields_and_keeps_the_rest():
    raw = "source: City clerk\nretrieved_by: fetch\ncustom: kept\n"
    out = yaml.safe_load(sidecar.edit(raw, {"source": "", "notes": "Call back", "obtained": "2024-03-11"}))
    assert out == {"retrieved_by": "fetch", "custom": "kept", "notes": "Call back", "obtained": "2024-03-11"}
    assert sidecar.edit("source: a\n", {"source": ""}) is None
    assert sidecar.edit(None, {"notes": ""}) is None
    for bad in ("- a list\n", "source: [unclosed\n"):
        with pytest.raises(ValueError):
            sidecar.edit(bad, {"notes": "x"})


def test_clean_accepts_only_the_editable_fields_and_known_skills():
    assert details.clean({"source": "  A  ", "skill": "government-contracts.md"}) == \
        {"source": "A", "skill": "government-contracts"}
    assert details.clean({"skill": ""}) == {"skill": ""}
    for bad in ({"retrieved_by": "me"}, {"skill": "../credentials"}, {"skill": "no-such-skill"},
                {"notes": "x" * (sidecar.MAX_VALUE_LEN + 1)}, {"source": 3}):
        with pytest.raises(details.DetailsError):
            details.clean(bad)


def test_the_operation_takes_a_map_of_text():
    with pytest.raises(OpError):
        ops.validate("set-details", {"values": {"source": 3}, "sha": "a" * 64})
    assert ops.validate("set-details", {"values": {"source": ""}, "sha": "a" * 64})["values"] == {"source": ""}


# ── a file waiting to be added ────────────────────────────────────────────────────────────

def test_a_waiting_file_gets_its_sidecar_written_beside_it(vault):
    path = _waiting(vault)
    side = vault / f"{path}.yml"
    side.unlink(missing_ok=True)
    out = _set(vault, {"source": "Clerk's office", "obtained": "2024-03-11", "notes": "", "skill": ""}, path=path)
    assert out["changed"] == ["source", "obtained"]
    assert yaml.safe_load(side.read_text()) == {"source": "Clerk's office", "obtained": "2024-03-11"}
    assert call("vault.details", vault=str(vault), path=path)["values"]["source"] == "Clerk's office"
    # Pre-processing reads it as it reads a hand-made one.
    clean, dropped = sidecar.filter_and_render(side.read_text())
    assert sidecar.provenance(clean) == {"source": "Clerk's office", "obtained": "2024-03-11"} and not dropped
    _set(vault, {"source": "", "obtained": ""}, path=path)
    assert not side.exists()


def test_a_waiting_file_keeps_fields_watchdog_wrote_and_refuses_an_unreadable_sidecar(vault):
    path = _waiting(vault)
    side = vault / f"{path}.yml"
    side.write_text("source: https://example.org/a\nretrieved_by: fetch\n")
    _set(vault, {"notes": "Check the date"}, path=path)
    assert yaml.safe_load(side.read_text()) == {"source": "https://example.org/a", "retrieved_by": "fetch",
                                                "notes": "Check the date"}
    side.write_text("source: [unclosed\n")
    with pytest.raises(SystemExit, match="could not be read"):
        _set(vault, {"notes": "x"}, path=path)
    assert side.read_text() == "source: [unclosed\n"


def test_only_a_file_waiting_in_incoming_can_be_changed(vault):
    for path in ("context.md", "../outside.pdf", "incoming/no-such-file.pdf"):
        with pytest.raises(SystemExit):
            _set(vault, {"notes": "x"}, path=path)
    with pytest.raises(SystemExit, match="either"):
        _set(vault, {"notes": "x"})


# ── a document Watchdog has read ──────────────────────────────────────────────────────────

def test_a_changed_source_reaches_every_copy_and_the_note(vault):
    sha, rec = _committed(vault)
    before = history.versions(vault)["versions"][0]["version"] if history.versions(vault)["versions"] else 0
    out = _set(vault, {"source": "Provided by a source", "obtained": "2025-01-02", "notes": "Second copy"}, sha=sha)
    assert out["stage"] == "added" and set(out["changed"]) == {"source", "obtained", "notes"}
    art = json.loads((vault / ".watchdog" / "extracted" / f"{sha}.json").read_text())["document"]
    assert art["source"] == "Provided by a source" and art["obtained"] == "2025-01-02"
    assert yaml.safe_load(art["sidecar"])["notes"] == "Second copy"
    assert yaml.safe_load((vault / f"{rec['morgue_path']}.yml").read_text())["source"] == "Provided by a source"
    note = (vault / f"{rec['document_note']}.md").read_text()
    assert "source: Provided by a source" in note and "2025-01-02" in note
    row = call("vault.document", vault=str(vault), sha=sha)
    assert row["source"] == "Provided by a source" and row["sidecar"]["notes"] == "Second copy"
    # One version, labelled for the reporter.
    latest = history.versions(vault)["versions"][0]
    assert latest["version"] > before and latest["label"] == f"Details of {rec['filename']} changed (by you)"


def test_removing_the_source_clears_it_from_the_note(vault):
    sha, rec = _committed(vault)
    _set(vault, {"source": "", "obtained": ""}, sha=sha)
    art = json.loads((vault / ".watchdog" / "extracted" / f"{sha}.json").read_text())["document"]
    assert "source" not in art and "obtained" not in art
    assert call("vault.document", vault=str(vault), sha=sha)["source"] is None


def test_nothing_is_written_when_nothing_changed(vault):
    sha, rec = _committed(vault)
    current = call("vault.details", vault=str(vault), sha=sha)["values"]
    note = vault / f"{rec['document_note']}.md"
    mtime = note.stat().st_mtime_ns
    assert _set(vault, current, sha=sha)["changed"] == []
    assert note.stat().st_mtime_ns == mtime


def test_a_new_skill_on_an_added_document_needs_its_text_kept(vault):
    sha, rec = _committed(vault)
    info = call("vault.details", vault=str(vault), sha=sha)
    assert info["skill_costs"] is True and info["reextract"] is False
    with pytest.raises(SystemExit, match="no longer kept"):
        _set(vault, {"skill": "court-documents", "notes": "kept back too"}, sha=sha)
    assert "kept back too" not in (vault / f"{rec['morgue_path']}.yml").read_text()
    # With its descriptor kept for a retry (D367), the pin is written there for the retry to read.
    kept = _queue(vault, sha, json.loads(
        (vault / ".watchdog" / "extracted" / f"{sha}.json").read_text())["document"]["sidecar"], kept=True)
    out = _set(vault, {"skill": "court-documents"}, sha=sha)
    assert out["skill_changed"] is True and out["reextract"] is True
    assert sidecar.skill_pin(json.loads(kept.read_text())["sidecar"]) == "court-documents"


def test_a_queued_document_takes_any_change_for_free(vault):
    sha = "b" * 64
    qf = _queue(vault, sha, "source: Clerk\nretrieved_by: fetch\n")
    info = call("vault.details", vault=str(vault), sha=sha)
    assert info["stage"] == "queued" and info["skill_costs"] is False
    assert info["values"] == {"source": "Clerk", "obtained": "", "notes": "", "skill": ""}
    out = _set(vault, {"skill": "court-documents", "source": ""}, sha=sha)
    assert out["skill_changed"] is True
    assert yaml.safe_load(json.loads(qf.read_text())["sidecar"]) == {"retrieved_by": "fetch", "skill": "court-documents"}


def test_refused_while_a_run_is_processing(vault, monkeypatch):
    sha, _ = _committed(vault)
    monkeypatch.setattr(history, "run_in_progress", lambda v: True)
    with pytest.raises(SystemExit, match="being processed"):
        _set(vault, {"notes": "x"}, sha=sha)


def test_an_unknown_document_is_refused(vault):
    for sha in ("c" * 64, "../registry/documents"):
        with pytest.raises(SystemExit, match="could not be found"):
            _set(vault, {"notes": "x"}, sha=sha)
    assert call_error("vault.details", vault=str(vault), sha="c" * 64)["code"] == "not_found"
