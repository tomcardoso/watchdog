"""No stored API key leaks out of Watchdog (D295).

Runs representative flows with sentinel keys, the way the desktop app runs them, then scans every
file written under a temporary home and investigation folder, captured stdout and stderr, the
events the backend sent the app, job logs and RPC responses for any sentinel. Covered:

  - storing keys from Settings (encrypted, as the app's main process sends them) and reading the
    status, an investigation's billing choice and its notices back;
  - a model call that succeeds (usage records, `telemetry.db`) and one a server refuses while
    echoing the Authorization header back (the error that reaches job logs, failure notes and the
    app's toasts);
  - a `watchdog` job started by the app, handed its keys on stdin, and the same command from a
    terminal, which can't read them;
  - an Ask Claude session's saved transcript;
  - a version-history snapshot of the investigation;
  - the backend's error responses, including a bad parameter that prints a traceback.

A sentinel's masked form (`sk-ant-SEN…ZZZZ`) may appear: Settings has always shown it. The
scan looks for the part of each key a mask never shows."""

import asyncio
import base64
import json
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from watchdog import keystore
from watchdog import model_client as mc
from watchdog.cmd import auth, base

TOKEN = secrets.token_hex(8).upper()
KEYS = {
    "anthropic": f"sk-ant-api03-SENTINEL-A-{TOKEN}-ZZZZ",
    "openai": f"sk-proj-SENTINEL-O-{TOKEN}-ZZZZ",
    "openai-work": f"sk-proj-SENTINEL-W-{TOKEN}-ZZZZ",
    "local": f"sk-local-SENTINEL-L-{TOKEN}-ZZZZ",
}
# The part of each key no mask shows (masks keep the first ten characters and the last four).
NEEDLES = [k[10:-4] for k in KEYS.values()]


def seal(key: str) -> dict:
    """Stand-in for safeStorage's ciphertext: opaque, and not the key in any readable form."""
    return {"enc": keystore.ENC_TAG, "data": base64.b64encode(secrets.token_bytes(8) + key[::-1].encode()).decode()}


def leaks(text: str) -> list[str]:
    return [n for n in NEEDLES if n in text]


class _Provider(BaseHTTPRequestHandler):
    """An OpenAI-compatible endpoint. Under /fail it refuses the key and echoes the Authorization
    header back in its error body, as some proxies and self-hosted servers do."""

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path.startswith("/fail"):
            body = json.dumps({"error": f"invalid token: {self.headers.get('Authorization')}"}).encode()
            self.send_response(401)
        else:
            body = json.dumps({"choices": [{"message": {"content": '{"a": 1}'}, "finish_reason": "stop"}],
                               "usage": {"prompt_tokens": 10, "completion_tokens": 2}}).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def provider_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Provider)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A temporary home with the demo investigation, as the app would see it."""
    home = tmp_path / "home"
    vault = tmp_path / "vault" / "port-calder"
    subprocess.run([sys.executable, "-m", "watchdog.gui.demo", str(vault), "--home", str(home)],
                   check=True, capture_output=True, timeout=180)
    wd = home / ".watchdog"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(base, "WATCHDOG_HOME", wd)
    monkeypatch.setattr(base, "CONFIG_FILE", wd / "config.json")
    monkeypatch.setattr(base, "PROJECTS_FILE", wd / "projects.json")
    import watchdog.config
    monkeypatch.setattr(watchdog.config, "CONFIG_FILE", wd / "config.json")
    for p in auth._PROVIDERS.values():
        monkeypatch.delenv(p["env"], raising=False)
    monkeypatch.delenv("WATCHDOG_APP", raising=False)
    return tmp_path, home, vault


def test_no_key_reaches_any_file_log_or_message(world, provider_server, monkeypatch, capfd):
    from watchdog.gui import chat, jobs, rpc, server
    from watchdog.gui.api import settings as api
    from watchdog.pipeline import history, orchestrate

    root, home, vault = world
    sink: list = []
    monkeypatch.setattr(rpc, "_test_sink", sink)
    seen: list = []          # every RPC response and error message the app would receive

    # ── Settings: the app stores keys, encrypted by its main process ──────────────
    keystore.provide({}, store="encrypted", backend="keychain")
    seen.append(api.set_anthropic_mode("api-key", key=KEYS["anthropic"], key_enc=seal(KEYS["anthropic"])))
    seen.append(api.set_key("openai", KEYS["openai"], key_enc=seal(KEYS["openai"])))
    work = api.add_key("openai", "Work", KEYS["openai-work"], key_enc=seal(KEYS["openai-work"]))
    seen.append(work)
    seen.append(api.set_key("local", KEYS["local"], key_enc=seal(KEYS["local"])))
    seen.append(api.choose_key(str(vault), "openai", work["id"]))
    seen.append(api.investigation_keys(str(vault)))
    seen.append(api.auth_status())

    # ── model calls: one that succeeds, one a server refuses, echoing the key ─────
    monkeypatch.setenv("LOCAL_BASE_URL", provider_server + "/ok/v1")
    orchestrate._begin_usage_run(vault)
    try:
        r = asyncio.run(orchestrate._call_model(task="extract", prompt="p", schema={"type": "object"},
                                                model="test-model", backend="local", vault=vault))
        assert r.parsed == {"a": 1}
        monkeypatch.setenv("LOCAL_BASE_URL", provider_server + "/fail/v1")
        with pytest.raises(mc.ProviderAuthError) as refused:
            asyncio.run(orchestrate._call_model(task="extract", prompt="p", schema={"type": "object"},
                                                model="test-model", backend="local", vault=vault))
        seen.append(str(refused.value))
        assert "invalid token" in str(refused.value)          # the reason is kept, the key isn't
    finally:
        orchestrate._end_usage_run(vault)

    # ── an operation the app starts (handed the keys), and a terminal command ──────
    job = jobs.MANAGER.start(vault, "bark", {}, "Post-processing", None)
    deadline = time.time() + 60
    while job.state == "running" and time.time() < deadline:
        time.sleep(0.05)
    jobs.MANAGER._flush(job)
    seen.append(job.to_dict())
    seen.append(list(job.log))
    job_out = "\n".join(line["text"] for line in job.log)
    # It was handed the keys its investigation bills; nothing was staged, so it finished at once.
    assert job.exit_code == 0, job_out
    assert "Nothing to finish" in job_out
    terminal = subprocess.run([sys.executable, "-m", "watchdog", "auth"], cwd=vault, capture_output=True,
                              text=True, timeout=60, env={**__import__("os").environ, "NO_COLOR": "1"})
    seen.append(terminal.stdout + terminal.stderr)
    assert "stored by the app, encrypted" in terminal.stdout

    # ── an Ask Claude session's transcript ───────────────────────────────────────
    session = chat.Session(vault, "ask", None, "Ask")
    env = chat.session_auth_env(session)
    assert env == {"ANTHROPIC_API_KEY": KEYS["anthropic"]}    # handed to Claude Code, by design
    mgr = chat.ChatManager()
    mgr.add_message(session, "user", "Who signed the lease?")
    mgr.add_message(session, "assistant", "The harbour commission.")
    mgr.save(session)

    # ── version history, and the backend's error responses ───────────────────────
    history.safe_snapshot(vault, {"kind": "test"})
    seen.append(server.handle({"id": 1, "method": "auth.setKey", "params": {"provider": "nope", "key": KEYS["openai"]}}))
    seen.append(server.handle({"id": 2, "method": "auth.setKey", "params": {"provider": "openai", "key": KEYS["openai"], "bogus": 1}}))
    keystore.provide({}, store="encrypted")                     # a failed decryption: keys locked
    seen.append(server.handle({"id": 3, "method": "ingest.estimate", "params": {"vault": str(vault), "stage": "bark"}}))

    # ── the scan ─────────────────────────────────────────────────────────────────
    found = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if path.suffix in (".pdf", ".png", ".jpg", ".mp3", ".wav", ".onnx", ".bin"):
            continue
        text = path.read_bytes().decode("utf-8", errors="replace")
        if leaks(text):
            found.append(str(path.relative_to(root)))
    out, err = capfd.readouterr()
    for name, blob in (("stdout", out), ("stderr", err), ("events", json.dumps(sink, default=str)),
                       ("responses", json.dumps(seen, default=str))):
        if leaks(blob):
            found.append(name)
    assert found == [], f"a key leaked into: {found}"
    # The files that matter were in fact written, so the scan covered them.
    wd = home / ".watchdog"
    assert (wd / "credentials.json").exists() and list((wd / "gui" / "chats").glob("*.json"))
    assert list((vault / ".watchdog").rglob("usage-*.json")) and (vault / ".watchdog" / "settings.json").exists()
    assert any(p.name == "telemetry.db" for p in root.rglob("telemetry.db"))
