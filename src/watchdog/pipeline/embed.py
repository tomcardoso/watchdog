"""
Semantic embedding index for watchdog investigations.

Index lives at <vault_path>/.embeddings/:
  vectors.npy  — float32 (N, dim), L2-normalised for cosine similarity
  meta.json    — list of {filename, page, preview} parallel to vector rows

Pages are embedded individually so searches return page-level attribution.
Re-ingesting a document replaces its existing entries rather than duplicating.
"""

import json
import numpy as np
from pathlib import Path

_MODEL = "BAAI/bge-small-en-v1.5"
_PREVIEW_LEN = 300

_embedder = None


def _get_embedder():
    global _embedder
    if _embedder is None:
        from fastembed import TextEmbedding
        _embedder = TextEmbedding(_MODEL)
    return _embedder


def _dir(vault_path: Path) -> Path:
    return vault_path / ".embeddings"


def _load(vault_path: Path) -> tuple["np.ndarray | None", list[dict]]:
    d = _dir(vault_path)
    vp = d / "vectors.npy"
    mp = d / "meta.json"
    if not vp.exists() or not mp.exists():
        return None, []
    return np.load(vp), json.loads(mp.read_text())


def _save(vault_path: Path, vectors: "np.ndarray", meta: list[dict]) -> None:
    d = _dir(vault_path)
    d.mkdir(parents=True, exist_ok=True)
    np.save(d / "vectors.npy", vectors)
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False))


def _normalise(v: "np.ndarray") -> "np.ndarray":
    norms = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.where(norms == 0, 1.0, norms)


def _strip_frontmatter(content: str) -> str:
    """Remove YAML frontmatter block so only body text is embedded."""
    if content.startswith("---\n"):
        end = content.find("\n---\n", 4)
        if end != -1:
            return content[end + 5:].strip()
    return content.strip()


def _drop(existing_vecs, existing_meta, predicate):
    """Remove entries matching predicate; returns (vecs, meta) — vecs may be None."""
    keep = [i for i, m in enumerate(existing_meta) if not predicate(m)]
    if not keep:
        return None, []
    return existing_vecs[keep], [existing_meta[i] for i in keep]


def add_document(vault_path: Path, filename: str, pages: list[dict]) -> int:
    """Embed pages and add to the investigation index. Returns pages indexed."""
    embedder = _get_embedder()
    texts = [p["markdown"] for p in pages]
    new_vecs = _normalise(np.array(list(embedder.embed(texts)), dtype=np.float32))
    new_meta = [
        {"type": "page", "filename": filename, "page": p["page"], "preview": p["markdown"][:_PREVIEW_LEN]}
        for p in pages
    ]

    existing_vecs, existing_meta = _load(vault_path)
    if existing_vecs is not None:
        existing_vecs, existing_meta = _drop(
            existing_vecs, existing_meta,
            lambda m: m.get("type", "page") == "page" and m.get("filename") == filename,
        )

    vectors = new_vecs if existing_vecs is None else np.vstack([existing_vecs, new_vecs])
    _save(vault_path, vectors, existing_meta + new_meta)
    return len(pages)


def add_note(vault_path: Path, note_path: str, content: str) -> None:
    """Embed a vault note (entity or document) and add to the investigation index."""
    body = _strip_frontmatter(content)
    if not body:
        return
    embedder = _get_embedder()
    vec = _normalise(np.array(list(embedder.embed([body])), dtype=np.float32))
    new_meta = [{"type": "note", "note_path": note_path, "preview": body[:_PREVIEW_LEN]}]

    existing_vecs, existing_meta = _load(vault_path)
    if existing_vecs is not None:
        existing_vecs, existing_meta = _drop(
            existing_vecs, existing_meta,
            lambda m: m.get("type") == "note" and m.get("note_path") == note_path,
        )

    vectors = vec if existing_vecs is None else np.vstack([existing_vecs, vec])
    _save(vault_path, vectors, existing_meta + new_meta)


def search(vault_path: Path, query: str, top_n: int = 5) -> list[dict]:
    """Return top_n entries most similar to query, scored by cosine similarity."""
    vectors, meta = _load(vault_path)
    if vectors is None or not meta:
        return []
    embedder = _get_embedder()
    q = _normalise(np.array(list(embedder.embed([query])), dtype=np.float32)[0])
    scores = vectors @ q
    top_indices = np.argsort(scores)[::-1][:top_n]
    return [{**meta[i], "score": float(scores[i])} for i in top_indices]


def index_stats(vault_path: Path) -> dict:
    _, meta = _load(vault_path)
    return {"pages": len(meta)}
