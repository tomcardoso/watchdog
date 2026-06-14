import json
from pathlib import Path

from watchdog.pipeline import abort


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    for sub in ("tmp", "timeline", "queue", "Registry"):
        (vault / ".watchdog" / sub).mkdir(parents=True)
    return vault


def test_abort_removes_staging_and_moves_queue_file(tmp_path):
    vault = _vault(tmp_path)
    sha = "abc1234def5678"
    tmp = vault / ".watchdog" / "tmp"
    tl = vault / ".watchdog" / "timeline"

    # staging artifacts for this sha
    (tmp / f"wdg_ex_{sha}.json").write_text("{}")
    (tmp / f"notes_{sha}.md").write_text("notes")
    (tmp / f"preflight_{sha}_pages.md").write_text("pages")
    (tmp / f"section_{sha}_01.md").write_text("sec")
    (tmp / f"section_ex_{sha}_01.json").write_text("{}")
    (tl / f"2020-01-01_{sha[:7]}.ndjson").write_text("{}")          # raw, this sha
    (tl / "2020-01-01.ndjson").write_text("{}")                     # canonical — keep
    (tmp / "wdg_ex_otherSHA.json").write_text("{}")                 # other doc — keep
    (vault / ".watchdog" / "queue" / f"{sha}.json").write_text("{}")

    result = abort.run(vault, sha)

    assert result["ok"] is True
    # this sha's staging gone
    assert not (tmp / f"wdg_ex_{sha}.json").exists()
    assert not (tmp / f"notes_{sha}.md").exists()
    assert not (tmp / f"section_{sha}_01.md").exists()
    assert not (tmp / f"section_ex_{sha}_01.json").exists()
    assert not (tl / f"2020-01-01_{sha[:7]}.ndjson").exists()
    # canonical timeline + other doc untouched
    assert (tl / "2020-01-01.ndjson").exists()
    assert (tmp / "wdg_ex_otherSHA.json").exists()
    # queue file moved to holding area, not deleted
    assert not (vault / ".watchdog" / "queue" / f"{sha}.json").exists()
    assert (vault / ".watchdog" / "queue" / "_failed" / f"{sha}.json").exists()
    assert result["requeue_path"].endswith(f"_failed/{sha}.json")


def test_abort_never_touches_vault_registry(tmp_path):
    vault = _vault(tmp_path)
    entities = vault / ".watchdog" / "Registry" / "entities.json"
    entities.write_text(json.dumps({"acme": {"id": "acme", "name": "Acme"}}))

    abort.run(vault, "somesha")

    assert json.loads(entities.read_text()) == {"acme": {"id": "acme", "name": "Acme"}}


def test_abort_is_noop_when_nothing_staged(tmp_path):
    vault = _vault(tmp_path)
    result = abort.run(vault, "missing")
    assert result["ok"] is True
    assert result["removed"] == []
    assert result["requeue_path"] is None
