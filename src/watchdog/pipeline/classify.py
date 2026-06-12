"""
Document type classifier for watchdog.

Embeds the first N pages of a document and compares against cached skill embeddings
(stored per-vault in .watchdog/) to determine the most likely document type.

Uses the same fastembed model as the search index so no second model is needed.
"""

import hashlib
import json
import numpy as np
from pathlib import Path

_CLASSIFY_PAGES = 10
_THRESHOLD = 0.65
_MODEL = "BAAI/bge-small-en-v1.5"


def _skills_dir(vault: Path) -> Path:
    return vault / ".claude" / "commands" / "records"


def _cache_paths(vault: Path) -> tuple[Path, Path]:
    base = vault / ".watchdog"
    return base / "skill-embeddings.npy", base / "skill-embeddings-meta.json"


def _skill_hash(vault: Path) -> str:
    """Stable hash of all skill file contents for cache invalidation."""
    h = hashlib.sha256()
    for f in sorted(_skills_dir(vault).glob("*.md")):
        if not f.name.startswith("_"):
            h.update(f.read_bytes())
    return h.hexdigest()[:16]


def _load_or_build(vault: Path) -> tuple[list[str], "np.ndarray"]:
    """Return (skill_names, embeddings). Builds and caches if stale or absent."""
    npy_path, meta_path = _cache_paths(vault)
    current_hash = _skill_hash(vault)

    if npy_path.exists() and meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
            if meta.get("model") == _MODEL and meta.get("skills_hash") == current_hash:
                return meta["skill_names"], np.load(npy_path)
        except Exception:
            pass

    skill_names, skill_texts = [], []
    for f in sorted(_skills_dir(vault).glob("*.md")):
        if not f.name.startswith("_"):
            skill_names.append(f.stem)
            skill_texts.append(f.read_text())

    if not skill_names:
        return [], np.array([])

    from watchdog.pipeline.embed import _get_embedder, _normalise
    embedder = _get_embedder()
    vecs = _normalise(np.array(list(embedder.embed(skill_texts)), dtype=np.float32))

    npy_path.parent.mkdir(parents=True, exist_ok=True)
    np.save(npy_path, vecs)
    meta_path.write_text(json.dumps({
        "model": _MODEL,
        "skills_hash": current_hash,
        "skill_names": skill_names,
    }))

    return skill_names, vecs


def classify_document(pages: list[dict], vault: Path) -> "str | None":
    """
    Return the skill name best matching this document's first pages, or None.

    Uses max-page cosine similarity: embed the first CLASSIFY_PAGES pages, compare
    each against every skill embedding, take the per-skill maximum, return the winner
    above THRESHOLD. Never raises — all errors return None.
    """
    if not pages:
        return None
    try:
        skill_names, skill_vecs = _load_or_build(vault)
        if not skill_names:
            return None

        page_texts = [p["markdown"] for p in pages[:_CLASSIFY_PAGES] if p.get("markdown")]
        if not page_texts:
            return None

        from watchdog.pipeline.embed import _get_embedder, _normalise
        embedder = _get_embedder()
        page_vecs = _normalise(np.array(list(embedder.embed(page_texts)), dtype=np.float32))

        scores = page_vecs @ skill_vecs.T       # (n_pages, n_skills)
        max_scores = scores.max(axis=0)         # (n_skills,)
        best_idx = int(np.argmax(max_scores))
        if float(max_scores[best_idx]) < _THRESHOLD:
            return None
        return skill_names[best_idx]
    except Exception:
        return None
