"""First-run setup for the desktop app: the non-interactive counterpart of `watchdog setup`.

`setup.complete` writes the same configuration `watchdog setup` writes (an existing config file
is the "set up" signal), `setup.models` reports which local models are on disk, and
`auth.routeIngestion` points the three ingestion stages at one model from another provider,
exactly as the terminal wizard does after it asks for the provider (cmd/auth.py).
"""

from __future__ import annotations

from pathlib import Path

from watchdog.gui.rpc import RpcError, method


@method("setup.models")
def models() -> dict:
    """Which local models and tools are already on disk (directory listings, no network)."""
    from watchdog.gui import engine_setup
    return engine_setup.check()


@method("setup.downloadModel")
def download_model(model: str) -> dict:
    """Start a job that downloads one on-demand model ahead of time (D273), the same step the
    first recording would otherwise trigger. Only `transcription` is on demand today. The job's
    progress is the `model` event, so it shows in the job dock like any other run."""
    import sys
    from watchdog.gui import engine_setup, jobs
    if model not in engine_setup.ON_DEMAND:
        raise RpcError(f"“{model}” isn't a model that downloads on demand.", code="bad_params")
    label = dict(engine_setup.STEPS)[model]
    argv = [sys.executable, "-m", "watchdog.gui.engine_setup", "models", "--only", model]
    job = jobs.MANAGER.start(None, ["download-model", model], f"Download: {label}", "download-model",
                             argv=argv)
    return job.to_dict()


@method("setup.complete")
def complete(projects_dir: str | None = None, auto_approve: bool = False) -> dict:
    """Write the configuration `watchdog setup` writes, without asking anything: the projects
    folder, automatic worker counts, the auto-approve answer, and a pip-installable OCR engine
    where Tesseract is missing (only when the person has not chosen an engine themselves).
    Existing settings are merged, never replaced."""
    from watchdog.cmd import setup as setup_cmd
    from watchdog.gui import engine_setup
    from watchdog.gui.api.settings import _read_config

    config = _read_config()
    chosen = (projects_dir or "").strip() or config.get("projects_dir") or str(Path.home() / "Investigations")
    path = Path(chosen).expanduser().resolve()
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise RpcError(f"Watchdog can't create {path}: {e.strerror or e}", code="bad_value")
    config.update({"projects_dir": str(path), "chunk_workers": "auto", "chew_workers": "auto"})
    if "ocr_engine" not in config:
        engine = engine_setup.preferred_ocr_engine()
        if engine:
            config["ocr_engine"] = engine
    if auto_approve:
        config["auto_approve"] = True
    else:
        config.pop("auto_approve", None)
    setup_cmd._persist(config)
    return {"projects_dir": str(path), "ocr_engine": config.get("ocr_engine"),
            "auto_approve": bool(config.get("auto_approve"))}


@method("auth.routeIngestion")
def route_ingestion(provider: str, model: str) -> dict:
    """Route classifier, extractor and finalizer to `model` (a `provider:model` value from
    `settings.models`), once the provider's key or base URL is stored."""
    from watchdog.cmd import auth as auth_cmd
    from watchdog.cmd import setup as setup_cmd
    from watchdog.gui.api.settings import _auth_status, _check_provider, _read_config

    _check_provider(provider)
    if provider == "anthropic":
        raise RpcError("Claude models are chosen by name in Settings.", code="bad_params")
    value = (model or "").strip()
    if not value.startswith(f"{provider}:") or len(value) <= len(provider) + 1:
        raise RpcError(f"Choose a {provider} model.", code="bad_params")
    if not auth_cmd.provider_ready(provider):
        raise RpcError("Add this provider's key or address first.", code="not_ready")
    config = _read_config()
    auth_cmd.route_stages_to_model(config, provider, value)
    setup_cmd._persist(config)
    auth_cmd._maybe_restore_concurrency_from_subscription()
    return _auth_status()
