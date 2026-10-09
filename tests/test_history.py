"""Version history (D286): the store, the index, each operation's version, restore and clear."""

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from watchdog.pipeline import entity_facts, history

from tests.gui_support import call

SRC = Path(__file__).resolve().parent.parent / "src"


def _vault(tmp_path: Path) -> Path:
    v = tmp_path / "v"
    (v / ".watchdog" / "registry").mkdir(parents=True)
    (v / "entities" / "person").mkdir(parents=True)
    (v / "documents").mkdir()
    (v / "context.md").write_text("# Context\n\nWho paid for Pier 9?\n")
    (v / "entities" / "person" / "jane.md").write_text("# Jane\n\n## Notes\n\nFirst note.\n")
    (v / ".watchdog" / "registry" / "entities.json").write_text("{}\n")
    return v


def _log(v: Path) -> list[dict]:
    return [json.loads(line) for line in (history.history_dir(v) / "log.jsonl").read_text().splitlines()]


def _kinds(v: Path) -> list[str]:
    return [r["cause"]["kind"] for r in _log(v)]


# ── the store ─────────────────────────────────────────────────────────────────────────

def test_round_trip_and_schema_version(tmp_path):
    v = _vault(tmp_path)
    n = history.snapshot(v, {"kind": "edit", "file": "context.md"})
    assert n == 1
    rec = _log(v)[0]
    assert rec["schema_version"] == history.SCHEMA_VERSION and rec["version"] == 1
    assert rec["cause"]["first"] is True
    paths = {p for p, _ in rec["changes"]}
    assert paths == {"context.md", "entities/person/jane.md", ".watchdog/registry/entities.json"}
    assert history.read_version(v, "context.md", 1)["text"] == "# Context\n\nWho paid for Pier 9?\n"
    assert history.snapshot(v, {"kind": "edit"}) is None             # nothing changed, no version


def test_unchanged_files_cost_nothing_and_content_is_stored_once(tmp_path):
    v = _vault(tmp_path)
    (v / "wiki").mkdir()
    (v / "wiki" / "a.md").write_text("same\n")
    (v / "wiki" / "b.md").write_text("same\n")
    history.snapshot(v, {"kind": "found"})
    objects = lambda: sorted(p for p in (history.history_dir(v) / "objects").rglob("*") if p.is_file())  # noqa: E731
    before = objects()
    assert len(before) == 4                          # a.md and b.md share one blob
    (v / "wiki" / "a.md").write_text("changed\n")
    history.snapshot(v, {"kind": "found"})
    (v / "wiki" / "a.md").write_text("same\n")       # back to an earlier content: blob reused
    history.snapshot(v, {"kind": "found"})
    assert len(objects()) == 5
    assert [r["changes"] for r in _log(v)[1:]] == [[["wiki/a.md", _log(v)[1]["changes"][0][1]]],
                                                   [["wiki/a.md", _log(v)[0]["changes"][-1][1]]]]


def test_deletions_are_versions(tmp_path):
    v = _vault(tmp_path)
    history.snapshot(v, {"kind": "found"})
    (v / "context.md").unlink()
    history.snapshot(v, {"kind": "edit"})
    assert _log(v)[-1]["changes"] == [["context.md", None]]
    fh = history.file_history(v, "context.md")
    assert fh["versions"][0]["deleted"] and not fh["exists"]


def test_excluded_paths_are_never_tracked(tmp_path):
    v = _vault(tmp_path)
    for rel in ("morgue/acme/organization/f.md", "incoming/new.md", "context/brief.md",
                ".watchdog/extracted/x.md", ".embeddings/n.md", ".fulltext/a.md", ".claude/c.md",
                ".obsidian/w.md", "entities/person/.hidden.md", "notes.txt",
                ".watchdog/registry/registry.json", ".watchdog/registry/processing.log",
                ".watchdog/history/objects/zz.md", ".watchdog/tmp/notes_x.md"):
        (v / rel).parent.mkdir(parents=True, exist_ok=True)
        (v / rel).write_text("x\n")
    history.snapshot(v, {"kind": "found"})
    tracked = {p for p, _ in _log(v)[0]["changes"]}
    assert tracked == {"context.md", "entities/person/jane.md", ".watchdog/registry/entities.json"}
    for name in history.REGISTRY_FILES:
        assert history.tracked(f".watchdog/registry/{name}")
    assert not history.tracked("../outside.md") and not history.tracked("morgue/x.md")


def test_index_is_rebuilt_from_the_log_and_a_torn_line_is_cut(tmp_path):
    v = _vault(tmp_path)
    history.snapshot(v, {"kind": "found"})
    (v / "context.md").write_text("two\n")
    history.snapshot(v, {"kind": "edit"})
    hdir = history.history_dir(v)
    want = history.file_history(v, "context.md")["versions"]
    (hdir / "index.json").unlink()
    assert history.file_history(v, "context.md")["versions"] == want
    with open(hdir / "log.jsonl", "ab") as f:      # a crash mid-append
        f.write(b'{"schema_version":1,"version":3,"at"')
    (v / "context.md").write_text("three\n")
    assert history.snapshot(v, {"kind": "edit"}) == 3
    assert [r["version"] for r in _log(v)] == [1, 2, 3]


def test_a_rewrite_within_the_timestamp_resolution_is_still_seen(tmp_path):
    v = _vault(tmp_path)
    history.snapshot(v, {"kind": "found"})
    st = (v / "context.md").stat()
    (v / "context.md").write_text("# Context\n\nWho paid for Pier 8?\n")      # same size
    os.utime(v / "context.md", ns=(st.st_atime_ns, st.st_mtime_ns))          # same mtime
    assert history.snapshot(v, {"kind": "edit"}) == 2


def test_a_newer_store_is_read_only(tmp_path, capsys):
    v = _vault(tmp_path)
    history.snapshot(v, {"kind": "found"})
    hdir = history.history_dir(v)
    with open(hdir / "log.jsonl", "a") as f:
        f.write(json.dumps({"schema_version": 99, "version": 2, "at": "x", "cause": {},
                            "changes": []}) + "\n")
    (hdir / "index.json").unlink()
    assert history.too_new(v)
    (v / "context.md").write_text("changed\n")
    with pytest.raises(history.HistoryTooNew):
        history.snapshot(v, {"kind": "edit"})
    assert history.safe_snapshot(v, {"kind": "edit"}) is None       # never fails the operation
    assert "version history not recorded" in capsys.readouterr().err
    with pytest.raises(history.HistoryTooNew):
        history.restore(v, "context.md", 1)
    with pytest.raises(history.HistoryTooNew):
        history.clear(v)


def test_concurrent_snapshots_get_distinct_versions(tmp_path):
    v = _vault(tmp_path)
    (v / "wiki").mkdir()
    history.snapshot(v, {"kind": "found"})
    errors = []

    def writer(i):
        try:
            for k in range(5):
                (v / "wiki" / f"p{i}.md").write_text(f"{i}-{k}\n")
                history.snapshot(v, {"kind": "edit"}, [f"wiki/p{i}.md"])
        except Exception as e:      # pragma: no cover — surfaced below
            errors.append(e)
    threads = [threading.Thread(target=writer, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    nums = [r["version"] for r in _log(v)]
    assert nums == list(range(1, len(nums) + 1)) and len(nums) == 31
    for i in range(6):
        assert history.read_version(v, f"wiki/p{i}.md",
                                    history.file_history(v, f"wiki/p{i}.md")["versions"][0]["version"]
                                    )["text"] == f"{i}-4\n"


def test_snapshots_from_two_processes_are_serialized(tmp_path):
    v = _vault(tmp_path)
    (v / "wiki").mkdir()
    code = ("import sys; from pathlib import Path; from watchdog.pipeline import history\n"
            "v = Path(sys.argv[1]); i = sys.argv[2]\n"
            "for k in range(10):\n"
            "    (v / 'wiki' / f'p{i}.md').write_text(f'{i}-{k}')\n"
            "    history.snapshot(v, {'kind': 'edit'}, [f'wiki/p{i}.md'])\n")
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    procs = [subprocess.Popen([sys.executable, "-c", code, str(v), str(i)], env=env) for i in range(3)]
    assert all(p.wait(timeout=60) == 0 for p in procs)
    nums = [r["version"] for r in _log(v)]
    assert nums == list(range(1, 31))


# ── recording ─────────────────────────────────────────────────────────────────────────

def test_recording_keeps_outside_changes_apart_and_nests(tmp_path):
    v = _vault(tmp_path)
    history.snapshot(v, {"kind": "found"})
    (v / "context.md").write_text("edited in Obsidian\n")
    with history.recording(v, {"kind": "rebuild"}):
        with history.recording(v, {"kind": "merge"}):     # inside: the outer version holds it
            (v / "entities" / "person" / "jane.md").write_text("# Jane\n\nrebuilt\n")
    assert _kinds(v) == ["found", "found", "rebuild"]
    assert _log(v)[1]["changes"][0][0] == "context.md"
    assert _log(v)[2]["changes"][0][0] == "entities/person/jane.md"


def test_an_operation_that_fails_is_still_recorded_and_labelled(tmp_path):
    v = _vault(tmp_path)
    with pytest.raises(RuntimeError):
        with history.recording(v, {"kind": "run", "documents": ["a.pdf"], "document_count": 1}):
            (v / "context.md").write_text("half\n")
            raise RuntimeError("boom")
    last = _log(v)[-1]
    assert last["cause"]["incomplete"] is True
    assert history.label(last["cause"]).endswith("(stopped before it finished)")


def test_labels_are_plain_language():
    L = history.label
    assert L({"kind": "run", "documents": ["a.pdf", "b.pdf"], "document_count": 2}) == "Documents added: a.pdf, b.pdf"
    assert L({"kind": "run", "documents": ["a", "b", "c", "d"], "document_count": 5}) == "Documents added: a, b, c and 2 more"
    assert L({"kind": "merge", "keep": "City of Port Calder", "merged": "The City", "by": "reporter"}) == \
        "Merged The City into City of Port Calder (by you)"
    assert L({"kind": "restore", "from_at": "2026-10-01T09:30:00-04:00", "part": "page"}) == \
        "Restored by you, from the version of 1 October 2026, 09:30"
    assert L({"kind": "found", "first": True}) == "Version when history began"
    assert L({"kind": "mark", "status": None}) == "Fact mark cleared"


def test_reading_a_page_records_a_session_edit_unless_a_run_holds_the_vault(tmp_path):
    from watchdog.vault_paths import processing_lock
    v = _vault(tmp_path)
    (v / "queries").mkdir()
    (v / "queries" / "q.md").write_text("one\n")
    history.snapshot(v, {"kind": "found"})
    (v / "queries" / "q.md").write_text("two\n")
    processing_lock(v).write_text("pid: cli\n")
    assert len(history.file_history(v, "queries/q.md")["versions"]) == 1
    processing_lock(v).unlink()
    fh = history.file_history(v, "queries/q.md")
    assert len(fh["versions"]) == 2 and fh["versions"][0]["current"]
    assert fh["restore"] == "page"


# ── diff ──────────────────────────────────────────────────────────────────────────────

def test_diff_is_line_level_with_words_inside_changed_lines():
    d = history.diff_text("a\nThe City paid $4,350,000.\nc\n", "a\nThe City paid $4,530,000.\nc\nd\n")
    assert d["added"] == 2 and d["removed"] == 1 and not d["truncated"]
    lines = d["hunks"][0]["lines"]
    minus = next(x for x in lines if x["op"] == "-")
    assert {"t": "del", "s": "4"} in minus["segments"] or any(s["t"] == "del" for s in minus["segments"])
    assert any(s["t"] == "eq" and "The City paid" in s["s"] for s in minus["segments"])
    assert lines[-1] == {"op": "+", "old": None, "new": 4, "segments": [{"t": "ins", "s": "d"}]}
    assert history.diff_text("x\n", "x\n")["identical"]


def test_diff_against_the_previous_version_and_the_current_file(tmp_path):
    v = _vault(tmp_path)
    history.snapshot(v, {"kind": "found"})
    (v / "context.md").write_text("# Context\n\nWho paid for Pier 10?\n")
    n = history.snapshot(v, {"kind": "edit"})
    d = history.diff(v, "context.md", n)
    assert d["before"]["version"] == 1 and d["after"]["version"] == n and d["added"] == 1
    first = history.diff(v, "context.md", 1)
    assert first["before"] == {"version": None, "exists": False}
    assert history.diff(v, "context.md", n, against="current")["identical"]


# ── restore and clear ─────────────────────────────────────────────────────────────────

def test_restore_a_page_writes_it_back_and_records_restored_by_you(tmp_path):
    v = _vault(tmp_path)
    history.snapshot(v, {"kind": "found"})
    (v / "context.md").write_text("rewritten\n")
    history.snapshot(v, {"kind": "edit"})
    out = history.restore(v, "context.md", 1)
    assert (v / "context.md").read_text() == "# Context\n\nWho paid for Pier 9?\n"
    assert out["part"] == "page" and out["version"] == 3
    assert _log(v)[-1]["cause"]["kind"] == "restore" and _log(v)[-1]["cause"]["from_version"] == 1
    assert history.label(_log(v)[-1]["cause"]).startswith("Restored by you")


def test_restore_of_a_generated_note_puts_back_only_the_reporters_notes(tmp_path):
    v = _vault(tmp_path)
    note = v / "entities" / "person" / "jane.md"
    history.snapshot(v, {"kind": "found"})
    note.write_text("# Jane\n\nRebuilt facts.\n\n## Notes\n\nA later note.\n")
    history.snapshot(v, {"kind": "run"})
    history.restore(v, "entities/person/jane.md", 1)
    assert note.read_text() == "# Jane\n\nRebuilt facts.\n\n## Notes\n\nFirst note.\n"
    assert _log(v)[-1]["cause"]["part"] == "notes"


def test_generated_files_and_deletions_cannot_be_restored(tmp_path):
    v = _vault(tmp_path)
    (v / "timeline.md").write_text("# Timeline\n")
    history.snapshot(v, {"kind": "found"})
    with pytest.raises(history.CannotRestore):
        history.restore(v, "timeline.md", 1)
    with pytest.raises(history.CannotRestore):
        history.restore(v, ".watchdog/registry/entities.json", 1)
    (v / "context.md").unlink()
    history.snapshot(v, {"kind": "edit"})
    with pytest.raises(history.CannotRestore):
        history.restore(v, "context.md", 2)
    with pytest.raises(LookupError):
        history.restore(v, "context.md", 9)


def test_clear_removes_every_past_version_and_keeps_the_files(tmp_path):
    v = _vault(tmp_path)
    history.snapshot(v, {"kind": "found"})
    (v / "context.md").write_text("secret source name\n")
    history.snapshot(v, {"kind": "edit"})
    (v / "context.md").write_text("cleaned\n")
    history.snapshot(v, {"kind": "edit"})
    out = history.clear(v)
    assert out["removed_versions"] == 3 and out["versions"] == 1
    assert (v / "context.md").read_text() == "cleaned\n"
    assert _kinds(v) == ["cleared"]
    blobs = [p.read_bytes() for p in (history.history_dir(v) / "objects").rglob("*") if p.is_file()]
    import zlib
    assert not any(b"secret source name" in zlib.decompress(b) for b in blobs)


# ── every write point records a version ───────────────────────────────────────────────

@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    root = tmp_path_factory.mktemp("demo-history")
    vault = root / "vault"
    env = {**os.environ, "PYTHONPATH": str(SRC), "NO_COLOR": "1"}
    proc = subprocess.run([sys.executable, "-m", "watchdog.gui.demo", str(vault), "--home", str(root / "home")],
                          env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    return vault


@pytest.fixture
def vault(demo, tmp_path):
    copy = tmp_path / "v"
    shutil.copytree(demo, copy)
    entity_facts.clear_cache()
    return copy


def test_each_demo_run_is_one_version_with_its_documents(demo):
    runs = [r for r in _log(demo) if r["cause"]["kind"] == "run"]
    assert len(runs) == 2                             # the demo's two batches
    assert sum(r["cause"]["document_count"] for r in runs) == 14
    # The merges inside a run belong to its version (I7): none is a version of its own.
    assert set(_kinds(demo)) <= {"found", "run", "mark"}
    assert all(r["cause"]["documents"] and r["cause"]["documents"][0].endswith((".pdf", ".png", ".docx", ".html", ".eml", ".xlsx", ".mp3", ".jpg", ".txt", ".csv", ".msg", ".pptx"))
               for r in runs)
    changed = {p for r in runs for p, _ in r["changes"]}
    assert "timeline.md" in changed and ".watchdog/registry/entities.json" in changed
    assert any(p.startswith("briefings/") for p in changed)
    assert not any(p.startswith(("morgue/", ".watchdog/extracted", ".embeddings", ".fulltext")) for p in changed)


def test_merge_undo_rebuild_and_mark_each_record_a_version(vault):
    from watchdog.pipeline import entity_notes, merge_entities, merge_log, merge_undo, verification
    merge_entities.run(vault, "leonard-pike", "dana-whitcombe")
    last = _log(vault)[-1]
    assert last["cause"] == {"kind": "merge", "keep": "Leonard Pike", "merged": "Dana Whitcombe",
                             "by": "reporter"}
    assert ".watchdog/registry/merges.json" in {p for p, _ in last["changes"]}
    m = next(x for x in merge_log.load(vault)["merges"] if x["merged"]["id"] == "dana-whitcombe"
             and x["decided_by"] == "reporter")
    merge_undo.undo(vault, m["id"])
    assert _log(vault)[-1]["cause"]["kind"] == "undo_merge"
    assert history.label(_log(vault)[-1]["cause"]) == "Undid the merge of Dana Whitcombe into Leonard Pike"
    for note in (vault / "entities").rglob("*.md"):
        note.unlink()
    entity_notes.rebuild(vault, index_search=False)
    assert _log(vault)[-1]["cause"]["kind"] == "rebuild"
    fid = next(iter(verification.all_facts(vault)))
    verification.mark(vault, fid, "verified")
    rec = _log(vault)[-1]
    assert rec["cause"]["kind"] == "mark" and rec["cause"]["status"] == "verified"
    assert "verification.md" in {p for p, _ in rec["changes"]}


def test_app_edits_record_versions_and_history_api(vault):
    v = str(vault)
    call("vault.writeFile", vault=v, path="context.md", text="# Context\n\nNew question.\n")
    assert _log(vault)[-1]["cause"] == {"kind": "edit", "file": "context.md"}
    note = "entities/person/leonard-pike"
    call("vault.saveNotes", vault=v, path=note, text="Called his office on Monday.")
    assert _log(vault)[-1]["cause"] == {"kind": "notes"}
    rid = call("review.items", vault=v)["items"][0]["rid"]
    call("review.resolve", vault=v, rids=[rid])
    assert _log(vault)[-1]["cause"]["kind"] == "review"

    fh = call("history.file", vault=v, path=note)
    assert fh["path"] == note + ".md" and fh["restore"] == "notes"
    assert fh["versions"][0]["label"] == "Your notes edited in the app" and fh["versions"][0]["current"]
    d = call("history.diff", vault=v, path=note, version=fh["versions"][0]["version"])
    assert d["added"] >= 1
    older = fh["versions"][1]["version"]
    out = call("history.restore", vault=v, path=note, version=older)
    assert out["part"] == "notes"
    assert "Called his office" not in (vault / (note + ".md")).read_text()
    assert call("history.file", vault=v, path=note)["versions"][0]["label"].startswith("Restored by you")

    vs = call("history.versions", vault=v, limit=3)
    assert len(vs["versions"]) == 3 and vs["more"] and vs["versions"][0]["changes"]
    st = call("history.stats", vault=v)
    assert st["versions"] == vs["total"] and st["bytes"] > 0
    from watchdog.gui import server
    resp = server.handle({"id": 1, "method": "history.file", "params": {"vault": v, "path": "morgue/x.pdf"}})
    assert resp["error"]["code"] == "not_tracked"
    resp = server.handle({"id": 1, "method": "history.restore",
                          "params": {"vault": v, "path": "timeline.md", "version": 1}})
    assert resp["error"]["code"] in ("cannot_restore", "not_found")
    cleared = call("history.clear", vault=v)
    assert cleared["versions"] == 1 and cleared["removed_versions"] > 1
