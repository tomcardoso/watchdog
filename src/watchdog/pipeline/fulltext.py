"""
Full-text (exact-term) search index for watchdog investigations (#109).

Complementary to embed.py's semantic index: a local SQLite FTS5 table over raw source
text (from the morgue) and every note the pipeline generates (entities, documents,
timeline, briefings, hot cache, run log). Where embed.py answers "what's most relevant",
this answers "every place this exact term or phrase appears" — the recall lane for names,
case numbers, and other tokens that never made it into a synthesized note.

One SQLite database at ``<vault>/.fulltext/index.db``. Re-indexing a document or note
deletes and re-inserts only its own rows (keyed by sha256 for corpus pages, note path for
notes), so no full-index rewrite is needed on an ordinary ingest.

Query syntax is deliberately simple, not FTS5's raw MATCH grammar (this is a tool for
journalists, not database users): a quoted substring matches as a phrase, bare words are
ANDed together, and every token is escaped before being handed to FTS5 so punctuation in a
name (O'Brien, AT&T) can't be misread as query syntax.
"""

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

_DB_REL = Path(".fulltext") / "index.db"

_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS fulltext USING fts5(
    text,
    kind UNINDEXED,
    key UNINDEXED,
    title UNINDEXED,
    path UNINDEXED,
    page UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""

_QUOTED_RE = re.compile(r'"([^"]+)"')


def _db_path(vault_path: Path) -> Path:
    return vault_path / _DB_REL


@contextmanager
def _connection(vault_path: Path):
    db_path = _db_path(vault_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(_SCHEMA)
    except sqlite3.OperationalError as e:
        conn.close()
        raise RuntimeError(
            "This Python's sqlite3 build has no FTS5 support — full-text search is "
            "unavailable. Semantic search (watchdog search) still works."
        ) from e
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def add_document(vault_path: Path, filename: str, sha256: str, pages: list[dict],
                  morgue_path: str = "") -> int:
    """Index each page of a source document's raw text, one FTS row per page so every hit
    carries an exact page citation. Returns the number of pages indexed.

    ``sha256`` is the delete-before-insert key, so re-ingesting the same document (a
    finalize re-run) replaces its rows instead of duplicating them.
    """
    rows = [
        (p.get("markdown", ""), "corpus", sha256, filename, morgue_path, p.get("page"))
        for p in pages if (p.get("markdown") or "").strip()
    ]
    with _connection(vault_path) as conn:
        conn.execute("DELETE FROM fulltext WHERE kind = 'corpus' AND key = ?", (sha256,))
        conn.executemany(
            "INSERT INTO fulltext (text, kind, key, title, path, page) VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
    return len(rows)


def add_note(vault_path: Path, note_path: str, kind: str, title: str, text: str) -> None:
    """Index one generated note (kind: entity/document/timeline/briefing/hot/log) as a
    single row. ``note_path`` is the delete-before-insert key, so re-writing the same note
    (synthesis, a timeline rebuild) replaces its row rather than duplicating it."""
    body = (text or "").strip()
    with _connection(vault_path) as conn:
        conn.execute("DELETE FROM fulltext WHERE kind = ? AND key = ?", (kind, note_path))
        if body:
            conn.execute(
                "INSERT INTO fulltext (text, kind, key, title, path, page) "
                "VALUES (?, ?, ?, ?, ?, NULL)",
                (body, kind, note_path, title, note_path),
            )


def _fts_escape(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


def build_match(query: str) -> str:
    """Turn a plain-language query into a safe FTS5 MATCH expression: quoted substrings
    become phrase clauses, bare words become individually-escaped AND clauses. Returns ""
    for a query with no usable terms."""
    query = query.strip()
    if not query:
        return ""
    phrases = _QUOTED_RE.findall(query)
    remainder = _QUOTED_RE.sub(" ", query)
    words = remainder.split()
    clauses = [_fts_escape(p) for p in phrases] + [_fts_escape(w) for w in words]
    return " AND ".join(clauses)


def search(vault_path: Path, query: str, limit: int = 5, kinds: list[str] | None = None) -> list[dict]:
    """Exact/phrase search, best matches first (FTS5 bm25 rank).

    Each hit is ``{kind, key, title, path, page, text}`` — ``text`` is the full indexed
    row (a page or a whole note) so the caller can window/highlight it exactly like the
    semantic search sections do. ``page`` is set only for ``kind == "corpus"``.
    """
    match_expr = build_match(query)
    if not match_expr or not _db_path(vault_path).exists():
        return []
    sql = ("SELECT kind, key, title, path, page, text, bm25(fulltext) AS rank "
           "FROM fulltext WHERE fulltext MATCH ?")
    params: list = [match_expr]
    if kinds:
        sql += f" AND kind IN ({','.join('?' * len(kinds))})"
        params.extend(kinds)
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)
    with _connection(vault_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        {"kind": r[0], "key": r[1], "title": r[2], "path": r[3], "page": r[4], "text": r[5]}
        for r in rows
    ]


def index_stats(vault_path: Path) -> dict:
    if not _db_path(vault_path).exists():
        return {"corpus": 0, "notes": 0, "total": 0}
    with _connection(vault_path) as conn:
        total = conn.execute("SELECT count(*) FROM fulltext").fetchone()[0]
        corpus = conn.execute("SELECT count(*) FROM fulltext WHERE kind = 'corpus'").fetchone()[0]
    return {"corpus": corpus, "notes": total - corpus, "total": total}
