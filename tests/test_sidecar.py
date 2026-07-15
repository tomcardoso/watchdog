from watchdog.pipeline import sidecar


def test_filter_and_render_keeps_allowed_keys_only():
    raw = ("source: https://sedar.com/x\nobtained: 2026-06-05\n"
           "notes: check p.12\nunknown_field: nope\n")
    clean, dropped = sidecar.filter_and_render(raw)
    assert sidecar.parse(clean) == {
        "source": "https://sedar.com/x", "obtained": "2026-06-05", "notes": "check p.12"}
    assert dropped == ["unknown_field"]


def test_filter_and_render_absent_or_malformed():
    assert sidecar.filter_and_render(None) == (None, [])
    assert sidecar.filter_and_render("just a string, not a map\n") == (None, [])


def test_filter_and_render_nothing_survives_the_allowlist():
    clean, dropped = sidecar.filter_and_render("unknown_field: nope\n")
    assert clean is None
    assert dropped == ["unknown_field"]


def test_filter_and_render_caps_value_length():
    raw = "notes: " + ("x" * (sidecar.MAX_VALUE_LEN + 500)) + "\n"
    clean, dropped = sidecar.filter_and_render(raw)
    assert len(sidecar.parse(clean)["notes"]) == sidecar.MAX_VALUE_LEN
    assert dropped == []


def test_provenance_parsed_from_filtered_text():
    clean, _ = sidecar.filter_and_render("source: https://sedar.com/x\nobtained: 2026-06-05\n")
    assert sidecar.provenance(clean) == {
        "source": "https://sedar.com/x", "obtained": "2026-06-05"}


def test_provenance_absent_or_none():
    assert sidecar.provenance(None) == {}
    assert sidecar.provenance("notes: no source here\n") == {}


def test_skill_pin_parsed_from_filtered_text():
    clean, _ = sidecar.filter_and_render("skill: bankruptcy\n")
    assert sidecar.skill_pin(clean) == "bankruptcy"


def test_skill_pin_absent():
    assert sidecar.skill_pin(None) is None
    assert sidecar.skill_pin("source: https://x\n") is None
