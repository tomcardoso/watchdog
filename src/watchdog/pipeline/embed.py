"""
Semantic embedding index for watchdog investigations.

Index lives at <vault_path>/.embeddings/:
  docs/{sha16}.npy   — float32 (n_pages, dim), L2-normalised — one file per document
  docs/{sha16}.json  — metadata list for that document
  notes/{id}.npy     — float32 (1, dim) per entity/document note
  notes/{id}.json    — metadata list for that note

Re-ingesting a document or note overwrites only its own files — no full-index rewrite.

Vaults using the legacy monolithic format (vectors.npy + meta.json) are migrated
automatically on first access.
"""

import hashlib
import json
import os
import re
import numpy as np
from pathlib import Path

# Pin fastembed cache to a persistent location — fastembed 0.8+ defaults to
# tempfile.gettempdir()/fastembed_cache which is ephemeral on many systems.
os.environ.setdefault(
    "FASTEMBED_CACHE_PATH",
    str(Path.home() / ".cache" / "fastembed"),
)

_MODEL = "BAAI/bge-small-en-v1.5"
_PREVIEW_LEN = 300

_embedder = None


def _get_embedder():
    global _embedder
    if _embedder is None:
        from fastembed import TextEmbedding
        _embedder = TextEmbedding(_MODEL)
    return _embedder


def _emb_root(vault_path: Path) -> Path:
    return vault_path / ".embeddings"


def _docs_dir(vault_path: Path) -> Path:
    return _emb_root(vault_path) / "docs"


def _notes_dir(vault_path: Path) -> Path:
    return _emb_root(vault_path) / "notes"


def _doc_id(filename: str) -> str:
    return hashlib.sha256(filename.encode()).hexdigest()[:16]


def _note_id(note_path: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9]", "-", note_path)[:40].strip("-")
    suffix = hashlib.sha256(note_path.encode()).hexdigest()[:8]
    return f"{safe}-{suffix}"


def _normalise(v: "np.ndarray") -> "np.ndarray":
    norms = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.where(norms == 0, 1.0, norms)


def _strip_frontmatter(content: str) -> str:
    if content.startswith("---\n"):
        end = content.find("\n---\n", 4)
        if end != -1:
            return content[end + 5:].strip()
    return content.strip()


def _migrate_if_needed(vault_path: Path) -> None:
    """Migrate legacy monolithic index to per-document files."""
    old_vecs_path = _emb_root(vault_path) / "vectors.npy"
    old_meta_path = _emb_root(vault_path) / "meta.json"
    if not old_vecs_path.exists() or not old_meta_path.exists():
        return

    meta = json.loads(old_meta_path.read_text())
    vecs = np.load(old_vecs_path)

    _docs_dir(vault_path).mkdir(parents=True, exist_ok=True)
    _notes_dir(vault_path).mkdir(parents=True, exist_ok=True)

    from collections import defaultdict
    groups: dict = defaultdict(list)
    for i, entry in enumerate(meta):
        if entry.get("type") == "note":
            key = ("note", entry["note_path"])
        else:
            key = ("doc", entry.get("filename", "unknown"))
        groups[key].append((i, entry))

    for (kind, key), rows in groups.items():
        indices     = [i for i, _ in rows]
        chunk_meta  = [e for _, e in rows]
        chunk_vecs  = vecs[indices]
        if kind == "note":
            fid = _note_id(key)
            d   = _notes_dir(vault_path)
        else:
            fid = _doc_id(key)
            d   = _docs_dir(vault_path)
        np.save(d / f"{fid}.npy", chunk_vecs)
        (d / f"{fid}.json").write_text(json.dumps(chunk_meta, ensure_ascii=False))

    old_vecs_path.unlink()
    old_meta_path.unlink()


def _load_all(vault_path: Path) -> tuple["np.ndarray | None", list[dict]]:
    """Load all vectors and metadata. Returns (None, []) when the index is empty."""
    _migrate_if_needed(vault_path)
    all_vecs: list = []
    all_meta: list = []
    for d in (_docs_dir(vault_path), _notes_dir(vault_path)):
        if not d.exists():
            continue
        for json_file in sorted(d.glob("*.json")):
            npy_file = json_file.with_suffix(".npy")
            if not npy_file.exists():
                continue
            all_meta.extend(json.loads(json_file.read_text()))
            all_vecs.append(np.load(npy_file))
    if not all_vecs:
        return None, []
    return np.vstack(all_vecs), all_meta


# Alias used by tests that inspect internal state.
_load = _load_all


def add_document(vault_path: Path, filename: str, pages: list[dict]) -> int:
    """Embed pages and write to the per-document index file. Returns pages indexed."""
    embedder = _get_embedder()
    texts    = [p["markdown"] for p in pages]
    vecs     = _normalise(np.array(list(embedder.embed(texts)), dtype=np.float32))
    meta     = [
        {"type": "page", "filename": filename, "page": p["page"], "preview": p["markdown"][:_PREVIEW_LEN]}
        for p in pages
    ]
    _docs_dir(vault_path).mkdir(parents=True, exist_ok=True)
    fid = _doc_id(filename)
    np.save(_docs_dir(vault_path) / f"{fid}.npy", vecs)
    (_docs_dir(vault_path) / f"{fid}.json").write_text(json.dumps(meta, ensure_ascii=False))
    return len(pages)


def add_note(vault_path: Path, note_path: str, content: str) -> None:
    """Embed a vault note (entity or document) and write to the per-note index file."""
    body = _strip_frontmatter(content)
    if not body:
        return
    embedder = _get_embedder()
    vec      = _normalise(np.array(list(embedder.embed([body])), dtype=np.float32))
    meta     = [{"type": "note", "note_path": note_path, "preview": body[:_PREVIEW_LEN]}]
    _notes_dir(vault_path).mkdir(parents=True, exist_ok=True)
    fid = _note_id(note_path)
    np.save(_notes_dir(vault_path) / f"{fid}.npy", vec)
    (_notes_dir(vault_path) / f"{fid}.json").write_text(json.dumps(meta, ensure_ascii=False))


def search(vault_path: Path, query: str, top_n: int = 5) -> list[dict]:
    """Return top_n entries most similar to query, scored by cosine similarity."""
    vectors, meta = _load_all(vault_path)
    if vectors is None or not meta:
        return []
    embedder = _get_embedder()
    q        = _normalise(np.array(list(embedder.embed([query])), dtype=np.float32)[0])
    scores   = vectors @ q
    top_idx  = np.argsort(scores)[::-1][:top_n]
    return [{**meta[i], "score": float(scores[i])} for i in top_idx]


def index_stats(vault_path: Path) -> dict:
    _, meta = _load_all(vault_path)
    pages = sum(1 for m in meta if m.get("type", "page") == "page")
    notes = sum(1 for m in meta if m.get("type") == "note")
    return {"pages": pages, "notes": notes, "total": len(meta)}
