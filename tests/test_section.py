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


def test_run_force_budget_sections_below_threshold(tmp_path, monkeypatch):
    monkeypatch.setattr(section, "_config_get", _config(threshold=100, budget=200, overlap=0))
    vault = _vault(tmp_path)
    _write_queue(vault, "doc1", [{"page": 1, "markdown": "x" * 40},
                                 {"page": 2, "markdown": "x" * 40}], 2)  # 20 est tokens, under threshold
    # force_budget sections it anyway, capped at half the doc → ≥2 sections
    result = section.run(vault, "doc1", force_budget=1000)
    assert result["sectioned"] is True
    assert len(result["sections"]) >= 2


def test_run_missing_queue_file_errors(tmp_path):
    vault = tmp_path / "vault"
    (vault / ".watchdog").mkdir(parents=True)
    assert "error" in section.run(vault, "nope")


# ── provider-aware thresholds (#321) ─────────────────────────────────────────

def test_model_defaults_scale_with_context_window():
    # 0.6 / 0.3 of the model's context window; Claude 200K reproduces the historical 120K/60K.
    assert section.model_defaults("sonnet") == (120_000, 60_000)
    assert section.model_defaults(None) == (120_000, 60_000)              # default tier
    assert section.model_defaults("deepseek-v4-flash") == (600_000, 300_000)   # 1M window
    assert section.model_defaults("gpt-5-mini") == (240_000, 120_000)          # 400K window


def test_section_token_threshold_model_aware(monkeypatch):
    monkeypatch.setattr(section, "_config_get", lambda k, d: d)   # no config override
    assert section.section_token_threshold("sonnet") == 120_000
    assert section.section_token_threshold("deepseek-v4-flash") == 600_000


def test_section_token_threshold_config_override_wins(monkeypatch):
    # An explicit config value overrides the model-aware default regardless of the model.
    monkeypatch.setattr(section, "_config_get",
                        lambda k, d: 42 if k == "section_token_threshold" else d)
    assert section.section_token_threshold("deepseek-v4-flash") == 42


def test_section_token_threshold_auto_uses_model_default(monkeypatch):
    # The 'auto' sentinel (or an unset key) falls back to the model-aware default.
    monkeypatch.setattr(section, "_config_get",
                        lambda k, d: "auto" if k == "section_token_threshold" else d)
    assert section.section_token_threshold("sonnet") == 120_000
    assert section.section_token_threshold("deepseek-v4-flash") == 600_000


def test_run_threshold_follows_model_window(tmp_path, monkeypatch):
    # With no config override, the same document sections under a small-window model but is
    # extracted whole under a large-window one — proving the model flows into the threshold.
    import watchdog.model_client as mc
    monkeypatch.setattr(section, "_config_get", lambda k, d: d)
    monkeypatch.setattr(mc, "context_window",
                        lambda model: 500 if model == "small" else 100_000)
    vault = _vault(tmp_path)
    pages = [{"page": n, "markdown": "x" * 400} for n in range(1, 11)]   # 1000 est tokens
    _write_queue(vault, "doc1", pages, 10)
    assert section.run(vault, "doc1", model="small")["sectioned"] is True     # 500*0.6=300 < 1000
    assert section.run(vault, "doc1", model="big")["sectioned"] is False      # 100000*0.6 ≫ 1000


# ── output-ceiling-aware thresholds (#343) ────────────────────────────────────

def test_model_defaults_capped_by_output_ceiling_for_openai_gemini():
    # A fixed-output-ceiling backend that can't paginate caps threshold/budget by the output-
    # derived input ceiling: 16000 * 0.7 / 0.8 = 14000 (budget = 14000 // 2 = 7000). The large
    # input window (400K/1M) is overridden by the much tighter output-driven cap.
    assert section.model_defaults("gpt-5-mini", backend="openai") == (14_000, 7_000)
    assert section.model_defaults("gemini-2.5-flash", backend="gemini") == (14_000, 7_000)


def test_model_defaults_uncapped_for_paginating_and_uncapped_backends():
    # claude-api/deepseek paginate their output, and the agent SDK has no ceiling — all keep the
    # pure input-window defaults regardless of the backend argument.
    assert section.model_defaults("sonnet", backend="claude-api") == (120_000, 60_000)
    assert section.model_defaults("sonnet", backend="claude-agent-sdk") == (120_000, 60_000)
    assert section.model_defaults("deepseek-v4-flash", backend="deepseek") == (600_000, 300_000)
    assert section.model_defaults("sonnet", backend=None) == (120_000, 60_000)


def test_section_token_threshold_capped_for_openai(monkeypatch):
    monkeypatch.setattr(section, "_config_get", lambda k, d: d)   # no config override
    # gpt-5-mini's 400K window would give 240K, but the output ceiling caps it to 14K.
    assert section.section_token_threshold("gpt-5-mini", backend="openai") == 14_000
    # Same model, a paginating backend → uncapped input-window default.
    assert section.section_token_threshold("gpt-5-mini", backend="claude-api") == 240_000


def test_run_sections_openai_doc_that_claude_would_extract_whole(tmp_path, monkeypatch):
    # A document that fits a big input window comfortably (extracted whole on a paginating backend)
    # must section on openai, whose fixed output ceiling it would otherwise overrun (#343).
    monkeypatch.setattr(section, "_config_get", lambda k, d: d)   # no config override
    vault = _vault(tmp_path)
    pages = [{"page": n, "markdown": "x" * 400} for n in range(1, 501)]   # 50_000 est tokens
    _write_queue(vault, "doc1", pages, 500)
    # 50K tokens < gpt-5-mini's 240K input threshold, but > its 14K output-capped threshold.
    assert section.run(vault, "doc1", model="gpt-5-mini", backend="claude-api")["sectioned"] is False
    assert section.run(vault, "doc1", model="gpt-5-mini", backend="openai")["sectioned"] is True

