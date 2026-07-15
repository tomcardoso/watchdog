"""Skip-at-chew for exact duplicates (#146): a file already ingested, already queued, or
repeated within the batch is moved aside before OCR rather than re-processed."""

import json
from pathlib import Path

from watchdog.pipeline.preprocess import sha256_file
from watchdog.pipeline.preprocess_batch import _filter_already_seen


def _vault(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    (v / "_INCOMING").mkdir(parents=True)
    (v / ".watchdog" / "queue").mkdir(parents=True)
    (v / ".watchdog" / "registry").mkdir(parents=True)
    return v


def test_filter_skips_ingested_queued_and_intrabatch(tmp_path):
    v = _vault(tmp_path)
    incoming = v / "_INCOMING"
    queue = v / ".watchdog" / "queue"

    ingested = incoming / "ingested.txt"
    ingested.write_text("already in the vault")
    queued = incoming / "queued.txt"
    queued.write_text("waiting in the queue")
    fresh = incoming / "fresh.txt"
    fresh.write_text("brand new content")
    dup1 = incoming / "dup1.txt"
    dup1.write_text("same bytes")
    dup2 = incoming / "dup2.txt"
    dup2.write_text("same bytes")   # identical to dup1

    (v / ".watchdog" / "registry" / "documents.json").write_text(
        json.dumps({sha256_file(ingested): {"filename": "ingested.txt"}}))
    (queue / f"{sha256_file(queued)}.json").write_text("{}")

    kept = _filter_already_seen([ingested, queued, fresh, dup1, dup2], v, incoming, queue)

    assert {f.name for f in kept} == {"fresh.txt", "dup1.txt"}        # first occurrence survives
    assert {p.name for p in (incoming / "_SKIPPED").glob("*")} == {
        "ingested.txt", "queued.txt", "dup2.txt"}
    # Kept files stay put in _INCOMING; skipped ones are moved out.
    assert fresh.exists() and dup1.exists()
    assert not ingested.exists() and not queued.exists() and not dup2.exists()


def test_filter_keeps_everything_when_nothing_seen(tmp_path):
    v = _vault(tmp_path)
    incoming = v / "_INCOMING"
    queue = v / ".watchdog" / "queue"
    a = incoming / "a.txt"
    a.write_text("one")
    b = incoming / "b.txt"
    b.write_text("two")

    kept = _filter_already_seen([a, b], v, incoming, queue)
    assert {f.name for f in kept} == {"a.txt", "b.txt"}
    assert not (incoming / "_SKIPPED").exists()
