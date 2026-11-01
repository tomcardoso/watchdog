"""Long-prompt rate cards (D365): a request over a model's prompt-length line is billed in full at
the upper card, by each provider's own definition of prompt length; the estimates price planned
calls that will cross it the same way; stored history is repriced by a migration. No model is
called."""

import json
import sys
from pathlib import Path

import pytest

from watchdog import model_catalog, model_client as mc
from watchdog.pipeline import ingest_setup, migrations

BENCH = Path(__file__).resolve().parent.parent / "benchmarks"


@pytest.fixture(autouse=True)
def flat_clock(monkeypatch):
    monkeypatch.setattr(model_catalog, "price_multiplier", lambda *_a, **_k: 1.0)
    monkeypatch.setattr(mc, "price_multiplier", lambda *_a, **_k: 1.0)


# ── Claude Haiku 5.5: $0.10 / $0.50 up to 100,000 prompt tokens, $0.50 / $2.50 over ──────────

def test_the_2026_10_31_call_is_billed_entirely_at_the_upper_card():
    """101,113 uncached + 12,330 cache-write = 113,443 prompt tokens: over the line, so every
    column moves to the upper card — about five times what was recorded."""
    usage = {"input_tokens": 101_113, "cache_creation_input_tokens": 12_330, "output_tokens": 65_099}
    expected = 101_113 * 0.50e-6 + 12_330 * 0.625e-6 + 65_099 * 2.50e-6
    assert mc._api_cost("claude-haiku-5-5", usage) == pytest.approx(expected)
    assert expected == pytest.approx(0.22101025)
    lower = 101_113 * 0.10e-6 + 12_330 * 0.125e-6 + 65_099 * 0.50e-6
    assert expected == pytest.approx(5 * lower)


def test_a_prompt_exactly_at_the_line_stays_on_the_lower_card():
    at = {"input_tokens": 90_000, "cache_read_input_tokens": 10_000, "output_tokens": 1_000}
    assert mc._api_cost("claude-haiku-5-5", at) == pytest.approx(
        90_000 * 0.10e-6 + 10_000 * 0.01e-6 + 1_000 * 0.50e-6)
    over = {**at, "input_tokens": 90_001}
    assert mc._api_cost("claude-haiku-5-5", over) == pytest.approx(
        90_001 * 0.50e-6 + 10_000 * 0.05e-6 + 1_000 * 2.50e-6)


def test_cache_reads_alone_can_carry_a_request_over_the_line():
    """Anthropic counts cache reads in the prompt length: a request whose uncached input is tiny
    still pays the upper card when the cached prefix takes it over 100,000."""
    usage = {"input_tokens": 500, "cache_read_input_tokens": 100_000, "output_tokens": 2_000}
    assert mc._api_cost("claude-haiku-5-5", usage) == pytest.approx(
        500 * 0.50e-6 + 100_000 * 0.05e-6 + 2_000 * 2.50e-6)


def test_the_sdk_usage_object_and_its_dict_price_the_same():
    class Usage:
        input_tokens = 101_113
        cache_creation_input_tokens = 12_330
        cache_read_input_tokens = 0
        output_tokens = 65_099
        cache_creation = None
    as_dict = {"input_tokens": 101_113, "cache_creation_input_tokens": 12_330,
               "cache_read_input_tokens": 0, "output_tokens": 65_099}
    assert mc._api_cost("claude-haiku-5-5", Usage()) == pytest.approx(mc._api_cost("claude-haiku-5-5", as_dict))


def test_one_hour_cache_writes_cost_twice_the_input_rate_of_the_card():
    usage = {"input_tokens": 1_000, "cache_creation_input_tokens": 3_000, "output_tokens": 0,
             "cache_creation": {"ephemeral_1h_input_tokens": 2_000, "ephemeral_5m_input_tokens": 1_000}}
    assert mc._api_cost("claude-haiku-5-5", usage) == pytest.approx(
        1_000 * 0.10e-6 + 1_000 * 0.125e-6 + 2_000 * 0.20e-6)


def test_a_batch_call_is_half_of_whichever_card_applies():
    long = {"input_tokens": 150_000, "output_tokens": 10_000}
    short = {"input_tokens": 50_000, "output_tokens": 10_000}
    assert mc._batch_cost("claude-haiku-5-5", long) == pytest.approx(150_000 * 0.25e-6 + 10_000 * 1.25e-6)
    assert mc._batch_cost("claude-haiku-5-5", short) == pytest.approx(50_000 * 0.05e-6 + 10_000 * 0.25e-6)


def test_a_flat_priced_claude_model_never_changes_card():
    usage = {"input_tokens": 150_000, "output_tokens": 1_000}
    assert mc._api_cost("claude-sonnet-5-5", usage) == pytest.approx(150_000 * 2.0e-6 + 1_000 * 10.0e-6)


# ── OpenAI: 2x input and cache, 1.5x output over 272,000 prompt tokens ───────────────────────

@pytest.mark.parametrize("model_id, low, high", [
    ("gpt-6-luna", (0.10e-6, 0.50e-6, 0.01e-6, 0.125e-6), (0.20e-6, 0.75e-6, 0.02e-6, 0.25e-6)),
    ("gpt-5.6-luna", (0.20e-6, 1.20e-6, 0.02e-6, 0.25e-6), (0.40e-6, 1.80e-6, 0.04e-6, 0.50e-6)),
])
def test_openai_prompt_tokens_choose_the_card(model_id, low, high):
    def usage(prompt):
        return {"prompt_tokens": prompt, "completion_tokens": 10_000,
                "prompt_tokens_details": {"cached_tokens": 200_000, "cache_write_tokens": 20_000}}

    def priced(rates, prompt):
        inp, out, cached, write = rates
        return (prompt - 220_000) * inp + 200_000 * cached + 20_000 * write + 10_000 * out
    # `prompt_tokens` already includes the cached part, so it alone decides.
    assert mc._openai_cost(model_id, usage(272_000)) == pytest.approx(priced(low, 272_000))
    assert mc._openai_cost(model_id, usage(272_001)) == pytest.approx(priced(high, 272_001))
    assert mc._openai_batch_cost(model_id, usage(300_000)) == pytest.approx(0.5 * priced(high, 300_000))


def test_gemini_and_deepseek_price_flat_at_every_length():
    for model_id in ("gemini-3.8-flash", "gemini-3.5-flash-lite", "deepseek-flash", "deepseek-v4-pro"):
        assert model_catalog.long_context_card(model_id) is None
        small = mc._openai_cost(model_id, {"prompt_tokens": 1_000, "completion_tokens": 100})
        big = mc._openai_cost(model_id, {"prompt_tokens": 500_000, "completion_tokens": 50_000})
        assert big == pytest.approx(500 * small)


def test_a_threshold_without_a_card_is_a_catalog_error():
    assert set(model_catalog._LONG_CONTEXT_PRICING) == {"claude-haiku-5-5", "gpt-6-luna", "gpt-5.6-luna"}
    for model_id, (threshold, _card) in model_catalog._LONG_CONTEXT_PRICING.items():
        assert model_catalog.catalog_long_context_threshold(model_id) == threshold


# ── stored history ────────────────────────────────────────────────────────────────────────

def test_repricing_a_record_judges_each_attempt_and_halves_batch():
    rec = {"model": "claude-haiku-5-5", "backend": "claude-api", "auth_mode": "api-key",
           "input_tokens": 101_113, "cache_write_tokens": 12_330, "cache_read_tokens": 0,
           "output_tokens": 65_099, "attempts": 1, "cost_usd": 0.04420075}
    assert mc.reprice_long_prompt_record(rec) == pytest.approx(0.22101025)
    # Two attempts of 60,000 each never crossed the line, though the sum would.
    assert mc.reprice_long_prompt_record({**rec, "input_tokens": 120_000, "cache_write_tokens": 0,
                                          "attempts": 2}) is None
    assert mc.reprice_long_prompt_record({**rec, "backend": "claude-batch"}) == pytest.approx(0.22101025 / 2)
    assert mc.reprice_long_prompt_record({**rec, "backend": "claude-agent-sdk"}) is None
    assert mc.reprice_long_prompt_record({**rec, "model": "claude-sonnet-5-5"}) is None
    openai = {"model": "gpt-6-luna", "backend": "openai", "input_tokens": 300_000,
              "cache_read_tokens": 0, "cache_write_tokens": 0, "output_tokens": 1_000}
    assert mc.reprice_long_prompt_record(openai) == pytest.approx(300_000 * 0.20e-6 + 1_000 * 0.75e-6)


def test_the_migration_reprices_once_and_keeps_what_was_recorded(tmp_path):
    vault = tmp_path / "v"
    usage = vault / ".watchdog" / "registry" / "usage"
    usage.mkdir(parents=True)
    long = {"task": "extract", "model": "claude-haiku-5-5", "backend": "claude-api",
            "input_tokens": 101_113, "cache_write_tokens": 12_330, "cache_read_tokens": 0,
            "output_tokens": 65_099, "attempts": 1, "cost_usd": 0.04420075}
    short = {**long, "input_tokens": 50_000, "cache_write_tokens": 0, "cost_usd": 0.0375495}
    f = usage / "usage-20261031T120000Z.json"
    f.write_text(json.dumps({"calls": [long, short], "totals": {"cost_usd": 0.08175025}}))
    ctx = migrations.Context(vault, [], {}, None, lambda m: None, lambda m: None)
    assert migrations._long_prompt_estimate(ctx)["items"] == 1
    import asyncio
    out = asyncio.run(migrations._long_prompt_apply(ctx))
    assert out["calls"] == 1 and "$0.04 as first recorded, $0.22 now" in out["summary"]
    data = json.loads(f.read_text())
    assert data["calls"][0]["cost_usd"] == pytest.approx(0.22101, abs=1e-6)
    assert data["calls"][0]["cost_usd_recorded"] == 0.04420075
    assert "cost_usd_recorded" not in data["calls"][1]
    assert data["totals"]["cost_usd"] == pytest.approx(0.22101 + 0.0375495, abs=1e-6)
    assert migrations._long_prompt_estimate(ctx)["items"] == 0          # done: nothing again
    asyncio.run(migrations._long_prompt_apply(ctx))
    assert json.loads(f.read_text()) == data


# ── estimates ─────────────────────────────────────────────────────────────────────────────

def _queue(est_tokens, pages):
    return [{"sha256": "x", "filename": "x.pdf", "est_tokens": est_tokens, "page_count": pages}]


def test_a_document_whose_planned_call_crosses_the_line_is_counted(tmp_path, monkeypatch):
    """A 70,000-est-token document goes to Haiku 5.5 as one call (under the sectioning threshold),
    but with its prompt overhead the call is over 100,000 real tokens."""
    monkeypatch.setattr(mc, "tokenizer_ratio", lambda *a, **k: 1.21)
    plan = ingest_setup.long_prompt_plan(_queue(70_000, 70), "haiku", "claude-api", tmp_path)
    assert plan["calls"] == 1 and plan["documents"] == 1 and plan["share"] == pytest.approx(1.0)
    small = ingest_setup.long_prompt_plan(_queue(5_000, 5), "haiku", "claude-api", tmp_path)
    assert small["calls"] == 0 and small["share"] == 0.0
    flat = ingest_setup.long_prompt_plan(_queue(70_000, 70), "sonnet", "claude-api", tmp_path)
    assert flat == {"share": 0.0, "calls": 0, "documents": 0, "threshold": None}
    sub = ingest_setup.long_prompt_plan(_queue(70_000, 70), "haiku", "claude-agent-sdk", tmp_path)
    assert sub["calls"] == 0


def test_the_history_estimate_scales_the_crossing_share(tmp_path, monkeypatch):
    monkeypatch.setattr(mc, "tokenizer_ratio", lambda *a, **k: 1.21)
    usage = tmp_path / ".watchdog" / "registry" / "usage"
    usage.mkdir(parents=True)
    (usage / "usage-1.json").write_text(json.dumps({"calls": [], "totals": {
        "input_tokens": 1_000_000, "cache_read_tokens": 0, "cache_write_tokens": 0,
        "output_tokens": 240_000, "cost_usd": 0.22}}))
    flat = ingest_setup.cost_estimate(tmp_path, _queue(70_000, 70), "claude-api")
    tiered = ingest_setup.cost_estimate(tmp_path, _queue(70_000, 70), "claude-api", model="haiku")
    assert tiered["long_prompt_calls"] == 1 and tiered["long_prompt_documents"] == 1
    assert tiered["cost_low"] == pytest.approx(5 * flat["cost_low"])     # every column 5x on Haiku


def test_the_catalog_projection_prices_each_model_at_its_own_card(tmp_path, monkeypatch):
    monkeypatch.setattr(mc, "tokenizer_ratio", lambda *a, **k: 1.21)
    rows = {r["id"]: r for r in ingest_setup._catalog_cost_projection(
        100_000, 0.25, _queue(70_000, 70), tmp_path)}
    haiku = rows["claude-haiku-5-5"]
    assert haiku["long_prompt_calls"] == 1
    assert haiku["cost"] == pytest.approx(100_000 * 1.21 * (0.50e-6 + 0.25 * 2.50e-6))
    assert rows["claude-sonnet-5-5"]["long_prompt_calls"] == 0
    no_queue = {r["id"]: r for r in ingest_setup._catalog_cost_projection(100_000, 0.25)}
    assert no_queue["claude-haiku-5-5"]["cost"] == pytest.approx(100_000 * 1.21 * (0.10e-6 + 0.25 * 0.50e-6))


def test_list_price_and_the_fallback_estimate_take_a_tier_alias(monkeypatch):
    monkeypatch.syspath_prepend(str(BENCH))
    import cost_reference as cr
    est = cr.fallback_estimate(1_000_000, "haiku-5.5", "medium")
    assert est["cost_low"] == pytest.approx(1_000_000 * 0.10e-6 + 240_000 * 0.50e-6)
    long = cr.fallback_estimate(1_000_000, "claude-haiku-5-5", "medium", long_share=1.0)
    assert long["cost_low"] == pytest.approx(5 * est["cost_low"])


def test_the_benchmark_preview_produces_a_figure_for_a_tier_alias(tmp_path, monkeypatch):
    """`_resolve` hands back `(None, 'haiku-5.5')`; the preview used to look that alias up in the
    catalog and print "no dollar estimate" for every arm."""
    monkeypatch.syspath_prepend(str(BENCH))
    import run_benchmark as rb
    monkeypatch.setattr(rb, "arm_backend", lambda *a, **k: "claude-api")
    monkeypatch.setattr(mc, "tokenizer_ratio", lambda *a, **k: 1.21)
    queue = tmp_path / ".watchdog" / "queue"
    queue.mkdir(parents=True)
    (queue / "abc.json").write_text(json.dumps({"filename": "a.pdf", "page_count": 1,
                                               "pages": [{"markdown": "x" * 40_000}]}))
    assert rb._model_id("haiku-5.5", default="sonnet") == "claude-haiku-5-5"
    est = rb.preview_extractor_arm(tmp_path, "haiku-5.5", "medium", None)
    assert est["cost_low"] is not None and est["cost_low"] > 0 and est.get("projected")
    sys.modules.pop("run_benchmark", None)
