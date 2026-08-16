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
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

BENCHMARKS_DIR = Path(__file__).resolve().parent.parent / "benchmarks"
if str(BENCHMARKS_DIR) not in sys.path:
    sys.path.insert(0, str(BENCHMARKS_DIR))

import bench_report as br  # noqa: E402
import cost_reference as cr  # noqa: E402
import run_benchmark as rb  # noqa: E402
import score_arms as sa  # noqa: E402
import verifier_precision as vp  # noqa: E402

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


def test_load_config_accepts_well_formed_sdk_check(tmp_path):
    config = rb.load_config(_write_config(tmp_path, sdk_check={
        "vault_prefix": "bench-sdkcheck", "corpus_dir": "sdk-check-corpus",
        "arms": [{"id": "sonnet-med-sdk-sub", "extractor_model": "claude-agent-sdk:sonnet"}],
    }))
    assert config["sdk_check"]["arms"][0]["id"] == "sonnet-med-sdk-sub"


def test_load_config_rejects_sdk_check_duplicate_arm_id(tmp_path):
    path = _write_config(tmp_path, sdk_check={
        "vault_prefix": "bench-sdkcheck", "corpus_dir": "sdk-check-corpus",
        "arms": [{"id": "a", "extractor_model": "claude-agent-sdk:sonnet"},
                {"id": "a", "extractor_model": "claude-api:sonnet"}],
    })
    with pytest.raises(SystemExit):
        rb.load_config(path)


def test_load_config_rejects_sdk_check_missing_model_field(tmp_path):
    path = _write_config(tmp_path, sdk_check={
        "vault_prefix": "bench-sdkcheck", "corpus_dir": "sdk-check-corpus",
        "arms": [{"id": "sonnet-med-sdk-sub"}],
    })
    with pytest.raises(SystemExit):
        rb.load_config(path)


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


# ── pre-flight vault staleness gate (#494) ──────────────────────────────────────

def test_stale_reason_none_for_absent_vault(tmp_path):
    assert rb._stale_reason(tmp_path / "nowhere") is None


def test_stale_reason_none_for_freshly_created_empty_vault(tmp_path):
    vault = tmp_path / "vault"
    (vault / ".watchdog").mkdir(parents=True)
    assert rb._stale_reason(vault) is None


def test_stale_reason_flags_queued_batch(tmp_path):
    vault = tmp_path / "vault"
    (vault / ".watchdog" / "queue").mkdir(parents=True)
    (vault / ".watchdog" / "queue" / "abc.json").write_text("{}")
    assert "queued" in rb._stale_reason(vault)


def test_stale_reason_flags_pending_finalization(tmp_path):
    vault = tmp_path / "vault"
    (vault / ".watchdog" / "tmp").mkdir(parents=True)
    (vault / ".watchdog" / "tmp" / "result_abc.json").write_text("{}")
    assert "pending finalization" in rb._stale_reason(vault)


def test_stale_reason_flags_pending_batch(tmp_path):
    vault = tmp_path / "vault"
    (vault / ".watchdog" / "registry").mkdir(parents=True)
    (vault / ".watchdog" / "registry" / "batch-pending.json").write_text("{}")
    assert "Batches API" in rb._stale_reason(vault)


def test_stale_reason_flags_already_extracted_documents(tmp_path):
    vault = tmp_path / "vault"
    (vault / ".watchdog" / "extracted").mkdir(parents=True)
    (vault / ".watchdog" / "extracted" / "abc.json").write_text("{}")
    assert "already has extracted" in rb._stale_reason(vault)


def test_planned_arm_vaults_covers_every_selected_stage(tmp_path):
    config = {
        "extractor_sweep": {"vault_prefix": "bench-ex", "arms": [{"id": "a"}, {"id": "b"}]},
        "finalizer_sweep": {"vault_prefix": "bench-fn", "arms": [{"id": "c"}]},
        "sdk_check": {"vault_prefix": "bench-sc", "arms": [{"id": "d"}]},
    }
    stages = {"extractor", "finalizer", "sdk-check"}
    vaults = rb.planned_arm_vaults(config, tmp_path, stages, tmp_path, lambda a: True)
    assert set(vaults) == {
        tmp_path / "bench-ex-a", tmp_path / "bench-ex-b",
        tmp_path / "bench-fn-base", tmp_path / "bench-fn-c",
        tmp_path / "bench-sc-d",
    }


def test_planned_arm_vaults_respects_arms_filter_and_unselected_stages(tmp_path):
    config = {
        "extractor_sweep": {"vault_prefix": "bench-ex", "arms": [{"id": "a"}, {"id": "b"}]},
        "finalizer_sweep": {"vault_prefix": "bench-fn", "arms": [{"id": "c"}]},
    }
    vaults = rb.planned_arm_vaults(config, tmp_path, {"extractor"}, tmp_path,
                                   lambda a: a["id"] == "a")
    assert vaults == [tmp_path / "bench-ex-a"]   # b excluded, finalizer stage not requested


def test_main_lists_a_stale_vault_before_the_prompt_and_resets_it_after(tmp_path, monkeypatch,
                                                                        capsys):
    """#494 made a stale vault a hard refusal, which was safe but meant hand-running `rm -rf`
    before every re-run. It is reset automatically now — but only after the operator confirms,
    and only after being named in the same breath as the cost, because a recursive delete must
    never be a surprise consequence of agreeing to spend money."""
    cfg = _matrix_config(tmp_path)   # sonnet-med-sdk, sonnet-med-api, haiku
    monkeypatch.setattr(rb, "verify_freeze", lambda *a, **k: None)
    monkeypatch.setattr(rb, "corpus_documents", lambda d: [Path("a.pdf")])
    root = tmp_path / ".vaults"
    stale_vault = root / "bench-ex3-haiku"
    (stale_vault / ".watchdog" / "queue").mkdir(parents=True)
    (stale_vault / ".watchdog" / "queue" / "abc.json").write_text("{}")
    monkeypatch.setattr(rb, "arm_vault", lambda prefix, aid, r: root / f"{prefix}-{aid}")
    monkeypatch.setattr(rb, "vault_root", lambda *a, **k: root)
    monkeypatch.setattr(rb, "ensure_master_vault", lambda *a, **k: tmp_path / "master")
    monkeypatch.setattr(rb, "seed_arm_vault", lambda *a, **k: None)
    monkeypatch.setattr(rb, "preview_extractor_arm", lambda *a, **k: {"cost_low": 0.0,
                                                                     "cost_high": 0.0})
    monkeypatch.setattr(wd_interactive, "confirm", lambda *a, **k: False)   # decline

    rb.main(["--config", str(cfg), "--stages", "extractor"])

    out = capsys.readouterr().out
    assert "will be RESET" in out
    assert "bench-ex3-haiku" in out
    # Declined, so nothing was deleted — the reset is on the far side of the confirmation.
    assert stale_vault.exists()


def test_main_reseeds_a_vault_it_reset(tmp_path, monkeypatch, capsys):
    """A reset vault must be put back before its arm runs.

    Seeding happens in the plan phase (previewing an arm needs its queue) but `reset_vaults`
    runs after the go-ahead, so a *stale* vault was skipped by the seed (it existed), deleted by
    the reset, and never re-created — the arm then ran against a path that was gone. Because
    `--estimate-only` also seeds, the documented estimate-then-run workflow hit this every time.
    """
    cfg = _matrix_config(tmp_path)   # sonnet-med-sdk, sonnet-med-api, haiku
    monkeypatch.setattr(rb, "verify_freeze", lambda *a, **k: None)
    monkeypatch.setattr(rb, "corpus_documents", lambda d: [Path("a.pdf")])
    root = tmp_path / ".vaults"
    stale_vault = root / "bench-ex3-haiku"
    (stale_vault / ".watchdog" / "queue").mkdir(parents=True)
    (stale_vault / ".watchdog" / "queue" / "abc.json").write_text("{}")

    seeded: list[Path] = []

    def fake_seed(master, dest):
        seeded.append(dest)
        (dest / ".watchdog" / "queue").mkdir(parents=True, exist_ok=True)
        return 1

    monkeypatch.setattr(rb, "arm_vault", lambda prefix, aid, r: root / f"{prefix}-{aid}")
    monkeypatch.setattr(rb, "vault_root", lambda *a, **k: root)
    monkeypatch.setattr(rb, "ensure_master_vault", lambda *a, **k: tmp_path / "master")
    monkeypatch.setattr(rb, "seed_arm_vault", fake_seed)
    monkeypatch.setattr(rb, "preview_extractor_arm", lambda *a, **k: {"cost_low": 0.0,
                                                                     "cost_high": 0.0})
    monkeypatch.setattr(wd_interactive, "confirm", lambda *a, **k: True)   # accept
    monkeypatch.setattr(rb, "run_extractor_arm",
                        lambda arm, vault: rb.ArmResult(arm_id=arm["id"], stage="extractor",
                                                        vault=vault, ok=True))
    import bench_report
    monkeypatch.setattr(bench_report, "write_run", lambda *a, **k: tmp_path / "run")

    rb.main(["--config", str(cfg), "--stages", "extractor"])

    # It was deleted (so the arm started clean) *and* put back (so the arm had a vault at all).
    assert stale_vault.exists(), "reset vault was never re-seeded — the arm would run on nothing"
    assert stale_vault in seeded


def test_main_does_not_reset_anything_on_an_estimate_only_run(tmp_path, monkeypatch):
    """`--estimate-only` is the free, always-safe path; it must stay side-effect free."""
    cfg = _matrix_config(tmp_path)
    monkeypatch.setattr(rb, "verify_freeze", lambda *a, **k: None)
    monkeypatch.setattr(rb, "corpus_documents", lambda d: [Path("a.pdf")])
    root = tmp_path / ".vaults"
    stale_vault = root / "bench-ex3-haiku"
    (stale_vault / ".watchdog" / "extracted").mkdir(parents=True)
    (stale_vault / ".watchdog" / "extracted" / "a.json").write_text("{}")
    monkeypatch.setattr(rb, "arm_vault", lambda prefix, aid, r: root / f"{prefix}-{aid}")
    monkeypatch.setattr(rb, "vault_root", lambda *a, **k: root)
    monkeypatch.setattr(rb, "ensure_master_vault", lambda *a, **k: tmp_path / "master")
    monkeypatch.setattr(rb, "seed_arm_vault", lambda *a, **k: None)
    monkeypatch.setattr(rb, "preview_extractor_arm", lambda *a, **k: {"cost_low": 0.0,
                                                                     "cost_high": 0.0})

    rb.main(["--config", str(cfg), "--stages", "extractor", "--estimate-only"])
    assert stale_vault.exists()


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
    monkeypatch.setattr(rb, "_deregister_obsidian_vault",
                        lambda path: captured.setdefault("obsidian_deregistered", path))
    monkeypatch.setattr(wd_ingest, "_run_preprocess", lambda *a, **k: None)

    shadow_root = tmp_path / "shadow"
    vault = rb.ensure_master_vault("bench-master-test", [], with_sidecars=True, root=shadow_root)

    assert captured["dir"] == str(shadow_root)
    assert vault == shadow_root / "bench-master-test"
    assert captured["deregistered"] == "bench-master-test"
    assert captured["obsidian_deregistered"] == vault


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


# ── _deregister_obsidian_vault: the Obsidian-side half of undoing cmd_new ─────
#
# cmd_new registers a vault in TWO places — projects.json (above) and Obsidian's own
# obsidian.json, the vault switcher in the app itself. A user's real obsidian.json can end up
# almost entirely bench-* fixtures if only the projects.json side gets deregistered.

def test_deregister_obsidian_vault_removes_only_its_own_path(monkeypatch, tmp_path):
    """Obsidian's `vaults` dict is keyed by an opaque random id, not the vault's path — matching
    has to be on the `path` field inside each entry, never the key."""
    import watchdog.cmd.vault as wd_vault
    cfg = tmp_path / "obsidian.json"
    monkeypatch.setattr(wd_vault, "_obsidian_config_path", lambda: cfg)
    bench_path = tmp_path / "bench-master"
    real_path = tmp_path / "toms-real-investigation"
    cfg.write_text(json.dumps({"vaults": {
        "abc123": {"path": str(bench_path), "ts": 1},
        "def456": {"path": str(real_path), "ts": 2},
    }}))

    rb._deregister_obsidian_vault(bench_path)

    data = json.loads(cfg.read_text())
    paths = {v["path"] for v in data["vaults"].values()}
    assert str(bench_path) not in paths
    assert str(real_path) in paths


def test_deregister_obsidian_vault_is_a_noop_when_config_missing(monkeypatch, tmp_path):
    import watchdog.cmd.vault as wd_vault
    monkeypatch.setattr(wd_vault, "_obsidian_config_path", lambda: tmp_path / "does-not-exist.json")
    rb._deregister_obsidian_vault(tmp_path / "bench-master")  # must not raise


def test_deregister_obsidian_vault_swallows_malformed_config(monkeypatch, tmp_path):
    """Mirrors _register_obsidian_vault's own non-fatal posture (see that function) — a missing,
    unreadable, or corrupt obsidian.json, or Obsidian not being installed at all, must never fail
    a benchmark run."""
    import watchdog.cmd.vault as wd_vault
    cfg = tmp_path / "obsidian.json"
    cfg.write_text("not valid json{")
    monkeypatch.setattr(wd_vault, "_obsidian_config_path", lambda: cfg)
    rb._deregister_obsidian_vault(tmp_path / "bench-master")  # must not raise


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


# ── _arm_line: the terse per-arm terminal output ───────────────────────────────

def test_arm_line_ok():
    text = rb._arm_line(1, 16, "extractor:haiku", _arm_result(), 74.0)
    assert "1/16" in text
    assert "extractor:haiku" in text
    assert "✓" in text
    assert "1m14s" in text


def test_arm_line_hard_failure_points_to_errors_log():
    text = rb._arm_line(2, 16, "extractor:sonnet-low", _arm_result(ok=False, error="boom"), 3.0)
    assert "✗" in text
    assert "errors.log" in text


def test_arm_line_per_document_failures_not_masked_by_ok_true():
    """The exact real-world case: cmd_extract returns ok=True with everything failed inside."""
    text = rb._arm_line(3, 16, "extractor:sonnet-med",
                        _arm_result(ok=True, doc_errors=["a: x", "b: y"]), 3.0)
    assert "✗" in text
    assert "2 failed" in text
    assert "errors.log" in text


def test_arm_line_cancelled():
    text = rb._arm_line(4, 16, "extractor:haiku", _arm_result(cancelled=True), 12.0)
    assert "interrupted" in text
    assert "✓" not in text and "✗" not in text


def test_arm_line_rate_limited_is_distinguishable_from_plain_interrupted():
    """#559: a rate limit used to print identically to a Ctrl-C (`interrupted`, no further
    detail) — the one distinction that mattered most, since a rate limit means the harness
    itself decided to stop for a reason worth seeing, not the operator stopping it."""
    rate_limited = rb._arm_line(1, 6, "extractor:gpt-luna-low-verify",
                                _arm_result(cancelled=True, rate_limited=True,
                                           documents_done=2, documents_total=6), 252.0)
    interrupted = rb._arm_line(2, 6, "extractor:haiku",
                               _arm_result(cancelled=True, documents_done=3,
                                          documents_total=6), 72.0)
    assert rate_limited != interrupted
    assert "rate-limited" in rate_limited and "⚠" in rate_limited
    assert "errors.log" in rate_limited
    assert "2/6" in rate_limited
    assert "interrupted" in interrupted and "3/6" in interrupted
    assert "rate-limited" not in interrupted and "⚠" not in interrupted
    assert "errors.log" not in interrupted


def test_arm_starting_line_shares_index_and_label_with_the_completion_line():
    """Printed before a (possibly minutes-long) arm begins, so there is something on screen
    while it's running — without it, a real extraction call leaves the terminal silent for as
    long as the call takes, indistinguishable from a hang."""
    text = rb._arm_starting_line(5, 16, "extractor:sonnet-high")
    assert "5/16" in text
    assert "extractor:sonnet-high" in text
    assert "running" in text


# ── resilience: try/except SystemExit per arm ──────────────────────────────────

def test_run_extractor_arm_records_failure_without_raising(monkeypatch, tmp_path):
    def _fails(ns, **kw):
        raise SystemExit("boom")
    monkeypatch.setattr(wd_ingest, "cmd_extract", _fails)
    result = rb.run_extractor_arm({"id": "haiku", "extractor_model": "haiku"}, tmp_path)
    assert result.ok is False
    assert result.error == "boom"
    assert result.arm_id == "haiku"
    assert result.stage == "extractor"


def test_run_extractor_arm_ok_reads_usage(monkeypatch, tmp_path):
    monkeypatch.setattr(wd_ingest, "cmd_extract", lambda ns, **kw: None)
    usage_dir = tmp_path / ".watchdog" / "registry" / "usage"
    usage_dir.mkdir(parents=True)
    (usage_dir / "usage-1.json").write_text(json.dumps({"calls": [{"cost_usd": 1.5}]}))
    result = rb.run_extractor_arm({"id": "haiku", "extractor_model": "haiku"}, tmp_path)
    assert result.ok is True
    assert result.usage["calls"][0]["cost_usd"] == 1.5
    # #551: the `<ts>` stem is the join key back to telemetry_db's `calls.run_id` (D193) — it
    # must round-trip from the usage file's own name, not be derived some other way.
    assert result.usage_run_id == "1"


def test_run_extractor_arm_usage_run_id_none_without_a_usage_file(monkeypatch, tmp_path):
    """A vault with no usage file at all (nothing extracted, or a hard failure before the first
    write) has no telemetry to join to — `None`, not a guessed or empty-string id."""
    monkeypatch.setattr(wd_ingest, "cmd_extract", lambda ns, **kw: None)
    result = rb.run_extractor_arm({"id": "haiku", "extractor_model": "haiku"}, tmp_path)
    assert result.ok is True
    assert result.usage is None
    assert result.usage_run_id is None


def test_run_extractor_arm_calls_cmd_extract_non_interactively(monkeypatch, tmp_path):
    """run_benchmark.py has no human to answer a prompt — cmd_extract must always be called with
    non_interactive=True from here, not left to its interactive default (#494)."""
    captured = {}

    def _capture(ns, **kw):
        captured.update(kw)
        return {"cancelled": False, "results": []}

    monkeypatch.setattr(wd_ingest, "cmd_extract", _capture)
    rb.run_extractor_arm({"id": "haiku", "extractor_model": "haiku"}, tmp_path)
    assert captured.get("non_interactive") is True


def test_run_extractor_arm_tags_ns_with_benchmark_arm_id(monkeypatch, tmp_path):
    """#611: every arm's telemetry should be identifiable as a benchmark run, and as this arm
    specifically — `cmd_extract` reads `ns.benchmark_arm_id` and threads it down to the global
    telemetry store."""
    captured = {}
    monkeypatch.setattr(wd_ingest, "cmd_extract",
                        lambda ns, **kw: captured.update(benchmark_arm_id=ns.benchmark_arm_id) or
                        {"cancelled": False, "results": []})
    rb.run_extractor_arm({"id": "sonnet-high", "extractor_model": "sonnet"}, tmp_path)
    assert captured["benchmark_arm_id"] == "sonnet-high"


@pytest.mark.parametrize("arm, expected", [
    ({"id": "a", "extractor_model": "haiku", "verify": True}, True),
    ({"id": "a", "extractor_model": "haiku", "verify": False}, False),
    ({"id": "a", "extractor_model": "haiku"}, False),
])
def test_run_extractor_arm_pins_the_verification_pass_per_arm(monkeypatch, tmp_path, arm, expected):
    """#535: an arm must never inherit `verify_extraction` from the machine running the sweep —
    the pass changes both the arm's spend and its recall, so an unset arm means off, explicitly."""
    captured = {}
    monkeypatch.setattr(wd_ingest, "cmd_extract",
                        lambda ns, **kw: captured.update(verify=ns.verify) or
                        {"cancelled": False, "results": []})
    rb.run_extractor_arm(arm, tmp_path)
    assert captured["verify"] is expected


def test_run_extractor_arm_chdirs_into_the_target_vault(monkeypatch, tmp_path):
    """`cmd_extract` (via `cmd_ingest`) resolves its vault from the current working directory
    only — no explicit-path argument exists (`ingest.py`'s `Path(".").resolve()`). Without a
    chdir into the arm's vault first, every real run fails identically with 'must be run from
    inside a Watchdog vault directory', regardless of which arm or vault — this was live and
    unnoticed until the first real (non-estimate) run of the tool."""
    seen_cwd = {}

    def _capture(ns, **kw):
        seen_cwd["cwd"] = Path.cwd()

    monkeypatch.setattr(wd_ingest, "cmd_extract", _capture)
    outside = Path.cwd()
    rb.run_extractor_arm({"id": "haiku", "extractor_model": "haiku"}, tmp_path)
    assert seen_cwd["cwd"] == tmp_path.resolve()
    assert Path.cwd() == outside   # restored afterward, not left inside the vault


def test_run_extractor_arm_restores_cwd_even_on_failure(monkeypatch, tmp_path):
    def _fails(ns, **kw):
        raise SystemExit("boom")
    monkeypatch.setattr(wd_ingest, "cmd_extract", _fails)
    outside = Path.cwd()
    rb.run_extractor_arm({"id": "haiku", "extractor_model": "haiku"}, tmp_path)
    assert Path.cwd() == outside


def test_run_extractor_arm_suppresses_cmd_extract_stdout(monkeypatch, tmp_path, capsys):
    """The runner is terse by design (#benchmark-ux) — the underlying pipeline's own verbose
    per-document output must not leak to the terminal during a real run."""
    def _noisy(ns, **kw):
        print("Sending 6 documents to a cloud AI model.")
        return {"cancelled": False, "results": []}
    monkeypatch.setattr(wd_ingest, "cmd_extract", _noisy)
    rb.run_extractor_arm({"id": "haiku", "extractor_model": "haiku"}, tmp_path)
    assert "Sending 6 documents" not in capsys.readouterr().out


def test_run_extractor_arm_picks_up_cancelled_from_summary(monkeypatch, tmp_path):
    """cmd_extract traps ctrl+c internally and returns normally with `cancelled: True` — no
    exception to catch, so this has to come from the return value now that cmd_ingest exposes it."""
    monkeypatch.setattr(wd_ingest, "cmd_extract", lambda ns, **kw: {"cancelled": True, "results": []})
    result = rb.run_extractor_arm({"id": "haiku", "extractor_model": "haiku"}, tmp_path)
    assert result.ok is True
    assert result.cancelled is True


def test_run_extractor_arm_captures_per_document_failures(monkeypatch, tmp_path):
    """A per-document failure (e.g. the file_metadata schema bug) is caught inside cmd_extract
    and tallied in `results`, never raised — `ok` alone would miss it entirely."""
    summary = {"cancelled": False, "results": [
        {"sha256": "a1", "filename": "doc1.pdf", "status": "failed", "reason": "400: bad schema"},
        {"sha256": "a2", "filename": "doc2.pdf", "status": "ok"},
    ]}
    monkeypatch.setattr(wd_ingest, "cmd_extract", lambda ns, **kw: summary)
    result = rb.run_extractor_arm({"id": "haiku", "extractor_model": "haiku"}, tmp_path)
    assert result.ok is True
    assert result.doc_errors == ["doc1.pdf: 400: bad schema"]


def test_run_extractor_arm_reports_rate_limited_and_document_counts(monkeypatch, tmp_path):
    """#559: `orchestrate._extract` already computes `rate_limited`/`stop_message` — this is the
    read that used to be dropped entirely (only `cancelled` reached the old ArmResult). The
    corpus size comes from the queue, counted before `cmd_extract` runs (a real run consumes/
    moves queue files as it goes, so counting after would undercount)."""
    queue = tmp_path / ".watchdog" / "queue"
    queue.mkdir(parents=True)
    for i in range(6):
        (queue / f"doc{i}.json").write_text("{}")

    summary = {"cancelled": True, "rate_limited": True, "stop_message": "rate_limit_error",
              "extracted": 2, "skipped": 0, "failed": 0, "wait_count": 2, "results": []}
    monkeypatch.setattr(wd_ingest, "cmd_extract", lambda ns, **kw: summary)

    result = rb.run_extractor_arm({"id": "gpt-luna-low-verify",
                                   "extractor_model": "openai:gpt-5.6-luna"}, tmp_path)

    assert result.ok is True
    assert result.rate_limited is True
    assert result.stop_message == "rate_limit_error"
    assert result.wait_count == 2
    assert result.documents_done == 2
    assert result.documents_total == 6


def test_run_extractor_arm_passes_concurrency_and_wait_bound_through(monkeypatch, tmp_path):
    """`concurrency`/`max_rate_limit_waits` come from the arm dict (merged from
    `extractor_sweep.defaults` by `main()`, #559) and `wait=True` is the default so a rate limit
    gets a bounded chance to resume instead of stopping the arm cold."""
    captured = {}

    def _capture(ns, **kw):
        captured["wait"] = ns.wait
        captured["max_rate_limit_waits"] = ns.max_rate_limit_waits
        captured["concurrency"] = ns.concurrency
        return {"cancelled": False, "results": []}
    monkeypatch.setattr(wd_ingest, "cmd_extract", _capture)

    rb.run_extractor_arm({"id": "gpt-mini-low-verify", "extractor_model": "openai:gpt-5.4-mini",
                          "concurrency": 3, "max_rate_limit_waits": 4}, tmp_path)

    assert captured == {"wait": True, "max_rate_limit_waits": 4, "concurrency": 3}


def test_run_extractor_arm_never_waits_on_a_batch_backend(monkeypatch, tmp_path):
    """A batch backend already refuses `--wait` outright (`cmd_ingest`'s own guard) — its results
    come back hours later in a separate process, so there is nothing to wait *for* here. Passing
    `wait=True` anyway would just sys.exit the arm."""
    captured = {}

    def _capture(ns, **kw):
        captured["wait"] = ns.wait
        return {"cancelled": False, "results": []}
    monkeypatch.setattr(wd_ingest, "cmd_extract", _capture)

    rb.run_extractor_arm({"id": "batch-sonnet-med", "extractor_model": "claude-batch:sonnet"},
                         tmp_path)

    assert captured["wait"] is False


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


def test_run_finalizer_arm_tags_ns_with_benchmark_arm_id(monkeypatch, tmp_path):
    captured = {}
    def _capture(ns):
        captured["benchmark_arm_id"] = ns.benchmark_arm_id
    monkeypatch.setattr(wd_ingest, "cmd_finalize", _capture)

    rb.run_finalizer_arm({"id": "haiku", "finalizer_model": "haiku"}, tmp_path)
    assert captured["benchmark_arm_id"] == "haiku"


def test_run_finalizer_arm_records_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(wd_ingest, "cmd_finalize",
                        lambda ns: (_ for _ in ()).throw(SystemExit("locked")))
    result = rb.run_finalizer_arm({"id": "haiku", "finalizer_model": "haiku"}, tmp_path)
    assert result.ok is False
    assert result.error == "locked"


def test_run_finalizer_arm_captures_non_raised_failure(monkeypatch, tmp_path):
    """A rate limit or reconciliation error during finalize is returned, not raised (same
    silent-ok=True trap as extraction's per-document failures) — _run_finalize's own `out.get
    ("error")` check, mirrored here so the terse runner can flag it too."""
    monkeypatch.setattr(wd_ingest, "cmd_finalize",
                        lambda ns: {"error": "rate limit reached"})
    result = rb.run_finalizer_arm({"id": "haiku", "finalizer_model": "haiku"}, tmp_path)
    assert result.ok is True
    assert result.doc_errors == ["rate limit reached"]


def test_run_finalizer_arm_suppresses_cmd_finalize_stdout(monkeypatch, tmp_path, capsys):
    def _noisy(ns):
        print("Finalizing — entity reconciliation + synthesis + timeline + briefing.")
        return {}
    monkeypatch.setattr(wd_ingest, "cmd_finalize", _noisy)
    rb.run_finalizer_arm({"id": "haiku", "finalizer_model": "haiku"}, tmp_path)
    assert "Finalizing" not in capsys.readouterr().out


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


def test_is_partial_true_for_rate_limited_arm():
    r = _arm_result(rate_limited=True, cancelled=True, documents_done=2, documents_total=6)
    assert br.is_partial(r) is True


def test_is_partial_true_for_per_document_failures_alone(tmp_path):
    """#559: an arm can fall short of the whole corpus purely from per-document failures — no
    rate limit, no Ctrl-C — and that must be treated as partial too, not just the rate-limited
    case. `is_partial` has no `reason` input at all; it only compares document counts."""
    r = _arm_result(ok=True, documents_done=4, documents_total=6,
                    doc_errors=["doc5.pdf: 400 bad schema", "doc6.pdf: 400 bad schema"])
    assert br.is_partial(r) is True


def test_is_partial_false_for_a_complete_arm():
    assert br.is_partial(_arm_result(documents_done=6, documents_total=6)) is False
    assert br.is_partial(_arm_result()) is False    # no document-count info at all -> not partial


def test_is_partial_false_for_non_extractor_stages():
    """The partial concept only applies to the extractor sweep's whole-corpus recall figure —
    finalizer/classifier arms have no comparable per-corpus denominator."""
    r = _arm_result(stage="finalizer", rate_limited=True, documents_done=1, documents_total=2)
    assert br.is_partial(r) is False


def test_extractor_table_never_shows_a_bare_percentage_for_a_partial_arm():
    """#559: a partial arm's recall cell must carry the partial caveat every time, whether the
    cause was a rate limit or plain per-document failures — never a bare percentage that reads
    as directly comparable to a complete arm's."""
    results = [
        _arm_result(arm_id="gpt-luna-low-verify", vault="/tmp/bench-ex-gpt-luna-low-verify",
                    rate_limited=True, cancelled=True, documents_done=2, documents_total=6,
                    usage={"calls": []}),
        _arm_result(arm_id="sonnet-med", vault="/tmp/bench-ex-sonnet-med",
                    documents_done=4, documents_total=6,
                    doc_errors=["doc5.pdf: x", "doc6.pdf: x"], usage={"calls": []}),
    ]
    scores = {"totals": {
        "facts": {"bench-ex-gpt-luna-low-verify": {"hit": 6, "of": 6},
                 "bench-ex-sonnet-med": {"hit": 30, "of": 32}},
        "must_not_miss": {"bench-ex-gpt-luna-low-verify": {"hit": 1, "of": 3},
                          "bench-ex-sonnet-med": {"hit": 14, "of": 18}},
    }}
    table = br.extractor_table_md(results, scores)
    assert "100% (6/6) — partial, 2/6 docs" in table
    assert "33% (1/3) — partial, 2/6 docs" in table
    assert "94% (30/32) — partial, 4/6 docs" in table
    # A bare "100% (6/6)" with no trailing caveat would also match "100% (6/6) — partial…" as a
    # substring, so check there's no line where the percentage cell is followed straight by the
    # table's own next `|` delimiter instead of the " — partial" suffix.
    for line in table.splitlines():
        if "(6/6)" in line or "(30/32)" in line:
            assert "partial" in line


def test_sdk_check_table_renders_failed_arm():
    results = [_arm_result(arm_id="sonnet-med-sdk-sub", stage="sdk-check", ok=False,
                          error="rate limited")]
    table = br.sdk_check_table_md(results)
    assert "failed: rate limited" in table


def test_sdk_check_table_renders_usage():
    results = [_arm_result(arm_id="sonnet-med-sdk-sub", stage="sdk-check",
                          usage={"calls": [{"cost_usd": 0.5, "latency_s": 10,
                                           "backend": "claude-agent-sdk",
                                           "auth_mode": "subscription"}]})]
    table = br.sdk_check_table_md(results)
    assert "claude-agent-sdk (subscription)" in table
    assert "~$0.500" in table   # subscription auth -> notional cost, marked with ~


def test_sdk_check_table_ignores_other_stages():
    results = [_arm_result(arm_id="haiku", stage="extractor")]
    table = br.sdk_check_table_md(results)
    assert "haiku" not in table


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


# ── run_id: minute precision so multiple sessions in a day don't collide ───────

def test_run_id_includes_time_not_just_date():
    from datetime import datetime
    rid = br.run_id(datetime(2026, 7, 29, 14, 32))
    assert rid == "2026-07-29-1432"


def test_run_id_still_dedupes_within_the_same_minute():
    from datetime import datetime
    now = datetime(2026, 7, 29, 14, 32)
    first = br.run_id(now)
    second = br.run_id(now, existing={first})
    assert second == f"{first}-2"


# ── write_run: errors.log ───────────────────────────────────────────────────────

def test_write_run_writes_errors_log_for_a_hard_failure(tmp_path):
    results = [_arm_result(arm_id="sonnet-low", ok=False, error="auth not configured")]
    scores = {"totals": {"facts": {}, "must_not_miss": {}}, "detail": [], "unscorable": [],
             "vaults": []}
    config = {"corpus": {"sha256": "c"}, "keys": {"sha256": "k"}}
    run_dir = br.write_run(tmp_path / "benchmarks", results, scores, config)
    log = (run_dir / "errors.log").read_text()
    assert "extractor:sonnet-low" in log
    assert "auth not configured" in log
    assert "errors.log" in (run_dir / "REPORT.md").read_text()


def test_write_run_writes_errors_log_for_per_document_failures(tmp_path):
    """The exact real-world case: ok=True at the arm level, but every document inside failed —
    REPORT.md's tables alone would show this arm looking indistinguishable from a clean run."""
    results = [_arm_result(arm_id="sonnet-med", ok=True,
                          doc_errors=["doc1.pdf: 400 bad schema", "doc2.pdf: 400 bad schema"])]
    scores = {"totals": {"facts": {}, "must_not_miss": {}}, "detail": [], "unscorable": [],
             "vaults": []}
    config = {"corpus": {"sha256": "c"}, "keys": {"sha256": "k"}}
    run_dir = br.write_run(tmp_path / "benchmarks", results, scores, config)
    log = (run_dir / "errors.log").read_text()
    assert "doc1.pdf: 400 bad schema" in log
    assert "doc2.pdf: 400 bad schema" in log
    assert "2 document(s) failed" in (run_dir / "REPORT.md").read_text()


def test_write_run_omits_errors_log_when_nothing_failed(tmp_path):
    results = [_arm_result(arm_id="haiku", ok=True)]
    scores = {"totals": {"facts": {}, "must_not_miss": {}}, "detail": [], "unscorable": [],
             "vaults": []}
    config = {"corpus": {"sha256": "c"}, "keys": {"sha256": "k"}}
    run_dir = br.write_run(tmp_path / "benchmarks", results, scores, config)
    assert not (run_dir / "errors.log").exists()


def test_write_run_writes_errors_log_for_a_rate_limited_arm(tmp_path):
    """The regression #559 mainly targets: a rate limit's in-flight documents get status
    `cancelled`, which `doc_errors` correctly excludes as not-a-failure, so a rate-limited arm had
    neither `error` nor `doc_errors` and — before this fix — wrote nothing to errors.log at all,
    even though the arm plainly didn't complete."""
    results = [_arm_result(arm_id="gpt-luna-low-verify", ok=True, cancelled=True,
                          rate_limited=True, stop_message="rate_limit_error",
                          documents_done=2, documents_total=6)]
    scores = {"totals": {"facts": {}, "must_not_miss": {}}, "detail": [], "unscorable": [],
             "vaults": []}
    config = {"corpus": {"sha256": "c"}, "keys": {"sha256": "k"}}
    run_dir = br.write_run(tmp_path / "benchmarks", results, scores, config)
    log = (run_dir / "errors.log").read_text()
    assert "extractor:gpt-luna-low-verify" in log
    assert "rate-limited" in log
    assert "rate_limit_error" in log
    assert "2/6" in log
    report = (run_dir / "REPORT.md").read_text()
    assert "Failed or incomplete arms" in report
    assert "rate-limited after 2/6 docs" in report


def test_write_run_errors_log_reports_both_rate_limit_and_doc_errors_for_one_arm(tmp_path):
    """The old `_errors_log_text` was an if/elif — an arm could only ever produce one kind of
    block. A rate-limited arm that also had per-document failures on the documents it did reach
    must report both (#559)."""
    results = [_arm_result(arm_id="gpt-luna-low-verify", ok=True, cancelled=True,
                          rate_limited=True, stop_message="rate_limit_error",
                          documents_done=2, documents_total=6,
                          doc_errors=["doc1.pdf: 400 bad schema"])]
    scores = {"totals": {"facts": {}, "must_not_miss": {}}, "detail": [], "unscorable": [],
             "vaults": []}
    config = {"corpus": {"sha256": "c"}, "keys": {"sha256": "k"}}
    run_dir = br.write_run(tmp_path / "benchmarks", results, scores, config)
    log = (run_dir / "errors.log").read_text()
    assert "rate-limited" in log
    assert "doc1.pdf: 400 bad schema" in log


def test_write_run_lists_a_partial_arm_under_failed_or_incomplete_even_without_doc_errors(tmp_path):
    """A partial arm (rate-limited or short a document with no doc_errors recorded) must still
    show up in the "Failed or incomplete arms" section — `ok=True` and empty `doc_errors` used to
    make it invisible there."""
    results = [_arm_result(arm_id="gpt-luna-low-verify", ok=True, cancelled=True,
                          rate_limited=True, documents_done=2, documents_total=6)]
    scores = {"totals": {"facts": {}, "must_not_miss": {}}, "detail": [], "unscorable": [],
             "vaults": []}
    config = {"corpus": {"sha256": "c"}, "keys": {"sha256": "k"}}
    run_dir = br.write_run(tmp_path / "benchmarks", results, scores, config)
    report = (run_dir / "REPORT.md").read_text()
    assert "gpt-luna-low-verify" in report
    assert "not a quality signal" in report


def test_write_run_lists_a_rate_limited_non_extractor_arm_too(tmp_path):
    """`is_partial` is deliberately gated to the extractor stage (only it has a recall cell for
    `_partial_suffix` to annotate), but a rate limit is a real stop regardless of stage. Before
    this fix, a rate-limited `sdk-check`/`classifier` arm got an `errors.log` block — written
    unconditionally on `r.rate_limited` — with no corresponding line in "Failed or incomplete
    arms", so REPORT.md's own "Full detail in `errors.log`" pointer named a file that never
    mentioned the arm at all."""
    results = [_arm_result(arm_id="sonnet-med-sdk", stage="sdk-check", ok=True,
                          rate_limited=True, stop_message="rate_limit_error",
                          documents_done=1, documents_total=2)]
    assert br.is_partial(results[0]) is False   # confirms the stage gate is intentionally kept
    scores = {"totals": {"facts": {}, "must_not_miss": {}}, "detail": [], "unscorable": [],
             "vaults": []}
    config = {"corpus": {"sha256": "c"}, "keys": {"sha256": "k"}}
    run_dir = br.write_run(tmp_path / "benchmarks", results, scores, config)
    assert "rate-limited" in (run_dir / "errors.log").read_text()
    report = (run_dir / "REPORT.md").read_text()
    assert "Failed or incomplete arms" in report
    assert "sonnet-med-sdk" in report


def test_run_json_carries_partial_flag_and_document_counts():
    results = [_arm_result(arm_id="gpt-luna-low-verify", ok=True, cancelled=True,
                          rate_limited=True, stop_message="rate_limit_error",
                          documents_done=2, documents_total=6)]
    scores = {"totals": {"facts": {}, "must_not_miss": {}}}
    config = {"corpus": {"sha256": "c"}, "keys": {"sha256": "k"}}
    data = br.run_json("2026-01-01-0000", results, scores, config, {"commit": None})
    arm = data["arms"][0]
    assert arm["partial"] is True
    assert arm["rate_limited"] is True
    assert arm["stop_message"] == "rate_limit_error"
    assert arm["documents_extracted"] == 2
    assert arm["documents_total"] == 6


def test_run_json_partial_false_and_document_counts_none_for_a_complete_arm():
    results = [_arm_result(arm_id="gpt-luna-med", ok=True)]
    scores = {"totals": {"facts": {}, "must_not_miss": {}}}
    config = {"corpus": {"sha256": "c"}, "keys": {"sha256": "k"}}
    data = br.run_json("2026-01-01-0000", results, scores, config, {"commit": None})
    arm = data["arms"][0]
    assert arm["partial"] is False
    assert arm["rate_limited"] is False
    assert arm["documents_extracted"] is None
    assert arm["documents_total"] is None


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


# ── score_arms.py restricted-denominator scoring (#559) ────────────────────────

def _write_key_with_document(tmp_path, name, sha256, facts, must_not_miss=None):
    """Like `_write_key`, but with a `document.sha256` — the field the restricted-denominator
    filter matches against `.watchdog/extracted/<sha>.json`."""
    if must_not_miss is None:
        must_not_miss = []
    keys_dir = tmp_path / "keys"
    keys_dir.mkdir(exist_ok=True)
    (keys_dir / f"{name}.yaml").write_text(yaml.safe_dump({
        "document": {"file": f"{name}.pdf", "sha256": sha256},
        "facts": facts, "must_not_miss": must_not_miss,
    }))
    return keys_dir


def test_score_skips_a_key_item_for_a_vault_that_never_extracted_its_document(tmp_path):
    """The core fix (#559): a vault that never staged an extraction for a key's document must not
    be charged a miss for that document's items — gated on `.watchdog/extracted/<sha>.json`
    presence, an exact match against the key's own `document.sha256`, not on any cancelled/
    rate_limited flag the caller passes in (there is no such input to `score()` at all)."""
    keys_dir = _write_key_with_document(
        tmp_path, "doc1", "aaa111",
        [{"id": "F1", "fact": "Revenue was $1,234,567 in 2024."}])

    complete = tmp_path / "vault-complete"
    (complete / ".watchdog" / "extracted").mkdir(parents=True)
    (complete / ".watchdog" / "extracted" / "aaa111.json").write_text(
        json.dumps({"document": {"note": "Revenue $1,234,567"}}))

    partial = tmp_path / "vault-partial"
    (partial / ".watchdog" / "extracted").mkdir(parents=True)
    # Never extracted aaa111 — only some other, unrelated document.
    (partial / ".watchdog" / "extracted" / "bbb222.json").write_text(
        json.dumps({"document": {"note": "unrelated"}}))

    result = sa.score([str(complete), str(partial)], keys_dir=keys_dir)

    assert result["totals"]["facts"]["vault-complete"] == {"hit": 1, "of": 1}
    # The denominator falls to 0, not "1 of 1 missed" — the item was never a fair test for a
    # vault that never opened the document it's about.
    assert result["totals"]["facts"]["vault-partial"] == {"hit": 0, "of": 0}
    cell = next(d for d in result["detail"] if d["qid"] == "doc1:F1")["cells"]["vault-partial"]
    assert cell["hit"] == "not_extracted"
    # The complete vault could still score it, so it must not be reported as unscorable — that
    # label is reserved for items with no numeric anchor at all, a different kind of "can't score."
    assert result["unscorable"] == []


def test_score_complete_vaults_totals_are_unchanged_by_a_partial_sibling(tmp_path):
    """Scoring a complete vault alongside a partial one must not change the complete vault's own
    numbers — the restricted-denominator filter is per-vault, never a side effect on the others
    in the same call."""
    keys_dir = _write_key_with_document(
        tmp_path, "doc1", "aaa111",
        [{"id": "F1", "fact": "Revenue was $1,234,567 in 2024."}],
        must_not_miss=[{"id": "M1", "item": "a buried liability of $9,876,543."}])

    complete = tmp_path / "vault-complete"
    (complete / ".watchdog" / "extracted").mkdir(parents=True)
    (complete / ".watchdog" / "extracted" / "aaa111.json").write_text(json.dumps(
        {"document": {"note": "Revenue $1,234,567 and a liability of $9,876,543"}}))

    partial = tmp_path / "vault-partial"
    (partial / ".watchdog" / "extracted").mkdir(parents=True)

    alone = sa.score([str(complete)], keys_dir=keys_dir)
    with_partial = sa.score([str(complete), str(partial)], keys_dir=keys_dir)

    assert alone["totals"]["facts"]["vault-complete"] == \
        with_partial["totals"]["facts"]["vault-complete"] == {"hit": 1, "of": 1}
    assert alone["totals"]["must_not_miss"]["vault-complete"] == \
        with_partial["totals"]["must_not_miss"]["vault-complete"] == {"hit": 1, "of": 1}


def test_score_a_key_with_no_document_sha256_is_never_filtered(tmp_path):
    """Backward compatibility: a key without `document.sha256` (older or synthetic fixtures, like
    `_write_key` above) has nothing to match against, so it must score exactly as it always did —
    never silently excluded from every vault's denominator."""
    keys_dir = _write_key(tmp_path, "doc1",
                          [{"id": "F1", "fact": "Revenue was $1,234,567 in 2024."}],
                          must_not_miss=[])
    vault = tmp_path / "vault-a"
    extracted = vault / ".watchdog" / "extracted"
    extracted.mkdir(parents=True)
    (extracted / "some-other-sha.json").write_text(
        json.dumps({"document": {"note": "Revenue $1,234,567"}}))

    result = sa.score([str(vault)], keys_dir=keys_dir)

    assert result["totals"]["facts"]["vault-a"] == {"hit": 1, "of": 1}


def test_score_a_fully_filtered_item_lands_in_detail_not_unscorable(tmp_path):
    """A key item whose document *no* vault in this call extracted has a numeric anchor — it's
    simply never been attempted — so it must land in `detail` (every cell `"not_extracted"`), not
    in `unscorable`, which means "no numeric anchor, needs hand check" and is printed under
    exactly that heading by `main()`. Scoring a single partial vault alone (a real single-arm
    run, e.g. one where the only arm that ran was rate-limited) must report the *same* detail/
    unscorable counts as scoring a vault that extracted everything — not a large chunk of
    genuinely-anchored items relabelled as anchor-less."""
    keys_dir = _write_key_with_document(
        tmp_path, "doc1", "aaa111",
        [{"id": "F1", "fact": "Revenue was $1,234,567 in 2024."}],
        must_not_miss=[{"id": "M1", "item": "a buried liability of $9,876,543."}])

    complete = tmp_path / "vault-complete"
    (complete / ".watchdog" / "extracted").mkdir(parents=True)
    (complete / ".watchdog" / "extracted" / "aaa111.json").write_text(json.dumps(
        {"document": {"note": "Revenue $1,234,567 and a liability of $9,876,543"}}))

    partial = tmp_path / "vault-partial"
    (partial / ".watchdog" / "extracted").mkdir(parents=True)   # never extracted aaa111 at all

    complete_only = sa.score([str(complete)], keys_dir=keys_dir)
    partial_only = sa.score([str(partial)], keys_dir=keys_dir)

    assert len(complete_only["detail"]) == len(partial_only["detail"]) == 2
    assert len(complete_only["unscorable"]) == len(partial_only["unscorable"]) == 0
    fact_cell = next(d for d in partial_only["detail"] if d["qid"] == "doc1:F1")["cells"]["vault-partial"]
    assert fact_cell["hit"] == "not_extracted"
    assert partial_only["totals"]["facts"]["vault-partial"] == {"hit": 0, "of": 0}


# ── score_arms.py per-document scoring + identifier fallback (#591) ────────────

def test_variants_drops_the_bare_integer_collapse_but_keeps_the_decimal_restatement():
    """The old `.rstrip("0").rstrip(".")` chain could strip the decimal point itself, turning a
    5-digit anchor into a 2-character bare integer that matches almost any digit run ("78003" ->
    "78.00" -> "78." -> "78"). The fix must drop that specific collapse while still keeping the
    legitimate one-decimal restatement ("4903" thousand really does appear as "4.9" million) —
    a blanket length floor would have discarded both, since they're the same three-character
    shape ("78003" also legitimately renders as "78.0" via the `.1f` line, which stays)."""
    assert "78" not in sa.variants("78003")
    assert "78.0" in sa.variants("78003")           # the .1f line's output is untouched
    assert "4.9" in sa.variants("4903")              # meaningful restatement, must survive
    assert "1" not in sa.variants("1000")            # 1000/1000 == 1.00 -> would collapse to "1"
    assert "260" not in sa.variants("260000")        # 260000/1000 == 260.00 -> would collapse to "260"


def test_variants_always_includes_the_anchor_itself():
    assert "78003" in sa.variants("78003")


def test_millions_prose_covers_a_whole_dollar_anchor():
    """A plain-dollar anchor ($5,000,000, not a thousands-denominated figure) needs a different
    conversion (/1,000,000, not /1,000) to match prose like "$5 million" — variants() alone
    produces only nonsense ("5000.0") for this case without millions_prose()."""
    assert sa.millions_prose("5000000") == {"5 million", "5.0 million", "5.0m"}


def test_millions_prose_uses_sensible_not_bankers_rounding():
    """1,250,000 -> 1.25 million exactly, and a sensibly-rounded "1.3 million" — not the
    round-half-to-even "1.2" plain float formatting would give."""
    assert sa.millions_prose("1250000") == {"1.25 million", "1.3 million", "1.3m"}


def test_millions_prose_formats_round_tens_without_scientific_notation():
    """A round ten of millions must render as "10 million", not as Decimal.normalize()'s
    "1E+1 million". These are the figures prose states most often ("$50 million"), so an
    exponent here would silently miss the most common large-round-figure phrasing."""
    assert sa.millions_prose("10000000") == {"10 million", "10.0 million", "10.0m"}
    assert sa.millions_prose("50000000") == {"50 million", "50.0 million", "50.0m"}
    assert sa.millions_prose("100000000") == {"100 million", "100.0 million", "100.0m"}
    for anchor in ("10000000", "50000000", "100000000", "2000000000"):
        assert not any("E" in v or "e+" in v for v in sa.millions_prose(anchor))


def test_millions_prose_below_one_million_is_empty():
    assert sa.millions_prose("400000") == set()


def test_millions_prose_never_generates_a_bare_short_token():
    """Every variant keeps "million"/"m" adjacent to the digits — the safety net that keeps this
    from reintroducing the #591 bare-digit collisions. No variant is under 4 characters."""
    for anchor in ("1000000", "5000000", "999999000"):
        for v in sa.millions_prose(anchor):
            assert len(v) >= 4, v


def test_score_item_prose_million_hits_only_when_the_value_actually_matches():
    """Tom's ruling: the benchmark scores on numeric value, not transcription format — "$5
    million" is a hit against a 5,000,000 anchor because it's the same number, and "$4 million"
    is not, because it isn't."""
    text = "The Directors' Charge was capped at $5,000,000."
    hit_blob = sa.norm("The Directors' Charge was capped at $5 million.")
    assert sa.score_item(text, hit_blob)[0] is True

    miss_blob = sa.norm("The Directors' Charge was capped at $4 million.")
    assert sa.score_item(text, miss_blob)[0] is False


def test_is_identifier_anchor_true_for_lso_number():
    text = "D.J. Miller (LSO# 344393P) is lawyer for the Applicant."
    assert sa.is_identifier_anchor("344393", text)


def test_is_identifier_anchor_true_for_trailing_letter_suffix():
    """A digit run immediately followed by a letter with no space ("78003K") is a reference-code
    suffix a plain quantity never has, even without a nearby keyword."""
    assert sa.is_identifier_anchor("78003", "Andrew Hanrahan (78003K)")


def test_is_identifier_anchor_false_for_a_plain_dollar_figure():
    assert not sa.is_identifier_anchor("2000000", "an increase from $2,000,000 to $5,000,000")


def test_name_candidates_finds_a_multiword_proper_name():
    assert "D.J. Miller" in sa.name_candidates("D.J. Miller (LSO# 344393P) is lawyer for the Applicant.")


def test_score_item_identifier_only_anchor_falls_back_to_name_presence():
    """A lawyer whose LSO number was never transcribed still scores a hit if their name and role
    were captured — the substance, not an identifier the extractor had no reason to keep."""
    text = "D.J. Miller (LSO# 344393P) is lawyer for the Applicant."
    blob = sa.norm('{"name": "D.J. Miller", "relationship": "lawyer for Applicant"}')
    hit, hits, total = sa.score_item(text, blob)
    assert hit is True
    assert hits == 0  # the numeric anchor itself never matched — only the name fallback did


def test_score_item_identifier_only_anchor_stays_a_miss_without_the_name():
    text = "D.J. Miller (LSO# 344393P) is lawyer for the Applicant."
    blob = sa.norm('{"note": "no relevant entity captured here"}')
    hit, hits, total = sa.score_item(text, blob)
    assert hit is False


def test_score_item_plain_dollar_figure_gets_no_identifier_fallback():
    """A missing quantity must not be rescued by the identifier fallback — only an item whose
    anchor actually reads as a reference code gets the non-numeric escape hatch. (Not rescued by
    the millions-prose match either — the blob's figure is unrelated to either anchor.)"""
    text = "The Directors' Charge increases from $2,000,000 to $5,000,000."
    blob = sa.norm('{"fact": "The Administration Charge was capped at $400,000."}')
    hit, hits, total = sa.score_item(text, blob)
    assert hit is False


def test_score_does_not_cross_credit_an_anchor_from_a_sibling_document(tmp_path):
    """The core fix: a key item is matched only against its own document's extraction. An anchor
    that appears only in a *different* document in the same vault must not count."""
    keys_dir = _write_key_with_document(
        tmp_path, "doc-a", "sha-a",
        [{"id": "F1", "fact": "Legal fees of $4,903 thousand were recognized."}])
    # A second key, for a different document, whose extraction happens to contain the figure.
    (keys_dir / "doc-b.yaml").write_text(yaml.safe_dump({
        "document": {"file": "doc-b.pdf", "sha256": "sha-b"},
        "facts": [], "must_not_miss": [],
    }))

    vault = tmp_path / "vault"
    extracted = vault / ".watchdog" / "extracted"
    extracted.mkdir(parents=True)
    # doc-a's own extraction never mentions the figure...
    (extracted / "sha-a.json").write_text(json.dumps({"document": {"note": "nothing relevant here"}}))
    # ...but a sibling document's extraction happens to contain "4.9" in an unrelated context.
    (extracted / "sha-b.json").write_text(json.dumps({"document": {"note": "grew 4.9 percent"}}))

    result = sa.score([str(vault)], keys_dir=keys_dir)

    assert result["totals"]["facts"]["vault"] == {"hit": 0, "of": 1}


def test_score_matches_an_anchor_within_its_own_document(tmp_path):
    keys_dir = _write_key_with_document(
        tmp_path, "doc-a", "sha-a",
        [{"id": "F1", "fact": "Legal fees of $4,903 thousand were recognized."}])
    vault = tmp_path / "vault"
    extracted = vault / ".watchdog" / "extracted"
    extracted.mkdir(parents=True)
    (extracted / "sha-a.json").write_text(json.dumps({"document": {"note": "grew 4.9 percent"}}))

    result = sa.score([str(vault)], keys_dir=keys_dir)

    assert result["totals"]["facts"]["vault"] == {"hit": 1, "of": 1}


def test_score_identifier_fallback_end_to_end(tmp_path):
    """Full `score()` path: a counsel-of-record item whose LSO number was never transcribed still
    scores a hit because the lawyer's name and relationship were captured, matching what the
    archived-run rescore found for #591."""
    keys_dir = _write_key_with_document(
        tmp_path, "doc-a", "sha-a", facts=[],
        must_not_miss=[{"id": "M1", "item": "D.J. Miller (LSO# 344393P) is lawyer for the Applicant."}])
    vault = tmp_path / "vault"
    extracted = vault / ".watchdog" / "extracted"
    extracted.mkdir(parents=True)
    (extracted / "sha-a.json").write_text(json.dumps({
        "entities": [{"name": "D.J. Miller", "roles": [{"relationship": "lawyer for Applicant"}]}]}))

    result = sa.score([str(vault)], keys_dir=keys_dir)

    assert result["totals"]["must_not_miss"]["vault"] == {"hit": 1, "of": 1}


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
    monkeypatch.setattr(rb, "preview_extractor_arm", lambda v, m, *a, **k: {"cost_low": 1.0, "cost_high": 2.0})
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


# ── ctrl+c stops the whole run, not just the current arm ───────────────────────

def test_cancelled_arm_stops_the_run_before_the_next_arm(tmp_path, monkeypatch, capsys):
    """cmd_extract traps ctrl+c internally and returns normally (`cancelled: True` in its
    summary) rather than raising — before this fix the outer loop had no way to see that and
    just started the next arm's real API calls, which is why a single ctrl+c wasn't enough."""
    cfg = _matrix_config(tmp_path)   # sonnet-med-sdk, sonnet-med-api, haiku — three arms
    monkeypatch.setattr(rb, "verify_freeze", lambda *a, **k: None)
    monkeypatch.setattr(rb, "corpus_documents", lambda d: [Path("a.pdf")])
    monkeypatch.setattr(rb, "ensure_master_vault", lambda *a, **k: tmp_path / "master")
    monkeypatch.setattr(rb, "seed_arm_vault", lambda *a, **k: None)
    monkeypatch.setattr(rb, "arm_vault", lambda prefix, aid, root: tmp_path / f"{prefix}-{aid}")
    monkeypatch.setattr(rb, "preview_extractor_arm", lambda v, m, *a, **k: {"cost_low": 1.0, "cost_high": 2.0})
    monkeypatch.setattr(rb, "arm_backend", lambda m, default: "claude-api")
    monkeypatch.setattr(rb, "_auth_mode", lambda: "api-key")
    monkeypatch.setattr(wd_interactive, "confirm", lambda *a, **k: True)

    calls = []

    def _fake_run_extractor_arm(arm, vault):
        calls.append(arm["id"])
        cancelled = arm["id"] == "sonnet-med-sdk"   # the first arm run
        return rb.ArmResult(arm_id=arm["id"], stage="extractor", vault=vault, ok=True,
                            cancelled=cancelled)

    monkeypatch.setattr(rb, "run_extractor_arm", _fake_run_extractor_arm)
    monkeypatch.setattr(br, "write_run", lambda *a, **k: tmp_path)

    rb.main(["--config", str(cfg), "--stages", "extractor"])

    assert calls == ["sonnet-med-sdk"]   # sonnet-med-api and haiku never ran
    out = capsys.readouterr().out
    assert "Run stopped — 1 of 3 arm(s) completed." in out
    # Printed before the fake arm ran — proof the "running…" line isn't just a formatter that
    # exists but is never actually called from the loop.
    assert "extractor:sonnet-med-sdk" in out and "running" in out


# ── fixture capture is scoped to a real run, never left on afterward (#352) ────────────────────

def test_fixture_capture_enabled_only_during_the_run(tmp_path, monkeypatch):
    """`fixture_capture.enable` must fire only around a confirmed real run — never during
    --estimate-only, and never left on once main() returns, since a benchmark process could go
    on to do other things in the same interpreter (e.g. a test suite)."""
    from watchdog import fixture_capture as fc
    cfg = _matrix_config(tmp_path)
    monkeypatch.setattr(rb, "verify_freeze", lambda *a, **k: None)
    monkeypatch.setattr(rb, "corpus_documents", lambda d: [Path("a.pdf")])
    monkeypatch.setattr(rb, "ensure_master_vault", lambda *a, **k: tmp_path / "master")
    monkeypatch.setattr(rb, "seed_arm_vault", lambda *a, **k: None)
    monkeypatch.setattr(rb, "arm_vault", lambda prefix, aid, root: tmp_path / f"{prefix}-{aid}")
    monkeypatch.setattr(rb, "preview_extractor_arm", lambda v, m, *a, **k: {"cost_low": 1.0, "cost_high": 2.0})
    monkeypatch.setattr(rb, "arm_backend", lambda m, default: "claude-api")
    monkeypatch.setattr(rb, "_auth_mode", lambda: "api-key")
    # Isolate the capture directory to tmp_path — enable() would otherwise create a real
    # (gitignored, but real) benchmarks/.fixture-capture/ directory in the repo checkout.
    monkeypatch.setattr(rb, "HERE", tmp_path)

    seen_enabled = []

    def _fake_run_extractor_arm(arm, vault):
        seen_enabled.append(fc.enabled())
        return rb.ArmResult(arm_id=arm["id"], stage="extractor", vault=vault, ok=True)

    monkeypatch.setattr(rb, "run_extractor_arm", _fake_run_extractor_arm)
    monkeypatch.setattr(br, "write_run", lambda *a, **k: tmp_path)

    assert not fc.enabled()

    monkeypatch.setattr(wd_interactive, "confirm", lambda *a, **k: False)
    rb.main(["--config", str(cfg), "--stages", "extractor", "--estimate-only"])
    assert seen_enabled == []          # estimate-only never reaches the run loop
    assert not fc.enabled()
    assert not (tmp_path / ".fixture-capture").exists()

    monkeypatch.setattr(wd_interactive, "confirm", lambda *a, **k: True)
    rb.main(["--config", str(cfg), "--stages", "extractor"])

    assert seen_enabled and all(seen_enabled)   # capture was on for every real arm
    assert not fc.enabled()                     # and off again once the run finished
    assert (tmp_path / ".fixture-capture").is_dir()


def test_fixture_capture_disabled_even_if_the_run_crashes(tmp_path, monkeypatch):
    """A crash mid-sweep (#494's existing resilience concern) must not leave capture on for
    whatever runs next in the same process."""
    from watchdog import fixture_capture as fc
    cfg = _matrix_config(tmp_path)
    _stub_common_arm_plumbing(monkeypatch, tmp_path)
    monkeypatch.setattr(rb, "HERE", tmp_path)

    def _boom(arm, vault):
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(rb, "run_extractor_arm", _boom)
    monkeypatch.setattr(br, "write_run", lambda *a, **k: tmp_path)

    rb.main(["--config", str(cfg), "--stages", "extractor"])

    assert not fc.enabled()


# ── the report survives an interrupt or crash mid-sweep, not just the designed cancel (#494) ───

def _stub_common_arm_plumbing(monkeypatch, tmp_path):
    monkeypatch.setattr(rb, "verify_freeze", lambda *a, **k: None)
    monkeypatch.setattr(rb, "corpus_documents", lambda d: [Path("a.pdf")])
    monkeypatch.setattr(rb, "ensure_master_vault", lambda *a, **k: tmp_path / "master")
    monkeypatch.setattr(rb, "seed_arm_vault", lambda *a, **k: None)
    monkeypatch.setattr(rb, "arm_vault", lambda prefix, aid, root: tmp_path / f"{prefix}-{aid}")
    monkeypatch.setattr(rb, "preview_extractor_arm", lambda v, m, *a, **k: {"cost_low": 1.0, "cost_high": 2.0})
    monkeypatch.setattr(rb, "arm_backend", lambda m, default: "claude-api")
    monkeypatch.setattr(rb, "_auth_mode", lambda: "api-key")
    monkeypatch.setattr(wd_interactive, "confirm", lambda *a, **k: True)


def test_keyboard_interrupt_mid_arm_still_writes_a_report(tmp_path, monkeypatch, capsys):
    """A Ctrl+C outside the designed `result.cancelled` path (e.g. one that lands on a blocking
    prompt, per the #494 incident) used to kill run_benchmark.py before bench_report.write_run
    ever ran, losing the report for every arm that had already completed."""
    cfg = _matrix_config(tmp_path)   # sonnet-med-sdk, sonnet-med-api, haiku
    _stub_common_arm_plumbing(monkeypatch, tmp_path)

    calls = []

    def _fake_run_extractor_arm(arm, vault):
        calls.append(arm["id"])
        if arm["id"] == "sonnet-med-api":
            raise KeyboardInterrupt
        return rb.ArmResult(arm_id=arm["id"], stage="extractor", vault=vault, ok=True)

    monkeypatch.setattr(rb, "run_extractor_arm", _fake_run_extractor_arm)
    captured = {}
    monkeypatch.setattr(br, "write_run",
                        lambda out_root, results, scores, config, provenance=None,
                               benchmarks_dir=None: (
                            captured.__setitem__("results", results), tmp_path)[1])

    rc = rb.main(["--config", str(cfg), "--stages", "extractor"])

    assert calls == ["sonnet-med-sdk", "sonnet-med-api"]   # haiku never started
    assert [r.arm_id for r in captured["results"]] == ["sonnet-med-sdk"]   # the completed one
    assert rc == 130
    out = capsys.readouterr().out
    assert "Interrupted" in out
    assert "Report written to" in out


def test_unhandled_exception_mid_arm_still_writes_a_report(tmp_path, monkeypatch, capsys):
    """Same guarantee as the KeyboardInterrupt case, for a genuinely unexpected crash — a bug
    anywhere in the loop must not cost the run its report either."""
    cfg = _matrix_config(tmp_path)
    _stub_common_arm_plumbing(monkeypatch, tmp_path)

    def _fake_run_extractor_arm(arm, vault):
        if arm["id"] == "sonnet-med-sdk":
            return rb.ArmResult(arm_id=arm["id"], stage="extractor", vault=vault, ok=True)
        raise RuntimeError("boom")

    monkeypatch.setattr(rb, "run_extractor_arm", _fake_run_extractor_arm)
    captured = {}
    monkeypatch.setattr(br, "write_run",
                        lambda out_root, results, scores, config, provenance=None,
                               benchmarks_dir=None: (
                            captured.__setitem__("results", results), tmp_path)[1])

    rc = rb.main(["--config", str(cfg), "--stages", "extractor"])

    assert [r.arm_id for r in captured["results"]] == ["sonnet-med-sdk"]
    assert rc == 1
    out = capsys.readouterr().out
    assert "RuntimeError: boom" in out
    assert "Report written to" in out


# ── sdk-check stage (#482 follow-up) ───────────────────────────────────────────

def _sdk_check_config(tmp_path):
    cfg = {
        "corpus": {"dir": "corpus", "sha256": "corpus/c.sha256"},
        "keys": {"dir": "keys", "sha256": "keys/k.sha256"},
        "master_vault": {"name": "m", "classify_name": "mc"},
        "sdk_check": {
            "vault_prefix": "bench-sdkcheck", "corpus_dir": "sdk-check-corpus",
            "arms": [{"id": "sonnet-med-sdk-sub",
                     "extractor_model": "claude-agent-sdk:sonnet",
                     "extractor_effort": "medium"}],
        },
    }
    p = tmp_path / "benchmark.yaml"
    p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return p


def test_sdk_check_is_not_run_by_default(tmp_path, monkeypatch, capsys):
    """Not in the default --stages list — it's meant to be requested explicitly, once auth mode
    has been switched to subscription, not swept in alongside everything else."""
    cfg = _sdk_check_config(tmp_path)
    monkeypatch.setattr(rb, "verify_freeze", lambda *a, **k: None)
    monkeypatch.setattr(rb, "corpus_documents", lambda d: [Path("a.pdf")])

    rb.main(["--config", str(cfg)])   # no --stages: the argparse default

    out = capsys.readouterr().out
    assert "sdk-check" not in out
    assert "Nothing to run" in out


def test_sdk_check_arm_relabeled_and_excluded_from_recall_scoring(tmp_path, monkeypatch):
    """The arm executes via the same run_extractor_arm path as the main sweep (same extraction
    logic), but must come out labelled 'sdk-check', not 'extractor' — otherwise it would be fed
    into vaults_to_score against keys for documents its two-document corpus doesn't contain."""
    cfg = _sdk_check_config(tmp_path)
    monkeypatch.setattr(rb, "verify_freeze", lambda *a, **k: None)
    monkeypatch.setattr(rb, "corpus_documents", lambda d: [Path("a.pdf")])
    monkeypatch.setattr(rb, "ensure_master_vault", lambda *a, **k: tmp_path / "master")
    monkeypatch.setattr(rb, "seed_arm_vault", lambda *a, **k: None)
    monkeypatch.setattr(rb, "arm_vault", lambda prefix, aid, root: tmp_path / f"{prefix}-{aid}")
    monkeypatch.setattr(rb, "preview_extractor_arm", lambda v, m, *a, **k: {"cost_low": None, "cost_high": None})
    monkeypatch.setattr(rb, "arm_backend", lambda m, default: "claude-agent-sdk")
    monkeypatch.setattr(rb, "_auth_mode", lambda: "subscription")
    monkeypatch.setattr(wd_interactive, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(
        rb, "run_extractor_arm",
        lambda arm, vault: rb.ArmResult(arm_id=arm["id"], stage="extractor", vault=str(vault),
                                        ok=True, usage={"calls": []}))

    captured = {}

    def fake_write_run(out_root, results, scores, config, provenance=None, benchmarks_dir=None):
        captured["results"] = results
        captured["scores"] = scores
        return tmp_path / "run-out"

    monkeypatch.setattr(br, "write_run", fake_write_run)

    rc = rb.main(["--config", str(cfg), "--stages", "sdk-check"])

    assert rc == 0
    results = captured["results"]
    assert len(results) == 1
    assert results[0].stage == "sdk-check"
    assert results[0].arm_id == "sonnet-med-sdk-sub"
    # An empty vaults_to_score (no "extractor"-staged result) short-circuits score_vaults —
    # confirms the relabel actually took effect before scoring, not just in the final object.
    assert captured["scores"]["vaults"] == []


# ── cost preview from archived benchmark history, since every arm vault is fresh (#478) ────────

def _write_archived_usage(root: Path, run_id: str, vault_name: str, ts: str, calls: list,
                          cost_usd: float = 1.0, input_tokens: int = 1000) -> Path:
    usage_dir = root / run_id / "artifacts" / vault_name / "usage"
    usage_dir.mkdir(parents=True, exist_ok=True)
    p = usage_dir / f"usage-{ts}.json"
    p.write_text(json.dumps({
        "calls": calls,
        "totals": {"input_tokens": input_tokens, "output_tokens": 0, "cache_read_tokens": 0,
                  "cache_write_tokens": 0, "cost_usd": cost_usd},
    }))
    return p


def _call(model="deepseek-v4-flash", effort=None, backend="deepseek", task="extract"):
    return {"model": model, "effort": effort, "backend": backend, "task": task}


def test_reference_usage_files_matches_model_effort_backend(tmp_path):
    match = _write_archived_usage(tmp_path, "2026-01-01-0000", "bench-ex-ds-flash",
                                  "20260101T000000Z", [_call()])
    _write_archived_usage(tmp_path, "2026-01-01-0000", "bench-ex-ds-flash-think",
                          "20260101T000001Z", [_call(model="deepseek-v4-flash-thinking")])
    _write_archived_usage(tmp_path, "2026-01-01-0000", "bench-ex-sonnet-med",
                          "20260101T000002Z", [_call(model="sonnet", effort="medium", backend="claude-api")])

    found = cr.reference_usage_files(tmp_path, "deepseek-v4-flash", None, "deepseek")

    assert found == [match]


def test_reference_usage_files_respects_effort_and_backend_distinctly(tmp_path):
    """Same model, different effort or backend, must not be treated as the same reference —
    output verbosity swings hard with effort alone (issue #478's follow-up comment), and the
    same model metered through a different backend (e.g. claude-batch's 50% discount) prices
    completely differently."""
    _write_archived_usage(tmp_path, "2026-01-01-0000", "bench-ex-sonnet-low",
                          "20260101T000000Z", [_call(model="sonnet", effort="low", backend="claude-api")])
    match = _write_archived_usage(tmp_path, "2026-01-01-0000", "bench-ex-sonnet-high",
                                  "20260101T000001Z",
                                  [_call(model="sonnet", effort="high", backend="claude-api")])
    _write_archived_usage(tmp_path, "2026-01-01-0000", "bench-batch-sonnet-high",
                          "20260101T000002Z", [_call(model="sonnet", effort="high", backend="claude-batch")])

    found = cr.reference_usage_files(tmp_path, "sonnet", "high", "claude-api")

    assert found == [match]


def test_reference_usage_files_finalize_only_excludes_extraction_calls(tmp_path):
    mixed = [_call(task="extract"), _call(task="reconcile")]
    standalone = [_call(task="reconcile"), _call(task="briefing")]
    _write_archived_usage(tmp_path, "2026-01-01-0000", "bench-fn-mixed", "20260101T000000Z", mixed)
    match = _write_archived_usage(tmp_path, "2026-01-01-0000", "bench-fn-standalone",
                                  "20260101T000001Z", standalone)

    found = cr.reference_usage_files(tmp_path, "deepseek-v4-flash", None, "deepseek",
                                     finalize_only=True)

    assert found == [match]


def test_reference_usage_files_caps_to_most_recent_max_runs(tmp_path):
    for i in range(4):
        _write_archived_usage(tmp_path, "2026-01-01-0000", f"bench-ex-ds-flash-{i}",
                              f"2026010{i+1}T000000Z", [_call()])

    found = cr.reference_usage_files(tmp_path, "deepseek-v4-flash", None, "deepseek", max_runs=2)

    assert len(found) == 2
    assert [p.name for p in found] == ["usage-20260103T000000Z.json", "usage-20260104T000000Z.json"]


def test_reference_usage_files_no_match_returns_empty(tmp_path):
    _write_archived_usage(tmp_path, "2026-01-01-0000", "bench-ex-haiku", "20260101T000000Z",
                          [_call(model="haiku", backend="claude-api")])
    assert cr.reference_usage_files(tmp_path, "deepseek-v4-flash", None, "deepseek") == []


def test_fallback_estimate_prices_against_catalog_scaled_by_effort_ratio():
    """No archived run anywhere yet: price directly against the catalog's own published rate for
    this exact model, scaled by the documented per-effort ratio — not a fabricated dollar figure,
    and clearly flagged (`projected: True`) as a rough placeholder rather than a calibrated one."""
    est = cr.fallback_estimate(1_000_000, "deepseek-v4-flash", "low")
    ratio = cr.DEFAULT_OUTPUT_RATIO_BY_EFFORT["low"]
    expected = 1_000_000 * 0.14e-6 + 1_000_000 * ratio * 0.28e-6
    assert est["cost_low"] == est["cost_high"] == pytest.approx(expected)
    assert est["runs_used"] == 0
    assert est["projected"] is True


def test_fallback_estimate_unknown_model_returns_none():
    assert cr.fallback_estimate(1000, "not-a-real-model-id", "low") is None


def test_confirm_run_labels_projected_estimates_as_rough_projection(monkeypatch, capsys):
    monkeypatch.setattr(wd_interactive, "confirm", lambda *a, **k: False)
    previews = [("extractor:gpt-luna", {"cost_low": 5.0, "cost_high": 5.0, "projected": True}, {}),
                ("extractor:ds-flash", {"cost_low": 0.01, "cost_high": 0.02}, {})]
    rb.confirm_run(previews, estimate_only=True)
    out = capsys.readouterr().out
    assert "extractor:gpt-luna: ~$5.00  (rough projection, no matching run history yet)" in out
    assert "extractor:ds-flash: ~$0.01-0.02" in out
    assert "(includes rough projection(s)" in out


def test_confirm_run_omits_projection_note_when_nothing_is_projected(monkeypatch, capsys):
    monkeypatch.setattr(wd_interactive, "confirm", lambda *a, **k: False)
    previews = [("extractor:ds-flash", {"cost_low": 0.01, "cost_high": 0.02}, {})]
    rb.confirm_run(previews, estimate_only=True)
    out = capsys.readouterr().out
    assert "rough projection" not in out


def test_preview_extractor_arm_borrows_reference_usage_when_vault_is_fresh(tmp_path):
    """The vault passed in has no usage history at all (every benchmark arm vault is fresh by
    design) — the preview must still produce a dollar figure by borrowing a matching archived
    run instead, and must not mark it as a rough projection since it's real measured usage."""
    vault = tmp_path / "vault"
    (vault / ".watchdog" / "queue").mkdir(parents=True)
    (vault / ".watchdog" / "registry").mkdir(parents=True)
    (vault / ".watchdog" / "queue" / "abc.json").write_text(json.dumps({
        "filename": "x.pdf", "pages": [{"page": 1, "markdown": "a" * 4000}],
    }))
    _write_archived_usage(tmp_path, "2026-01-01-0000", "bench-ex-ds-flash", "20260101T000000Z",
                          [_call()], cost_usd=1.5, input_tokens=1000)

    est = rb.preview_extractor_arm(vault, "deepseek:deepseek-v4-flash", None, tmp_path)

    assert est["cost_low"] == est["cost_high"] == 1.5
    assert est["runs_used"] == 1
    assert not est.get("projected")


def test_preview_extractor_arm_falls_back_to_catalog_projection_when_nothing_matches(tmp_path):
    vault = tmp_path / "vault"
    (vault / ".watchdog" / "queue").mkdir(parents=True)
    (vault / ".watchdog" / "registry").mkdir(parents=True)
    (vault / ".watchdog" / "queue" / "abc.json").write_text(json.dumps({
        "filename": "x.pdf", "pages": [{"page": 1, "markdown": "a" * 4000}],
    }))

    est = rb.preview_extractor_arm(vault, "deepseek:deepseek-v4-flash", "low", tmp_path)

    assert est["cost_low"] is not None
    assert est["cost_low"] == est["cost_high"]
    assert est["projected"] is True


def test_reference_usage_files_reads_both_the_runs_dir_and_the_legacy_layout(tmp_path):
    """Runs moved to `benchmarks/runs/<id>/` (#550), but runs archived before that move sit
    directly under the benchmarks root. Both are valid history for the cost preview, and missing
    the legacy ones would degrade silently — the preview would just fall back to its static
    projection with no signal that it had lost every reference it used to have."""
    legacy = _write_archived_usage(tmp_path, "2026-01-01-0000", "bench-ex-a", "20260101T000000Z",
                                   [_call()])
    moved = _write_archived_usage(tmp_path / "runs", "2026-02-02-0000", "bench-ex-b",
                                  "20260202T000000Z", [_call()])
    found = cr.reference_usage_files(tmp_path, "deepseek-v4-flash", None, "deepseek")
    assert set(found) == {legacy, moved}



# ── run provenance and run.json (#550 follow-up) ────────────────────────────────

def test_git_provenance_records_commit_and_dirty_state(tmp_path):
    """A run's figures are only valid against the code that produced them, so the commit is
    recorded — and `dirty` alongside it, because with uncommitted changes the hash does not
    describe what ran."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    (tmp_path / "a.txt").write_text("one")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "first"], cwd=tmp_path, check=True)

    clean = br.git_provenance(tmp_path)
    assert clean["dirty"] is False
    assert len(clean["commit"]) == 40
    assert clean["commit_short"] == clean["commit"][:9]

    (tmp_path / "a.txt").write_text("two")             # uncommitted edit
    assert br.git_provenance(tmp_path)["dirty"] is True


def test_git_provenance_outside_a_repo_records_unknown_not_clean(tmp_path):
    """"Unknown" and "clean" are different claims and only one of them justifies trusting the
    commit — a checkout without git must not report a clean tree by default."""
    prov = br.git_provenance(tmp_path)
    assert prov["commit"] is None
    assert prov["dirty"] is None                       # not False
    assert "unknown" in br._provenance_note(prov)


def test_provenance_note_flags_a_dirty_tree_as_unreproducible():
    note = br._provenance_note({"commit": "a" * 40, "commit_short": "aaaaaaaaa",
                                "branch": "main", "dirty": True})
    assert "uncommitted changes" in note and "not reproducible" in note


def test_write_run_emits_run_json_with_provenance_and_per_arm_metrics(tmp_path):
    """#551's composite score needs the run's numbers as data, not as markdown to re-parse."""
    results = [_arm_result(arm_id="gpt-mini-low", vault=str(tmp_path / "bench-ex-gpt-mini-low"),
                           usage={"calls": [{"cost_usd": 0.5, "latency_s": 10.0,
                                             "task": "extract-section", "attempts": 2,
                                             "backend": "openai"}]})]
    scores = {"totals": {"facts": {"bench-ex-gpt-mini-low": {"hit": 7, "of": 10}},
                        "must_not_miss": {"bench-ex-gpt-mini-low": {"hit": 4, "of": 5}}}}
    config = {"corpus": {"sha256": "corpora/extract/corpus-v1.sha256"},
              "keys": {"sha256": "keys/keys-v1.sha256"}}
    prov = {"commit": "b" * 40, "commit_short": "bbbbbbbbb", "branch": "main", "dirty": False,
            "captured_at": "2026-08-05T00:00:00+00:00"}

    run_dir = br.write_run(tmp_path / "out", results, scores, config, prov)
    data = json.loads((run_dir / "run.json").read_text())

    assert data["provenance"] == prov
    assert data["frozen_refs"]["corpus"] == "corpora/extract/corpus-v1.sha256"
    arm = data["arms"][0]
    assert arm["arm_id"] == "gpt-mini-low"
    assert arm["cost_usd"] == pytest.approx(0.5)
    assert arm["retries"] == 1 and arm["sectioned_calls"] == 1
    assert arm["facts"] == {"hit": 7, "of": 10}
    assert arm["must_not_miss"] == {"hit": 4, "of": 5}
    # The same figures the human report renders — one calculation, two renderings.
    assert "b" * 9 in (run_dir / "REPORT.md").read_text()


def test_run_json_cost_matches_the_report_table(tmp_path):
    """run.json and REPORT.md must never disagree: they read the same helper, and a consumer
    that trusts one over the other would be picking a winner between two truths."""
    results = [_arm_result(arm_id="a", vault=str(tmp_path / "bench-ex-a"),
                           usage={"calls": [{"cost_usd": 1.25, "latency_s": 3.0,
                                             "task": "extract", "backend": "openai"}]})]
    config = {"corpus": {"sha256": "c"}, "keys": {"sha256": "k"}}
    data = br.run_json("rid", results, {}, config, {"commit": None})
    assert data["arms"][0]["cost_usd"] == pytest.approx(
        br._usage_totals(results[0].usage)["cost_usd"])


# ── #551 index instrumentation: model/effort/verify, pages_extracted, coverage_gaps,
#    notional_cost, frozen_refs digests, versions ──────────────────────────────────

def _write_extracted(vault, sha, *, page_count=None, coverage_gap=None):
    d = vault / ".watchdog" / "extracted"
    d.mkdir(parents=True, exist_ok=True)
    doc = {}
    if page_count is not None:
        doc["page_count"] = page_count
    doc["coverage_gap"] = coverage_gap
    (d / f"{sha}.json").write_text(json.dumps({"document": doc}))


def test_pages_extracted_sums_the_arms_own_pages_not_the_corpus_total(tmp_path):
    """The denominator for cost-per-page/speed-per-page must be the arm's own extracted pages —
    a partial arm (fewer documents than the corpus has) must not get its smaller cost divided
    over the full corpus's page count."""
    vault = tmp_path / "bench-ex-a"
    _write_extracted(vault, "aaa", page_count=5)
    _write_extracted(vault, "bbb", page_count=3)   # only 2 of the corpus's, say, 3 documents
    results = [_arm_result(arm_id="a", vault=str(vault))]
    config = {"corpus": {"sha256": "c"}, "keys": {"sha256": "k"}}
    data = br.run_json("rid", results, {}, config, {"commit": None})
    assert data["arms"][0]["pages_extracted"] == 8


def test_pages_extracted_and_coverage_gaps_none_when_directory_absent(tmp_path):
    """A missing measurement and a measured zero are different claims — an arm whose vault was
    never created (or never extracted anything) must report `None`, not `0`."""
    results = [_arm_result(arm_id="a", vault=str(tmp_path / "no-such-vault"))]
    config = {"corpus": {"sha256": "c"}, "keys": {"sha256": "k"}}
    data = br.run_json("rid", results, {}, config, {"commit": None})
    assert data["arms"][0]["pages_extracted"] is None
    assert data["arms"][0]["coverage_gaps"] is None


def test_coverage_gaps_counts_only_non_null_gaps(tmp_path):
    vault = tmp_path / "bench-ex-a"
    _write_extracted(vault, "aaa", page_count=1, coverage_gap={"reason": "ocr fallback"})
    _write_extracted(vault, "bbb", page_count=1, coverage_gap=None)
    _write_extracted(vault, "ccc", page_count=1, coverage_gap={"reason": "figure"})
    results = [_arm_result(arm_id="a", vault=str(vault))]
    config = {"corpus": {"sha256": "c"}, "keys": {"sha256": "k"}}
    data = br.run_json("rid", results, {}, config, {"commit": None})
    assert data["arms"][0]["coverage_gaps"] == 2


def test_model_and_effort_come_from_usage_calls_when_present():
    results = [_arm_result(arm_id="sonnet-5-med", vault=None,
                           usage={"calls": [{"model": "sonnet-5", "effort": "medium",
                                             "cost_usd": 0.1}]})]
    config = {"corpus": {"sha256": "c"}, "keys": {"sha256": "k"},
              "extractor_sweep": {"arms": [{"id": "sonnet-5-med", "extractor_model": "wrong",
                                            "extractor_effort": "wrong"}]}}
    data = br.run_json("rid", results, {}, config, {"commit": None})
    assert data["arms"][0]["model"] == "sonnet-5"
    assert data["arms"][0]["effort"] == "medium"


def test_model_and_effort_fall_back_to_config_when_arm_made_no_calls():
    """A hard failure before the first request leaves an arm with no calls at all — the
    configured model/effort is still worth recording, even though nothing ran."""
    results = [_arm_result(arm_id="sonnet-5-med", vault=None, ok=False, error="boom",
                           usage={"calls": []})]
    config = {"corpus": {"sha256": "c"}, "keys": {"sha256": "k"},
              "extractor_sweep": {"arms": [{"id": "sonnet-5-med", "extractor_model": "sonnet-5",
                                            "extractor_effort": "medium"}]}}
    data = br.run_json("rid", results, {}, config, {"commit": None})
    assert data["arms"][0]["model"] == "sonnet-5"
    assert data["arms"][0]["effort"] == "medium"


def test_effort_is_none_for_a_model_with_no_effort_control():
    """Claude Haiku and DeepSeek have no effort knob — their calls carry `effort: None`
    honestly, and that is real signal, not an absent measurement to fall back away from. The
    config below deliberately supplies a (wrong) `extractor_effort` so a bug that always falls
    back to config, ignoring the calls, would be caught here rather than coincidentally passing."""
    results = [_arm_result(arm_id="haiku", vault=None,
                           usage={"calls": [{"model": "haiku", "effort": None,
                                             "cost_usd": 0.05}]})]
    config = {"corpus": {"sha256": "c"}, "keys": {"sha256": "k"},
              "extractor_sweep": {"arms": [{"id": "haiku", "extractor_model": "haiku",
                                            "extractor_effort": "high"}]}}
    data = br.run_json("rid", results, {}, config, {"commit": None})
    assert data["arms"][0]["effort"] is None


def test_notional_cost_true_for_subscription_claude_arm_false_for_metered():
    sub = _arm_result(arm_id="sonnet-med-sdk", vault=None,
                      usage={"calls": [{"cost_usd": 1.0, "backend": "claude-agent-sdk",
                                        "auth_mode": "subscription"}]})
    metered = _arm_result(arm_id="gpt-mini", vault=None,
                          usage={"calls": [{"cost_usd": 1.0, "backend": "openai"}]})
    config = {"corpus": {"sha256": "c"}, "keys": {"sha256": "k"}}
    data = br.run_json("rid", [sub, metered], {}, config, {"commit": None})
    assert data["arms"][0]["notional_cost"] is True
    assert data["arms"][1]["notional_cost"] is False


def test_verify_reads_arm_config_default_false():
    results = [_arm_result(arm_id="gpt-luna-low-verify", vault=None),
              _arm_result(arm_id="gpt-luna-low", vault=None)]
    config = {"corpus": {"sha256": "c"}, "keys": {"sha256": "k"},
              "extractor_sweep": {"arms": [
                  {"id": "gpt-luna-low-verify", "extractor_model": "x", "verify": True},
                  {"id": "gpt-luna-low", "extractor_model": "x"}]}}
    data = br.run_json("rid", results, {}, config, {"commit": None})
    assert data["arms"][0]["verify"] is True
    assert data["arms"][1]["verify"] is False


def test_frozen_refs_carries_manifest_digests(tmp_path):
    import hashlib
    (tmp_path / "keys").mkdir()
    (tmp_path / "corpora" / "extract").mkdir(parents=True)
    corpus_manifest = tmp_path / "corpora" / "extract" / "corpus-v1.sha256"
    keys_manifest = tmp_path / "keys" / "keys-v1.sha256"
    corpus_manifest.write_text("abc123  file.pdf\n")
    keys_manifest.write_text("def456  key.yaml\n")
    config = {"corpus": {"sha256": "corpora/extract/corpus-v1.sha256"},
              "keys": {"sha256": "keys/keys-v1.sha256"}}
    data = br.run_json("rid", [], {}, config, {"commit": None}, benchmarks_dir=tmp_path)
    assert data["frozen_refs"]["corpus_digest"] == hashlib.sha256(
        corpus_manifest.read_bytes()).hexdigest()
    assert data["frozen_refs"]["keys_digest"] == hashlib.sha256(
        keys_manifest.read_bytes()).hexdigest()
    # Path strings are unchanged — other code and archived runs depend on them exactly.
    assert data["frozen_refs"]["corpus"] == "corpora/extract/corpus-v1.sha256"
    assert data["frozen_refs"]["keys"] == "keys/keys-v1.sha256"


def test_frozen_refs_digest_none_when_manifest_missing(tmp_path):
    config = {"corpus": {"sha256": "corpora/extract/corpus-v1.sha256"},
              "keys": {"sha256": "keys/keys-v1.sha256"}}
    data = br.run_json("rid", [], {}, config, {"commit": None}, benchmarks_dir=tmp_path)
    assert data["frozen_refs"]["corpus_digest"] is None
    assert data["frozen_refs"]["keys_digest"] is None


def test_run_json_carries_usage_run_id():
    """The join key back to telemetry_db's `calls.run_id` (D193) must reach run.json — it is a
    different namespace from run.json's own top-level `run_id` (the benchmark run's id), and
    without this field an archived run's arms can't be matched to their telemetry rows later."""
    results = [_arm_result(arm_id="a", vault=None, usage_run_id="20260806T030702Z")]
    config = {"corpus": {"sha256": "c"}, "keys": {"sha256": "k"}}
    data = br.run_json("rid", results, {}, config, {"commit": None})
    assert data["arms"][0]["usage_run_id"] == "20260806T030702Z"


def test_versions_carries_both_constants():
    import score_arms
    config = {"corpus": {"sha256": "c"}, "keys": {"sha256": "k"}}
    data = br.run_json("rid", [], {}, config, {"commit": None})
    assert data["versions"] == {"scorer": score_arms.SCORER_VERSION,
                               "cost_model": br.COST_MODEL_VERSION}


def test_pages_extracted_skips_a_malformed_artifact_rather_than_failing(tmp_path):
    """A run has already been paid for by the time this renders, so one unparseable artifact must
    cost that arm its own pages, not the whole report. The good siblings still count."""
    vault = tmp_path / "bench-ex-a"
    _write_extracted(vault, "aaa", page_count=5)
    _write_extracted(vault, "bbb", page_count=3)
    (vault / ".watchdog" / "extracted" / "ccc.json").write_text("{ truncated")
    (vault / ".watchdog" / "extracted" / "ddd.json").write_text('["not an object"]')
    results = [_arm_result(arm_id="a", vault=str(vault))]
    config = {"corpus": {"sha256": "c"}, "keys": {"sha256": "k"}}
    data = br.run_json("rid", results, {}, config, {"commit": None})
    assert data["arms"][0]["pages_extracted"] == 8


def test_model_joins_distinct_values_when_an_arm_did_not_run_uniformly():
    """Two models across one arm's calls means the arm didn't run under one setting — the whole
    point of recording this per arm. Collapsing to the first would hide exactly that."""
    results = [_arm_result(arm_id="a", vault=None,
                           usage={"calls": [{"model": "sonnet-5", "effort": "low"},
                                            {"model": "sonnet-5", "effort": "low"},
                                            {"model": "haiku", "effort": "low"}]})]
    config = {"corpus": {"sha256": "c"}, "keys": {"sha256": "k"}}
    data = br.run_json("rid", results, {}, config, {"commit": None})
    assert data["arms"][0]["model"] == "sonnet-5, haiku"
    assert data["arms"][0]["effort"] == "low"


def test_qualitative_aggregate_reproduces_the_committed_pass(tmp_path):
    """The judge protocol is kept tracked so the next pass is comparable to the last. That only
    holds if the aggregator keeps producing the same numbers from the same judgments, so this
    pins it against the one committed pass (2026-07-29).

    Denominators come from the answer keys, not from the judgments that came back: a keyed item
    nobody graded is a miss, not an item that never existed. Counting only what returned shrinks
    the denominator and inflates every percentage — and it means adding documents or key items
    silently fails to raise the bar for later runs.

    Scored against `keys/v1/`, the version this pass was judged under. Item ids belong to a key
    version — #573's de-bundling turned `M6` into `M6.1`/`M6.2`/`M6.3` — so replaying it against
    the live keys matches nothing and reports every keyed item as an ungraded miss. Pinning the
    key version here is what keeps the archived pass reproducible across later key revisions."""
    qual = Path(__file__).resolve().parents[1] / "benchmarks" / "qualitative"
    keys_v1 = Path(__file__).resolve().parents[1] / "benchmarks" / "keys" / "v1"
    expected = json.loads((qual / "summary.json").read_text())["summary"]
    # Run against a copy: aggregate.py writes summary.json/detail_rows.json into the directory it
    # reads, so pointing it at the repo would have the test overwrite the very artifacts it pins.
    work = tmp_path / "pass"
    work.mkdir()
    for f in qual.glob("*.json"):
        shutil.copy(f, work / f.name)
    proc = subprocess.run([sys.executable, str(qual / "aggregate.py"), str(work),
                           f"--keys={keys_v1}"],
                          capture_output=True, text=True, cwd=qual.parents[1])
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    for arm, per_arm in expected.items():
        f, m = per_arm["facts"], per_arm["must_not_miss"]
        assert f"{arm:14} {f['hit']:>8}/{f['total']:<8}" in out, f"{arm} facts drifted:\n{out}"
        assert f"{m['hit']:>7}/{m['total']:<7}" in out, f"{arm} must_not_miss drifted:\n{out}"
    # Every keyed item was graded in this pass, so nothing should be reported as an ungraded miss.
    assert "had no judgment" not in out


def test_quote_text_accepts_both_key_shapes():
    """#573: `facts` carry a single quote string, `must_not_miss` a list of verbatim spans — a
    de-bundled claim is often supported by two adjacent spans, or by several lines of a backsheet.
    Callers only scan the result for numeric anchors, so joining is lossless for them; what matters
    is that a list no longer raises (it used to concatenate str + list and TypeError)."""
    import score_arms
    assert score_arms.quote_text({"quote": "one span"}) == "one span"
    assert score_arms.quote_text({"quote": ["a", "b"]}) == "a b"
    assert score_arms.quote_text({}) == ""
    assert score_arms.quote_text({"quote": None}) == ""


def test_must_not_miss_items_are_anchored_and_singular():
    """#573's invariant on the live keys: every `must_not_miss` entry is one independently
    checkable claim carrying either a verbatim `quote` or `basis: inferred` — never both, never
    neither. Bundled entries used to make an extraction holding two of three claims depend on a
    judge's discretion, which depressed this column across every pass before 2026-08-09.

    Ids must also be unique per document: `aggregate.py` keys judgments by id, so a duplicate
    silently overwrites a verdict rather than failing."""
    import yaml
    keys = Path(__file__).resolve().parents[1] / "benchmarks" / "keys"
    seen_any = False
    for kf in sorted(keys.glob("*.yaml")):        # v1/ is a subdirectory and is deliberately excluded
        items = yaml.safe_load(kf.read_text()).get("must_not_miss") or []
        ids = [it["id"] for it in items]
        assert len(ids) == len(set(ids)), f"{kf.name}: duplicate must_not_miss ids"
        for it in items:
            seen_any = True
            quoted, inferred = bool(it.get("quote")), it.get("basis") == "inferred"
            assert quoted != inferred, f"{kf.name}:{it['id']} must be quoted xor inferred"
            if quoted:
                assert isinstance(it["quote"], list), f"{kf.name}:{it['id']} quote must be a list"
                assert all(isinstance(q, str) and q.strip() for q in it["quote"])
            else:
                assert (it.get("why_inferred") or "").strip(), \
                    f"{kf.name}:{it['id']} inferred without why_inferred"
    assert seen_any, "no must_not_miss items found — key glob is wrong"


# ── vault auto-reset (#494 follow-up) ───────────────────────────────────────────

def _fake_vault(root: Path, name: str) -> Path:
    v = root / name
    (v / ".watchdog" / "extracted").mkdir(parents=True)
    (v / ".watchdog" / "extracted" / "a.json").write_text("{}")
    return v


def test_reset_vaults_clears_stale_vaults_under_the_root(tmp_path):
    """Re-running an arm used to mean hand-running `rm -rf` first. The runner resets the vault
    itself now — after the operator confirms, never before."""
    root = tmp_path / ".vaults"
    v1, v2 = _fake_vault(root, "bench-ex-a"), _fake_vault(root, "bench-ex-b")
    assert rb.reset_vaults([v1, v2], root) == [v1.resolve(), v2.resolve()]
    assert not v1.exists() and not v2.exists()


def test_reset_vaults_refuses_a_path_outside_the_vault_root(tmp_path):
    """The guard that matters: this is a recursive delete of paths built from config, and the
    config could name anything. A target outside the disposable shadow tree raises rather than
    being skipped — config and code disagreeing about what is disposable is a stop condition."""
    root = tmp_path / ".vaults"
    root.mkdir()
    outside = _fake_vault(tmp_path / "real-investigations", "important")
    with pytest.raises(RuntimeError, match="outside the benchmark vault root"):
        rb.reset_vaults([outside], root)
    assert outside.exists()


def test_reset_vaults_refuses_the_root_itself(tmp_path):
    root = tmp_path / ".vaults"
    (root / ".watchdog").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="outside the benchmark vault root"):
        rb.reset_vaults([root], root)
    assert root.exists()


def test_reset_vaults_refuses_a_directory_that_is_not_a_vault(tmp_path):
    """No `.watchdog/` means this isn't a vault, whatever the config called it — deleting it
    would be destroying something the benchmark never created."""
    root = tmp_path / ".vaults"
    stray = root / "notes"
    stray.mkdir(parents=True)
    (stray / "keep.txt").write_text("hand-written")
    with pytest.raises(RuntimeError, match="not a vault"):
        rb.reset_vaults([stray], root)
    assert (stray / "keep.txt").exists()


def test_reset_vaults_refuses_a_symlink(tmp_path):
    """A symlink inside the root can point anywhere; resolving it would smuggle a delete past
    the containment check."""
    root = tmp_path / ".vaults"
    root.mkdir()
    target = _fake_vault(tmp_path / "elsewhere", "real")
    link = root / "bench-ex-link"
    link.symlink_to(target)
    with pytest.raises(RuntimeError):
        rb.reset_vaults([link], root)
    assert target.exists()


def test_reset_vaults_ignores_a_vault_that_does_not_exist(tmp_path):
    root = tmp_path / ".vaults"
    root.mkdir()
    assert rb.reset_vaults([root / "bench-ex-gone"], root) == []


# ── page text survives a vault reset (#554) ────────────────────────────────────

def test_write_run_keeps_the_page_text_the_run_was_extracted_from(tmp_path):
    """Run four arms today, re-run them tomorrow, then try to judge today's run: the vaults are
    gone, and with them the page text a verifier-precision judge grades against. The run keeps
    its own copy so an old run stays judgeable."""
    vault = tmp_path / "bench-ex-a"
    (vault / ".watchdog" / "queue").mkdir(parents=True)
    (vault / ".watchdog" / "queue" / "abc.json").write_text(
        json.dumps({"pages": [{"page": 1, "markdown": "page one text"}]}))
    (vault / ".watchdog" / "extracted").mkdir(parents=True)
    (vault / ".watchdog" / "extracted" / "abc.json").write_text("{}")
    results = [_arm_result(arm_id="a", vault=str(vault))]
    config = {"corpus": {"sha256": "c"}, "keys": {"sha256": "k"}}

    run_dir = br.write_run(tmp_path / "out", results, {}, config, {"commit": None})

    kept = json.loads((run_dir / "pages" / "abc.json").read_text())
    assert kept["pages"][0]["markdown"] == "page one text"

    shutil.rmtree(vault)                               # tomorrow's re-run resets it
    assert (run_dir / "pages" / "abc.json").exists()   # today's run is still judgeable


def test_page_text_is_stored_once_per_run_not_once_per_arm(tmp_path):
    """Every arm in a run extracts the same corpus from the same chew, so the page text is
    identical across them — storing it per arm would multiply it by the arm count for nothing."""
    results = []
    for name in ("bench-ex-a", "bench-ex-b"):
        v = tmp_path / name
        (v / ".watchdog" / "queue").mkdir(parents=True)
        (v / ".watchdog" / "queue" / "abc.json").write_text(json.dumps({"pages": []}))
        results.append(_arm_result(arm_id=name, vault=str(v)))
    config = {"corpus": {"sha256": "c"}, "keys": {"sha256": "k"}}

    run_dir = br.write_run(tmp_path / "out", results, {}, config, {"commit": None})
    assert [p.name for p in sorted((run_dir / "pages").glob("*.json"))] == ["abc.json"]


def test_verifier_precision_reads_a_run_directory(tmp_path):
    """The judge tooling has to be able to read what write_run kept, or keeping it is pointless."""
    run = tmp_path / "2026-01-01-0000"
    arm = run / "artifacts" / "bench-ex-a" / "extracted"
    arm.mkdir(parents=True)
    (run / "pages").mkdir()
    (run / "pages" / "abc.json").write_text(
        json.dumps({"pages": [{"page": 1, "markdown": "grounding text"}]}))

    extracted_dir, pages_dir = vp._source_dirs(str(run))
    assert Path(extracted_dir) == arm
    assert vp.doc_pages(pages_dir, "abc")[0]["text"] == "grounding text"


def test_verifier_precision_still_reads_a_live_vault(tmp_path):
    """An in-flight run should still be judgeable straight from its vault, before it is archived."""
    vault = tmp_path / "bench-ex-a"
    (vault / ".watchdog" / "extracted").mkdir(parents=True)
    (vault / ".watchdog" / "queue").mkdir(parents=True)
    extracted_dir, pages_dir = vp._source_dirs(str(vault))
    assert Path(extracted_dir) == vault / ".watchdog" / "extracted"
    assert Path(pages_dir) == vault / ".watchdog" / "queue"


def test_verifier_precision_picks_an_arm_from_a_multi_arm_run(tmp_path):
    """A run's normal shape for this tool is a verify/noverify pair, so naming the arm is the
    common path — not an escape hatch from an error. Matches the bare arm id as well as the full
    vault directory name."""
    run = tmp_path / "2026-01-01-0000"
    for name in ("bench-ex-gpt-mini-low-verify", "bench-ex-gpt-mini-low-noverify"):
        (run / "artifacts" / name / "extracted").mkdir(parents=True)
    (run / "pages").mkdir()

    extracted, pages = vp._source_dirs(str(run), "gpt-mini-low-verify")
    assert Path(extracted) == run / "artifacts" / "bench-ex-gpt-mini-low-verify" / "extracted"
    assert Path(pages) == run / "pages"
    # The full vault directory name works too.
    assert vp._source_dirs(str(run), "bench-ex-gpt-mini-low-noverify")[0].endswith(
        "bench-ex-gpt-mini-low-noverify/extracted")


def test_verifier_precision_multi_arm_run_without_arm_lists_the_options(tmp_path):
    run = tmp_path / "run"
    for name in ("bench-ex-a", "bench-ex-b"):
        (run / "artifacts" / name / "extracted").mkdir(parents=True)
    with pytest.raises(SystemExit) as e:
        vp._source_dirs(str(run))
    assert "pass --arm" in str(e.value) and "bench-ex-a" in str(e.value)


def test_verifier_precision_rejects_an_unknown_arm(tmp_path):
    run = tmp_path / "run"
    (run / "artifacts" / "bench-ex-a" / "extracted").mkdir(parents=True)
    with pytest.raises(SystemExit, match="No arm matching"):
        vp._source_dirs(str(run), "nope")


def test_verifier_precision_single_arm_run_still_needs_no_arm(tmp_path):
    run = tmp_path / "run"
    (run / "artifacts" / "bench-ex-a" / "extracted").mkdir(parents=True)
    assert vp._source_dirs(str(run))[0].endswith("bench-ex-a/extracted")
