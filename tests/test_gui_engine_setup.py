"""The desktop app's first-run pieces: `engine_setup`, `setup.complete`, `auth.routeIngestion`,
and the graceful absence of qpdf/Ghostscript. No downloads: every model fetch is replaced."""

import io
import json
from pathlib import Path

import pytest

from watchdog import progress
from watchdog.gui import engine_setup
from watchdog.pipeline import preprocess, preprocess_batch

from tests.gui_support import call, call_error

pytest_plugins = ["tests.gui_support"]


def config(home):
    path = home / "config.json"
    return json.loads(path.read_text()) if path.exists() else {}


@pytest.fixture(autouse=True)
def _own_home(monkeypatch, tmp_path):
    """The record of downloaded models lives in Watchdog's folder; keep it off the real one."""
    from watchdog import config as config_file
    monkeypatch.setattr(config_file, "HOME", tmp_path / "wdg")


@pytest.fixture
def sink(monkeypatch):
    buf = io.StringIO()
    monkeypatch.setattr("sys.__stdout__", buf)
    monkeypatch.setenv("WATCHDOG_PROGRESS", "1")
    return buf


def events(buf):
    return [e for e in (progress.parse(ln) for ln in buf.getvalue().split("\n")) if e]


# ── engine_setup models ──────────────────────────────────────────────────────────

def test_models_reports_each_step_and_failures_are_warnings(monkeypatch, sink):

    def boom():
        raise RuntimeError("network unreachable")

    monkeypatch.setattr(engine_setup, "_RUNNERS", {
        "docling": lambda: None, "gliner": boom, "embedding": lambda: None,
        "reranker": lambda: "skipped: off", "ocr": lambda: "rapidocr"})
    result = engine_setup.download_models()
    assert result == {"docling": "ok", "gliner": "warn", "embedding": "ok", "reranker": "ok", "ocr": "ok"}
    evs = [e for e in events(sink) if e["kind"] == "engine"]
    assert [(e["step"], e["state"]) for e in evs][:4] == [
        ("docling", "start"), ("docling", "ok"), ("gliner", "start"), ("gliner", "warn")]
    assert evs[3]["detail"] == "network unreachable"


def test_models_only_filters_and_main_exit_code(monkeypatch, sink):
    ran = []
    monkeypatch.setattr(engine_setup, "_RUNNERS", {s: (lambda s=s: ran.append(s)) for s, _ in engine_setup.STEPS})
    assert engine_setup.main(["models", "--only", "gliner,ocr"]) == 0
    assert ran == ["gliner", "ocr"]
    with pytest.raises(SystemExit):
        engine_setup.main(["models", "--only", "nope"])


def test_check_is_json_and_offline(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path / "fe"))
    monkeypatch.setenv("DOCLING_ARTIFACTS_PATH", str(tmp_path / "dl"))
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hf"))
    (tmp_path / "fe" / "models--qdrant--bge-small-en-v1.5-onnx-q").mkdir(parents=True)
    assert engine_setup.main(["check"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["embedding"] is True and data["docling"] is False


def test_preferred_ocr_engine(monkeypatch):
    monkeypatch.setattr(engine_setup.sys, "platform", "linux")
    monkeypatch.setattr(engine_setup, "available_ocr_engine", lambda: "rapidocr")
    monkeypatch.setitem(__import__("sys").modules, "tesserocr", None)   # import raises ImportError
    assert engine_setup.preferred_ocr_engine() == "rapidocr"
    monkeypatch.setattr(engine_setup.sys, "platform", "darwin")
    assert engine_setup.preferred_ocr_engine() is None


# ── setup.complete ───────────────────────────────────────────────────────────────

def test_complete_writes_what_setup_writes(wdg_home, tmp_path, monkeypatch):
    monkeypatch.setattr(engine_setup, "preferred_ocr_engine", lambda: "rapidocr")
    target = tmp_path / "Cases"
    r = call("setup.complete", projects_dir=str(target))
    assert target.is_dir() and r["ocr_engine"] == "rapidocr"
    c = config(wdg_home)
    assert c["projects_dir"] == str(target.resolve())
    assert c["chunk_workers"] == "auto" and c["chew_workers"] == "auto"
    assert "auto_approve" not in c
    assert call("app.info")["setup_complete"] is True


def test_complete_leaves_explicit_ocr_and_merges(wdg_home, tmp_path, monkeypatch):
    monkeypatch.setattr(engine_setup, "preferred_ocr_engine", lambda: "rapidocr")
    (wdg_home / "config.json").write_text(json.dumps({"ocr_engine": "easyocr", "extractor_model": "sonnet"}))
    call("setup.complete", projects_dir=str(tmp_path / "P"), auto_approve=True)
    c = config(wdg_home)
    assert c["ocr_engine"] == "easyocr" and c["extractor_model"] == "sonnet" and c["auto_approve"] is True
    call("setup.complete", projects_dir=str(tmp_path / "P"), auto_approve=False)
    assert "auto_approve" not in config(wdg_home)


def test_complete_defaults_projects_dir_to_investigations(wdg_home, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(engine_setup, "preferred_ocr_engine", lambda: None)
    call("setup.complete")
    assert (tmp_path / "Investigations").is_dir()


# ── auth.routeIngestion ──────────────────────────────────────────────────────────

def test_route_ingestion_sets_three_stages(wdg_home):
    call("auth.setKey", provider="openai", key="sk-test")
    r = call("auth.routeIngestion", provider="openai", model="openai:gpt-5-mini")
    c = config(wdg_home)
    assert c["classifier_model"] == c["extractor_model"] == c["finalizer_model"] == "openai:gpt-5-mini"
    assert r["claude"]["mode"] is None or isinstance(r["claude"], dict)


def test_route_ingestion_needs_a_ready_provider_and_matching_model(wdg_home):
    assert call_error("auth.routeIngestion", provider="openai", model="openai:gpt-5-mini")["code"] == "not_ready"
    call("auth.setKey", provider="openai", key="sk-test")
    assert call_error("auth.routeIngestion", provider="openai", model="gemini:x")["code"] == "bad_params"
    assert call_error("auth.routeIngestion", provider="anthropic", model="x")["code"] == "bad_params"


def test_setup_check_marks_helper_tools_optional(wdg_home):
    assert all(d["required"] is False for d in call("setup.check")["deps"])


# ── qpdf / Ghostscript absent ────────────────────────────────────────────────────

def test_pdf_preprocess_without_qpdf_or_gs(monkeypatch, tmp_path):
    def missing(*a, **k):
        raise FileNotFoundError("qpdf")
    monkeypatch.setattr(preprocess.subprocess, "run", missing)
    src = tmp_path / "a.pdf"
    src.write_bytes(b"%PDF-1.4")
    assert preprocess.pdf_preprocess(src) is None


def test_page_count_falls_back_to_pypdf(monkeypatch, tmp_path):
    from pypdf import PdfWriter
    pdf = tmp_path / "three.pdf"
    w = PdfWriter()
    for _ in range(3):
        w.add_blank_page(72, 72)
    with open(pdf, "wb") as f:
        w.write(f)

    def missing(*a, **k):
        raise FileNotFoundError("qpdf")
    monkeypatch.setattr(preprocess_batch.subprocess, "run", missing)
    assert preprocess_batch._count_pdf_pages(pdf) == 3


def test_every_download_runs_inside_the_os_trust_store(monkeypatch, sink):
    import truststore
    state = {"on": False, "calls": []}
    monkeypatch.setattr(truststore, "inject_into_ssl", lambda: state.update(on=True))
    monkeypatch.setattr(truststore, "extract_from_ssl", lambda: state.update(on=False))
    seen = {}
    monkeypatch.setattr(engine_setup, "_RUNNERS", {
        s: (lambda s=s: seen.update({s: state["on"]})) for s, _ in engine_setup.STEPS})
    engine_setup.download_models()
    assert seen == {s: True for s, _ in engine_setup.STEPS}
    assert state["on"] is False


def test_trust_store_is_released_only_when_the_last_download_ends(monkeypatch):
    import truststore
    from watchdog.tls import os_trust_store
    calls = []
    monkeypatch.setattr(truststore, "inject_into_ssl", lambda: calls.append("in"))
    monkeypatch.setattr(truststore, "extract_from_ssl", lambda: calls.append("out"))
    with os_trust_store():
        with os_trust_store():
            pass
        assert calls == ["in"]
    assert calls == ["in", "out"]
    with pytest.raises(RuntimeError):
        with os_trust_store():
            raise RuntimeError("boom")
    assert calls == ["in", "out", "in", "out"]


def _fake_models(monkeypatch, on_disk, ran, fail=()):
    """Four fingerprinted steps whose runners record that they ran."""
    def runner(step):
        def go():
            ran.append(step)
            if step in fail:
                raise RuntimeError("no network")
        return go
    steps = ("docling", "gliner", "embedding", "reranker")
    monkeypatch.setattr(engine_setup, "_RUNNERS", {**{s: runner(s) for s in steps}, "ocr": lambda: "x"})
    monkeypatch.setattr(engine_setup, "fingerprints", lambda: {s: f"{s}-v1" for s in steps})
    monkeypatch.setattr(engine_setup, "check", lambda: dict(on_disk))


def test_a_model_downloaded_for_this_version_is_not_fetched_again(monkeypatch, sink):
    on_disk = {"docling": False, "gliner": False, "embedding": False, "reranker": False}
    ran = []
    _fake_models(monkeypatch, on_disk, ran)
    engine_setup.download_models()
    assert ran == ["docling", "gliner", "embedding", "reranker"]

    ran.clear()
    on_disk.update(docling=True, gliner=True, embedding=True, reranker=True)
    result = engine_setup.download_models()
    assert ran == []
    assert result == {s: "ok" for s in ("docling", "gliner", "embedding", "reranker", "ocr")}
    assert any(e.get("detail") == "Already downloaded" for e in events(sink))


def test_a_changed_fingerprint_or_a_cleared_cache_downloads_again(monkeypatch, sink):
    on_disk = {"docling": True, "gliner": True, "embedding": True, "reranker": True}
    ran = []
    _fake_models(monkeypatch, on_disk, ran)
    engine_setup.download_models()
    ran.clear()

    on_disk["gliner"] = False                      # the cache was cleared
    monkeypatch.setattr(engine_setup, "fingerprints", lambda: {
        "docling": "docling-v2", "gliner": "gliner-v1", "embedding": "embedding-v1", "reranker": "reranker-v1"})
    engine_setup.download_models()
    assert ran == ["docling", "gliner"]


def test_a_failed_download_is_not_recorded(monkeypatch, sink):
    on_disk = {"docling": True, "gliner": True, "embedding": True, "reranker": True}
    ran = []
    _fake_models(monkeypatch, on_disk, ran, fail=("docling",))
    assert engine_setup.download_models()["docling"] == "warn"
    ran.clear()
    engine_setup.download_models()
    assert ran == ["docling"]                      # tried again; the others are settled


# ── Docling's models: a partial download is not a finished one (D357) ────────────────

@pytest.fixture
def docling_dir(monkeypatch, tmp_path):
    root = tmp_path / "docling"
    monkeypatch.setenv("DOCLING_ARTIFACTS_PATH", str(root))
    return root


def test_docling_is_complete_only_with_files_and_no_unfinished_marker(docling_dir):
    assert engine_setup._docling_complete() is False            # no folder
    docling_dir.mkdir()
    assert engine_setup._docling_complete() is False            # empty folder
    layout = docling_dir / "layout"
    layout.mkdir()
    (layout / "model.safetensors").write_bytes(b"x")
    assert engine_setup._docling_complete() is True
    cut = layout / ".cache" / "huggingface" / "download"
    cut.mkdir(parents=True)
    (cut / "weights.bin.abc.incomplete").write_bytes(b"x")      # a file cut off mid-transfer
    assert engine_setup._docling_complete() is False
    (cut / "weights.bin.abc.incomplete").unlink()
    assert engine_setup._docling_complete() is True
    engine_setup._record("docling", engine_setup.INCOMPLETE)    # a download that began, never ended
    assert engine_setup._docling_complete() is False


def test_a_download_that_stops_part_way_leaves_docling_incomplete_until_it_finishes(monkeypatch, sink, docling_dir):
    def partial():
        docling_dir.mkdir(parents=True, exist_ok=True)
        (docling_dir / "layout.bin").write_bytes(b"x")          # the first repository arrived
        raise RuntimeError("certificate verify failed")

    monkeypatch.setattr(engine_setup, "fingerprints", lambda: {"docling": "docling-v1"})
    monkeypatch.setattr(engine_setup, "_RUNNERS", {"docling": partial})
    assert engine_setup.download_models(["docling"]) == {"docling": "warn"}
    assert "certificate verify failed" in engine_setup.ERRORS["docling"]
    assert engine_setup._docling_complete() is False            # a file is there, and still not done

    monkeypatch.setitem(engine_setup._RUNNERS, "docling", lambda: None)
    assert engine_setup.download_models(["docling"]) == {"docling": "ok"}
    assert engine_setup._docling_complete() is True


# ── the capture browser (D357) ──────────────────────────────────────────────────────

def test_capture_browser_installs_the_package_then_the_browser(monkeypatch):
    from watchdog.pipeline import capture
    present = {"on": False}
    monkeypatch.setattr(capture, "render_available", lambda: present["on"])
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd[2:], kw["env"]))
        if cmd[2:4] == ["playwright", "install"]:
            present["on"] = True
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr(engine_setup, "_macos_ca_bundle", lambda: None)
    assert engine_setup._install_capture() is None
    assert [c[0][:2] for c in calls] == [["pip", "install"], ["playwright", "install"]]
    assert all(env["NODE_USE_SYSTEM_CA"] == "1" for _, env in calls)


def test_capture_browser_is_left_alone_when_installed_and_reports_why_it_failed(monkeypatch):
    from watchdog.pipeline import capture
    monkeypatch.setattr(capture, "render_available", lambda: True)
    monkeypatch.setattr("subprocess.run", lambda *a, **k: pytest.fail("ran an installer"))
    assert engine_setup._install_capture() == "already installed"

    monkeypatch.setattr(capture, "render_available", lambda: False)
    monkeypatch.setattr(
        "subprocess.run",
        lambda cmd, **kw: type("R", (), {"returncode": 1, "stdout": "", "stderr": "SELF_SIGNED_CERT_IN_CHAIN"})())
    with pytest.raises(RuntimeError, match="SELF_SIGNED_CERT_IN_CHAIN"):
        engine_setup._install_capture()


def test_macos_ca_bundle_is_used_for_node_unless_the_person_set_one(monkeypatch, tmp_path):
    monkeypatch.setattr(engine_setup.sys, "platform", "darwin")
    monkeypatch.delenv("NODE_EXTRA_CA_CERTS", raising=False)
    monkeypatch.setattr(engine_setup, "_macos_ca_bundle", lambda: tmp_path / "ca.pem")
    assert engine_setup._node_env()["NODE_EXTRA_CA_CERTS"] == str(tmp_path / "ca.pem")
    monkeypatch.setenv("NODE_EXTRA_CA_CERTS", "/theirs.pem")
    assert engine_setup._node_env()["NODE_EXTRA_CA_CERTS"] == "/theirs.pem"


def test_the_download_operation_says_why_it_failed(monkeypatch):
    from watchdog.ops import setup as setup_ops

    def fail(only):
        engine_setup.ERRORS["capture"] = "SELF_SIGNED_CERT_IN_CHAIN"
        return {"capture": "warn"}

    monkeypatch.setattr(engine_setup, "download_models", fail)
    with pytest.raises(SystemExit) as e:
        setup_ops.download_model(None, model="capture")
    assert "SELF_SIGNED_CERT_IN_CHAIN" in str(e.value)
    with pytest.raises(SystemExit):
        setup_ops.download_model(None, model="nope")


# ── where conversion reads Docling's models from (D358) ──────────────────────────────

def test_docling_is_read_from_the_hub_cache_when_no_folder_is_named(monkeypatch, tmp_path):
    hub = tmp_path / "hub"
    monkeypatch.delenv("DOCLING_ARTIFACTS_PATH", raising=False)
    monkeypatch.setenv("HF_HUB_CACHE", str(hub))
    assert engine_setup._docling_complete() is False                       # no hub folder at all
    (hub / "models--someone--else").mkdir(parents=True)
    assert engine_setup._docling_complete() is False                       # nothing of Docling's
    layout = hub / "models--docling-project--docling-layout-heron"
    (layout / "snapshots" / "abc").mkdir(parents=True)
    assert engine_setup._docling_complete() is True                        # a command-line install's cache
    (layout / "blobs").mkdir()
    (layout / "blobs" / "deadbeef.incomplete").write_bytes(b"x")
    assert engine_setup._docling_complete() is False                       # a file still arriving


def test_a_record_for_another_docling_version_means_the_models_are_not_in_place(monkeypatch, tmp_path):
    hub = tmp_path / "hub"
    (hub / "models--docling-project--docling-models").mkdir(parents=True)
    monkeypatch.delenv("DOCLING_ARTIFACTS_PATH", raising=False)
    monkeypatch.setenv("HF_HUB_CACHE", str(hub))
    monkeypatch.setattr(engine_setup, "_docling_fingerprint", lambda: "docling 3.0")
    engine_setup._record("docling", "docling 2.9")
    assert engine_setup._docling_complete() is False
    engine_setup._record("docling", "docling 3.0")
    assert engine_setup._docling_complete() is True


def test_the_docling_step_loads_the_models_the_way_a_conversion_does(monkeypatch):
    from watchdog.pipeline import preprocess
    loaded = []
    monkeypatch.setattr(preprocess, "load_pdf_models", lambda: loaded.append(True))
    assert engine_setup._download_docling() is None
    assert loaded == [True]


def test_converter_models_load_inside_the_os_trust_store_and_a_failure_is_left_to_convert(monkeypatch):
    import truststore
    from watchdog.pipeline import preprocess
    state = {"on": False}
    monkeypatch.setattr(truststore, "inject_into_ssl", lambda: state.update(on=True))
    monkeypatch.setattr(truststore, "extract_from_ssl", lambda: state.update(on=False))
    seen = []

    class Converter:
        def initialize_pipeline(self, fmt):
            seen.append((fmt, state["on"]))
            raise RuntimeError("certificate verify failed")

    preprocess._load_models(Converter(), "pdf")        # raises nothing: convert() reports it
    assert seen == [("pdf", True)] and state["on"] is False
