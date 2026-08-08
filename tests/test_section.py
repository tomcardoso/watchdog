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


def test_run_default_overlap_scales_with_budget_not_a_fixed_value(tmp_path, monkeypatch):
    # No section_overlap_tokens override: the default overlap must scale with budget (#490),
    # not stay fixed at a value large enough to swallow a small budget outright. Old behaviour
    # (a fixed 4,000-token overlap) would round to 4 overlap pages here and produce a
    # 2-page-per-step stride; the fix produces a clean, non-overlapping 6-page stride instead.
    monkeypatch.setattr(section, "_config_get",
                        lambda k, d: {"section_token_threshold": 100,
                                     "section_token_budget": 6000}.get(k, d))
    vault = _vault(tmp_path)
    pages = [{"page": n, "markdown": "x" * 4000} for n in range(1, 11)]   # 1000 tok/page, 10 pages
    _write_queue(vault, "doc1", pages, 10)

    result = section.run(vault, "doc1")
    assert result["sectioned"] is True
    labels = [s["label"] for s in result["sections"]]
    assert labels == ["pages 1–6", "pages 7–10"]


def test_default_overlap_reproduces_historical_value_at_claudes_default_budget():
    # _OVERLAP_NUMERATOR/_OVERLAP_DENOMINATOR must reproduce the old fixed 4,000-token overlap
    # exactly at Claude's default 60,000-token budget — this fix targets other backends'
    # runaway scaling, not a behaviour change on the default path.
    assert max(1, 60_000 * section._OVERLAP_NUMERATOR // section._OVERLAP_DENOMINATOR) == 4_000


def test_default_overlap_shrinks_for_a_smaller_output_capped_budget():
    # At the openai/gemini output-capped budget (14,000, post-#490), overlap scales down
    # proportionally instead of staying at the old fixed 4,000 (which would have been ~29% of
    # this budget alone, before even accounting for the halving bug also fixed in #490).
    assert max(1, 14_000 * section._OVERLAP_NUMERATOR // section._OVERLAP_DENOMINATOR) == 933


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
                        lambda model, backend=None: 500 if model == "small" else 100_000)
    vault = _vault(tmp_path)
    pages = [{"page": n, "markdown": "x" * 400} for n in range(1, 11)]   # 1000 est tokens
    _write_queue(vault, "doc1", pages, 10)
    assert section.run(vault, "doc1", model="small")["sectioned"] is True     # 500*0.6=300 < 1000
    assert section.run(vault, "doc1", model="big")["sectioned"] is False      # 100000*0.6 ≫ 1000


# ── output-ceiling-aware thresholds (#343) ────────────────────────────────────

def test_model_defaults_capped_by_output_ceiling_for_openai_gemini():
    # A fixed-output-ceiling backend that can't paginate caps threshold/budget by the output-
    # derived input ceiling: 16000 * 0.7 / 0.8 = 14000. Budget is NOT halved again on this path
    # (#490) — the ceiling-derived value already represents the full safe amount a single call
    # can handle, whether that call covers a whole document or one section. The large input
    # window (400K/1M) is overridden by the much tighter output-driven cap either way.
    assert section.model_defaults("gpt-5-mini", backend="openai") == (14_000, 14_000)
    assert section.model_defaults("gemini-2.5-flash", backend="gemini") == (14_000, 14_000)


def test_model_defaults_checks_extract_and_extract_section_ceilings_separately(monkeypatch):
    # threshold (gates whole-document extraction) and budget (sizes one section) are checked
    # against their own task's ceiling, not a single shared lookup (#490) — even though
    # _TASK_MAX_TOKENS happens to give both "extract" and "extract-section" the same value today.
    import watchdog.model_client as mc
    seen = []

    def fake_ceiling(task, backend, model):
        seen.append(task)
        return {"extract": 32_000, "extract-section": 8_000}[task]

    monkeypatch.setattr(mc, "output_ceiling_for_sectioning", fake_ceiling)
    threshold, budget = section.model_defaults("gpt-5-mini", backend="openai")
    assert sorted(seen) == ["extract", "extract-section"]
    assert threshold == int(32_000 * 0.7 / 0.8)   # capped by the "extract" ceiling
    assert budget == int(8_000 * 0.7 / 0.8)        # capped by the "extract-section" ceiling


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


# ── tokenizer-aware thresholds (#574) ────────────────────────────────────────

def test_model_defaults_shrinks_for_new_tokenizer_claude_model():
    # Sonnet 5 / Opus 4.8 use a newer tokenizer producing ~30% more real tokens for the same
    # text than the chars/4 est_tokens heuristic assumes — threshold/budget shrink by that same
    # ratio so the real tokens a call sends still respect the 200K context window.
    assert section.model_defaults("sonnet-5") == (int(120_000 / 1.3), int(60_000 / 1.3))
    assert section.model_defaults("opus") == (int(120_000 / 1.3), int(60_000 / 1.3))


def test_model_defaults_unchanged_for_old_tokenizer_and_non_claude_models():
    # No behaviour change for anyone on the old tokenizer or a non-Anthropic provider (#574) —
    # same historical figures as before this fix.
    assert section.model_defaults("sonnet") == (120_000, 60_000)
    assert section.model_defaults("haiku") == (120_000, 60_000)
    assert section.model_defaults("deepseek-v4-flash") == (600_000, 300_000)
    assert section.model_defaults("gpt-5-mini") == (240_000, 120_000)


def test_run_sections_earlier_on_new_tokenizer_claude_model(tmp_path, monkeypatch):
    # A document that fits comfortably under Sonnet 4.6's threshold must section under Sonnet 5's
    # tokenizer-adjusted, smaller threshold for the exact same est-token count.
    monkeypatch.setattr(section, "_config_get", lambda k, d: d)   # no config override
    vault = _vault(tmp_path)
    # 100_000 est tokens: under Sonnet 4.6's 120K threshold, over Sonnet 5's ~92.3K one.
    pages = [{"page": n, "markdown": "x" * 4000} for n in range(1, 101)]
    _write_queue(vault, "doc1", pages, 100)
    assert section.run(vault, "doc1", model="sonnet")["sectioned"] is False
    assert section.run(vault, "doc1", model="sonnet-5")["sectioned"] is True

