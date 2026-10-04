"""`watchdog review` — the one-at-a-time queue — and the near-duplicate stamp it reads (D252)."""

import argparse
import json
import re

import pytest

import watchdog.cmd.review as review
from watchdog.pipeline import orchestrate, resolutions

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(s: str) -> str:
    return _ANSI.sub("", s)


_CALLOUT = "> [!contradiction] Date of incorporation\n> 2019 per the filing vs 2020 per the registry"
_ALERTS = """
## 2026-10-03 16:12 — 2 matches (1 term, 2 documents)

### `offshore` — 2 documents

- [ ] **[[documents/memo|memo.pdf]]** <!--wid:alert:aaaaaaa:t1-->
  - p. 3: paid through an offshore account
- [ ] **[[documents/letter|letter.pdf]]** <!--wid:alert:bbbbbbb:t1-->
  - p. 1: an offshore subsidiary
"""


@pytest.fixture
def vault(tmp_path, monkeypatch):
    v = tmp_path / "probe"
    reg = v / ".watchdog" / "registry"
    reg.mkdir(parents=True)
    (v / "briefings").mkdir()
    (reg / "entities.json").write_text(json.dumps({
        "acme": {"name": "Acme", "note_path": "entities/organization/acme",
                 "roles": [{"target_id": "ghost", "target_name": "Ghost Co"}],
                 "contradictions": [_CALLOUT], "appears_in": ["s1"]}}))
    (reg / "documents.json").write_text(json.dumps({
        "a" * 64: {"filename": "memo-copy.pdf", "document_note": "documents/memo-copy",
                   "near_duplicate_of": "[[documents/memo|memo.pdf]]"},
        "b" * 64: {"filename": "memo.pdf", "document_note": "documents/memo",
                   "near_duplicate_of": None}}))
    (v / "briefings" / "alerts-2026-10-03.md").write_text(_ALERTS)
    monkeypatch.chdir(v)
    return v


def test_open_items_covers_every_kind(vault):
    items = review.open_items(vault)
    by_kind = {k: [i for i in items if i["kind"] == k] for k in review.KINDS}
    (c,) = by_kind["contradictions"]
    assert c["title"] == "Acme — Date of incorporation"
    assert c["detail"] == ["2019 per the filing vs 2020 per the registry"]
    assert c["note"] == "entities/organization/acme"
    assert c["rid"] == resolutions.contradiction_id(_CALLOUT)

    (lead,) = by_kind["leads"]
    assert lead["title"].startswith("Ghost Co") and lead["rid"] == "lead:unprofiled:ghost"

    a1, a2 = by_kind["alerts"]
    assert (a1["title"], a1["note"], a1["detail"]) == (
        "`offshore` in memo.pdf", "documents/memo", ["p. 3: paid through an offshore account"])
    assert a2["rid"] == "alert:bbbbbbb:t1"

    (d,) = by_kind["duplicates"]
    assert d["title"] == "memo-copy.pdf" and d["note"] == "documents/memo-copy"
    assert "Closely matches memo.pdf" in d["detail"][0]
    assert d["rid"] == resolutions.duplicate_id("a" * 64)


def test_resolved_items_drop_out(vault):
    resolutions.resolve(vault, ["alert:aaaaaaa:t1", resolutions.duplicate_id("a" * 64),
                                resolutions.contradiction_id(_CALLOUT)])
    items = review.open_items(vault)
    assert [i["rid"] for i in items] == ["lead:unprofiled:ghost", "alert:bbbbbbb:t1"]
    assert review.count_open_duplicates(vault) == 0


def test_an_alert_repeated_across_files_appears_once(vault):
    (vault / "briefings" / "alerts-2026-10-04.md").write_text(_ALERTS)
    assert len(review.open_items(vault, ("alerts",))) == 2


def _picks(monkeypatch, *labels):
    """Answer successive pick() calls by choice label."""
    queue = list(labels)

    def pick(items, current=0, **k):
        label = queue.pop(0)
        return review.interactive.CANCELLED if label is None else items.index(label)
    monkeypatch.setattr(review.interactive, "pick", pick)
    monkeypatch.setattr(review.sys.stdin, "isatty", lambda: True)
    return queue


def test_walk_marks_handled_keeps_open_and_opens_obsidian(vault, monkeypatch, capsys):
    opened = []
    monkeypatch.setattr(review, "open_url", lambda url: opened.append(url) or True)
    _picks(monkeypatch, "Open in Obsidian", "Mark as handled", "Keep open", None)
    review.cmd_review(argparse.Namespace(kind=None))
    assert len(opened) == 1 and opened[0].startswith("obsidian://open?path=")
    assert "acme.md" in opened[0]
    assert resolutions.resolved_ids(vault) == {resolutions.contradiction_id(_CALLOUT)}
    assert "1 handled" in _plain(capsys.readouterr().out)


def test_review_one_kind(vault, monkeypatch):
    _picks(monkeypatch, "Mark as handled")
    review.cmd_review(argparse.Namespace(kind="duplicates"))
    assert resolutions.resolved_ids(vault) == {resolutions.duplicate_id("a" * 64)}


def test_off_a_terminal_it_prints_the_list(vault, monkeypatch, capsys):
    monkeypatch.setattr(review.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(review.interactive, "pick", lambda *a, **k: pytest.fail("prompted"))
    review.cmd_review(argparse.Namespace(kind=None))
    out = _plain(capsys.readouterr().out)
    for text in ("Contradictions", "Leads", "Watch-list hits", "Possible duplicate documents",
                 "resolve: alert:aaaaaaa:t1", "watchdog review resolve <id>"):
        assert text in out


def test_nothing_to_review(vault, capsys):
    resolutions.resolve(vault, [resolutions.duplicate_id("a" * 64)])
    review.cmd_review(argparse.Namespace(kind="duplicates"))
    assert "no open possible duplicate documents" in _plain(capsys.readouterr().out)


def test_review_outside_a_vault_exits(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit, match="not inside a Watchdog project"):
        review.cmd_review(argparse.Namespace(kind=None))


# ── near_duplicate_of stamp ──────────────────────────────────────────────────

@pytest.mark.parametrize("near_dup, expected", [
    (None, None),
    ({"near_duplicates": []}, None),
    ({"near_duplicates": [
        {"sha256": "1" * 64, "filename": "a.pdf", "similarity": 0.86, "document_note": "documents/a"},
        {"sha256": "2" * 64, "filename": "b.pdf", "similarity": 0.97, "document_note": "documents/b"},
    ]}, "[[documents/b|b.pdf]]"),
    ({"near_duplicates": [{"sha256": "3" * 64, "filename": "same|batch.pdf",
                           "similarity": 0.9, "document_note": ""}]}, "same-batch.pdf"),
])
def test_near_duplicate_of_names_the_closest_match(near_dup, expected):
    assert orchestrate._near_duplicate_of(near_dup) == expected


def test_stamp_document_records_the_near_duplicate():
    extraction = {"document": {}}
    pf = {"filename": "copy.pdf", "pages": ["x"], "near_dup": {"near_duplicates": [
        {"sha256": "1" * 64, "filename": "orig.pdf", "similarity": 0.9,
         "document_note": "documents/orig"}]}}
    orchestrate._stamp_document(extraction, sha="c" * 64, pf=pf, skill_label="generic")
    assert extraction["document"]["near_duplicate_of"] == "[[documents/orig|orig.pdf]]"
