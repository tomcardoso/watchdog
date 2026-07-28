"""Automated benchmark runner (#361 / #215 / #466) — replaces BENCHMARKING.md's hand-run protocol.

Not a pytest module (no ``test_`` prefix). Run via the pipx dev venv, from the repo root:

    ~/.local/pipx/venvs/watchdog-intel/bin/python benchmarks/run_benchmark.py \
        [--config benchmarks/benchmark.yaml] [--estimate-only] \
        [--stages extractor,finalizer,classifier,classifier-sweep] \
        [--arms sonnet-med-sdk,sonnet-med-api]

Drives the real `watchdog` CLI functions in-process (`cmd_extract`, `cmd_finalize`, the same
cost-estimate/usage-file library calls `dig --estimate`/`watchdog usage` use) rather than
shelling out or reimplementing ingest/finalize logic — the same idiom `tests/test_cli.py`
already uses to exercise these functions directly.

Every real (non-`--estimate-only`) run needs your explicit go-ahead — this tool computes and
prints the full cost preview for every arm up front and asks ONE confirmation for the whole
matrix; it never spends money silently and has no flag that bypasses the ask.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import yaml

HERE = Path(__file__).resolve().parent


# ─── config ──────────────────────────────────────────────────────────────────────────────────

def load_config(path: Path) -> dict:
    """Load and validate the arm matrix. Unknown top-level keys are tolerated (forward-compat
    with sections added later, e.g. a CI-trigger section) — only the shape of sections this
    tool actually understands is checked."""
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as e:
        sys.exit(f"Error: can't read config {path}: {e}")
    except yaml.YAMLError as e:
        sys.exit(f"Error: invalid YAML in {path}: {e}")

    for stage_key, arms_key in (("extractor_sweep", "arms"), ("finalizer_sweep", "arms")):
        stage = config.get(stage_key)
        if not stage:
            continue
        seen = set()
        for arm in stage.get(arms_key, []):
            aid = arm.get("id")
            if not aid:
                sys.exit(f"Error: {stage_key} has an arm with no 'id'")
            if aid in seen:
                sys.exit(f"Error: {stage_key} has a duplicate arm id '{aid}'")
            seen.add(aid)
        model_field = "extractor_model" if stage_key == "extractor_sweep" else "finalizer_model"
        for arm in stage.get(arms_key, []):
            if not arm.get(model_field):
                sys.exit(f"Error: {stage_key} arm '{arm.get('id')}' is missing '{model_field}'")

    sweep = config.get("classifier_sweep")
    if sweep:
        seen = set()
        for arm in sweep.get("arms", []):
            aid = arm.get("id")
            if not aid:
                sys.exit("Error: classifier_sweep has an arm with no 'id'")
            if aid in seen:
                sys.exit(f"Error: classifier_sweep has a duplicate arm id '{aid}'")
            seen.add(aid)
            if not arm.get("classifier_model"):
                sys.exit(f"Error: classifier_sweep arm '{aid}' is missing 'classifier_model'")

    return config


def verify_freeze(base_dir: Path, sha256_file: Path) -> None:
    """Python re-implementation of `shasum -a 256 -c` — verifies every file the manifest names,
    relative to `base_dir`, still matches. Exits on the first mismatch or missing file; a benchmark
    run must never proceed on a drifted corpus or key set."""
    if not sha256_file.exists():
        sys.exit(f"Error: freeze manifest not found: {sha256_file}")
    bad = []
    for line in sha256_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            digest, filename = line.split("  ", 1)
        except ValueError:
            sys.exit(f"Error: malformed line in {sha256_file}: {line!r}")
        target = base_dir / filename
        if not target.exists():
            bad.append(f"{filename}: missing")
            continue
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != digest.strip():
            bad.append(f"{filename}: hash mismatch")
    if bad:
        sys.exit("Error: freeze verification failed for " + sha256_file.name + ":\n  "
                 + "\n  ".join(bad))


# ─── vault plumbing ──────────────────────────────────────────────────────────────────────────

def vault_root(config: dict, config_dir: Path, cli_override: Path | None) -> Path:
    """Where benchmark vaults live. This is deliberately NOT the installed watchdog's own
    `_projects_dir()` (the user's real `~/investigations`) — early runs of this tool created
    `bench-*` vaults there and registered them in `~/.watchdog/projects.json`, leaving 12+ stray
    entries in the user's actual project list (#475 follow-up). Benchmark vaults are disposable
    scratch fixtures, not investigations, so they get their own isolated, gitignored tree.
    Priority: `--vault-root` flag, then benchmark.yaml's optional `vault_root:` key (resolved
    relative to the config file, same convention as `corpus.dir`/`keys.dir`), then the default
    `benchmarks/.vaults/`."""
    if cli_override is not None:
        return cli_override.expanduser().resolve()
    configured = config.get("vault_root")
    if configured:
        p = Path(configured).expanduser()
        return (p if p.is_absolute() else config_dir / p).resolve()
    return HERE / ".vaults"


def _quiet(fn, *args, **kwargs):
    """Runs `fn` with stdout captured, for the noisy-but-harmless calls this driver makes into
    the real CLI plumbing (`cmd_new`'s vault-created banner, `_run_preprocess`'s chew progress) —
    reusing the actual functions is what keeps a benchmark vault's layout genuinely identical to
    a real one, but their terminal output is meant for an interactive human, not a cost-preview
    run. On any exception the captured buffer is printed before re-raising, so a real failure is
    never hidden behind a suppressed happy path. Deliberately not a `--quiet` flag on the
    production CLI itself — this is benchmark-harness-only cosmetics."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            return fn(*args, **kwargs)
    except BaseException:
        print(buf.getvalue(), end="")
        raise


def _deregister_benchmark_vault(slug: str) -> None:
    """`cmd_new` always registers the vault it creates in `~/.watchdog/projects.json` — that's
    real `watchdog new` behaviour, and reusing `cmd_new` (rather than hand-rolling the vault
    layout) is what keeps a benchmark vault authentic. But a benchmark vault is scratch space, not
    an investigation, so leaving it registered would be exactly the projects.json pollution this
    shadow root exists to avoid. Undo just that one side effect immediately after `cmd_new`
    returns, removing only the slug just created — never touching any other entry."""
    from watchdog.cmd.base import load_projects, save_projects
    projects = load_projects()
    if slug in projects:
        del projects[slug]
        save_projects(projects)


def corpus_documents(corpus_dir: Path) -> list[Path]:
    """The PDFs in a corpus directory (extension-insensitive — corpus-v1 has one `.PDF`), each
    paired with its `.yml` sidecar if one exists."""
    return sorted(p for p in corpus_dir.iterdir() if p.suffix.lower() == ".pdf")


def _new_vault_args(name: str, root: Path) -> SimpleNamespace:
    return SimpleNamespace(name=name, name_flag=None, description="", dir=str(root))


def ensure_master_vault(name: str, docs: list[Path], *, with_sidecars: bool, root: Path) -> Path:
    """Create-and-chew a master vault once, reuse it on every later run. `with_sidecars=False`
    (the classifier master) drops each `.yml` sidecar so classification actually runs — a pinned
    sidecar skips it entirely (D120)."""
    from watchdog.cmd import vault as vault_cmd
    from watchdog.cmd.ingest import _run_preprocess

    vault = root / name
    queue_dir = vault / ".watchdog" / "queue"
    if vault.exists() and queue_dir.is_dir() and any(queue_dir.glob("*.json")):
        return vault  # already chewed — reused as-is, matching seed_bench.sh's trust

    if not vault.exists():
        print(f"  Setting up master vault {name}…")
        _quiet(vault_cmd.cmd_new, _new_vault_args(name, root))
        _deregister_benchmark_vault(name)

    incoming = vault / "_INCOMING"
    for doc in docs:
        shutil.copy2(doc, incoming / doc.name)
        if with_sidecars:
            sidecar = doc.with_suffix(doc.suffix + ".yml")
            if sidecar.exists():
                shutil.copy2(sidecar, incoming / sidecar.name)

    # _run_preprocess, not cmd_chew: cmd_chew unconditionally offers (and, with skip_warning set,
    # silently runs) a full ingest right after chewing via _offer_ingest — exactly the auto-ingest
    # side effect this driver must avoid, since every arm needs control over its own model knobs.
    n = len(docs)
    label = f"{n} document{'s' if n != 1 else ''}"
    print(f"  Chewing {label}…")
    _quiet(_run_preprocess, vault, confirm=False, show_ingest_hint=False)
    print(f"  ✓ {label} ready")
    return vault


def seed_arm_vault(master: Path, dest: Path) -> int:
    """Generalizes seed_bench.sh: clone a chewed master's staging+queue into a fresh arm vault,
    rewriting the master's absolute path to the dest's inside each copied queue file (queue
    entries reference absolute staging paths). Refuses to reseed an already-seeded dest."""
    dest_queue = dest / ".watchdog" / "queue"
    if dest_queue.is_dir() and any(dest_queue.glob("*.json")):
        sys.exit(f"Error: {dest} is already seeded — refusing to reseed (remove it first if "
                 f"this run should start over).")

    (dest / ".watchdog" / "staging").mkdir(parents=True, exist_ok=True)
    dest_queue.mkdir(parents=True, exist_ok=True)
    shutil.copytree(master / ".watchdog" / "staging", dest / ".watchdog" / "staging",
                    dirs_exist_ok=True)

    master_str, dest_str = str(master), str(dest)
    n = 0
    for qf in (master / ".watchdog" / "queue").glob("*.json"):
        text = qf.read_text(encoding="utf-8").replace(master_str, dest_str)
        (dest_queue / qf.name).write_text(text, encoding="utf-8")
        n += 1
    return n


def arm_vault(prefix: str, arm_id: str, root: Path) -> Path:
    return root / f"{prefix}-{arm_id}"


# ─── arm execution ───────────────────────────────────────────────────────────────────────────

@dataclass
class ArmResult:
    arm_id: str
    stage: str                     # "extractor" | "finalizer" | "classifier" | "classifier-sweep"
    vault: Path | None
    ok: bool
    skipped: bool = False          # true only for classifier-sweep with an empty corpus
    error: str | None = None
    estimate: dict | None = None
    usage: dict | None = None
    extra: dict = field(default_factory=dict)


def _resolve(model_str: str | None, default: str):
    from watchdog.cmd.ingest import _resolve_stage
    return _resolve_stage(model_str, None, default=default)


def _effective_backend(backend: str | None) -> str:
    from watchdog.cmd.auth import resolve_auth
    from watchdog.cmd.ingest import _effective_extract_backend
    auth_mode = resolve_auth()["mode"] if backend is None else None
    return _effective_extract_backend(backend, auth_mode)


def arm_backend(model_str: str, *, default: str) -> str:
    """The backend an arm will actually run on. A bare Claude tier (`sonnet`) carries no backend
    of its own — `_effective_backend` resolves it from the *current auth mode*, which is exactly
    the invisible variable that made two runs of one arm incomparable (#475). Surfacing it in the
    preview means you see, before spending, whether this run is the agent SDK or the raw API."""
    backend, _ = _resolve(model_str, default=default)
    return _effective_backend(backend)


def _auth_mode() -> str:
    from watchdog.cmd.auth import resolve_auth
    return resolve_auth().get("mode", "none")


def preview_extractor_arm(vault: Path, extractor_model: str) -> dict:
    from watchdog.pipeline.ingest_setup import scan_queue, cost_estimate
    queue_files = scan_queue(vault)
    return cost_estimate(vault, queue_files, arm_backend(extractor_model, default="sonnet"))


def preview_finalizer_arm(vault: Path, finalizer_model: str) -> dict:
    from watchdog.pipeline.ingest_setup import finalize_cost_estimate
    return finalize_cost_estimate(vault, arm_backend(finalizer_model, default="haiku"))


def _latest_usage(vault: Path) -> dict | None:
    from watchdog.pipeline.orchestrate import usage_files
    files = usage_files(vault)
    if not files:
        return None
    import json
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def run_extractor_arm(arm: dict, vault: Path) -> ArmResult:
    from watchdog.cmd import ingest as ing
    ns = SimpleNamespace(command="dig", extractor_model=arm["extractor_model"],
                        extractor_effort=arm.get("extractor_effort"), estimate=False,
                        force=False, skip_warning=True, wait=False, no_finalize=True)
    try:
        ing.cmd_extract(ns)
    except SystemExit as e:
        return ArmResult(arm_id=arm["id"], stage="extractor", vault=vault, ok=False, error=str(e))
    return ArmResult(arm_id=arm["id"], stage="extractor", vault=vault, ok=True,
                     usage=_latest_usage(vault))


def run_finalizer_arm(arm: dict, vault: Path) -> ArmResult:
    from watchdog.cmd import ingest as ing
    ns = SimpleNamespace(finalizer_model=arm["finalizer_model"],
                        finalizer_effort=arm.get("finalizer_effort"), estimate=False,
                        skip_briefing=False)
    try:
        ing.cmd_finalize(ns)
    except SystemExit as e:
        return ArmResult(arm_id=arm["id"], stage="finalizer", vault=vault, ok=False, error=str(e))
    return ArmResult(arm_id=arm["id"], stage="finalizer", vault=vault, ok=True,
                     usage=_latest_usage(vault))


def _classify_results(vault: Path, expected: dict[str, str]) -> dict:
    import json
    per_doc = {}
    for f in (vault / ".watchdog" / "extracted").glob("*.json"):
        try:
            doc = json.loads(f.read_text(encoding="utf-8")).get("document", {})
        except (OSError, ValueError):
            continue
        filename = doc.get("filename")
        if not filename or filename not in expected:
            continue
        got = doc.get("record_skill")
        per_doc[filename] = {"expected": expected[filename], "got": got,
                             "ok": got == expected[filename]}
    return per_doc


def run_classifier_smoke(arm: dict, vault: Path, expected: dict[str, str]) -> ArmResult:
    from watchdog.cmd import ingest as ing
    ex_ns = SimpleNamespace(command="dig", extractor_model=arm["extractor_model"],
                            extractor_effort=None, estimate=False, force=False,
                            skip_warning=True, wait=False, no_finalize=True)
    try:
        ing.cmd_extract(ex_ns)
        fn_ns = SimpleNamespace(finalizer_model=arm["finalizer_model"], finalizer_effort=None,
                                estimate=False, skip_briefing=True)
        ing.cmd_finalize(fn_ns)
    except SystemExit as e:
        return ArmResult(arm_id=arm["id"], stage="classifier", vault=vault, ok=False, error=str(e))
    return ArmResult(arm_id=arm["id"], stage="classifier", vault=vault, ok=True,
                     extra={"classification": _classify_results(vault, expected)})


def run_classifier_sweep_arm(arm: dict, vault: Path, fixed: dict, expected: dict[str, str]) -> ArmResult:
    from watchdog.cmd import ingest as ing
    ex_ns = SimpleNamespace(command="dig", extractor_model=fixed["extractor_model"],
                            classifier_model=arm["classifier_model"], extractor_effort=None,
                            estimate=False, force=False, skip_warning=True, wait=False,
                            no_finalize=True)
    try:
        ing.cmd_extract(ex_ns)
        fn_ns = SimpleNamespace(finalizer_model=fixed["finalizer_model"], finalizer_effort=None,
                                estimate=False, skip_briefing=True)
        ing.cmd_finalize(fn_ns)
    except SystemExit as e:
        return ArmResult(arm_id=arm["id"], stage="classifier-sweep", vault=vault, ok=False,
                         error=str(e))
    return ArmResult(arm_id=arm["id"], stage="classifier-sweep", vault=vault, ok=True,
                     extra={"classification": _classify_results(vault, expected)})


def classify_corpus_ready(corpus_dir: Path) -> bool:
    """True iff the classifier-model sweep has a document set to run against — at least one
    PDF and an `expected.yaml` labelling it. See classify-corpus/README.md."""
    if not corpus_dir.is_dir():
        return False
    return bool(corpus_documents(corpus_dir)) and (corpus_dir / "expected.yaml").exists()


# ─── orchestration ───────────────────────────────────────────────────────────────────────────

def _corpus_expected_skills(keys: list[dict]) -> dict[str, str]:
    return {k["document"]["file"]: k["document"]["expected_skill"] for k in keys
           if k.get("document", {}).get("expected_skill")}


def confirm_run(previews: list[tuple[str, dict, dict]], *, estimate_only: bool) -> bool:
    """`previews` is a list of (arm_label, estimate_dict, meta) tuples, already computed (free).
    Prints a per-arm breakdown plus a grand total, then one confirmation for the whole run."""
    from watchdog import interactive
    total_low = total_high = 0.0
    any_priced = False
    print("\nCost preview:")
    for label, est, meta in previews:
        low, high = est.get("cost_low"), est.get("cost_high")
        if low is None:
            print(f"  {label}: no dollar estimate (subscription auth or no usage history yet)")
        else:
            any_priced = True
            total_low += low
            total_high += high
            print(f"  {label}: ~${low:.2f}-{high:.2f}")
        # Which backend this arm resolves to, and on Claude the auth mode that chose it (#475).
        backend = (meta or {}).get("backend")
        if backend:
            auth = (meta or {}).get("auth_mode")
            suffix = f" ({auth})" if auth and backend.startswith("claude-") else ""
            print(f"    backend: {backend}{suffix}")
    if any_priced:
        print(f"  TOTAL: ~${total_low:.2f}-{total_high:.2f}")
    else:
        print("  TOTAL: no dollar estimate for any arm")
    if estimate_only:
        return False
    return interactive.confirm(f"\nRun {len(previews)} arm(s)?", default=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "benchmark.yaml")
    parser.add_argument("--estimate-only", action="store_true")
    parser.add_argument("--stages", default="extractor,finalizer,classifier,classifier-sweep")
    parser.add_argument("--arms", default=None,
                        help="comma-separated arm ids to run (default: every arm in the "
                             "selected stages). Lets one comparison run without paying for the "
                             "whole sweep, e.g. --arms sonnet-med-sdk,sonnet-med-api")
    parser.add_argument("--out", type=Path, default=HERE)
    parser.add_argument("--vault-root", type=Path, default=None,
                        help="Where benchmark vaults are created (default: benchmarks/.vaults/, "
                             "or benchmark.yaml's 'vault_root:' key) — always a shadow tree, "
                             "never the real ~/investigations directory (#475 follow-up)")
    args = parser.parse_args(argv)
    stages = set(args.stages.split(","))
    wanted_arms = {a.strip() for a in args.arms.split(",") if a.strip()} if args.arms else None

    config = load_config(args.config)
    config_dir = args.config.parent
    root = vault_root(config, config_dir, args.vault_root)

    corpus_dir = config_dir / config["corpus"]["dir"]
    verify_freeze(corpus_dir, config_dir / config["corpus"]["sha256"])
    keys_dir = config_dir / config["keys"]["dir"]
    verify_freeze(keys_dir, config_dir / config["keys"]["sha256"])

    docs = corpus_documents(corpus_dir)
    keys = [yaml.safe_load(f.read_text(encoding="utf-8")) for f in sorted(keys_dir.glob("*.yaml"))]
    expected_skills = _corpus_expected_skills(keys)

    # An unknown arm id is a typo, and silently running the whole sweep because of one is an
    # expensive way to find out — so validate against every id the config defines, up front.
    if wanted_arms is not None:
        known = {a["id"] for key in ("extractor_sweep", "finalizer_sweep", "classifier_sweep")
                 for a in (config.get(key) or {}).get("arms", [])}
        known |= {(config.get("classifier_smoke") or {}).get("arm", {}).get("id")} - {None}
        unknown = wanted_arms - known
        if unknown:
            sys.exit(f"Error: unknown arm id(s): {', '.join(sorted(unknown))}. "
                     f"Known: {', '.join(sorted(known))}")

    def _selected(arm: dict) -> bool:
        return wanted_arms is None or arm.get("id") in wanted_arms

    results: list[ArmResult] = []
    previews: list[tuple[str, dict, dict]] = []
    plan: list[tuple[str, dict]] = []   # (kind, arm-plus-context) queued for real execution

    if "extractor" in stages and config.get("extractor_sweep"):
        sweep = config["extractor_sweep"]
        master = ensure_master_vault(config["master_vault"]["name"], docs, with_sidecars=True,
                                     root=root)
        for arm in sweep["arms"]:
            if not _selected(arm):
                continue
            vault = arm_vault(sweep["vault_prefix"], arm["id"], root)
            if not vault.exists():
                seed_arm_vault(master, vault)
            est = preview_extractor_arm(vault, arm["extractor_model"])
            meta = {"backend": arm_backend(arm["extractor_model"], default="sonnet"),
                    "auth_mode": _auth_mode()}
            previews.append((f"extractor:{arm['id']}", est, meta))
            plan.append(("extractor", {"arm": arm, "vault": vault}))

    if "finalizer" in stages and config.get("finalizer_sweep"):
        sweep = config["finalizer_sweep"]
        base_vault = arm_vault(sweep["vault_prefix"], "base", root)
        base_master = ensure_master_vault(config["master_vault"]["name"], docs, with_sidecars=True,
                                          root=root)
        if not base_vault.exists():
            seed_arm_vault(base_master, base_vault)
            base_result = run_extractor_arm({"id": "base", **sweep["base"]}, base_vault)
            if not base_result.ok:
                sys.exit(f"Error: finalizer sweep's base extraction failed: {base_result.error}")
        for arm in sweep["arms"]:
            if not _selected(arm):
                continue
            vault = arm_vault(sweep["vault_prefix"], arm["id"], root)
            if not vault.exists():
                shutil.copytree(base_vault, vault)
            est = preview_finalizer_arm(vault, arm["finalizer_model"])
            meta = {"backend": arm_backend(arm["finalizer_model"], default="haiku"),
                    "auth_mode": _auth_mode()}
            previews.append((f"finalizer:{arm['id']}", est, meta))
            plan.append(("finalizer", {"arm": arm, "vault": vault}))

    if "classifier" in stages and config.get("classifier_smoke"):
        smoke = config["classifier_smoke"]
        master = ensure_master_vault(config["master_vault"]["classify_name"], docs,
                                     with_sidecars=False, root=root)
        if _selected(smoke["arm"]):
            vault = arm_vault(smoke["vault_prefix"], smoke["arm"]["id"], root)
            if not vault.exists():
                seed_arm_vault(master, vault)
            est = preview_extractor_arm(vault, smoke["arm"]["extractor_model"])
            meta = {"backend": arm_backend(smoke["arm"]["extractor_model"], default="sonnet"),
                    "auth_mode": _auth_mode()}
            previews.append((f"classifier-smoke:{smoke['arm']['id']}", est, meta))
            plan.append(("classifier", {"arm": smoke["arm"], "vault": vault}))

    if "classifier-sweep" in stages and config.get("classifier_sweep"):
        sweep = config["classifier_sweep"]
        cc_dir = config_dir / sweep["corpus_dir"]
        if classify_corpus_ready(cc_dir):
            cc_docs = corpus_documents(cc_dir)
            cc_expected = yaml.safe_load((cc_dir / "expected.yaml").read_text(encoding="utf-8"))
            cc_master_name = f"{sweep['vault_prefix']}-master"
            cc_master = ensure_master_vault(cc_master_name, cc_docs, with_sidecars=False,
                                            root=root)
            for arm in sweep["arms"]:
                if not _selected(arm):
                    continue
                vault = arm_vault(sweep["vault_prefix"], arm["id"], root)
                if not vault.exists():
                    seed_arm_vault(cc_master, vault)
                est = preview_extractor_arm(vault, sweep["fixed"]["extractor_model"])
                previews.append((f"classifier-sweep:{arm['id']}", est, {}))
                plan.append(("classifier-sweep",
                            {"arm": arm, "vault": vault, "fixed": sweep["fixed"],
                             "expected": cc_expected}))
        else:
            print("\nclassifier sweep skipped — benchmarks/classify-corpus/ has no documents "
                 "yet, see its README.")
            results.append(ArmResult(arm_id="classifier-sweep", stage="classifier-sweep",
                                     vault=None, ok=True, skipped=True))

    if not plan:
        print("Nothing to run for the requested stage(s).")
        return 0

    proceed = confirm_run(previews, estimate_only=args.estimate_only)
    if not proceed:
        return 0

    from watchdog.cmd.ingest import _caffeinate
    with _caffeinate():
        for kind, ctx in plan:
            if kind == "extractor":
                results.append(run_extractor_arm(ctx["arm"], ctx["vault"]))
            elif kind == "finalizer":
                results.append(run_finalizer_arm(ctx["arm"], ctx["vault"]))
            elif kind == "classifier":
                results.append(run_classifier_smoke(ctx["arm"], ctx["vault"], expected_skills))
            elif kind == "classifier-sweep":
                results.append(run_classifier_sweep_arm(ctx["arm"], ctx["vault"], ctx["fixed"],
                                                        ctx["expected"]))

    import bench_report
    vaults_to_score = [str(r.vault) for r in results
                       if r.stage == "extractor" and r.ok and r.vault]
    from score_arms import score as score_vaults
    scores = score_vaults(vaults_to_score) if vaults_to_score else {
        "vaults": [], "detail": [], "totals": {"facts": {}, "must_not_miss": {}}, "unscorable": []}
    out_dir = bench_report.write_run(args.out, results, scores, config)
    print(f"\nReport written to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
