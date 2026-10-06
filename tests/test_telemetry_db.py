"""Unit tests for the global telemetry store (#611) — `telemetry_db.record_call` in isolation,
independent of `orchestrate._record_usage`'s own call-site/error-handling tests in
`test_orchestrate.py`."""

import json
import sqlite3

import pytest

from watchdog import telemetry_db

_MINIMAL_RECORD = {
    "task": "extract", "model": "claude-sonnet-4-6", "backend": "claude-api",
    "input_tokens": 100, "output_tokens": 20, "cache_read_tokens": 0, "cache_write_tokens": 0,
    "cost_usd": 0.01, "latency_s": 1.5, "attempts": 1, "end_ts": 1700000000.0,
}


def test_record_call_creates_db_and_inserts_row(tmp_path, monkeypatch):
    db_path = tmp_path / "telemetry.db"
    monkeypatch.setattr(telemetry_db, "DB_PATH", db_path)
    vault = tmp_path / "vault"
    vault.mkdir()

    telemetry_db.record_call(
        _MINIMAL_RECORD, vault=vault, run_id="20260101T000000Z", benchmark_arm_id=None,
        prompt_hash="abc123", config_snapshot={"extract_model": "sonnet"},
    )

    assert db_path.exists()
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT * FROM calls").fetchall()
        cols = [d[0] for d in conn.execute("SELECT * FROM calls").description]
    finally:
        conn.close()
    assert len(rows) == 1
    row = dict(zip(cols, rows[0]))
    assert row["run_id"] == "20260101T000000Z"
    assert row["vault_path"] == str(vault.resolve())
    assert row["vault_name"] == "vault"
    assert row["benchmark_arm_id"] is None
    assert row["task"] == "extract"
    assert row["model"] == "claude-sonnet-4-6"
    assert row["input_tokens"] == 100
    assert row["output_tokens"] == 20
    assert row["cost_usd"] == 0.01
    assert row["prompt_hash"] == "abc123"
    assert row["codebase_version"]   # non-empty; exact value depends on the install
    assert json.loads(row["config_json"]) == {"extract_model": "sonnet"}
    assert row["pruned_json"] is None
    assert row["rate_limit_json"] is None


def test_record_call_stores_benchmark_arm_id(tmp_path, monkeypatch):
    monkeypatch.setattr(telemetry_db, "DB_PATH", tmp_path / "telemetry.db")
    vault = tmp_path / "vault"
    vault.mkdir()

    telemetry_db.record_call(
        _MINIMAL_RECORD, vault=vault, run_id="r1", benchmark_arm_id="extractor-sweep-3",
        prompt_hash=None, config_snapshot=None,
    )

    conn = sqlite3.connect(telemetry_db.DB_PATH)
    try:
        row = conn.execute("SELECT benchmark_arm_id, prompt_hash, config_json FROM calls").fetchone()
    finally:
        conn.close()
    assert row == ("extractor-sweep-3", None, None)


def test_record_call_serializes_pruned_and_rate_limit_as_json(tmp_path, monkeypatch):
    monkeypatch.setattr(telemetry_db, "DB_PATH", tmp_path / "telemetry.db")
    vault = tmp_path / "vault"
    vault.mkdir()
    record = dict(_MINIMAL_RECORD, pruned=["extra_field"],
                 rate_limit={"limit_tokens": 150000, "remaining_tokens": 149800})

    telemetry_db.record_call(record, vault=vault, run_id="r1", benchmark_arm_id=None,
                             prompt_hash=None, config_snapshot=None)

    conn = sqlite3.connect(telemetry_db.DB_PATH)
    try:
        row = conn.execute("SELECT pruned_json, rate_limit_json FROM calls").fetchone()
    finally:
        conn.close()
    assert json.loads(row[0]) == ["extra_field"]
    assert json.loads(row[1]) == {"limit_tokens": 150000, "remaining_tokens": 149800}


def test_record_call_twice_appends_two_rows_without_error(tmp_path, monkeypatch):
    """The schema is created with `CREATE TABLE IF NOT EXISTS` on every connect — a second call
    against an already-initialized db must not error."""
    monkeypatch.setattr(telemetry_db, "DB_PATH", tmp_path / "telemetry.db")
    vault = tmp_path / "vault"
    vault.mkdir()

    for _ in range(2):
        telemetry_db.record_call(_MINIMAL_RECORD, vault=vault, run_id="r1", benchmark_arm_id=None,
                                 prompt_hash=None, config_snapshot=None)

    conn = sqlite3.connect(telemetry_db.DB_PATH)
    try:
        (count,) = conn.execute("SELECT COUNT(*) FROM calls").fetchone()
    finally:
        conn.close()
    assert count == 2


def test_record_call_creates_parent_directory(tmp_path, monkeypatch):
    db_path = tmp_path / "nested" / "does-not-exist-yet" / "telemetry.db"
    monkeypatch.setattr(telemetry_db, "DB_PATH", db_path)
    vault = tmp_path / "vault"
    vault.mkdir()

    telemetry_db.record_call(_MINIMAL_RECORD, vault=vault, run_id="r1", benchmark_arm_id=None,
                             prompt_hash=None, config_snapshot=None)

    assert db_path.exists()


def test_record_call_raises_on_connection_failure(tmp_path, monkeypatch):
    """`record_call` itself does not swallow errors — `orchestrate._record_usage` (the only real
    caller) is responsible for catching and logging this, so this module stays trivially
    testable for the failure path itself (see `telemetry_db.record_call`'s own docstring)."""
    monkeypatch.setattr(telemetry_db, "DB_PATH", tmp_path / "telemetry.db")
    vault = tmp_path / "vault"
    vault.mkdir()

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(sqlite3, "connect", boom)

    with pytest.raises(sqlite3.OperationalError):
        telemetry_db.record_call(_MINIMAL_RECORD, vault=vault, run_id="r1", benchmark_arm_id=None,
                                 prompt_hash=None, config_snapshot=None)


# ── Opt-out, purge, connection reuse ─────────────────────────────────────────

from watchdog.telemetry_db import enabled as _real_enabled   # noqa: E402 — before conftest's patch


@pytest.mark.parametrize("config, expected", [
    (None, True),                       # no config file yet
    ({}, True),                         # key unset
    ({"telemetry": True}, True),
    ({"telemetry": False}, False),
])
def test_enabled_reads_the_telemetry_key(tmp_path, monkeypatch, config, expected):
    from watchdog import config as base
    cfg = tmp_path / "config.json"
    if config is not None:
        cfg.write_text(json.dumps(config))
    monkeypatch.setattr(base, "CONFIG_FILE", cfg)
    assert _real_enabled() is expected


def test_enabled_treats_unreadable_config_as_on(tmp_path, monkeypatch):
    from watchdog import config as base
    cfg = tmp_path / "config.json"
    cfg.write_text("{not json")
    monkeypatch.setattr(base, "CONFIG_FILE", cfg)
    assert _real_enabled() is True


def test_purge_vault_deletes_only_that_vaults_rows(tmp_path):
    keep, drop = tmp_path / "keep", tmp_path / "drop"
    keep.mkdir()
    drop.mkdir()
    for v in (keep, drop, drop):
        telemetry_db.record_call(_MINIMAL_RECORD, vault=v, run_id="r", benchmark_arm_id=None,
                                 prompt_hash=None, config_snapshot=None)
    assert telemetry_db.purge_vault(drop) == 2
    conn = sqlite3.connect(telemetry_db.DB_PATH)
    try:
        assert conn.execute("SELECT vault_name FROM calls").fetchall() == [("keep",)]
    finally:
        conn.close()


def test_purge_vault_never_creates_the_store(tmp_path):
    assert telemetry_db.purge_vault(tmp_path) == 0
    assert not telemetry_db.DB_PATH.exists()


def test_connection_is_reused_across_calls_and_closed_on_close(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    telemetry_db.record_call(_MINIMAL_RECORD, vault=vault, run_id="r", benchmark_arm_id=None,
                             prompt_hash=None, config_snapshot=None)
    first = telemetry_db._conn
    telemetry_db.record_call(_MINIMAL_RECORD, vault=vault, run_id="r", benchmark_arm_id=None,
                             prompt_hash=None, config_snapshot=None)
    assert first is not None and telemetry_db._conn is first
    telemetry_db.close()
    assert telemetry_db._conn is None


def test_relocate_vault_moves_only_that_vaults_rows(tmp_path):
    old, other, new = tmp_path / "old", tmp_path / "other", tmp_path / "new"
    old.mkdir()
    other.mkdir()
    for v in (old, old, other):
        telemetry_db.record_call(_MINIMAL_RECORD, vault=v, run_id="r", benchmark_arm_id=None,
                                 prompt_hash=None, config_snapshot=None)
    old_resolved = old.resolve()
    old.rename(new)
    assert telemetry_db.relocate_vault(old_resolved, new) == 2
    assert telemetry_db.purge_vault(new) == 2
    conn = sqlite3.connect(telemetry_db.DB_PATH)
    try:
        assert conn.execute("SELECT vault_name FROM calls").fetchall() == [("other",)]
    finally:
        conn.close()


def test_relocate_vault_never_creates_the_store(tmp_path):
    assert telemetry_db.relocate_vault(tmp_path / "a", tmp_path / "b") == 0
    assert not telemetry_db.DB_PATH.exists()


def test_connection_overwrites_deleted_content(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    telemetry_db.record_call(_MINIMAL_RECORD, vault=vault, run_id="r", benchmark_arm_id=None,
                             prompt_hash=None, config_snapshot=None)
    assert telemetry_db._connect().execute("PRAGMA secure_delete").fetchone()[0] == 1


def test_purged_rows_leave_no_trace_in_the_database_or_its_wal(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    record = {**_MINIMAL_RECORD, "filename": "zq-unmistakable-filename.pdf"}
    telemetry_db.record_call(record, vault=vault, run_id="r", benchmark_arm_id=None,
                             prompt_hash=None, config_snapshot=None)
    assert telemetry_db.purge_vault(vault) == 1
    for p in (telemetry_db.DB_PATH, telemetry_db.DB_PATH.with_name(telemetry_db.DB_PATH.name + "-wal")):
        if p.exists():
            assert b"zq-unmistakable-filename" not in p.read_bytes(), p.name
