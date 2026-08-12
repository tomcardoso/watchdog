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


# ── plan_ranges (greedy page packing, #596) ─────────────────────────────────

def _covered(ranges):
    seen = set()
    for s, e in ranges:
        seen.update(range(s, e + 1))
    return seen


def test_plan_ranges_uniform_density_packs_to_the_budget():
    # 120 pages of 100 tokens, 5,000-token budget → 50 pages a section, no overlap.
    assert plan_ranges([100] * 120, 5_000, 0) == [(1, 50), (51, 100), (101, 120)]


def test_plan_ranges_packs_by_actual_density_not_the_average():
    # Same document, same budget, but the density is lumpy: pages 3 and 4 are 5x the rest. The
    # average (200 tok/page) would cut uniform 3-page ranges and put 1,200 tokens into pages 3–5,
    # double the budget. Greedy packing gives the dense stretch its own smaller sections.
    tokens = [100, 100, 500, 500, 100, 100, 100, 100]     # avg 200, budget 600
    ranges = plan_ranges(tokens, 600, 0)
    assert ranges == [(1, 2), (3, 3), (4, 5), (6, 8)]
    for s, e in ranges:
        assert sum(tokens[s - 1:e]) <= 600
    assert _covered(ranges) == set(range(1, 9))


def test_plan_ranges_page_bigger_than_the_budget_stands_alone():
    # Sections split on page boundaries, so an oversized page has nothing smaller to cut — it must
    # get its own section and the walk must still advance past it, not loop.
    assert plan_ranges([100, 5_000, 100, 100], 600, 0) == [(1, 1), (2, 2), (3, 4)]


def test_plan_ranges_overlap_replays_whole_trailing_pages_within_the_allowance():
    # 400-token overlap allowance: page 3 (100) and page 2 (100) both fit and are replayed;
    # page 1 would be a third 100 and fits too, but backing up that far would not advance.
    assert plan_ranges([100] * 9, 300, 400) == [(1, 3), (2, 4), (3, 5), (4, 6),
                                                (5, 7), (6, 8), (7, 9)]
    # A dense trailing page eats most of the same allowance on its own, so only it is replayed —
    # the page before it no longer fits, where a page-count overlap would have replayed it anyway.
    assert plan_ranges([100, 100, 350, 100, 100], 550, 400) == [(1, 3), (3, 5)]


def test_plan_ranges_overlap_never_stalls():
    # An overlap allowance far larger than the budget still advances one page a section, and every
    # page is still covered.
    ranges = plan_ranges([100] * 10, 300, 100_000)
    assert _covered(ranges) == set(range(1, 11))
    assert [s for s, _ in ranges] == list(range(1, 9))     # strictly advancing starts


def test_plan_ranges_empty_document():
    assert plan_ranges([], 500, 0) == []


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


def test_run_paginated_sections_follow_per_page_density(tmp_path, monkeypatch):
    # Density varies inside the document (#596): page 3 is twice as dense as the rest. The old
    # average-derived split (avg 120 tok/page → 2 pages a section) would have cut "pages 3–4" at
    # 300 tokens, half again over the 200-token budget; per-page packing isolates the dense page.
    monkeypatch.setattr(section, "_config_get", _config(threshold=100, budget=200, overlap=0))
    vault = _vault(tmp_path)
    pages = [{"page": n, "markdown": "x" * (800 if n == 3 else 400)} for n in range(1, 6)]
    _write_queue(vault, "doc1", pages, 5)

    result = section.run(vault, "doc1")
    assert [s["label"] for s in result["sections"]] == ["pages 1–2", "pages 3–3", "pages 4–5"]
    for sec in result["sections"]:
        assert est_tokens((vault / sec["pages_path"]).read_text()) <= 200 + 50   # + page markers


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


# ── output-ceiling-aware thresholds (#343, #598) ───────────────────────────────

def test_model_defaults_capped_by_output_ceiling_for_openai_gemini():
    # A fixed-output-ceiling backend that can't paginate caps threshold/budget by the output-
    # derived input ceiling: inverting the affine *total*-output fit (#542 follow-up) against the
    # real per-model wire ceiling (#598 — the catalogued `max_output_tokens` cap under headroom,
    # no longer a task base plus a reasoning reserve). No effort given ⇒ medium row.
    # gpt-5.4's ceiling is 128_000 catalogued * 0.9 = 115_200; (115_200*0.7-1_051)/0.989 exceeds
    # _MAX_OUTPUT_CAPPED_BUDGET, so it clamps to 50_000. gemini-3.5-flash's ceiling is
    # 65_536 * 0.9 = 58_982 (int-truncated); (58_982*0.7-1_051)/0.989 = 40_683, under the clamp.
    # Budget is NOT halved again on this path (#490) — the ceiling-derived value already
    # represents the full safe amount a single call can handle, whether that call covers a whole
    # document or one section. The large input window (1.05M/1M) is overridden by the much
    # tighter output-driven cap either way.
    assert section.model_defaults("gpt-5.4", backend="openai") == (50_000, 50_000)
    assert section.model_defaults("gemini-3.5-flash", backend="gemini") == (40_683, 40_683)


def test_model_defaults_output_capped_budget_varies_by_effort():
    # The wire ceiling itself no longer varies by effort (#598) — but `effort` still selects
    # which row of the output-density fit `_invert_output_ceiling` inverts that SAME ceiling
    # against, so the input-side budget still varies. gemini-3.5-flash's ceiling (58_982) shows
    # all three shapes: low's shallow marginal rate (fixed=2_509, marginal=0.103) extrapolates
    # past _MAX_OUTPUT_CAPPED_BUDGET and clamps; medium and high both land within the cap at
    # their own fixed/marginal pair's value.
    assert section.model_defaults("gemini-3.5-flash", backend="gemini", effort="low") == \
        (section._MAX_OUTPUT_CAPPED_BUDGET, section._MAX_OUTPUT_CAPPED_BUDGET)
    assert section.model_defaults("gemini-3.5-flash", backend="gemini", effort="medium") == (40_683, 40_683)
    assert section.model_defaults("gemini-3.5-flash", backend="gemini", effort="high") == (5_151, 5_151)


def test_model_defaults_uses_one_ceiling_lookup_for_both_threshold_and_budget(monkeypatch):
    # #598: the ceiling is no longer looked up per task — `output_ceiling_for_sectioning` doesn't
    # even take one — so ONE lookup now sizes both threshold and budget, replacing the old
    # separate "extract"/"extract-section" lookups (#490) that happened to resolve to the same
    # value under `_TASK_MAX_TOKENS`.
    import watchdog.model_client as mc
    calls = []

    def fake_ceiling(backend, model):
        calls.append((backend, model))
        return 32_000

    monkeypatch.setattr(mc, "output_ceiling_for_sectioning", fake_ceiling)
    threshold, budget = section.model_defaults("gpt-5-mini", backend="openai")
    assert calls == [("openai", "gpt-5-mini")]
    # No effort given ⇒ medium row (fixed=1051, marginal=0.989); threshold and budget are now the
    # SAME value, both capped by the one ceiling.
    expected = int((32_000 * 0.7 - 1_051) / 0.989)
    assert threshold == budget == expected


def test_model_defaults_floors_output_capped_budget_for_pathological_ceiling(monkeypatch):
    # A ceiling too small to clear the fixed per-call cost must not invert to zero or negative
    # (#542): with the medium row (fixed=1051, marginal=0.989), (100*0.7-1051)/0.989 is negative
    # without a floor.
    import watchdog.model_client as mc
    monkeypatch.setattr(mc, "output_ceiling_for_sectioning", lambda backend, model: 100)
    threshold, budget = section.model_defaults("gpt-5-mini", backend="openai")
    assert threshold == section._MIN_OUTPUT_CAPPED_BUDGET
    assert budget == section._MIN_OUTPUT_CAPPED_BUDGET


def test_model_defaults_uncapped_for_paginating_and_uncapped_backends():
    # claude-api/deepseek paginate their output, and the agent SDK has no ceiling — all keep the
    # pure input-window defaults regardless of the backend argument.
    assert section.model_defaults("sonnet", backend="claude-api") == (120_000, 60_000)
    assert section.model_defaults("sonnet", backend="claude-agent-sdk") == (120_000, 60_000)
    assert section.model_defaults("deepseek-v4-flash", backend="deepseek") == (600_000, 300_000)
    assert section.model_defaults("sonnet", backend=None) == (120_000, 60_000)


def test_section_token_threshold_capped_for_openai(monkeypatch):
    monkeypatch.setattr(section, "_config_get", lambda k, d: d)   # no config override
    # gpt-5.4's 1.05M window would give 630K, but the output ceiling — 128_000 catalogued * 0.9 =
    # 115_200 (#598) — caps it, and the medium-row inversion of that clamps to
    # _MAX_OUTPUT_CAPPED_BUDGET (50_000).
    assert section.section_token_threshold("gpt-5.4", backend="openai") == 50_000
    # Same model, a paginating backend → uncapped input-window default.
    assert section.section_token_threshold("gpt-5.4", backend="claude-api") == 630_000


def test_run_sections_openai_doc_that_claude_would_extract_whole(tmp_path, monkeypatch):
    # A document that fits a big input window comfortably (extracted whole on a paginating backend)
    # must section on openai, whose fixed output ceiling it would otherwise overrun (#343).
    monkeypatch.setattr(section, "_config_get", lambda k, d: d)   # no config override
    vault = _vault(tmp_path)
    pages = [{"page": n, "markdown": "x" * 400} for n in range(1, 601)]   # 60_000 est tokens
    _write_queue(vault, "doc1", pages, 600)
    # 60K tokens < gpt-5.4's 630K input threshold, but > its 50K output-capped threshold.
    assert section.run(vault, "doc1", model="gpt-5.4", backend="claude-api")["sectioned"] is False
    assert section.run(vault, "doc1", model="gpt-5.4", backend="openai")["sectioned"] is True


def test_run_effort_flows_through_to_the_output_capped_threshold(tmp_path, monkeypatch):
    # The wire ceiling itself is the same regardless of effort (#598), but `effort` still selects
    # which row of the output-density fit that SAME ceiling is inverted against, so high effort's
    # steep marginal rate still yields a much smaller input threshold than low's.
    monkeypatch.setattr(section, "_config_get", lambda k, d: d)   # no config override
    vault = _vault(tmp_path)
    pages = [{"page": n, "markdown": "x" * 400} for n in range(1, 301)]   # 30_000 est tokens
    _write_queue(vault, "doc1", pages, 300)
    # 30K tokens <= low's 50,000 (clamped) threshold, but > high's ~20,980: at high effort the fit
    # predicts ~2.5 output tokens per input token, so the same ceiling buys far less input. Both
    # thresholds here are real inverted values — deliberately NOT `_MIN_OUTPUT_CAPPED_BUDGET`,
    # which would mean the ceiling couldn't even cover the fit's fixed cost (see
    # test_uncatalogued_reasoning_model_budget_does_not_collapse_to_the_floor).
    assert section.run(vault, "doc1", model="gpt-5-mini", backend="openai", effort="low")["sectioned"] is False
    assert section.run(vault, "doc1", model="gpt-5-mini", backend="openai", effort="high")["sectioned"] is True


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

