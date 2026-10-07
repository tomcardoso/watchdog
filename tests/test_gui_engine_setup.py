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
