"""Audio and video transcription (D273): segment-to-page assembly, timestamps, the metadata a
transcript carries, the routing into pre-processing, and the pieces around it (model download
events, the app's progress, the prompt note). Transcription itself is always mocked; decoding
tests run only where PyAV is installed."""

import io
import json
import sys
import types
import wave
from pathlib import Path

import pytest

from watchdog import progress
from watchdog.pipeline import preprocess, preprocess_batch, prompts, quote_verify, transcribe
from watchdog.pipeline.transcribe import assemble_pages, fmt_timestamp, parse_timestamp


# ── timestamps ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("seconds,text", [(0, "00:00:00"), (59.9, "00:00:59"), (61, "00:01:01"),
                                          (3725, "01:02:05"), (-3, "00:00:00")])
def test_fmt_timestamp(seconds, text):
    assert fmt_timestamp(seconds) == text


def test_parse_timestamp_round_trips():
    assert parse_timestamp("[01:02:05] Good evening.") == 3725.0
    assert parse_timestamp("no stamp here") is None


# ── segment → page assembly ──────────────────────────────────────────────────────────

def test_mid_sentence_segments_join_into_one_timestamped_line():
    segs = [(0.0, 4.0, " The committee approved the"), (4.0, 8.0, " budget of four million dollars."),
            (10.0, 13.0, " Next item.")]
    pages, _ = assemble_pages(segs, 20)
    assert pages[0]["markdown"] == (
        "[00:00:00] The committee approved the budget of four million dollars.\n\n[00:00:10] Next item.")


def test_sentence_end_without_pause_keeps_the_line_going_until_soft_limit():
    segs = [(0.0, 3.0, "One."), (3.1, 6.0, "Two."), (30.0, 33.0, "Three.")]
    pages, _ = assemble_pages(segs, 40)
    lines = pages[0]["markdown"].split("\n\n")
    assert lines == ["[00:00:00] One. Two.", "[00:00:30] Three."]


def test_unpunctuated_speech_still_breaks_at_the_hard_limit():
    segs = [(float(t), float(t + 10), "and so on") for t in range(0, 130, 10)]
    pages, _ = assemble_pages(segs, 130)
    stamps = [ln[:10] for ln in pages[0]["markdown"].split("\n\n")]
    assert stamps == ["[00:00:00]", "[00:01:00]", "[00:02:00]"]


def test_pages_are_fixed_time_blocks_numbered_by_time_with_ranges():
    segs = [(5.0, 9.0, "First block."), (299.0, 304.0, "Straddles the boundary."),
            (310.0, 312.0, "Second block."), (905.0, 910.0, "Fourth block.")]
    pages, ranges = assemble_pages(segs, 930.5)
    assert [p["page"] for p in pages] == [1, 2, 4]          # 10:00-15:00 had no speech
    assert "Straddles the boundary." in pages[0]["markdown"]  # kept with the page it starts on
    assert ranges == [{"page": 1, "start": 0.0, "end": 300.0},
                      {"page": 2, "start": 300.0, "end": 600.0},
                      {"page": 4, "start": 900.0, "end": 930.5}]
    assert pages[2]["markdown"] == "[00:15:05] Fourth block."


def test_no_speech_gives_no_pages():
    assert assemble_pages([(0.0, 1.0, "   ")], 60) == ([], [])


# ── process_media ────────────────────────────────────────────────────────────────────

@pytest.fixture
def fake_engine(monkeypatch):
    """faster_whisper importable, the model already downloaded, transcription canned."""
    monkeypatch.setitem(sys.modules, "faster_whisper", types.ModuleType("faster_whisper"))
    monkeypatch.setattr(transcribe, "ensure_model", lambda name, emit=None: None)
    monkeypatch.setattr(transcribe, "configured_model", lambda config=None: "small")
    monkeypatch.setattr(transcribe, "configured_language", lambda config=None: None)
    monkeypatch.setattr(transcribe, "_model_revision", lambda name: "abc123")
    calls = {}

    def fake_transcribe(path, *, model_name, language, on_progress=None, duration_hint=None):
        calls.update(model=model_name, language=language)
        if on_progress:
            on_progress(310.0, duration_hint)
        return {"segments": [(1.0, 3.0, "Order, please."), (305.0, 309.0, "Item two.")],
                "language": "en", "language_probability": 0.9876, "duration": 620.0}

    monkeypatch.setattr(transcribe, "transcribe", fake_transcribe)
    return calls


def test_process_media_result_shape_and_versioned_metadata(tmp_path, monkeypatch, fake_engine):
    f = tmp_path / "council.mp4"
    f.write_bytes(b"not really video")
    monkeypatch.setattr(transcribe, "probe", lambda p: {"kind": "video", "duration": 620.0, "has_audio": True})
    result = transcribe.process_media(f)
    assert result["page_count"] == 2
    assert result["pages"][0] == {"page": 1, "markdown": "[00:00:01] Order, please."}
    meta = result["metadata"]
    assert meta["source_type"] == "transcript" and meta["ocr_used"] is False
    media = meta["media"]
    assert media["format"] == transcribe.MEDIA_FORMAT
    assert media["kind"] == "video" and media["duration_seconds"] == 620.0
    assert media["pages"] == [{"page": 1, "start": 0.0, "end": 300.0}, {"page": 2, "start": 300.0, "end": 600.0}]
    assert media["language"] == "en" and media["language_probability"] == 0.988
    tx = media["transcription"]
    assert tx["engine"] == "faster-whisper" and tx["model"] == "small"
    assert tx["model_repo"] == "Systran/faster-whisper-small" and tx["model_revision"] == "abc123"
    assert tx["compute_type"] == "int8" and tx["beam_size"] == 5 and tx["language_setting"] == "auto"
    assert len(result["sha256"]) == 64


def test_process_media_reports_transcription_progress(tmp_path, monkeypatch, fake_engine):
    f = tmp_path / "a.mp3"
    f.write_bytes(b"x")
    monkeypatch.setattr(transcribe, "probe", lambda p: {"kind": "audio", "duration": 620.0, "has_audio": True})
    seen = []
    transcribe.process_media(f, emit=lambda kind, **kw: seen.append((kind, kw)))
    assert ("transcribe", {"name": "a.mp3", "position": 310, "duration": 620}) in seen


@pytest.mark.parametrize("kind,word", [("video", "video"), ("audio", "file")])
def test_a_recording_without_sound_fails_clearly(tmp_path, monkeypatch, fake_engine, kind, word):
    f = tmp_path / "silent.mp4"
    f.write_bytes(b"x")
    monkeypatch.setattr(transcribe, "probe", lambda p: {"kind": kind, "duration": 5.0, "has_audio": False})
    result = transcribe.process_media(f)
    assert result == {"error": f"This {word} has no sound track, so there is nothing to transcribe."}


def test_missing_engine_is_a_clear_error(tmp_path, monkeypatch):
    f = tmp_path / "a.wav"
    f.write_bytes(b"x")
    monkeypatch.setattr(transcribe, "probe", lambda p: {"kind": "audio", "duration": 5.0, "has_audio": True})
    monkeypatch.setitem(sys.modules, "faster_whisper", None)     # import raises ImportError
    assert "Repair the engine" in transcribe.process_media(f)["error"]


def test_failed_download_is_a_clear_error(tmp_path, monkeypatch):
    f = tmp_path / "a.wav"
    f.write_bytes(b"x")
    monkeypatch.setattr(transcribe, "probe", lambda p: {"kind": "audio", "duration": 5.0, "has_audio": True})
    monkeypatch.setitem(sys.modules, "faster_whisper", types.ModuleType("faster_whisper"))
    monkeypatch.setattr(transcribe, "model_cached", lambda name: False)
    monkeypatch.setattr(transcribe, "download_size", lambda name: 486_000_000)

    def offline(name, on_progress=None, poll=0.5):
        raise OSError("no network")

    monkeypatch.setattr(transcribe, "download_model", offline)
    seen = []
    result = transcribe.process_media(f, emit=lambda kind, **kw: seen.append((kind, kw["state"])))
    assert "could not be downloaded" in result["error"]
    assert seen == [("model", "start"), ("model", "failed")]


def test_ensure_model_emits_sized_progress(monkeypatch):
    monkeypatch.setattr(transcribe, "model_cached", lambda name: False)
    monkeypatch.setattr(transcribe, "download_size", lambda name: 486_000_000)

    def fake_download(name, on_progress=None, poll=0.5):
        on_progress(486_000_000, 486_000_000)

    monkeypatch.setattr(transcribe, "download_model", fake_download)
    seen = []
    transcribe.ensure_model("small", lambda kind, **kw: seen.append(kw))
    assert seen[0]["label"] == "Downloading the transcription model (486 MB)"
    assert [e["state"] for e in seen] == ["start", "progress", "done"]
    assert seen[-1]["done"] == seen[-1]["total"] == 486


def test_settings_are_validated():
    assert transcribe.configured_model({"transcription_model": "medium"}) == "medium"
    assert transcribe.configured_model({"transcription_model": "huge"}) == transcribe.DEFAULT_MODEL
    assert transcribe.configured_language({}) is None
    assert transcribe.configured_language({"transcription_language": "auto"}) is None
    assert transcribe.configured_language({"transcription_language": "FR"}) == "fr"
    assert transcribe.configured_language({"transcription_language": "klingon"}) is None


def test_the_settings_screen_offers_the_models_and_languages():
    from watchdog.cmd.setup import _CONFIGURE_KEYS
    assert _CONFIGURE_KEYS["transcription_model"]["choices"] == list(transcribe.MODELS)
    assert _CONFIGURE_KEYS["transcription_model"]["default"] == transcribe.DEFAULT_MODEL
    assert _CONFIGURE_KEYS["transcription_language"]["choices"][0] == "auto"


# ── routing ──────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("suffix", sorted(transcribe.MEDIA_SUFFIXES))
def test_media_suffixes_route_to_transcription_not_docling(tmp_path, monkeypatch, capsys, suffix):
    f = tmp_path / f"clip{suffix}"
    f.write_bytes(b"x")
    monkeypatch.setattr(transcribe, "process_media", lambda p, emit=None: {
        "filename": p.name, "sha256": "0" * 64, "page_count": 1,
        "pages": [{"page": 1, "markdown": "[00:00:00] Hello."}], "metadata": {"source_type": "transcript"}})
    monkeypatch.setattr(preprocess, "process_with_docling", lambda *a, **k: pytest.fail("Docling called"))
    monkeypatch.setattr(sys, "argv", ["preprocess", str(f)])
    preprocess.main()
    out = json.loads(capsys.readouterr().out)
    assert out["metadata"]["source_type"] == "transcript"
    assert suffix not in preprocess.DOCLING_SUFFIXES


def test_research_still_accepts_media_urls():
    from watchdog.pipeline import research
    assert research.extension_for("application/octet-stream", "https://example.org/meeting.mp3") == ".mp3"


def test_stderr_emitter_only_with_progress_on(monkeypatch, capsys):
    monkeypatch.delenv("WATCHDOG_PROGRESS", raising=False)
    transcribe.stderr_emitter("transcribe", name="a.mp3", position=1, duration=2)
    assert capsys.readouterr().err == ""
    monkeypatch.setenv("WATCHDOG_PROGRESS", "1")
    transcribe.stderr_emitter("transcribe", name="a.mp3", position=1, duration=2)
    assert progress.parse(capsys.readouterr().err.rstrip("\n")) == {
        "kind": "transcribe", "name": "a.mp3", "position": 1, "duration": 2}


# ── preprocess_batch ─────────────────────────────────────────────────────────────────

class _ExitedPopen:
    def __init__(self, stdout, stderr):
        self.stdout, self.stderr = io.StringIO(stdout), io.StringIO(stderr)

    def poll(self):
        return 0

    def kill(self): pass
    def wait(self): pass


def test_preprocess_one_forwards_child_progress_and_keeps_stdout_clean(tmp_path, monkeypatch):
    f = tmp_path / "a.mp3"
    f.write_bytes(b"x")
    payload = {"filename": "a.mp3", "pages": [{"page": 1, "markdown": "[00:00:00] Hi."}]}
    line = progress.PREFIX + json.dumps({"kind": "transcribe", "name": "a.mp3", "position": 5, "duration": 9})
    monkeypatch.setattr(preprocess_batch.subprocess, "Popen",
                        lambda cmd, **kw: _ExitedPopen(json.dumps(payload), f"warning: x\n{line}\n"))
    forwarded = []
    monkeypatch.setattr(preprocess_batch.progress, "emit", lambda kind, **kw: forwarded.append((kind, kw)))
    result = preprocess_batch.preprocess_one(f)
    assert result["pages"] == payload["pages"]
    assert forwarded == [("transcribe", {"name": "a.mp3", "position": 5, "duration": 9})]


def test_media_label():
    assert preprocess_batch._media_label({"metadata": {"media": {"duration_seconds": 2530, "kind": "video"}}}) == "42 min video"
    assert preprocess_batch._media_label({"metadata": {}}) == ""


def test_prefetch_only_when_a_recording_needs_a_missing_model(monkeypatch, tmp_path):
    fetched = []
    monkeypatch.setattr(transcribe, "configured_model", lambda config=None: "small")
    monkeypatch.setattr(transcribe, "ensure_model", lambda name, emit=None: fetched.append(name))
    monkeypatch.setattr(transcribe, "model_cached", lambda name: False)
    preprocess_batch._prefetch_transcription_model([tmp_path / "a.pdf"])
    assert fetched == []
    preprocess_batch._prefetch_transcription_model([tmp_path / "a.pdf", tmp_path / "b.WAV"])
    assert fetched == ["small"]
    monkeypatch.setattr(transcribe, "model_cached", lambda name: True)
    preprocess_batch._prefetch_transcription_model([tmp_path / "b.wav"])
    assert fetched == ["small"]


def test_media_timeout_scales_with_length(monkeypatch, tmp_path):
    monkeypatch.setattr(transcribe, "probe", lambda p: {"duration": 3 * 3600})
    assert transcribe.media_timeout(tmp_path / "a.mp3", 600) == 3 * 3600 * 4 + 600
    monkeypatch.setattr(transcribe, "probe", lambda p: {"duration": 30})
    assert transcribe.media_timeout(tmp_path / "a.mp3", 600) == 720


# ── downstream: prompt, quotes, registry, app ────────────────────────────────────────

def test_transcript_note_only_for_transcripts():
    from watchdog.model_client import _flatten_prompt as flat
    common = dict(pages_text="BODY", skill_text="S", sidecar=None, brief=None, known_document_types=[])
    plain = flat(prompts.build_extract_prompt(**common, processing={"source_type": "docling"}))
    spoken = flat(prompts.build_extract_prompt(**common, processing={"source_type": "transcript"}))
    section = flat(prompts.build_section_prompt(pages_text="BODY", skill_text="S", carry_forward="",
                                                section_label="pages 1-2", is_first=True,
                                                known_document_types=[],
                                                processing={"source_type": "transcript"}))
    assert "MACHINE TRANSCRIPT" not in plain
    assert "MACHINE TRANSCRIPT" in spoken and "MACHINE TRANSCRIPT" in section
    assert spoken.index("MACHINE TRANSCRIPT") < spoken.index("BODY")


def test_expanded_quote_drops_the_line_timestamp():
    page = "[00:01:10] The contract was worth four million dollars. It was signed.\n\n[00:01:30] Next."
    r = quote_verify.resolve_quote({1: page}, 1, "The contract was worth")
    assert r["quote"] == "The contract was worth four million dollars."


def test_stamp_document_carries_media():
    from watchdog.pipeline import orchestrate
    media = {"format": 1, "kind": "audio", "pages": []}
    ex = {"document": {}}
    orchestrate._stamp_document(ex, sha="s" * 64, skill_label="general-records", pf={
        "filename": "a.mp3", "pages": [], "processing": {"source_type": "transcript", "media": media}})
    assert ex["document"]["media"] == media
    ex2 = {"document": {}}
    orchestrate._stamp_document(ex2, sha="s" * 64, skill_label="general-records", pf={
        "filename": "a.pdf", "pages": [], "processing": {"source_type": "docling"}})
    assert "media" not in ex2["document"]


def test_vaultio_media_info_reads_known_fields_of_any_format():
    from watchdog.gui import vaultio
    assert vaultio.media_info({"filename": "a.pdf"}) is None
    info = vaultio.media_info({"media": {
        "format": 7, "kind": "video", "duration_seconds": 620, "page_seconds": 300,
        "pages": [{"page": 1, "start": 0, "end": 300}, {"page": "bad"}, {"page": 2, "start": 300}],
        "transcription": {"model": "small", "future_field": 1}, "speakers": ["unknown"]}})
    assert info == {"kind": "video", "duration_seconds": 620.0, "page_seconds": 300.0,
                    "pages": [{"page": 1, "start": 0.0, "end": 300.0}, {"page": 2, "start": 300.0, "end": 300.0}],
                    "language": None, "model": "small"}


def test_jobs_show_model_download_and_transcription():
    from watchdog.gui import jobs
    job = jobs.Job(None, "chew", {}, "Add", "chew")
    job.apply_progress({"kind": "model", "label": "Downloading the transcription model (486 MB)",
                        "state": "progress", "done": 120, "total": 486})
    assert job.progress["stage"] == "model" and job.progress["done"] == 120
    assert job.progress["current"].startswith("Downloading the transcription model")
    job.apply_progress({"kind": "model", "state": "done", "done": 486, "total": 486})
    assert job.progress["stage"] is None
    job.apply_progress({"kind": "chew", "state": "start", "total": 3})
    job.apply_progress({"kind": "transcribe", "name": "hearing.mp4", "position": 725, "duration": 3725})
    assert job.progress["note"] == "Transcribing hearing.mp4, 12:05 of 1:02:05"
    job.apply_progress({"kind": "chew", "state": "file", "name": "hearing.mp4", "done": 1, "total": 3})
    assert job.progress["note"] is None and job.progress["stage"] == "chew"


def test_engine_setup_keeps_transcription_on_demand(monkeypatch):
    from watchdog.gui import engine_setup
    ran = []
    monkeypatch.setattr(engine_setup, "_RUNNERS", {s: (lambda s=s: ran.append(s)) for s, _ in engine_setup.STEPS + engine_setup.ON_DEMAND})
    engine_setup.download_models()
    assert "transcription" not in ran
    ran.clear()
    engine_setup.download_models(["transcription"])
    assert ran == ["transcription"]


def test_engine_setup_check_reports_transcription(monkeypatch, tmp_path):
    from watchdog.gui import engine_setup
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hf"))
    monkeypatch.setattr(transcribe, "configured_model", lambda config=None: "small")
    assert engine_setup._transcription_check()["transcription"] is False
    snap = tmp_path / "hf" / "models--Systran--faster-whisper-small" / "snapshots" / "abc"
    snap.mkdir(parents=True)
    (snap / "model.bin").write_bytes(b"")
    got = engine_setup._transcription_check()
    assert got == {"transcription": True, "transcription_model": "small", "transcription_size_mb": 486}


def test_download_model_rpc_rejects_other_models():
    from watchdog.gui.api import onboarding
    from watchdog.gui.rpc import RpcError
    with pytest.raises(RpcError):
        onboarding.download_model("gliner")


# ── real decoding (PyAV) ─────────────────────────────────────────────────────────────

def _wav(path: Path, seconds: float, rate: int = 22050) -> Path:
    import math
    n = int(seconds * rate)
    frames = bytearray()
    for i in range(n):
        v = int(8000 * math.sin(2 * math.pi * 440 * i / rate)) if (i // rate) % 2 == 0 else 0
        frames += int(v).to_bytes(2, "little", signed=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return path


def test_probe_and_windowed_decode(tmp_path, monkeypatch):
    pytest.importorskip("av")
    f = _wav(tmp_path / "tone.wav", 5.0)
    info = transcribe.probe(f)
    assert info["kind"] == "audio" and info["has_audio"] and abs(info["duration"] - 5.0) < 0.05
    monkeypatch.setattr(transcribe, "CUT_SEARCH_SECONDS", 1)
    windows = list(transcribe.decode_windows(f, window_seconds=2))
    assert len(windows) >= 2
    offsets = [o for o, _ in windows]
    assert offsets == sorted(offsets) and offsets[0] == 0
    total = sum(len(a) for _, a in windows)
    assert abs(total - 5 * transcribe.SAMPLE_RATE) < transcribe.SAMPLE_RATE // 10
    # each later window starts where the previous one ended
    for (o1, a1), (o2, _a2) in zip(windows, windows[1:]):
        assert abs(o1 + len(a1) / transcribe.SAMPLE_RATE - o2) < 1e-6


def test_probe_rejects_a_file_that_is_not_media(tmp_path):
    pytest.importorskip("av")
    f = tmp_path / "fake.mp3"
    f.write_text("this is text")
    with pytest.raises(transcribe.MediaError):
        transcribe.probe(f)


def test_silent_video_is_detected(tmp_path):
    av = pytest.importorskip("av")
    import numpy as np
    path = tmp_path / "silent.mp4"
    with av.open(str(path), "w") as c:
        try:
            s = c.add_stream("libx264", rate=10)
        except Exception:  # noqa: BLE001
            s = c.add_stream("mpeg4", rate=10)
        s.width, s.height, s.pix_fmt = 64, 48, "yuv420p"
        for i in range(10):
            for p in s.encode(av.VideoFrame.from_ndarray(np.zeros((48, 64, 3), np.uint8), format="rgb24")):
                c.mux(p)
        for p in s.encode(None):
            c.mux(p)
    info = transcribe.probe(path)
    assert info == {"kind": "video", "duration": info["duration"], "has_audio": False}


def test_file_metadata_reads_container_tags_without_ffprobe(tmp_path, monkeypatch):
    av = pytest.importorskip("av")
    import numpy as np
    from watchdog.pipeline import file_metadata
    path = tmp_path / "interview.m4a"
    with av.open(str(path), "w", format="mp4") as c:
        c.metadata["title"] = "Interview with the clerk"
        c.metadata["artist"] = "Field recorder"
        a = c.add_stream("aac", rate=16000)
        a.layout = "mono"
        fs = a.codec_context.frame_size or 1024
        fmt = a.codec_context.format.name
        for i in range(20):
            fr = av.AudioFrame.from_ndarray(np.zeros((1, fs), np.float32), format=fmt, layout="mono")
            fr.sample_rate, fr.pts = 16000, i * fs
            for p in a.encode(fr):
                c.mux(p)
        for p in a.encode(None):
            c.mux(p)
    monkeypatch.setattr(file_metadata.shutil, "which", lambda name: None)
    meta = file_metadata.extract(path)
    assert meta["title"] == "Interview with the clerk"
    assert meta["author"] == "Field recorder"
    assert meta["duration_seconds"] > 0


# ── a real model, when one is installed ──────────────────────────────────────────────

@pytest.mark.slow
def test_real_transcription_of_silence_is_empty(tmp_path):
    """Opt-in (`pytest -m slow`): needs faster-whisper and the small model in the cache."""
    pytest.importorskip("faster_whisper")
    if not transcribe.model_cached("small"):
        pytest.skip("the small transcription model is not downloaded")
    f = tmp_path / "quiet.wav"
    with wave.open(str(f), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000 * 3)
    out = transcribe.transcribe(f, model_name="small", language="en")
    assert out["segments"] == [] and abs(out["duration"] - 3.0) < 0.1
