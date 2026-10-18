"""Unit tests for the shared helpers in `watchdog.gui.vaultio`."""

import sys

import pytest

from watchdog.gui import vaultio
from watchdog.gui.rpc import RpcError


def test_split_frontmatter():
    fm, body = vaultio.split_frontmatter("---\ntitle: T\ndate: 2024-01-15\ntags: [a, b]\n---\n\nBody\n")
    assert fm == {"title": "T", "date": "2024-01-15", "tags": ["a", "b"]} and body.strip() == "Body"
    assert vaultio.split_frontmatter("no frontmatter") == ({}, "no frontmatter")
    assert vaultio.split_frontmatter("---\n: : bad\n  - [\n---\nrest")[0] == {}
    assert vaultio.split_frontmatter("---\n- a list\n---\nrest")[0] == {}
    assert vaultio.split_frontmatter("")[0] == {}


def test_split_sections_respects_fences_and_notes_tail():
    body = "# T\n\n## Summary\n\nS\n\n```\n## Not a heading\n```\n\n## Notes\n\nmine\n\n## Mine too\n\nmore\n"
    s = vaultio.split_sections(body)
    assert list(s) == ["summary", "notes"]
    assert "Not a heading" in s["summary"] and s["notes"].endswith("more")


def test_first_paragraph_and_title():
    assert vaultio.first_paragraph("<!-- c -->\n\nOne\nstill one\n\nTwo") == "One\nstill one"
    assert vaultio.first_paragraph("<!-- only a comment -->") is None
    assert vaultio.note_title({}, "intro\n# Heading\n") == "Heading"
    assert vaultio.note_title({"title": " X "}, "# Heading") == "X"


def test_split_pages():
    assert vaultio.split_pages("<!-- PAGE 1 -->\na\n<!-- PAGE 3 -->\nb") == [
        {"page": 1, "text": "a"}, {"page": 3, "text": "b"}]
    assert vaultio.split_pages("plain") == [{"page": 1, "text": "plain"}]
    assert vaultio.split_pages("  ") == []


def test_dates():
    assert [vaultio.precision(d) for d in ("2024", "2024-05", "2024-05-06", "May", None)] == [
        "year", "month", "day", None, None]
    assert sorted(["2024-05-06", "2024", "2024-05"], key=vaultio.date_sort_key) == ["2024", "2024-05", "2024-05-06"]


def test_strip_ansi_covers_colour_and_hyperlinks():
    assert vaultio.strip_ansi("\x1b[1mBold\x1b[0m \x1b]8;;http://x\x1b\\link\x1b]8;;\x1b\\") == "Bold link"


def test_resolve_in_vault(tmp_path):
    (tmp_path / "a").mkdir()
    ok = vaultio.resolve_in_vault(tmp_path, "a/b.md")
    assert ok.name == "b.md" and ok.parent.name == "a"
    for bad in ("", "  ", "../x", "a/../../x", "/abs", "C:/x", "a\x00b", None, 5, ".."):
        with pytest.raises(RpcError) as e:
            vaultio.resolve_in_vault(tmp_path, bad)
        assert e.value.code == "bad_path"


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_resolve_in_vault_follows_symlinks_but_not_out(tmp_path):
    vault, outside = tmp_path / "v", tmp_path / "o"
    (vault / "real").mkdir(parents=True)
    outside.mkdir()
    (vault / "inside").symlink_to(vault / "real", target_is_directory=True)
    (vault / "out").symlink_to(outside, target_is_directory=True)
    assert vaultio.resolve_in_vault(vault, "inside/x.md").parent.name == "real"
    with pytest.raises(RpcError):
        vaultio.resolve_in_vault(vault, "out/x.md")


def test_write_text_atomic_replaces_and_keeps_mode(tmp_path):
    p = tmp_path / "f.md"
    p.write_text("old")
    p.chmod(0o640)
    vaultio.write_text_atomic(p, "new")
    assert p.read_text() == "new" and [x.name for x in tmp_path.iterdir()] == ["f.md"]
    if sys.platform != "win32":
        assert p.stat().st_mode & 0o777 == 0o640
    vaultio.write_text_atomic(tmp_path / "sub" / "g.md", "x")
    assert (tmp_path / "sub" / "g.md").read_text() == "x"


def test_plain_makes_yaml_values_json_safe():
    import datetime
    assert vaultio.plain({1: datetime.date(2024, 1, 2), "s": {1, }, "t": (1, 2)}) == {
        "1": "2024-01-02", "s": [1], "t": [1, 2]}
