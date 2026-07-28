"""Unit tests for the automated benchmark runner (#361 / #215 / #466).

`benchmarks/run_benchmark.py`, `benchmarks/bench_report.py`, and `benchmarks/score_arms.py` live
outside `src/` (dev-only tooling, never shipped) — they're added to `sys.path` here so this file
can import them directly, the same way `score_arms.py` itself has always been run as a standalone
script rather than an installed package module.

Only the pure/deterministic pieces are exercised here: config validation, vault-seeding string
rewriting, cost-preview aggregation, the try/except-per-arm resilience path, report-markdown
generation, and the `score_arms.py` refactor. Real I/O (actual vault creation, chewing, model
calls) is not unit-tested here, matching `score_arms.py`'s own convention — that's exercised by
actually running the tool under the live-approval rule, same as any other real ingest.
"""
import json
import sys
from pathlib import Path

import pytest
import yaml

BENCHMARKS_DIR = Path(__file__).resolve().parent.parent / "benchmarks"
if str(BENCHMARKS_DIR) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS_DIR))

import bench_report as br  # noqa: E402
import run_benchmark as rb  # noqa: E402
import score_arms as sa  # noqa: E402

import watchdog.cmd.ingest as wd_ingest  # noqa: E402
import watchdog.interactive as wd_interactive  # noqa: E402


# ── load_config ───────────────────────────────────────────────────────────────

def _write_config(tmp_path, **overrides):
    config = {
        "corpus": {"dir": "corpus", "sha256": "corpus/corpus-v1.sha256"},
        "keys": {"dir": "keys", "sha256": "keys/keys-v1.sha256"},
        "master_vault": {"name": "bench-master", "classify_name": "bench-classify-master"},
        "extractor_sweep": {
            "vault_prefix": "bench-ex3",
            "arms": [{"id": "haiku", "extractor_model": "haiku"}],
        },
        "finalizer_sweep": {
            "vault_prefix": "bench-fn",
            "base": {"extractor_model": "deepseek:deepseek-v4-flash"},
            "arms": [{"id": "haiku", "finalizer_model": "haiku"}],
        },
    }
    config.update(overrides)
    path = tmp_path / "benchmark.yaml"
    path.write_text(yaml.safe_dump(config))
    return path


def test_load_config_accepts_well_formed_matrix(tmp_path):
    config = rb.load_config(_write_config(tmp_path))
    assert config["extractor_sweep"]["arms"][0]["id"] == "haiku"


def test_load_config_rejects_duplicate_arm_id(tmp_path):
    path = _write_config(tmp_path, extractor_sweep={
        "vault_prefix": "bench-ex3",
        "arms": [{"id": "haiku", "extractor_model": "haiku"},
                {"id": "haiku", "extractor_model": "sonnet"}],
    })
    with pytest.raises(SystemExit):
        rb.load_config(path)


def test_load_config_rejects_missing_model_field(tmp_path):
    path = _write_config(tmp_path, extractor_sweep={
        "vault_prefix": "bench-ex3",
        "arms": [{"id": "haiku"}],
    })
    with pytest.raises(SystemExit):
        rb.load_config(path)


def test_load_config_tolerates_unknown_top_level_key(tmp_path):
    config = rb.load_config(_write_config(tmp_path, some_future_section={"anything": True}))
    assert config["some_future_section"] == {"anything": True}


# ── verify_freeze ─────────────────────────────────────────────────────────────

def test_verify_freeze_passes_on_matching_hash(tmp_path):
    target = tmp_path / "doc.txt"
    target.write_text("hello")
    import hashlib
    digest = hashlib.sha256(b"hello").hexdigest()
    manifest = tmp_path / "manifest.sha256"
    manifest.write_text(f"{digest}  doc.txt\n")
    rb.verify_freeze(tmp_path, manifest)  # must not raise


def test_verify_freeze_exits_on_drifted_file(tmp_path):
    target = tmp_path / "doc.txt"
    target.write_text("hello")
    manifest = tmp_path / "manifest.sha256"
    manifest.write_text("0" * 64 + "  doc.txt\n")
    with pytest.raises(SystemExit):
        rb.verify_freeze(tmp_path, manifest)


# ── seed_arm_vault ─────────────────────────────────────────────────────────────

def test_seed_arm_vault_rewrites_master_path(tmp_path):
    master = tmp_path / "master"
    dest = tmp_path / "dest"
    (master / ".watchdog" / "staging").mkdir(parents=True)
    (master / ".watchdog" / "staging" / "doc.txt").write_text("chewed content")
    (master / ".watchdog" / "queue").mkdir(parents=True)
    queue_entry = {"staging_path": str(master / ".watchdog" / "staging" / "doc.txt")}
    (master / ".watchdog" / "queue" / "abc.json").write_text(json.dumps(queue_entry))

    n = rb.seed_arm_vault(master, dest)

    assert n == 1
    seeded = json.loads((dest / ".watchdog" / "queue" / "abc.json").read_text())
    assert seeded["staging_path"] == str(dest / ".watchdog" / "staging" / "doc.txt")
    assert (dest / ".watchdog" / "staging" / "doc.txt").read_text() == "chewed content"


def test_seed_arm_vault_refuses_to_reseed(tmp_path):
    master = tmp_path / "master"
    dest = tmp_path / "dest"
    (master / ".watchdog" / "staging").mkdir(parents=True)
    (master / ".watchdog" / "queue").mkdir(parents=True)
    (master / ".watchdog" / "queue" / "abc.json").write_text("{}")
    rb.seed_arm_vault(master, dest)
    with pytest.raises(SystemExit):
        rb.seed_arm_vault(master, dest)


# ── vault_root: shadow vault isolation (#475 follow-up, D146) ──────────────────
#
# Benchmark vaults must never land in the installed watchdog's real ~/investigations, and must
# never leave a trace in ~/.watchdog/projects.json — that pollution (12+ stray bench-* entries)
# is exactly what prompted this fix. See DECISIONS.md D146.

def test_vault_root_defaults_to_dot_vaults(tmp_path):
    assert rb.vault_root({}, tmp_path, None) == rb.HERE / ".vaults"


def test_vault_root_reads_config_key_relative_to_config_dir(tmp_path):
    config = {"vault_root": "custom-vaults"}
    assert rb.vault_root(config, tmp_path, None) == (tmp_path / "custom-vaults").resolve()


def test_vault_root_cli_flag_overrides_config_key(tmp_path):
    config = {"vault_root": "custom-vaults"}
    override = tmp_path / "cli-vaults"
    assert rb.vault_root(config, tmp_path, override) == override.resolve()


def test_arm_vault_uses_given_root(tmp_path):
    assert rb.arm_vault("bench-ex3", "haiku", tmp_path) == tmp_path / "bench-ex3-haiku"


def test_ensure_master_vault_passes_shadow_root_to_cmd_new(monkeypatch, tmp_path):
    """A benchmark vault must be created under the shadow root it's handed, never wherever the
    installed watchdog's own _projects_dir() happens to resolve. Mocks cmd_new/_run_preprocess
    rather than doing a real chew, matching this file's convention (see module docstring) of not
    exercising real vault I/O."""
    import watchdog.cmd.vault as wd_vault

    captured = {}

    def _fake_cmd_new(args):
        captured["dir"] = args.dir
        # Real cmd_new would build the full vault layout on disk; recreate just enough for the
        # existence checks ensure_master_vault performs right after.
        (Path(args.dir) / args.name / ".watchdog" / "queue").mkdir(parents=True)
        (Path(args.dir) / args.name / "_INCOMING").mkdir(parents=True)

    monkeypatch.setattr(wd_vault, "cmd_new", _fake_cmd_new)
    monkeypatch.setattr(rb, "_deregister_benchmark_vault",
                        lambda slug: captured.setdefault("deregistered", slug))
    monkeypatch.setattr(wd_ingest, "_run_preprocess", lambda *a, **k: None)

    shadow_root = tmp_path / "shadow"
    vault = rb.ensure_master_vault("bench-master-test", [], with_sidecars=True, root=shadow_root)

    assert captured["dir"] == str(shadow_root)
    assert vault == shadow_root / "bench-master-test"
    assert captured["deregistered"] == "bench-master-test"


def test_deregister_benchmark_vault_removes_only_its_own_slug(monkeypatch, tmp_path):
    """cmd_new always registers the vault it creates — undoing that must touch only the slug
    just created, never any other project (a real investigation, or another benchmark vault)."""
    import watchdog.cmd.base as wd_base
    home = tmp_path / ".watchdog"
    home.mkdir()
    monkeypatch.setattr(wd_base, "WATCHDOG_HOME", home)
    monkeypatch.setattr(wd_base, "PROJECTS_FILE", home / "projects.json")
    wd_base.save_projects({"bench-master": {"name": "bench-master"},
                          "toms-real-investigation": {"name": "Tom's real investigation"}})

    rb._deregister_benchmark_vault("bench-master")

    projects = wd_base.load_projects()
    assert "bench-master" not in projects
    assert "toms-real-investigation" in projects


def test_deregister_benchmark_vault_is_a_noop_when_slug_absent(monkeypatch, tmp_path):
    import watchdog.cmd.base as wd_base
    home = tmp_path / ".watchdog"
    home.mkdir()
    monkeypatch.setattr(wd_base, "WATCHDOG_HOME", home)
    monkeypatch.setattr(wd_base, "PROJECTS_FILE", home / "projects.json")
    wd_base.save_projects({"toms-real-investigation": {"name": "x"}})

    rb._deregister_benchmark_vault("bench-master")  # never registered — must not raise

    assert wd_base.load_projects() == {"toms-real-investigation": {"name": "x"}}


# ── _quiet: output suppression, never at the cost of a swallowed failure ──────

def test_quiet_suppresses_stdout_on_success(capsys):
    def _noisy():
        print("loud banner")
        return 42
    result = rb._quiet(_noisy)
    assert result == 42
    assert capsys.readouterr().out == ""


def test_quiet_reprints_captured_buffer_before_reraising(capsys):
    def _noisy_then_fails():
        print("about to explode")
        raise RuntimeError("boom")
    with pytest.raises(RuntimeError, match="boom"):
        rb._quiet(_noisy_then_fails)
    assert "about to explode" in capsys.readouterr().out


# ── cost preview / confirm_run ─────────────────────────────────────────────────

def test_confirm_run_estimate_only_never_asks(monkeypatch, capsys):
    def _boom(*a, **k):
        raise AssertionError("must not prompt when --estimate-only")
    monkeypatch.setattr(wd_interactive, "confirm", _boom)

    previews = [
        ("extractor:haiku", {"cost_low": 1.0, "cost_high": 2.0}, {}),
        ("extractor:local", {"cost_low": None, "cost_high": None}, {}),
    ]
    result = rb.confirm_run(previews, estimate_only=True)
    assert result is False
    out = capsys.readouterr().out
    assert "extractor:haiku" in out
    assert "no dollar estimate" in out
    assert "TOTAL" in out


def test_confirm_run_asks_once_when_not_estimate_only(monkeypatch):
    calls = []
    monkeypatch.setattr(wd_interactive, "confirm",
                        lambda *a, **k: calls.append((a, k)) or True)
    previews = [("extractor:haiku", {"cost_low": 1.0, "cost_high": 2.0}, {})]
    assert rb.confirm_run(previews, estimate_only=False) is True
    assert len(calls) == 1


# ── resilience: try/except SystemExit per arm ──────────────────────────────────

def test_run_extractor_arm_records_failure_without_raising(monkeypatch, tmp_path):
    def _fails(ns):
        raise SystemExit("boom")
    monkeypatch.setattr(wd_ingest, "cmd_extract", _fails)
    result = rb.run_extractor_arm({"id": "haiku", "extractor_model": "haiku"}, tmp_path)
    assert result.ok is False
    assert result.error == "boom"
    assert result.arm_id == "haiku"
    assert result.stage == "extractor"


def test_run_extractor_arm_ok_reads_usage(monkeypatch, tmp_path):
    monkeypatch.setattr(wd_ingest, "cmd_extract", lambda ns: None)
    usage_dir = tmp_path / ".watchdog" / "registry" / "usage"
    usage_dir.mkdir(parents=True)
    (usage_dir / "usage-1.json").write_text(json.dumps({"calls": [{"cost_usd": 1.5}]}))
    result = rb.run_extractor_arm({"id": "haiku", "extractor_model": "haiku"}, tmp_path)
    assert result.ok is True
    assert result.usage["calls"][0]["cost_usd"] == 1.5


# ── run_finalizer_arm delegates correctly ──────────────────────────────────────

def test_run_finalizer_arm_passes_arm_knobs_through(monkeypatch, tmp_path):
    captured = {}
    def _capture(ns):
        captured["finalizer_model"] = ns.finalizer_model
        captured["finalizer_effort"] = ns.finalizer_effort
    monkeypatch.setattr(wd_ingest, "cmd_finalize", _capture)

    arm = {"id": "gemini-flash-lite", "finalizer_model": "gemini:gemini-3.1-flash-lite"}
    result = rb.run_finalizer_arm(arm, tmp_path)

    assert result.ok is True
    assert captured["finalizer_model"] == "gemini:gemini-3.1-flash-lite"
    assert captured["finalizer_effort"] is None


def test_run_finalizer_arm_records_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(wd_ingest, "cmd_finalize",
                        lambda ns: (_ for _ in ()).throw(SystemExit("locked")))
    result = rb.run_finalizer_arm({"id": "haiku", "finalizer_model": "haiku"}, tmp_path)
    assert result.ok is False
    assert result.error == "locked"


# ── classifier-sweep skip path ─────────────────────────────────────────────────

def test_classify_corpus_ready_false_when_empty(tmp_path):
    assert rb.classify_corpus_ready(tmp_path) is False


def test_classify_corpus_ready_false_without_expected_yaml(tmp_path):
    (tmp_path / "doc.pdf").write_bytes(b"%PDF-1.4")
    assert rb.classify_corpus_ready(tmp_path) is False


def test_classify_corpus_ready_true_when_populated(tmp_path):
    (tmp_path / "doc.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "expected.yaml").write_text("doc.pdf: corporate-filings\n")
    assert rb.classify_corpus_ready(tmp_path) is True


# ── report generation ──────────────────────────────────────────────────────────

def _arm_result(**kwargs):
    defaults = dict(arm_id="a", stage="extractor", vault=None, ok=True)
    defaults.update(kwargs)
    return rb.ArmResult(**defaults)


def test_extractor_table_renders_failed_arm():
    results = [_arm_result(arm_id="haiku", ok=False, error="rate limited")]
    table = br.extractor_table_md(results, {"totals": {"facts": {}, "must_not_miss": {}}})
    assert "failed: rate limited" in table


def test_extractor_table_renders_scores_and_usage():
    results = [_arm_result(arm_id="sonnet-high", vault="/tmp/bench-ex3-sonnet-high",
                          usage={"calls": [{"cost_usd": 1.0, "latency_s": 20,
                                           "attempts": 2, "task": "extract"}]})]
    scores = {"totals": {"facts": {"bench-ex3-sonnet-high": {"hit": 30, "of": 39}},
                        "must_not_miss": {"bench-ex3-sonnet-high": {"hit": 10, "of": 20}}}}
    table = br.extractor_table_md(results, scores)
    assert "77% (30/39)" in table
    assert "50% (10/20)" in table
    assert "$1.000" in table
    assert "1 retries" in table


def test_classifier_sweep_table_renders_skip_notice():
    results = [_arm_result(arm_id="classifier-sweep", stage="classifier-sweep",
                          vault=None, ok=True, skipped=True)]
    assert "Skipped" in br.classifier_sweep_table_md(results)


def test_classifier_sweep_table_renders_scores():
    results = [_arm_result(arm_id="haiku", stage="classifier-sweep",
                          extra={"classification": {"a.pdf": {"expected": "real-estate",
                                                              "got": "real-estate", "ok": True},
                                                    "b.pdf": {"expected": "corporate-filings",
                                                              "got": "bankruptcy", "ok": False}}})]
    table = br.classifier_sweep_table_md(results)
    assert "1/2" in table


def test_write_run_layout(tmp_path):
    vault = tmp_path / "vault" / "bench-ex3-haiku"
    (vault / ".watchdog" / "extracted").mkdir(parents=True)
    (vault / ".watchdog" / "extracted" / "abc.json").write_text("{}")
    results = [_arm_result(arm_id="haiku", vault=str(vault), usage={"calls": []})]
    scores = {"totals": {"facts": {}, "must_not_miss": {}}, "detail": [], "unscorable": [],
             "vaults": []}
    config = {"corpus": {"sha256": "corpus/corpus-v1.sha256"},
             "keys": {"sha256": "keys/keys-v1.sha256"}}
    out_root = tmp_path / "benchmarks"

    run_dir = br.write_run(out_root, results, scores, config)

    assert (run_dir / "REPORT.md").exists()
    assert (run_dir / "docs-summary.md").exists()
    assert (run_dir / "config.yaml").exists()
    assert (run_dir / "artifacts" / "bench-ex3-haiku" / "extracted" / "abc.json").exists()

    run_dir_2 = br.write_run(out_root, results, scores, config)
    assert run_dir_2 != run_dir


# ── score_arms.py refactor regression ──────────────────────────────────────────

def _write_key(tmp_path, name, facts, must_not_miss=None):
    if must_not_miss is None:
        must_not_miss = [{"id": "M1", "item": "a buried item nobody should miss, e.g. $9,876,543."}]
    keys_dir = tmp_path / "keys"
    keys_dir.mkdir(exist_ok=True)
    (keys_dir / f"{name}.yaml").write_text(
        yaml.safe_dump({"facts": facts, "must_not_miss": must_not_miss}))
    return keys_dir


def test_score_returns_documented_shape(tmp_path):
    keys_dir = _write_key(tmp_path, "doc1",
                          [{"id": "F1", "fact": "Revenue was $1,234,567 in 2024."}],
                          must_not_miss=[])
    vault = tmp_path / "vault-a"
    extracted = vault / ".watchdog" / "extracted"
    extracted.mkdir(parents=True)
    (extracted / "abc.json").write_text(json.dumps({"document": {"note": "Revenue $1,234,567"}}))

    result = sa.score([str(vault)], keys_dir=keys_dir)

    assert result["vaults"] == ["vault-a"]
    assert len(result["detail"]) == 1
    cell = result["detail"][0]["cells"]["vault-a"]
    assert cell["hit"] is True
    assert result["totals"]["facts"]["vault-a"] == {"hit": 1, "of": 1}


def test_score_main_prints_summary(tmp_path, capsys):
    keys_dir = _write_key(tmp_path, "doc1", [{"id": "F1", "fact": "Revenue was $1,234,567 in 2024."}])
    vault = tmp_path / "vault-a"
    extracted = vault / ".watchdog" / "extracted"
    extracted.mkdir(parents=True)
    (extracted / "abc.json").write_text(json.dumps({"document": {"note": "Revenue $1,234,567"}}))

    monkeypatch_keys = sa.KEYS
    try:
        sa.KEYS = sorted(keys_dir.glob("*.yaml"))
        sa.main([str(vault)])
    finally:
        sa.KEYS = monkeypatch_keys

    out = capsys.readouterr().out
    assert "doc1:F1" in out
    assert "FACTS (numeric-scorable)" in out
    assert "unscorable" in out


def test_score_main_reports_na_instead_of_crashing_on_zero_item_category(tmp_path, capsys):
    """A key with no must_not_miss items used to raise ZeroDivisionError in main()'s summary
    print (h / n * 100 with n == 0) — must print 'n/a' for that category instead."""
    keys_dir = _write_key(tmp_path, "doc1",
                          [{"id": "F1", "fact": "Revenue was $1,234,567 in 2024."}],
                          must_not_miss=[])
    vault = tmp_path / "vault-a"
    extracted = vault / ".watchdog" / "extracted"
    extracted.mkdir(parents=True)
    (extracted / "abc.json").write_text(json.dumps({"document": {"note": "Revenue $1,234,567"}}))

    monkeypatch_keys = sa.KEYS
    try:
        sa.KEYS = sorted(keys_dir.glob("*.yaml"))
        sa.main([str(vault)])  # must not raise
    finally:
        sa.KEYS = monkeypatch_keys

    out = capsys.readouterr().out
    assert "0/0  (n/a)" in out


# ── backend + auth reporting (#475) ────────────────────────────────────────────

def _usage(backend="claude-api", auth_mode="api-key", cost=1.0):
    return {"calls": [{"cost_usd": cost, "latency_s": 20, "attempts": 1, "task": "extract",
                       "backend": backend, "auth_mode": auth_mode}]}


def test_extractor_table_names_backend_and_auth():
    """A bare `sonnet` arm resolves to claude-agent-sdk or claude-api by auth mode alone, and the
    two bill materially different input tokens. The arm row has to say which actually ran, or two
    runs of "the same" arm are silently incomparable."""
    results = [_arm_result(arm_id="sonnet-med", vault="/tmp/bench-ex3-sonnet-med",
                          usage=_usage("claude-agent-sdk", "subscription"))]
    table = br.extractor_table_md(results, {"totals": {"facts": {}, "must_not_miss": {}}})
    assert "Backend" in table
    assert "claude-agent-sdk (subscription)" in table


def test_extractor_table_omits_auth_for_non_claude_backends():
    results = [_arm_result(arm_id="ds-flash", vault="/tmp/bench-ex3-ds-flash",
                          usage=_usage("deepseek", "api-key"))]
    table = br.extractor_table_md(results, {"totals": {"facts": {}, "must_not_miss": {}}})
    assert "| deepseek |" in table
    assert "deepseek (api-key)" not in table


def test_subscription_arm_costs_are_marked_notional():
    """On subscription auth nothing is billed per token, so `cost_usd` is a list-price
    projection. It gets a `~` and a caveat, so it isn't read as spend next to a metered arm."""
    results = [_arm_result(arm_id="sonnet-med", vault="/tmp/bench-ex3-sonnet-med",
                          usage=_usage("claude-agent-sdk", "subscription", cost=3.0))]
    table = br.extractor_table_md(results, {"totals": {"facts": {}, "must_not_miss": {}}})
    assert "~$3.000" in table
    note = "\n".join(br._notional_note(results))
    assert "sonnet-med" in note and "not amounts billed" in note


def test_metered_arm_costs_are_not_marked_notional():
    results = [_arm_result(arm_id="sonnet-api", vault="/tmp/bench-ex3-sonnet-api",
                          usage=_usage("claude-api", "api-key", cost=3.0))]
    table = br.extractor_table_md(results, {"totals": {"facts": {}, "must_not_miss": {}}})
    assert "$3.000" in table and "~$3.000" not in table
    assert br._notional_note(results) == []


def test_docs_summary_warns_when_figures_are_notional():
    """The docs fragment is hand-pasted into a page journalists read, where a dollar figure is
    taken at face value — the caveat has to travel with it, not just live in the tech report."""
    results = [_arm_result(arm_id="sonnet-med", vault="/tmp/v",
                          usage=_usage("claude-agent-sdk", "subscription"))]
    summary = br.docs_summary_md(results, {"totals": {"facts": {}, "must_not_miss": {}}})
    assert "not a charge that was incurred" in summary

    metered = [_arm_result(arm_id="ds", vault="/tmp/v", usage=_usage("deepseek", "api-key"))]
    assert "not a charge that was incurred" not in br.docs_summary_md(
        metered, {"totals": {"facts": {}, "must_not_miss": {}}})


# ── --arms selection (#475) ────────────────────────────────────────────────────

def _matrix_config(tmp_path):
    """A minimal config with a two-arm extractor sweep, written to disk for main()."""
    cfg = {
        "corpus": {"dir": "corpus", "sha256": "corpus/c.sha256"},
        "keys": {"dir": "keys", "sha256": "keys/k.sha256"},
        "master_vault": {"name": "m", "classify_name": "mc"},
        "extractor_sweep": {"vault_prefix": "bench-ex3", "arms": [
            {"id": "sonnet-med-sdk", "extractor_model": "claude-agent-sdk:sonnet"},
            {"id": "sonnet-med-api", "extractor_model": "claude-api:sonnet"},
            {"id": "haiku", "extractor_model": "haiku"},
        ]},
    }
    p = tmp_path / "benchmark.yaml"
    p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return p


def test_arm_backend_resolves_bare_tier_by_auth_mode(monkeypatch):
    """A bare `sonnet` has no backend of its own — auth mode picks it. That is the variable that
    made two runs of one arm incomparable, so the preview has to resolve and show it."""
    import watchdog.cmd.auth as wd_auth
    monkeypatch.setattr(wd_auth, "resolve_auth", lambda *a, **k: {"mode": "subscription"})
    assert rb.arm_backend("sonnet", default="sonnet") == "claude-agent-sdk"
    monkeypatch.setattr(wd_auth, "resolve_auth", lambda *a, **k: {"mode": "api-key", "key": "k"})
    assert rb.arm_backend("sonnet", default="sonnet") == "claude-api"


def test_arm_backend_honours_an_explicit_pin_regardless_of_auth_mode(monkeypatch):
    import watchdog.cmd.auth as wd_auth
    monkeypatch.setattr(wd_auth, "resolve_auth", lambda *a, **k: {"mode": "subscription"})
    assert rb.arm_backend("claude-api:sonnet", default="sonnet") == "claude-api"
    assert rb.arm_backend("deepseek:deepseek-v4-flash", default="sonnet") == "deepseek"


def test_confirm_run_prints_backend_and_auth_per_arm(monkeypatch, capsys):
    monkeypatch.setattr(wd_interactive, "confirm", lambda *a, **k: False)
    previews = [("extractor:sonnet-med-sdk", {"cost_low": 1.0, "cost_high": 2.0},
                 {"backend": "claude-agent-sdk", "auth_mode": "subscription"}),
                ("extractor:ds-flash", {"cost_low": 0.01, "cost_high": 0.02},
                 {"backend": "deepseek", "auth_mode": "api-key"})]
    rb.confirm_run(previews, estimate_only=True)
    out = capsys.readouterr().out
    assert "backend: claude-agent-sdk (subscription)" in out
    assert "backend: deepseek" in out
    assert "deepseek (api-key)" not in out   # auth only matters where it chose the backend


def test_arms_filter_runs_only_the_named_arms(tmp_path, monkeypatch, capsys):
    """A full sweep is ~$15; running one comparison should not require paying for the rest."""
    cfg = _matrix_config(tmp_path)
    monkeypatch.setattr(rb, "verify_freeze", lambda *a, **k: None)
    monkeypatch.setattr(rb, "corpus_documents", lambda d: [Path("a.pdf")])
    monkeypatch.setattr(rb, "ensure_master_vault", lambda *a, **k: tmp_path / "master")
    monkeypatch.setattr(rb, "seed_arm_vault", lambda *a, **k: None)
    monkeypatch.setattr(rb, "arm_vault", lambda prefix, aid, root: tmp_path / f"{prefix}-{aid}")
    monkeypatch.setattr(rb, "preview_extractor_arm", lambda v, m: {"cost_low": 1.0, "cost_high": 2.0})
    monkeypatch.setattr(rb, "arm_backend", lambda m, default: "claude-api")
    monkeypatch.setattr(rb, "_auth_mode", lambda: "api-key")
    monkeypatch.setattr(wd_interactive, "confirm", lambda *a, **k: False)

    rb.main(["--config", str(cfg), "--stages", "extractor",
             "--arms", "sonnet-med-sdk,sonnet-med-api"])

    out = capsys.readouterr().out
    assert "extractor:sonnet-med-sdk" in out
    assert "extractor:sonnet-med-api" in out
    assert "extractor:haiku" not in out


def test_arms_filter_rejects_an_unknown_arm_id(tmp_path, monkeypatch):
    """A typo'd arm id must not silently fall through to running the whole sweep."""
    cfg = _matrix_config(tmp_path)
    monkeypatch.setattr(rb, "verify_freeze", lambda *a, **k: None)
    monkeypatch.setattr(rb, "corpus_documents", lambda d: [Path("a.pdf")])
    with pytest.raises(SystemExit, match="unknown arm id"):
        rb.main(["--config", str(cfg), "--stages", "extractor", "--arms", "sonnet-med-sdkk"])
