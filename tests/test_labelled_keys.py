"""Labelled API keys per provider and each investigation's choice of key (#690, D290).

Covers the storage format (old single string read as "Default", several keys, a default), the
per-investigation choice in `.watchdog/settings.json`, the rule that a missing chosen key stops
before any model call instead of billing another account, the environment override, usage
records naming the key that paid, the Agent SDK session environment, and the file's mode."""

import asyncio
import json
import os
import stat
import sys

import pytest

from watchdog import model_client as mc
from watchdog.cmd import auth, base


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "WATCHDOG_HOME", tmp_path / "home")
    monkeypatch.setattr(base, "CONFIG_FILE", tmp_path / "home" / "config.json")
    monkeypatch.setattr(base, "PROJECTS_FILE", tmp_path / "home" / "projects.json")
    for p in auth._PROVIDERS.values():
        monkeypatch.delenv(p["env"], raising=False)
    return tmp_path / "home"


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "case"
    (v / ".watchdog" / "registry").mkdir(parents=True)
    return v


def creds(home):
    return json.loads((home / "credentials.json").read_text())


def two_openai_keys():
    """A personal default key and a second, work-billed one."""
    auth._save_state({"mode": "api-key", "keys": {"openai": "sk-personal-1111111111"}})
    work = auth.add_key("openai", "Work", "sk-work-2222222222")
    return work


# ── storage ──────────────────────────────────────────────────────────────────────

def test_old_single_string_reads_as_one_key_named_default(home):
    auth._save_state({"mode": "api-key", "keys": {"openai": "sk-old-123456789012"}})
    keys = auth.list_keys("openai")
    assert [(k["id"], k["label"], k["default"]) for k in keys] == [("default", "Default", True)]
    assert "123456789012" not in keys[0]["masked"]
    assert auth.get_api_key("openai") == "sk-old-123456789012"
    assert auth.resolve_key("openai")["label"] == "Default"


def test_one_default_key_is_written_back_in_the_old_form(home):
    auth._save_state({"mode": "api-key", "keys": {}})
    auth.add_key("openai", "Default", "sk-only-1234567890")
    assert creds(home)["keys"]["openai"] == "sk-only-1234567890"     # older versions can still read it


def test_several_keys_and_choosing_the_default(home):
    work = two_openai_keys()
    stored = creds(home)["keys"]["openai"]
    assert stored["default"] == "default" and len(stored["items"]) == 2
    assert auth.get_api_key("openai") == "sk-personal-1111111111"     # first key stays the default
    auth.set_default_key("openai", work)
    assert auth.get_api_key("openai") == "sk-work-2222222222"
    assert [k["label"] for k in auth.list_keys("openai") if k["default"]] == ["Work"]


def test_first_key_becomes_default_and_delete_moves_it(home):
    a = auth.add_key("deepseek", "Personal", "sk-a-1234567890")
    b = auth.add_key("deepseek", "Globe", "sk-b-1234567890")
    assert auth.resolve_key("deepseek")["id"] == a
    auth.delete_key("deepseek", a)
    assert auth.resolve_key("deepseek") == {"key": "sk-b-1234567890", "id": b, "label": "Globe", "source": "default"}
    auth.delete_key("deepseek", b)
    assert "deepseek" not in creds(home)["keys"]


def test_labels_are_short_plain_and_unique_per_provider(home):
    two_openai_keys()
    with pytest.raises(ValueError, match="already"):
        auth.add_key("openai", "  work ", "sk-x-1234567890")
    with pytest.raises(ValueError):
        auth.add_key("openai", "", "sk-x-1234567890")
    with pytest.raises(ValueError, match="40"):
        auth.add_key("openai", "x" * 41, "sk-x-1234567890")
    with pytest.raises(ValueError, match="plain text"):
        auth.add_key("openai", "a\x00b", "sk-x-1234567890")
    auth.add_key("deepseek", "Work", "sk-x-1234567890")              # unique per provider only


def test_rename_and_replace_keep_the_id(home):
    work = two_openai_keys()
    auth.rename_key("openai", work, "Globe")
    auth.replace_key("openai", work, "sk-globe-3333333333")
    item = next(k for k in auth.list_keys("openai") if k["id"] == work)
    assert item["label"] == "Globe"
    assert auth.label_for_key("openai", "sk-globe-3333333333") == "Globe"


def test_store_default_key_replaces_the_default_secret_only(home):
    two_openai_keys()
    state = auth._load_state()
    auth.store_default_key(state, "openai", "sk-new-personal-999")
    auth._save_state(state)
    labels = {k["label"]: k for k in auth.list_keys("openai")}
    assert set(labels) == {"Default", "Work"}
    assert auth.get_api_key("openai") == "sk-new-personal-999"


def test_credentials_stay_0600_and_writes_leave_no_temp_files(home):
    two_openai_keys()
    path = home / "credentials.json"
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert [p.name for p in home.iterdir() if p.name.endswith(".tmp")] == []


# ── the investigation's choice ───────────────────────────────────────────────────

def test_no_choice_uses_the_default(home, vault):
    two_openai_keys()
    assert auth.resolve_key("openai", vault)["source"] == "default"
    assert auth.get_api_key("openai", vault) == "sk-personal-1111111111"


def test_choice_is_stored_in_the_folder_without_the_key(home, vault):
    work = two_openai_keys()
    auth.choose_key(vault, "openai", work)
    saved = json.loads((vault / ".watchdog" / "settings.json").read_text())
    assert saved == {"schema_version": 1, "keys": {"openai": {"id": work, "label": "Work"}}}
    assert "sk-work" not in (vault / ".watchdog" / "settings.json").read_text()
    assert auth.get_api_key("openai", vault) == "sk-work-2222222222"
    assert auth.get_api_key("openai") == "sk-personal-1111111111"    # other investigations unchanged
    auth.choose_key(vault, "openai", None)
    assert auth.get_api_key("openai", vault) == "sk-personal-1111111111"


def test_process_scope_applies_the_choice_to_lookups_that_name_no_vault(home, vault):
    work = two_openai_keys()
    auth.choose_key(vault, "openai", work)
    auth.use_investigation(vault)
    assert auth.get_api_key("openai") == "sk-work-2222222222"


def test_a_shared_folder_matches_a_key_of_the_same_label(home, vault):
    """On another computer the id differs; a key with the chosen label is that account."""
    (vault / ".watchdog" / "settings.json").write_text(json.dumps(
        {"schema_version": 1, "keys": {"openai": {"id": "k-elsewhere", "label": "work"}}}))
    two_openai_keys()
    assert auth.resolve_key("openai", vault)["label"] == "Work"


def test_missing_chosen_key_raises_and_names_it(home, vault):
    work = two_openai_keys()
    auth.choose_key(vault, "openai", work)
    auth.delete_key("openai", work)
    with pytest.raises(auth.KeyChoiceError) as e:
        auth.get_api_key("openai", vault)
    assert "“Work”" in str(e.value) and "Settings → Models & keys" in str(e.value)


def test_missing_chosen_key_stops_before_any_model_call(home, vault, monkeypatch):
    work = two_openai_keys()
    auth.choose_key(vault, "openai", work)
    auth.delete_key("openai", work)
    auth.use_investigation(vault)
    sent = []

    async def backend(*a, **k):
        sent.append(k)
        return {"text": "{}", "usage": {}, "cost_usd": 0.0}
    monkeypatch.setitem(mc._ABACKENDS, "openai", backend)
    with pytest.raises(mc.ModelError, match="Work"):
        asyncio.run(mc.acomplete_json(task="t", prompt="p", schema={"type": "object"},
                                      model="gpt-5.6-luna", backend="openai"))
    assert sent == []                                              # never fell back to Personal


def test_run_preflight_stops_on_a_missing_key_only_for_providers_used(home, vault):
    work = two_openai_keys()
    auth.choose_key(vault, "openai", work)
    auth.delete_key("openai", work)
    with pytest.raises(auth.KeyChoiceError):
        auth.check_run_keys(vault, ["openai"])
    auth.check_run_keys(vault, ["deepseek"])                       # not chosen: nothing to stop


def test_dig_exits_before_running_when_the_chosen_key_is_missing(home, vault, monkeypatch):
    from watchdog.ops import ingest
    work = two_openai_keys()
    auth.choose_key(vault, "openai", work)
    auth.delete_key("openai", work)
    with pytest.raises(SystemExit) as e:
        ingest._require_investigation_keys(vault, ("openai", None))
    assert "Work" in str(e.value.code)


def test_anthropic_choice_is_not_needed_on_a_subscription(home, vault):
    auth._save_state({"mode": "subscription", "keys": {}})
    (vault / ".watchdog" / "settings.json").write_text(json.dumps(
        {"keys": {"anthropic": {"id": "k-gone", "label": "Globe"}}}))
    auth.check_run_keys(vault, [None])                             # no key is sent on a subscription
    auth._save_state({"mode": "api-key", "keys": {"anthropic": "sk-ant-default-123"}})
    with pytest.raises(auth.KeyChoiceError):
        auth.check_run_keys(vault, [None])


def test_environment_variable_overrides_every_choice(home, vault, monkeypatch):
    work = two_openai_keys()
    auth.choose_key(vault, "openai", work)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env-0000")
    r = auth.resolve_key("openai", vault)
    assert r["key"] == "sk-from-env-0000" and r["source"] == "env"
    assert r["label"] == "OPENAI_API_KEY (environment)"
    auth.delete_key("openai", work)
    assert auth.get_api_key("openai", vault) == "sk-from-env-0000"  # env satisfies a missing choice


def test_key_users_names_registered_investigations(home, vault):
    work = two_openai_keys()
    auth.choose_key(vault, "openai", work)
    home.mkdir(parents=True, exist_ok=True)
    (home / "projects.json").write_text(json.dumps({"case": {"name": "Port Calder", "path": str(vault)}}))
    assert auth.key_users("openai", work) == ["Port Calder"]
    assert auth.key_users("openai", "default") == []


# ── usage names the key that paid ────────────────────────────────────────────────

def test_model_result_and_usage_record_carry_the_label(home, vault, monkeypatch):
    from watchdog import telemetry_db
    from watchdog.cmd.usage import cost_by_key
    from watchdog.pipeline import orchestrate
    work = two_openai_keys()
    auth.choose_key(vault, "openai", work)
    keys_seen = []

    async def backend(prompt, model_id, schema, api_key, max_tokens=None, effort=None, prefix=None):
        keys_seen.append(api_key)
        return {"text": '{"a": 1}', "usage": {"prompt_tokens": 10, "completion_tokens": 2}, "cost_usd": 0.5}
    monkeypatch.setitem(mc._ABACKENDS, "openai", backend)
    orchestrate._begin_usage_run(vault)
    try:
        r = asyncio.run(orchestrate._call_model(task="extract", prompt="p", schema={"type": "object"},
                                                model="gpt-5.6-luna", backend="openai", vault=vault))
        records = list(orchestrate._run.usage)
    finally:
        orchestrate._end_usage_run(vault)
    assert keys_seen == ["sk-work-2222222222"] and r.key_label == "Work"
    assert records[0]["key_label"] == "Work"
    assert "sk-work" not in json.dumps(records)
    assert cost_by_key(records) == [{"label": "Work", "cost_usd": 0.5, "calls": 1}]
    import sqlite3
    row = sqlite3.connect(telemetry_db.DB_PATH).execute("SELECT key_label FROM calls").fetchall()
    assert row == [("Work",)]


def test_telemetry_store_gains_the_column_on_an_old_database(tmp_path, monkeypatch):
    import sqlite3
    from watchdog import telemetry_db
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(telemetry_db._SCHEMA.replace(",\n    key_label TEXT", ""))
    conn.close()
    monkeypatch.setattr(telemetry_db, "DB_PATH", path)
    telemetry_db.close()
    with telemetry_db._lock:
        cols = {r[1] for r in telemetry_db._connect().execute("PRAGMA table_info(calls)")}
    telemetry_db.close()
    assert "key_label" in cols


# ── Ask Claude / Research sessions (Agent SDK) ───────────────────────────────────

def test_agent_sdk_session_gets_the_investigations_anthropic_key(home, vault):
    from watchdog.gui import chat
    auth._save_state({"mode": "api-key", "keys": {"anthropic": "sk-ant-personal-111"}})
    globe = auth.add_key("anthropic", "Globe", "sk-ant-globe-222")
    session = chat.Session(vault, "ask", None, "Ask")
    assert chat.session_auth_env(session) == {"ANTHROPIC_API_KEY": "sk-ant-personal-111"}
    auth.choose_key(vault, "anthropic", globe)
    assert chat.session_auth_env(session) == {"ANTHROPIC_API_KEY": "sk-ant-globe-222"}
    assert session.key_label == "Globe"


def test_agent_sdk_session_on_a_subscription_gets_no_key(home, vault):
    from watchdog.gui import chat
    auth._save_state({"mode": "subscription", "keys": {"anthropic": "sk-ant-personal-111"}})
    assert chat.session_auth_env(chat.Session(vault, "ask", None, "Ask")) == {}


def test_session_refuses_to_start_when_the_chosen_anthropic_key_is_missing(home, vault):
    from watchdog.gui import chat
    from watchdog.gui.rpc import RpcError
    auth._save_state({"mode": "api-key", "keys": {"anthropic": "sk-ant-personal-111"}})
    globe = auth.add_key("anthropic", "Globe", "sk-ant-globe-222")
    auth.choose_key(vault, "anthropic", globe)
    auth.delete_key("anthropic", globe)
    with pytest.raises(RpcError) as e:
        chat.ChatManager().start(vault, "ask", None, "who")
    assert e.value.code == "key_missing" and "Globe" in str(e.value)
