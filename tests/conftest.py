import pytest


@pytest.fixture(autouse=True)
def reset_pipeline_config_caches():
    """Reset module-level config caches before every test to prevent cross-test pollution."""
    import watchdog.pipeline.preprocess as preprocess_mod
    import watchdog.pipeline.near_dup as near_dup_mod
    preprocess_mod._reset_config_cache()
    near_dup_mod._reset_config_cache()
    yield
    preprocess_mod._reset_config_cache()
    near_dup_mod._reset_config_cache()


@pytest.fixture(autouse=True)
def isolate_telemetry_db(tmp_path, monkeypatch):
    """Redirect the global telemetry store (#611) into `tmp_path` for every test, independent of
    `test_cli.py`'s `wdg_home` fixture — any test that exercises `_record_usage`/`orchestrate.run`/
    `orchestrate.finalize` against a real vault would otherwise write to the developer's actual
    `~/.watchdog/telemetry.db`, which `wdg_home` alone doesn't cover for test files (e.g.
    `test_orchestrate.py`) that build their own vault without going through it."""
    import watchdog.telemetry_db as telemetry_db_mod
    monkeypatch.setattr(telemetry_db_mod, "DB_PATH", tmp_path / "telemetry.db")
    # Recording is on for tests regardless of the developer's own `telemetry` setting; the
    # setting itself is tested against `telemetry_db.enabled` imported before this patch.
    monkeypatch.setattr(telemetry_db_mod, "enabled", lambda: True)
    yield
    telemetry_db_mod.close()


@pytest.fixture(autouse=True)
def outside_a_claude_code_session(monkeypatch):
    """Tests run as a person at a terminal. Claude Code marks its own shells with CLAUDECODE=1,
    and `watchdog search` confines itself when it sees that, so a developer running the suite
    from a session would otherwise get different results than CI."""
    monkeypatch.delenv("CLAUDECODE", raising=False)


@pytest.fixture(autouse=True)
def no_investigation_key_scope():
    """A run sets the process's key-choice scope to its vault (#690, D290); clear it around every
    test so one test's vault never decides which key another test's lookups resolve."""
    from watchdog.cmd import auth
    auth.use_investigation(None)
    yield
    auth.use_investigation(None)


@pytest.fixture(autouse=True)
def no_app_provided_keys(monkeypatch):
    """Keys the app hands a process live in `watchdog.keystore`'s memory (D295); clear them around
    every test, and never let a test read its keys from the suite's own stdin."""
    from watchdog import keystore
    monkeypatch.delenv(keystore.SECRETS_ENV, raising=False)
    keystore.forget()
    yield
    keystore.forget()
