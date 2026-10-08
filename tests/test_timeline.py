import json
import os
from pathlib import Path

from watchdog.pipeline.timeline import (
    stage_timeline_events,
    cmd_timeline_collisions,
    cmd_rebuild_timeline,
    collisions,
    month_precision_groups,
    apply_precision_matches,
)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _vault(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    (v / ".watchdog").mkdir(parents=True)
    return v


def _extraction(key_facts: list[dict], sha: str = "abcdef1234567") -> dict:
    """Build an extraction whose timeline derives from dated document.key_facts (#140)."""
    return {"document": {"sha256": sha, "key_facts": key_facts}, "entities": []}


def _read_ndjson(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ── stage_timeline_events ───────────────────────────────────────────────────

def test_stage_writes_one_file_per_date(tmp_path):
    vault = _vault(tmp_path)
    n = stage_timeline_events(vault, _extraction([
        {"fact": "Appointed director", "date": "2020-03-15", "page": 2, "entities": ["alice"]},
        {"fact": "Resigned", "date": "2021", "page": 5, "basis": "inferred", "entities": ["alice"]},
        {"fact": "Owns a controlling stake", "entities": ["alice"]},   # no date → not a timeline event
    ], sha="abcdef1234567"))

    assert n == 2
    td = vault / ".watchdog" / "timeline"
    f1 = td / "2020-03-15_abcdef1.ndjson"
    f2 = td / "2021_abcdef1.ndjson"
    assert f1.exists() and f2.exists()
    assert _read_ndjson(f1)[0] == {
        "date": "2020-03-15",
        "event": "Appointed director",
        "source_sha256": "abcdef1234567",
        "page": 2,
        "entity_ids": ["alice"],
        "basis": "stated",
    }


def test_stage_unions_entity_tags_on_same_event(tmp_path):
    """Same (date, event text) collapses to one record, unioning the two source facts' entity
    tags (#237) — that union is what lets the deduped global timeline attribute an event to every
    entity it concerns, not just the one the surviving restatement happened to carry."""
    vault = _vault(tmp_path)
    stage_timeline_events(vault, _extraction([
        {"fact": "Acme filed for bankruptcy", "date": "2020-03-15", "entities": ["alice"]},
        {"fact": "Acme filed for bankruptcy", "date": "2020-03-15", "entities": ["acme"]},
    ], sha="sha12340000"))

    recs = _read_ndjson(vault / ".watchdog" / "timeline" / "2020-03-15_sha1234.ndjson")
    assert len(recs) == 1
    assert recs[0]["entity_ids"] == ["alice", "acme"]


def test_stage_keeps_distinct_events_on_same_date(tmp_path):
    vault = _vault(tmp_path)
    stage_timeline_events(vault, _extraction([
        {"fact": "Event A", "date": "2020-03-15", "entities": ["alice"]},
        {"fact": "Event B", "date": "2020-03-15", "basis": "inferred", "entities": ["alice"]},
    ], sha="zzzzzzz9999"))

    recs = _read_ndjson(vault / ".watchdog" / "timeline" / "2020-03-15_zzzzzzz.ndjson")
    assert {r["event"] for r in recs} == {"Event A", "Event B"}


def test_stage_keeps_distinct_events_with_long_shared_opening(tmp_path):
    """Regression guard: dedup keys on the full fact text today, not a truncated prefix. A
    prefix-based key (what this function used before) would treat these two facts as one and
    silently drop the second — the opening clause is identical, but who the property went to
    (the material fact) differs after the shared part."""
    vault = _vault(tmp_path)
    shared_opening = ("On March 3, 2019, Acme Holdings Ltd. transferred beneficial ownership of "
                       "the Cayman Islands shell company, via an intermediary numbered offshore "
                       "company incorporated for this purpose, to ")
    assert len(shared_opening) > 150   # not tied to any number in the source; just long enough

    stage_timeline_events(vault, _extraction([
        {"fact": shared_opening + "John Smith.", "date": "2019-03-03", "entities": ["alice"]},
        {"fact": shared_opening + "Jane Doe's family trust.", "date": "2019-03-03", "entities": ["alice"]},
    ], sha="longopen0000"))

    recs = _read_ndjson(vault / ".watchdog" / "timeline" / "2019-03-03_longope.ndjson")
    assert len(recs) == 2
    assert any(r["event"].endswith("John Smith.") for r in recs)
    assert any(r["event"].endswith("family trust.") for r in recs)


def test_stage_keeps_untagged_dated_fact(tmp_path):
    vault = _vault(tmp_path)
    stage_timeline_events(vault, _extraction([
        {"fact": "The hearing was held by Zoom", "date": "2020-03-15"},   # no entity tags
    ], sha="untag00000aa"))

    recs = _read_ndjson(vault / ".watchdog" / "timeline" / "2020-03-15_untag00.ndjson")
    assert recs[0]["event"] == "The hearing was held by Zoom"


def test_stage_skips_facts_without_date_or_text(tmp_path):
    vault = _vault(tmp_path)
    n = stage_timeline_events(vault, _extraction([
        {"fact": "no date", "entities": ["alice"]},
        {"fact": "", "date": "2020"},
        {"date": "2021"},   # missing fact text
    ]))
    assert n == 0
    td = vault / ".watchdog" / "timeline"
    assert not td.exists() or not list(td.glob("*.ndjson"))


def test_stage_returns_zero_without_sha(tmp_path):
    vault = _vault(tmp_path)
    assert stage_timeline_events(vault, {"document": {"key_facts": []}, "entities": []}) == 0


# ── Integration with the collisions / rebuild flow ──────────────────────────

def test_stage_then_collisions_promotes_and_rebuild_renders(tmp_path, capsys):
    vault = _vault(tmp_path)
    stage_timeline_events(vault, _extraction([
        {"fact": "Appointed director", "date": "2020-03-15", "entities": ["alice"]},
    ], sha="doc1xxxxxxx"))

    cmd_timeline_collisions(vault)
    assert capsys.readouterr().out.strip() == "[]"  # no canonical existed → promotion, no collision

    td = vault / ".watchdog" / "timeline"
    assert (td / "2020-03-15.ndjson").exists()  # canonical created from the raw file

    cmd_rebuild_timeline(vault)
    timeline_md = (vault / "timeline.md").read_text(encoding="utf-8")
    assert "## 2020" in timeline_md                # year-grouped heading
    assert "15 Mar 2020" in timeline_md            # human-rendered date
    assert "Appointed director" in timeline_md
    assert "Do not edit by hand" in timeline_md   # auto-generated warning header


def test_collisions_consumes_raw_on_promotion(tmp_path):
    """After a raw-only date is promoted to canonical, the raw file is deleted and a second
    collisions() run reports nothing — otherwise every later ingest re-litigates the date as a
    phantom collision (canonical vs. its own already-promoted raws) and, on a failed dedup call,
    bakes in duplicate rows (#250)."""
    vault = _vault(tmp_path)
    stage_timeline_events(vault, _extraction([
        {"fact": "Appointed director", "date": "2020-03-15", "entities": ["alice"]},
    ], sha="doc1xxxxxxx"))
    td = vault / ".watchdog" / "timeline"
    raw = td / "2020-03-15_doc1xxx.ndjson"
    assert raw.exists()

    first = collisions(vault)
    assert first == []                          # promotion, not a collision
    assert (td / "2020-03-15.ndjson").exists()  # canonical created
    assert not raw.exists()                     # raw consumed

    # A second sweep with nothing new must be a no-op — the canonical stands alone.
    assert collisions(vault) == []
    assert _read_ndjson(td / "2020-03-15.ndjson") == [
        {"date": "2020-03-15", "event": "Appointed director", "source_sha256": "doc1xxxxxxx",
         "page": None, "entity_ids": ["alice"], "basis": "stated"},
    ]


def test_collisions_leaves_raws_when_canonical_exists(tmp_path):
    """When a canonical already exists, collisions() reports the date and leaves the raws in
    place — the caller (post-ingest) deletes them only after a *successful* dedup write, so a
    failed dedup call loses nothing (#250)."""
    vault = _vault(tmp_path)
    td = vault / ".watchdog" / "timeline"
    td.mkdir(parents=True)
    (td / "2020-03-15.ndjson").write_text(
        json.dumps({"date": "2020-03-15", "event": "Existing", "source_sha256": "old",
                    "basis": "stated"}) + "\n", encoding="utf-8")
    stage_timeline_events(vault, _extraction([
        {"fact": "New event", "date": "2020-03-15", "entities": ["bob"]},
    ], sha="newdoc12345"))
    raw = td / "2020-03-15_newdoc1.ndjson"

    cols = collisions(vault)
    assert len(cols) == 1 and cols[0]["date"] == "2020-03-15"
    assert raw.exists()   # left for the caller to consume after dedup succeeds


def test_collisions_routes_same_batch_same_date_raws_through_dedup(tmp_path):
    """#696: two documents of one batch dating events to the same day used to be merged straight
    into a new canonical with no dedup call, so every cross-document restatement survived. Now the
    first raw is promoted and the rest come back as a collision, exactly like a later batch's."""
    vault = _vault(tmp_path)
    stage_timeline_events(vault, _extraction([
        {"fact": "Receiver appointed over Acme", "date": "2021-06-01", "entities": ["acme"]},
    ], sha="aaaa1111111"))
    stage_timeline_events(vault, _extraction([
        {"fact": "Court appoints a receiver for Acme", "date": "2021-06-01", "entities": ["acme"]},
    ], sha="bbbb2222222"))
    td = vault / ".watchdog" / "timeline"

    cols = collisions(vault)
    assert cols == [{"date": "2021-06-01", "canonical": ".watchdog/timeline/2021-06-01.ndjson",
                     "raw": [".watchdog/timeline/2021-06-01_bbbb222.ndjson"]}]
    assert [e["source_sha256"] for e in _read_ndjson(td / "2021-06-01.ndjson")] == ["aaaa1111111"]
    assert not (td / "2021-06-01_aaaa111.ndjson").exists()
    assert (td / "2021-06-01_bbbb222.ndjson").exists()   # left for the caller's dedup


def _seed_registries(vault: Path, docs: dict, manifest: dict) -> None:
    reg = vault / ".watchdog" / "registry"
    reg.mkdir(parents=True, exist_ok=True)
    (reg / "documents.json").write_text(json.dumps(docs), encoding="utf-8")
    (reg / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _seed_canonical(vault: Path, date: str, records: list[dict]) -> None:
    td = vault / ".watchdog" / "timeline"
    td.mkdir(parents=True, exist_ok=True)
    (td / f"{date}.ndjson").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def test_rebuild_attributes_document_and_entities(tmp_path):
    """The unified renderer resolves each record's source_sha256 → document link (+ page) and
    each entity_id → entity link, year-grouped (#237)."""
    vault = _vault(tmp_path)
    _seed_registries(
        vault,
        docs={"sha-a": {"document_note": "documents/doc-a", "title": "Doc A",
                        "morgue_path": "morgue/x/doc-a.pdf"}},
        manifest={"alice": {"name": "Alice Smith", "type": "Person",
                            "note_path": "entities/person/alice-smith"}},
    )
    _seed_canonical(vault, "2020-03-15", [
        {"date": "2020-03-15", "event": "Appointed director", "source_sha256": "sha-a",
         "page": 2, "entity_ids": ["alice"], "basis": "stated"},
    ])

    cmd_rebuild_timeline(vault)
    md = (vault / "timeline.md").read_text(encoding="utf-8")
    assert "## 2020" in md
    assert "[[entities/person/alice-smith|Alice Smith]]" in md
    assert "*[[documents/doc-a|Doc A]], [[morgue/x/doc-a.pdf#page=2|p. 2]]*" in md
    assert "Appointed director" in md


def test_rebuild_renders_multiple_entity_links(tmp_path):
    """A cross-document-deduped event carrying more than one entity tag links all of them."""
    vault = _vault(tmp_path)
    _seed_registries(
        vault,
        docs={"sha-a": {"document_note": "documents/doc-a", "title": "Doc A"}},
        manifest={
            "alice": {"name": "Alice", "type": "Person", "note_path": "entities/person/alice"},
            "acme": {"name": "Acme Corp", "type": "Company", "note_path": "entities/company/acme"},
        },
    )
    _seed_canonical(vault, "2020-03-15", [
        {"date": "2020-03-15", "event": "Acme filed for bankruptcy", "source_sha256": "sha-a",
         "entity_ids": ["alice", "acme"], "basis": "stated"},
    ])

    cmd_rebuild_timeline(vault)
    md = (vault / "timeline.md").read_text(encoding="utf-8")
    assert "[[entities/person/alice|Alice]], [[entities/company/acme|Acme Corp]]" in md


def test_rebuild_falls_back_to_bare_id_when_entity_missing(tmp_path):
    """An entity_id absent from the manifest (e.g. a stale record) renders as bare text rather
    than a broken link — and never crashes the render."""
    vault = _vault(tmp_path)
    _seed_registries(vault, docs={}, manifest={})
    _seed_canonical(vault, "2020-03-15", [
        {"date": "2020-03-15", "event": "Something happened", "source_sha256": "sha-x",
         "entity_ids": ["ghost"], "basis": "stated"},
    ])

    cmd_rebuild_timeline(vault)
    md = (vault / "timeline.md").read_text(encoding="utf-8")
    assert "— ghost —" in md
    assert "Something happened" in md


def test_rebuild_marks_inferred_events(tmp_path):
    vault = _vault(tmp_path)
    _seed_registries(vault, docs={}, manifest={})
    _seed_canonical(vault, "2021", [
        {"date": "2021", "event": "Likely resigned", "source_sha256": "s", "basis": "inferred"},
    ])
    cmd_rebuild_timeline(vault)
    assert "*(inferred)*" in (vault / "timeline.md").read_text(encoding="utf-8")


def test_rebuild_empty_carries_generated_warning(tmp_path):
    vault = _vault(tmp_path)
    cmd_rebuild_timeline(vault)
    timeline_md = (vault / "timeline.md").read_text(encoding="utf-8")
    assert "*No events yet.*" in timeline_md
    assert "Do not edit by hand" in timeline_md


def test_stage_collision_reported_when_canonical_exists(tmp_path, capsys):
    vault = _vault(tmp_path)
    td = vault / ".watchdog" / "timeline"
    td.mkdir(parents=True)
    (td / "2020-03-15.ndjson").write_text(
        json.dumps({"date": "2020-03-15", "event": "Existing", "source_sha256": "old",
                    "basis": "stated"}) + "\n",
        encoding="utf-8",
    )

    stage_timeline_events(vault, _extraction([
        {"fact": "New event", "date": "2020-03-15", "entities": ["bob"]},
    ], sha="newdoc12345"))

    cmd_timeline_collisions(vault)
    collisions = json.loads(capsys.readouterr().out)
    assert len(collisions) == 1
    assert collisions[0]["date"] == "2020-03-15"


# ── cross-precision reconciliation (#239) ───────────────────────────────────

def _ev(date, event, entity_ids=None, page=None):
    r = {"date": date, "event": event, "source_sha256": "s", "entity_ids": entity_ids or [],
         "basis": "stated"}
    if page is not None:
        r["page"] = page
    return r


def test_precision_groups_only_when_month_has_both_precisions(tmp_path):
    vault = _vault(tmp_path)
    _seed_canonical(vault, "2020-03", [_ev("2020-03", "Filed in March")])
    _seed_canonical(vault, "2020-03-15", [_ev("2020-03-15", "Filed on the 15th")])
    _seed_canonical(vault, "2020-06", [_ev("2020-06", "Lone month event")])        # no day sibling
    _seed_canonical(vault, "2020-07-01", [_ev("2020-07-01", "Lone day event")])    # no month sibling

    groups = month_precision_groups(vault)
    assert [g["month"] for g in groups] == ["2020-03"]
    g = groups[0]
    assert [e["event"] for e in g["coarse"]] == ["Filed in March"]
    assert [e["event"] for e in g["precise"]] == ["Filed on the 15th"]


def test_precision_groups_excludes_bare_year(tmp_path):
    """A YYYY event alongside precise dates is deliberately left out — a bare year spans too many
    days to match a specific occurrence safely (scoped to month↔day)."""
    vault = _vault(tmp_path)
    _seed_canonical(vault, "2020", [_ev("2020", "Something in 2020")])
    _seed_canonical(vault, "2020-03-15", [_ev("2020-03-15", "Filed on the 15th")])
    assert month_precision_groups(vault) == []


def test_precision_groups_ignores_raw_files(tmp_path):
    """Only canonical (no-underscore) files feed reconciliation, mirroring the renderer."""
    vault = _vault(tmp_path)
    td = vault / ".watchdog" / "timeline"
    td.mkdir(parents=True, exist_ok=True)
    (td / "2020-03_abc1234.ndjson").write_text(json.dumps(_ev("2020-03", "raw")) + "\n")
    _seed_canonical(vault, "2020-03-15", [_ev("2020-03-15", "Filed on the 15th")])
    assert month_precision_groups(vault) == []   # the month event is only raw, not canonical


def test_apply_matches_folds_coarse_into_day_and_unions_entities(tmp_path):
    vault = _vault(tmp_path)
    _seed_canonical(vault, "2020-03", [_ev("2020-03", "Acme filed", entity_ids=["acme"])])
    _seed_canonical(vault, "2020-03-15", [_ev("2020-03-15", "Acme filed on the 15th", entity_ids=["alice"])])
    group = month_precision_groups(vault)[0]

    folded = apply_precision_matches(vault, group, [{"coarse": 0, "precise": 0}])

    assert folded == 1
    td = vault / ".watchdog" / "timeline"
    assert not (td / "2020-03.ndjson").exists()          # month file emptied → deleted
    day = _read_ndjson(td / "2020-03-15.ndjson")
    assert len(day) == 1
    assert day[0]["date"] == "2020-03-15"                # precise date wins
    assert day[0]["entity_ids"] == ["alice", "acme"]     # attribution unioned


def test_apply_matches_union_does_not_duplicate_shared_entity(tmp_path):
    """When the coarse and precise records already share an entity, the union adds it once."""
    vault = _vault(tmp_path)
    _seed_canonical(vault, "2020-03", [_ev("2020-03", "Acme filed", entity_ids=["acme", "bob"])])
    _seed_canonical(vault, "2020-03-15",
                    [_ev("2020-03-15", "Acme filed on the 15th", entity_ids=["acme"])])
    group = month_precision_groups(vault)[0]

    apply_precision_matches(vault, group, [{"coarse": 0, "precise": 0}])

    day = _read_ndjson(vault / ".watchdog" / "timeline" / "2020-03-15.ndjson")
    assert day[0]["entity_ids"] == ["acme", "bob"]   # acme not duplicated


def test_apply_matches_keeps_unmatched_coarse(tmp_path):
    vault = _vault(tmp_path)
    _seed_canonical(vault, "2020-03", [
        _ev("2020-03", "Acme filed"),           # index 0 — matched
        _ev("2020-03", "Board reshuffled"),     # index 1 — distinct, unmatched
    ])
    _seed_canonical(vault, "2020-03-15", [_ev("2020-03-15", "Acme filed on the 15th")])
    group = month_precision_groups(vault)[0]

    apply_precision_matches(vault, group, [{"coarse": 0, "precise": 0}])

    survivors = _read_ndjson(vault / ".watchdog" / "timeline" / "2020-03.ndjson")
    assert [r["event"] for r in survivors] == ["Board reshuffled"]


def test_apply_matches_ignores_bad_and_repeat_indices(tmp_path):
    vault = _vault(tmp_path)
    _seed_canonical(vault, "2020-03", [_ev("2020-03", "Acme filed", entity_ids=["acme"])])
    _seed_canonical(vault, "2020-03-15", [_ev("2020-03-15", "Filed on the 15th")])
    group = month_precision_groups(vault)[0]

    folded = apply_precision_matches(vault, group, [
        {"coarse": 9, "precise": 0},        # out-of-range coarse
        {"coarse": 0, "precise": 5},        # out-of-range precise
        {"coarse": 0, "precise": 0},        # valid
        {"coarse": 0, "precise": 0},        # repeat of an already-folded coarse
        "not a dict",
    ])
    assert folded == 1
    day = _read_ndjson(vault / ".watchdog" / "timeline" / "2020-03-15.ndjson")
    assert day[0]["entity_ids"] == ["acme"]   # unioned exactly once


def test_apply_matches_no_matches_is_noop(tmp_path):
    vault = _vault(tmp_path)
    _seed_canonical(vault, "2020-03", [_ev("2020-03", "Acme filed")])
    _seed_canonical(vault, "2020-03-15", [_ev("2020-03-15", "Filed on the 15th")])
    group = month_precision_groups(vault)[0]

    assert apply_precision_matches(vault, group, []) == 0
    assert (vault / ".watchdog" / "timeline" / "2020-03.ndjson").exists()   # untouched


# ── post-flight wiring ──────────────────────────────────────────────────────

def _full_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    reg = vault / ".watchdog" / "registry"
    reg.mkdir(parents=True)
    (vault / ".watchdog" / "tmp").mkdir()
    (vault / "incoming").mkdir()
    (vault / "documents").mkdir()
    (reg / "entities.json").write_text("{}\n")
    (reg / "documents.json").write_text("{}\n")
    (reg / "registry.json").write_text(json.dumps({"document_count": 0, "entity_count": 0}) + "\n")
    (reg / "processing.log").write_text("")
    return vault


def test_postflight_stages_timeline_files(tmp_path):
    from watchdog.pipeline.postflight import run as postflight_run

    vault = _full_vault(tmp_path)
    extraction = {
        "document": {
            "sha256": "post123abc",
            "filename": "doc.pdf",
            "original_path": "incoming/doc.pdf",
            "title": "Doc",
            "document_type": "Report",
            "date_of_document": "2024-01-15",
            "page_count": 1,
            "summary": "x",
            "key_facts": [
                {"fact": "Appointed", "date": "2020-03-15", "entities": ["alice"]},
            ],
        },
        "entities": [
            {"id": "alice", "name": "Alice", "type": "Person", "aliases": [], "roles": []},
        ],
        "morgue_entity_id": "alice",
        "morgue_document_type": "report",
    }
    ext_path = vault / ".watchdog" / "tmp" / "wdg_ex_post123abc.json"
    ext_path.write_text(json.dumps(extraction), encoding="utf-8")

    result = postflight_run(vault, ext_path)
    assert result.get("ok"), result

    raw = vault / ".watchdog" / "timeline" / "2020-03-15_post123.ndjson"
    assert raw.exists()
    assert _read_ndjson(raw)[0]["event"] == "Appointed"


def _register(vault: Path, *shas: str) -> None:
    reg = vault / ".watchdog" / "registry"
    reg.mkdir(parents=True, exist_ok=True)
    (reg / "documents.json").write_text(json.dumps(
        {s: {"filename": f"{s[:4]}.pdf", "document_note": f"documents/{s[:4]}"} for s in shas}))


def test_a_failed_dedup_does_not_hide_the_other_documents_events(tmp_path):
    """Two committed documents share a date; one is promoted, the other's raw stays because the
    dedup call failed. timeline.md still shows both (D260)."""
    vault = _vault(tmp_path)
    a, b = "a" * 64, "b" * 64
    stage_timeline_events(vault, _extraction([{"fact": "Contract signed", "date": "2021-03-04"}], sha=a))
    stage_timeline_events(vault, _extraction([{"fact": "Board approved the loan", "date": "2021-03-04"}], sha=b))
    _register(vault, a, b)
    collisions(vault)                       # promotes one; the other raw is left for dedup
    # …and the dedup call fails, so nothing else happens before the rebuild.
    cmd_rebuild_timeline(vault, quiet=True)
    md = (vault / "timeline.md").read_text()
    assert "Contract signed" in md and "Board approved the loan" in md


def test_uncommitted_documents_stay_out_of_timeline_md(tmp_path):
    vault = _vault(tmp_path)
    a, staged = "a" * 64, "c" * 64
    stage_timeline_events(vault, _extraction([{"fact": "Contract signed", "date": "2021-03-04"}], sha=a))
    stage_timeline_events(vault, _extraction([{"fact": "Not yet committed", "date": "2021-03-04"}], sha=staged))
    _register(vault, a)
    collisions(vault)
    cmd_rebuild_timeline(vault, quiet=True)
    md = (vault / "timeline.md").read_text()
    assert "Not yet committed" not in md


def test_a_promoted_event_is_not_repeated_from_its_raw(tmp_path):
    vault = _vault(tmp_path)
    a = "a" * 64
    stage_timeline_events(vault, _extraction([{"fact": "Contract signed", "date": "2021-03-04"}], sha=a))
    _register(vault, a)
    td = vault / ".watchdog" / "timeline"
    raw = next(td.glob("*_*.ndjson"))
    (td / "2021-03-04.ndjson").write_text(raw.read_text())     # canonical and raw both present
    cmd_rebuild_timeline(vault, quiet=True)
    assert (vault / "timeline.md").read_text().count("Contract signed") == 1


def test_a_staged_re_extraction_of_a_committed_document_stays_out_of_timeline_md(tmp_path):
    """`--force` re-extracts a committed document and stages new raws under the same sha; until
    `bark` commits them (its queue file is still there), they must not render (D260)."""
    vault = _vault(tmp_path)
    a = "a" * 64
    _register(vault, a)
    td = vault / ".watchdog" / "timeline"
    td.mkdir(parents=True, exist_ok=True)
    (td / "2021-03-04.ndjson").write_text(json.dumps(
        {"date": "2021-03-04", "event": "Contract signed", "source_sha256": a}) + "\n")
    stage_timeline_events(vault, _extraction([{"fact": "Contract was signed", "date": "2021-03-04"}], sha=a))
    queue = vault / ".watchdog" / "queue"
    queue.mkdir(parents=True, exist_ok=True)
    (queue / f"{a}.json").write_text("{}")
    tmp = vault / ".watchdog" / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / f"result_{a}.json").write_text("{}")
    cmd_rebuild_timeline(vault, quiet=True)
    md = (vault / "timeline.md").read_text()
    assert "Contract signed" in md and "Contract was signed" not in md


def test_a_queued_force_that_has_not_run_does_not_hide_a_documents_events(tmp_path):
    """`ingest --force` writes the queue file before extracting; until the re-extraction runs,
    the document's pending raws must still render."""
    vault = _vault(tmp_path)
    a, b = "a" * 64, "b" * 64
    stage_timeline_events(vault, _extraction([{"fact": "Contract signed", "date": "2021-03-04"}], sha=a))
    stage_timeline_events(vault, _extraction([{"fact": "Board approved the loan", "date": "2021-03-04"}], sha=b))
    _register(vault, a, b)
    collisions(vault)
    queue = vault / ".watchdog" / "queue"
    queue.mkdir(parents=True, exist_ok=True)
    tmp = vault / ".watchdog" / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    for s in (a, b):                                    # left over from a run whose briefing failed
        (tmp / f"result_{s}.json").write_text("{}")
        os.utime(tmp / f"result_{s}.json", (1_000_000, 1_000_000))
    for s in (a, b):
        (queue / f"{s}.json").write_text("{}")
    cmd_rebuild_timeline(vault, quiet=True)
    md = (vault / "timeline.md").read_text()
    assert "Contract signed" in md and "Board approved the loan" in md
