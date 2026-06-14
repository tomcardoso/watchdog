import json
from pathlib import Path

from watchdog.pipeline import section
from watchdog.pipeline.section import plan_ranges, char_windows, est_tokens, est_tokens_from_pages


# ── token estimation ────────────────────────────────────────────────────────

def test_est_tokens_chars_over_four():
    assert est_tokens("a" * 40) == 10
    assert est_tokens("") == 0


def test_est_tokens_from_pages_sums():
    pages = [{"markdown": "a" * 40}, {"markdown": "b" * 80}]
    assert est_tokens_from_pages(pages) == 30


# ── plan_ranges (pages) ─────────────────────────────────────────────────────

def test_plan_ranges_with_overlap_covers_every_page():
    r = plan_ranges(120, 50, 3)
    assert r[0] == (1, 50) and r[1] == (48, 97) and r[2] == (95, 120)
    covered = set()
    for s, e in r:
        covered.update(range(s, e + 1))
    assert covered == set(range(1, 121))


def test_plan_ranges_overlap_capped_so_it_advances():
    r = plan_ranges(10, 3, 5)
    covered = set()
    for s, e in r:
        covered.update(range(s, e + 1))
    assert covered == set(range(1, 11))


# ── char_windows ────────────────────────────────────────────────────────────

def test_char_windows_with_overlap():
    assert char_windows(100, 40, 10) == [(0, 40), (30, 70), (60, 100)]


def test_char_windows_single():
    assert char_windows(30, 100, 10) == [(0, 30)]


def test_char_windows_empty():
    assert char_windows(0, 100, 10) == []


# ── run (token-gated) ───────────────────────────────────────────────────────

def _config(threshold=100, budget=200, overlap=0):
    return lambda k, d: {"section_token_threshold": threshold,
                         "section_token_budget": budget,
                         "section_overlap_tokens": overlap}.get(k, d)


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / ".watchdog" / "queue").mkdir(parents=True)
    (vault / ".watchdog" / "tmp").mkdir(parents=True)
    return vault


def _write_queue(vault: Path, sha: str, pages: list, page_count: int):
    (vault / ".watchdog" / "queue" / f"{sha}.json").write_text(
        json.dumps({"filename": "f.pdf", "page_count": page_count, "pages": pages})
    )


def test_run_below_token_threshold_not_sectioned(tmp_path, monkeypatch):
    monkeypatch.setattr(section, "_config_get", _config(threshold=100))
    vault = _vault(tmp_path)
    _write_queue(vault, "doc1", [{"page": 1, "markdown": "x" * 40},
                                 {"page": 2, "markdown": "x" * 40}], 2)  # 20 est tokens
    result = section.run(vault, "doc1")
    assert result["sectioned"] is False
    assert result["est_tokens"] == 20


def test_run_paginated_splits_on_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(section, "_config_get", _config(threshold=100, budget=200, overlap=0))
    vault = _vault(tmp_path)
    pages = [{"page": n, "markdown": "x" * 400} for n in range(1, 11)]  # 100 tok/page, 1000 total
    _write_queue(vault, "doc1", pages, 10)

    result = section.run(vault, "doc1")
    assert result["sectioned"] is True
    secs = result["sections"]
    assert all(s["paginated"] for s in secs)
    assert secs[0]["label"] == "pages 1–2"        # budget 200 / 100 tok-per-page = 2 pages
    first = (vault / secs[0]["pages_path"]).read_text()
    assert "<!-- PAGE 1 -->" in first and "<!-- PAGE 2 -->" in first
    assert "<!-- PAGE 3 -->" not in first


def test_run_non_paginated_splits_on_chars(tmp_path, monkeypatch):
    monkeypatch.setattr(section, "_config_get", _config(threshold=100, budget=200, overlap=0))
    vault = _vault(tmp_path)
    # single page (text file) of 1000 est tokens = 4000 chars
    _write_queue(vault, "doc1", [{"page": 1, "markdown": "y" * 4000}], 1)

    result = section.run(vault, "doc1")
    assert result["sectioned"] is True
    secs = result["sections"]
    assert all(not s["paginated"] for s in secs)
    assert secs[0]["label"].startswith("part 1 of")
    body = (vault / secs[0]["pages_path"]).read_text()
    assert "<!-- PAGE" not in body          # no page markers for non-paginated
    assert len(body) == 800                 # budget 200 tokens * 4 chars


def test_run_missing_queue_file_errors(tmp_path):
    vault = tmp_path / "vault"
    (vault / ".watchdog").mkdir(parents=True)
    assert "error" in section.run(vault, "nope")
