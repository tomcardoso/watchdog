"""The copy of an investigation for troubleshooting (`watchdog.ops.troubleshoot`, D323): what the
zip holds, the originals only when asked, and nothing from outside the investigation folder."""

import json
import os
import zipfile

import pytest

from watchdog.ops import CollectingReporter, troubleshoot
from watchdog.ops.troubleshoot import troubleshooting_copy

pytest_plugins = ["tests.gui_support"]   # the wdg_home and rich_vault fixtures

SECRET = "sk-ant-never-in-the-copy"


@pytest.fixture
def vault(wdg_home, rich_vault):
    """The rich vault plus a file in every folder the copy must leave out or include on request,
    and Watchdog's own folder holding credentials, a chat, a log and a secret setting."""
    v = rich_vault
    w = v / ".watchdog"

    def put(rel, text="x"):
        p = v / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    put(".watchdog/extracted/" + "1" * 64 + ".json", "{}")
    put(".watchdog/timeline/canonical/2024-01.ndjson", "{}\n")
    put(".watchdog/briefings/2026-03-02-09-00.json", "{}")
    put(".watchdog/registry/usage/usage-2026-03-02.json", "{}")
    put(".watchdog/registry/.processing-lock", "pid")
    put("morgue/acme-corp/annual-report/report-one.pdf", "%PDF original")
    put("morgue/acme-corp/annual-report/report-one.md", "full text")
    put("incoming/waiting.pdf", "%PDF waiting")
    put(".watchdog/queue/" + "4" * 64 + ".json", "{}")
    put(".watchdog/staging/" + "4" * 64 + "/waiting.pdf", "%PDF staged")
    put(".watchdog/text-positions/" + "1" * 64 + ".json", "{}")
    put(".watchdog/history/objects/ab/cdef", "blob")
    put(".watchdog/backups/2026-03-02-merge/entities.json", "{}")
    put(".watchdog/tmp/result_x.json", "{}")
    put("queries/a-question.md", "an answer")
    put("wiki/a-thread.md", "a thread")
    put(".claude/settings.json", "{}")
    put(".embeddings/index.bin", "vectors")
    (w / "settings.json").write_text(json.dumps(
        {"schema_version": 1, "keys": {"anthropic": {"id": "k1", "label": "Newsroom key"}}}))

    (wdg_home / "credentials.json").write_text(json.dumps({"anthropic_api_key": SECRET}))
    (wdg_home / "chats").mkdir()
    (wdg_home / "chats" / "chat.json").write_text("a conversation")
    (wdg_home / "logs").mkdir()
    (wdg_home / "logs" / "run.log").write_text("a log")
    (wdg_home / "config.json").write_text(json.dumps({
        "wayback_secret_key": SECRET, "wayback_access_key": SECRET, "projects_dir": str(wdg_home),
        "extractor_model": "haiku", "unknown_key": "whatever"}))

    # Links that would reach outside the folder: a linked file and a linked folder.
    outside = v.parent / "outside"
    outside.mkdir()
    (outside / "elsewhere.md").write_text("a file beside the folder")
    os.symlink(wdg_home / "credentials.json", v / "entities" / "credentials.md")
    os.symlink(outside, v / "documents" / "linked")
    os.symlink(wdg_home, v / "briefings" / "home")
    return v


def _copy(vault, tmp_path, **kw):
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    rep = CollectingReporter()
    result = troubleshooting_copy(rep, vault, output=str(out), **kw)
    z = zipfile.ZipFile(result["output"])
    top = z.namelist()[0].split("/", 1)[0]
    names = {n.split("/", 1)[1] for n in z.namelist()}
    return result, z, top, names, rep


def test_copy_holds_the_records_and_notes(vault, tmp_path):
    result, z, top, names, rep = _copy(vault, tmp_path)
    assert result["output"].endswith(".zip") and top == result["output"].rsplit("/", 1)[1][:-4]
    assert {"manifest.json", ".watchdog/registry/entities.json", ".watchdog/registry/documents.json",
            ".watchdog/registry/usage/usage-2026-03-02.json",
            ".watchdog/extracted/" + "1" * 64 + ".json",
            ".watchdog/timeline/canonical/2024-01.ndjson",
            ".watchdog/briefings/2026-03-02-09-00.json"} <= names
    assert any(n.startswith("entities/person/") for n in names)
    assert any(n.startswith("documents/") for n in names)
    assert any(n.startswith("briefings/") for n in names)
    assert result["files"] == len(names) - 1 and not result["originals"]
    assert "not included" in rep.text


def test_originals_and_left_out_folders(vault, tmp_path):
    _, _, _, names, _ = _copy(vault, tmp_path)
    for gone in ("morgue/", "incoming/", ".watchdog/queue/", ".watchdog/staging/",
                 ".watchdog/text-positions/", ".watchdog/history/", ".watchdog/backups/",
                 ".watchdog/tmp/", "queries/", "wiki/", ".claude/", ".embeddings/"):
        assert not any(n.startswith(gone) for n in names), gone
    assert ".watchdog/registry/.processing-lock" not in names
    assert ".watchdog/settings.json" not in names

    result, _, _, names, _ = _copy(vault, tmp_path, originals=True)
    assert result["originals"]
    assert {"morgue/acme-corp/annual-report/report-one.pdf", "morgue/acme-corp/annual-report/report-one.md",
            "incoming/waiting.pdf", ".watchdog/queue/" + "4" * 64 + ".json",
            ".watchdog/staging/" + "4" * 64 + "/waiting.pdf",
            ".watchdog/text-positions/" + "1" * 64 + ".json"} <= names
    assert not any(n.startswith((".watchdog/history/", "queries/", "wiki/")) for n in names)


@pytest.mark.parametrize("originals", [False, True])
def test_nothing_from_outside_the_folder_and_no_credential(vault, tmp_path, originals):
    _, z, _, names, _ = _copy(vault, tmp_path, originals=originals)
    assert "entities/credentials.md" not in names
    assert not any(n.startswith(("documents/linked", "briefings/home")) for n in names)
    for info in z.infolist():
        data = z.read(info)
        assert SECRET.encode() not in data, info.filename
        assert b"a conversation" not in data and b"a log" not in data
        assert b"a file beside the folder" not in data
    root = vault.resolve()
    for e in troubleshoot.plan(vault, originals):
        assert e.path.resolve().is_relative_to(root)


def test_manifest(vault, tmp_path):
    _, z, top, _, _ = _copy(vault, tmp_path)
    m = json.loads(z.read(f"{top}/manifest.json"))
    assert m["format_version"] == 1 and m["includes_originals"] is False
    assert m["watchdog_version"] and m["platform"]["system"] and m["platform"]["python"]
    # Known settings only: never a secret, a folder path or an unknown key.
    assert m["settings"] == {"extractor_model": "haiku"}
    assert m["investigation_settings"] == {"keys": {"anthropic": {"label": "Newsroom key"}}}
    assert m["left_out"]


def test_sizes_match_what_is_written(vault, tmp_path):
    s = troubleshoot.sizes(vault)
    result, *_ = _copy(vault, tmp_path)
    assert result["files"] == s["files"] and s["bytes"] > 0
    with_orig, _, _, names, _ = _copy(vault, tmp_path, originals=True)
    assert with_orig["files"] == s["files"] + s["originals_files"]
    assert s["originals_files"] == sum(n.startswith(troubleshoot.ORIGINALS_DIRS) for n in names) >= 6


def test_a_second_copy_gets_its_own_name_and_no_partial_file_is_left(vault, tmp_path, monkeypatch):
    monkeypatch.setattr(troubleshoot, "_name", lambda v: "fixed")
    a, *_ = _copy(vault, tmp_path)
    b, *_ = _copy(vault, tmp_path)
    assert a["output"] != b["output"] and b["output"].endswith("fixed-2.zip")
    assert not list((tmp_path / "out").glob("*.part"))


def test_a_copy_saved_inside_the_investigation_does_not_copy_itself(vault):
    result = troubleshooting_copy(CollectingReporter(), vault, output=str(vault / "briefings"))
    names = zipfile.ZipFile(result["output"]).namelist()
    assert not any(n.endswith(".zip") or n.endswith(".part") for n in names)


def test_missing_folder_is_refused(vault, tmp_path):
    with pytest.raises(SystemExit, match="does not exist"):
        troubleshooting_copy(CollectingReporter(), vault, output=str(tmp_path / "nope"))


def test_size_rpc(vault):
    from tests.gui_support import call
    s = call("vault.troubleshootingSize", vault=str(vault))
    assert set(s) == {"files", "bytes", "originals_files", "originals_bytes"}
    assert s == troubleshoot.sizes(vault) and s["originals_files"] >= 6
