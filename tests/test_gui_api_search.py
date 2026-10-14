"""`search.*` handlers. The semantic lane needs fastembed (not installed in the lightweight dev
environment), so `embed.search`/`index_stats` are monkeypatched; the exact lane runs against a
real FTS5 index built with `fulltext`."""

import json

import pytest

from watchdog.pipeline import embed, fulltext

from tests.gui_support import SHA1, SHA2, call, call_error, register
from tests.test_write_vault import make_vault

pytest_plugins = ["tests.gui_support"]   # the rich_vault / wdg_home fixtures


def V(vault):
    return str(vault)


@pytest.fixture
def indexed(rich_vault):
    """The rich vault with its source pages and two notes in the full-text index."""
    for sha, name, morgue in ((SHA1, "report-one.pdf", "morgue/acme-corp/annual-report/report-one.pdf"),
                              (SHA2, "report-two.pdf", "morgue/acme-corp/annual-report/report-two.pdf")):
        fulltext.add_document(rich_vault, name, sha, [
            {"page": 1, "markdown": f"Page one of {name} mentions Ghost Ltd and Acme."},
            {"page": 2, "markdown": "Jane Doe was appointed director in March 2019."}], morgue_path=morgue)
    fulltext.add_note(rich_vault, "entities/person/jane-doe", "entity", "Jane Doe",
                      "Jane Doe is a director of Acme Corp.")
    fulltext.add_note(rich_vault, "documents/report-one", "document", "Report One",
                      "Report One covers the director appointment.")
    return rich_vault


@pytest.fixture
def no_embeddings(monkeypatch):
    monkeypatch.setattr(embed, "index_stats", lambda v: {"passages": 0, "notes": 0, "total": 0})

    def boom(*a, **k):
        raise AssertionError("embed.search must not run on an empty index")
    monkeypatch.setattr(embed, "search", boom)


@pytest.fixture
def embeddings(monkeypatch):
    seen = []
    monkeypatch.setattr(embed, "index_stats", lambda v: {"passages": 5, "notes": 2, "total": 7})

    def fake(vault, query, top_n=5, min_score=0.0, scope="all", rerank=True):
        seen.append({"query": query, "top_n": top_n, "min_score": min_score, "scope": scope, "rerank": rerank})
        if scope == "corpus":
            return [{"type": "passage", "filename": "report-one.pdf", "page": 2,
                     "text": "Jane Doe was appointed director.", "score": 0.912345},
                    {"type": "passage", "filename": "unknown.pdf", "page": 1, "text": "x", "score": 0.5}]
        return [{"type": "note", "note_path": "documents/report-one", "preview": "Report One…", "score": 0.7},
                {"type": "note", "note_path": "entities/person/jane-doe", "preview": "Jane…", "score": 0.6}]
    monkeypatch.setattr(embed, "search", fake)
    return seen


# ── search.query ─────────────────────────────────────────────────────────────────

def test_query_exact_lane_with_an_empty_semantic_index(indexed, no_embeddings):
    r = call("search.query", vault=V(indexed), query="Ghost Ltd")
    assert r["query"] == "Ghost Ltd" and r["index_empty"] is True
    assert r["exact_error"] is None and r["semantic_error"] is None
    assert r["passages"] == [] and r["notes"] == []
    hits = {h["title"]: h for h in r["exact"]}
    one = hits["report-one.pdf"]
    assert one["kind"] == "corpus" and one["page"] == 1 and "Ghost Ltd" in one["text"]
    assert one["sha"] == SHA1 and one["note"] == "documents/report-one"
    assert one["original"] == "morgue/acme-corp/annual-report/report-one.pdf"
    assert one["path"] == "morgue/acme-corp/annual-report/report-one.pdf"
    json.dumps(r)


def test_query_exact_hits_on_generated_notes(indexed, no_embeddings):
    r = call("search.query", vault=V(indexed), query="director")
    by_kind = {}
    for h in r["exact"]:
        by_kind.setdefault(h["kind"], []).append(h)
    ent = by_kind["entity"][0]
    assert ent["note"] == "entities/person/jane-doe" and ent["sha"] is None and ent["page"] is None
    doc = by_kind["document"][0]
    assert doc["sha"] == SHA1 and doc["note"] == "documents/report-one"
    assert doc["original"] == "morgue/acme-corp/annual-report/report-one.pdf"


def test_query_with_semantic_results(indexed, embeddings):
    r = call("search.query", vault=V(indexed), query="who is the director", top=3, threshold=0.4, rerank=False)
    assert embeddings == [
        {"query": "who is the director", "top_n": 3, "min_score": 0.4, "scope": "corpus", "rerank": False},
        {"query": "who is the director", "top_n": 3, "min_score": 0.4, "scope": "notes", "rerank": True}]
    assert r["index_empty"] is False
    p0, p1 = r["passages"]
    assert (p0["filename"], p0["page"], p0["score"]) == ("report-one.pdf", 2, 0.9123)
    assert (p0["sha"], p0["note"]) == (SHA1, "documents/report-one")
    assert p0["original"] == "morgue/acme-corp/annual-report/report-one.pdf"
    assert (p1["sha"], p1["note"], p1["original"]) == (None, None, None)
    n0, n1 = r["notes"]
    assert (n0["note_path"], n0["note"], n0["sha"], n0["preview"]) == (
        "documents/report-one", "documents/report-one", SHA1, "Report One…")
    assert (n1["note"], n1["sha"]) == ("entities/person/jane-doe", None)


def test_query_defaults_do_not_filter_by_score(indexed, embeddings):
    call("search.query", vault=V(indexed), query="x")
    assert embeddings[0]["min_score"] == -1.0 and embeddings[0]["top_n"] == 5 and embeddings[0]["rerank"] is True


def test_query_does_not_link_a_passage_whose_filename_is_shared(indexed, embeddings):
    path = indexed / ".watchdog" / "registry" / "documents.json"
    docs = json.loads(path.read_text())
    docs[SHA2]["filename"] = "report-one.pdf"
    path.write_text(json.dumps(docs))
    p0 = call("search.query", vault=V(indexed), query="x")["passages"][0]
    assert p0["sha"] is None and p0["note"] is None


def test_query_reports_a_semantic_failure_and_still_answers_exactly(indexed, monkeypatch):
    monkeypatch.setattr(embed, "index_stats", lambda v: {"passages": 1, "notes": 0, "total": 1})

    def broken(*a, **k):
        raise ImportError("No module named 'fastembed'")
    monkeypatch.setattr(embed, "search", broken)
    r = call("search.query", vault=V(indexed), query="Ghost")
    assert "fastembed" in r["semantic_error"] and r["passages"] == [] and r["exact"]


def test_query_reports_an_exact_failure_and_still_answers_semantically(indexed, embeddings, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("This Python's sqlite3 build has no FTS5 support")
    monkeypatch.setattr(fulltext, "search", broken)
    r = call("search.query", vault=V(indexed), query="Ghost")
    assert "FTS5" in r["exact_error"] and r["exact"] == [] and len(r["passages"]) == 2


def test_query_in_a_vault_with_no_indexes(rich_vault, no_embeddings):
    r = call("search.query", vault=V(rich_vault), query="Ghost")
    assert r["index_empty"] is True and r["exact"] == [] and r["exact_error"] is None
    assert not (rich_vault / ".fulltext").exists()      # a read never creates the index


def test_query_validation(indexed, no_embeddings):
    assert call_error("search.query", vault=V(indexed), query="  ")["code"] == "bad_params"
    assert call_error("search.query", vault=V(indexed), query="x", top="lots")["code"] == "bad_params"
    assert call_error("search.query", vault="/nope", query="x")["code"] == "not_a_vault"
    assert len(call("search.query", vault=V(indexed), query="Acme", top=0)["exact"]) == 1   # clamped to 1


# ── search.batch ─────────────────────────────────────────────────────────────────

def test_batch_in_one_vault(indexed):
    r = call("search.batch", vault=V(indexed), terms=["Ghost Ltd", " ", "jane", "Nobody Atall"])
    assert [t["term"] for t in r["terms"]] == ["Ghost Ltd", "jane", "Nobody Atall"]
    ghost, jane, nobody = r["terms"]
    assert ghost["checked"] is True and {h["kind"] for h in ghost["hits"]} == {"corpus"}
    assert {h["note"] for h in ghost["hits"]} == {"documents/report-one", "documents/report-two"}
    assert jane["hits"][0] == {"kind": "entity", "title": "Jane Doe", "note": "entities/person/jane-doe",
                               "page": None, "text": None, "path": "entities/person/jane-doe",
                               "sha": None, "type": "person"}
    assert nobody == {"term": "Nobody Atall", "checked": True, "hits": []}


def test_batch_marks_a_failed_lookup_unchecked(indexed, monkeypatch):
    real = fulltext.search

    def flaky(vault, term, **kw):
        if term == "bad":
            raise RuntimeError("no fts5")
        return real(vault, term, **kw)
    monkeypatch.setattr(fulltext, "search", flaky)
    r = call("search.batch", vault=V(indexed), terms=["bad", "Ghost"])
    assert r["terms"][0] == {"term": "bad", "checked": False, "hits": []}
    assert r["terms"][1]["checked"] is True and r["terms"][1]["hits"]


def test_batch_everywhere_tags_hits_with_the_vault_name(indexed, wdg_home, tmp_path):
    other = make_vault(tmp_path / "other")
    fulltext.add_document(other, "memo.pdf", "7" * 64, [{"page": 1, "markdown": "Ghost Ltd appears here too."}],
                          morgue_path="morgue/x/y/memo.pdf")
    register(wdg_home, indexed, slug="rich", name="Rich Case")
    register(wdg_home, other, slug="other", name="Other Case")
    r = call("search.batch", terms=["Ghost Ltd"], everywhere=True)
    (term,) = r["terms"]
    assert term["checked"] is True
    assert {h["vault_name"] for h in term["hits"]} == {"Rich Case", "Other Case"}
    assert any(h["title"] == "memo.pdf" and h["vault_name"] == "Other Case" for h in term["hits"])


def test_batch_validation(indexed):
    assert call_error("search.batch", vault=V(indexed), terms=[])["code"] == "bad_params"
    assert call_error("search.batch", vault=V(indexed), terms=["", " "])["code"] == "bad_params"
    assert call_error("search.batch", vault=V(indexed), terms="Ghost")["code"] == "bad_params"
    assert call_error("search.batch", terms=["x"])["code"] == "bad_params"
    assert call_error("search.batch", vault="/nope", terms=["x"])["code"] == "not_a_vault"


# ── search.everywhere ────────────────────────────────────────────────────────────

def test_everywhere(indexed, wdg_home, tmp_path):
    other = make_vault(tmp_path / "other")
    archived = make_vault(tmp_path / "archived")
    fulltext.add_document(archived, "a.pdf", "8" * 64, [{"page": 1, "markdown": "Ghost Ltd"}], morgue_path="m/a.pdf")
    register(wdg_home, indexed, slug="rich", name="Rich Case")
    register(wdg_home, other, slug="other", name="Other Case")
    register(wdg_home, archived, slug="archived", name="Archived", archived=True)
    register(wdg_home, tmp_path / "gone", slug="gone", name="Gone")
    r = call("search.everywhere", query="Ghost Ltd")
    assert [v["slug"] for v in r["vaults"]] == ["other", "rich"]      # in project-name order
    by_slug = {v["slug"]: v for v in r["vaults"]}
    assert set(by_slug) == {"rich", "other"}               # archived is not searched; gone is skipped
    rich = by_slug["rich"]
    assert rich["name"] == "Rich Case" and rich["path"] == V(indexed) and rich["error"] is None
    assert {h["title"] for h in rich["exact"]} == {"report-one.pdf", "report-two.pdf"}
    assert rich["exact"][0]["note"].startswith("documents/")
    assert by_slug["other"]["exact"] == [] and by_slug["other"]["entity_hits"] == []
    assert r["skipped"] == [{"slug": "gone", "reason": "folder not found"}]


def test_everywhere_entity_hits(indexed, wdg_home):
    register(wdg_home, indexed, slug="rich", name="Rich Case")
    (v,) = call("search.everywhere", query="doe")["vaults"]
    assert [e["id"] for e in v["entity_hits"]] == ["jane-doe"]


def test_everywhere_validation(wdg_home):
    assert call_error("search.everywhere", query="")["code"] == "bad_params"
    assert call("search.everywhere", query="x") == {"vaults": [], "skipped": []}


# ── search.status ────────────────────────────────────────────────────────────────

def test_status(indexed, embeddings):
    docs = indexed / ".embeddings" / "docs"
    docs.mkdir(parents=True)
    for name in ("a", "b"):
        (docs / f"{name}.npy").write_bytes(b"")
        (docs / f"{name}.json").write_text("[]")
    s = call("search.status", vault=V(indexed))
    assert s["total"] == 7 and s["documents"] == 2 and s["notes"] == 2 and s["passages"] == 5
    assert s["fulltext"] == {"corpus": 4, "notes": 2, "total": 6}


def test_status_of_an_unindexed_vault(rich_vault, no_embeddings):
    s = call("search.status", vault=V(rich_vault))
    assert s["total"] == 0 and s["documents"] == 0 and s["notes"] == 0
    assert s["fulltext"] == {"corpus": 0, "notes": 0, "total": 0}
