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
    # 0.6 / 0.3 of the model's context window, then divided by the model's measured
    # tokenizer_ratio (#617). Claude's 200K window gives the historical 120K/60K before that
    # division; Sonnet 4.6's measured 0.93 widens it to 129,032/64,516, since chars/4 turns out to
    # over-estimate for Claude's old tokenizer on real chewed documents.
    assert section.model_defaults("sonnet") == (int(120_000 / 0.93), int(60_000 / 0.93))
    assert section.model_defaults("sonnet") == (129_032, 64_516)
    assert section.model_defaults(None) == (129_032, 64_516)              # default tier
    assert section.model_defaults("deepseek-v4-flash") == (740_740, 370_370)   # 1M window / 0.81
    # `gpt-5-mini` is NOT in the catalog (the real ids are gpt-5.4-mini etc.), so it declares no
    # ratio and its window fractions pass through undivided — the uncorrected control.
    assert section.model_defaults("gpt-5-mini") == (240_000, 120_000)          # 400K window


def test_section_token_threshold_model_aware(monkeypatch):
    monkeypatch.setattr(section, "_config_get", lambda k, d: d)   # no config override
    assert section.section_token_threshold("sonnet") == 129_032    # 120K / 0.93 (#617)
    assert section.section_token_threshold("deepseek-v4-flash") == 740_740  # 600K / 0.81 (#617)


def test_section_token_threshold_config_override_wins(monkeypatch):
    # An explicit config value overrides the model-aware default regardless of the model.
    monkeypatch.setattr(section, "_config_get",
                        lambda k, d: 42 if k == "section_token_threshold" else d)
    assert section.section_token_threshold("deepseek-v4-flash") == 42


def test_section_token_threshold_auto_uses_model_default(monkeypatch):
    # The 'auto' sentinel (or an unset key) falls back to the model-aware default.
    monkeypatch.setattr(section, "_config_get",
                        lambda k, d: "auto" if k == "section_token_threshold" else d)
    assert section.section_token_threshold("sonnet") == 129_032    # 120K / 0.93 (#617)
    assert section.section_token_threshold("deepseek-v4-flash") == 740_740  # 600K / 0.81 (#617)


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


# ── the output ceiling no longer sizes the input (#555) ───────────────────────

def test_model_defaults_ignores_the_output_ceiling(monkeypatch):
    # The load-bearing assertion of #555: `model_defaults` must never consult
    # `output_ceiling_for_sectioning`. A backend that enforces a fixed output ceiling it can't
    # paginate past (openai/gemini) used to have its threshold/budget capped by inverting an
    # affine output-density fit against that ceiling (#343, #542); across 673 archived extraction
    # calls that cap never bound — peak output was 14-27% of the envelope on every model but
    # gpt-5.4-mini at high effort — while the pooled fit behind it let one model's reasoning
    # volume set every other model's budget. Both the fit and the ceiling term are gone.
    #
    # Monkeypatching the lookup to explode is what makes this mutation-resistant: reinstating any
    # ceiling term, however it is clamped, turns this red rather than merely changing a number.
    import watchdog.model_client as mc

    def boom(backend, model):
        raise AssertionError("model_defaults must not consult the output ceiling (#555)")

    monkeypatch.setattr(mc, "output_ceiling_for_sectioning", boom)
    # Pure input-window defaults on the very backends that used to be capped. Both models price
    # flat, so the surviving long-context clamp doesn't confound the assertion.
    assert section.model_defaults("gpt-5.4-mini", backend="openai") == (300_000, 150_000)
    assert section.model_defaults("gemini-3.5-flash", backend="gemini") == (659_340, 329_670)


def test_model_defaults_clamped_below_a_long_context_pricing_tier():
    # #555/D199: the one clamp that survives, and the only one whose bound is a published number
    # rather than a fitted one. gpt-5.4 and friends roughly double their rate above 272,000 real
    # input tokens; 10% headroom puts the cap at 244,800 real, which at their measured 0.80
    # tokenizer_ratio is 306,000 est. Without it the window fraction alone would plan 393,750 est
    # = 315,000 real, past the boundary.
    for model in ("gpt-5.4", "gpt-5.5", "gpt-5.6-luna", "gpt-5.6-terra"):
        threshold, budget = section.model_defaults(model, backend="openai")
        assert (threshold, budget) == (306_000, 306_000), model
    # Gemini 3.1 Pro carries its own, lower boundary (200,000) at its own 0.91 ratio.
    assert section.model_defaults("gemini-3.1-pro-preview", backend="gemini") == (197_802, 197_802)


def test_long_context_clamp_keeps_real_tokens_under_the_boundary():
    # The assertion that actually matters is in REAL tokens, since that is what a provider meters:
    # budget x tokenizer_ratio must land under the catalogued boundary for every tiered model, and
    # the headroom must be real slack rather than a rounding artifact. Mutating the clamp to apply
    # after the ratio division turns this red on exactly the sub-1.0 ratios that make it wrong.
    import watchdog.model_client as mc
    from watchdog.model_catalog import catalog_long_context_threshold
    tiered = [("openai", m) for m in ("gpt-5.4", "gpt-5.5", "gpt-5.6-luna", "gpt-5.6-terra")]
    tiered.append(("gemini", "gemini-3.1-pro-preview"))
    for backend, model in tiered:
        boundary = catalog_long_context_threshold(model)
        assert boundary, f"{model} lost its catalogued boundary"
        _, budget = section.model_defaults(model, backend=backend)
        real = budget * mc.tokenizer_ratio(model, backend, None)
        assert real < boundary, f"{model} plans {real:,.0f} real tokens against a {boundary:,} tier"
        assert real >= boundary * 0.85, f"{model} gave up more headroom than intended"


def test_model_defaults_unclamped_for_flat_priced_models():
    # A model with no catalogued boundary keeps the pure window-derived default — the clamp must
    # not leak onto models that price flat at every length, including the ones sharing a family
    # (and a tokenizer) with a tiered one. gpt-5.4-mini shares gpt-5.4's 0.80 ratio but has a 400K
    # window, so it could never reach the boundary and must not be shrunk toward it.
    assert section.model_defaults("gpt-5.4-mini", backend="openai") == (300_000, 150_000)
    assert section.model_defaults("gemini-3.5-flash", backend="gemini") == (659_340, 329_670)
    assert section.model_defaults("deepseek-v4-flash", backend="deepseek") == (740_740, 370_370)
    assert section.model_defaults("sonnet") == (129_032, 64_516)


def test_long_context_clamp_binds_the_threshold_not_just_the_budget():
    # The threshold decides whether a document is sectioned AT ALL, so an unclamped threshold would
    # send a document larger than the boundary in ONE whole-document call — the larger exposure,
    # since only big documents reach a pricing tier in the first place. A 350,000 est-token
    # document (280,000 real) sits under gpt-5.4's unclamped 787,500 threshold but over its
    # clamped 306,000 one.
    threshold, _ = section.model_defaults("gpt-5.4", backend="openai")
    assert 306_000 == threshold < 787_500
    assert 350_000 > threshold


def test_model_defaults_identical_across_every_backend():
    # One model, one pair of numbers, whatever backend routes it (#555). Previously the same model
    # returned 50_000 on openai and 787_500 on claude-api — a 15x swing driven entirely by whether
    # the backend happened to paginate its output, which is what produced #555's 58x catalogue
    # spread. Both surviving terms (context window, pricing boundary) are properties of the model
    # id, so the backend cannot move either.
    for model, expected in (("gpt-5.4", (306_000, 306_000)),          # tiered
                            ("gpt-5.4-mini", (300_000, 150_000))):    # flat-priced
        for backend in ("openai", "claude-api", "claude-agent-sdk", "deepseek", None):
            assert section.model_defaults(model, backend=backend) == expected, (model, backend)


def test_model_defaults_track_only_window_and_tokenizer_ratio(monkeypatch):
    # The whole formula, stated as a test: window x fraction / ratio, nothing else. Both inputs are
    # catalogued per model, so a catalogue edit is the only thing that can move a budget.
    import watchdog.model_client as mc
    monkeypatch.setattr(mc, "context_window", lambda model, backend=None: 300_000)
    monkeypatch.setattr(mc, "tokenizer_ratio", lambda model, backend=None, vault=None: 1.5)
    assert section.model_defaults("whatever") == (int(180_000 / 1.5), int(90_000 / 1.5))


def test_model_defaults_budget_never_collapses_to_zero(monkeypatch):
    # A pathologically small window must still yield a usable budget rather than 0, which would
    # make `plan_ranges` emit one section per page. The `max(1, ...)` floor is the only guard left
    # now that `_MIN_OUTPUT_CAPPED_BUDGET` is gone.
    import watchdog.model_client as mc
    monkeypatch.setattr(mc, "context_window", lambda model, backend=None: 2)
    _, budget = section.model_defaults("tiny")
    assert budget == 1


def test_section_token_threshold_identical_across_backends(monkeypatch):
    monkeypatch.setattr(section, "_config_get", lambda k, d: d)   # no config override
    # The same on the backend that enforces an output ceiling as on the one that paginates past it
    # (#555) — for a tiered model, where the pricing clamp binds, and a flat-priced one, where the
    # 1.05M/400K window fraction over the measured 0.80 ratio does.
    assert section.section_token_threshold("gpt-5.4", backend="openai") == 306_000
    assert section.section_token_threshold("gpt-5.4", backend="claude-api") == 306_000
    assert section.section_token_threshold("gpt-5.4-mini", backend="openai") == 300_000
    assert section.section_token_threshold("gpt-5.4-mini", backend="claude-api") == 300_000


def test_run_sectioning_decision_does_not_depend_on_backend(tmp_path, monkeypatch):
    # The behavioural counterpart: a 60K-token document that a paginating backend extracts whole
    # is now also extracted whole on openai. Before #555 the same document sectioned on openai
    # alone, because its output-derived threshold was 50_000 against a 630K input threshold.
    monkeypatch.setattr(section, "_config_get", lambda k, d: d)   # no config override
    vault = _vault(tmp_path)
    pages = [{"page": n, "markdown": "x" * 400} for n in range(1, 601)]   # 60_000 est tokens
    _write_queue(vault, "doc1", pages, 600)
    assert section.run(vault, "doc1", model="gpt-5.4", backend="claude-api")["sectioned"] is False
    assert section.run(vault, "doc1", model="gpt-5.4", backend="openai")["sectioned"] is False
    # Still sections when the document genuinely exceeds the window-derived threshold.
    big = [{"page": n, "markdown": "x" * 400} for n in range(1, 9_001)]   # 900_000 est tokens
    _write_queue(vault, "doc2", big, 9_000)
    assert section.run(vault, "doc2", model="gpt-5.4", backend="openai")["sectioned"] is True


def test_run_no_longer_accepts_an_effort_argument(tmp_path):
    # `effort` is gone from the sectioning path entirely (#555): it selected a row of the output-
    # density fit, and with that fit deleted there is nothing for it to select. Kept as an explicit
    # test because a caller still passing it would otherwise fail far from here.
    import pytest
    vault = _vault(tmp_path)
    _write_queue(vault, "doc1", [{"page": 1, "markdown": "x" * 400}], 1)
    with pytest.raises(TypeError):
        section.run(vault, "doc1", model="gpt-5.4", backend="openai", effort="high")
    with pytest.raises(TypeError):
        section.model_defaults("gpt-5.4", backend="openai", effort="high")


# ── tokenizer-aware thresholds (#574) ────────────────────────────────────────

def test_model_defaults_shrinks_for_new_tokenizer_claude_model():
    # Sonnet 5 / Opus 4.8 share a newer tokenizer that produces more real tokens for the same text
    # than the chars/4 est_tokens heuristic assumes — measured at 1.28 against corpus-v1 (#617),
    # replacing #574's quoted 1.3. Threshold/budget shrink by that ratio so the real tokens a call
    # sends still respect the 200K context window.
    assert section.model_defaults("sonnet-5") == (int(120_000 / 1.28), int(60_000 / 1.28))
    assert section.model_defaults("opus") == (int(120_000 / 1.28), int(60_000 / 1.28))


def test_model_defaults_widen_for_over_estimating_tokenizers():
    # The correction runs both ways (#617), and widening is the COMMON case: every catalogued
    # tokenizer except Claude 4.7+ measures below 1.0, meaning chars/4 over-estimates it, so its
    # budget widens rather than shrinks. Safe because _THRESHOLD_FRACTION leaves 40% of the window
    # unused regardless.
    assert section.model_defaults("sonnet") == (int(120_000 / 0.93), int(60_000 / 0.93))
    assert section.model_defaults("haiku") == (int(120_000 / 0.93), int(60_000 / 0.93))
    assert section.model_defaults("deepseek-v4-flash") == (740_740, 370_370)     # 0.81
    # gpt-5.4-mini rather than gpt-5.4: same 0.80 ratio, but no pricing boundary to clamp against,
    # so the widening is visible rather than masked by the clamp. 400K window: 240_000/0.80.
    assert section.model_defaults("gpt-5.4-mini", backend="claude-api") == (300_000, 150_000)
    # Shrinking is now the exception — only the Claude 4.7+ tokenizer, at 1.28.
    assert section.model_defaults("sonnet-5") == (int(120_000 / 1.28), int(60_000 / 1.28))
    # An uncatalogued id still declares nothing and is the uncorrected control.
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

