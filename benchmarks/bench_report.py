"""Pure report/artifact builder for run_benchmark.py (#361 / #215 / #466).

Not a pytest module. Every function here is deterministic and side-effect-free except
`write_run`, which is the only place this module touches disk — kept separate so the table/
summary generation can be unit-tested against synthetic data without any real vault or model
call involved.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import yaml

# The token-accounting epoch a run's cost figures were computed under — hand-maintained, bumped
# by whoever changes how cost is computed or priced, so a consumer can tell two runs' dollar
# figures apart instead of ranking them as if they meant the same thing. History: v1 = pre-D145
# (Claude costs inflated by ~11.2K tokens/call from the agent SDK's tool definitions); v2 =
# post-D145 but pre-#547 (Gemini thinking tokens went unpriced, so Gemini costs were floors, not
# real totals); v3 = current.
COST_MODEL_VERSION = 3


def run_id(now: datetime | None = None, *, existing: set[str] | None = None) -> str:
    """"YYYY-MM-DD-HHMM", or "-2"/"-3"/... if that exact minute is already taken (`existing`, or —
    when not given — an empty set). Minute precision (not just the date) so more than one run in
    a day gets its own folder without colliding — a working session often means several."""
    base = (now or datetime.now()).strftime("%Y-%m-%d-%H%M")
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


def _arm_calls_field(usage: dict | None, key: str) -> tuple[bool, str | None]:
    """Distinct `key` values seen across an arm's calls, first-seen order, joined with `", "` if
    more than one — worth surfacing on its own when that happens, since it means the arm didn't
    run uniformly under one setting. Returns `(found, value)`: `found` is False only when the arm
    made no calls at all, which the caller needs as a distinct case from "the calls all read
    None" — an effort-less model (Claude Haiku, DeepSeek) legitimately reports `effort: None` on
    every call, and that is real information, not a missing measurement to fall back away from."""
    calls = (usage or {}).get("calls", [])
    if not calls:
        return False, None
    seen = []
    for c in calls:
        v = c.get(key)
        if v not in seen:
            seen.append(v)
    return True, (seen[0] if len(seen) == 1 else ", ".join(str(v) for v in seen))


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


def _pct_cell(totals: dict, vault_basename: str, *, partial_suffix: str | None = None) -> str:
    t = totals.get(vault_basename)
    if not t or not t.get("of"):
        return "—"
    h, n = t["hit"], t["of"]
    cell = f"{h / n * 100:.0f}% ({h}/{n})"
    return f"{cell} — {partial_suffix}" if partial_suffix else cell


def is_partial(r) -> bool:
    """True when an extractor arm didn't get through the whole corpus — a rate limit, a Ctrl-C,
    or per-document failures that left fewer staged extractions than the corpus had documents
    (#559). Distinct from `r.ok`, which stays True for all three (`cmd_extract` catches and
    returns a summary, never raises), and from `r.doc_errors`, which is only the third case —
    without this, a partial arm's recall figure reads as a real (catastrophic) quality result
    instead of the different, smaller question it actually answers."""
    if r.stage != "extractor" or not r.ok:
        return False
    if r.rate_limited or r.cancelled:
        return True
    if r.documents_total is not None and r.documents_done is not None:
        return r.documents_done < r.documents_total
    return False


def _partial_suffix(r) -> str | None:
    if not is_partial(r):
        return None
    if r.documents_total is not None:
        return f"partial, {r.documents_done}/{r.documents_total} docs"
    return "partial"


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
        # A partial arm answered a different (smaller) question than a complete one — never show
        # its recall as a bare percentage, which would read as a real, comparable score (#559).
        suffix = _partial_suffix(r)
        lines.append(f"| `{vb}` | {_backends(r.usage)} | {_pct_cell(facts, vb, partial_suffix=suffix)} | "
                     f"{_pct_cell(mnm, vb, partial_suffix=suffix)} | {_cost_cell(r.usage)} | "
                     f"{u['latency_s']:.0f}s | "
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
        return "Skipped — `benchmarks/corpora/classify/` has no documents yet, see its README."
    lines = ["| Arm (classifier model) | Correct / total |", "|---|---|"]
    for r in sweep_results:
        if not r.ok:
            lines.append(f"| `{r.arm_id}` | failed: {r.error} |")
            continue
        classification = r.extra.get("classification", {})
        correct = sum(1 for c in classification.values() if c["ok"])
        lines.append(f"| `{r.arm_id}` | {correct}/{len(classification)} |")
    return "\n".join(lines)


def sdk_check_table_md(results: list) -> str:
    """The subscription-mode follow-up to extractor_sweep's sonnet-med-sdk/sonnet-med-api pair
    (see benchmark.yaml's `sdk_check`) — same shape as the extractor table minus the recall
    columns, since its two-document corpus doesn't score against the six-document keys."""
    lines = ["| Arm | Backend | Cost | Latency (summed) |", "|---|---|---|---|"]
    for r in results:
        if r.stage != "sdk-check":
            continue
        if not r.ok:
            lines.append(f"| `{r.arm_id}` | | failed: {r.error} | |")
            continue
        u = _usage_totals(r.usage)
        lines.append(f"| `{r.arm_id}` | {_backends(r.usage)} | {_cost_cell(r.usage)} | "
                     f"{u['latency_s']:.0f}s |")
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


def _failed_line(r) -> str:
    """The one-line summary of why an arm landed in write_run's "Failed or incomplete arms"
    section — priority order matches how informative each cause is: a hard failure's own message,
    then a rate limit's document count, then a plain interrupt's, then a per-document-failure
    count, all only reachable when `is_partial`/doc_errors put the arm on this list at all."""
    if r.error:
        return r.error
    if r.rate_limited:
        done = r.documents_done if r.documents_done is not None else "?"
        total = r.documents_total if r.documents_total is not None else "?"
        return f"rate-limited after {done}/{total} docs"
    if r.cancelled:
        return (f"interrupted after {r.documents_done}/{r.documents_total} docs"
               if r.documents_total is not None else "interrupted")
    if is_partial(r):
        return f"incomplete — {r.documents_done}/{r.documents_total} docs extracted"
    return f"{len(r.doc_errors)} document(s) failed"


def _errors_log_text(results: list) -> str:
    """Full failure detail for every arm that had any — a hard failure (`error`, arm-level),
    per-document failures caught and tallied internally by cmd_extract/cmd_finalize (`doc_errors`
    — these never raise, so `ok` alone misses them), or a rate limit / partial completion (#559).
    That third case used to write nothing at all: a rate limit's documents get `status: cancelled`
    on the in-flight ones, which `doc_errors` correctly excludes as not-a-failure, so the arm had
    neither `error` nor `doc_errors` and produced no errors.log entry — the exact regression this
    fix targets. Each arm gets at most one block, but that block can carry more than one kind of
    detail (a rate-limited arm can also have had per-document failures on top), unlike the old
    if/elif that could only ever report one. Kept out of the terminal (run_benchmark.py's terse
    per-arm line just says "see errors.log") and out of REPORT.md's tables, which are read as
    much for their shape as their content."""
    blocks = []
    for r in results:
        parts = []
        if r.error:
            parts.append(f"  {r.error}")
        if r.rate_limited:
            done = r.documents_done if r.documents_done is not None else "?"
            total = r.documents_total if r.documents_total is not None else "?"
            reason = r.stop_message or "rate limit"
            parts.append(f"  rate-limited: {reason} — {done}/{total} documents extracted before "
                        f"this arm stopped.")
        elif is_partial(r):
            done = r.documents_done if r.documents_done is not None else "?"
            total = r.documents_total if r.documents_total is not None else "?"
            cause = "interrupted" if r.cancelled else "per-document failures"
            parts.append(f"  incomplete ({cause}) — {done}/{total} documents extracted before "
                        f"this arm stopped.")
        if r.doc_errors:
            parts.append("\n".join(f"  {e}" for e in r.doc_errors))
        if parts:
            blocks.append(f"{r.stage}:{r.arm_id}\n" + "\n".join(parts))
    return "\n\n".join(blocks)


# ─── run provenance (#550 follow-up) ─────────────────────────────────────────────────────────
# A run's figures are only valid against the code that produced them, and this has bitten
# repeatedly: pre-D145 Claude costs were inflated by ~11.2K tokens/call, pre-#547 Gemini costs
# omitted billed thinking tokens entirely, and the #541 starvation failure was diagnosed for
# hours before anyone noticed the run predated the fix. Nothing in the run recorded which commit
# it came from. `dirty` matters as much as the hash: with uncommitted changes in the tree, the
# commit does not describe what actually ran, and the run should be treated as unreproducible.


def git_provenance(repo_dir: Path | None = None) -> dict:
    """The commit a run was made from, plus whether the working tree was clean. Best-effort —
    a checkout without git (an export, a container) records nulls rather than failing a run
    that has already been paid for."""
    import subprocess
    cwd = str(repo_dir or Path(__file__).resolve().parent)
    def _git(*args):
        try:
            r = subprocess.run(("git", *args), cwd=cwd, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return None
        return r.stdout.strip() if r.returncode == 0 else None
    commit = _git("rev-parse", "HEAD")
    status = _git("status", "--porcelain")
    return {
        "commit": commit,
        "commit_short": commit[:9] if commit else None,
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        # None (not False) when git itself was unavailable — "unknown" and "clean" are different
        # claims, and only one of them justifies trusting the commit hash above.
        "dirty": None if status is None else bool(status),
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _provenance_note(prov: dict) -> str:
    if not prov.get("commit"):
        return "Code version: **unknown** (no git metadata available)."
    dirty = prov.get("dirty")
    state = ("with **uncommitted changes** — this run is not reproducible from the commit alone"
             if dirty else "clean tree" if dirty is False else "tree state unknown")
    return f"Code version: `{prov['commit_short']}` on `{prov.get('branch') or '?'}` ({state})."


def _scored_document_count(vault) -> int | None:
    """How many documents this arm actually staged an extraction for, counted straight off
    `.watchdog/extracted/*.json` rather than trusted from the run summary's self-reported
    counts — the same ground truth `score_arms.vault_extracted_shas` restricts its denominator
    to (#559), so a consumer of run.json can cross-check `documents_extracted` against what was
    really scored instead of taking it on faith."""
    if not vault:
        return None
    d = Path(vault) / ".watchdog" / "extracted"
    return len(list(d.glob("*.json"))) if d.is_dir() else None


def _extracted_page_stats(vault) -> tuple[int | None, int | None]:
    """`(pages_extracted, coverage_gaps)` summed/counted straight off an arm's own
    `.watchdog/extracted/*.json` artifacts. This is the arm's OWN page count, not the corpus
    total — the #551 index divides cost and latency by it to get cost-per-page and
    speed-per-page, and a partial arm (rate limit, Ctrl-C, per-document failures, #559) must not
    get flattered by dividing its smaller cost over a denominator it never earned. Defensive in
    the same spirit as `score_arms.vault_doc_texts`: a file that fails to parse, isn't a JSON
    object, or lacks `document`/`page_count` is skipped rather than raising — a benchmark run
    that's already been paid for should not be discarded over one malformed artifact."""
    if not vault:
        return None, None
    d = Path(vault) / ".watchdog" / "extracted"
    if not d.is_dir():
        return None, None
    pages = 0
    gaps = 0
    for f in d.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        doc = data.get("document") if isinstance(data, dict) else None
        if not isinstance(doc, dict):
            continue
        page_count = doc.get("page_count")
        if isinstance(page_count, int):
            pages += page_count
        if doc.get("coverage_gap") is not None:
            gaps += 1
    return pages, gaps


def _manifest_digest(benchmarks_dir: Path, rel_path: str) -> str | None:
    """sha256 of a frozen-reference manifest's own bytes (not the corpus/keys files it lists) —
    `frozen_refs`' path strings only say which manifest was verified, not what that manifest
    actually said at the time; two runs can quote the same path after the file was edited
    between them and look identical without this. `None`, not a raised error, when the file
    can't be read — the run has already happened and paid for itself either way."""
    import hashlib
    try:
        data = (Path(benchmarks_dir) / rel_path).read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest()


def run_json(rid: str, results: list, scores: dict, config: dict, prov: dict,
            benchmarks_dir: Path | None = None) -> dict:
    """The run's numbers in a machine-readable shape, so a consumer (the composite score index,
    #551) reads structured data instead of re-parsing REPORT.md's markdown tables. Every figure
    here is the same one the tables render, from the same helpers — this is a second rendering of
    the run, never a second calculation of it.

    `benchmarks_dir` resolves `frozen_refs`' relative manifest paths for digesting; defaults to
    this file's own directory, which is where `config.yaml`'s `corpus`/`keys` paths are always
    relative to in real use — only tests pass something else."""
    import score_arms
    benchmarks_dir = Path(benchmarks_dir) if benchmarks_dir is not None else Path(__file__).resolve().parent
    facts = scores.get("totals", {}).get("facts", {})
    mnm = scores.get("totals", {}).get("must_not_miss", {})
    # Only extractor_sweep arms declare `extractor_model`/`extractor_effort`/`verify` — keying
    # this off `arm_id` alone (rather than scoping to `r.stage == "extractor"` below) would risk
    # a finalizer/classifier arm sharing an id (e.g. "haiku") with an extractor arm and silently
    # inheriting the wrong config.
    extractor_arm_configs = {a["id"]: a for a in config.get("extractor_sweep", {}).get("arms", [])}
    arms = []
    for r in results:
        vb = Path(r.vault).name if r.vault else r.arm_id
        u = _usage_totals(r.usage)
        arm_cfg = extractor_arm_configs.get(r.arm_id, {}) if r.stage == "extractor" else {}
        # The model/effort that actually served the arm, read from its own calls first — a bare
        # model id like "sonnet" resolves differently depending on runtime auth (#475), so only
        # the calls themselves say for certain what ran. Falls back to the arm's configured value
        # only when it made no calls at all (a hard failure before the first request). The global
        # SQLite telemetry store (D193, #611) also carries model/effort per call, but that store
        # is machine-local and not archived with the run — a run.json copied elsewhere, or read
        # after `~/.watchdog/telemetry.db` is wiped, still has to say what produced its numbers.
        had_calls, model = _arm_calls_field(r.usage, "model")
        model = model if had_calls else arm_cfg.get("extractor_model")
        had_calls, effort = _arm_calls_field(r.usage, "effort")
        effort = effort if had_calls else arm_cfg.get("extractor_effort")
        pages_extracted, coverage_gaps = _extracted_page_stats(r.vault)
        arms.append({
            "arm_id": r.arm_id, "stage": r.stage, "vault": vb,
            "ok": bool(r.ok), "skipped": bool(r.skipped), "cancelled": bool(r.cancelled),
            "error": r.error, "doc_errors": list(r.doc_errors),
            "backends": _backends(r.usage) or None,
            "cost_usd": round(u["cost_usd"], 6), "latency_s": round(u["latency_s"], 3),
            "retries": u["retries"], "sectioned_calls": u["sectioned_calls"],
            "facts": facts.get(vb), "must_not_miss": mnm.get(vb),
            # #559: a rate limit, a Ctrl-C, or per-document failures can all leave an extractor
            # arm short of the whole corpus while `ok` stays True — `partial` is the one flag a
            # consumer needs to know the `facts`/`must_not_miss` figures above answer a smaller
            # question than a complete arm's.
            "partial": is_partial(r),
            "rate_limited": bool(r.rate_limited),
            "stop_message": r.stop_message,
            "documents_extracted": r.documents_done,
            "documents_total": r.documents_total,
            "scored_documents": _scored_document_count(r.vault),
            "model": model,
            "effort": effort,
            "verify": bool(arm_cfg.get("verify", False)),
            # The #551 index's cost-per-page/speed-per-page denominator — the arm's own pages,
            # never the corpus total (see `_extracted_page_stats`).
            "pages_extracted": pages_extracted,
            # A subscription arm's `cost_usd` is a list-price equivalent, not billed money
            # (`_is_notional`'s own docstring) — without this flag reaching run.json, the index's
            # cost column can't tell the two kinds of dollar figure apart.
            "notional_cost": _is_notional(r.usage),
            "coverage_gaps": coverage_gaps,
            # The `<ts>` stem of the `usage-<ts>.json` this arm's `r.usage` was loaded from
            # (`run_benchmark._latest_usage`) — the join key back to `telemetry_db`'s `calls.run_id`
            # column (D193 point 3), which uses that same stem, NOT this dict's own `run_id` above
            # (the benchmark run's "YYYY-MM-DD-HHMM" id — a different namespace with the same
            # name). Without this, matching an archived run's arms to their telemetry rows means
            # guessing from timestamps. `None` when the arm has no usage file at all.
            "usage_run_id": r.usage_run_id,
        })
    corpus_ref, keys_ref = config["corpus"]["sha256"], config["keys"]["sha256"]
    return {
        "run_id": rid,
        "provenance": prov,
        "frozen_refs": {
            "corpus": corpus_ref, "keys": keys_ref,
            # The path string alone only says which manifest was verified, not what it said at
            # verification time — a manifest edited between two runs quotes the same path and
            # looks identical without the digest of its own bytes alongside it.
            "corpus_digest": _manifest_digest(benchmarks_dir, corpus_ref),
            "keys_digest": _manifest_digest(benchmarks_dir, keys_ref),
        },
        # Which code computed these figures, not just which commit (`provenance` above) — a
        # scorer or cost-accounting change can land without touching pipeline code at all, and
        # `git_provenance`'s commit hash doesn't say whether either epoch changed since a past run.
        "versions": {"scorer": score_arms.SCORER_VERSION, "cost_model": COST_MODEL_VERSION},
        "arms": arms,
    }


def _copy_page_text(vault: Path, dest: Path) -> None:
    """Preserve the chewed page text a run was extracted from, so a judge pass can still be run
    against this run later.

    Arm vaults are disposable and are reset the next time that arm runs, which used to mean the
    page text vanished with them — and page text is not a nice-to-have here: it is the grounding
    reference `verifier_precision.py` grades an added fact against. Without this, running four
    arms today and re-running them tomorrow silently destroys the ability to judge today's run.

    Stored once per run, not once per arm: every arm in a run extracts the same corpus from the
    same chew, so the files are identical across arms and keyed by document sha256 already."""
    src = vault / ".watchdog" / "queue"
    if not src.is_dir():
        return
    dest.mkdir(parents=True, exist_ok=True)
    for f in src.glob("*.json"):
        target = dest / f.name
        if not target.exists():
            shutil.copy2(f, target)


def write_run(out_root: Path, results: list, scores: dict, config: dict,
              provenance: dict | None = None, benchmarks_dir: Path | None = None) -> Path:
    # Captured at run *start* by the caller and passed in — a sweep runs for tens of minutes and
    # the tree can change under it, so capturing at write time would record the wrong commit.
    prov = provenance or git_provenance()
    out_root = Path(out_root)
    existing = {p.name for p in out_root.iterdir()} if out_root.is_dir() else set()
    rid = run_id(existing=existing)
    run_dir = out_root / rid
    run_dir.mkdir(parents=True, exist_ok=True)

    verified = [config["corpus"]["sha256"], config["keys"]["sha256"]]
    report = [
        f"# Benchmark run {rid}", "",
        "Verified frozen references: " + ", ".join(f"`{v}`" for v in verified), "",
        _provenance_note(prov), "",
        "## Extractor sweep", "", extractor_table_md(results, scores),
        *_notional_note([r for r in results if r.stage == "extractor"]), "",
        "## SDK backend check", "", sdk_check_table_md(results),
        *_notional_note([r for r in results if r.stage == "sdk-check"]), "",
        "## Finalizer sweep", "", finalizer_table_md(results),
        *_notional_note([r for r in results if r.stage == "finalizer"]), "",
        "## Classifier smoke test", "", classifier_table_md(results), "",
        "## Classifier model sweep", "", classifier_sweep_table_md(results), "",
    ]
    # `is_partial` is gated to the extractor stage (only it has a recall cell for
    # `_partial_suffix` to annotate) — but `r.rate_limited` is a real stop regardless of stage, so
    # it needs its own clause here or a rate-limited sdk-check/classifier arm gets an errors.log
    # block ("Full detail in `errors.log`") with no corresponding line in the report naming it.
    failed = [r for r in results if not r.ok or r.doc_errors or is_partial(r) or r.rate_limited]
    if failed:
        report += ["## Failed or incomplete arms", "", "Full detail in `errors.log`.", "",
                   "A partial arm (rate-limited, interrupted, or missing individual documents) "
                   "answered a different, smaller question than a complete arm did — its recall "
                   "figure above is not a quality signal comparable to a complete arm's, and "
                   "its `cost_usd` still includes spend on any document that never produced an "
                   "extraction at all.", ""]
        report += [f"- `{r.stage}:{r.arm_id}` — " + _failed_line(r) for r in failed]
        report.append("")
    (run_dir / "REPORT.md").write_text("\n".join(report), encoding="utf-8")
    (run_dir / "docs-summary.md").write_text(docs_summary_md(results, scores), encoding="utf-8")
    (run_dir / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    (run_dir / "run.json").write_text(
        json.dumps(run_json(rid, results, scores, config, prov, benchmarks_dir=benchmarks_dir),
                  indent=2) + "\n",
        encoding="utf-8")

    errors_text = _errors_log_text(results)
    if errors_text:
        (run_dir / "errors.log").write_text(errors_text + "\n", encoding="utf-8")

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
        _copy_page_text(vault, run_dir / "pages")

    return run_dir
