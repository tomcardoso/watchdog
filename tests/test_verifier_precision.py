"""Unit tests for `benchmarks/verifier_precision.py` (#535, step 3).

Same posture as `tests/test_benchmark_runner.py`: dev-only tooling outside `src/`, added to
`sys.path` here and exercised for its pure pieces — packet assembly from a vault's staged
extractions, and the tally that turns a judge's grades into a precision number. The judging
itself is a model's job and is not unit-testable.
"""
import json
import sys
from pathlib import Path

import pytest

BENCHMARKS_DIR = Path(__file__).resolve().parent.parent / "benchmarks"
if str(BENCHMARKS_DIR) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS_DIR))

import verifier_precision as vp  # noqa: E402


def _vault(tmp_path, extractions, *, pages=True):
    vault = tmp_path / "bench-vault"
    (vault / ".watchdog" / "extracted").mkdir(parents=True)
    (vault / ".watchdog" / "queue").mkdir(parents=True)
    for sha, extraction in extractions.items():
        (vault / ".watchdog" / "extracted" / f"{sha}.json").write_text(
            json.dumps(extraction), encoding="utf-8")
        if pages:
            (vault / ".watchdog" / "queue" / f"{sha}.json").write_text(
                json.dumps({"pages": [{"page": 1, "markdown": "The page text."}]}),
                encoding="utf-8")
    return vault


def _extraction(filename, facts):
    return {"document": {"sha256": "sha-" + filename, "filename": filename, "key_facts": facts},
            "entities": []}


def test_build_writes_a_packet_only_for_documents_with_added_facts(tmp_path, capsys):
    vault = _vault(tmp_path, {
        "sha-a.pdf": _extraction("a.pdf", [
            {"fact": "Extracted."},
            {"fact": "Added.", "page": 1, "added_by": "verify"},
        ]),
        "sha-b.pdf": _extraction("b.pdf", [{"fact": "Extracted only."}]),
    })
    out = tmp_path / "judge"

    vp.build(str(vault), str(out))

    assert (out / "packet-a.json").exists()
    assert not (out / "packet-b.json").exists()
    packet = json.loads((out / "packet-a.json").read_text())
    assert [f["id"] for f in packet["added_facts"]] == ["a:v1"]
    # The restatement reference is the extractor's own facts, never the pass's — a candidate
    # judged against another added fact would let the pass grade itself.
    assert [f["fact"] for f in packet["existing_facts"]] == ["Extracted."]
    assert packet["pages"] == [{"page": 1, "text": "The page text."}]


def test_build_warns_when_a_packet_has_no_grounding_text(tmp_path, capsys):
    vault = _vault(tmp_path, {
        "sha-a.pdf": _extraction("a.pdf", [{"fact": "Added.", "added_by": "verify"}])},
        pages=False)

    vp.build(str(vault), str(tmp_path / "judge"))

    assert "WARNING: no queue page text for a" in capsys.readouterr().out


def test_build_says_so_when_nothing_was_added(tmp_path, capsys):
    vault = _vault(tmp_path, {"sha-a.pdf": _extraction("a.pdf", [{"fact": "Extracted."}])})

    vp.build(str(vault), str(tmp_path / "judge"))

    assert "was it extracted with --verify?" in capsys.readouterr().out


def _judged(tmp_path, grades):
    """A packet with one item per grade given, plus its judgment file."""
    d = tmp_path / "judge"
    d.mkdir()
    (d / "packet-a.json").write_text(json.dumps({
        "document": "a",
        "added_facts": [{"id": f"a:v{i}", "fact": f"Fact {i}"} for i in range(len(grades))],
    }), encoding="utf-8")
    (d / "judgment-a.json").write_text(json.dumps({
        "judgments": {f"a:v{i}": {"grade": g, "note": ""} for i, g in enumerate(grades)},
    }), encoding="utf-8")
    return d


def test_aggregate_reports_precision_as_material_over_added(tmp_path, capsys):
    d = _judged(tmp_path, ["grounded_material", "grounded_material",
                           "grounded_trivial", "unsupported"])

    vp.aggregate(str(d))

    summary = json.loads((d / "summary.json").read_text())["summary"]
    assert summary["graded"] == 4
    assert summary["precision"] == 0.5
    assert summary["unsupported_rate"] == 0.25
    assert "precision (material / added)  2/4  (50%)" in capsys.readouterr().out


def test_aggregate_flags_ungraded_and_unknown_items_instead_of_counting_them(tmp_path, capsys):
    """A half-finished or drifted judgment must not quietly produce a precision number off a
    smaller denominator than the packet asked for."""
    d = _judged(tmp_path, ["grounded_material", "grounded_material", "unsupported"])
    judgments = json.loads((d / "judgment-a.json").read_text())
    del judgments["judgments"]["a:v1"]                       # never graded
    judgments["judgments"]["a:v2"]["grade"] = "excellent"    # not one of the three grades
    judgments["judgments"]["a:v9"] = {"grade": "grounded_material"}   # not in the packet
    (d / "judgment-a.json").write_text(json.dumps(judgments), encoding="utf-8")

    vp.aggregate(str(d))

    out = capsys.readouterr().out
    assert "a:v1 has no grade" in out
    assert "a:v2 has unknown grade 'excellent'" in out
    assert "graded a:v9, which is not in the packet" in out
    assert json.loads((d / "summary.json").read_text())["summary"]["graded"] == 1


def test_aggregate_exits_when_nothing_was_graded_at_all(tmp_path):
    d = _judged(tmp_path, ["grounded_material"])
    (d / "judgment-a.json").write_text(json.dumps({"judgments": {}}), encoding="utf-8")

    with pytest.raises(SystemExit, match="No graded facts found"):
        vp.aggregate(str(d))


def test_aggregate_reports_a_missing_judgment_file_rather_than_scoring_around_it(tmp_path, capsys):
    d = _judged(tmp_path, ["grounded_material"])
    (d / "packet-b.json").write_text(json.dumps({
        "document": "b", "added_facts": [{"id": "b:v0", "fact": "Fact"}]}), encoding="utf-8")

    vp.aggregate(str(d))

    out = capsys.readouterr().out
    assert "b: no judgment file — 1 fact(s) ungraded" in out
    assert json.loads((d / "summary.json").read_text())["summary"]["graded"] == 1


def test_added_facts_finds_only_the_verifiers_own(tmp_path):
    extraction = _extraction("a.pdf", [
        {"fact": "Extracted."},
        {"fact": "Added.", "added_by": "verify"},
        "not even an object",
    ])
    assert vp.added_facts(extraction) == [(1, {"fact": "Added.", "added_by": "verify"})]
