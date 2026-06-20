"""Tests for per-vault command-skill installation (setup_cmd.py).

Record (domain) skills are global now — see tests/test_skills_catalog.py.
"""

from watchdog.setup_cmd import install_skills


def test_install_skills_installs_command_skills(tmp_path):
    install_skills(tmp_path)
    installed = {p.name for p in tmp_path.glob("*.md")}
    assert "watchdog-query.md" in installed          # a Claude Code command skill landed


def test_install_skills_does_not_copy_records(tmp_path):
    install_skills(tmp_path)
    # Domain/record skills live globally now and are not seeded into the vault.
    assert not (tmp_path / "records").exists()
