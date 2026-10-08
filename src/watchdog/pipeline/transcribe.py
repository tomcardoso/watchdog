"""Local speech-to-text for audio and video files (D273).

Pre-processing turns a recording into the same shape every other document takes: a list of
"pages" of text. A page is a fixed block of time (`PAGE_SECONDS`, five minutes), so page 3 of a
recording is always 10:00 to 15:00 and a fact's page citation doubles as a place in the recording.
Inside a page, each run of speech starts with a `[hh:mm:ss]` timestamp.

The engine is faster-whisper (Whisper weights run on CTranslate2, int8 on the CPU). Audio is
decoded with PyAV, whose wheels carry their own FFmpeg libraries, so no system ffmpeg is needed.
Everything here runs on this computer: nothing is sent anywhere while a file is transcribed. The
only network use is the one-time model download (`download_model`), from the Hugging Face hub into
the same cache the other local models use.

`assemble_pages` and the formatting helpers are pure functions and carry the logic worth testing;
`transcribe` and `decode_windows` are thin wrappers around the two libraries.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Iterable, Iterator

AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac"}
VIDEO_SUFFIXES = {".mp4", ".m4v", ".mov", ".avi", ".webm", ".mkv"}
MEDIA_SUFFIXES = AUDIO_SUFFIXES | VIDEO_SUFFIXES

# Five-minute pages (D273). Spoken English runs at roughly 130-160 words a minute, so a page holds
# about 650-800 words, close to a dense printed page: small enough that "page 4" narrows a citation
# to one stretch of the recording, large enough that a two-hour hearing is 24 pages rather than
# hundreds. Sectioning packs pages greedily under the token budget, so the block size does not
# change how many extraction calls a long recording takes.
PAGE_SECONDS = 300

SAMPLE_RATE = 16000                 # what Whisper expects
WINDOW_SECONDS = 20 * 60            # audio is transcribed in windows of at most this length...
CUT_SEARCH_SECONDS = 20             # ...cut at the quietest point in the window's last stretch
LINE_MAX_SECONDS = 60               # a timestamped line never runs longer than this
LINE_SOFT_SECONDS = 25              # past this, a line ends at the next sentence end
PAUSE_SECONDS = 1.5                 # a pause this long after a sentence end starts a new line

# Model choices offered in Settings. `size_mb` is the download (the weights are stored as float16
# and quantized to int8 when loaded), read from the Hugging Face listing on 2026-10-07; the
# download itself asks the hub for the real figure. Every repository is MIT-licensed, as are
# OpenAI's Whisper weights they are converted from.
MODELS: dict[str, dict] = {
    "medium": {"repo": "Systran/faster-whisper-medium", "size_mb": 1531, "multilingual": True},
    "large-v3-turbo": {"repo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo", "size_mb": 1622,
                       "multilingual": True},
    "small": {"repo": "Systran/faster-whisper-small", "size_mb": 486, "multilingual": True},
    "distil-large-v3": {"repo": "Systran/faster-distil-whisper-large-v3", "size_mb": 1516,
                        "multilingual": False},
}
DEFAULT_MODEL = "medium"   # the most accurate on names and noisy audio in our tests (D273)
# The files faster-whisper loads (its own download_model uses the same list).
_ALLOW_PATTERNS = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json",
                   "vocabulary.*"]

# Whisper's language codes, for validating `transcription_language`. "auto" detects per recording.
LANGUAGES = (
    "af am ar as az ba be bg bn bo br bs ca cs cy da de el en es et eu fa fi fo fr gl gu ha haw he "
    "hi hr ht hu hy id is it ja jw ka kk km kn ko la lb ln lo lt lv mg mi mk ml mn mr ms mt my ne "
    "nl nn no oc pa pl ps pt ro ru sa sd si sk sl sn so sq sr su sv sw ta te tg th tk tl tr tt uk "
    "ur uz vi yi yo yue zh"
).split()

_TIMESTAMP_RE = re.compile(r"\[(\d{1,2}):(\d{2}):(\d{2})\]")
_SENTENCE_END_RE = re.compile(r"[.?!…][\"'”’)\]]*$")


class MediaError(Exception):
    """A recording that cannot be transcribed, with a message fit to show the person."""


def is_media(path: Path) -> bool:
    return path.suffix.lower() in MEDIA_SUFFIXES


# ── settings ──────────────────────────────────────────────────────────────────────────

def configured_model(config: dict | None = None) -> str:
    if config is None:
        from watchdog import config as user_config
        config = user_config.read()
    name = config.get("transcription_model") or DEFAULT_MODEL
    return name if name in MODELS else DEFAULT_MODEL


def configured_language(config: dict | None = None) -> str | None:
    """The language code to transcribe in, or None to detect it from each recording."""
    if config is None:
        from watchdog import config as user_config
        config = user_config.read()
    code = (config.get("transcription_language") or "auto").strip().lower()
    return code if code in LANGUAGES else None


# ── formatting ────────────────────────────────────────────────────────────────────────

def fmt_timestamp(seconds: float) -> str:
    """`[hh:mm:ss]`'s inside, always with hours, so every timestamp in a transcript sorts and
    reads the same way."""
    s = max(0, int(seconds))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def parse_timestamp(text: str) -> float | None:
    m = _TIMESTAMP_RE.search(text)
    if not m:
        return None
    h, mi, s = (int(g) for g in m.groups())
    return float(h * 3600 + mi * 60 + s)


def page_range(page: int, duration: float, page_seconds: int = PAGE_SECONDS) -> tuple[float, float]:
    start = float((page - 1) * page_seconds)
    return start, float(min(page * page_seconds, max(duration, start)))


def _lines(segments: list[tuple[float, float, str]]) -> list[tuple[float, str]]:
    """Join consecutive segments into timestamped lines.

    Whisper's segments break wherever its 30-second window or a breath falls, often mid-sentence.
    A timestamp inside a sentence would split it for everything downstream that reads sentences
    (quote checking expands a quote to its whole sentence, and treats a line break as the end of
    one), so a new line starts only at a sentence end that is followed by a pause or comes after
    `LINE_SOFT_SECONDS` of speech, or unconditionally once a line reaches `LINE_MAX_SECONDS`
    (speech with no punctuation at all would otherwise never break).
    """
    out: list[tuple[float, str]] = []
    start: float | None = None
    parts: list[str] = []
    prev_end = 0.0
    for seg_start, seg_end, text in segments:
        text = " ".join(text.split())
        if not text:
            continue
        if start is not None:
            joined = " ".join(parts)
            ended = bool(_SENTENCE_END_RE.search(joined))
            length = seg_start - start
            pause = seg_start - prev_end
            if length >= LINE_MAX_SECONDS or (ended and (pause >= PAUSE_SECONDS or length >= LINE_SOFT_SECONDS)):
                out.append((start, joined))
                start, parts = None, []
        if start is None:
            start = seg_start
        parts.append(text)
        prev_end = seg_end
    if start is not None:
        out.append((start, " ".join(parts)))
    return out


def assemble_pages(segments: Iterable[tuple[float, float, str]], duration: float,
                   page_seconds: int = PAGE_SECONDS) -> tuple[list[dict], list[dict]]:
    """Turn timed segments into (pages, page_ranges).

    Page n covers [(n-1) * page_seconds, n * page_seconds). A segment belongs to the page its
    start falls in, so a sentence that runs across a page boundary is kept whole on the earlier
    page. A block with no speech gets no page: page numbers stay tied to time (page 4 is always
    15:00 to 20:00), so they can skip, exactly as a document's pages can when a scanned page is
    blank. `page_ranges` gives each page's start and end in seconds, the end clipped to the
    recording's duration.
    """
    by_page: dict[int, list[tuple[float, float, str]]] = {}
    for seg in sorted(segments, key=lambda s: s[0]):
        start, end, text = float(seg[0]), float(seg[1]), str(seg[2])
        if not text.strip():
            continue
        by_page.setdefault(int(start // page_seconds) + 1, []).append((start, end, text))
    pages, ranges = [], []
    for n in sorted(by_page):
        lines = _lines(by_page[n])
        markdown = "\n\n".join(f"[{fmt_timestamp(t)}] {text}" for t, text in lines)
        pages.append({"page": n, "markdown": markdown})
        last_end = max(e for _s, e, _t in by_page[n])
        start, end = page_range(n, max(duration, last_end), page_seconds)
        ranges.append({"page": n, "start": start, "end": end})
    return pages, ranges


# ── the recording ─────────────────────────────────────────────────────────────────────

def _is_cover_art(stream) -> bool:
    try:
        import av
        return bool(stream.disposition & av.stream.Disposition.attached_pic)
    except Exception:  # noqa: BLE001 — older PyAV: treat every video stream as video
        return False


def probe(path: Path) -> dict:
    """{"kind": "audio"|"video", "duration": seconds|None, "has_audio": bool} from the container
    header, without decoding. Raises MediaError when the file can't be opened at all."""
    import av
    try:
        container = av.open(str(path))
    except Exception as e:  # noqa: BLE001
        raise MediaError(f"This file could not be read as audio or video ({e}).") from e
    with container:
        video = [s for s in container.streams.video if not _is_cover_art(s)]
        has_audio = bool(container.streams.audio)
        duration = container.duration / 1_000_000 if container.duration else None
        if duration is None and has_audio:
            s = container.streams.audio[0]
            if s.duration and s.time_base:
                duration = float(s.duration * s.time_base)
    return {"kind": "video" if video else "audio", "duration": duration, "has_audio": has_audio}


def _quietest_cut(audio, lo: int, hi: int) -> int:
    """Sample index of the quietest 0.2 s frame in audio[lo:hi], where a cut splits no word."""
    import numpy as np
    frame = SAMPLE_RATE // 5
    region = audio[lo:hi]
    n = len(region) // frame
    if n < 2:
        return hi
    energy = np.square(region[: n * frame].reshape(n, frame)).mean(axis=1)
    return lo + int(np.argmin(energy)) * frame + frame // 2


def decode_windows(path: Path, window_seconds: int = WINDOW_SECONDS) -> Iterator[tuple[float, object]]:
    """Yield (offset_seconds, mono float32 16 kHz samples) windows of the first audio track.

    Streaming keeps memory flat whatever the length: a six-hour hearing never sits in memory
    whole. Each window is cut at the quietest point of its last `CUT_SEARCH_SECONDS`, so a word
    is not split between two windows.
    """
    import av
    import numpy as np
    window = window_seconds * SAMPLE_RATE
    search = CUT_SEARCH_SECONDS * SAMPLE_RATE
    with av.open(str(path)) as container:
        if not container.streams.audio:
            raise MediaError("This recording has no sound track, so there is nothing to transcribe.")
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
        buf: list = []
        have = 0
        offset = 0

        def frames():
            for frame in container.decode(stream):
                yield from resampler.resample(frame)
            yield from resampler.resample(None)

        for out in frames():
            chunk = out.to_ndarray().reshape(-1).astype(np.float32) / 32768.0
            buf.append(chunk)
            have += len(chunk)
            if have >= window + search:
                audio = np.concatenate(buf)
                cut = _quietest_cut(audio, window - search, window)
                yield offset / SAMPLE_RATE, audio[:cut]
                offset += cut
                rest = audio[cut:]
                buf, have = [rest], len(rest)
        if have:
            yield offset / SAMPLE_RATE, np.concatenate(buf)


# ── the model ─────────────────────────────────────────────────────────────────────────

def model_repo(name: str) -> str:
    return MODELS.get(name, MODELS[DEFAULT_MODEL])["repo"]


def _repo_cache_dir(repo: str) -> Path:
    from watchdog.gui.engine_setup import _hf_cache
    return _hf_cache() / ("models--" + repo.replace("/", "--"))


def model_cached(name: str) -> bool:
    """True when the model's weights and tokenizer are already in the Hugging Face cache."""
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        return False
    repo = model_repo(name)
    for filename in ("model.bin", "config.json", "tokenizer.json"):
        try:
            hit = try_to_load_from_cache(repo, filename)
        except Exception:  # noqa: BLE001
            return False
        if not isinstance(hit, str):
            return False
    return True


def download_size(name: str) -> int:
    """Bytes the model's download takes, from the hub when it answers, else the catalogue."""
    try:
        from huggingface_hub import HfApi
        import fnmatch
        info = HfApi().model_info(model_repo(name), files_metadata=True)
        total = sum(s.size or 0 for s in info.siblings or []
                    if any(fnmatch.fnmatch(s.rfilename, p) for p in _ALLOW_PATTERNS))
        if total:
            return total
    except Exception:  # noqa: BLE001
        pass
    return MODELS.get(name, MODELS[DEFAULT_MODEL])["size_mb"] * 1_000_000


def _dir_bytes(root: Path) -> int:
    total = 0
    try:
        for p in (root / "blobs").iterdir():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return total


def download_model(name: str, on_progress: Callable[[int, int], None] | None = None,
                   poll: float = 0.5) -> None:
    """Fetch the model into the Hugging Face cache, calling on_progress(done_bytes, total_bytes)
    as it arrives. Progress is read off the cache folder's size rather than the library's
    progress bars, whose hooks change between huggingface_hub versions."""
    from huggingface_hub import snapshot_download
    repo = model_repo(name)
    total = download_size(name)
    cache = _repo_cache_dir(repo)
    base = _dir_bytes(cache)
    result: dict = {}

    def run() -> None:
        try:
            snapshot_download(repo, allow_patterns=_ALLOW_PATTERNS)
        except BaseException as e:  # noqa: BLE001
            result["error"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    while t.is_alive():
        t.join(poll)
        if on_progress:
            on_progress(min(total, max(0, _dir_bytes(cache) - base)), total)
    if "error" in result:
        raise result["error"]
    if on_progress:
        on_progress(total, total)


def download_label(name: str, total_bytes: int | None = None) -> str:
    mb = round((total_bytes if total_bytes is not None else MODELS[name]["size_mb"] * 1_000_000) / 1_000_000)
    return f"Downloading the transcription model ({mb:,} MB)"


def ensure_model(name: str, emit: Callable[..., None] | None = None) -> None:
    """Download the model unless it is already cached, reporting progress as `model` events."""
    if model_cached(name):
        return
    total = download_size(name)
    label = download_label(name, total)
    last = [0.0]

    def report(done: int, tot: int) -> None:
        now = time.time()
        if emit and (now - last[0] >= 0.5 or done >= tot):
            last[0] = now
            emit("model", name="transcription", label=label, state="progress",
                 done=round(done / 1_000_000), total=round(tot / 1_000_000))

    if emit:
        emit("model", name="transcription", label=label, state="start",
             done=0, total=round(total / 1_000_000))
    try:
        download_model(name, report)
    except Exception as e:  # noqa: BLE001
        if emit:
            emit("model", name="transcription", label=label, state="failed", detail=str(e)[:300],
                 done=None, total=None)
        raise MediaError(
            "The transcription model could not be downloaded. Check the internet connection and "
            "add the file again, or download the model ahead of time in Settings, under Setup."
        ) from e
    if emit:
        emit("model", name="transcription", label=label, state="done",
             done=round(total / 1_000_000), total=round(total / 1_000_000))


def _cpu_threads() -> int:
    from watchdog.pipeline.preprocess import _perf_cpu_count
    return max(1, _perf_cpu_count())


def load_model(name: str):
    from faster_whisper import WhisperModel
    return WhisperModel(model_repo(name), device=DECODE_SETTINGS["device"],
                        compute_type=DECODE_SETTINGS["compute_type"],
                        cpu_threads=_cpu_threads(), local_files_only=True)


# ── transcription ─────────────────────────────────────────────────────────────────────

# How every recording is decoded. Stored with the model in each transcript's metadata
# (`media_record`), so a later model or setting can be compared with what produced a transcript.
DECODE_SETTINGS = {"device": "cpu", "compute_type": "int8", "beam_size": 5, "vad_filter": True,
                   "language_detection_segments": 3}

# Version of the `media` block in a transcript's processing metadata and the registry. Bump it
# when a field changes meaning; adding a field needs no bump, since readers ignore what they
# don't know.
MEDIA_FORMAT = 1

def transcribe(path: Path, *, model_name: str, language: str | None,
               on_progress: Callable[[float, float | None], None] | None = None,
               duration_hint: float | None = None) -> dict:
    """Transcribe the first audio track. Returns {"segments": [(start, end, text)], "language",
    "language_probability", "duration"}; on_progress(position_seconds, duration_seconds)."""
    model = load_model(model_name)
    segments: list[tuple[float, float, str]] = []
    detected, probability = language, None
    decoded = 0.0
    for offset, audio in decode_windows(path):
        decoded = offset + len(audio) / SAMPLE_RATE
        if len(audio) < SAMPLE_RATE // 2:      # under half a second: nothing to hear
            continue
        pieces, info = model.transcribe(
            audio, language=detected, beam_size=DECODE_SETTINGS["beam_size"],
            vad_filter=DECODE_SETTINGS["vad_filter"],
            language_detection_segments=DECODE_SETTINGS["language_detection_segments"],
        )
        if detected is None:
            # Detected once, from the first window, then held for the rest of the recording.
            detected, probability = info.language, info.language_probability
        for seg in pieces:
            segments.append((offset + seg.start, offset + seg.end, seg.text))
            if on_progress:
                on_progress(offset + seg.end, duration_hint)
    return {"segments": segments, "language": detected, "language_probability": probability,
            "duration": max(decoded, duration_hint or 0.0)}


def _model_revision(name: str) -> str | None:
    """The hub commit of the cached model, read off its snapshot folder."""
    try:
        from huggingface_hub import try_to_load_from_cache
        hit = try_to_load_from_cache(model_repo(name), "model.bin")
        return Path(hit).parent.name if isinstance(hit, str) else None
    except Exception:  # noqa: BLE001
        return None


def _engine_version() -> str | None:
    try:
        from importlib.metadata import version
        return version("faster-whisper")
    except Exception:  # noqa: BLE001
        return None


def media_record(*, kind: str, duration: float, ranges: list[dict], language: str | None,
                 language_probability: float | None, model_name: str,
                 language_setting: str | None) -> dict:
    """The versioned `media` block (format MEDIA_FORMAT): what the app needs to show a recording
    (kind, duration, each page's time range) and everything that produced its transcript."""
    return {
        "format": MEDIA_FORMAT,
        "kind": kind,
        "duration_seconds": round(duration, 2),
        "page_seconds": PAGE_SECONDS,
        "pages": ranges,
        "language": language,
        "language_probability": (round(language_probability, 3)
                                 if language_probability is not None else None),
        "transcription": {
            "engine": "faster-whisper",
            "engine_version": _engine_version(),
            "model": model_name,
            "model_repo": model_repo(model_name),
            "model_revision": _model_revision(model_name),
            "language_setting": language_setting or "auto",
            **DECODE_SETTINGS,
        },
    }


def process_media(path: Path, emit: Callable[..., None] | None = None) -> dict:
    """Pre-process one recording into the preprocess.py result shape, or {"error": str}."""
    from watchdog.pipeline.preprocess import sha256_file
    try:
        info = probe(path)
        if not info["has_audio"]:
            if info["kind"] == "video":
                raise MediaError("This video has no sound track, so there is nothing to transcribe.")
            raise MediaError("This file has no sound track, so there is nothing to transcribe.")
        try:
            import faster_whisper  # noqa: F401
        except ImportError as e:
            raise MediaError("Transcription is not installed in this copy of Watchdog. Repair the "
                             "engine in Settings, under Setup.") from e
        name = configured_model()
        language = configured_language()
        ensure_model(name, emit)
        last = [0.0]

        def progress(position: float, duration: float | None) -> None:
            now = time.time()
            if emit and now - last[0] >= 2:
                last[0] = now
                emit("transcribe", name=path.name, position=round(position), duration=round(duration) if duration else None)

        result = transcribe(path, model_name=name, language=language,
                            on_progress=progress, duration_hint=info["duration"])
    except MediaError as e:
        return {"error": str(e)}
    except Exception as e:  # noqa: BLE001
        return {"error": f"Transcription failed: {e}"}

    duration = result["duration"]
    pages, ranges = assemble_pages(result["segments"], duration)
    metadata = {
        "ocr_used": False,
        "garbled_detected": False,
        "source_type": "transcript",
        "chunked": False,
        "media": media_record(kind=info["kind"], duration=duration, ranges=ranges,
                              language=result["language"],
                              language_probability=result["language_probability"],
                              model_name=name, language_setting=language),
    }
    return {
        "filename": path.name,
        "sha256": sha256_file(path),
        "page_count": max((p["page"] for p in pages), default=0),
        "pages": pages,
        "metadata": metadata,
    }


def stderr_emitter(kind: str, **fields) -> None:
    """Progress from the pre-processing subprocess. Its stdout carries only the JSON result, so
    progress goes to stderr with the same prefix `watchdog.progress` uses, and the parent
    (preprocess_batch.preprocess_one) forwards each line to its own progress stream."""
    from watchdog import progress
    if not progress.enabled():
        return
    try:
        sys.stderr.write(progress.PREFIX + json.dumps({"kind": kind, **fields}, ensure_ascii=False) + "\n")
        sys.stderr.flush()
    except Exception:  # noqa: BLE001
        pass


def media_timeout(path: Path, default: int) -> int:
    """Seconds to allow one recording: the normal per-file limit, or four times its length plus
    ten minutes when that is longer, so a slow computer finishing a long hearing is not cut off.
    The limit guards against a hung process, not a slow one."""
    try:
        duration = probe(path)["duration"] or 0
    except Exception:  # noqa: BLE001
        duration = 0
    return max(default, int(duration * 4) + 600)


def os_environ_quiet() -> None:
    """Keep the hub's progress bars out of logs; this module reports its own progress."""
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
