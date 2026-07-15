"""Deterministic watch-word scan (#165).

After a batch is ingested, scan each new document's page-aligned morgue markdown for the
user's watch terms (the vault-root ``watchlist.md``). Pure Python — no model call. Each hit
records the term, document, page, a context snippet, and (when the matched text resolves to a
known registry entity) a link to that entity's note. ``write_alerts`` appends the run's hits to
``briefings/alerts-<date>.md`` and returns a one-line summary for the console.

Matching: a literal term matches case-insensitively on word boundaries ("Ana" won't match
"banana"); a term wrapped in ``/.../`` is treated as a regular expression (still case-insensitive).
"""

import datetime
import json
import re
from collections import defaultdict
from pathlib import Path

from watchdog.pipeline import resolutions

_CONTEXT_CHARS = 80          # context shown on each side of a match in the snippet
_MAX_HITS_PER_DOC = 50       # bound a pathological term (e.g. a too-broad regex) per document
_SNIPPETS_PER_DOC = 3        # distinct-page snippets shown per term per document in the alert
_PAGE_RE = re.compile(r"<!--\s*PAGE\s+(\d+)\s*-->")


def load_terms(vault: Path) -> list[dict]:
    """Parse ``watchlist.md`` into ``{"term", "regex"}`` matchers.

    Blank lines and ``#`` comments are skipped. A line wrapped in ``/.../`` compiles as a regex;
    anything else is a literal matched case-insensitively on word boundaries. An invalid regex is
    skipped rather than crashing the scan.
    """
    path = vault / "watchlist.md"
    if not path.exists():
        return []
    terms: list[dict] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if len(line) >= 2 and line.startswith("/") and line.endswith("/"):
            try:
                rx = re.compile(line[1:-1], re.IGNORECASE)
            except re.error:
                continue
        else:
            # Lookarounds (not \b) so terms that begin or end with punctuation still match.
            rx = re.compile(rf"(?<!\w){re.escape(line)}(?!\w)", re.IGNORECASE)
        terms.append({"term": line, "regex": rx})
    return terms


def add_terms(vault: Path, terms: list[str]) -> list[str]:
    """Append new terms to ``watchlist.md``, skipping any already present — case-insensitively,
    against the same parsed term text ``load_terms`` matches on (so a candidate already covered
    by an existing ``/regex/`` line's literal text is still caught as a duplicate). Creates the
    file if it doesn't exist yet (normally already created by ``watchdog new``). Existing lines,
    including ``#`` comments, are untouched — this only ever appends.

    Returns the terms actually added, in order (#229 — `/watchdog-context` proposing seed terms).
    """
    existing_lower = {t["term"].lower() for t in load_terms(vault)}
    to_add: list[str] = []
    for term in terms:
        term = term.strip()
        if not term or term.lower() in existing_lower:
            continue
        to_add.append(term)
        existing_lower.add(term.lower())
    if not to_add:
        return []

    path = vault / "watchlist.md"
    prefix = path.read_text(encoding="utf-8") if path.exists() else ""
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    path.write_text(prefix + "\n".join(to_add) + "\n", encoding="utf-8")
    return to_add


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _entity_index(vault: Path) -> dict[str, dict]:
    """Lowercased name/alias → entity, from the registry manifest (free, already on disk)."""
    manifest = _load_json(vault / ".watchdog" / "registry" / "manifest.json")
    idx: dict[str, dict] = {}
    for eid, e in manifest.items():
        for name in [e.get("name", ""), *e.get("aliases", [])]:
            if name:
                idx.setdefault(name.lower(), {
                    "id": eid, "name": e["name"], "type": e["type"], "note_path": e["note_path"],
                })
    return idx


def _page_for(markers: list[tuple[int, int]], pos: int) -> int | None:
    """Page number of the ``<!-- PAGE N -->`` marker most recently before ``pos``."""
    page = None
    for start, num in markers:
        if start > pos:
            break
        page = num
    return page


def _snippet(text: str, start: int, end: int) -> str:
    """A whitespace-collapsed window around the match, with the match bolded and ellipses
    where the window is clipped."""
    a, b = max(0, start - _CONTEXT_CHARS), min(len(text), end + _CONTEXT_CHARS)
    seg = text[a:b]
    rs, re_ = start - a, end - a
    seg = f"{seg[:rs]}**{seg[rs:re_]}**{seg[re_:]}"
    seg = re.sub(r"\s+", " ", _PAGE_RE.sub("", seg)).strip()
    return ("… " if a > 0 else "") + seg + (" …" if b < len(text) else "")


def scan(vault: Path, results: list[dict]) -> list[dict]:
    """Scan this run's successfully-written documents against ``watchlist.md``.

    ``results`` is the per-document result list from the ingest run; only ``status == "ok"``
    documents are scanned (they are the ones written to the morgue). Returns a flat list of hits.
    """
    terms = load_terms(vault)
    if not terms:
        return []
    docs_reg = _load_json(vault / ".watchdog" / "registry" / "documents.json")
    entity_index = _entity_index(vault)
    resolved = resolutions.resolved_ids(vault)

    hits: list[dict] = []
    for r in results:
        if r.get("status") != "ok":
            continue
        sha256 = r.get("sha256")
        doc = docs_reg.get(sha256, {})
        morgue_path = doc.get("morgue_path")
        if not morgue_path:
            continue
        md = vault / Path(morgue_path).with_suffix(".md")
        if not md.exists():
            continue
        text = md.read_text(encoding="utf-8", errors="replace")
        markers = [(m.start(), int(m.group(1))) for m in _PAGE_RE.finditer(text)]
        for t in terms:
            rid = resolutions.alert_id(sha256 or "", t["term"])
            if rid in resolved:
                continue   # this term was acknowledged for this document (#266)
            for n, m in enumerate(t["regex"].finditer(text)):
                if n >= _MAX_HITS_PER_DOC:
                    break
                hits.append({
                    "term": t["term"],
                    "filename": doc.get("filename") or r.get("filename"),
                    "document_note": doc.get("document_note"),
                    "page": _page_for(markers, m.start()),
                    "snippet": _snippet(text, m.start(), m.end()),
                    "entity": entity_index.get(m.group(0).lower()),
                    "rid": rid,
                })
    return hits


def _format_run(hits: list[dict], now: datetime.datetime) -> str:
    n_terms = len({h["term"] for h in hits})
    n_docs = len({h["filename"] for h in hits})
    lines = [f"\n## {now:%Y-%m-%d %H:%M} — {len(hits)} match"
             f"{'es' if len(hits) != 1 else ''} "
             f"({n_terms} term{'s' if n_terms != 1 else ''}, "
             f"{n_docs} document{'s' if n_docs != 1 else ''})\n"]

    by_term: dict[str, list[dict]] = defaultdict(list)
    for h in hits:
        by_term[h["term"]].append(h)

    for term in sorted(by_term):
        thits = by_term[term]
        by_doc: dict[str, list[dict]] = defaultdict(list)
        for h in thits:
            by_doc[h["filename"]].append(h)
        lines.append(f"### `{term}` — {len(by_doc)} document"
                     f"{'s' if len(by_doc) != 1 else ''}\n")
        for fname in sorted(by_doc):
            dhits = by_doc[fname]
            note = dhits[0].get("document_note")
            link = f"[[{note}|{fname}]]" if note else fname
            ent = next((h["entity"] for h in dhits if h.get("entity")), None)
            ent_txt = f" · known entity [[{ent['note_path']}|{ent['name']}]]" if ent else ""
            count = f" ({len(dhits)} matches)" if len(dhits) > 1 else ""
            rid = dhits[0].get("rid")
            wid = f" <!--wid:{rid}-->" if rid else ""
            lines.append(f"- [ ] **{link}**{ent_txt}{count}{wid}")
            shown: list[int | None] = []
            for h in dhits:
                if h["page"] in shown:
                    continue
                shown.append(h["page"])
                pg = f"p. {h['page']}" if h["page"] else "page unknown"
                lines.append(f"  - {pg}: {h['snippet']}")
                if len(shown) >= _SNIPPETS_PER_DOC:
                    break
        lines.append("")
    return "\n".join(lines) + "\n"


def write_alerts(vault: Path, hits: list[dict]) -> tuple[str, int, int] | None:
    """Append this run's hits to ``briefings/alerts-<date>.md`` (dated, append-per-run).

    Returns ``(relpath, n_terms, n_docs)`` for the console summary, or ``None`` when there were
    no hits (no file is created).
    """
    if not hits:
        return None
    now = datetime.datetime.now()
    relpath = f"briefings/alerts-{now:%Y-%m-%d}.md"
    path = vault / relpath
    path.parent.mkdir(exist_ok=True)
    run = _format_run(hits, now)
    if path.exists():
        with open(path, "a", encoding="utf-8") as f:
            f.write(run)
    else:
        path.write_text(
            f"# Watch-word alerts — {now:%Y-%m-%d}\n\n"
            f"*Deterministic scan of newly-ingested documents against `watchlist.md`.*\n"
            f"*Tick a box and run `watchdog resolve --sync` to stop re-reporting a term "
            f"for a document.*\n" + run,
            encoding="utf-8")
    return relpath, len({h["term"] for h in hits}), len({h["filename"] for h in hits})
