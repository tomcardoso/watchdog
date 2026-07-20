"""Golden-vault parity test — the safety net for the #403 two-phase ingest refactor.

Phase 1 moves vault writes from per-document post-flight to a commit pass at finalize-start,
and its contract is that the *output is byte-identical*. This test pins that: a fixture ingest
with canned model responses is run end to end, and every file the run produces in the vault is
hashed into a manifest. Volatile content (today's date, run timestamps, durations, absolute
tmp paths) is normalized first, so the manifest is stable across runs and machines but still
sensitive to any real change in what gets written.

The manifest is committed alongside this test. Regenerate it deliberately, never reflexively:

    REGENERATE_GOLDEN=1 PYTHONPATH=src ~/.local/pipx/venvs/watchdog-intel/bin/pytest \
        tests/test_golden_vault.py

A diff here during the #403 work means the refactor changed observable output, which phase 1
says it must not.
"""

import asyncio
import hashlib
import json
import os
import re
from pathlib import Path

from watchdog.pipeline import orchestrate

from tests.test_orchestrate import _extraction, _mock, _queue_doc
from tests.test_write_vault import make_vault

GOLDEN = Path(__file__).parent / "golden" / "vault-manifest.json"

# Content that legitimately differs between two identical runs. Each is replaced with a stable
# placeholder before hashing — the field's *presence* still matters, only its value is freed.
_NORMALIZERS = [
    # Compact form first (`20260720T171406Z`, used in usage filenames) — the dashed pattern
    # below would not match it, and a stray unnormalized run timestamp reads as a file that
    # vanished plus a file that appeared.
    (re.compile(r"\d{8}T\d{6}Z"), "<TIMESTAMP>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}T[\d:.]+(?:Z|[+-]\d{2}:?\d{2})?"), "<TIMESTAMP>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}"), "<DATE>"),
    # A briefing is named `<date>-HH-MM.md`; the trailing clock time moves between runs.
    (re.compile(r"<DATE>-\d{2}-\d{2}"), "<DATE>-<TIME>"),
    # `log.md` heads each run with `<date> HH:MM`.
    (re.compile(r"<DATE> \d{2}:\d{2}"), "<DATE> <TIME>"),
    # Absolute tmp roots only — anchored at `/private/var/...` or a leading `/tmp/`, so the
    # vault-relative path `.watchdog/tmp/synthesis-result.json` is left alone.
    (re.compile(r"/private/var/folders/[^\s\"']+|(?<![\w.])/tmp/[^\s\"']+"), "<TMPPATH>"),
    (re.compile(r"\b\d+\.\d+s\b"), "<DURATION>"),
    (re.compile(r"\$\d+\.\d{2,6}\b"), "<COST>"),
]

# Paths whose content is inherently run-ordered, binary-unstable, or diagnostic rather than part
# of the vault's committed state. Their existence is recorded; their bytes are not.
# `.fulltext/index.db` is SQLite: internal page layout and rowids shift between byte-identical
# logical contents, so hashing it would fail for reasons unrelated to what was indexed.
_CONTENT_EXEMPT = ("ingest.log", "usage/", ".write-lock", "/tmp/", ".fulltext/index.db")


def _normalize(text: str) -> str:
    for pattern, replacement in _NORMALIZERS:
        text = pattern.sub(replacement, text)
    return text


def _manifest(vault: Path) -> dict:
    """Relative path -> sha256 of normalized content (or "<exempt>") for every file in the vault.

    Paths are normalized as well as content: a usage record and a briefing both carry the run's
    timestamp in their *filename*, which would otherwise read as a file disappearing and a new
    one appearing on every run."""
    out = {}
    for path in sorted(vault.rglob("*")):
        if not path.is_file():
            continue
        rel = _normalize(path.relative_to(vault).as_posix())
        if any(marker in rel for marker in _CONTENT_EXEMPT):
            out[rel] = "<exempt>"
            continue
        try:
            content = _normalize(path.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            content = path.read_bytes().hex()
        out[rel] = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    return out


def _run_fixture_ingest(tmp_path, monkeypatch) -> Path:
    """A two-document fixture ingest, model mocked, run end to end through the real
    preflight/post-flight/write_vault/finalize path.

    `concurrency=1` is load-bearing, not incidental. Under the default concurrency the two
    documents race, and whichever extraction finishes first writes first — so `appears_in`
    ordering in the registry (and the fragment order in the entity note) flips between
    otherwise identical runs. Serializing pins that order so the manifest is comparable at all.
    The underlying nondeterminism is a property of the current per-document write path; #403
    phase 1, which serializes writes into a single commit pass, is what removes it.
    """
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha="a" * 64, filename="alpha.pdf",
               text="Acme Corp filed an annual report on January 15, 2024 disclosing $4,500,000.")
    _queue_doc(vault, sha="b" * 64, filename="beta.pdf",
               text="Acme Corp received a loan of $1,200,000 from Beta Bank.")
    _mock(monkeypatch, extraction=_extraction(sha="a" * 64, filename="alpha.pdf"))
    asyncio.run(orchestrate.run(vault, concurrency=1))
    return vault


def test_vault_output_matches_golden(tmp_path, monkeypatch):
    vault = _run_fixture_ingest(tmp_path, monkeypatch)
    manifest = _manifest(vault)

    if os.environ.get("REGENERATE_GOLDEN"):
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return

    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))

    missing = sorted(set(expected) - set(manifest))
    added = sorted(set(manifest) - set(expected))
    changed = sorted(p for p in set(expected) & set(manifest)
                     if expected[p] != manifest[p] and expected[p] != "<exempt>")

    assert not missing, f"vault files no longer written: {missing}"
    assert not added, f"vault files newly written: {added}"
    assert not changed, f"vault file content changed: {changed}"
