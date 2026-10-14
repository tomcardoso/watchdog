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
    the `wdg_home` fixtures — any test that exercises `_record_usage`/`orchestrate.run`/
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
def no_investigation_key_scope():
    """A run sets the process's key-choice scope to its vault (#690, D290); clear it around every
    test so one test's vault never decides which key another test's lookups resolve."""
    from watchdog import auth
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


@pytest.fixture
def catalog_entry(monkeypatch):
    """Add a synthetic model to the catalog for one test, for behaviour no current model has (D310
    removed the last Claude model with thinking off by default, and the last OpenAI model from
    before explicit cache breakpoints). Call it with the entry's fields, as `model_catalog.yaml`
    spells them; it returns the id."""
    from watchdog import model_catalog as cat

    def add(**entry):
        mid = entry["id"]
        monkeypatch.setitem(cat._MODELS, mid, entry)
        for alias in cat._tier_aliases(entry):
            monkeypatch.setitem(cat._MODEL_IDS, alias, mid)
        if entry["provider"] == "anthropic":
            monkeypatch.setitem(cat._PRICING, mid, (float(entry["input"]), float(entry["output"]),
                                                    float(entry["cache_write"]),
                                                    float(entry["cache_read"])))
        else:
            monkeypatch.setitem(cat._OPENAI_PRICING, mid, (
                float(entry["input"]), float(entry["output"]), float(entry["cached_input"]),
                float(entry.get("cache_write", 0.0))))
        return mid
    return add


@pytest.fixture
def thinking_off_claude(catalog_entry):
    """A Claude model that ships with thinking off (`thinking: true`), shaped like Sonnet 4.6 was:
    the case `catalog_needs_thinking_param` exists for (D206)."""
    return catalog_entry(id="claude-test-thinking-off", name="Claude Test", provider="anthropic",
                         tier="test-thinking-off", input=3.0e-6, output=15.0e-6,
                         cache_write=3.75e-6, cache_read=0.30e-6, context_window=200_000,
                         max_output_tokens=128_000, effort_levels=["low", "medium", "high", "max"],
                         thinking=True)
