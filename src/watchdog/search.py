"""Search results as the app's search screen and an Ask Claude session's `search` tool read them:
the semantic and exact lanes shaped into one JSON object, each hit with the facts recorded on its
page (D283), and the name-and-text lookups across one investigation's or every investigation's
records.
"""

from __future__ import annotations

import json
from pathlib import Path

from watchdog.pipeline.json_io import _read_json_or
from watchdog.projects import check_health


# Facts listed beside one search hit, so a session can cite what it found (D283).
FACTS_PER_HIT = 12


def page_facts(vault: Path):
    """A function (sha, page) -> the facts of that page of that document, each with its citation
    link, for search hits. Facts whose matched passage is on the page count too."""
    from watchdog.pipeline import citations, entity_facts
    index = entity_facts.FactIndex(vault)

    def facts(sha: str | None, page) -> list[dict]:
        if not sha or not isinstance(page, int):
            return []
        out = []
        for f in index.document_facts(sha):
            if page in (f.get("page"), f.get("passage_page")) and f.get("note"):
                out.append({"id": f["id"], "cite": citations.link(f), "fact": f.get("fact"),
                            "page": f.get("page"), "mark": (f.get("mark") or {}).get("status")})
        return out[:FACTS_PER_HIT]
    return facts, index


def build_json(query: str, passages: list[dict], notes: list[dict],
                       exact: list[dict] | None = None, vault: Path | None = None) -> dict:
    """Shape search results as JSON for the session's `search` tool (D299) and the app's search
    screen. Each passage carries the citable span (`text`) + its page; `score` is the
    cosine similarity (ordering already reflects fusion + rerank). ``exact`` is the full-text
    (FTS5) lane (#109) — every hit for the exact term/phrase, no relevance score.

    This JSON shape is a contract with the `watchdog-query` skill
    (src/watchdog/skills/watchdog-query.md), which tells Claude what each field means: a field
    rename or removal here must change the skill with it (#499).

    With `vault`, each passage and each exact match in a document's text also carries `facts`:
    the facts recorded on that page, each with its D271 `id`, the `cite` link to paste into a
    page (`[[documents/<slug>#^f-<hash>|p. 4]]`), its text, page and the reporter's mark (D283)."""
    facts_on, index = page_facts(vault) if vault is not None else (None, None)
    sha_by_name: dict = {}
    if index is not None:
        for sha, d in index.documents.items():
            if isinstance(d, dict):
                sha_by_name.setdefault(d.get("filename"), []).append(sha)

    def with_facts(item: dict, sha: str | None) -> dict:
        if facts_on is not None:
            item["facts"] = facts_on(sha, item.get("page"))
        return item

    def by_name(name) -> str | None:
        shas = sha_by_name.get(name) or []
        return shas[0] if len(shas) == 1 else None

    return {
        "query": query,
        "passages": [
            with_facts({"filename": r.get("filename"), "page": r.get("page"),
                        "text": r.get("text"), "score": round(float(r["score"]), 4)},
                       by_name(r.get("filename")))
            for r in passages
        ],
        "notes": [
            {"note_path": r.get("note_path"), "preview": r.get("preview"),
             "score": round(float(r["score"]), 4)}
            for r in notes
        ],
        "exact": [
            with_facts({"kind": r.get("kind"), "title": r.get("title"), "path": r.get("path"),
                        "page": r.get("page"), "text": r.get("text")},
                       r.get("key") if r.get("kind") == "corpus" else None)
            if r.get("kind") == "corpus" else
            {"kind": r.get("kind"), "title": r.get("title"), "path": r.get("path"),
             "page": r.get("page"), "text": r.get("text")}
            for r in (exact or [])
        ],
    }


def manifest_matches(manifest: dict, term: str) -> list[dict]:
    """Entities whose name or any alias contains `term` (case-insensitive substring)."""
    t = term.lower()
    hits = []
    for eid, entry in manifest.items():
        names = [entry.get("name", ""), *entry.get("aliases", [])]
        if any(t in n.lower() for n in names if n):
            hits.append({"id": eid, "name": entry.get("name"), "type": entry.get("type"),
                        "note_path": entry.get("note_path")})
    return hits


def batch_report(vault: Path, terms: list[str], limit: int) -> tuple[list[dict], int]:
    """One entry per term — `{term, entities, hits, error}` — from the vault's manifest (name and
    alias matches) and its full-text index, plus how many exact-match lookups failed. A failed
    lookup carries its `error` and must never be read as "no hits". Shared with the desktop app."""
    from watchdog.pipeline import fulltext
    manifest_path = vault / ".watchdog" / "registry" / "manifest.json"
    manifest = {}
    if manifest_path.exists():
        manifest = _read_json_or(manifest_path, {}, catch=(json.JSONDecodeError,))

    report = []
    failures = 0
    for term in terms:
        entities = manifest_matches(manifest, term)
        try:
            hits, error = fulltext.search(vault, term, limit=limit), None
        except Exception as e:
            hits, error = [], str(e)
            failures += 1
        report.append({"term": term, "entities": entities, "hits": hits, "error": error})
    return report, failures


def everywhere_report(all_projects: dict, terms: list[str], limit: int) -> tuple[list[dict], list[tuple[str, str]]]:
    """Every active (non-archived) investigation's manifest and full-text matches for `terms`:
    `(results, skipped)`, where each result is `{slug, name, path, entities, hits, error}` in
    project-name order and `skipped` lists `(slug, problem)` for investigations whose folder is
    missing or isn't a vault. Shared with the desktop app."""
    from watchdog.pipeline import fulltext

    active = {k: v for k, v in all_projects.items() if not v.get("archived")}
    results = []
    skipped = []
    for slug, info in sorted(active.items(), key=lambda x: x[1]["name"]):
        problem = check_health(info)
        if problem:
            skipped.append((slug, problem))
            continue
        vault = Path(info["path"])

        manifest_path = vault / ".watchdog" / "registry" / "manifest.json"
        manifest = {}
        if manifest_path.exists():
            manifest = _read_json_or(manifest_path, {}, catch=(json.JSONDecodeError,))

        entities_by_id = {}
        hits = []
        error = None
        for term in terms:
            for e in manifest_matches(manifest, term):
                entities_by_id[e["id"]] = e
            try:
                hits.extend(fulltext.search(vault, term, limit=limit))
            except Exception as e:
                error = str(e)

        results.append({"slug": slug, "name": info["name"], "path": info["path"],
                        "entities": list(entities_by_id.values()), "hits": hits, "error": error})
    return results, skipped

