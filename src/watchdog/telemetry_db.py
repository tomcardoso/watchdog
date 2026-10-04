"""Global, cross-vault store of per-call model usage (D193, D247).

The same records `orchestrate._record_usage` writes to each vault's usage files, in one SQLite
database, so a question across vaults or models is one query. The per-vault JSON files stay
authoritative for `watchdog usage`. A row adds the vault path and name, any benchmark arm, a hash
of the prompt sent, the codebase version and the run's config snapshot. Raw responses are not
stored. A write failure never breaks a run: the caller logs it.

On by default; `watchdog configure telemetry false` stops new rows, and `watchdog delete --purge`
removes a vault's rows. Rows hold vault paths and document filenames, so the file is as sensitive
as the vaults it describes."""

import json
import sqlite3
import threading
from pathlib import Path
from watchdog import config as user_config

WATCHDOG_HOME = Path.home() / ".watchdog"
DB_PATH = WATCHDOG_HOME / "telemetry.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    vault_path TEXT NOT NULL,
    vault_name TEXT NOT NULL,
    benchmark_arm_id TEXT,
    task TEXT NOT NULL,
    model TEXT NOT NULL,
    backend TEXT NOT NULL,
    effort TEXT,
    auth_mode TEXT,
    filename TEXT,
    detail TEXT,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cache_read_tokens INTEGER NOT NULL,
    cache_write_tokens INTEGER NOT NULL,
    cost_usd REAL,
    latency_s REAL,
    attempts INTEGER,
    failed INTEGER,
    end_ts REAL,
    reasoning_tokens INTEGER,
    api_ms REAL,
    num_turns INTEGER,
    stop_reason TEXT,
    est_input_tokens INTEGER,
    prompt_hash TEXT,
    codebase_version TEXT NOT NULL,
    config_json TEXT,
    pruned_json TEXT,
    rate_limit_json TEXT,
    batch_id TEXT,
    batch_submitted_at TEXT,
    batch_ended_at TEXT,
    batch_collected_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_calls_run ON calls(run_id);
CREATE INDEX IF NOT EXISTS idx_calls_task_model ON calls(task, model, effort);
CREATE INDEX IF NOT EXISTS idx_calls_vault ON calls(vault_path);
"""


# One connection per process, reused across calls and closed at the end of a run (`close`). It is
# keyed by path so a test that repoints `DB_PATH` gets a fresh connection, not a stale one.
_conn: sqlite3.Connection | None = None
_conn_path: Path | None = None
_lock = threading.Lock()


def enabled() -> bool:
    """The `telemetry` configure key — on unless set to false. An unreadable config counts as on,
    the same default an absent key gets; `dig`/`bark` already refuse to run on a corrupt one."""
    return user_config.get("telemetry") is not False


def _connect() -> sqlite3.Connection:
    """The process's connection to `DB_PATH`, opened and given its schema on first use. WAL mode
    lets another process (a `watchdog dig` against another vault, a benchmark arm) write at the
    same time; the short busy timeout bounds how long a contended write can hold up the event
    loop this runs on — a write that still can't get the lock fails and is logged, never waited
    out. Caller holds `_lock`."""
    global _conn, _conn_path
    if _conn is not None and _conn_path == DB_PATH:
        return _conn
    _close_locked()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=0.5, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=500")
    conn.executescript(_SCHEMA)
    _conn, _conn_path = conn, DB_PATH
    return conn


def _close_locked() -> None:
    global _conn, _conn_path
    if _conn is not None:
        try:
            _conn.close()
        finally:
            _conn, _conn_path = None, None


def close() -> None:
    """Close the process's connection, if open. Called at the end of every run."""
    with _lock:
        _close_locked()


def purge_vault(vault: Path) -> int:
    """Delete every row recorded for `vault`; returns how many. A no-op when the store doesn't
    exist — purging must never create it."""
    if not DB_PATH.exists():
        return 0
    with _lock:
        conn = _connect()
        cur = conn.execute("DELETE FROM calls WHERE vault_path = ?", (str(vault.resolve()),))
        conn.commit()
        return cur.rowcount


def record_call(record: dict, *, vault: Path, run_id: str, benchmark_arm_id: str | None,
                prompt_hash: str | None, config_snapshot: dict | None) -> None:
    """Insert one call record into the global store. `record` is the same flat dict
    `orchestrate._record_usage` builds for a vault's JSON usage file; this adds the fields that
    file doesn't carry (vault identity, benchmark tag, prompt hash, codebase version, config
    snapshot).

    Raises on failure (a locked db, disk full, a corrupt file) rather than swallowing — the
    caller (`orchestrate._record_usage`) is responsible for catching this and logging a WARN via
    the vault's own `ingest.log` instead of letting it propagate, since a telemetry write must
    never fail or slow down the actual ingest it's observing. Left raising here, not swallowed
    in this module, so a test can assert the failure path without needing a vault/log to check."""
    import watchdog
    row = (
        run_id, str(vault.resolve()), vault.name, benchmark_arm_id,
        record["task"], record["model"], record["backend"], record.get("effort"),
        record.get("auth_mode"), record.get("filename"), record.get("detail"),
        record["input_tokens"], record["output_tokens"],
        record["cache_read_tokens"], record["cache_write_tokens"],
        record.get("cost_usd"), record.get("latency_s"), record.get("attempts"),
        1 if record.get("failed") else 0, record.get("end_ts"),
        record.get("reasoning_tokens"), record.get("api_ms"), record.get("num_turns"),
        record.get("stop_reason"), record.get("est_input_tokens"), prompt_hash,
        watchdog.__version__,
        json.dumps(config_snapshot, ensure_ascii=False) if config_snapshot else None,
        json.dumps(record["pruned"], ensure_ascii=False) if record.get("pruned") else None,
        json.dumps(record["rate_limit"], ensure_ascii=False) if record.get("rate_limit") else None,
        record.get("batch_id"), record.get("batch_submitted_at"),
        record.get("batch_ended_at"), record.get("batch_collected_at"),
    )
    with _lock:
        conn = _connect()
        conn.execute(
            """INSERT INTO calls (
                run_id, vault_path, vault_name, benchmark_arm_id,
                task, model, backend, effort, auth_mode, filename, detail,
                input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                cost_usd, latency_s, attempts, failed, end_ts,
                reasoning_tokens, api_ms, num_turns, stop_reason, est_input_tokens,
                prompt_hash, codebase_version, config_json, pruned_json, rate_limit_json,
                batch_id, batch_submitted_at, batch_ended_at, batch_collected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            row,
        )
        conn.commit()
