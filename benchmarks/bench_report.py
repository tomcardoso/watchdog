"""Pure report/artifact builder for run_benchmark.py (#361 / #215 / #466).

Not a pytest module. Every function here is deterministic and side-effect-free except
`write_run`, which is the only place this module touches disk — kept separate so the table/
summary generation can be unit-tested against synthetic data without any real vault or model
call involved.
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import yaml


def run_id(now: datetime | None = None, *, existing: set[str] | None = None) -> str:
    """"YYYY-MM-DD", or "YYYY-MM-DD-2"/"-3"/... if that name is already taken (`existing`, or —
    when not given — an empty set, i.e. always the bare date)."""
    base = (now or datetime.now()).date().isoformat()
    taken = existing if existing is not None else set()
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def _usage_totals(usage: dict | None) -> dict:
    calls = (usage or {}).get("calls", [])
    return {
        "cost_usd": sum(c.get("cost_usd") or 0.0 for c in calls),
        "latency_s": sum(c.get("latency_s") or 0.0 for c in calls),
        "retries": sum(1 for c in calls if (c.get("attempts") or 1) > 1),
        "sectioned_calls": sum(1 for c in calls if c.get("task") == "extract-section"),
    }


# A bare `sonnet` arm resolves to claude-agent-sdk or claude-api by whichever auth mode the
# machine happened to be in, and the two differ materially on billed input tokens. Recording only
# the arm id left that invisible, so two runs of "the same" arm were not comparable and nothing
# said so (#475). Every arm row now carries the backend that actually served it.
_CLAUDE_BACKENDS = ("claude-agent-sdk", "claude-api", "claude-batch")


def _backends(usage: dict | None) -> str:
    """Distinct `backend (auth)` labels across an arm's calls, first-seen order. Auth mode is
    appended only for Claude backends, where it is what selected the backend."""
    seen = []
    for c in (usage or {}).get("calls", []):
        backend = c.get("backend")
        if not backend:
            continue
        label = (f"{backend} ({c.get('auth_mode')})"
                 if backend in _CLAUDE_BACKENDS and c.get("auth_mode") else backend)
        if label not in seen:
            seen.append(label)
    return ", ".join(seen) or "—"


def _is_notional(usage: dict | None) -> bool:
    """True when any Claude call in the arm ran on subscription auth — `cost_usd` is then a
    list-price equivalent rather than an amount billed."""
    return any(c.get("backend") in _CLAUDE_BACKENDS and c.get("auth_mode") == "subscription"
               for c in (usage or {}).get("calls", []))


def _notional_note(results: list) -> list[str]:
    """A report-level caveat naming the arms whose costs are list-price projections, or [] when
    every arm was metered. Without it a subscription arm's dollar figure sits in the same column
    as a metered one and reads as directly comparable."""
    arms = [r.arm_id for r in results if r.ok and _is_notional(r.usage)]
    if not arms:
        return []
    return ["", f"> **Costs marked `~` are list-price equivalents, not amounts billed** — "
                f"{', '.join(f'`{a}`' for a in sorted(set(arms)))} ran on subscription auth, "
                f"where no per-token billing happens. They are comparable to each other and to "
                f"list prices, but not to a metered arm's real spend.", ""]


def _cost_cell(usage: dict | None) -> str:
    """`$1.234`, or `~$1.234` when the figure is a subscription list-price equivalent."""
    total = _usage_totals(usage)["cost_usd"]
    return f"{'~' if _is_notional(usage) else ''}${total:.3f}"


def _pct_cell(totals: dict, vault_basename: str) -> str:
    t = totals.get(vault_basename)
    if not t or not t.get("of"):
        return "—"
    h, n = t["hit"], t["of"]
    return f"{h / n * 100:.0f}% ({h}/{n})"


def extractor_table_md(results: list, scores: dict) -> str:
    lines = ["| Arm | Backend | Facts | must_not_miss | Cost | Latency (summed) | "
             "Retries / sectioned calls |",
             "|---|---|---|---|---|---|---|"]
    facts = scores.get("totals", {}).get("facts", {})
    mnm = scores.get("totals", {}).get("must_not_miss", {})
    for r in results:
        if r.stage != "extractor":
            continue
        if not r.ok:
            lines.append(f"| `{r.arm_id}` | | failed: {r.error} | | | | |")
            continue
        vb = Path(r.vault).name if r.vault else r.arm_id
        u = _usage_totals(r.usage)
        lines.append(f"| `{vb}` | {_backends(r.usage)} | {_pct_cell(facts, vb)} | "
                     f"{_pct_cell(mnm, vb)} | {_cost_cell(r.usage)} | {u['latency_s']:.0f}s | "
                     f"{u['retries']} retries, {u['sectioned_calls']} sectioned calls |")
    return "\n".join(lines)


def finalizer_table_md(results: list) -> str:
    lines = ["| Arm | Backend | Entity duplicates flagged | Contradictions found | Cost | "
             "Latency (summed) |",
             "|---|---|---|---|---|---|"]
    for r in results:
        if r.stage != "finalizer":
            continue
        if not r.ok:
            lines.append(f"| `{r.arm_id}` | | failed: {r.error} | | | |")
            continue
        vb = Path(r.vault).name if r.vault else r.arm_id
        u = _usage_totals(r.usage)
        dups, contradictions = _entity_note_counts(r.vault) if r.vault else (0, 0)
        lines.append(f"| `{vb}` | {_backends(r.usage)} | {dups} | {contradictions} | "
                     f"{_cost_cell(r.usage)} | {u['latency_s']:.0f}s |")
    return "\n".join(lines)


def _entity_note_counts(vault: Path) -> tuple[int, int]:
    """Plain-text scan of `entities/**/*.md` for `[!contradiction]` callouts, per
    keys/README.md's own scoring note — no new parser needed. `dups` counts entity notes whose
    `aliases`/frontmatter marks them as a resolved duplicate merge target is out of scope for a
    plain-text scan, so it is reported as the count of notes containing a merge marker instead."""
    vault = Path(vault)
    entities_dir = vault / "entities"
    if not entities_dir.is_dir():
        return 0, 0
    contradictions = 0
    dups = 0
    for f in entities_dir.rglob("*.md"):
        text = f.read_text(encoding="utf-8", errors="ignore")
        contradictions += text.count("[!contradiction]")
        if "aka" in text.lower() or "merged" in text.lower():
            dups += 1
    return dups, contradictions


def classifier_table_md(results: list) -> str:
    lines = ["| Document | Expected skill | Classified skill | Pass/fail |", "|---|---|---|---|"]
    for r in results:
        if r.stage != "classifier":
            continue
        if not r.ok:
            lines.append(f"| — | | | failed: {r.error} |")
            continue
        for filename, c in r.extra.get("classification", {}).items():
            mark = "pass" if c["ok"] else "FAIL"
            lines.append(f"| `{filename}` | {c['expected']} | {c['got']} | {mark} |")
    return "\n".join(lines)


def classifier_sweep_table_md(results: list) -> str:
    sweep_results = [r for r in results if r.stage == "classifier-sweep"]
    if any(r.skipped for r in sweep_results):
        return "Skipped — `benchmarks/classify-corpus/` has no documents yet, see its README."
    lines = ["| Arm (classifier model) | Correct / total |", "|---|---|"]
    for r in sweep_results:
        if not r.ok:
            lines.append(f"| `{r.arm_id}` | failed: {r.error} |")
            continue
        classification = r.extra.get("classification", {})
        correct = sum(1 for c in classification.values() if c["ok"])
        lines.append(f"| `{r.arm_id}` | {correct}/{len(classification)} |")
    return "\n".join(lines)


def docs_summary_md(results: list, scores: dict) -> str:
    """Compact, journalist-safe fragment for hand-pasting into docs/benchmarks.md — never
    auto-inserted into that page."""
    extractor = [r for r in results if r.stage == "extractor" and r.ok]
    finalizer = [r for r in results if r.stage == "finalizer" and r.ok]
    lines = ["## Latest run — quick figures", ""]
    # This fragment gets hand-pasted into a page journalists read, where a dollar figure is taken
    # at face value. If any arm ran on subscription auth its cost was never billed, so say so
    # here too rather than only in the technical report (#475).
    if any(_is_notional(r.usage) for r in extractor + finalizer):
        lines += ["> Some figures below come from runs on a Claude subscription rather than "
                  "pay-as-you-go billing. For those, the amount shown is what the same work "
                  "would cost at published per-token rates — not a charge that was incurred.", ""]
    if extractor:
        costs = [_usage_totals(r.usage)["cost_usd"] for r in extractor]
        times = [_usage_totals(r.usage)["latency_s"] for r in extractor]
        lines.append(f"- Processing the six-document benchmark set cost between "
                     f"${min(costs):.2f} and ${max(costs):.2f}, depending on which extraction "
                     f"model was used, and took between {min(times) / 60:.0f} and "
                     f"{max(times) / 60:.0f} minutes.")
    if finalizer:
        costs = [_usage_totals(r.usage)["cost_usd"] for r in finalizer]
        lines.append(f"- Finishing up (linking people and companies across documents, writing "
                     f"the summary) cost between ${min(costs):.2f} and ${max(costs):.2f} on top "
                     f"of that.")
    if not extractor and not finalizer:
        lines.append("- No arms completed in this run.")
    return "\n".join(lines)


def write_run(out_root: Path, results: list, scores: dict, config: dict) -> Path:
    out_root = Path(out_root)
    existing = {p.name for p in out_root.iterdir()} if out_root.is_dir() else set()
    rid = run_id(existing=existing)
    run_dir = out_root / rid
    run_dir.mkdir(parents=True, exist_ok=True)

    verified = [config["corpus"]["sha256"], config["keys"]["sha256"]]
    report = [
        f"# Benchmark run {rid}", "",
        "Verified frozen references: " + ", ".join(f"`{v}`" for v in verified), "",
        "## Extractor sweep", "", extractor_table_md(results, scores),
        *_notional_note([r for r in results if r.stage == "extractor"]), "",
        "## Finalizer sweep", "", finalizer_table_md(results),
        *_notional_note([r for r in results if r.stage == "finalizer"]), "",
        "## Classifier smoke test", "", classifier_table_md(results), "",
        "## Classifier model sweep", "", classifier_sweep_table_md(results), "",
    ]
    failed = [r for r in results if not r.ok]
    if failed:
        report += ["## Failed arms", ""]
        report += [f"- `{r.stage}:{r.arm_id}` — {r.error}" for r in failed]
        report.append("")
    (run_dir / "REPORT.md").write_text("\n".join(report), encoding="utf-8")
    (run_dir / "docs-summary.md").write_text(docs_summary_md(results, scores), encoding="utf-8")
    (run_dir / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    artifacts = run_dir / "artifacts"
    for r in results:
        if not r.ok or not r.vault:
            continue
        vault = Path(r.vault)
        dest = artifacts / vault.name
        for sub, src in (("extracted", vault / ".watchdog" / "extracted"),
                        ("briefings", vault / "briefings")):
            if src.is_dir():
                shutil.copytree(src, dest / sub, dirs_exist_ok=True)
        usage_src = vault / ".watchdog" / "registry" / "usage"
        if usage_src.is_dir():
            shutil.copytree(usage_src, dest / "usage", dirs_exist_ok=True)
        registry_src = vault / ".watchdog" / "registry"
        if registry_src.is_dir():
            (dest / "registry").mkdir(parents=True, exist_ok=True)
            for f in ("documents.json", "entities.json", "manifest.json", "ingest.log"):
                fp = registry_src / f
                if fp.exists():
                    shutil.copy2(fp, dest / "registry" / f)

    return run_dir
