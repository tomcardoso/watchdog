"""
Semantic search index for watchdog investigations.

Two kinds of vector live side by side under <vault_path>/.embeddings/:

  docs/{sha16}.npy   — float32 (n_passages, dim), L2-normalised — overlapping
                       sub-page *passage* windows for one source document
  docs/{sha16}.json  — passage metadata (filename, page, text) for that document
  notes/{id}.npy     — float32 (1, dim) per entity/document note
  notes/{id}.json    — metadata for that note

Re-ingesting a document or note overwrites only its own files — no full-index rewrite.

Corpus passages (what a *source* says) and notes (what we *concluded*) are kept as
separate streams so source-passage ranking isn't diluted by synthesized prose; the
``scope`` argument to :func:`search` selects between them.

Passages, not pages
-------------------
A whole page averages many topics into one vector and dilutes a short query, so each
page's text is split into overlapping word windows (Semantra's windowing idea —
``_WINDOW_SIZE`` words, ``_WINDOW_OVERLAP`` shared with the neighbour). Windows never
cross a page boundary, so every passage carries an exact page citation. The matched
window *is* the citable span — there is no separate highlighting step.

bge asymmetric retrieval
------------------------
The bge model family is trained for asymmetric retrieval: a short query is embedded
with an instruction prefix, passages without one. We honour that — see ``_QUERY_PREFIX``.
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

_MODEL_DEFAULT = "BAAI/bge-small-en-v1.5"

# bge models want short queries prefixed with this instruction; passages get nothing.
# Omitting it leaves a free, model-recommended retrieval gain on the table.
_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

_PREVIEW_LEN = 300       # note preview length (notes only)
_WINDOW_SIZE = 128       # words per passage window (≈ tokens)
_WINDOW_OVERLAP = 16     # words shared between adjacent windows

_embedder = None


def _config_get(key: str, default):
    """Read ~/.watchdog/config.json (best-effort). Local copy to avoid importing the
    heavyweight preprocess module just for one value."""
    try:
        cfg = json.loads((Path.home() / ".watchdog" / "config.json").read_text())
    except Exception:
        return default
    return cfg.get(key, default)


def _model_name() -> str:
    return _config_get("embed_model", _MODEL_DEFAULT)


def _get_embedder():
    global _embedder
    if _embedder is None:
        from fastembed import TextEmbedding
        _embedder = TextEmbedding(_model_name())
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


def _windows(text: str) -> list[str]:
    """Split one page's text into overlapping word windows.

    A page short enough to fit one window is returned verbatim (one window); a longer
    page yields windows of ``_WINDOW_SIZE`` words that overlap their neighbour by
    ``_WINDOW_OVERLAP`` words, so a phrase straddling a window boundary still lands
    whole in at least one passage.
    """
    words = text.split()
    if not words:
        return []
    if len(words) <= _WINDOW_SIZE:
        return [text.strip()]
    step = max(1, _WINDOW_SIZE - _WINDOW_OVERLAP)
    out: list[str] = []
    i = 0
    while i < len(words):
        out.append(" ".join(words[i:i + _WINDOW_SIZE]))
        if i + _WINDOW_SIZE >= len(words):
            break
        i += step
    return out


def _parse_query(query: str) -> tuple[list[str], list[str]]:
    """Split a query into positive and negative phrases (Semantra-style arithmetic).

    A ``+``/``-`` sign acts as a separator only when preceded by start-of-string or
    whitespace, so ``"shell company -real estate"`` becomes pos ``["shell company"]`` /
    neg ``["real estate"]`` (the whole phrase up to the next sign is one term — no quotes
    needed) while a hyphenated word like ``"no-bid"`` stays intact.
    """
    parts = re.split(r"(?:^|\s)([+\-])\s*", query.strip())
    pos: list[str] = []
    neg: list[str] = []
    lead = parts[0].strip()
    if lead:
        pos.append(lead)
    for i in range(1, len(parts), 2):
        sign = parts[i]
        text = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if text:
            (neg if sign == "-" else pos).append(text)
    return pos, neg


def _load_all(vault_path: Path) -> tuple["np.ndarray | None", list[dict]]:
    """Load all vectors and metadata. Returns (None, []) when the index is empty."""
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
    """Window each page, embed the passages, and write the per-document index file.

    Returns the number of passages indexed.
    """
    texts: list[str] = []
    meta: list[dict] = []
    for p in pages:
        for window in _windows(p.get("markdown", "")):
            texts.append(window)
            meta.append({"type": "passage", "filename": filename, "page": p.get("page"), "text": window})
    if not texts:
        return 0
    embedder = _get_embedder()
    vecs = _normalise(np.array(list(embedder.embed(texts)), dtype=np.float32))
    _docs_dir(vault_path).mkdir(parents=True, exist_ok=True)
    fid = _doc_id(filename)
    np.save(_docs_dir(vault_path) / f"{fid}.npy", vecs)
    (_docs_dir(vault_path) / f"{fid}.json").write_text(json.dumps(meta, ensure_ascii=False))
    return len(texts)


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


def _embed_query(query: str) -> "np.ndarray | None":
    """Embed a query into a single unit vector, honouring +/- arithmetic and the bge
    query prefix. Returns None when the query has no usable terms."""
    pos, neg = _parse_query(query)
    if not pos and not neg:
        return None
    embedder = _get_embedder()
    pos_vecs = list(embedder.embed([_QUERY_PREFIX + p for p in pos])) if pos else []
    neg_vecs = list(embedder.embed([_QUERY_PREFIX + p for p in neg])) if neg else []
    base = pos_vecs[0] if pos_vecs else neg_vecs[0]
    vec = np.zeros_like(np.asarray(base, dtype=np.float32))
    if pos_vecs:
        vec = vec + np.sum(np.asarray(pos_vecs, dtype=np.float32), axis=0)
    if neg_vecs:
        vec = vec - np.sum(np.asarray(neg_vecs, dtype=np.float32), axis=0)
    return _normalise(vec)


def search(vault_path: Path, query: str, top_n: int = 5,
           min_score: float = 0.0, scope: str = "all") -> list[dict]:
    """Return the top_n entries most similar to query, scored by cosine similarity.

    ``scope`` is ``"corpus"`` (source passages only), ``"notes"`` (entity/document notes
    only), or ``"all"``. Results scoring below ``min_score`` are dropped.
    """
    vectors, meta = _load_all(vault_path)
    if vectors is None or not meta:
        return []
    q = _embed_query(query)
    if q is None:
        return []
    scores  = vectors @ q
    results: list[dict] = []
    for i in np.argsort(scores)[::-1]:
        score = float(scores[i])
        if score < min_score:
            break  # argsort is descending, so nothing past here qualifies
        m = meta[i]
        is_note = m.get("type") == "note"
        if scope == "corpus" and is_note:
            continue
        if scope == "notes" and not is_note:
            continue
        results.append({**m, "score": score})
        if len(results) >= top_n:
            break
    return results


def index_stats(vault_path: Path) -> dict:
    _, meta = _load_all(vault_path)
    passages = sum(1 for m in meta if m.get("type") != "note")
    notes    = sum(1 for m in meta if m.get("type") == "note")
    return {"passages": passages, "notes": notes, "total": len(meta)}
