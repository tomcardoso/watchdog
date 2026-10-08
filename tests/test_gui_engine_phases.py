"""The app's two-phase engine install (D272): what phase 1 installs, that the backend starts and
serves reads without the phase-2 libraries, and that adding documents is refused until the
engine is complete."""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from watchdog.gui import engine_setup, jobs

from tests.gui_support import call, call_error

pytest_plugins = ["tests.gui_support"]

SRC = Path(__file__).resolve().parents[1] / "src"

# Everything phase 2 brings, including what the phase-2 distributions pull in.
PHASE2_MODULES = ("docling", "docling_core", "docling_parse", "fastembed", "gliner", "torch",
                  "torchvision", "transformers", "onnxruntime", "tokenizers", "huggingface_hub",
                  "rapidocr", "ocrmac", "easyocr", "tesserocr", "scipy", "pandas")


# ── core-requirements ────────────────────────────────────────────────────────────

REQUIRES = [
    "anthropic>=0.40", "docling>=2.0", "fastembed>=0.4", "gliner>=0.2", "pyyaml>=6.0",
    "ocrmac; sys_platform == 'darwin'", "Pillow>=10.0", "docling[asr]; extra == 'asr'",
    "numpy>=1.24; extra == 'dev'", 'playwright>=1.49; extra == "web"',
]


def test_core_requirements_drop_phase2_and_extras_and_add_numpy():
    out = engine_setup.core_requirements(REQUIRES)
    assert out == ["anthropic>=0.40", "pyyaml>=6.0", "Pillow>=10.0", "numpy"]


def test_core_requirements_keep_platform_markers():
    out = engine_setup.core_requirements(["truststore>=0.9", "pywin32; sys_platform == 'win32'"])
    assert out == ["truststore>=0.9", "pywin32; sys_platform == 'win32'", "numpy"]


def test_core_requirements_cover_the_package_metadata():
    """Every dependency the package declares is in exactly one phase."""
    from importlib.metadata import PackageNotFoundError, requires
    try:
        declared = [r for r in (requires("watchdog-intel") or []) if "extra ==" not in r]
    except PackageNotFoundError:
        pytest.skip("watchdog-intel is not installed")
    core = {engine_setup._requirement_name(r) for r in engine_setup.core_requirements()}
    for req in declared:
        name = engine_setup._requirement_name(req)
        assert (name in core) != (name in engine_setup.PHASE2_ONLY), name


def test_core_requirements_command_prints_one_per_line(capsys):
    assert engine_setup.main(["core-requirements"]) == 0
    lines = capsys.readouterr().out.split()
    assert "numpy" in lines and not any(line.startswith("docling") for line in lines)


# ── the phase-1 import surface ───────────────────────────────────────────────────

BLOCKED_RUN = textwrap.dedent("""
    import importlib, json, os, pkgutil, sys

    BLOCKED = set(json.loads(sys.argv[1]))

    class Blocker:
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in BLOCKED:
                raise ImportError(f"{name} is not installed (phase 2)")
            return None

    sys.meta_path.insert(0, Blocker())

    import watchdog.cli  # noqa: F401
    from watchdog.gui import server
    server.load_api()
    import watchdog.cmd, watchdog.gui.api, watchdog.pipeline
    failed = []
    for pkg in (watchdog.gui.api, watchdog.cmd, watchdog.pipeline):
        for m in pkgutil.iter_modules(pkg.__path__):
            try:
                importlib.import_module(f"{pkg.__name__}.{m.name}")
            except Exception as e:
                failed.append(f"{m.name}: {e}")
    vault = sys.argv[2]
    calls = [("app.info", {}), ("engine.ready", {}), ("settings.schema", {}), ("auth.status", {}),
             ("projects.list", {}), ("setup.models", {}),
             ("vault.summary", {"vault": vault}), ("vault.documents", {"vault": vault}),
             ("vault.entities", {"vault": vault}), ("search.status", {"vault": vault}),
             ("search.query", {"vault": vault, "query": "contract"}),
             ("jobs.start", {"vault": vault, "args": ["add"], "label": "x"})]
    out, pending = {}, None
    for i, (method, params) in enumerate(calls):
        resp = server.handle({"id": i, "method": method, "params": params})
        out[method] = resp.get("error", {}).get("code") if "error" in resp else "ok"
        if method == "search.query" and "result" in resp:
            pending = resp["result"]["semantic_pending"]
    loaded = sorted(m for m in sys.modules if m.split(".")[0] in BLOCKED)
    print(json.dumps({"failed": failed, "calls": out, "loaded": loaded, "semantic_pending": pending}))
""")


def test_backend_and_reads_work_without_phase2_libraries(wdg_home, rich_vault, tmp_path):
    """The backend starts, every API, command and pipeline module imports, and the read calls
    answer, with docling, torch, fastembed, gliner and the rest of phase 2 unimportable."""
    script = tmp_path / "blocked.py"
    script.write_text(BLOCKED_RUN)
    env = {**os.environ, "PYTHONPATH": str(SRC), "WATCHDOG_ENGINE_PENDING": "1",
           "HOME": str(tmp_path), "WATCHDOG_HOME": str(wdg_home)}
    env.pop("WATCHDOG_ENFORCE_ACCESS", None)
    r = subprocess.run([sys.executable, str(script), json.dumps(PHASE2_MODULES), str(rich_vault)],
                       capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0, r.stderr[-3000:]
    data = json.loads(r.stdout.strip().splitlines()[-1])
    assert data["failed"] == []
    assert data["loaded"] == []
    calls = data["calls"]
    assert calls.pop("jobs.start") == "engine_not_ready"
    assert set(calls.values()) == {"ok"}, calls
    assert data["semantic_pending"] is True


# ── gating ───────────────────────────────────────────────────────────────────────

@pytest.fixture
def pending(monkeypatch):
    monkeypatch.setenv(engine_setup.PENDING_ENV, "1")


@pytest.fixture
def fake_cli(monkeypatch):
    monkeypatch.setattr(jobs, "command_argv", lambda args: [sys.executable, "-c", "pass"])


@pytest.mark.parametrize("args", [
    ["add", "--skip-warning", "incoming"], ["chew"], ["dig"], ["bark"], ["ingest"], ["watch"],
    ["add", "--watch"], ["extract"], ["finalize"], ["requeue"], ["reindex"],
    ["review", "merge-entities", "a", "b"],
])
def test_pipeline_commands_refused_while_pending(pending, wdg_home, args):
    err = call_error("jobs.start", vault=None, args=args, label="x")
    assert err["code"] == "engine_not_ready"
    assert "still setting up" in err["message"]


def test_add_refusal_names_adding_documents(pending, wdg_home):
    err = call_error("jobs.start", vault=None, args=["add"], label="x")
    assert err["message"] == engine_setup.ENGINE_NOT_READY
    assert err["data"] == {"command": "add"}


def test_action_run_refuses_requeue_while_pending(pending, wdg_home):
    assert call_error("action.run", vault=None, args=["requeue"])["code"] == "engine_not_ready"


@pytest.mark.parametrize("args", [["research", "fetch", "https://example.org"], ["research-fetch"],
                                  ["timeline"], ["leads"], ["review", "watchlist"]])
def test_other_commands_run_while_pending(pending, wdg_home, fake_cli, args):
    job = call("jobs.start", vault=None, args=args, label="x")
    assert job["state"] in ("running", "done")


def test_nothing_refused_when_not_pending(monkeypatch, wdg_home, fake_cli):
    monkeypatch.delenv(engine_setup.PENDING_ENV, raising=False)
    assert call("engine.ready") == {"ready": True}
    assert call("jobs.start", vault=None, args=["add"], label="x")["state"] in ("running", "done")


def test_set_ready_lifts_the_gate_for_the_backend_and_its_children(pending, wdg_home):
    assert call("app.info")["engine_ready"] is False
    assert call("engine.setReady") == {"ready": True}
    assert engine_setup.PENDING_ENV not in os.environ
    assert call("engine.ready") == {"ready": True}


def test_search_skips_the_meaning_lane_while_pending(pending, wdg_home, rich_vault, monkeypatch):
    from watchdog.pipeline import embed
    monkeypatch.setattr(embed, "index_stats", lambda v: {"total": 3, "notes": 1, "passages": 2})

    def search(*a, **k):
        raise AssertionError("semantic search must not run while the engine is incomplete")

    monkeypatch.setattr(embed, "search", search)
    r = call("search.query", vault=str(rich_vault), query="contract")
    assert r["semantic_pending"] is True and r["semantic_error"] is None


# ── OCR choice made once the OCR engine exists ───────────────────────────────────

def test_apply_preferred_ocr_fills_a_missing_choice_only(wdg_home, monkeypatch):
    monkeypatch.setattr(engine_setup, "preferred_ocr_engine", lambda: "rapidocr")
    assert engine_setup.apply_preferred_ocr() is None          # no settings file yet
    cfg = wdg_home / "config.json"
    cfg.write_text(json.dumps({"projects_dir": "/x"}))
    assert engine_setup.apply_preferred_ocr() == "rapidocr"
    assert json.loads(cfg.read_text())["ocr_engine"] == "rapidocr"
    cfg.write_text(json.dumps({"ocr_engine": "easyocr"}))
    assert engine_setup.apply_preferred_ocr() is None
    assert json.loads(cfg.read_text())["ocr_engine"] == "easyocr"


def test_on_demand_models_run_only_when_named(monkeypatch):
    ran = []
    monkeypatch.setattr(engine_setup, "ON_DEMAND", [("whisper", "Transcription")])
    runners = {s: (lambda s=s: ran.append(s)) for s, _ in engine_setup.STEPS + [("whisper", "")]}
    monkeypatch.setattr(engine_setup, "_RUNNERS", runners)
    engine_setup.download_models()
    assert "whisper" not in ran
    ran.clear()
    engine_setup.download_models(["whisper"])
    assert ran == ["whisper"]
