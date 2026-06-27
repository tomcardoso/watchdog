"""Tests for the deterministic watch-word scan (#165)."""

import json
from pathlib import Path

from watchdog.pipeline import watchlist


def _build_vault(tmp_path: Path, *, watchlist_text=None, pages=None,
                 manifest=None, morgue_path="morgue/report/doc.pdf",
                 sha="abc123", filename="doc.pdf") -> Path:
    """Make a minimal vault: optional watchlist.md, a page-marked morgue .md, and the
    documents/manifest registries the scan reads."""
    vault = tmp_path / "vault"
    (vault / ".watchdog" / "Registry").mkdir(parents=True)
    if watchlist_text is not None:
        (vault / "watchlist.md").write_text(watchlist_text, encoding="utf-8")

    if pages is not None:
        md = vault / Path(morgue_path).with_suffix(".md")
        md.parent.mkdir(parents=True, exist_ok=True)
        body = "\n\n".join(f"<!-- PAGE {n} -->\n\n{text}" for n, text in pages)
        md.write_text(body + "\n", encoding="utf-8")

    docs_reg = {sha: {"sha256": sha, "filename": filename,
                      "document_note": "documents/doc", "morgue_path": morgue_path}}
    (vault / ".watchdog" / "Registry" / "documents.json").write_text(json.dumps(docs_reg))
    (vault / ".watchdog" / "Registry" / "manifest.json").write_text(json.dumps(manifest or {}))
    return vault


def _results(sha="abc123", filename="doc.pdf", status="ok"):
    return [{"sha256": sha, "filename": filename, "status": status}]


# ── load_terms ──────────────────────────────────────────────────────────────

def test_load_terms_skips_comments_and_blanks(tmp_path):
    vault = _build_vault(tmp_path, watchlist_text="# a comment\n\nAcme Corp\n   \n# another\n")
    terms = watchlist.load_terms(vault)
    assert [t["term"] for t in terms] == ["Acme Corp"]


def test_load_terms_absent_file(tmp_path):
    vault = _build_vault(tmp_path)  # no watchlist.md
    assert watchlist.load_terms(vault) == []


def test_load_terms_invalid_regex_skipped(tmp_path):
    vault = _build_vault(tmp_path, watchlist_text="/[unclosed/\nGood\n")
    assert [t["term"] for t in watchlist.load_terms(vault)] == ["Good"]


# ── scan: matching semantics ─────────────────────────────────────────────────

def test_scan_case_insensitive(tmp_path):
    vault = _build_vault(tmp_path, watchlist_text="acme corp\n",
                         pages=[(1, "Payment to ACME CORP was approved.")])
    hits = watchlist.scan(vault, _results())
    assert len(hits) == 1
    assert hits[0]["term"] == "acme corp"
    assert hits[0]["page"] == 1


def test_scan_word_boundary(tmp_path):
    vault = _build_vault(tmp_path, watchlist_text="Ana\n",
                         pages=[(1, "She ate a banana, not Ana's lunch.")])
    hits = watchlist.scan(vault, _results())
    # matches the standalone "Ana", not the "ana" inside "banana"
    assert len(hits) == 1
    assert "**Ana**" in hits[0]["snippet"]


def test_scan_regex(tmp_path):
    vault = _build_vault(tmp_path, watchlist_text="/Smith,?\\s+John/\n",
                         pages=[(1, "Defendant Smith, John appeared.")])
    hits = watchlist.scan(vault, _results())
    assert len(hits) == 1


def test_scan_no_match(tmp_path):
    vault = _build_vault(tmp_path, watchlist_text="Nonexistent\n",
                         pages=[(1, "Nothing relevant here.")])
    assert watchlist.scan(vault, _results()) == []


# ── scan: page attribution ───────────────────────────────────────────────────

def test_scan_page_attribution(tmp_path):
    vault = _build_vault(tmp_path, watchlist_text="target\n",
                         pages=[(1, "first page"), (2, "second page"), (3, "the target is here")])
    hits = watchlist.scan(vault, _results())
    assert len(hits) == 1
    assert hits[0]["page"] == 3
    assert "PAGE" not in hits[0]["snippet"]  # page markers stripped from snippets


# ── scan: entity annotation ──────────────────────────────────────────────────

def test_scan_annotates_known_entity(tmp_path):
    manifest = {"acme-corporation": {
        "name": "Acme Corporation", "type": "company", "aliases": ["Acme Corp"],
        "note_path": "entities/company/acme-corporation"}}
    vault = _build_vault(tmp_path, watchlist_text="Acme Corp\n", manifest=manifest,
                         pages=[(1, "Paid to Acme Corp today.")])
    hits = watchlist.scan(vault, _results())
    assert hits[0]["entity"]["name"] == "Acme Corporation"
    assert hits[0]["entity"]["note_path"] == "entities/company/acme-corporation"


def test_scan_unknown_term_no_entity(tmp_path):
    vault = _build_vault(tmp_path, watchlist_text="Acme Corp\n",
                         pages=[(1, "Paid to Acme Corp today.")])
    assert watchlist.scan(vault, _results())[0]["entity"] is None


# ── scan: no-op and robustness ───────────────────────────────────────────────

def test_scan_empty_watchlist_noop(tmp_path):
    vault = _build_vault(tmp_path, watchlist_text="# only comments\n",
                         pages=[(1, "Acme Corp appears here.")])
    assert watchlist.scan(vault, _results()) == []


def test_scan_skips_non_ok_results(tmp_path):
    vault = _build_vault(tmp_path, watchlist_text="Acme\n",
                         pages=[(1, "Acme is here.")])
    assert watchlist.scan(vault, _results(status="failed")) == []


def test_scan_missing_morgue_md_no_crash(tmp_path):
    vault = _build_vault(tmp_path, watchlist_text="Acme\n")  # no pages → no morgue .md
    assert watchlist.scan(vault, _results()) == []


# ── write_alerts ─────────────────────────────────────────────────────────────

def test_write_alerts_creates_dated_file(tmp_path):
    import datetime
    vault = _build_vault(tmp_path, watchlist_text="Acme Corp\n",
                         pages=[(1, "Paid to Acme Corp.")])
    hits = watchlist.scan(vault, _results())
    result = watchlist.write_alerts(vault, hits)
    assert result is not None
    relpath, n_terms, n_docs = result
    today = datetime.date.today().strftime("%Y-%m-%d")
    assert relpath == f"briefings/alerts-{today}.md"
    assert (n_terms, n_docs) == (1, 1)
    content = (vault / relpath).read_text(encoding="utf-8")
    assert "Watch-word alerts" in content
    assert "Acme Corp" in content
    assert "doc.pdf" in content


def test_write_alerts_appends_per_run(tmp_path):
    vault = _build_vault(tmp_path, watchlist_text="Acme\n", pages=[(1, "Acme here.")])
    hits = watchlist.scan(vault, _results())
    relpath = watchlist.write_alerts(vault, hits)[0]
    watchlist.write_alerts(vault, hits)  # second run, same day
    content = (vault / relpath).read_text(encoding="utf-8")
    # one file header, two run sections (run headers are the only lines starting with "## ")
    assert content.count("# Watch-word alerts") == 1
    run_headers = [ln for ln in content.splitlines() if ln.startswith("## ")]
    assert len(run_headers) == 2


def test_write_alerts_no_hits_returns_none(tmp_path):
    import datetime
    vault = _build_vault(tmp_path)
    assert watchlist.write_alerts(vault, []) is None
    today = datetime.date.today().strftime("%Y-%m-%d")
    assert not (vault / "briefings" / f"alerts-{today}.md").exists()
