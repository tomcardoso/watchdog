"""The stored candidate harvest (D365): pre-processing harvests each page once, extraction reads
it. The checklist built from the store must equal the live harvest's, on the whole-document and
sectioned paths. GLiNER is always a deterministic fake here; the real model never loads."""

import json
import re

import pytest

from watchdog.pipeline import harvest, orchestrate, preflight, preprocess_batch, section


class _WordModel:
    """Fake GLiNER: every capitalised word is a person, every word ending in "Corp" an
    organization. Depends only on the words, so a trailing `---` word changes nothing."""

    def __init__(self):
        self.calls = 0

    def predict_entities(self, chunk, labels, threshold):
        self.calls += 1
        out = []
        for w in chunk.split():
            w = w.strip(".,;:")
            if w.endswith("Corp"):
                out.append({"label": "organization", "text": w})
            elif w[:1].isupper() and w.isalpha():
                out.append({"label": "person", "text": w})
        return out


class _WindowModel(_WordModel):
    """Fake GLiNER whose answer also depends on the whole window (its word count and last word),
    the way a real model's output depends on context."""

    def predict_entities(self, chunk, labels, threshold):
        words = chunk.split()
        return super().predict_entities(chunk, labels, threshold) + [
            {"label": "location", "text": f"W{len(words)}-{words[-1]}"}]


@pytest.fixture
def model(monkeypatch):
    m = _WordModel()
    monkeypatch.setattr(harvest, "_load_gliner", lambda: m)
    return m


def _page_md(n: int) -> str:
    rows = "\n".join(f"| item {i} | {n * 10 + i} | {n},{i:03d} |" for i in range(6))
    names = " ".join(f"Name{chr(65 + (n + k) % 26)}" for k in range(260))   # > one GLiNER window
    return (f"Header CV-21-00656040-00CL. Paid $1,{n:03d} on March {n % 28 + 1}, 2021 "
            f"to AcmeCorp; {n}.5% of fees. Smith met Jones.\n\n"
            f"| label | 2020 | 2021 |\n|---|---|---|\n{rows}\n\n{names}\n")


def _pages(count: int = 9) -> list[dict]:
    return [{"page": n, "markdown": _page_md(n)} for n in range(1, count + 1)]


def _live(text: str) -> str:
    return orchestrate._candidates_checklist(text)


def _stored(text: str, store: dict) -> str:
    return orchestrate._candidates_checklist(text, stored=store)


def test_whole_document_checklist_equals_live(model):
    pages = _pages()
    store = harvest.harvest_pages(pages)
    text = orchestrate._pages_text(pages)
    live = _live(text)
    assert "[person]" in live and "[docket]" in live and "[figure]" in live
    model.calls = 0
    assert _stored(text, store) == live
    assert model.calls == 0          # nothing ran the model at extraction


def test_whole_document_exact_even_for_a_context_sensitive_model(monkeypatch):
    m = _WindowModel()
    monkeypatch.setattr(harvest, "_load_gliner", lambda: m)
    pages = _pages(5)
    store = harvest.harvest_pages(pages)
    text = orchestrate._pages_text(pages)
    assert _stored(text, store) == _live(text)


def test_store_survives_json_round_trip(model):
    pages = _pages(4)
    store = json.loads(json.dumps(harvest.harvest_pages(pages)))
    text = orchestrate._pages_text(pages)
    assert _stored(text, store) == _live(text)


def _queued_vault(tmp_path, pages):
    vault = tmp_path / "v"
    (vault / ".watchdog" / "queue").mkdir(parents=True)
    (vault / ".watchdog" / "registry").mkdir()
    sha = "a" * 64
    (vault / ".watchdog" / "queue" / f"{sha}.json").write_text(json.dumps(
        {"sha256": sha, "filename": "d.pdf", "page_count": len(pages), "pages": pages,
         "harvest": harvest.harvest_pages(pages)}))
    return vault, sha


def test_sectioned_checklists_equal_live(tmp_path, model):
    pages = _pages(9)
    vault, sha = _queued_vault(tmp_path, pages)
    plan = section.run(vault, sha, force_budget=2500)
    assert plan["sectioned"] and len(plan["sections"]) >= 3
    pf = preflight.run(vault, sha)
    for sec in plan["sections"]:
        text = (vault / sec["pages_path"]).read_text(encoding="utf-8")
        model.calls = 0
        got = _stored(text, pf["harvest"])
        assert model.calls == 0
        assert got == _live(text)
        # A section's checklist covers only its own pages.
        assert {int(p) for p in re.findall(r"^p\.(\d+):", got, re.M)} <= \
            {int(p) for p in re.findall(r"<!-- PAGE (\d+) -->", text)}
    # Re-split halves (#540) are numbered pages too.
    halves = orchestrate._resplit_section(vault, sha, plan["sections"][0])
    for h in halves:
        text = (vault / h["pages_path"]).read_text(encoding="utf-8")
        assert _stored(text, pf["harvest"]) == _live(text)


def test_section_boundary_page_reads_as_it_does_mid_document(monkeypatch):
    """The one place stored and live can differ: a section's last page, which the live harvest
    reads without the `---` separator word that follows it in the whole document. The store keeps
    the whole-document reading, so the page gets the same names whichever section it lands in."""
    m = _WindowModel()
    monkeypatch.setattr(harvest, "_load_gliner", lambda: m)
    pages = _pages(4)
    store = harvest.harvest_pages(pages)
    whole = harvest.candidates(orchestrate._pages_text(pages), store)
    first_two = harvest.candidates(orchestrate._pages_text(pages[:2]), store)
    page2 = [c for c in first_two if c["page"] == 2]
    assert page2 == [c for c in whole if c["page"] == 2]
    live = harvest.harvest_entities(harvest.split_pages(orchestrate._pages_text(pages[:2])))
    assert [c for c in live if c["page"] == 2] != page2   # this fake does see the separator


def test_repeat_cap_counts_only_the_pages_a_section_covers(model):
    # The docket on every page is page furniture: kept on the first three pages of whatever text
    # is being harvested, so a section starting at page 5 keeps it on 5, 6 and 7.
    pages = _pages(9)
    store = harvest.harvest_pages(pages)
    text = orchestrate._pages_text(pages[4:])
    cands = harvest.candidates(text, store)
    assert [c["page"] for c in cands if c["kind"] == "docket"] == [5, 6, 7]
    assert cands == harvest.harvest(text) + harvest.harvest_entities(harvest.split_pages(text))


def test_character_split_section_has_no_names_and_needs_no_model(monkeypatch):
    def boom():
        raise AssertionError("GLiNER must not load for text with no page markers")
    monkeypatch.setattr(harvest, "_load_gliner", boom)
    text = "Paid $500 to Smith on March 3, 2021."
    assert harvest.candidates(text, {"format": 1}) == harvest.harvest(text)


def test_page_missing_from_store_is_harvested_live(model):
    pages = _pages(4)
    store = harvest.harvest_pages(pages[:3])
    text = orchestrate._pages_text(pages)
    model.calls = 0
    assert _stored(text, store) == _live(text)
    assert model.calls > 0


def test_live_names_mark_the_progress_row_only_when_gliner_runs(model):
    pages = _pages(2)
    store = harvest.harvest_pages(pages)
    text = orchestrate._pages_text(pages)
    marked = []
    harvest.candidates(text, store, before_live_names=lambda: marked.append(1))
    assert marked == []
    harvest.candidates(text, None, before_live_names=lambda: marked.append(1))
    assert marked == [1]


# ── stamps ────────────────────────────────────────────────────────────────────────────────

def test_names_stamp_mismatch_falls_back_to_live(model, monkeypatch):
    pages = _pages(3)
    store = harvest.harvest_pages(pages)
    # Plant a name the live model would never find; it shows only if the store is read.
    store["names"]["1"].insert(0, ["person", "Planted"])
    text = orchestrate._pages_text(pages)
    assert "Planted" in _stored(text, store)
    monkeypatch.setattr(harvest, "_GLINER_THRESHOLD", 0.5)
    model.calls = 0
    out = _stored(text, store)
    assert "Planted" not in out and model.calls > 0
    assert out == _live(text)


@pytest.mark.parametrize("change", ["_GLINER_MODEL", "_GLINER_LABELS", "_GLINER_WINDOW_WORDS",
                                    "_GLINER_WINDOW_OVERLAP"])
def test_every_gliner_setting_is_in_the_stamp(monkeypatch, change):
    before = harvest.names_stamp()
    new = {"_GLINER_MODEL": "other/model", "_GLINER_LABELS": ["person"],
           "_GLINER_WINDOW_WORDS": 300, "_GLINER_WINDOW_OVERLAP": 10}[change]
    monkeypatch.setattr(harvest, change, new)
    assert harvest.names_stamp() != before


def test_regex_stamp_mismatch_falls_back_to_live(model, monkeypatch):
    pages = _pages(2)
    store = harvest.harvest_pages(pages)
    store["regex"]["1"].insert(0, ["money", "$999,999"])
    text = orchestrate._pages_text(pages)
    assert "$999,999" in _stored(text, store)
    monkeypatch.setattr(harvest, "_REGEX_VERSION", harvest._REGEX_VERSION + 1)
    model.calls = 0
    out = _stored(text, store)
    assert "$999,999" not in out and out == _live(text)


def test_unknown_store_format_is_ignored(model):
    pages = _pages(2)
    store = harvest.harvest_pages(pages)
    store["format"] = 99
    store["regex"]["1"].insert(0, ["money", "$999,999"])
    text = orchestrate._pages_text(pages)
    assert _stored(text, store) == _live(text)


# ── GLiNER failure ────────────────────────────────────────────────────────────────────────

def test_gliner_load_failure_stores_regex_and_marks_names_missing(monkeypatch):
    def fail():
        raise OSError("model cache corrupted")
    monkeypatch.setattr(harvest, "_load_gliner", fail)
    pages = _pages(3)
    store = harvest.harvest_pages(pages)
    assert store["names"] is None and set(store["regex"]) == {"1", "2", "3"}
    text = orchestrate._pages_text(pages)
    # Still failing at extraction: the regex half only, as before.
    assert _stored(text, store) == harvest.format_checklist(harvest.harvest(text))
    # Working again at extraction: names are found live.
    m = _WordModel()
    monkeypatch.setattr(harvest, "_load_gliner", lambda: m)
    out = _stored(text, store)
    assert "[person]" in out and m.calls > 0 and out == _live(text)


def test_gliner_failure_mid_run_loses_every_page(monkeypatch):
    class Flaky(_WordModel):
        def predict_entities(self, chunk, labels, threshold):
            if self.calls >= 3:
                raise RuntimeError("out of memory")
            return super().predict_entities(chunk, labels, threshold)
    monkeypatch.setattr(harvest, "_load_gliner", lambda: Flaky())
    assert harvest.harvest_pages(_pages(3))["names"] is None


# ── pre-processing and pre-flight ─────────────────────────────────────────────────────────

def test_add_harvest_stores_it_on_a_queued_result(model):
    started = []
    result = {"pages": _pages(2), "char_count": 10}
    preprocess_batch.add_harvest(result, on_start=lambda: started.append(1))
    assert result["harvest"] == harvest.harvest_pages(_pages(2)) and started == [1]


@pytest.mark.parametrize("result", [{"error": "bad", "pages": [{"page": 1, "markdown": "x"}]},
                                    {"pages": [{"page": 1, "markdown": ""}], "char_count": 0}])
def test_add_harvest_skips_results_that_are_not_queued(model, result):
    preprocess_batch.add_harvest(result)
    assert "harvest" not in result and model.calls == 0


def test_add_harvest_never_raises(monkeypatch):
    def boom(pages):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(harvest, "harvest_pages", boom)
    result = {"pages": _pages(1), "char_count": 10}
    preprocess_batch.add_harvest(result)
    assert "harvest" not in result


def test_preflight_passes_the_stored_harvest(tmp_path, model):
    vault, sha = _queued_vault(tmp_path, _pages(2))
    assert preflight.run(vault, sha)["harvest"] == harvest.harvest_pages(_pages(2))
