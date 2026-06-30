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
import math
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

# Hybrid corpus retrieval (scope="corpus"): dense cosine fused with a sparse BM25 leg,
# then a cross-encoder rerank of the fused candidate pool. Sparse retrieval catches the
# exact tokens embeddings blur — case numbers, dollar amounts, statute cites, names.
_RERANK_DEFAULT = "BAAI/bge-reranker-base"   # cross-encoder; configurable via rerank_model
_RERANK_POOL = 100       # fused candidates reranked before truncation (Anthropic: ~150→20)
_BM25_K1 = 1.5
_BM25_B = 0.75
_RRF_K = 60              # reciprocal-rank-fusion constant (standard)
_TOKEN_RE = re.compile(r"\w[\w'\-]*", re.UNICODE)   # unicode-aware (CJK/Cyrillic/accented)

_embedder = None
_reranker = None


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


def _rerank_model_name() -> str:
    return _config_get("rerank_model", _RERANK_DEFAULT)


def _rerank_enabled() -> bool:
    """Reranking is on unless rerank_model is explicitly cleared/disabled in config."""
    return (_rerank_model_name() or "").strip().lower() not in ("", "none", "off", "false", "0")


_rerank_notified = False


def _notify_rerank_once(msg: str) -> None:
    """Emit a reranker status line to stderr at most once per process, so a fall-back to
    fusion (e.g. the model can't download) is announced rather than silent."""
    global _rerank_notified
    if not _rerank_notified:
        import sys
        print(f"  {msg}", file=sys.stderr)
        _rerank_notified = True


def _get_reranker():
    # First construction downloads the model (~300MB); fastembed prints its own progress.
    global _reranker
    if _reranker is None:
        from fastembed.rerank.cross_encoder import TextCrossEncoder
        _reranker = TextCrossEncoder(_rerank_model_name())
    return _reranker


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _bm25_scores(query_tokens: list[str], docs_tokens: list[list[str]]) -> list[float]:
    """Okapi BM25 score of each passage against the query, computed in-memory over the
    loaded corpus (no persisted index — passages already live in memory at query time)."""
    n = len(docs_tokens)
    if n == 0 or not query_tokens:
        return [0.0] * n
    dls = [len(t) for t in docs_tokens]
    avgdl = (sum(dls) / n) or 1.0
    df: dict[str, int] = {}
    for toks in docs_tokens:
        for t in set(toks):
            df[t] = df.get(t, 0) + 1
    q = set(query_tokens)
    idf = {t: math.log(1 + (n - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5)) for t in q}
    scores = [0.0] * n
    for i, toks in enumerate(docs_tokens):
        if not toks:
            continue
        tf: dict[str, int] = {}
        for t in toks:
            if t in q:
                tf[t] = tf.get(t, 0) + 1
        dl = dls[i]
        s = 0.0
        for t, f in tf.items():
            s += idf[t] * (f * (_BM25_K1 + 1)) / (f + _BM25_K1 * (1 - _BM25_B + _BM25_B * dl / avgdl))
        scores[i] = s
    return scores


def _rrf(*rankings: list[int]) -> list[int]:
    """Reciprocal-rank fusion: combine several best-first rankings of the same item set
    into one. An item absent from a ranking simply contributes nothing from it."""
    fused: dict[int, float] = {}
    for ranking in rankings:
        for rank, idx in enumerate(ranking):
            fused[idx] = fused.get(idx, 0.0) + 1.0 / (_RRF_K + rank)
    return sorted(fused, key=lambda i: fused[i], reverse=True)


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


def add_document(vault_path: Path, filename: str, pages: list[dict], context: str = "") -> int:
    """Window each page, embed the passages, and write the per-document index file.

    ``context`` is an optional document-level contextual prefix (title, type, the entities
    the document is about — built at ingest in ``write_vault``). It is prepended to each
    window *before embedding* and stored alongside the window so the sparse (BM25) leg sees
    it too, anchoring a passage that lacks the document's who/what to its document (Anthropic
    contextual-retrieval). The stored ``text`` stays the clean window, so the citation and
    the displayed snippet are unaffected.

    Returns the number of passages indexed.
    """
    ctx = (context or "").strip()
    texts: list[str] = []
    meta: list[dict] = []
    for p in pages:
        for window in _windows(p.get("markdown", "")):
            texts.append(f"{ctx}\n\n{window}" if ctx else window)
            meta.append({"type": "passage", "filename": filename, "page": p.get("page"),
                         "text": window, "context": ctx})
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
           min_score: float = 0.0, scope: str = "all", rerank: bool = True) -> list[dict]:
    """Return the top_n best entries for the query.

    ``scope`` is ``"corpus"`` (source passages only), ``"notes"`` (entity/document notes
    only), or ``"all"``. ``"corpus"`` runs the hybrid pipeline — dense cosine fused with a
    sparse BM25 leg, then a cross-encoder rerank of the fused pool (``rerank``, on by
    default; configurable via ``rerank_model``). ``"notes"`` and ``"all"`` rank by cosine.

    Each result's ``score`` is the cosine similarity (so ``min_score`` / the CLI
    ``--threshold`` keep their dense-cutoff meaning), even when the *order* is set by
    fusion + rerank.
    """
    vectors, meta = _load_all(vault_path)
    if vectors is None or not meta:
        return []
    q = _embed_query(query)
    if q is None:
        return []
    dense = vectors @ q
    if scope == "corpus":
        return _hybrid_corpus_search(query, dense, meta, top_n, min_score, rerank)
    # notes / all: cosine ranking (synthesized prose; the dense signal is what matters)
    results: list[dict] = []
    for i in np.argsort(dense)[::-1]:
        score = float(dense[i])
        if score < min_score:
            break  # argsort is descending, so nothing past here qualifies
        if scope == "notes" and meta[i].get("type") != "note":
            continue
        results.append({**meta[i], "score": score})
        if len(results) >= top_n:
            break
    return results


def _hybrid_corpus_search(query: str, dense: "np.ndarray", meta: list[dict],
                          top_n: int, min_score: float, rerank: bool) -> list[dict]:
    cidx = [i for i, m in enumerate(meta) if m.get("type") != "note"]
    if not cidx:
        return []
    # Two candidate rankings over the corpus-local index space, then fuse.
    dense_order = sorted(range(len(cidx)), key=lambda j: dense[cidx[j]], reverse=True)
    corpus_tokens = [
        _tokenize(f"{meta[cidx[j]].get('context', '')} {meta[cidx[j]].get('text', '')}")
        for j in range(len(cidx))
    ]
    bm = _bm25_scores(_tokenize(query), corpus_tokens)
    bm25_order = [j for j in sorted(range(len(cidx)), key=lambda j: bm[j], reverse=True) if bm[j] > 0]
    order = _rrf(dense_order, bm25_order) if bm25_order else dense_order
    pool = order[:_RERANK_POOL]
    if rerank and _rerank_enabled() and len(pool) > 1:
        try:
            reranker = _get_reranker()
            scores = list(reranker.rerank(query, [meta[cidx[j]].get("text", "") for j in pool]))
            pool = [j for j, _ in sorted(zip(pool, scores), key=lambda x: x[1], reverse=True)]
        except Exception as exc:
            # Reranker unavailable (e.g. model download blocked) → keep the fusion order
            # rather than failing the search, but say so once so it's not silent.
            _notify_rerank_once(
                f"Reranker {_rerank_model_name()} unavailable ({exc.__class__.__name__}); "
                f"ranking by BM25 + embedding fusion. Use --no-rerank to silence."
            )
    results: list[dict] = []
    for j in pool:
        gi = cidx[j]
        score = float(dense[gi])
        if score < min_score:
            continue  # advisory cosine floor; ordering is by fusion/rerank, so don't break
        results.append({**meta[gi], "score": score})
        if len(results) >= top_n:
            break
    return results


def index_stats(vault_path: Path) -> dict:
    _, meta = _load_all(vault_path)
    passages = sum(1 for m in meta if m.get("type") != "note")
    notes    = sum(1 for m in meta if m.get("type") == "note")
    return {"passages": passages, "notes": notes, "total": len(meta)}
