"""Tests for `watchdog reindex` (#218): rebuilding `.embeddings/` from on-disk vault state
alone, with zero model calls. Fastembed is monkeypatched out (matches test_embed.py)."""

import argparse
import asyncio
import hashlib

import numpy as np
import pytest

from watchdog import model_client
from watchdog.cmd.reindex import _pages_from_morgue_text, cmd_reindex
from watchdog.pipeline import embed as embed_mod, orchestrate

from tests.test_orchestrate import _extraction, _queue_doc
from tests.test_write_vault import make_vault


class _FakeEmbedder:
    """Deterministic unit vector per text (matches test_embed.py's fake)."""
    DIM = 8

    def embed(self, texts):
        for text in texts:
            v = np.zeros(self.DIM, dtype=np.float32)
            idx = int(hashlib.md5(text.encode()).hexdigest(), 16) % self.DIM
            v[idx] = 1.0
            yield v


@pytest.fixture(autouse=True)
def fake_embedder(monkeypatch):
    monkeypatch.setattr(embed_mod, "_embedder", _FakeEmbedder())


@pytest.fixture(autouse=True)
def no_rerank(monkeypatch):
    monkeypatch.setattr(embed_mod, "_rerank_enabled", lambda: False)


def _args(project=None):
    return argparse.Namespace(project=project)


def _ingest_one_doc(vault, monkeypatch):
    """Run a real (mocked-model) ingest so documents.json/entities.json/morgue text and
    .embeddings/ all reflect a genuine write_vault pass — ground truth to reindex against."""
    _queue_doc(vault, text="Acme Corp filed an annual report.")

    async def fake(*, task, prompt, schema, model=None, backend=None, max_retries=1, effort=None):
        parsed = {
            "classify": {"skill": "general-records.md"},
            "extract": _extraction(),
            "briefing": {"investigation_status": "x", "what_was_ingested": []},
        }.get(task, {"entity_syntheses": []} if task == "entity-synthesis" else {"keep": []})
        return model_client.ModelResult(parsed=parsed, text="", model="m", backend="b",
                                        auth_mode="subscription", cost_usd=0.0)
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", fake)
    asyncio.run(orchestrate.run(vault))


# ── _pages_from_morgue_text ──────────────────────────────────────────────────

def test_pages_from_morgue_text_round_trips():
    text = "<!-- PAGE 1 -->\n\nfirst page text\n\n<!-- PAGE 2 -->\n\nsecond page text\n"
    assert _pages_from_morgue_text(text) == [
        {"page": 1, "markdown": "first page text"},
        {"page": 2, "markdown": "second page text"},
    ]


def test_pages_from_morgue_text_single_page():
    assert _pages_from_morgue_text("<!-- PAGE 1 -->\n\nonly page\n") == [
        {"page": 1, "markdown": "only page"}]


def test_pages_from_morgue_text_empty_string():
    assert _pages_from_morgue_text("") == []


# ── cmd_reindex ───────────────────────────────────────────────────────────────

def test_reindex_rebuilds_corpus_and_notes_matching_the_original_ingest(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _ingest_one_doc(vault, monkeypatch)

    before = embed_mod.index_stats(vault)
    assert before["passages"] > 0
    assert before["notes"] > 0

    monkeypatch.chdir(vault)
    cmd_reindex(_args())

    after = embed_mod.index_stats(vault)
    assert after == before


def test_reindex_makes_no_model_calls(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    _ingest_one_doc(vault, monkeypatch)

    def _boom(*a, **k):
        raise AssertionError("reindex must not call the model")
    monkeypatch.setattr(orchestrate.model_client, "acomplete_json", _boom)

    monkeypatch.chdir(vault)
    cmd_reindex(_args())   # would raise via the mocked acomplete_json if it ever called out


def test_reindex_rebuilds_with_a_new_embed_model_dimension(tmp_path, monkeypatch):
    """The whole point of #218: switching embed_model and reindexing rebuilds every vector
    from disk, with no re-chew."""
    vault = make_vault(tmp_path)
    _ingest_one_doc(vault, monkeypatch)

    class _BiggerEmbedder:
        DIM = 16
        def embed(self, texts):
            for _ in texts:
                yield np.ones(self.DIM, dtype=np.float32)
    monkeypatch.setattr(embed_mod, "_embedder", _BiggerEmbedder())

    monkeypatch.chdir(vault)
    cmd_reindex(_args())

    vecs, _ = embed_mod._load_all(vault)
    assert vecs.shape[1] == 16


def test_reindex_skips_documents_with_no_morgue_text_but_still_rebuilds_notes(tmp_path, monkeypatch, capsys):
    vault = make_vault(tmp_path)
    _ingest_one_doc(vault, monkeypatch)
    for md in (vault / "morgue").rglob("*.md"):
        md.unlink()

    monkeypatch.chdir(vault)
    cmd_reindex(_args())

    out = capsys.readouterr().out
    assert "skip" in out.lower()
    stats = embed_mod.index_stats(vault)
    assert stats["passages"] == 0
    assert stats["notes"] > 0


def test_reindex_errors_when_nothing_ingested(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    monkeypatch.chdir(vault)
    with pytest.raises(SystemExit, match="nothing to reindex"):
        cmd_reindex(_args())
