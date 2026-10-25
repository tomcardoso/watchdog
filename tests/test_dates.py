"""Date reading (D329), the housekeeping framework and its date chore (D330), and date ranges
on the timeline. Every date string here is invented."""

import asyncio
import json

import pytest

from watchdog import model_client
from watchdog.pipeline import dates, housekeeping, orchestrate, postflight, timeline


def _r(text):
    r = dates.read(text)
    return r.date, r.pending


# ── reading (D329) ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, iso", [
    ("12 septembre 2019", "2019-09-12"),          # French
    ("1er mars 2021", "2021-03-01"),
    ("lundi 3 juin 2019", "2019-06-03"),
    ("3 maggio 2020", "2020-05-03"),              # Italian
    ("1 dicembre 2019", "2019-12-01"),
    ("15 de agosto de 2016", "2016-08-15"),       # Spanish
    ("5. Mai 2017", "2017-05-05"),                # German
    ("Montag, 3. Juni 2019", "2019-06-03"),
    ("15 de março de 2021", "2021-03-15"),        # Portuguese
    ("15 marca 2021", "2021-03-15"),              # Polish
    ("15 березня 2021", "2021-03-15"),            # Ukrainian
    ("15 марта 2021 г.", "2021-03-15"),           # Russian
    ("15. maaliskuuta 2021", "2021-03-15"),       # Finnish
    ("15 Μαρτίου 2021", "2021-03-15"),            # Greek
    ("15 मार्च 2021", "2021-03-15"),              # Hindi
    ("١٥ مارس ٢٠٢١", "2021-03-15"),               # Arabic, with Arabic-Indic digits
    ("15 tháng 3 năm 2021", "2021-03-15"),        # Vietnamese
    ("15 Maret 2021", "2021-03-15"),              # Indonesian
    ("2021年3月15日", "2021-03-15"),               # Chinese / Japanese
    ("2021년 3월 15일", "2021-03-15"),             # Korean (read by code: dateparser misses it)
    ("April 30, 2020", "2020-04-30"),             # the English forms the old month table read
    ("Feb. 1, 2021", "2021-02-01"),
    ("17th day of March, 2021", "2021-03-17"),
    ("17 th day of March, 2021", "2021-03-17"),
    ("February 1, 2021,", "2021-02-01"),
    ("27-Sep-2012", "2012-09-27"),
    ("18-SEP-24", "2024-09-18"),
    ("15-Mar-70", "1970-03-15"),
    ("3 June 2019 at 10:00", "2019-06-03"),
])
def test_reads_dates_in_many_languages(text, iso):
    assert _r(text) == (iso, None)


@pytest.mark.parametrize("text, iso", [
    ("mai 2020", "2020-05"), ("Mai 2017", "2017-05"), ("décembre 2019", "2019-12"),
    ("березень 2021", "2021-03"), ("2021年3月", "2021-03"), ("May 2019.", "2019-05"),
    ("09/2019", "2019-09"), ("2024/03", "2024-03"),
])
def test_keeps_only_the_precision_the_wording_gives(text, iso):
    assert _r(text) == (iso, None)


@pytest.mark.parametrize("text", ["March", "mars", "le 3", "the 3rd", "21 novembre", "3rd of May",
                                  "septembre", "3 mars"])
def test_a_date_without_a_printed_year_is_never_placed_by_code(text):
    assert _r(text) == ("", dates.PENDING_UNRESOLVED)


@pytest.mark.parametrize("text", ["yesterday", "hier", "in two days", "the following Tuesday",
                                  "sometime last year", "dans les 30 jours"])
def test_relative_dates_are_never_parsed(text):
    assert _r(text) == ("", dates.PENDING_UNRESOLVED)


@pytest.mark.parametrize("text", [
    "15 Mart 2021",         # read as a time zone, month filled from the reference date
    "2019-W12",             # a week, month filled in
    "3 and 7 June 2019",    # two days
    "after March 2020", "early June 2019", "circa 2019", "fin juin 2019",
    "April–June 2024", "2020-2021", "Q1 2020", "du 3 au 7 juin 2019",
    "le 03/04/2021",        # figures inside words: dateparser would pick an order
    "February 30, 2020", "30/02/2020", "12.09.19",
])
def test_what_code_cannot_settle_goes_to_the_pass(text):
    assert _r(text) == ("", dates.PENDING_UNRESOLVED)


def test_ambiguous_numeric_date_keeps_its_year_and_both_readings():
    r = dates.read("03/04/2021")
    assert (r.date, r.pending, r.readings) == ("2021", dates.PENDING_AMBIGUOUS, ("2021-04-03", "2021-03-04"))
    r = dates.read("2020/07/08")
    assert r.pending == dates.PENDING_AMBIGUOUS and r.readings == ("2020-07-08", "2020-08-07")


@pytest.mark.parametrize("text, iso", [
    ("13/04/2021", "2021-04-13"), ("04.04.2021", "2021-04-04"), ("2019/08/16", "2019-08-16"),
    ("2020-7-8", "2020-07-08"),
])
def test_a_numeric_date_with_one_reading_is_read(text, iso):
    assert _r(text) == (iso, None)


def test_valid_iso_rejects_impossible_dates_and_years():
    assert dates.valid_iso("2020-02-29") and dates.valid_iso("2020-02") and dates.valid_iso("2020")
    assert not dates.valid_iso("2021-02-29")
    assert not dates.valid_iso("2021-13")
    assert not dates.valid_iso("2564-03")
    assert not dates.valid_iso("20210315")


# ── validation of the model's answers (D330) ─────────────────────────────────────────────

def _item(text, **kw):
    return {"sha": "s", "index": 0, "text": text, "fact": kw.get("fact", "A council vote."),
            "doc_date": kw.get("doc_date"), "pending": kw.get("pending", dates.PENDING_UNRESOLVED),
            "readings": kw.get("readings", [])}


def test_validate_accepts_a_range_and_a_single_date():
    assert dates.validate(_item("April–June 2024"), {"start": "2024-04", "end": "2024-06"}) == \
        {"start": "2024-04", "end": "2024-06"}
    assert dates.validate(_item("Q1 2020"), {"start": "2020-01", "end": "2020-03"})
    assert dates.validate(_item("le 03/04/2021"), {"start": "2021-04-03", "end": ""}) == \
        {"start": "2021-04-03", "end": ""}


def test_validate_treats_an_empty_answer_as_no_date():
    assert dates.validate(_item("spring 2019"), {"start": "", "end": ""}) == {}


@pytest.mark.parametrize("answer", [
    {"start": "2024-13", "end": ""},             # no such month
    {"start": "2021-02-29", "end": ""},          # no such day
    {"start": "April 2024", "end": ""},          # not ISO
    {"start": "2024-06", "end": "2024-04"},      # ends before it starts
    {"start": "2031-04", "end": ""},             # outside the printed year
    {"start": "", "end": "2024-06"},             # an end with no start
    {"start": 2024, "end": ""},                  # not a string
])
def test_validate_rejects_malformed_answers(answer):
    assert dates.validate(_item("April–June 2024"), answer) is None


def test_validate_holds_an_ambiguous_date_to_its_two_readings():
    item = _item("03/04/2021", pending=dates.PENDING_AMBIGUOUS, readings=["2021-04-03", "2021-03-04"])
    assert dates.validate(item, {"start": "2021-03-04", "end": ""}) == {"start": "2021-03-04", "end": ""}
    assert dates.validate(item, {"start": "2021-04", "end": ""}) is None
    assert dates.validate(item, {"start": "2021-03-04", "end": "2021-04-03"}) is None


def test_validate_anchors_a_relative_date_to_the_fact_or_the_document():
    assert dates.validate(_item("the following Tuesday", doc_date="2019-12-30"),
                          {"start": "2020-01-07", "end": ""})
    assert dates.validate(_item("the following Tuesday", doc_date="2019-12-30"),
                          {"start": "2023-01-07", "end": ""}) is None
    assert dates.validate(_item("the following Tuesday"), {"start": "2020-01-07", "end": ""}) is None
    assert dates.validate(_item("two days later", fact="Two days after the 2018 vote, it resigned."),
                          {"start": "2018-05-03", "end": ""})


# ── the housekeeping framework (D330) ────────────────────────────────────────────────────

def _result(parsed):
    return model_client.ModelResult(parsed=parsed, text="", model="fake", backend="claude-api",
                                    auth_mode="api-key", usage={"input_tokens": 1, "output_tokens": 1},
                                    cost_usd=0.0, latency_s=0.0)


def _chore(max_items=2):
    return housekeeping.Chore(task="t", schema={}, build_prompt=lambda items: "|".join(items),
                              validate=lambda item, a: a["value"] if a.get("value") else None,
                              max_items=max_items)


def test_run_chore_makes_no_call_when_there_is_nothing_to_do():
    async def call(**kw):
        raise AssertionError("no call expected")
    res = asyncio.run(housekeeping.run_chore(_chore(), [], call=call))
    assert res.calls == 0 and res.settled == {}


def test_run_chore_splits_validates_and_survives_malformed_answers():
    prompts = []

    async def call(*, task, schema, prompt, detail):
        prompts.append(prompt)
        if prompt == "a|b":
            return _result({"answers": [{"item": 1, "value": "A"}, {"item": 1, "value": "dup"},
                                        {"item": 9, "value": "x"}, "junk", {"item": 2}]})
        return _result({"answers": "not a list"})

    res = asyncio.run(housekeeping.run_chore(_chore(), ["a", "b", "c"], call=call))
    assert prompts == ["a|b", "c"]
    assert res.settled == {0: "A"} and res.calls == 2
    assert res.rejected == 4 and not res.unanswered


def test_run_chore_stops_at_the_spending_limit_between_calls():
    async def call(**kw):
        return _result({"answers": []})
    res = asyncio.run(housekeeping.run_chore(_chore(max_items=1), ["a", "b", "c"], call=call,
                                             stop=lambda: "limit reached"))
    assert res.calls == 1 and res.stopped == "limit reached" and res.unanswered == {1, 2}


def test_run_chore_a_halting_failure_leaves_the_rest_unanswered():
    async def call(**kw):
        raise model_client.RateLimitError("slow down")
    res = asyncio.run(housekeeping.run_chore(
        _chore(max_items=1), ["a", "b"], call=call, failures=model_client.CALL_FAILURES,
        halting=(model_client.RateLimitError,)))
    assert res.unanswered == {0, 1} and res.calls == 0 and "slow down" in res.error


def test_answers_schema_shape():
    s = housekeeping.answers_schema({"start": {"type": "string"}}, ["start"])
    item = s["properties"]["answers"]["items"]
    assert item["required"] == ["item", "start"] and item["additionalProperties"] is False


# ── the date chore end to end, before the commit ─────────────────────────────────────────

SHA = "d" * 64


def _stage(vault, facts, doc_date="2024-07-01"):
    ext = {"document": {"sha256": SHA, "filename": "minutes.txt", "date_of_document": doc_date,
                        "key_facts": facts},
           "entities": [{"id": "harbour-board", "name": "Harbour Board", "type": "Organization"}]}
    dates.sanitize(ext)
    postflight.explode_key_facts(ext)
    (vault / ".watchdog" / "extracted").mkdir(parents=True)
    (vault / ".watchdog" / "extracted" / f"{SHA}.json").write_text(json.dumps(ext), encoding="utf-8")
    timeline.stage_timeline_events(vault, ext)
    return ext


def _facts():
    return [
        {"fact": "The board held hearings.", "date": "April–June 2024", "entities": ["harbour-board"]},
        {"fact": "The board met.", "date": "03/04/2024", "entities": ["harbour-board"]},
        {"fact": "The board adjourned.", "date": "sometime", "entities": ["harbour-board"]},
    ]


def test_date_pass_applies_a_range_and_rejects_a_malformed_answer(tmp_path, monkeypatch):
    _stage(tmp_path, _facts())
    seen = []

    async def fake(**kw):
        seen.append(kw)
        assert kw["task"] == "date-resolve" and kw["schema"] is dates.SCHEMA
        assert '"April–June 2024"' in kw["prompt"] and "2024-04-03 or 2024-03-04" in kw["prompt"]
        return _result({"answers": [
            {"item": 1, "start": "2024-04", "end": "2024-06"},
            {"item": 2, "start": "2024-05-01", "end": ""},      # not one of its two readings
            {"item": 3, "start": "", "end": ""},
        ]})

    monkeypatch.setattr(orchestrate, "_call_model", fake)
    out = asyncio.run(orchestrate._date_pass(tmp_path, [SHA], None, "haiku", None, None))
    assert len(seen) == 1
    assert out["resolved"] == 1 and out["rejected"] == 1

    art = json.loads((tmp_path / ".watchdog" / "extracted" / f"{SHA}.json").read_text(encoding="utf-8"))
    hearing, meeting, adjourned = art["document"]["key_facts"]
    assert (hearing["date"], hearing["date_end"], hearing["date_basis"]) == ("2024-04", "2024-06", "inferred")
    assert hearing["date_text"] == "April–June 2024" and "date_pending" not in hearing
    assert meeting["date"] == "2024" and "date_basis" not in meeting and "date_pending" not in meeting
    assert adjourned["date"] == "" and "date_pending" not in adjourned
    events = art["entities"][0]["timeline_events"]
    assert {(e["date"], e.get("date_end")) for e in events} == {("2024-04", "2024-06"), ("2024", None)}

    raws = sorted(p.name for p in (tmp_path / ".watchdog" / "timeline").glob("*.ndjson"))
    assert raws == [f"2024-04_{SHA[:7]}.ndjson", f"2024_{SHA[:7]}.ndjson"]
    rec = json.loads((tmp_path / ".watchdog" / "timeline" / raws[0]).read_text(encoding="utf-8"))
    assert rec["date_end"] == "2024-06" and rec["date_basis"] == "inferred"

    # Nothing pending any more: no second call.
    asyncio.run(orchestrate._date_pass(tmp_path, [SHA], None, "haiku", None, None))
    assert len(seen) == 1


def test_date_pass_failure_keeps_what_code_read(tmp_path, monkeypatch):
    _stage(tmp_path, _facts())

    async def boom(**kw):
        raise model_client.ModelError("down")

    monkeypatch.setattr(orchestrate, "_call_model", boom)
    out = asyncio.run(orchestrate._date_pass(tmp_path, [SHA], None, "haiku", None, None))
    assert out["resolved"] == 0 and out["unanswered"] == 3
    art = json.loads((tmp_path / ".watchdog" / "extracted" / f"{SHA}.json").read_text(encoding="utf-8"))
    assert [f["date"] for f in art["document"]["key_facts"]] == ["", "2024", ""]
    assert all(f.get("date_pending") for f in art["document"]["key_facts"])


def test_date_pass_counts_towards_the_finalize_estimate(tmp_path):
    from watchdog.pipeline import ingest_setup
    _stage(tmp_path, _facts())
    est = ingest_setup.housekeeping_estimate(tmp_path, [SHA])
    assert est["items"] == 3 and est["calls"] == 1 and est["est_tokens"] > 0
    assert "date-resolve" in orchestrate.FINALIZE_TASKS


# ── ranges on the timeline ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("start, end, shown", [
    ("2024-04", "2024-06", "Apr–Jun 2024"),
    ("2024-04-03", "2024-04-07", "3–7 Apr 2024"),
    ("2024-04-03", "2024-06-07", "3 Apr – 7 Jun 2024"),
    ("2019", "2021", "2019–2021"),
    ("2019-12", "2020-02", "Dec 2019 – Feb 2020"),
    ("2019", "2020-02", "2019 – February 2020"),
    ("2024-04-03", None, "3 Apr 2024"),
    ("2024-04", "nonsense", "April 2024"),
])
def test_render_span(start, end, shown):
    assert dates.render_span(start, end) == shown


def _write_canonical(vault, date, recs):
    td = vault / ".watchdog" / "timeline"
    td.mkdir(parents=True, exist_ok=True)
    (td / f"{date}.ndjson").write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")


def test_timeline_md_shows_a_range_under_its_start_with_the_inferred_wording(tmp_path):
    _write_canonical(tmp_path, "2024-04", [{
        "date": "2024-04", "date_end": "2024-06", "date_text": "April–June 2024",
        "date_basis": "inferred", "event": "The board held hearings.", "source_sha256": SHA,
        "entity_ids": []}])
    timeline.rebuild_timeline(tmp_path, quiet=True)
    md = (tmp_path / "timeline.md").read_text(encoding="utf-8")
    assert "## 2024" in md
    assert "**Apr–Jun 2024**" in md and "date inferred from “April–June 2024”" in md


def test_timeline_dedup_folds_only_when_both_ends_match():
    events = [{"date": "2024-04", "event": "Hearings.", "date_end": "2024-06", "entity_ids": ["a"]},
              {"date": "2024-04", "event": "Hearings held.", "entity_ids": ["b"]},
              {"date": "2024-04", "event": "Hearings ran.", "date_end": "2024-06", "entity_ids": ["c"]}]
    kept = orchestrate._select_kept(events, [{"keep": 0, "duplicates": [1, 2]}])
    assert [e["event"] for e in kept] == ["Hearings.", "Hearings held."]
    assert kept[0]["entity_ids"] == ["a", "c"]


def test_staging_keeps_a_range_apart_from_the_same_text_on_one_date(tmp_path):
    ext = {"document": {"sha256": SHA, "key_facts": [
        {"fact": "Hearings.", "date": "2024-04", "date_end": "2024-06"},
        {"fact": "Hearings.", "date": "2024-04"}]}}
    timeline.stage_timeline_events(tmp_path, ext)
    lines = (tmp_path / ".watchdog" / "timeline" / f"2024-04_{SHA[:7]}.ndjson").read_text().splitlines()
    assert len(lines) == 2


def test_a_range_is_left_out_of_the_month_precision_pass(tmp_path):
    _write_canonical(tmp_path, "2024-04", [{"date": "2024-04", "date_end": "2024-06", "event": "Hearings."}])
    _write_canonical(tmp_path, "2024-04-03", [{"date": "2024-04-03", "event": "A hearing."}])
    assert timeline.month_precision_groups(tmp_path) == []


def test_app_timeline_event_carries_the_range_and_the_inferred_mark():
    from watchdog.gui import vaultio
    ev = vaultio.timeline_event({"date": "2024-04", "date_end": "2024-06", "date_text": "April–June 2024",
                                 "date_basis": "inferred", "event": "Hearings."}, {}, {})
    assert (ev["end"], ev["end_precision"], ev["date_text"], ev["date_inferred"]) == \
        ("2024-06", "month", "April–June 2024", True)
    plain = vaultio.timeline_event({"date": "2024-04-03", "event": "A hearing."}, {}, {})
    assert plain["end"] is None and plain["date_inferred"] is False and plain["date_text"] is None
