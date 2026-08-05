"""Automated benchmark runner (#361 / #215 / #466) — replaces BENCHMARKING.md's hand-run protocol.

Not a pytest module (no ``test_`` prefix). Run via the pipx dev venv, from the repo root:

    ~/.local/pipx/venvs/watchdog-intel/bin/python benchmarks/run_benchmark.py \
        [--config benchmarks/benchmark.yaml] [--estimate-only] \
        [--stages extractor,finalizer,classifier,classifier-sweep] \
        [--arms sonnet-med-sdk,sonnet-med-api]

`sdk-check` is a fifth stage (not in the default --stages list — request it explicitly): the
backend A/B's subscription-mode follow-up, run on its own once `watchdog auth` is switched to
subscription. See `sdk_check:` in benchmark.yaml.

Drives the real `watchdog` CLI functions in-process (`cmd_extract`, `cmd_finalize`, the same
cost-estimate/usage-file library calls `dig --estimate`/`watchdog usage` use) rather than
shelling out or reimplementing ingest/finalize logic — the same idiom `tests/test_cli.py`
already uses to exercise these functions directly.

Every real (non-`--estimate-only`) run needs your explicit go-ahead — this tool computes and
prints the full cost preview for every arm up front and asks ONE confirmation for the whole
matrix; it never spends money silently and has no flag that bypasses the ask.

A real run also auto-captures any truncation/malformed-JSON/schema-drift/pagination-continuation
response it hits to a local gitignored `benchmarks/.fixture-capture/` directory (#352, D164) — a
source to hand-promote curated examples from into `tests/fixtures/model_responses/`. Safe here
specifically because `corpus-v1` is public court filings, not a real investigation vault.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import os
import shutil
import sys
import time
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

    sweep = config.get("sdk_check")
    if sweep:
        seen = set()
        for arm in sweep.get("arms", []):
            aid = arm.get("id")
            if not aid:
                sys.exit("Error: sdk_check has an arm with no 'id'")
            if aid in seen:
                sys.exit(f"Error: sdk_check has a duplicate arm id '{aid}'")
            seen.add(aid)
            if not arm.get("extractor_model"):
                sys.exit(f"Error: sdk_check arm '{aid}' is missing 'extractor_model'")

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
    """`cmd_new` registers the vault it creates in TWO places (real `watchdog new` behaviour) —
    `~/.watchdog/projects.json` (this function) and Obsidian's own `obsidian.json`, the vault
    switcher (see `_deregister_obsidian_vault` below). Reusing `cmd_new`, rather than hand-rolling
    the vault layout, is what keeps a benchmark vault authentic — but both registrations assume
    every vault `cmd_new` creates is a real, permanent investigation, which a benchmark fixture is
    not, so both have to be undone. This one: remove only the slug just created, never touching
    any other entry."""
    from watchdog.cmd.base import load_projects, save_projects
    projects = load_projects()
    if slug in projects:
        del projects[slug]
        save_projects(projects)


def _deregister_obsidian_vault(vault_path: Path) -> None:
    """The Obsidian-side half of undoing `cmd_new`'s registration (see
    `_deregister_benchmark_vault` above for why both exist). Obsidian's `vaults` dict is keyed by
    an opaque random id, not the vault's path, so matching has to be on the `path` field inside
    each entry, not the key. Mirrors `_register_obsidian_vault`'s own error posture exactly — a
    missing, unreadable, or malformed `obsidian.json`, or Obsidian not being installed at all,
    must never fail a benchmark run, so any failure here is swallowed the same way."""
    import json
    from watchdog.cmd.vault import _obsidian_config_path
    cfg = _obsidian_config_path()
    try:
        if not cfg.exists():
            return
        data = json.loads(cfg.read_text())
        vaults = data.get("vaults", {})
        target = str(vault_path)
        remaining = {k: v for k, v in vaults.items() if v.get("path") != target}
        if len(remaining) != len(vaults):
            data["vaults"] = remaining
            cfg.write_text(json.dumps(data))
    except Exception:
        pass  # non-fatal — same posture as _register_obsidian_vault


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
        _deregister_obsidian_vault(vault)

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


def _stale_reason(vault: Path) -> str | None:
    """Why `vault` isn't safe to start a fresh arm run against, or None if it's fresh (either
    absent, or present but untouched). Checked up front for every arm targeted by this run,
    rather than only being discovered deep inside that arm's own turn — `seed_arm_vault`'s
    reseed-refusal, or `cmd_extract`'s two-phase pending-finalization gate — potentially after
    other arms have already spent real money (#494)."""
    if not vault.exists():
        return None
    wd = vault / ".watchdog"
    queue = wd / "queue"
    if queue.is_dir() and any(queue.glob("*.json")):
        return "already has a queued/staged batch"
    if any((wd / "tmp").glob("result_*.json")):
        return "has a batch pending finalization (extracted but not yet finalized)"
    if (wd / "registry" / "batch-pending.json").exists():
        return "has a pending Message Batches API extraction"
    extracted = wd / "extracted"
    if extracted.is_dir() and any(extracted.glob("*.json")):
        return "already has extracted documents"
    return None


def planned_arm_vaults(config: dict, config_dir: Path, stages: set[str], root: Path,
                       selected) -> list[Path]:
    """Every arm vault this run's selected stages/arms will target — computed without creating
    or seeding anything, mirroring `main`'s own stage-gating exactly, so staleness can be
    checked before any vault is touched or any preview computed."""
    vaults: list[Path] = []
    if "extractor" in stages and config.get("extractor_sweep"):
        sweep = config["extractor_sweep"]
        vaults += [arm_vault(sweep["vault_prefix"], a["id"], root)
                  for a in sweep["arms"] if selected(a)]
    if "finalizer" in stages and config.get("finalizer_sweep"):
        sweep = config["finalizer_sweep"]
        vaults.append(arm_vault(sweep["vault_prefix"], "base", root))
        vaults += [arm_vault(sweep["vault_prefix"], a["id"], root)
                  for a in sweep["arms"] if selected(a)]
    if "classifier" in stages and config.get("classifier_smoke"):
        smoke = config["classifier_smoke"]
        if selected(smoke["arm"]):
            vaults.append(arm_vault(smoke["vault_prefix"], smoke["arm"]["id"], root))
    if "classifier-sweep" in stages and config.get("classifier_sweep"):
        sweep = config["classifier_sweep"]
        if classify_corpus_ready(config_dir / sweep["corpus_dir"]):
            vaults += [arm_vault(sweep["vault_prefix"], a["id"], root)
                      for a in sweep["arms"] if selected(a)]
    if "sdk-check" in stages and config.get("sdk_check"):
        sweep = config["sdk_check"]
        vaults += [arm_vault(sweep["vault_prefix"], a["id"], root)
                  for a in sweep["arms"] if selected(a)]
    return vaults


# ─── arm execution ───────────────────────────────────────────────────────────────────────────

@dataclass
class ArmResult:
    arm_id: str
    stage: str                     # "extractor" | "finalizer" | "classifier" | "classifier-sweep"
                                    # | "sdk-check"
    vault: Path | None
    ok: bool
    skipped: bool = False          # true only for classifier-sweep with an empty corpus
    cancelled: bool = False        # ctrl+c during this arm — the whole run stops after it
    error: str | None = None
    # Per-document failures inside an otherwise-successful call (cmd_extract/cmd_finalize catch
    # these internally and return normally — `ok` alone would miss them entirely, silently, once
    # the terse runner stops printing the underlying pipeline's own per-document error lines).
    # Written to errors.log by bench_report.write_run, not the terminal.
    doc_errors: list[str] = field(default_factory=list)
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


def preview_extractor_arm(vault: Path, extractor_model: str, extractor_effort: str | None = None,
                          benchmarks_root: Path | None = None) -> dict:
    """Cost preview for one extractor arm (issue #478). Every arm vault is fresh by design
    (`BENCHMARKING.md`), so it never has its own usage history for `cost_estimate` to price
    against — this instead borrows usage archived from a past benchmark run of the same
    model/effort/backend combination (`cost_reference.reference_usage_files`), and only when
    none exists anywhere yet falls back to a rough catalog-list-price projection, clearly marked
    as such rather than presented as a calibrated figure."""
    import cost_reference
    from watchdog.pipeline.ingest_setup import scan_queue, cost_estimate
    queue_files = scan_queue(vault)
    backend = arm_backend(extractor_model, default="sonnet")
    _, model = _resolve(extractor_model, default="sonnet")
    ref_files = (cost_reference.reference_usage_files(benchmarks_root, model, extractor_effort,
                                                      backend) if benchmarks_root else [])
    est = cost_estimate(vault, queue_files, backend, usage_files=ref_files)
    if est["cost_low"] is None and backend != "claude-agent-sdk" and est["documents"]:
        fallback = cost_reference.fallback_estimate(est["est_tokens"], model, extractor_effort)
        if fallback:
            est.update(fallback)
    return est


def preview_finalizer_arm(vault: Path, finalizer_model: str, finalizer_effort: str | None = None,
                          benchmarks_root: Path | None = None) -> dict:
    """`preview_extractor_arm`'s counterpart for the finalizer sweep (issue #478) — same
    borrow-then-fallback chain, restricted to standalone-finalize usage (`finalize_only=True`),
    matching `finalize_cost_estimate`'s own task filter."""
    import cost_reference
    from watchdog.pipeline.ingest_setup import finalize_cost_estimate
    backend = arm_backend(finalizer_model, default="haiku")
    _, model = _resolve(finalizer_model, default="haiku")
    ref_files = (cost_reference.reference_usage_files(benchmarks_root, model, finalizer_effort,
                                                      backend, finalize_only=True)
                if benchmarks_root else [])
    est = finalize_cost_estimate(vault, backend, usage_files=ref_files)
    if est["cost_low"] is None and backend != "claude-agent-sdk" and est["docs"]:
        fallback = cost_reference.fallback_estimate(est["est_tokens"], model, finalizer_effort)
        if fallback:
            est.update(fallback)
    return est


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


@contextlib.contextmanager
def _in_vault(vault: Path):
    """`cmd_extract`/`cmd_finalize` (via `cmd_ingest`) resolve their vault from the current
    working directory only — `ingest.py`'s `vault = Path(".").resolve()` takes no explicit path
    argument, unlike the cost-estimate/chew functions this driver calls elsewhere. This is the
    one place that gap gets bridged, so every real-execution call site targets the right vault
    regardless of where `run_benchmark.py` itself was invoked from."""
    prev = Path.cwd()
    os.chdir(vault)
    try:
        yield
    finally:
        os.chdir(prev)


def _doc_errors(summary: dict | None) -> list[str]:
    """Per-document failures from a cmd_extract summary — caught and tallied internally
    (`_guarded`'s except Exception), never raised, so `ok=True` at the ArmResult level says
    nothing about whether every document actually succeeded."""
    if not summary:
        return []
    return [f"{r.get('filename') or r.get('sha256', '?')}: {r.get('reason', '')}"
           for r in summary.get("results", []) if r.get("status") not in ("ok", "skipped", "cancelled")]


def run_extractor_arm(arm: dict, vault: Path) -> ArmResult:
    from watchdog.cmd import ingest as ing
    # `verify` (#535) is an explicit True/False per arm, never None: an arm must not inherit the
    # verification pass from whatever `verify_extraction` the machine running the sweep happens
    # to have configured — an arm's spend and its recall both change with it.
    ns = SimpleNamespace(command="dig", extractor_model=arm["extractor_model"],
                        extractor_effort=arm.get("extractor_effort"), estimate=False,
                        force=False, skip_warning=True, wait=False, no_finalize=True,
                        verify=bool(arm.get("verify", False)))
    try:
        with _in_vault(vault):
            summary = _quiet(ing.cmd_extract, ns, non_interactive=True)
    except SystemExit as e:
        return ArmResult(arm_id=arm["id"], stage="extractor", vault=vault, ok=False, error=str(e))
    return ArmResult(arm_id=arm["id"], stage="extractor", vault=vault, ok=True,
                     cancelled=bool((summary or {}).get("cancelled")),
                     doc_errors=_doc_errors(summary), usage=_latest_usage(vault))


def run_finalizer_arm(arm: dict, vault: Path) -> ArmResult:
    from watchdog.cmd import ingest as ing
    ns = SimpleNamespace(finalizer_model=arm["finalizer_model"],
                        finalizer_effort=arm.get("finalizer_effort"), estimate=False,
                        skip_briefing=False)
    try:
        with _in_vault(vault):
            out = _quiet(ing.cmd_finalize, ns)
    except SystemExit as e:
        return ArmResult(arm_id=arm["id"], stage="finalizer", vault=vault, ok=False, error=str(e))
    # A finalize failure (rate limit, reconciliation error) is also caught and returned rather
    # than raised — same silent-`ok=True` trap as extraction's per-document failures.
    reason = (out or {}).get("error") or (out or {}).get("briefing_error")
    return ArmResult(arm_id=arm["id"], stage="finalizer", vault=vault, ok=True,
                     doc_errors=[reason] if reason else [], usage=_latest_usage(vault))


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
                            skip_warning=True, wait=False, no_finalize=True, verify=False)
    try:
        with _in_vault(vault):
            ex_summary = _quiet(ing.cmd_extract, ex_ns, non_interactive=True)
            if (ex_summary or {}).get("cancelled"):
                return ArmResult(arm_id=arm["id"], stage="classifier", vault=vault, ok=True,
                                 cancelled=True, doc_errors=_doc_errors(ex_summary))
            fn_ns = SimpleNamespace(finalizer_model=arm["finalizer_model"], finalizer_effort=None,
                                    estimate=False, skip_briefing=True)
            _quiet(ing.cmd_finalize, fn_ns)
    except SystemExit as e:
        return ArmResult(arm_id=arm["id"], stage="classifier", vault=vault, ok=False, error=str(e))
    return ArmResult(arm_id=arm["id"], stage="classifier", vault=vault, ok=True,
                     doc_errors=_doc_errors(ex_summary),
                     extra={"classification": _classify_results(vault, expected)})


def run_classifier_sweep_arm(arm: dict, vault: Path, fixed: dict, expected: dict[str, str]) -> ArmResult:
    from watchdog.cmd import ingest as ing
    ex_ns = SimpleNamespace(command="dig", extractor_model=fixed["extractor_model"],
                            classifier_model=arm["classifier_model"], extractor_effort=None,
                            estimate=False, force=False, skip_warning=True, wait=False,
                            no_finalize=True, verify=False)
    try:
        with _in_vault(vault):
            ex_summary = _quiet(ing.cmd_extract, ex_ns, non_interactive=True)
            if (ex_summary or {}).get("cancelled"):
                return ArmResult(arm_id=arm["id"], stage="classifier-sweep", vault=vault, ok=True,
                                 cancelled=True, doc_errors=_doc_errors(ex_summary))
            fn_ns = SimpleNamespace(finalizer_model=fixed["finalizer_model"], finalizer_effort=None,
                                    estimate=False, skip_briefing=True)
            _quiet(ing.cmd_finalize, fn_ns)
    except SystemExit as e:
        return ArmResult(arm_id=arm["id"], stage="classifier-sweep", vault=vault, ok=False,
                         error=str(e))
    return ArmResult(arm_id=arm["id"], stage="classifier-sweep", vault=vault, ok=True,
                     doc_errors=_doc_errors(ex_summary),
                     extra={"classification": _classify_results(vault, expected)})


def classify_corpus_ready(corpus_dir: Path) -> bool:
    """True iff the classifier-model sweep has a document set to run against — at least one
    PDF and an `expected.yaml` labelling it. See corpora/classify/README.md."""
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
    any_priced = any_projected = False
    print("\nCost preview:")
    for label, est, meta in previews:
        low, high = est.get("cost_low"), est.get("cost_high")
        if low is None:
            print(f"  {label}: no dollar estimate (subscription auth or no usage history yet)")
        else:
            any_priced = True
            total_low += low
            total_high += high
            # `cost_reference.fallback_estimate` (#478) sets this when no archived benchmark run
            # of this exact model/effort/backend exists yet, so the figure is a catalog-list-price
            # projection rather than one calibrated from real usage — flagged rather than shown
            # indistinguishably from a run-calibrated range.
            if est.get("projected"):
                any_projected = True
                print(f"  {label}: ~${low:.2f}  (rough projection, no matching run history yet)")
            else:
                print(f"  {label}: ~${low:.2f}-{high:.2f}")
        # Which backend this arm resolves to, and on Claude the auth mode that chose it (#475).
        backend = (meta or {}).get("backend")
        if backend:
            auth = (meta or {}).get("auth_mode")
            suffix = f" ({auth})" if auth and backend.startswith("claude-") else ""
            print(f"    backend: {backend}{suffix}")
    if any_priced:
        note = "  (includes rough projection(s) — see above)" if any_projected else ""
        print(f"  TOTAL: ~${total_low:.2f}-{total_high:.2f}{note}")
    else:
        print("  TOTAL: no dollar estimate for any arm")
    if estimate_only:
        return False
    return interactive.confirm(f"\nRun {len(previews)} arm(s)?", default=False)


def _arm_idx(i: int, total: int) -> str:
    return f"[{i:>{len(str(total))}}/{total}]"


def _arm_starting_line(i: int, total: int, label: str) -> str:
    """Printed the moment an arm begins, before its own line in the run loop. A real extraction
    arm can run for several minutes (a 209-page document at high effort), and `_quiet` suppresses
    the underlying pipeline's own progress output entirely — without this there is nothing on
    screen to say the run is alive rather than hung. A second, separate line rather than
    overwriting this one in place (`\\r`): the runner's own scrolling design is meant to survive
    being piped to a log file, which in-place terminal updates don't."""
    return f"  {_arm_idx(i, total)} {label:<28} running…"


def _arm_line(i: int, total: int, label: str, result: ArmResult, elapsed: float) -> str:
    """One terse line per finished arm, printed after `_arm_starting_line`'s. Developer-only
    tool — the underlying pipeline's own verbose per-document output (progress rows, warnings, an
    elapsed ticker) is suppressed (`_quiet`) during a real run in favour of this; full failure
    detail goes to errors.log, not the terminal, so a bad arm doesn't have to be read off a
    scrolling wall of text."""
    secs = int(elapsed)
    dur = f"{secs // 60}m{secs % 60:02d}s"
    idx = _arm_idx(i, total)
    if result.cancelled:
        return f"  {idx} {label:<28} interrupted"
    if not result.ok or result.doc_errors:
        n = len(result.doc_errors)
        detail = f"{n} failed" if n else "failed"
        return f"  {idx} {label:<28} ✗ {detail}  {dur}  (see errors.log)"
    return f"  {idx} {label:<28} ✓  {dur}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "benchmark.yaml")
    parser.add_argument("--estimate-only", action="store_true")
    parser.add_argument("--stages", default="extractor,finalizer,classifier,classifier-sweep")
    parser.add_argument("--arms", default=None,
                        help="comma-separated arm ids to run (default: every arm in the "
                             "selected stages). Lets one comparison run without paying for the "
                             "whole sweep, e.g. --arms sonnet-med-sdk,sonnet-med-api")
    parser.add_argument("--out", type=Path, default=HERE / "runs",
                        help="Where run directories are written (default: benchmarks/runs/, gitignored)")
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
        known = {a["id"] for key in ("extractor_sweep", "finalizer_sweep", "classifier_sweep",
                                     "sdk_check")
                 for a in (config.get(key) or {}).get("arms", [])}
        known |= {(config.get("classifier_smoke") or {}).get("arm", {}).get("id")} - {None}
        unknown = wanted_arms - known
        if unknown:
            sys.exit(f"Error: unknown arm id(s): {', '.join(sorted(unknown))}. "
                     f"Known: {', '.join(sorted(known))}")

    def _selected(arm: dict) -> bool:
        return wanted_arms is None or arm.get("id") in wanted_arms

    # Every target vault must be fresh before this run spends a cent — refuse the whole run
    # rather than discover mid-sweep, after earlier arms already ran, that a later arm's vault
    # has stale state (#494).
    stale = [(v, r) for v in planned_arm_vaults(config, config_dir, stages, root, _selected)
            if (r := _stale_reason(v))]
    if stale:
        lines = "\n".join(f"  {v} — {r}" for v, r in stale)
        rm = "\n".join(f"  rm -rf {v}" for v, _ in stale)
        sys.exit(f"Error: {len(stale)} target vault(s) aren't fresh — refusing to start the "
                 f"run:\n{lines}\n\nRemove them first, then re-run, e.g.:\n{rm}")

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
            est = preview_extractor_arm(vault, arm["extractor_model"], arm.get("extractor_effort"),
                                        args.out)
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
            est = preview_finalizer_arm(vault, arm["finalizer_model"], arm.get("finalizer_effort"),
                                        args.out)
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
            est = preview_extractor_arm(vault, smoke["arm"]["extractor_model"],
                                        smoke["arm"].get("extractor_effort"), args.out)
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
                est = preview_extractor_arm(vault, sweep["fixed"]["extractor_model"],
                                            sweep["fixed"].get("extractor_effort"), args.out)
                previews.append((f"classifier-sweep:{arm['id']}", est, {}))
                plan.append(("classifier-sweep",
                            {"arm": arm, "vault": vault, "fixed": sweep["fixed"],
                             "expected": cc_expected}))
        else:
            print("\nclassifier sweep skipped — benchmarks/corpora/classify/ has no documents "
                 "yet, see its README.")
            results.append(ArmResult(arm_id="classifier-sweep", stage="classifier-sweep",
                                     vault=None, ok=True, skipped=True))

    # Not in the default --stages list (see the module docstring) — this is the backend A/B's
    # subscription-mode follow-up (extractor_sweep's sonnet-med-sdk/sonnet-med-api comment), run
    # on its own once you've switched `watchdog auth` to subscription. Its own small corpus, own
    # master vault, own prefix — kept fully separate from the main extractor sweep's vaults.
    if "sdk-check" in stages and config.get("sdk_check"):
        sweep = config["sdk_check"]
        sc_dir = config_dir / sweep["corpus_dir"]
        sc_docs = corpus_documents(sc_dir)
        sc_master = ensure_master_vault(f"{sweep['vault_prefix']}-master", sc_docs,
                                        with_sidecars=True, root=root)
        for arm in sweep["arms"]:
            if not _selected(arm):
                continue
            vault = arm_vault(sweep["vault_prefix"], arm["id"], root)
            if not vault.exists():
                seed_arm_vault(sc_master, vault)
            est = preview_extractor_arm(vault, arm["extractor_model"], arm.get("extractor_effort"),
                                        args.out)
            meta = {"backend": arm_backend(arm["extractor_model"], default="sonnet"),
                    "auth_mode": _auth_mode()}
            previews.append((f"sdk-check:{arm['id']}", est, meta))
            plan.append(("sdk-check", {"arm": arm, "vault": vault}))

    if not plan:
        print("Nothing to run for the requested stage(s).")
        return 0

    proceed = confirm_run(previews, estimate_only=args.estimate_only)
    if not proceed:
        return 0

    from watchdog.cmd.ingest import _caffeinate
    from watchdog import fixture_capture
    total = len(plan)
    print(f"\nRunning {total} arm(s)…\n")
    # Auto-captures real responses that hit truncation/malformed-JSON/schema-drift/pagination
    # conditions (#352) — a curated subset gets hand-promoted into tests/fixtures/model_responses/
    # later. Safe here specifically because corpus-v1 is public court filings, not a real
    # investigation vault; this must never be enabled from production ingest code (see
    # fixture_capture.py's module docstring and DECISIONS.md D164).
    fixture_capture.enable(HERE / ".fixture-capture")
    # Only `result.cancelled` (SIGINT trapped inside async extraction, see orchestrate.py) is the
    # *designed* early-stop path — it breaks the loop normally and falls straight through to
    # scoring/write_run below. Anything else that interrupts the loop (a Ctrl+C outside that
    # narrow window, or a genuinely unexpected exception) must not lose the whole run's report
    # over one bad arm — whatever's in `results` so far still gets scored and written (#494).
    interrupted: BaseException | None = None
    try:
        with _caffeinate():
            for i, (kind, ctx) in enumerate(plan, 1):
                label = f"{kind}:{ctx['arm']['id']}"
                print(_arm_starting_line(i, total, label), flush=True)
                start = time.monotonic()
                if kind == "extractor":
                    result = run_extractor_arm(ctx["arm"], ctx["vault"])
                elif kind == "finalizer":
                    result = run_finalizer_arm(ctx["arm"], ctx["vault"])
                elif kind == "classifier":
                    result = run_classifier_smoke(ctx["arm"], ctx["vault"], expected_skills)
                elif kind == "classifier-sweep":
                    result = run_classifier_sweep_arm(ctx["arm"], ctx["vault"], ctx["fixed"],
                                                      ctx["expected"])
                elif kind == "sdk-check":
                    # Same extraction path as the main sweep (run_extractor_arm), relabelled so
                    # it's excluded from extractor-sweep recall scoring and the six-document cost
                    # summary below — its two-document corpus doesn't match either one.
                    result = run_extractor_arm(ctx["arm"], ctx["vault"])
                    result.stage = "sdk-check"
                results.append(result)
                print(_arm_line(i, total, label, result, time.monotonic() - start))
                if result.cancelled:
                    print(f"\nRun stopped — {i} of {total} arm(s) completed.")
                    break
    except KeyboardInterrupt as e:
        interrupted = e
        print(f"\n\nInterrupted — {len(results)} of {total} arm(s) completed. Writing a report "
              f"for what finished…")
    except Exception as e:
        interrupted = e
        print(f"\n\n{type(e).__name__}: {e}\n{len(results)} of {total} arm(s) completed. "
              f"Writing a report for what finished…")
    finally:
        fixture_capture.disable()

    import bench_report
    vaults_to_score = [str(r.vault) for r in results
                       if r.stage == "extractor" and r.ok and r.vault]
    from score_arms import score as score_vaults
    scores = score_vaults(vaults_to_score) if vaults_to_score else {
        "vaults": [], "detail": [], "totals": {"facts": {}, "must_not_miss": {}}, "unscorable": []}
    out_dir = bench_report.write_run(args.out, results, scores, config)
    n_failed = sum(1 for r in results if not r.ok or r.doc_errors)
    tail = f" — {n_failed} arm(s) had failures, see errors.log" if n_failed else ""
    print(f"\nReport written to {out_dir}{tail}")
    if interrupted is not None:
        return 130 if isinstance(interrupted, KeyboardInterrupt) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
