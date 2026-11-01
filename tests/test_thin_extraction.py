"""Thin extractions (D367): a document whose extraction kept implausibly few facts for the words
of text it has is kept, warned about in processing.log, flagged on its record, kept for a retry,
and can be extracted again alone with Retry extraction (the `add` operation's `reextract`)."""

import asyncio
import json

import pytest

from watchdog.ops import ingest as ing
from watchdog.ops.ingest import RunOptions
from watchdog.pipeline import abort, ingest_setup, orchestrate, postflight
from watchdog.pipeline.postflight import _find_coverage_gap, find_thin_extraction

from tests.test_orchestrate import _extraction, _mock, _queue_doc
from tests.test_write_vault import make_vault

pytest_plugins = ["tests.gui_support"]   # the wdg_home fixture


def _facts(n):
    return [{"fact": f"Fact {i}.", "page": 1} for i in range(n)]


def _ext(n_facts):
    return {"document": {"key_facts": _facts(n_facts)}}


def _texts(words, pages=1):
    per = words // pages
    return {i + 1: " ".join(["word"] * per) for i in range(pages)}


# ── the rule ──────────────────────────────────────────────────────────────────────────────

def test_long_report_with_one_fact_is_flagged():
    # The shape of the benchmark collapses: one fact kept from a long report.
    thin = find_thin_extraction(_ext(1), _texts(11_968, pages=34))   # 352 words a page
    assert thin == {"facts": 1, "words": 11_968, "expected_min": 12}


def test_short_order_with_one_fact_is_flagged():
    # The third collapse: a 5-page order of about 1,200 words gave one fact (14–20 elsewhere).
    thin = find_thin_extraction(_ext(1), _texts(1_200, pages=5))
    assert thin and thin["expected_min"] == 2


@pytest.mark.parametrize("facts,words", [
    (48, 12_000),    # about one fact per 250 words: healthy
    (12, 12_000),    # exactly one per 1,000 words: the edge is not flagged
    (2, 1_200),
    (71, 26_000),
])
def test_healthy_extractions_are_not_flagged(facts, words):
    assert find_thin_extraction(_ext(facts), _texts(words)) is None


def test_short_document_is_never_flagged():
    # A cover letter or a nearly blank scan: under 1,000 words, one fact is plenty.
    assert find_thin_extraction(_ext(1), _texts(900)) is None


# ── an answer that ended early (D376) ─────────────────────────────────────────────────────

def _with_locators(*locators):
    return {"document": {"key_facts": [{"fact": f"Fact {i}.", "page": 1, "quote_locator": loc}
                                       for i, loc in enumerate(locators)]}}


@pytest.mark.parametrize("loc", ["x", "[]", "...", "", " ", []])
def test_a_placeholder_locator_in_a_short_answer_means_it_ended_early(loc):
    # The #804 collapses ended after a fact or two carrying "x" or "[]" as a locator. A short
    # document (under the words-per-fact bar) is caught too.
    thin = find_thin_extraction(_with_locators("The court ordered that", loc), _texts(900))
    assert thin == {"facts": 2, "words": 900, "expected_min": None, "ended_early": True}
    assert "ended early" in postflight.render_thin_warning(thin)


def test_both_signals_together_keep_the_expected_count():
    thin = find_thin_extraction(_with_locators("x"), _texts(11_968, pages=34))
    assert thin == {"facts": 1, "words": 11_968, "expected_min": 12, "ended_early": True}
    assert "at least 12 expected" in postflight.render_thin_warning(thin)


def test_real_locators_and_omitted_ones_are_not_placeholders():
    ok = _with_locators("The Applicant shall be entitled", "Ernst & Young Inc. is", None, "$2,000,000")
    assert find_thin_extraction({"document": {"key_facts": ok["document"]["key_facts"][:3]}},
                                _texts(900)) is None
    assert find_thin_extraction(_with_locators("$2,000,000"), _texts(900)) is None


def test_a_placeholder_in_a_full_answer_is_not_a_collapse():
    # One stray placeholder among many facts is a bad locator, not an answer that stopped.
    facts = [f"Sentence {i} of the order" for i in range(3)] + ["x"]
    assert find_thin_extraction(_with_locators(*facts), _texts(900)) is None
    assert find_thin_extraction(_ext(1), _texts(40)) is None


def test_zero_facts_and_missing_text_are_left_to_others():
    assert find_thin_extraction(_ext(0), _texts(12_000)) is None   # the empty-extraction guard's
    assert find_thin_extraction(_ext(1), {}) is None                # no text to weigh
    assert find_thin_extraction(_ext(1), None) is None


def test_threshold_is_configurable_and_zero_turns_it_off(monkeypatch):
    monkeypatch.setattr(postflight, "_config_get", lambda k, d: 0 if k == "thin_extraction_words_per_fact" else d)
    assert find_thin_extraction(_ext(1), _texts(12_000)) is None
    monkeypatch.setattr(postflight, "_config_get", lambda k, d: 5_000 if k == "thin_extraction_words_per_fact" else d)
    assert find_thin_extraction(_ext(3), _texts(12_000)) is None
    assert find_thin_extraction(_ext(1), _texts(12_000))["expected_min"] == 3


def test_coverage_gap_reads_passage_page_when_a_fact_has_no_page():
    # The collapsed facts had `page: None`; their source passage was still found on a page.
    ext = {"document": {"key_facts": [{"fact": "x", "page": None, "passage_page": 2}]}}
    assert _find_coverage_gap(ext, 40) == {"start": 3, "end": 40, "pages": 38}
    # Neither anchor: still not assessable.
    assert _find_coverage_gap({"document": {"key_facts": [{"fact": "x"}]}}, 40) is None


# ── through a run ─────────────────────────────────────────────────────────────────────────

LONG_TEXT = " ".join(["word"] * 3_000)
SHA = "abc123"


def _no_local_ner(monkeypatch):
    # The local entity model is slow and beside the point here.
    from watchdog.pipeline import harvest
    monkeypatch.setattr(harvest, "harvest_entities", lambda *a, **k: [])


def _thin_run(tmp_path, monkeypatch):
    _no_local_ner(monkeypatch)
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha=SHA, text=LONG_TEXT)
    _mock(monkeypatch, extraction=_extraction(sha=SHA))   # one fact from 3,000 words
    asyncio.run(orchestrate.run(vault))
    return vault


def _record(vault):
    docs = json.loads((vault / ".watchdog" / "registry" / "documents.json").read_text())
    return docs[SHA]


def test_thin_document_is_kept_warned_flagged_and_kept_for_retry(tmp_path, monkeypatch):
    vault = _thin_run(tmp_path, monkeypatch)

    rec = _record(vault)   # committed: the result was not discarded
    assert rec["thin_extraction"] == {"facts": 1, "words": 3_000, "expected_min": 3}

    log = (vault / ".watchdog" / "registry" / "processing.log").read_text(encoding="utf-8")
    assert "OK test-doc.pdf" in log
    warn = [ln for ln in log.splitlines() if "WARN test-doc.pdf: thin extraction" in ln]
    assert warn and "1 fact from 3,000 words" in warn[0]

    # The queue descriptor is removed at commit, but a copy is kept for Retry extraction.
    assert not (vault / ".watchdog" / "queue" / f"{SHA}.json").exists()
    assert (vault / ".watchdog" / "queue" / "_kept" / f"{SHA}.json").exists()

    from watchdog.gui import vaultio
    row = vaultio.document_row(vault, SHA, rec)
    assert row["thin_extraction"] == {"facts": 1, "words": 3_000, "expected_min": 3,
                                      "ended_early": False, "retry": True}


def test_healthy_document_is_not_flagged_and_keeps_nothing(tmp_path, monkeypatch):
    _no_local_ner(monkeypatch)
    vault = make_vault(tmp_path)
    _queue_doc(vault, sha=SHA, text="Acme Corp filed an annual report.")
    _mock(monkeypatch, extraction=_extraction(sha=SHA))
    asyncio.run(orchestrate.run(vault))
    assert _record(vault)["thin_extraction"] is None
    assert not (vault / ".watchdog" / "queue" / "_kept" / f"{SHA}.json").exists()
    from watchdog.gui import vaultio
    assert vaultio.document_row(vault, SHA, _record(vault))["thin_extraction"] is None


def test_retry_that_comes_back_healthy_replaces_the_extraction_and_clears_the_flag(tmp_path, monkeypatch):
    vault = _thin_run(tmp_path, monkeypatch)
    better = _extraction(sha=SHA)
    better["document"]["key_facts"] = [{"fact": f"Fact {i}.", "page": 1} for i in range(6)]
    _mock(monkeypatch, extraction=better)

    restored, missing = ingest_setup.restore_for_reextract(vault, [SHA])
    assert restored == [SHA] and missing == []
    asyncio.run(orchestrate.run(vault, force=True, only_shas=[SHA]))

    assert _record(vault)["thin_extraction"] is None
    staged = json.loads((vault / ".watchdog" / "extracted" / f"{SHA}.json").read_text())
    assert len(staged["document"]["key_facts"]) == 6
    assert not (vault / ".watchdog" / "queue" / "_kept" / f"{SHA}.json").exists()
    assert not (vault / ".watchdog" / "queue" / f"{SHA}.json").exists()


def test_retry_that_is_still_thin_keeps_the_warning_and_the_copy(tmp_path, monkeypatch):
    vault = _thin_run(tmp_path, monkeypatch)
    ingest_setup.restore_for_reextract(vault, [SHA])
    asyncio.run(orchestrate.run(vault, force=True, only_shas=[SHA]))
    assert _record(vault)["thin_extraction"]["facts"] == 1
    assert (vault / ".watchdog" / "queue" / "_kept" / f"{SHA}.json").exists()


def test_failed_retry_leaves_the_committed_document_as_it_was(tmp_path, monkeypatch):
    vault = _thin_run(tmp_path, monkeypatch)
    staged = vault / ".watchdog" / "extracted" / f"{SHA}.json"
    before = staged.read_text()
    ingest_setup.restore_for_reextract(vault, [SHA])

    abort.run(vault, SHA, keep_section_checkpoints=True)   # what a failed extraction does

    assert staged.read_text() == before                     # the stored extraction stays
    assert not (vault / ".watchdog" / "queue" / f"{SHA}.json").exists()
    assert not (vault / ".watchdog" / "queue" / "_failed" / f"{SHA}.json").exists()
    assert (vault / ".watchdog" / "queue" / "_kept" / f"{SHA}.json").exists()


def test_reextract_queue_reads_the_kept_copy_without_writing(tmp_path, monkeypatch):
    vault = _thin_run(tmp_path, monkeypatch)
    files = ingest_setup.reextract_queue(vault, [SHA, "nope"])
    assert [f["sha256"] for f in files] == [SHA]
    assert files[0]["path"].endswith(f"_kept/{SHA}.json")
    assert not (vault / ".watchdog" / "queue" / f"{SHA}.json").exists()


# ── the operation ─────────────────────────────────────────────────────────────────────────

def _fake_orchestrate(monkeypatch, calls):
    from watchdog import auth as auth_module
    monkeypatch.setattr(auth_module, "resolve_auth", lambda: {"mode": "api-key", "key": "sk-x"})
    monkeypatch.setattr(orchestrate, "has_pending_finalization", lambda v: False)

    async def fake_run(*a, **k):
        calls.append(k)
        return {"results": [], "extracted": 0, "skipped": 0, "failed": 0, "cancelled": False,
                "rate_limited": False, "stop_message": None, "rate_limit_resets_at": None,
                "quarantined": 0, "finalize_skipped": False}
    monkeypatch.setattr(orchestrate, "run", fake_run)


def test_reextract_runs_only_that_document_forced(wdg_home, tmp_path, monkeypatch):
    vault = _thin_run(tmp_path, monkeypatch)
    other = vault / ".watchdog" / "queue" / "other.json"
    other.write_text(json.dumps({"sha256": "other", "filename": "o.pdf", "page_count": 1,
                                 "pages": [{"page": 1, "markdown": "x"}]}))
    monkeypatch.chdir(vault)
    calls = []
    _fake_orchestrate(monkeypatch, calls)

    ing.process(vault, RunOptions(skip_warning=True, reextract=[SHA]), finalize=True)

    assert calls[0]["force"] is True
    assert calls[0]["only_shas"] == [SHA]
    # The fake run re-extracted nothing, so the restored descriptor is taken back out; the
    # other queued document is untouched.
    assert not (vault / ".watchdog" / "queue" / f"{SHA}.json").exists()
    assert other.exists()


def test_reextract_with_nothing_kept_stops_before_sending(wdg_home, tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    monkeypatch.chdir(vault)
    calls = []
    _fake_orchestrate(monkeypatch, calls)
    with pytest.raises(SystemExit):
        ing.process(vault, RunOptions(skip_warning=True, reextract=["gone"]), finalize=True)
    assert calls == []


def test_add_operation_takes_reextract():
    from watchdog import ops
    assert ops.schema()["add"]["params"]["reextract"]["type"] == "list[str]"


def test_app_preflight_counts_only_the_document_to_retry(tmp_path, monkeypatch):
    from watchdog.gui.api import ingest as api
    vault = _thin_run(tmp_path, monkeypatch)
    opts = api.run_options({"reextract": [SHA]})
    queue_files, to_send = api._queue(vault, opts)
    assert [q["sha256"] for q in to_send] == [SHA] and queue_files == to_send
    with pytest.raises(Exception):
        api.run_options({"reextract": "abc"})
