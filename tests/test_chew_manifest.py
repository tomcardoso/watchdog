"""The frozen-chew check for benchmark runs (D372, `benchmarks/chew_manifest.py`)."""

import json
import sys
import types
from pathlib import Path

BENCHMARKS_DIR = Path(__file__).resolve().parent.parent / "benchmarks"
if str(BENCHMARKS_DIR) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS_DIR))

import bench_report as br  # noqa: E402
import chew_manifest as cm  # noqa: E402


def _vault(root: Path, docs: dict[str, tuple[list[str], str]]) -> Path:
    """A chewed vault: sha -> (page texts, engine)."""
    queue = root / ".watchdog" / "queue"
    queue.mkdir(parents=True)
    for sha, (pages, engine) in docs.items():
        (queue / f"{sha}.json").write_text(json.dumps({
            "filename": f"{sha}.pdf", "sha256": sha,
            "pages": [{"page": i + 1, "markdown": t} for i, t in enumerate(pages)],
            "metadata": {"ocr_engine": engine, "platform": "darwin" if engine == "ocrmac" else "linux"},
        }))
    return root


def _manifest(tmp_path: Path, docs) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(cm.fingerprint(cm.read_chew(_vault(tmp_path / "frozen", docs)))))
    return path


def test_a_chew_identical_to_the_frozen_one_raises_nothing(tmp_path):
    docs = {"aaa": (["one", "two"], "ocrmac")}
    manifest = _manifest(tmp_path, docs)
    result = cm.check(_vault(tmp_path / "run", docs), manifest)
    assert result == {"manifest": True, "differs": []}
    assert cm.warning_lines(result) == []


def test_a_page_read_differently_is_named_with_both_engines(tmp_path):
    manifest = _manifest(tmp_path, {"aaa": (["one", "two", "three"], "ocrmac"), "bbb": (["x"], "none")})
    run = _vault(tmp_path / "run", {"aaa": (["one", "tw0", "three"], "rapidocr"), "bbb": (["x"], "none")})
    result = cm.check(run, manifest)
    [diff] = result["differs"]
    assert diff["sha256"] == "aaa" and diff["pages"] == [2]
    assert (diff["ocr_engine"], diff["frozen_ocr_engine"]) == ("rapidocr", "ocrmac")
    lines = cm.warning_lines(result)
    assert lines[0].startswith("WARNING")
    assert "aaa.pdf: page 2 differ" in lines[-1] and "rapidocr on linux" in lines[-1] and "ocrmac on darwin" in lines[-1]


def test_a_document_missing_from_the_chew_is_listed(tmp_path):
    manifest = _manifest(tmp_path, {"aaa": (["one"], "ocrmac"), "bbb": (["two"], "ocrmac")})
    result = cm.check(_vault(tmp_path / "run", {"aaa": (["one"], "ocrmac")}), manifest)
    assert [d["sha256"] for d in result["differs"]] == ["bbb"] and result["differs"][0]["missing"]


def test_no_manifest_is_said_rather_than_taken_for_a_match(tmp_path):
    result = cm.check(_vault(tmp_path / "run", {"aaa": (["one"], "ocrmac")}), tmp_path / "absent.json")
    assert result == {"manifest": False, "differs": []}
    assert cm.warning_lines(result)[0].startswith("Note: no frozen-chew manifest")


def test_the_cli_writes_a_manifest_and_checks_against_it(tmp_path, capsys):
    vault = _vault(tmp_path / "frozen", {"aaa": (["one"], "ocrmac")})
    out = tmp_path / "m.json"
    assert cm.main(["write", str(vault), "--out", str(out)]) == 0
    assert json.loads(out.read_text())["documents"]["aaa"]["ocr_engine"] == "ocrmac"
    assert cm.main(["check", str(vault), "--manifest", str(out)]) == 0
    other = _vault(tmp_path / "other", {"aaa": (["0ne"], "rapidocr")})
    assert cm.main(["check", str(other), "--manifest", str(out)]) == 1
    assert "WARNING" in capsys.readouterr().out


def test_the_report_records_each_documents_engine_and_flags_a_different_chew(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "MANIFEST", _manifest(tmp_path, {"aaa": (["one"], "ocrmac")}))
    run = _vault(tmp_path / "run", {"aaa": (["0ne"], "rapidocr")})
    arm = types.SimpleNamespace(stage="extractor", vault=str(run))
    check = br.chew_check([arm])
    assert check["documents"][0]["ocr_engine"] == "rapidocr" and len(check["documents"][0]["text_sha256"]) == 64
    assert [d["sha256"] for d in check["differs"]] == ["aaa"]
    assert br.chew_md(check)[0] == "## Chew"
    assert br.chew_check([types.SimpleNamespace(stage="classifier", vault=str(run))]) is None
