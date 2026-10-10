"""Jobs: operations run in a worker process for the desktop app (`watchdog.gui.jobs`, D298)."""

import json
import os
import sys
import time

import pytest

from watchdog import ops, progress
from watchdog.gui import jobs, rpc
from watchdog.gui.api import jobs as api

posix_only = pytest.mark.skipif(os.name == "nt", reason="signals differ on Windows")


@pytest.fixture
def sink(monkeypatch):
    events: list = []
    monkeypatch.setattr(rpc, "_test_sink", events)
    return events


# A stand-in worker: reads the request line, then runs the `code` parameter as its program.
_RUNNER = ("import json,sys; req = json.loads(sys.stdin.readline()); "
           "exec(compile(req['params']['code'], 'job', 'exec'))")


@pytest.fixture
def fake_cli(monkeypatch):
    """Run the `code` parameter of a `script` operation instead of a real operation."""
    def script(rep, vault, *, code: str):
        raise AssertionError("runs in the stand-in worker")
    monkeypatch.setitem(ops.OPS, "script", ops.Op("script", script, False, None, False, "job",
                                                   params={"code": {"type": "str", "required": True,
                                                                    "default": None, "nullable": False}}))
    monkeypatch.setitem(ops.OPS, "vscript", ops.Op("vscript", script, True, None, False, "job",
                                                    params={"code": {"type": "str", "required": True,
                                                                     "default": None, "nullable": False}}))
    monkeypatch.setattr(jobs, "worker_argv", lambda: [sys.executable, "-u", "-c", _RUNNER])


def _run(code, **kw):
    return api.start(op="script", params={"code": code}, **kw)


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


_wdp_inline = _wdp


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
    started = _run(script, vault=None, label="demo run", kind="dig")
    assert started["state"] == "running" and started["label"] == "demo run" and started["kind"] == "dig"
    job = _wait(started["id"])
    assert job.state == "done" and job.exit_code == 0
    got = api.get(started["id"])
    texts = [(ln["stream"], ln["text"]) for ln in got["log"]]
    assert ("out", "hello") in texts and ("out", "between") in texts and ("err", "oops") in texts
    assert not any(progress.PREFIX.strip() in t for _, t in texts)       # progress stays out of the log
    assert got["progress"]["stage"] == "dig"
    assert got["progress"]["docs"]["s1"] == {"filename": "a.pdf", "state": "done", "detail": "3 facts"}
    # The job's state changes a moment before its job.finished event is emitted.
    deadline = time.time() + 5
    while sink[-1]["event"] != "job.finished" and time.time() < deadline:
        time.sleep(0.02)
    names = [e["event"] for e in sink]
    assert names[0] == "job.started" and names[-1] == "job.finished"
    assert sink[0]["data"]["job"]["op"] == "script" and "args" not in sink[0]["data"]["job"]
    assert names.count("job.progress") == 3
    logged = [ln for e in sink if e["event"] == "job.log" for ln in e["data"]["lines"]]
    assert {ln["text"] for ln in logged} >= {"hello", "between", "oops"}
    assert sink[-1]["data"]["job"]["state"] == "done"
    assert any(j["id"] == started["id"] for j in api.list_jobs())


def test_failed_job_reports_exit_code(sink, fake_cli):
    started = _run("import sys; sys.exit(3)", label="x")
    job = _wait(started["id"])
    assert job.state == "failed" and job.exit_code == 3


def test_env_and_cwd(sink, fake_cli, tmp_path):
    (tmp_path / ".watchdog" / "registry").mkdir(parents=True)
    script = ("import os,sys; print(os.getcwd()); print(os.environ['NO_COLOR'], "
              "os.environ['WATCHDOG_PROGRESS'], os.environ['WATCHDOG_APP'], os.environ['PYTHONIOENCODING']); "
              "print(repr(sys.stdin.read()))")
    job = _wait(api.start(vault=str(tmp_path), op="vscript", params={"code": script}, label="env")["id"])
    out = [ln["text"] for ln in job.log]
    assert out[0] == str(tmp_path.resolve()) or out[0] == str(tmp_path)
    assert out[1] == "1 1 1 utf-8" and out[2] == "''"           # nothing after the request line
    assert job.params == {"code": script}


def test_vault_must_be_a_vault(sink, fake_cli, tmp_path):
    with pytest.raises(rpc.RpcError) as e:
        api.start(vault=str(tmp_path), op="vscript", params={"code": "pass"}, label="x")
    assert e.value.code == "not_a_vault"
    with pytest.raises(rpc.RpcError) as e:
        api.start(op="vscript", params={"code": "pass"}, label="x")     # needs an investigation
    assert e.value.code == "bad_params"
    with pytest.raises(rpc.RpcError) as e:
        api.start(op="no-such-op", label="x")
    assert e.value.code == "bad_params"
    with pytest.raises(rpc.RpcError) as e:
        api.start(op="script", params={"code": 3}, label="x")         # typed parameters
    assert e.value.code == "bad_params"
    with pytest.raises(rpc.RpcError) as e:
        api.start(op="script", params={"code": "pass", "argv": ["x"]}, label="x")
    assert e.value.code == "bad_params"


@posix_only
def test_cancel_sends_sigint_then_kills(sink, fake_cli):
    graceful = ("import time\ntry:\n    print('ready', flush=True)\n    time.sleep(30)\n"
                "except KeyboardInterrupt:\n    print('stopping gracefully', flush=True)\n    raise SystemExit(130)")
    started = _run(graceful, label="g")
    deadline = time.time() + 10
    while not api.get(started["id"])["log"] and time.time() < deadline:
        time.sleep(0.05)
    api.cancel(started["id"])
    job = _wait(started["id"])
    assert job.state == "cancelled" and job.exit_code == 130
    assert "stopping gracefully" in [ln["text"] for ln in job.log]

    stubborn = ("import signal,time\nsignal.signal(signal.SIGINT, signal.SIG_IGN)\n"
                "print('ready', flush=True)\ntime.sleep(30)")
    started = _run(stubborn, label="s")
    deadline = time.time() + 10
    while not api.get(started["id"])["log"] and time.time() < deadline:
        time.sleep(0.05)
    api.cancel(started["id"])
    time.sleep(0.3)
    assert jobs.MANAGER.get(started["id"]).state == "running"          # SIGINT alone did not stop it
    api.cancel(started["id"])                                           # the second cancel kills
    job = _wait(started["id"])
    assert job.state == "cancelled" and job.exit_code != 0


def test_a_stopped_job_that_exits_cleanly_is_cancelled_not_done(sink, fake_cli):
    """`watchdog watch` stops on Ctrl+C with exit 0; the app must still show it as stopped."""
    clean = ("import time\ntry:\n    print('ready', flush=True)\n    time.sleep(30)\n"
             "except KeyboardInterrupt:\n    raise SystemExit(0)")
    started = _run(clean, label="c")
    deadline = time.time() + 10
    while not api.get(started["id"])["log"] and time.time() < deadline:
        time.sleep(0.05)
    api.cancel(started["id"])
    job = _wait(started["id"])
    assert job.state == "cancelled" and job.exit_code == 0


def test_log_is_a_ring_buffer(sink, fake_cli):
    started = _run("for i in range(5200): print(i)", label="many")
    job = _wait(started["id"])
    assert len(job.log) == jobs.LOG_LINES
    assert job.log[-1]["text"] == "5199" and job.log[0]["text"] == "200"


def test_log_events_are_batched(sink, fake_cli):
    _wait(_run("for i in range(2000): print(i)", label="batch")["id"])
    batches = [e for e in sink if e["event"] == "job.log"]
    assert 0 < len(batches) < 200
    assert sum(len(e["data"]["lines"]) for e in batches) == 2000


@posix_only
def test_shutdown_hook_kills_running_jobs(sink, fake_cli):
    assert jobs.MANAGER.shutdown in rpc.SHUTDOWN_HOOKS
    started = _run("import time; time.sleep(30)", label="long")
    time.sleep(0.2)
    jobs.MANAGER.shutdown()
    job = _wait(started["id"])
    assert job.state != "running" and job.exit_code != 0


def test_action_run_strips_ansi_and_reports_code(fake_cli):
    code = ("import sys; print('\\x1b[32mok\\x1b[0m'); " + _wdp_inline(kind="result", op="script", result={"n": 1})
            + "; print('bad', file=sys.stderr); sys.exit(2)")
    out = api.run_action(op="script", params={"code": code})
    assert out == {"code": 2, "result": {"n": 1}, "log": "ok", "error": "bad"}


def test_action_run_times_out(fake_cli):
    with pytest.raises(rpc.RpcError) as e:
        api.run_action(op="script", params={"code": "import time; time.sleep(5)"}, timeout=0.3)
    assert e.value.code == "timeout"


def test_action_run_runs_a_real_operation(tmp_path, monkeypatch):
    """The real worker, end to end: a quick operation outside any investigation."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    (tmp_path / ".watchdog").mkdir()
    (tmp_path / ".watchdog" / "config.json").write_text("{}")
    out = api.run_action(op="projects-new", params={"name": "Harbour Files", "dir": str(tmp_path / "inv")})
    assert out["code"] == 0, out
    assert out["result"]["slug"] == "harbour-files"
    assert (tmp_path / "inv" / "harbour-files" / ".watchdog").is_dir()
    assert "\x1b" not in out["log"]
    out = api.run_action(op="projects-new", params={"name": "Harbour Files", "dir": str(tmp_path / "inv")})
    assert out["code"] == 1 and "already exists" in out["error"] and out["result"] is None


def test_jobs_ops_lists_every_operation_with_typed_parameters():
    schema = api.list_ops()
    assert {"add", "chew", "dig", "bark", "reindex", "merge-entities", "undo-merge", "requeue",
            "watch", "recheck-contradictions", "export", "projects-rename"} <= set(schema)
    assert schema["add"]["params"]["paths"]["type"] == "list[str]"
    assert schema["merge-entities"]["params"]["keep"]["required"] is True
    assert schema["add"]["engine"] == "add" and schema["reindex"]["engine"] == "index"


def test_shared_run_options_an_operation_does_not_take_are_dropped():
    assert ops.validate("bark", {"skip_briefing": True, "extractor_model": "opus", "limit": 2}) == {
        "skip_briefing": True}
    assert ops.validate("dig", {"verify": False, "skill": "", "limit": None}) == {"verify": False}
    with pytest.raises(ops.OpError):
        ops.validate("bark", {"paths": ["x"]})
