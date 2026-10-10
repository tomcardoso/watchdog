"""`search.*` — the semantic + exact lanes of search, batch term checks, and the
cross-investigation "have I seen this name anywhere?" lookup.

`search.query` runs `embed.search` and `fulltext.search` as a session's `search` tool does and
returns the same JSON shape (`cmd/vault._build_search_json`), each item enriched with the document
sha, its note and its original so the app can link straight to them. The batch and everywhere
lookups share the library's data functions (`batch_report`, `everywhere_report`). Without
fastembed installed the semantic lane reports `semantic_error` and the exact lane still answers.
"""

from __future__ import annotations

from pathlib import Path

from watchdog.gui import engine_setup, vaultio
from watchdog.gui.rpc import RpcError, method
from watchdog.gui.vaultio import require_vault

_TOP_MAX = 100


def _top(top) -> int:
    try:
        n = int(top)
    except (TypeError, ValueError):
        raise RpcError("The number of results must be a whole number.", code="bad_params")
    return max(1, min(n, _TOP_MAX))


class _DocIndex:
    """Lookups from what a search hit names (a sha, a filename, a note path) to the registry
    document it belongs to."""

    def __init__(self, vault: Path):
        self.docs = vaultio.load_documents(vault)
        self.by_name: dict[str, list[str]] = {}
        self.by_note: dict[str, str] = {}
        for sha, rec in self.docs.items():
            if not isinstance(rec, dict):
                continue
            self.by_name.setdefault(rec.get("filename"), []).append(sha)
            stem = vaultio.doc_note_stem(rec)
            if stem:
                self.by_note[stem] = sha

    def by_filename(self, filename: str | None) -> str | None:
        """A passage carries only a filename; link it only when no other document shares it."""
        shas = self.by_name.get(filename) or []
        return shas[0] if len(shas) == 1 else None

    def refs(self, sha: str | None) -> dict:
        rec = self.docs.get(sha) if sha else None
        if not isinstance(rec, dict):
            return {"sha": None, "note": None, "original": None}
        morgue = rec.get("morgue_path")
        return {"sha": sha, "note": vaultio.doc_note_stem(rec),
                "original": morgue.replace("\\", "/") if isinstance(morgue, str) and morgue else None}


def _exact_hit(index: _DocIndex, h: dict) -> dict:
    """One full-text hit with its links. A corpus hit is keyed on the document's sha; any other
    kind is a generated note, whose `path` is the note's vault-relative path."""
    if h.get("kind") == "corpus":
        refs = index.refs(h.get("key"))
        if refs["sha"] is None:
            refs["original"] = h.get("path") or None
        return {"kind": h.get("kind"), "title": h.get("title"), "path": h.get("path"),
                "page": h.get("page"), "text": h.get("text"), **refs}
    path = h.get("path")
    stem = path[:-3] if isinstance(path, str) and path.endswith(".md") else path
    sha = index.by_note.get(stem) if h.get("kind") == "document" else None
    refs = index.refs(sha)
    return {"kind": h.get("kind"), "title": h.get("title"), "path": path, "page": h.get("page"),
            "text": h.get("text"), "sha": sha, "note": refs["note"] or stem or None,
            "original": refs["original"]}


@method("search.query")
def query(vault: str, query: str, top: int = 5, threshold: float | None = None,
          rerank: bool = True) -> dict:
    from watchdog.cmd.vault import _build_search_json
    from watchdog.pipeline import embed, fulltext

    v = require_vault(vault)
    if not isinstance(query, str) or not query.strip():
        raise RpcError("Type something to search for.", code="bad_params")
    n = _top(top)
    index = _DocIndex(v)

    exact_raw, exact_error = [], None
    try:
        exact_raw = fulltext.search(v, query, limit=n)
    except Exception as e:  # noqa: BLE001 — an unavailable lane must not hide the other one
        exact_error = str(e)

    passages_raw: list = []
    notes_raw: list = []
    semantic_error = None
    stats = embed.index_stats(v)
    index_empty = stats["total"] == 0
    # The meaning-based lane needs the search models, which the app's engine installs in its
    # background phase (D272); until then only exact matches are searched.
    semantic_pending = not engine_setup.engine_ready()
    if not index_empty and not semantic_pending:
        # Without an explicit threshold nothing is filtered (cosine ≥ -1 always holds), as in the CLI.
        min_score = threshold if threshold is not None else -1.0
        try:
            passages_raw = embed.search(v, query, top_n=n, min_score=min_score, scope="corpus",
                                        rerank=bool(rerank))
            notes_raw = embed.search(v, query, top_n=n, min_score=min_score, scope="notes")
        except Exception as e:  # noqa: BLE001 — e.g. fastembed not installed
            semantic_error = f"{type(e).__name__}: {e}"

    base = _build_search_json(query, passages_raw, notes_raw, exact_raw)
    passages = []
    for item, raw in zip(base["passages"], passages_raw):
        passages.append({**item, **index.refs(index.by_filename(raw.get("filename")))})
    notes = []
    for item, raw in zip(base["notes"], notes_raw):
        note_path = item.get("note_path")
        stem = note_path[:-3] if isinstance(note_path, str) and note_path.endswith(".md") else note_path
        sha = index.by_note.get(stem)
        refs = index.refs(sha)
        notes.append({**item, "sha": sha, "note": stem, "original": refs["original"]})
    return {
        "query": query,
        "index_empty": index_empty,
        "exact_error": exact_error,
        "semantic_error": semantic_error,
        "semantic_pending": semantic_pending,
        "exact": [_exact_hit(index, h) for h in exact_raw],
        "passages": passages,
        "notes": notes,
    }


def _term_hits(index: _DocIndex, report_row: dict, vault_name: str | None = None) -> list[dict]:
    hits = []
    for e in report_row["entities"]:
        hits.append({"kind": "entity", "title": e.get("name"), "note": e.get("note_path"),
                     "page": None, "text": None, "path": e.get("note_path"), "sha": None,
                     "type": e.get("type")})
    for h in report_row["hits"]:
        item = _exact_hit(index, h)
        hits.append({k: item[k] for k in ("kind", "title", "note", "page", "text", "path", "sha")})
    if vault_name is not None:
        for h in hits:
            h["vault_name"] = vault_name
    return hits


@method("search.batch")
def batch(terms: list[str], vault: str | None = None, everywhere: bool = False,
          top: int = 5) -> dict:
    """Every term checked against manifest names/aliases and the full-text index — the lane that
    scales to a leaked roster or sanctions list. A term whose exact-match lookup failed comes back
    `checked: false`, never as an empty result."""
    from watchdog.cmd.vault import batch_report, everywhere_report

    if not isinstance(terms, list):
        raise RpcError("terms must be a list of strings", code="bad_params")
    clean = [t.strip() for t in terms if isinstance(t, str) and t.strip()]
    if not clean:
        raise RpcError("No search terms were given.", code="bad_params")
    n = _top(top)

    if everywhere:
        projects = vaultio.registered_projects()
        out = []
        for term in clean:
            results, _skipped = everywhere_report(projects, [term], n)
            hits, ok = [], True
            for r in results:
                hits += _term_hits(_DocIndex(Path(r["path"])), r, vault_name=r["name"])
                ok = ok and r["error"] is None
            out.append({"term": term, "checked": ok, "hits": hits})
        return {"terms": out}

    if vault is None:
        raise RpcError("Choose an investigation to search, or search everywhere.", code="bad_params")
    v = require_vault(vault)
    report, _failures = batch_report(v, clean, n)
    index = _DocIndex(v)
    return {"terms": [{"term": r["term"], "checked": r["error"] is None, "hits": _term_hits(index, r)}
                      for r in report]}


@method("search.everywhere")
def everywhere(query: str, top: int = 5) -> dict:
    from watchdog.cmd.vault import everywhere_report

    if not isinstance(query, str) or not query.strip():
        raise RpcError("Type something to search for.", code="bad_params")
    results, skipped = everywhere_report(vaultio.registered_projects(), [query.strip()], _top(top))
    vaults = []
    for r in results:
        index = _DocIndex(Path(r["path"]))
        vaults.append({
            "slug": r["slug"], "name": r["name"], "path": r["path"],
            "entity_hits": r["entities"],
            "exact": [_exact_hit(index, h) for h in r["hits"]],
            "error": r["error"],
        })
    return {"vaults": vaults, "skipped": [{"slug": slug, "reason": reason} for slug, reason in skipped]}


@method("search.status")
def status(vault: str) -> dict:
    from watchdog.pipeline import embed, fulltext

    v = require_vault(vault)
    stats = embed.index_stats(v)
    docs_dir = v / ".embeddings" / "docs"
    return {
        "total": stats["total"],
        "documents": len(list(docs_dir.glob("*.npy"))) if docs_dir.is_dir() else 0,
        "notes": stats["notes"],
        "passages": stats["passages"],
        "fulltext": fulltext.index_stats(v),
    }
