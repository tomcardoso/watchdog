"""Terminal hyperlinks into Obsidian (#709)."""

from watchdog import links, terminal


def test_obsidian_url_points_at_the_note_file(tmp_path):
    url = links.obsidian_url(tmp_path, "entities/person/jane-doe")
    assert url.startswith("obsidian://open?path=")
    assert url.endswith("jane-doe.md")
    assert "%2Fentities%2Fperson%2F" in url            # the whole path is percent-encoded


def test_obsidian_url_keeps_an_existing_extension(tmp_path):
    assert links.obsidian_url(tmp_path, "morgue/a/b/order.pdf").endswith("order.pdf")


def test_links_are_plain_text_off_a_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr(terminal, "_COLOR", False)
    assert links.note_link(tmp_path, "documents/x", "Report") == "Report"


def test_links_are_osc8_hyperlinks_on_a_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr(terminal, "_COLOR", True)
    out = links.note_link(tmp_path, "documents/x", "Report")
    assert out.startswith("\033]8;;obsidian://open?path=") and "\033\\Report\033]8;;\033\\" in out


def test_no_path_means_no_link(tmp_path, monkeypatch):
    monkeypatch.setattr(terminal, "_COLOR", True)
    assert links.note_link(tmp_path, None, "Report") == "Report"
