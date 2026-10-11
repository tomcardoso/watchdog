"""Pre-processing named files, downloading links, the Wayback setting and search's JSON shape
(`watchdog.ops.ingest`, `watchdog.ops.research`, `watchdog.search`). Ported from the
command-line tests when the command line was removed (D300)."""

import json

import pytest

from watchdog import search
from watchdog.ops import CollectingReporter
from watchdog.ops import ingest as ing
from watchdog.ops import research as research_op

pytest_plugins = ["tests.gui_support"]   # the wdg_home fixture


@pytest.fixture
def vault(wdg_home, tmp_path):
    from watchdog.ops.projects import create
    return create("Shell Co", parent=tmp_path / "Investigations")


# ── pre-processing named files ───────────────────────────────────────────────────────────

def _spy_run_ingest(monkeypatch):
    import watchdog.pipeline.preprocess_batch as ppb
    calls = []
    monkeypatch.setattr(ppb, "run_ingest", lambda v, files=None, **k: calls.append(files))
    return calls


def test_preprocess_a_file_already_in_incoming_reads_it_in_place(vault, monkeypatch):
    calls = _spy_run_ingest(monkeypatch)
    f = vault / "incoming" / "doc.pdf"
    f.write_bytes(b"")
    ing.preprocess(vault, [str(f)])
    assert calls == [[f.resolve()]]


def test_preprocess_copies_a_file_from_outside_incoming(vault, monkeypatch, tmp_path):
    """A file named from anywhere else (Downloads, the vault's own context/) is copied in, not
    taken away from where the reporter keeps it, and its sidecar comes with it."""
    calls = _spy_run_ingest(monkeypatch)
    outside = tmp_path / "downloads" / "doc.pdf"
    outside.parent.mkdir()
    outside.write_bytes(b"%PDF original")
    (outside.parent / "doc.pdf.yml").write_text("source: x\n")
    ing.preprocess(vault, [str(outside)])
    assert outside.exists()
    copied = calls[0][0]
    assert copied.parent.resolve() == (vault / "incoming").resolve()
    assert copied.read_bytes() == b"%PDF original"
    assert copied.with_name("doc.pdf.yml").exists()


def test_preprocess_reads_several_files_and_folders(vault, monkeypatch, tmp_path):
    calls = _spy_run_ingest(monkeypatch)
    folder = tmp_path / "a-folder"
    folder.mkdir()
    (folder / "one.pdf").write_bytes(b"%PDF one")
    (folder / "two.txt").write_text("two")
    single = tmp_path / "three.pdf"
    single.write_bytes(b"%PDF three")
    ing.preprocess(vault, [str(folder), str(single)])
    assert sorted(f.name for f in calls[0]) == ["one.pdf", "three.pdf", "two.txt"]
    assert all(f.parent.resolve() == (vault / "incoming").resolve() for f in calls[0])
    assert single.exists() and (folder / "one.pdf").exists()


def test_preprocess_a_missing_file_stops(vault, monkeypatch):
    _spy_run_ingest(monkeypatch)
    with pytest.raises(SystemExit, match="not found"):
        ing.preprocess(vault, ["/no/such/file.pdf"])


def test_preprocess_refuses_a_path_inside_the_investigation(vault, monkeypatch):
    _spy_run_ingest(monkeypatch)
    with pytest.raises(SystemExit, match="inside this investigation"):
        ing.preprocess(vault, [str(vault / "documents")])


# ── the Wayback Machine setting ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("cfg, expected", [
    ({"wayback_access_key": "a", "wayback_secret_key": "s"}, None),                 # save flag off
    ({"wayback_save": True, "wayback_access_key": "a"}, None),                       # a key missing
    ({"wayback_save": True, "wayback_access_key": "a", "wayback_secret_key": "s"}, ("a", "s")),
    (None, None),                                                                    # no settings
])
def test_wayback_creds(wdg_home, cfg, expected):
    if cfg is not None:
        (wdg_home / "config.json").write_text(json.dumps(cfg))
    assert research_op.wayback_creds() == expected


# ── downloading links ────────────────────────────────────────────────────────────────────

def _fake_deposit_many(monkeypatch, captured):
    from watchdog.pipeline.research import Deposit

    def fake(vault, entries, **kw):
        captured["entries"] = entries
        captured["wayback"] = kw.get("wayback")
        return [Deposit(e["url"], vault / "incoming" / "x.html") for e in entries]

    monkeypatch.setattr("watchdog.pipeline.research.deposit_many", fake)


def test_fetch_links_downloads_inline_urls(vault, monkeypatch):
    captured = {}
    _fake_deposit_many(monkeypatch, captured)
    rep = CollectingReporter()
    out = research_op.fetch_links(rep, vault, targets=["https://a.com/x", "https://b.com/y"])
    assert [e["url"] for e in captured["entries"]] == ["https://a.com/x", "https://b.com/y"]
    assert out == {"downloaded": 2, "failed": 0}
    assert "Downloaded 2 of 2" in rep.text and "ready to add" in rep.text


def test_fetch_links_reads_a_links_file(vault, monkeypatch):
    (vault / "links.txt").write_text("https://a.com\n# a comment\n\nhttps://b.com\tTitle B\n")
    captured = {}
    _fake_deposit_many(monkeypatch, captured)
    research_op.fetch_links(CollectingReporter(), vault, targets=[str(vault / "links.txt")])
    assert [e["url"] for e in captured["entries"]] == ["https://a.com", "https://b.com"]
    assert captured["entries"][1]["title"] == "Title B"   # TSV columns preserved


def test_fetch_links_with_an_empty_file_stops(vault):
    (vault / "empty.txt").write_text("# only a comment\n")
    with pytest.raises(SystemExit, match="no links"):
        research_op.fetch_links(CollectingReporter(), vault, targets=[str(vault / "empty.txt")])


def test_fetch_links_archives_when_wayback_is_set_up(vault, wdg_home, monkeypatch):
    (wdg_home / "config.json").write_text(json.dumps(
        {"wayback_save": True, "wayback_access_key": "a", "wayback_secret_key": "s"}))
    captured = {}
    _fake_deposit_many(monkeypatch, captured)
    research_op.fetch_links(CollectingReporter(), vault, targets=["https://a.com/x"])
    assert captured["wayback"] == ("a", "s")


# ── search's JSON shape and name matches ─────────────────────────────────────────────────

def test_build_json_shape():
    passages = [{"type": "passage", "filename": "acme.pdf", "page": 3,
                 "text": "the kickback to Cyprus", "context": "Acme filing", "score": 0.51234}]
    notes = [{"type": "note", "note_path": "entities/person/jane",
              "preview": "Jane is a director", "score": 0.4}]
    out = search.build_json("kickback", passages, notes)
    assert out["query"] == "kickback"
    # only the fields a consumer needs: internal type/context dropped, score rounded
    assert {k: out["passages"][0][k] for k in ("filename", "page", "text", "score")} == {
        "filename": "acme.pdf", "page": 3, "text": "the kickback to Cyprus", "score": 0.5123}
    assert "context" not in out["passages"][0] and "type" not in out["passages"][0]
    assert out["notes"][0]["note_path"] == "entities/person/jane"
    assert json.loads(json.dumps(out, ensure_ascii=False))["passages"][0]["page"] == 3


def test_build_json_empty():
    out = search.build_json("nothing", [], [])
    assert (out["query"], out["passages"], out["notes"], out["exact"]) == ("nothing", [], [], [])


def test_manifest_matches_by_name_alias_and_not_otherwise():
    manifest = {"alice-smith": {"name": "Alice Smith", "type": "Person",
                                "aliases": ["A. Smith"], "note_path": "entities/person/alice-smith"}}
    hits = search.manifest_matches(manifest, "alice")
    assert len(hits) == 1 and hits[0]["note_path"] == "entities/person/alice-smith"
    assert len(search.manifest_matches(manifest, "A. Smith")) == 1
    assert search.manifest_matches(manifest, "Bob Jones") == []
