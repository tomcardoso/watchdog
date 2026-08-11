"""Global, cross-vault telemetry store for per-call model usage (issue #611).

`pipeline.orchestrate._record_usage` already writes a rich per-call record to each vault's own
`.watchdog/registry/usage/usage-<ts>.json` — this module is an additive sink for the same data,
in one SQLite database shared across every vault, so a question like "what's actual median output
tokens for extract-section at high effort on gpt-5.6" can be answered with one query instead of
globbing and parsing every vault's usage files by hand. The JSON files stay authoritative for
`watchdog usage` and everything else that already reads them (D50, D86, D102, D132) — nothing here
changes their format or how they're written.

Beyond what the JSON record already carries, a row adds: which vault it came from (`vault_path`/
`vault_name`, since the store is global, not per-vault), which benchmark arm produced it if any
(`benchmark_arm_id`, None for an ordinary ingest run), a hash of the prompt actually sent
(`prompt_hash`, sha256 — catches config-driven prompt variation, not just template edits), the
codebase version that produced it (`codebase_version`, `watchdog.__version__`), and a snapshot of
the config values in effect for the run (`config_json`).

Raw provider responses are deliberately not captured here — see DECISIONS.md's entry for this
change. A write failure here must never break an ingest run: `record_call` swallows every error
and logs a WARN via the caller's own `_log`, the same best-effort posture `_record_usage` already
takes toward every other side channel."""

import json
import sqlite3
from pathlib import Path

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


def _connect() -> sqlite3.Connection:
    """Open `DB_PATH`, creating its parent/schema on first use. WAL mode + a busy timeout so a
    second concurrent writer (a separate `watchdog dig` process against another vault, or a
    benchmark sweep's back-to-back arms) retries instead of raising `database is locked` — the
    JSON usage files never had this problem since each vault's own directory only ever sees one
    writer, but this store is shared across every vault."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    return conn


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
    conn = _connect()
    try:
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
    finally:
        conn.close()
