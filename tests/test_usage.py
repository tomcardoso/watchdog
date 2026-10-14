"""The usage analysis Activity → Usage reads (`watchdog.usage`, #319): stage grouping, wall-clock
and peak concurrency, backends and auth, the subscription caveat, batch lifecycle notes, cost per
page and totals."""

import json

from watchdog import usage


def _call(task="extract", filename="doc.pdf", detail="pages 1–1", model="claude-sonnet-5-5",
          cost_usd=0.01, input_tokens=100, output_tokens=20, latency_s=1.5,
          effort=None, auth_mode="api-key", attempts=1, end_ts=None, api_ms=None, num_turns=None,
          failed=False, backend="claude-api", batch_id=None, batch_submitted_at=None,
          batch_ended_at=None, batch_collected_at=None):
    call = {
        "task": task, "model": model, "backend": backend,
        "input_tokens": input_tokens, "output_tokens": output_tokens,
        "cache_read_tokens": 0, "cache_write_tokens": 0,
        "cost_usd": cost_usd, "attempts": attempts, "latency_s": latency_s, "effort": effort,
        "auth_mode": auth_mode, "filename": filename, "detail": detail, "end_ts": end_ts,
    }
    # api_ms/num_turns (#402) are only present on claude-agent-sdk records — omitted here by
    # default so a plain _call() matches a real raw-API record with no such keys at all.
    if api_ms is not None:
        call["api_ms"] = api_ms
    if num_turns is not None:
        call["num_turns"] = num_turns
    if failed:
        call["failed"] = True
    # batch_* fields are only present on claude-batch-collected records — same "omitted by
    # default" convention as api_ms/num_turns above.
    if batch_id is not None:
        call["batch_id"] = batch_id
        call["batch_submitted_at"] = batch_submitted_at
        call["batch_ended_at"] = batch_ended_at
        call["batch_collected_at"] = batch_collected_at
    return call


def test_stage_buckets_put_each_task_where_its_run_reads_it():
    assert usage.STAGE["classify"] == "classifier"
    assert usage.STAGE["verify"] == "extractor"            # same model, same document, same stage
    assert usage.STAGE["reconcile"] == "finalizer" and usage.STAGE["relationship-labels"] == "finalizer"
    assert usage.STAGE["ask"] == "ask" and usage.STAGE["research"] == "research"


def test_wall_span_is_the_real_elapsed_time_for_concurrent_calls():
    calls = [_call(latency_s=10, end_ts=110), _call(latency_s=10, end_ts=112), _call(latency_s=4, end_ts=104)]
    assert usage.wall_span(calls) == 12            # 100 → 112, not the summed 24s


def test_wall_span_is_none_without_end_times():
    assert usage.wall_span([_call(), _call()]) is None


def test_peak_concurrency_counts_overlap_not_calls():
    # Four calls, two at a time.
    calls = [_call(latency_s=10, end_ts=10), _call(latency_s=10, end_ts=10),
             _call(latency_s=10, end_ts=20), _call(latency_s=10, end_ts=20)]
    assert usage.peak_concurrency(calls) == 2
    assert usage.peak_concurrency([_call(), _call()]) == 1           # no end_ts: nothing knowable


def test_stage_models_names_each_model_once():
    calls = [_call(model="claude-sonnet-5-5"), _call(model="claude-sonnet-5-5"), _call(model="gpt-5-mini")]
    names = usage.stage_models(calls).split(", ")
    assert len(names) == 2


def test_backends_name_auth_only_for_claude():
    calls = [_call(backend="claude-agent-sdk", auth_mode="subscription"), _call(backend="deepseek", auth_mode=None)]
    assert usage.stage_backends(calls) == "claude-agent-sdk (subscription), deepseek"
    assert usage.run_backends(calls) == "sdk/sub, deepseek"
    assert usage.run_backends([]) == "—"


def test_subscription_costs_are_flagged_as_list_price():
    assert "list-price" in usage.subscription_note([_call(backend="claude-agent-sdk", auth_mode="subscription")])
    assert usage.subscription_note([_call(backend="claude-api", auth_mode="api-key")]) is None
    assert usage.subscription_note([_call(backend="openai", auth_mode="subscription")]) is None


def test_batch_lifecycle_note_spans_processing_and_idle_time():
    note = usage.batch_lifecycle_note(_call(
        backend="claude-batch", batch_id="msgbatch_1", batch_submitted_at="2026-01-01T00:00:00Z",
        batch_ended_at="2026-01-01T00:30:00Z", batch_collected_at="2026-01-01T05:30:00Z"))
    assert "msgbatch_1" in note and "processed 30.0m" in note and "idle 300.0m" in note


def test_pages_in_detail_reads_page_ranges_only():
    assert usage.pages_in_detail("pages 1–36") == 36
    assert usage.pages_in_detail("pages 15–34") == 20
    assert usage.pages_in_detail("digest") is None and usage.pages_in_detail(None) is None


def test_corpus_pages_reads_the_extracted_artifacts(tmp_path):
    extracted = tmp_path / ".watchdog" / "extracted"
    extracted.mkdir(parents=True)
    (extracted / "a.json").write_text(json.dumps({"document": {"page_count": 10}}))
    (extracted / "b.json").write_text(json.dumps({"document": {"page_count": 5}}))
    assert usage.corpus_pages(tmp_path) == (15, 2)
    assert usage.corpus_pages(tmp_path / "nothing") is None


def test_run_totals_split_cost_by_stage_and_count_failed_calls():
    calls = [_call(task="classify", cost_usd=0.01), _call(task="extract", cost_usd=0.10),
             _call(task="extract", cost_usd=0.05, failed=True), _call(task="reconcile", cost_usd=0.02)]
    t = usage.run_totals(calls)
    assert round(t["cost_usd"], 4) == 0.18
    assert (round(t["classifier_cost"], 4), round(t["extractor_cost"], 4), round(t["finalizer_cost"], 4)) \
        == (0.01, 0.15, 0.02)
    assert t["input_tokens"] == 400


def test_cost_by_key_groups_calls_by_the_key_that_paid():
    calls = [dict(_call(cost_usd=1.0), key_label="Globe"), dict(_call(cost_usd=0.5), key_label="Personal"),
             dict(_call(cost_usd=2.0), key_label="Globe"), _call(cost_usd=0.0)]
    rows = usage.cost_by_key(calls)
    assert [(r["label"], r["calls"]) for r in rows] == [("Globe", 2), ("Personal", 1), (None, 1)]
