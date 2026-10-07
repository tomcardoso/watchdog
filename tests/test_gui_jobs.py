"""Jobs: `watchdog` commands run as subprocesses for the desktop app (`watchdog.gui.jobs`)."""

import json
import os
import sys
import time

import pytest

from watchdog import progress
from watchdog.gui import jobs, rpc
from watchdog.gui.api import jobs as api

posix_only = pytest.mark.skipif(os.name == "nt", reason="signals differ on Windows")


@pytest.fixture
def sink(monkeypatch):
    events: list = []
    monkeypatch.setattr(rpc, "_test_sink", events)
    return events


@pytest.fixture
def fake_cli(monkeypatch):
    """Run `python -c <args[0]>` instead of the real command."""
    monkeypatch.setattr(jobs, "command_argv", lambda args: [sys.executable, "-u", "-c", *args])


def _wait(job_id, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = jobs.MANAGER.get(job_id)
        if job.state != "running" and job.proc.stdout.closed and job.finished:
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def _wdp(**event) -> str:
    return "print(" + repr(progress.PREFIX + json.dumps(event)) + ")"


def test_flags_for_add(): 
    out = jobs.flags_for("add", {"extractor_model": "sonnet", "verify": True, "concurrency": 4,
                                 "skill": "court-documents", "wait": True, "skip_briefing": True,
                                 "finalizer_synthesis_model": "haiku", "limit": 3, "force": True,
                                 "chew_workers": 2})
    assert out[:2] == ["--extractor-model", "sonnet"]
    for flag in ("--verify", "--wait", "--skip-briefing"):
        assert flag in out
    assert "--concurrency" in out and out[out.index("--concurrency") + 1] == "4"
    assert out[out.index("--skill") + 1] == "court-documents"
    assert "--finalizer-synthesis-model" in out
    # `add` takes neither --limit, --force nor --chew-workers.
    assert not {"--limit", "--force", "--chew-workers"} & set(out)


def test_flags_for_each_command():
    assert jobs.flags_for("dig", {"limit": 2, "force": True, "verify": False, "classify_pages": 3}) == [
        "--classify-pages", "3", "--limit", "2", "--force", "--no-verify"]
    assert jobs.flags_for("bark", {"finalizer_effort": "low", "skip_briefing": True,
                                   "extractor_model": "opus", "verify": True}) == [
        "--finalizer-effort", "low", "--skip-briefing"]
    assert jobs.flags_for("chew", {"chew_workers": 2, "chunk_workers": "auto", "force": True}) == [
        "--chew-workers", "2", "--chunk-workers", "auto"]


def test_flags_unset_gives_nothing():
    assert jobs.flags_for("add", {}) == [] and jobs.flags_for("dig", None) == []
    assert jobs.flags_for("add", {"verify": None, "skill": None, "concurrency": ""}) == []


def test_flags_rpc_rejects_other_commands():
    with pytest.raises(rpc.RpcError):
        api.flags("status", {})
    assert api.flags("bark", {"skip_briefing": True}) == {"args": ["--skip-briefing"]}


def test_grouped_commands_pass_through_the_cli_rewrite():
    from watchdog.cmd import groups
    assert groups.rewrite(["review", "resolve", "abc"]) == ["resolve", "abc"]
    assert groups.rewrite(["research", "fetch", "https://x.example"]) == ["fetch", "https://x.example"]
    assert groups.rewrite(["projects", "rename", "a", "b"]) == ["rename", "a", "b"]


def test_job_streams_log_and_progress(sink, fake_cli, tmp_path):
    script = "\n".join([
        "import sys",
        "print('\\x1b[1mhello\\x1b[0m')",
        _wdp(kind="stage", stage="dig", done=0, total=2),
        _wdp(kind="doc", sha="s1", filename="a.pdf", state="started", detail=None),
        "print('between')",
        _wdp(kind="doc", sha="s1", filename="a.pdf", state="done", detail="3 facts"),
        "print('oops', file=sys.stderr)",
        "sys.exit(0)",
    ])
    started = api.start(vault=None, args=[script], label="demo run", kind="dig")
    assert started["state"] == "running" and started["label"] == "demo run" and started["kind"] == "dig"
    job = _wait(started["id"])
    assert job.state == "done" and job.exit_code == 0
    got = api.get(started["id"])
    texts = [(l["stream"], l["text"]) for l in got["log"]]
    assert ("out", "hello") in texts and ("out", "between") in texts and ("err", "oops") in texts
    assert not any(progress.PREFIX.strip() in t for _, t in texts)       # progress stays out of the log
    assert got["progress"]["stage"] == "dig"
    assert got["progress"]["docs"]["s1"] == {"filename": "a.pdf", "state": "done", "detail": "3 facts"}
    names = [e["event"] for e in sink]
    assert names[0] == "job.started" and names[-1] == "job.finished"
    assert names.count("job.progress") == 3
    logged = [l for e in sink if e["event"] == "job.log" for l in e["data"]["lines"]]
    assert {l["text"] for l in logged} >= {"hello", "between", "oops"}
    assert sink[-1]["data"]["job"]["state"] == "done"
    assert any(j["id"] == started["id"] for j in api.list_jobs())


def test_failed_job_reports_exit_code(sink, fake_cli):
    started = api.start(args=["import sys; sys.exit(3)"], label="x")
    job = _wait(started["id"])
    assert job.state == "failed" and job.exit_code == 3


def test_env_and_cwd(sink, fake_cli, tmp_path):
    (tmp_path / ".watchdog" / "registry").mkdir(parents=True)
    script = ("import os,sys; print(os.getcwd()); print(os.environ['NO_COLOR'], "
              "os.environ['WATCHDOG_PROGRESS'], os.environ['PYTHONIOENCODING']); "
              "print(repr(sys.stdin.read()))")
    job = _wait(api.start(vault=str(tmp_path), args=[script], label="env")["id"])
    out = [l["text"] for l in job.log]
    assert out[0] == str(tmp_path.resolve()) or out[0] == str(tmp_path)
    assert out[1] == "1 1 utf-8" and out[2] == "''"                       # stdin is closed


def test_vault_must_be_a_vault(sink, fake_cli, tmp_path):
    with pytest.raises(rpc.RpcError) as e:
        api.start(vault=str(tmp_path), args=["pass"], label="x")
    assert e.value.code == "not_a_vault"
    with pytest.raises(rpc.RpcError):
        api.start(args="not a list", label="x")


@posix_only
def test_cancel_sends_sigint_then_kills(sink, fake_cli):
    graceful = ("import time\ntry:\n    print('ready', flush=True)\n    time.sleep(30)\n"
                "except KeyboardInterrupt:\n    print('stopping gracefully', flush=True)\n    raise SystemExit(130)")
    started = api.start(args=[graceful], label="g")
    deadline = time.time() + 10
    while not api.get(started["id"])["log"] and time.time() < deadline:
        time.sleep(0.05)
    api.cancel(started["id"])
    job = _wait(started["id"])
    assert job.state == "cancelled" and job.exit_code == 130
    assert "stopping gracefully" in [l["text"] for l in job.log]

    stubborn = ("import signal,time\nsignal.signal(signal.SIGINT, signal.SIG_IGN)\n"
                "print('ready', flush=True)\ntime.sleep(30)")
    started = api.start(args=[stubborn], label="s")
    deadline = time.time() + 10
    while not api.get(started["id"])["log"] and time.time() < deadline:
        time.sleep(0.05)
    api.cancel(started["id"])
    time.sleep(0.3)
    assert jobs.MANAGER.get(started["id"]).state == "running"          # SIGINT alone did not stop it
    api.cancel(started["id"])                                           # the second cancel kills
    job = _wait(started["id"])
    assert job.state == "cancelled" and job.exit_code != 0


def test_log_is_a_ring_buffer(sink, fake_cli):
    started = api.start(args=["for i in range(5200): print(i)"], label="many")
    job = _wait(started["id"])
    assert len(job.log) == jobs.LOG_LINES
    assert job.log[-1]["text"] == "5199" and job.log[0]["text"] == "200"


def test_log_events_are_batched(sink, fake_cli):
    job = _wait(api.start(args=["for i in range(2000): print(i)"], label="batch")["id"])
    batches = [e for e in sink if e["event"] == "job.log"]
    assert 0 < len(batches) < 200
    assert sum(len(e["data"]["lines"]) for e in batches) == 2000


@posix_only
def test_shutdown_hook_kills_running_jobs(sink, fake_cli):
    assert jobs.MANAGER.shutdown in rpc.SHUTDOWN_HOOKS
    started = api.start(args=["import time; time.sleep(30)"], label="long")
    time.sleep(0.2)
    jobs.MANAGER.shutdown()
    job = _wait(started["id"])
    assert job.state != "running" and job.exit_code != 0


def test_action_run_strips_ansi_and_reports_code(fake_cli):
    out = api.run_action(args=["import sys; print('\\x1b[32mok\\x1b[0m'); print('bad', file=sys.stderr); "
                               "sys.exit(2)"])
    assert out == {"code": 2, "stdout": "ok\n", "stderr": "bad\n"}


def test_action_run_times_out(fake_cli):
    with pytest.raises(rpc.RpcError) as e:
        api.run_action(args=["import time; time.sleep(5)"], timeout=0.3)
    assert e.value.code == "timeout"


def test_action_run_runs_the_real_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    out = api.run_action(args=["settings", "about"])
    assert out["code"] == 0 and "\x1b" not in out["stdout"] and out["stdout"].strip()
