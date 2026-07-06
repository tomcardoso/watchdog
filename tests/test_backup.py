"""Tests for `watchdog.pipeline.backup` (#270): pre-mutation snapshots for
merge-entities, ingest's discard choice, and delete --purge."""

from pathlib import Path

from watchdog.pipeline.backup import snapshot


def make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / ".watchdog" / "Registry").mkdir(parents=True)
    return vault


def test_snapshot_returns_none_when_nothing_exists(tmp_path):
    vault = make_vault(tmp_path)
    result = snapshot(vault, "merge-entities", [vault / "nope.json"])
    assert result is None
    assert not (vault / ".watchdog" / "backups").exists()


def test_snapshot_copies_existing_file_preserving_relative_path(tmp_path):
    vault = make_vault(tmp_path)
    target = vault / ".watchdog" / "Registry" / "entities.json"
    target.write_text('{"a": 1}')

    backup_dir = snapshot(vault, "merge-entities", [target])

    assert backup_dir is not None
    copied = backup_dir / ".watchdog" / "Registry" / "entities.json"
    assert copied.read_text() == '{"a": 1}'


def test_snapshot_skips_missing_paths_but_copies_existing_ones(tmp_path):
    vault = make_vault(tmp_path)
    present = vault / ".watchdog" / "Registry" / "entities.json"
    present.write_text("{}")
    missing = vault / ".watchdog" / "Registry" / "documents.json"

    backup_dir = snapshot(vault, "merge-entities", [present, missing])

    assert (backup_dir / ".watchdog" / "Registry" / "entities.json").exists()
    assert not (backup_dir / ".watchdog" / "Registry" / "documents.json").exists()


def test_snapshot_copies_directories(tmp_path):
    vault = make_vault(tmp_path)
    frag = vault / ".watchdog" / "tmp" / "entity-fragments"
    frag.mkdir(parents=True)
    (frag / "_queue.json").write_text("{}")

    backup_dir = snapshot(vault, "ingest-discard", [frag])

    assert (backup_dir / ".watchdog" / "tmp" / "entity-fragments" / "_queue.json").exists()


def test_snapshot_names_directory_with_operation(tmp_path):
    vault = make_vault(tmp_path)
    target = vault / ".watchdog" / "Registry" / "entities.json"
    target.write_text("{}")

    backup_dir = snapshot(vault, "delete-purge", [target])

    assert backup_dir.name.endswith("-delete-purge")
    assert backup_dir.parent == vault / ".watchdog" / "backups"


def test_snapshot_prunes_to_five_most_recent(tmp_path):
    vault = make_vault(tmp_path)
    target = vault / ".watchdog" / "Registry" / "entities.json"
    target.write_text("{}")
    backups_root = vault / ".watchdog" / "backups"

    # Pre-seed six older backup dirs with distinct sortable names, oldest first.
    for i in range(6):
        (backups_root / f"2020010{i}T000000Z-merge-entities").mkdir(parents=True)

    snapshot(vault, "merge-entities", [target])

    remaining = sorted(d.name for d in backups_root.iterdir())
    assert len(remaining) == 5
    assert "20200100T000000Z-merge-entities" not in remaining   # oldest pruned first
