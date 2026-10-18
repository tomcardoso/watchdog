"""`config.home()` is the one place Watchdog's own folder is decided (CLAUDE.md, Testing): no
module builds `~/.watchdog` itself, so a test's `config.HOME` moves every file kept there."""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "watchdog"

# `Path.home() / ".watchdog"`, `expanduser("~/.watchdog…")`, `os.path.join(…, ".watchdog")` under a
# home directory. The Claude Code rule strings in vault.py (`Read(~/.watchdog/**)`) are patterns
# the CLI expands, not paths Watchdog opens.
_HAND_BUILT = re.compile(r"""home\(\)\s*/\s*["']\.watchdog|expanduser\(\s*["']~/\.watchdog|home[^\n]{0,40}["']\.watchdog["']""")


def test_nothing_but_config_builds_the_watchdog_folder():
    offenders = []
    for path in SRC.rglob("*.py"):
        if path.name == "config.py" and path.parent == SRC:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _HAND_BUILT.search(line):
                offenders.append(f"{path.relative_to(SRC)}:{n}: {line.strip()}")
    assert not offenders, "use watchdog.config.home():\n" + "\n".join(offenders)


def test_the_folder_moves_with_config_home(tmp_path, monkeypatch):
    from watchdog import access, config, skills_catalog, telemetry_db
    monkeypatch.setattr(config, "HOME", tmp_path / "wd")
    monkeypatch.setattr(telemetry_db, "DB_PATH", None)
    monkeypatch.setattr(skills_catalog, "USER_SKILLS_DIR", None)
    monkeypatch.delenv(access.ACCESS_FILE_ENV, raising=False)
    assert telemetry_db.db_path() == tmp_path / "wd" / "telemetry.db"
    assert skills_catalog.user_skills_dir() == tmp_path / "wd" / "skills" / "records"
    assert access.access_file() == tmp_path / "wd" / "access.json"
