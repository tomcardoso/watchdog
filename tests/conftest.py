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
