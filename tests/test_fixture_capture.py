"""Tests for fixture_capture — the opt-in hook that snapshots real model responses hitting
specific conditions during a benchmark run (#352)."""

import json

import pytest

from watchdog import fixture_capture as fc


@pytest.fixture(autouse=True)
def reset_capture_state():
    """`fixture_capture` holds module-level state (`_capture_dir`) — make sure a test that
    enables it never leaks that into a later, unrelated test."""
    fc.disable()
    yield
    fc.disable()


def test_disabled_by_default():
    assert not fc.enabled()


def test_capture_is_noop_when_disabled(tmp_path):
    fc.capture("truncation", backend="openai", model_id="gpt-5-mini", text="partial")
    assert list(tmp_path.iterdir()) == []


def test_enable_creates_directory_and_capture_writes_a_file(tmp_path):
    target = tmp_path / "captures"
    fc.enable(target)
    assert fc.enabled()
    assert target.is_dir()

    fc.capture("truncation", backend="openai", model_id="gpt-5-mini", task="extract",
              text="partial output", usage={"input_tokens": 10})

    files = list(target.glob("*.json"))
    assert len(files) == 1
    record = json.loads(files[0].read_text(encoding="utf-8"))
    assert record["condition"] == "truncation"
    assert record["backend"] == "openai"
    assert record["model_id"] == "gpt-5-mini"
    assert record["task"] == "extract"
    assert record["text"] == "partial output"
    assert record["usage"] == {"input_tokens": 10}


def test_disable_stops_further_capture(tmp_path):
    fc.enable(tmp_path)
    fc.disable()
    assert not fc.enabled()
    fc.capture("malformed_json", backend="gemini", model_id="gemini-flash", text="not json")
    assert list(tmp_path.glob("*.json")) == []


def test_model_id_with_slash_is_sanitized_for_filename(tmp_path):
    # openrouter model ids are namespaced ("org/model") — must not be interpreted as a path.
    fc.enable(tmp_path)
    fc.capture("malformed_json", backend="openrouter", model_id="meta-llama/llama-3", text="x")
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    assert "/" not in files[0].name


def test_each_capture_gets_a_distinct_file(tmp_path):
    fc.enable(tmp_path)
    fc.capture("schema_drift", backend="openai", model_id="gpt-5-mini", removed=["x"])
    fc.capture("schema_drift", backend="openai", model_id="gpt-5-mini", removed=["y"])
    assert len(list(tmp_path.glob("*.json"))) == 2
