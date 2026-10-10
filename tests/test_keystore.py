"""Keys stored encrypted by the desktop app (D295).

The app's main process encrypts each key with Electron's `safeStorage` and hands Python the keys
it needs; Python never decrypts. These tests stand in for the main process with a fake blob (any
opaque base64 string): what matters on this side is that only the blob is written, that a key is
usable only once the app has handed it over, that a process without it stops before any model
call (I16) with a message that says what to do, and that each job is handed only its own keys."""

import asyncio
import base64
import io
import json
import os
import stat
import sys
import time

import pytest

from watchdog import keystore
from watchdog import model_client as mc
from watchdog.cmd import auth, base


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "WATCHDOG_HOME", tmp_path / "home")
    monkeypatch.setattr(base, "CONFIG_FILE", tmp_path / "home" / "config.json")
    monkeypatch.setattr(base, "PROJECTS_FILE", tmp_path / "home" / "projects.json")
    import watchdog.config
    monkeypatch.setattr(watchdog.config, "CONFIG_FILE", tmp_path / "home" / "config.json")
    monkeypatch.delenv("WATCHDOG_APP", raising=False)
    for p in auth._PROVIDERS.values():
        monkeypatch.delenv(p["env"], raising=False)
    return tmp_path / "home"


@pytest.fixture
def vault(tmp_path):
    v = tmp_path / "case"
    (v / ".watchdog" / "registry").mkdir(parents=True)
    return v


def seal(key: str) -> dict:
    """What the main process sends as `key_enc`: an opaque blob. (Reversed and base64-encoded
    here, so a test can tell it isn't the key; the real one is safeStorage ciphertext.)"""
    return {"enc": keystore.ENC_TAG, "data": base64.b64encode(key[::-1].encode()).decode()}


def raw(home) -> str:
    return (home / "credentials.json").read_text()


def app_encrypts():
    keystore.provide({}, store="encrypted", backend="keychain")


# ── writing ───────────────────────────────────────────────────────────────────────

def test_the_app_writes_only_the_encrypted_blob(home):
    from watchdog.gui.api import settings as api
    app_encrypts()
    key = "sk-ant-api03-secretsecret-WXYZ"
    api.set_anthropic_mode("api-key", key=key, key_enc=seal(key))
    api.add_key("openai", "Work", "sk-proj-work-secret-1234", key_enc=seal("sk-proj-work-secret-1234"))
    text = raw(home)
    assert key not in text and "sk-proj-work-secret-1234" not in text
    data = json.loads(text)
    blob = data["keys"]["anthropic"]
    assert blob["enc"] == keystore.ENC_TAG and blob["masked"] == "sk-ant-api…WXYZ"
    assert data["keys"]["openai"]["items"][0]["key"]["enc"] == keystore.ENC_TAG   # a named key: item form
    # This process may use them at once: the app sent the key with its blob.
    assert auth.get_api_key("anthropic") == key
    assert auth.get_api_key("openai") == "sk-proj-work-secret-1234"
    assert stat.S_IMODE(os.stat(home / "credentials.json").st_mode) == 0o600


def test_replacing_a_labelled_key_keeps_its_id_and_stores_a_blob(home):
    from watchdog.gui.api import settings as api
    app_encrypts()
    out = api.add_key("deepseek", "Work", "sk-ds-one-1111", key_enc=seal("sk-ds-one-1111"))
    api.set_key("deepseek", "sk-ds-two-2222", id=out["id"], key_enc=seal("sk-ds-two-2222"))
    assert "sk-ds-two-2222" not in raw(home) and "sk-ds-one-1111" not in raw(home)
    assert auth.list_keys("deepseek")[0]["id"] == out["id"]
    assert auth.list_keys("deepseek")[0]["masked"] == "sk-ds-two-…2222"
    assert auth.get_api_key("deepseek") == "sk-ds-two-2222"


def test_with_encryption_available_a_plaintext_write_is_refused(home):
    from watchdog.gui.api import settings as api
    from watchdog.gui.rpc import RpcError
    app_encrypts()
    with pytest.raises(RpcError, match="couldn't encrypt"):
        api.set_key("openai", "sk-proj-plain-0000")
    with pytest.raises(RpcError):
        api.add_key("openai", "Work", "sk-proj-plain-0000")
    assert not (home / "credentials.json").exists()


def test_a_malformed_blob_is_refused(home):
    from watchdog.gui.api import settings as api
    from watchdog.gui.rpc import RpcError
    app_encrypts()
    with pytest.raises(RpcError, match="form"):
        api.set_key("openai", "sk-proj-0000", key_enc={"enc": "other", "data": "x"})


# ── reading: what the app hands over ────────────────────────────────────────────

def test_migrated_file_reads_back_through_the_keys_the_app_provides(home):
    """The shape the main process writes when it migrates a plaintext file (keystore.ts): every
    secret replaced by a blob, structure untouched."""
    personal, work = "sk-proj-personal-1111", "sk-proj-work-2222"
    anth = "sk-ant-api03-anthropic-3333"
    blobs = {k: {**seal(k), "masked": auth._mask(k)} for k in (personal, work, anth)}
    auth._save_state({"mode": "api-key", "keys": {
        "anthropic": blobs[anth],
        "openai": {"default": "k-1", "items": [{"id": "k-1", "label": "Personal", "key": blobs[personal]},
                                               {"id": "k-2", "label": "Work", "key": blobs[work]}]}}})
    keystore.provide({b["data"]: k for k, b in blobs.items()}, store="encrypted", backend="keychain")
    assert auth.get_api_key("anthropic") == anth
    assert auth.get_api_key("openai") == personal
    assert [k["label"] for k in auth.list_keys("openai")] == ["Personal", "Work"]
    assert all(k["encrypted"] and not k["locked"] for k in auth.list_keys("openai"))
    # A write elsewhere in the file carries every blob through unchanged.
    auth.rename_key("openai", "k-2", "Newsroom")
    data = json.loads(raw(home))
    assert data["keys"]["openai"]["items"][1]["key"] == blobs[work]
    assert data["keys"]["anthropic"] == blobs[anth]


def test_auth_status_reports_where_keys_are_stored_and_never_the_key(home):
    from watchdog.gui.api import settings as api
    app_encrypts()
    api.set_key("openai", "sk-proj-status-9999", key_enc=seal("sk-proj-status-9999"))
    s = api.auth_status()
    assert s["storage"]["store"] == "encrypted" and s["storage"]["backend"] == "keychain"
    assert s["storage"]["encrypted"] == 1 and s["storage"]["plaintext"] == 0 and s["storage"]["locked"] == 0
    assert "sk-proj-status-9999" not in json.dumps(s)


def test_secrets_provide_is_an_rpc_that_takes_the_keys_and_the_store(home):
    from watchdog.gui.api import keystore as api
    from watchdog.gui.rpc import RpcError
    blob = seal("sk-proj-provided-1234")
    auth._save_state({"mode": None, "keys": {"openai": {**blob, "masked": "x"}}})
    assert api.provide("encrypted", {blob["data"]: "sk-proj-provided-1234"}, backend="dpapi") == {"ok": True, "keys": 1}
    assert auth.get_api_key("openai") == "sk-proj-provided-1234"
    with pytest.raises(RpcError):
        api.provide("maybe")


# ── the fallback: no usable secure storage ──────────────────────────────────────

def test_without_secure_storage_keys_stay_plaintext_in_a_private_file(home):
    from watchdog.gui.api import settings as api
    keystore.provide({}, store="plaintext", backend="basic_text", reason="no keyring")
    api.set_key("openai", "sk-proj-fallback-1234")
    data = json.loads(raw(home))
    assert data["keys"]["openai"] == "sk-proj-fallback-1234"
    assert stat.S_IMODE(os.stat(home / "credentials.json").st_mode) == 0o600
    s = api.auth_status()["storage"]
    assert s["store"] == "plaintext" and s["backend"] == "basic_text" and s["plaintext"] == 1


def test_fallback_counts_keys_it_cannot_read(home):
    """Keys encrypted earlier, on a session whose keyring is now unavailable: listed, locked."""
    from watchdog.gui.api import settings as api
    auth._save_state({"mode": "api-key", "keys": {"anthropic": {**seal("sk-ant-x-0000"), "masked": "sk-ant-x…0000"}}})
    keystore.provide({}, store="plaintext", backend="basic_text", unreadable=1)
    s = api.auth_status()
    assert s["storage"]["locked"] == 1
    assert s["claude"]["key_masked"] == "sk-ant-x…0000"


# ── the command line ─────────────────────────────────────────────────────────────

def _blob_file(home, key="sk-ant-api03-locked-5678", provider="anthropic"):
    auth._save_state({"mode": "api-key", "keys": {provider: {**seal(key), "masked": auth._mask(key)}}})


def test_the_command_line_cannot_read_an_encrypted_key_and_says_what_to_do(home):
    _blob_file(home)
    with pytest.raises(auth.KeyLockedError) as e:
        auth.get_api_key("anthropic")
    msg = str(e.value)
    assert "stored by the Watchdog app" in msg and "ANTHROPIC_API_KEY" in msg and "Nothing has been sent" in msg
    assert isinstance(e.value, auth.KeyChoiceError)


def test_the_command_line_exits_cleanly_rather_than_crashing(home, monkeypatch, capsys):
    from watchdog import cli
    _blob_file(home, "sk-proj-locked-1111", "openai")
    monkeypatch.setattr(cli, "_main", lambda: auth.get_api_key("openai"))
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert "OPENAI_API_KEY" in str(e.value.code) and str(e.value.code).startswith("Error:")


def test_the_command_line_status_lists_locked_keys_masked(home, capsys):
    _blob_file(home)
    d = auth.status_data()
    assert d["keys"][0]["locked"] is True and d["keys"][0]["masked"] == "sk-ant-api…5678"
    auth._status()
    out = capsys.readouterr().out
    assert "stored by the app, encrypted" in out and "ANTHROPIC_API_KEY" in out


def test_the_command_line_does_not_write_plaintext_beside_the_apps_keys(home, monkeypatch, capsys):
    _blob_file(home)
    monkeypatch.setattr(auth, "getpass", lambda prompt: "sk-proj-typed-0000")
    assert auth.prompt_and_store_key("openai", auth._load_state()) is False
    assert "sk-proj-typed-0000" not in raw(home)
    assert "Settings → Models & keys" in capsys.readouterr().out
    with pytest.raises(ValueError):
        auth.add_key("openai", "Work", "sk-proj-typed-0000")


def test_the_environment_variable_still_overrides_an_encrypted_key(home, vault, monkeypatch):
    _blob_file(home)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-env-0000")
    r = auth.resolve_key("anthropic", vault)
    assert r["key"] == "sk-ant-from-env-0000" and r["source"] == "env"
    assert auth.resolve_auth("anthropic")["key"] == "sk-ant-from-env-0000"


def test_a_locked_key_stops_before_any_model_call(home, vault, monkeypatch):
    """I16 unchanged: no key this process can use means no call, and never another key."""
    personal = "sk-proj-plain-default-0000"
    auth._save_state({"mode": None, "keys": {"openai": {"default": "k-2", "items": [
        {"id": "k-1", "label": "Personal", "key": personal},
        {"id": "k-2", "label": "Work", "key": {**seal("sk-proj-work-9999"), "masked": "m"}}]}}})
    sent = []

    async def backend(*a, **k):
        sent.append(a)
        return {"text": "{}", "usage": {}, "cost_usd": 0.0}
    monkeypatch.setitem(mc._ABACKENDS, "openai", backend)
    with pytest.raises(mc.ModelError, match="OPENAI_API_KEY"):
        asyncio.run(mc.acomplete_json(task="t", prompt="p", schema={"type": "object"},
                                      model="gpt-5.6-luna", backend="openai"))
    assert sent == []                                    # never fell back to the readable Personal key
    with pytest.raises(auth.KeyChoiceError):
        auth.check_run_keys(vault, ["openai"])


def test_under_the_app_a_locked_key_says_to_restart_not_to_set_a_variable(home, monkeypatch):
    _blob_file(home)
    monkeypatch.setenv("WATCHDOG_APP", "1")
    with pytest.raises(auth.KeyLockedError) as e:
        auth.get_api_key("anthropic")
    assert "Restart Watchdog" in str(e.value) and "ANTHROPIC_API_KEY" not in str(e.value)


# ── jobs: each is handed only its investigation's keys ──────────────────────────

def _two_investigations(home, tmp_path):
    work, personal = "sk-proj-work-AAAA", "sk-proj-personal-BBBB"
    anth = "sk-ant-api03-only-CCCC"
    b = {k: {**seal(k), "masked": auth._mask(k)} for k in (work, personal, anth)}
    auth._save_state({"mode": "api-key", "keys": {
        "anthropic": b[anth],
        "openai": {"default": "k-p", "items": [{"id": "k-p", "label": "Personal", "key": b[personal]},
                                               {"id": "k-w", "label": "Work", "key": b[work]}]}}})
    keystore.provide({v["data"]: k for k, v in b.items()}, store="encrypted", backend="keychain")
    a, c = tmp_path / "a", tmp_path / "c"
    for v in (a, c):
        (v / ".watchdog").mkdir(parents=True)
    auth.choose_key(c, "openai", "k-w")
    return a, c, work, personal, anth


def test_secrets_for_run_hands_over_one_key_per_provider_for_that_investigation(home, tmp_path, monkeypatch):
    a, c, work, personal, anth = _two_investigations(home, tmp_path)
    assert sorted(auth.secrets_for_run(a).values()) == sorted([personal, anth])
    assert sorted(auth.secrets_for_run(c).values()) == sorted([work, anth])
    assert auth.secrets_for_run(None) == {}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env-0000")         # env wins: nothing to hand over
    assert sorted(auth.secrets_for_run(c).values()) == [work]


def test_read_stdin_takes_the_keys_and_clears_the_flag(monkeypatch):
    monkeypatch.setenv(keystore.SECRETS_ENV, "stdin")
    monkeypatch.setattr(sys, "stdin", io.StringIO(keystore.child_input({"blobdata": "sk-x-1"}).decode()))
    keystore.read_stdin()
    assert keystore.reveal({"enc": keystore.ENC_TAG, "data": "blobdata"}) == "sk-x-1"
    assert keystore.SECRETS_ENV not in os.environ


def test_a_job_gets_its_keys_on_stdin_not_in_its_environment_or_arguments(home, tmp_path, monkeypatch):
    from watchdog.gui import jobs, rpc
    monkeypatch.setattr(rpc, "_test_sink", [])
    monkeypatch.setattr(jobs, "require_engine", lambda args: None)
    monkeypatch.setattr(jobs, "_require_granted_cwd", lambda v: None)
    a, c, work, personal, anth = _two_investigations(home, tmp_path)
    probe = ("import json, os, sys\n"
             "from watchdog import keystore\n"
             "env = dict(os.environ)\n"
             "keystore.read_stdin()\n"
             "print(json.dumps({'keys': sorted(keystore._plain.values()), 'env': env, 'argv': sys.argv}))\n")
    monkeypatch.setattr(jobs, "command_argv", lambda args: [sys.executable, "-u", "-c", probe])
    job = jobs.MANAGER.start(c, ["probe"], "probe", None)
    deadline = time.time() + 20
    while job.state == "running" and time.time() < deadline:
        time.sleep(0.05)
    out = json.loads(next(line["text"] for line in job.log if line["stream"] == "out"))
    assert out["keys"] == sorted([work, anth])                    # not Personal: c chose Work
    assert out["env"].get("WATCHDOG_SECRETS") == "stdin"
    flat = json.dumps(out["env"]) + json.dumps(out["argv"]) + json.dumps(job.to_dict())
    assert not any(k in flat for k in (work, personal, anth))
    # A short command (run_action) is handed its keys the same way.
    done = jobs.run_action(a, ["probe"], timeout=20)
    assert json.loads(done["stdout"])["keys"] == sorted([personal, anth])


def test_a_module_job_that_calls_a_model_gets_its_keys_and_others_do_not(home, tmp_path, monkeypatch):
    # The contradiction re-check runs `python -m watchdog.pipeline.recheck`, not a `watchdog`
    # command, and calls a model: it must be handed its keys too (secrets=True). A module job that
    # calls no model (rebuild notes, undo merge, a model download) is handed none.
    from watchdog.gui import jobs, rpc
    monkeypatch.setattr(rpc, "_test_sink", [])
    monkeypatch.setattr(jobs, "require_engine", lambda args: None)
    monkeypatch.setattr(jobs, "_require_granted_cwd", lambda v: None)
    a, c, work, personal, anth = _two_investigations(home, tmp_path)
    probe = ("import json, os\n"
             "from watchdog import keystore\n"
             "keystore.read_stdin()\n"
             "print(json.dumps(sorted(keystore._plain.values())))\n")

    def run(**kw):
        job = jobs.MANAGER.start(c, ["probe"], "probe", None, argv=[sys.executable, "-u", "-c", probe], **kw)
        deadline = time.time() + 20
        while job.state == "running" and time.time() < deadline:
            time.sleep(0.05)
        return json.loads(next(line["text"] for line in job.log if line["stream"] == "out"))

    assert run(secrets=True) == sorted([work, anth])
    assert run() == []


def test_the_recheck_module_reads_the_keys_it_is_handed():
    import inspect
    from watchdog.gui.api import contradictions
    from watchdog.pipeline import recheck
    assert "read_stdin()" in inspect.getsource(recheck.main)
    assert "secrets=True" in inspect.getsource(contradictions.start)
