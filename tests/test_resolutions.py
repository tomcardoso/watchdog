import json
from pathlib import Path

from watchdog.pipeline import resolutions


def _vault(tmp_path: Path) -> Path:
    (tmp_path / ".watchdog" / "registry").mkdir(parents=True)
    return tmp_path


# ── id builders ──────────────────────────────────────────────────────────────────

def test_id_builders_are_stable_and_namespaced():
    assert resolutions.lead_id("isolated", "acme") == "lead:isolated:acme"
    a = resolutions.alert_id("abcdef1234567890", "offshore")
    assert a.startswith("alert:abcdef1:")
    # A regex/whitespace term still yields a single clean token (hashed).
    assert " " not in resolutions.alert_id("sha0000", "/foo bar/")
    c1 = resolutions.contradiction_id("> [!contradiction] Address differs")
    # Cosmetic reflow (extra whitespace, case, blockquote marker) doesn't change the id.
    c2 = resolutions.contradiction_id(">   [!contradiction]   address DIFFERS  ")
    assert c1 == c2 and c1.startswith("contradiction:")


# ── store I/O ─────────────────────────────────────────────────────────────────

def test_missing_and_corrupt_store_reads_empty(tmp_path):
    v = _vault(tmp_path)
    assert resolutions.resolved_ids(v) == frozenset()
    (v / ".watchdog" / "registry" / "resolutions.json").write_text("{ not json", encoding="utf-8")
    assert resolutions.resolved_ids(v) == frozenset()


def test_resolve_unresolve_roundtrip_and_idempotence(tmp_path):
    v = _vault(tmp_path)
    added = resolutions.resolve(v, ["lead:isolated:john", "lead:inferred:carol"], label="manual")
    assert set(added) == {"lead:isolated:john", "lead:inferred:carol"}
    # Re-resolving an existing id is a no-op.
    assert resolutions.resolve(v, ["lead:isolated:john"]) == []
    assert resolutions.resolved_ids(v) == {"lead:isolated:john", "lead:inferred:carol"}

    removed = resolutions.unresolve(v, ["lead:isolated:john", "never-there"])
    assert removed == ["lead:isolated:john"]
    assert resolutions.resolved_ids(v) == {"lead:inferred:carol"}

    stored = json.loads((v / ".watchdog" / "registry" / "resolutions.json").read_text())
    assert stored["schema_version"] == 1
    assert stored["resolved"]["lead:inferred:carol"]["label"] == "manual"


# ── merge propagation (#219 / D54) ───────────────────────────────────────────────

def test_remap_entity_moves_only_lead_keys(tmp_path):
    v = _vault(tmp_path)
    resolutions.resolve(v, [
        "lead:isolated:dupe", "lead:inferred:dupe",
        "contradiction:deadbeef1234", "alert:abc1234:0f0f0f0f",
    ])
    moved = resolutions.remap_entity(v, "dupe", "survivor")
    assert moved == 2
    ids = resolutions.resolved_ids(v)
    assert "lead:isolated:survivor" in ids and "lead:inferred:survivor" in ids
    assert not any(rid.endswith(":dupe") for rid in ids)
    # Content-keyed ids are untouched by the merge.
    assert "contradiction:deadbeef1234" in ids and "alert:abc1234:0f0f0f0f" in ids


def test_remap_entity_noop_when_nothing_matches(tmp_path):
    v = _vault(tmp_path)
    resolutions.resolve(v, ["lead:isolated:other"])
    assert resolutions.remap_entity(v, "dupe", "survivor") == 0


# ── checkbox sync ────────────────────────────────────────────────────────────────

def test_sync_from_briefings_imports_ticked_and_reopens_cleared(tmp_path):
    v = _vault(tmp_path)
    briefings = v / "briefings"
    briefings.mkdir()
    (briefings / "leads-2025-01-01.md").write_text(
        "# Investigative leads\n\n"
        "- [x] **Acme Ltd** — appears in 4 documents <!--wid:lead:isolated:acme-->\n"
        "- [ ] **Beta Co** — appears in 3 documents <!--wid:lead:isolated:beta-->\n"
        "- [x] no marker here so it is skipped\n",
        encoding="utf-8")
    # beta was previously resolved; clearing its box should reopen it.
    resolutions.resolve(v, ["lead:isolated:beta"])

    added, removed = resolutions.sync_from_briefings(v)
    assert added == ["lead:isolated:acme"]
    assert removed == ["lead:isolated:beta"]
    assert resolutions.resolved_ids(v) == {"lead:isolated:acme"}


def test_sync_with_no_briefings_dir_is_noop(tmp_path):
    v = _vault(tmp_path)
    assert resolutions.sync_from_briefings(v) == ([], [])


# ── callout filtering ────────────────────────────────────────────────────────────

def test_filter_callouts_drops_only_resolved_blocks():
    a = "> [!contradiction] Address differs"
    b = "> [!contradiction] Director count differs"
    resolved = frozenset({resolutions.contradiction_id(a)})
    out = resolutions.filter_callouts([a, b], resolved)
    assert out == [b]


def test_filter_callouts_empty_resolved_is_passthrough():
    callouts = ["> [!contradiction] Something"]
    assert resolutions.filter_callouts(callouts, frozenset()) == callouts


def test_split_callouts_splits_on_blank_lines():
    body = "> [!contradiction] First\n\n> [!contradiction] Second"
    assert resolutions.split_callouts(body) == [
        "> [!contradiction] First",
        "> [!contradiction] Second",
    ]


def test_split_callouts_empty_is_empty_list():
    assert resolutions.split_callouts("") == []
    assert resolutions.split_callouts("   \n\n  ") == []


def test_dedup_callouts_keeps_first_seen_wording():
    a = "> [!contradiction]   Address differs"
    b = "> [!contradiction] Address differs"
    c = "> [!contradiction] Director count differs"
    assert resolutions.dedup_callouts([a, b, c]) == [a, c]
