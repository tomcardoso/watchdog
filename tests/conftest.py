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
