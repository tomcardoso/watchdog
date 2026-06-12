"""Tests for pipeline/classify.py — monkeypatches out fastembed."""

import json
import numpy as np
import pytest
from pathlib import Path

from watchdog.pipeline import embed as embed_mod
import watchdog.pipeline.classify as cls_mod
from watchdog.pipeline.classify import classify_document, _load_or_build, _skill_hash


# ── Fake embedder ─────────────────────────────────────────────────────────────

class _FakeEmbedder:
    """Deterministic embedder: texts containing a keyword get a distinct unit vector."""
    DIM = 4
    _KEYWORDS = ["court", "corporate", "land", "tax"]

    def __init__(self):
        self.call_count = 0

    def embed(self, texts):
        self.call_count += 1
        for text in texts:
            v = np.zeros(self.DIM, dtype=np.float32)
            tl = text.lower()
            for i, kw in enumerate(self._KEYWORDS):
                if kw in tl:
                    v[i] = 1.0
                    break
            else:
                v[self.DIM - 1] = 1.0
            yield v


@pytest.fixture(autouse=True)
def fake_embedder(monkeypatch):
    fe = _FakeEmbedder()
    monkeypatch.setattr(embed_mod, "_embedder", fe)
    return fe


# ── Vault helpers ─────────────────────────────────────────────────────────────

def _make_vault(tmp_path: Path) -> tuple[Path, Path]:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / ".watchdog").mkdir()
    skills_dir = vault / ".claude" / "commands" / "records"
    skills_dir.mkdir(parents=True)
    return vault, skills_dir


def _add_skill(skills_dir: Path, name: str, content: str) -> None:
    (skills_dir / f"{name}.md").write_text(content)


def _pages(*texts: str) -> list[dict]:
    return [{"page": i + 1, "markdown": t} for i, t in enumerate(texts)]


# ── classify_document ─────────────────────────────────────────────────────────

def test_empty_pages_returns_none(tmp_path):
    vault, skills_dir = _make_vault(tmp_path)
    _add_skill(skills_dir, "court-documents", "court affidavit judgment")
    assert classify_document([], vault) is None


def test_no_skills_returns_none(tmp_path):
    vault, _ = _make_vault(tmp_path)
    assert classify_document(_pages("court affidavit"), vault) is None


def test_returns_winning_skill(tmp_path):
    vault, skills_dir = _make_vault(tmp_path)
    _add_skill(skills_dir, "court-documents", "court affidavit judgment")
    _add_skill(skills_dir, "corporate-filings", "corporate director registration")

    result = classify_document(_pages("court affidavit proceedings"), vault)
    assert result == "court-documents"


def test_below_threshold_returns_none(tmp_path, monkeypatch):
    vault, skills_dir = _make_vault(tmp_path)
    _add_skill(skills_dir, "court-documents", "court affidavit judgment")
    monkeypatch.setattr(cls_mod, "_THRESHOLD", 2.0)  # impossible to exceed
    assert classify_document(_pages("court affidavit"), vault) is None


def test_uses_only_first_n_pages(tmp_path, monkeypatch):
    vault, skills_dir = _make_vault(tmp_path)
    _add_skill(skills_dir, "court-documents", "court affidavit judgment")
    _add_skill(skills_dir, "corporate-filings", "corporate director registration")
    monkeypatch.setattr(cls_mod, "_CLASSIFY_PAGES", 2)

    # First 2 pages are court; later pages are corporate — should still pick court
    pages = _pages("court affidavit", "court judgment", "corporate director", "corporate filing")
    assert classify_document(pages, vault) == "court-documents"


def test_skips_underscore_skills(tmp_path):
    vault, skills_dir = _make_vault(tmp_path)
    _add_skill(skills_dir, "_template", "court affidavit template")
    _add_skill(skills_dir, "court-documents", "court affidavit judgment")

    result = classify_document(_pages("court affidavit"), vault)
    assert result == "court-documents"


# ── cache ─────────────────────────────────────────────────────────────────────

def test_cache_written_after_first_call(tmp_path):
    vault, skills_dir = _make_vault(tmp_path)
    _add_skill(skills_dir, "court-documents", "court affidavit")

    classify_document(_pages("court"), vault)

    npy = vault / ".watchdog" / "skill-embeddings.npy"
    meta = vault / ".watchdog" / "skill-embeddings-meta.json"
    assert npy.exists()
    assert meta.exists()
    assert json.loads(meta.read_text())["skill_names"] == ["court-documents"]


def test_cache_hit_skips_skill_embedding(tmp_path, fake_embedder):
    vault, skills_dir = _make_vault(tmp_path)
    _add_skill(skills_dir, "court-documents", "court affidavit")

    classify_document(_pages("court"), vault)
    calls_after_first = fake_embedder.call_count

    classify_document(_pages("court"), vault)
    # Second call: only pages embedded (skills served from cache)
    assert fake_embedder.call_count == calls_after_first + 1


def test_cache_invalidated_when_skill_changes(tmp_path):
    vault, skills_dir = _make_vault(tmp_path)
    skill_file = skills_dir / "court-documents.md"
    skill_file.write_text("court affidavit")

    classify_document(_pages("court"), vault)
    meta_before = json.loads((vault / ".watchdog" / "skill-embeddings-meta.json").read_text())

    skill_file.write_text("court affidavit updated content")
    classify_document(_pages("court"), vault)
    meta_after = json.loads((vault / ".watchdog" / "skill-embeddings-meta.json").read_text())

    assert meta_before["skills_hash"] != meta_after["skills_hash"]


# ── _skill_hash ───────────────────────────────────────────────────────────────

def test_skill_hash_excludes_underscore_files(tmp_path):
    vault, skills_dir = _make_vault(tmp_path)
    _add_skill(skills_dir, "court-documents", "content")

    h1 = _skill_hash(vault)
    _add_skill(skills_dir, "_template", "irrelevant")
    h2 = _skill_hash(vault)
    assert h1 == h2
