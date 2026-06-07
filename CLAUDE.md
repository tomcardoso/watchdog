# Watchdog — developer notes

## Testing

Write tests for new features and any non-trivial function. The suite lives in `tests/` and runs with:

```
pipx run pytest
```

Tests use `tmp_path` and `monkeypatch` to redirect `WATCHDOG_HOME`, `PROJECTS_FILE`, and `CONFIG_FILE` away from the real home directory — patch all three when testing anything that touches the registry or projects list. See the `wdg_home` and `configured` fixtures in `tests/test_cli.py` for the pattern.

CI runs on every push and PR via `.github/workflows/ci.yml`.
