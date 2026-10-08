

def test_settings_help_in_the_app_names_no_terminal_commands():
    import re
    from watchdog.gui import server
    from watchdog.gui.api import settings as settings_api
    server.load_api()
    for section in settings_api.schema()["sections"]:
        for row in section["keys"]:
            for field in ("short", "help"):
                assert not re.search(r"watchdog [a-z]|--[a-z]", row[field] or ""), (row["key"], field)
    assert "Activity → Maintenance" in settings_api.app_text("After changing this, run `watchdog reindex` — rebuilt.")
    assert settings_api.app_text("Keep this. Override for one run with `watchdog dig --x N`.") == "Keep this."
