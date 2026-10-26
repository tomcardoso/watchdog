"""Spending limits per stored key (`watchdog/key_spend.py`, D347): the spend log, the stop a run
makes once a key it pays with reaches its limit, and the stop before a run starts."""

import json

import pytest

from watchdog import auth, config, key_spend
from watchdog.pipeline import orchestrate

from tests.keys_support import stored


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HOME", tmp_path / "home")
    for p in auth.PROVIDERS.values():
        monkeypatch.delenv(p["env"], raising=False)
    (tmp_path / "home").mkdir()
    auth.save_state({"mode": "api-key", "keys": stored(openai="sk-proj-abcdefghijklmnop")})
    return tmp_path / "home"


def test_the_log_counts_only_stored_keys_and_real_charges(home):
    key_spend.record("openai", "Default", 1.25, auth_mode="api-key")
    key_spend.record("openai", "Default", 0, auth_mode="api-key")            # nothing charged
    key_spend.record("openai", "Nobody", 4.0, auth_mode="api-key")           # no such key
    key_spend.record("anthropic", None, 2.0, auth_mode="subscription")       # the subscription
    rows = [json.loads(line) for line in key_spend.ledger_path().read_text().splitlines()]
    assert [(r["key"], r["usd"]) for r in rows] == [("openai:default", 1.25)]
    assert key_spend.spent("openai:default") == 1.25


def test_a_run_stops_once_a_key_it_paid_with_reaches_its_limit(home, monkeypatch):
    key_spend.set_limit("openai:default", 2)
    monkeypatch.setattr(orchestrate._run, "usage",
                        [{"backend": "openai", "key_label": "Default", "auth_mode": "api-key"}])
    assert orchestrate._key_limit_stop() is None
    key_spend.record("openai", "Default", 2.0, auth_mode="api-key")
    msg = orchestrate._key_limit_stop()
    assert msg and "OpenAI key “Default” has reached its spending limit" in msg


def test_a_run_doesnt_start_on_a_key_already_over_its_limit(home, tmp_path):
    from watchdog.ops import ingest
    vault = tmp_path / "case"
    (vault / ".watchdog").mkdir(parents=True)
    ingest._require_investigation_keys(vault, ["openai"])        # no limit: goes ahead
    key_spend.set_limit("openai:default", 1)
    key_spend.record("openai", "Default", 1.5, auth_mode="api-key")
    with pytest.raises(SystemExit, match="spending limit"):
        ingest._require_investigation_keys(vault, ["openai"])
