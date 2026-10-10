"""The worker (`python -m watchdog.worker`, D298): one operation per process, read from stdin as
`{op, params}`, reported on stdout as plain lines and `watchdog.progress` events, the result last.

Round trips for representative operations: adding documents with a canned model and the
re-check with a fake one (in this process, so the model can be replaced), merge, undo and the
search index in a real worker process, and Stop sent as a signal. Plus the guard that no app
path builds a `watchdog …` command line any more.
"""

import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.gui_support import wdg_home  # noqa: F401  (fixture)
from watchdog import model_client, ops, progress, worker

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    root = tmp_path_factory.mktemp("demo-worker")
    vault, home = root / "vault", root / "home"
    env = {**os.environ, "PYTHONPATH": str(SRC), "NO_COLOR": "1"}
    proc = subprocess.run([sys.executable, "-m", "watchdog.gui.demo", str(vault), "--home", str(home)],
                          env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    return vault, home


@pytest.fixture
def demo_copy(demo, tmp_path):
    vault, home = demo
    copy_home = tmp_path / "home"
    shutil.copytree(home, copy_home)
    copy = tmp_path / "vault"
    shutil.copytree(vault, copy)
    projects = copy_home / ".watchdog" / "projects.json"
    data = json.loads(projects.read_text())
    for info in data.values():
        info["path"] = str(copy)
    projects.write_text(json.dumps(data))
    access = copy_home / ".watchdog" / "access.json"
    if access.exists():
        a = json.loads(access.read_text())
        a["folders"] = [{**f, "path": str(tmp_path.resolve())} for f in a["folders"]]
        access.write_text(json.dumps(a))
    return copy, copy_home


def _parse(stdout: str) -> tuple[list[str], list[dict], object]:
    lines, events, result = [], [], None
    for line in stdout.split("\n"):
        event = progress.parse(line)
        if event is None:
            if line.strip():
                lines.append(line)
        elif event["kind"] == "result":
            result = event["result"]
        else:
            events.append(event)
    return lines, events, result


def _in_process(monkeypatch, capfd, request: dict, cwd: Path | None = None):
    """Run `worker.main()` here, with `request` as its stdin."""
    for name in ("WATCHDOG_APP", "WATCHDOG_PROGRESS", "NO_COLOR"):
        monkeypatch.setenv(name, "1")
    monkeypatch.delenv("WATCHDOG_SECRETS", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request) + "\n"))
    if cwd is not None:
        monkeypatch.chdir(cwd)
    code = worker.main()
    out, err = capfd.readouterr()
    return (code, *_parse(out), err)


def _subprocess(request: dict, cwd: Path | None, home: Path, timeout=120):
    env = {**os.environ, "PYTHONPATH": str(SRC), "HOME": str(home), "USERPROFILE": str(home)}
    env.pop("WATCHDOG_SECRETS", None)
    done = subprocess.run([sys.executable, "-m", "watchdog.worker"], cwd=str(cwd) if cwd else None,
                          input=json.dumps(request) + "\n", capture_output=True, text=True,
                          env=env, timeout=timeout)
    return (done.returncode, *_parse(done.stdout), done.stderr)


# ── the protocol ──────────────────────────────────────────────────────────────────────────

def test_a_request_it_cannot_read_exits_64(monkeypatch, capfd):
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json\n"))
    for name in ("WATCHDOG_APP", "WATCHDOG_PROGRESS"):
        monkeypatch.setenv(name, "1")
    assert worker.main() == 64
    assert "op" in capfd.readouterr().err


def test_an_unknown_operation_or_parameter_is_refused(monkeypatch, capfd, tmp_path):
    code, *_rest, err = _in_process(monkeypatch, capfd, {"op": "no-such-op", "params": {}})
    assert code == 64 and "Unknown operation" in err
    code, *_rest, err = _in_process(monkeypatch, capfd, {"op": "export", "params": {"format": 3}}, tmp_path)
    assert code == 64 and "format must be str" in err


def test_an_error_is_the_last_stderr_line_without_the_error_prefix(monkeypatch, capfd, tmp_path):
    code, lines, events, result, err = _in_process(monkeypatch, capfd, {"op": "reindex", "params": {}}, tmp_path)
    assert code == 1 and result is None
    assert err.strip().splitlines()[-1] == "this folder is not a Watchdog investigation."


# ── adding documents, with a canned model ─────────────────────────────────────────────────

def test_add_round_trip_with_a_canned_model(wdg_home, tmp_path, monkeypatch, capfd):  # noqa: F811
    from watchdog.gui import demo
    (wdg_home / "config.json").write_text(json.dumps({"projects_dir": str(tmp_path / "inv")}))
    monkeypatch.setattr("watchdog.cmd.vault._register_obsidian_vault", lambda v: None)
    made = ops.run("projects-new", {"name": "Worker Check", "dir": str(tmp_path / "inv")},
                   ops.CollectingReporter())
    vault = Path(made["path"])
    docs = demo.all_docs()
    demo.render_files(docs, tmp_path / "files")
    batch = [d for d in docs if d["file"] in demo.BATCH_ONE]
    demo._stage_chewed(vault, batch)
    monkeypatch.setattr(model_client, "acomplete_json", demo.CannedModel(docs, vault))
    monkeypatch.setattr("watchdog.cmd.auth.resolve_auth", lambda *a, **k: {"mode": "api-key"})
    monkeypatch.setattr("watchdog.cmd.auth.check_run_keys", lambda *a, **k: None)

    code, lines, events, result, err = _in_process(
        monkeypatch, capfd, {"op": "add", "params": {"skip_warning": True}}, vault)
    assert code == 0, err
    assert result["extracted"] == len(batch) and result["failed"] == 0
    kinds = {e["kind"] for e in events}
    assert {"stage", "doc"} <= kinds                       # the shapes the job dock reads
    assert {e["stage"] for e in events if e["kind"] == "stage"} >= {"dig", "commit", "briefing"}
    text = "\n".join(lines)
    assert "\x1b" not in text and "watchdog " not in text and "Ctrl+C" not in text
    assert (vault / "briefings").glob("*.md")
    assert len(json.loads((vault / ".watchdog" / "registry" / "documents.json").read_text())) == len(batch)


def test_add_without_the_acknowledgement_sends_nothing(wdg_home, tmp_path, monkeypatch, capfd):  # noqa: F811
    """The app passes skip_warning only once the reporter acknowledged; without it the worker
    declines the gate, as a closed terminal prompt would."""
    from watchdog.gui import demo
    (wdg_home / "config.json").write_text(json.dumps({"projects_dir": str(tmp_path / "inv")}))
    monkeypatch.setattr("watchdog.cmd.vault._register_obsidian_vault", lambda v: None)
    vault = Path(ops.run("projects-new", {"name": "Gate Check", "dir": str(tmp_path / "inv")},
                         ops.CollectingReporter())["path"])
    docs = demo.all_docs()
    demo.render_files(docs, tmp_path / "files")
    demo._stage_chewed(vault, docs[:1])

    async def never(**kw):
        raise AssertionError("no model call without the acknowledgement")
    monkeypatch.setattr(model_client, "acomplete_json", never)
    monkeypatch.setattr("watchdog.cmd.auth.resolve_auth", lambda *a, **k: {"mode": "api-key"})
    monkeypatch.setattr("watchdog.cmd.auth.check_run_keys", lambda *a, **k: None)
    code, lines, events, result, err = _in_process(monkeypatch, capfd, {"op": "dig", "params": {}}, vault)
    assert code == 0 and result is None
    assert "Public records only" in "\n".join(lines)


# ── the re-check, with a fake model ───────────────────────────────────────────────────────

def test_recheck_round_trip(demo_copy, monkeypatch, capfd):
    vault, _home = demo_copy
    monkeypatch.setattr("watchdog.pipeline.entity_notes.index_notes", lambda *a, **k: None)
    monkeypatch.setattr("watchdog.cmd.auth.check_run_keys", lambda *a, **k: None)
    calls = []

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        calls.append(task)
        return model_client.ModelResult(parsed={"merges": [], "contradictions": []}, text="",
                                        model="claude-sonnet-5-5", backend="claude-api",
                                        auth_mode="api-key", cost_usd=0.001,
                                        usage={"input_tokens": 100, "output_tokens": 5})
    monkeypatch.setattr(model_client, "acomplete_json", fake)
    entities = json.loads((vault / ".watchdog" / "registry" / "entities.json").read_text())
    eid = max(entities, key=lambda e: len(entities[e].get("appears_in") or []))
    code, lines, events, result, err = _in_process(
        monkeypatch, capfd, {"op": "recheck-contradictions", "params": {"ids": [eid]}}, vault)
    assert code == 0, err
    assert calls and set(calls) == {"reconcile"}
    assert result["calls_done"] == result["calls_total"] >= 1 and result["filed"] == []
    assert any(e["kind"] == "stage" and e["stage"] == "recheck" for e in events)
    assert "No new contradictions were found." in "\n".join(lines)


# ── real worker processes ─────────────────────────────────────────────────────────────────

def test_merge_then_undo_round_trip(demo_copy):
    vault, home = demo_copy
    entities = json.loads((vault / ".watchdog" / "registry" / "entities.json").read_text())
    people = sorted(e for e, v in entities.items() if v.get("type") == "person")
    keep, merge = people[0], people[1]
    code, lines, events, result, err = _subprocess(
        {"op": "merge-entities", "params": {"keep": keep, "merge": merge}}, vault, home)
    assert code == 0, err
    assert result["keep_name"] == entities[keep]["name"] and result["merge_name"] == entities[merge]["name"]
    after = json.loads((vault / ".watchdog" / "registry" / "entities.json").read_text())
    assert merge not in after
    assert "Rebuild the search index" in "\n".join(lines)          # app wording, not a command

    log = json.loads((vault / ".watchdog" / "registry" / "merges.json").read_text())
    entries = log["merges"] if isinstance(log, dict) else log
    mid = next(m["id"] for m in reversed(entries) if (m.get("merged") or {}).get("id") == merge
               or merge in json.dumps(m))
    code, lines, events, result, err = _subprocess({"op": "undo-merge", "params": {"id": mid}}, vault, home)
    assert code == 0, err
    assert merge in json.loads((vault / ".watchdog" / "registry" / "entities.json").read_text())
    assert result["split_name"] == entities[merge]["name"]


def test_reindex_round_trip(demo_copy):
    vault, home = demo_copy
    code, lines, events, result, err = _subprocess({"op": "reindex", "params": {}}, vault, home)
    try:
        import fastembed  # noqa: F401
    except ImportError:
        # Without the search tools the index is left alone and the reason is one sentence.
        assert code == 1 and result is None
        assert "search tools" in err and "Reinstalling the app restores them." in err
        return
    assert code == 0, err
    assert result["documents"] >= 1 and result["notes"] >= 1


def test_rebuild_timeline_round_trip(demo_copy):
    vault, home = demo_copy
    (vault / "timeline.md").unlink()
    code, lines, events, result, err = _subprocess({"op": "rebuild-timeline", "params": {}}, vault, home)
    assert code == 0, err
    assert (vault / "timeline.md").exists() and result["events"] >= 1


@pytest.mark.skipif(os.name == "nt", reason="signals differ on Windows")
def test_stop_is_a_signal_and_the_watcher_stops_cleanly(demo_copy, monkeypatch):
    from watchdog.gui import jobs, rpc
    vault, home = demo_copy
    monkeypatch.setattr(rpc, "_test_sink", [])
    monkeypatch.setattr(jobs, "_require_granted_cwd", lambda v: None)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PYTHONPATH", str(SRC))
    job = jobs.MANAGER.start(vault, "watch", {}, "Watching incoming")
    deadline = time.time() + 30
    while not any("watching" in ln["text"] for ln in job.log) and time.time() < deadline:
        time.sleep(0.05)
    assert any("Stop in Activity" in ln["text"] for ln in job.log), list(job.log)
    jobs.MANAGER.cancel(job.id)
    deadline = time.time() + 30
    while job.state == "running" and time.time() < deadline:
        time.sleep(0.05)
    assert job.state == "cancelled" and job.exit_code == 0
    assert any("Stopped watching." in ln["text"] for ln in job.log)


# ── no app path builds a `watchdog` command line ──────────────────────────────────────────

_RENDERER = ROOT / "gui" / "src"
_ARGV = re.compile(r"""startJob\(\s*\[|runAction\(\s*\[|args:\s*\[\s*['"]|jobs\.flags|command_argv|flagsFor""")


def test_no_app_path_builds_a_watchdog_command_line():
    found = []
    for path in list(_RENDERER.rglob("*.ts")) + list(_RENDERER.rglob("*.tsx")):
        text = path.read_text(encoding="utf-8")
        for m in _ARGV.finditer(text):
            line = text[:m.start()].count("\n") + 1
            found.append(f"{path.relative_to(ROOT)}:{line}: {text.splitlines()[line - 1].strip()[:90]}")
    backend = [SRC / "watchdog" / "gui" / "jobs.py", *(SRC / "watchdog" / "gui" / "api").glob("*.py")]
    cli = re.compile(r"""["']-m["'],\s*["']watchdog(?:\.pipeline\.[a-z_]+)?["']|command_argv|accepted_flags|build_parser""")
    for path in backend:
        text = path.read_text(encoding="utf-8")
        for m in cli.finditer(text):
            found.append(f"{path.relative_to(ROOT)}: {m.group(0)}")
    assert not found, "app code still builds a CLI command:\n" + "\n".join(found)


def test_investigation_operations(wdg_home, tmp_path, monkeypatch):  # noqa: F811
    from watchdog.cmd import base
    (wdg_home / "config.json").write_text(json.dumps({"projects_dir": str(tmp_path / "inv")}))
    monkeypatch.setattr("watchdog.cmd.vault._register_obsidian_vault", lambda v: None)
    monkeypatch.setattr("watchdog.cmd.vault._obsidian_config_path", lambda: tmp_path / "obsidian.json")
    rep = ops.CollectingReporter()
    made = ops.run("projects-new", {"name": "Dock Leases", "description": "Who holds them?",
                                    "dir": str(tmp_path / "inv")}, rep)
    assert made["slug"] == "dock-leases" and "watchdog " not in rep.text
    renamed = ops.run("projects-rename", {"slug": "dock-leases", "name": "Pier Leases"}, rep)
    assert renamed["slug"] == "pier-leases" and Path(renamed["path"]).name == "pier-leases"
    ops.run("projects-describe", {"slug": "pier-leases", "description": ""}, rep)
    assert "description" not in base.load_projects()["pier-leases"]
    ops.run("projects-archive", {"slug": "pier-leases"}, rep)
    assert base.load_projects()["pier-leases"]["archived"] is True
    ops.run("projects-archive", {"slug": "pier-leases", "archived": False}, rep)
    assert "archived" not in base.load_projects()["pier-leases"]
    gone = ops.run("projects-delete", {"slug": "pier-leases", "purge": True}, rep)
    assert gone["removed"] and not Path(renamed["path"]).exists()
