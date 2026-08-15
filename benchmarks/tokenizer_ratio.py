"""Per-model token density against corpus-v1 — the measurement behind `tokenizer_ratio` (#617).

Not a pytest module — a standalone benchmark tool, same posture as `score_arms.py`. It answers
one question per model: how many *real* tokens does the provider count for text that
`pipeline/section.py`'s chars/4 `est_tokens` heuristic scores as one estimated token? That
multiplier is `model_catalog.yaml`'s `tokenizer_ratio`, which `section.model_defaults` divides
its window-derived threshold/budget by. Before this script it was set from a vendor sentence for
two Claude models and left at an unmeasured 1.0 for everyone else.

    ~/.local/pipx/venvs/watchdog-intel/bin/python benchmarks/tokenizer_ratio.py

(Add `PYTHONPATH=<checkout>/src` when running from a worktree, so it measures *that* checkout's
`est_tokens` rather than the pipx-installed package's.)

Two independent modes, both reported by default, because neither alone covers the catalog:

`--history` reads the usage records already archived under `benchmarks/<run-id>/artifacts/
*/usage/*.json`. Free and offline — every past benchmark run is a measurement we already paid
for. Each record carries `filename` plus a `detail` of the form `pages 3-11`, so the exact text
that call sent is reconstructible from the master chew, and its `est_tokens` recomputed here.

`--count` asks the providers that expose a free, non-generative token counter — Anthropic's
`messages.count_tokens` and Gemini's `models/<id>:countTokens` — to count the corpus directly.
No tokens are generated, nothing is billed, and no subscription session window is touched.
OpenAI and DeepSeek expose no such endpoint, so they are absent from this mode by construction
and take their numbers from `--history` instead; note that tiktoken is NOT a substitute, since
it cannot map any GPT-5.x id in this catalog (`encoding_for_model("gpt-5.4-mini")` raises), and
is wrong for Claude in any case.

## Why history needs a regression and not a division

The naive read of an archived record — `input_tokens / est_input_tokens` — is not the tokenizer
ratio, and this is the trap #617 was opened about. The two sides measure different text:
`est_input_tokens` is the *document text only*, while the provider's `input_tokens` counts the
*entire rendered prompt* — schema, extraction instructions, record skill, carry-forward
entities, harvested candidates, scratchpad, and the document. Dividing gives

    tokenizer_ratio x (1 + prompt_overhead / document_tokens)

which is not even a constant bias: it varies inversely with section size, so it drifts with the
very sectioning budget it feeds. What saves the archived data is that a benchmark run sends many
*different* section sizes to the same model. Fitting `actual = ratio * est + overhead` across
them separates the two terms — the slope is the tokenizer ratio, the intercept is the prompt
scaffolding, in tokens. Both halves are checkable: the intercepts land at 7.2K-9.3K actual
tokens across four unrelated providers, matching #617's independent estimate of ~7,000 est
tokens of static scaffolding, which is the reason to believe the slope too.

Read `r2` and `slope_stderr` before trusting a row. A model whose archived calls cluster at one
section size, or whose prompt overhead varied a lot across the run (harvested candidates and
carry-forward entities both grow during a run), fits badly and its slope should not set a
catalog constant on its own — `gpt-5.4-nano` is the worked example, at r2 0.38.
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import json
import re
import ssl
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

from watchdog.pipeline.orchestrate import _pages_text  # noqa: E402
from watchdog.pipeline.section import est_tokens  # noqa: E402

# `detail` on an extract/extract-section usage record, e.g. "pages 3-11" or "pages 3-11 (repair)".
# The dash is an en dash in real records; both are accepted so a hand-written fixture still parses.
_PAGES_RE = re.compile(r"pages (\d+)[–-](\d+)")

# Models with a free, non-generative token-counting endpoint. Everyone else is history-only.
_COUNTABLE = {"anthropic", "gemini"}

# A one-character message sent before each counted document so the provider's own message framing
# (role wrappers, BOS markers) cancels out in the subtraction, leaving the document's own tokens.
# Without it a per-document count carries ~5-10 tokens of framing that would inflate short
# documents' ratios more than long ones — the same size-dependent bias the regression exists to
# remove, reintroduced by the measurement itself.
_BASELINE = "."


def slice_pages(doc: dict, start: int, end: int) -> tuple[int, str]:
    """The (est tokens, real text) pair for pages `start`..`end` of a chewed document.

    The two sides deliberately come from different strings, because that is how sectioning
    actually works. The estimate is `est_tokens` over the *raw page markdown*, which is what
    `section.run` packs against (`page_tokens = [est_tokens(by_num.get(n, ""))...]`) and what
    `est_tokens_from_pages` sums for the threshold check. The text is `orchestrate._pages_text`,
    which wraps every page in a `<!-- PAGE N -->` marker and joins on `\\n\\n---\\n\\n` before it
    goes to the model. Those markers are real tokens the call spends but that the estimate never
    counted, so folding them into the measured ratio is correct: the ratio's whole job is to keep
    what sectioning *sends* inside the window given what sectioning *estimated*."""
    pages = [{"page": n, "markdown": doc["pages"].get(n, "")} for n in range(start, end + 1)]
    return sum(est_tokens(p["markdown"]) for p in pages), _pages_text(pages)


def load_chew(vault: Path) -> dict[str, dict]:
    """Master-chew documents keyed by filename: `{pages: {1-based page -> markdown}, ...}`.

    Reads the queue files `watchdog chew` wrote, which hold the exact markdown strings the
    extraction path applies `est_tokens` to — measuring anything else (the source PDF, a
    re-chew) would measure a different corpus."""
    queue = vault / ".watchdog" / "queue"
    if not queue.is_dir():
        sys.exit(f"Error: no chew queue at {queue}\n"
                 f"Run the master chew first, or pass --vault.")
    docs = {}
    for f in sorted(queue.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        pages = {p["page"]: p.get("markdown", "") for p in d.get("pages", [])}
        doc = {
            "pages": pages,
            "page_count": d.get("page_count", len(pages)),
            "ocr": bool((d.get("metadata") or {}).get("ocr_used")),
        }
        doc["est"], doc["text"] = slice_pages(doc, min(pages), max(pages)) if pages else (0, "")
        docs[d["filename"]] = doc
    if not docs:
        sys.exit(f"Error: chew queue at {queue} is empty.")
    return docs


def fit(points: list[tuple[float, float]]) -> dict:
    """Least-squares fit of `actual = slope * est + intercept` over (est, actual) pairs.

    Returns the slope (the tokenizer ratio), the intercept (prompt scaffolding, in real tokens),
    `r2`, and the slope's standard error — the last two are what say whether the slope is worth
    acting on. `slope_stderr` is None for a fit with fewer than three points or no spread in
    `est`, where the residual variance is undefined rather than zero."""
    n = len(points)
    sx = sum(p[0] for p in points)
    sy = sum(p[1] for p in points)
    sxx = sum(p[0] ** 2 for p in points)
    sxy = sum(p[0] * p[1] for p in points)
    den = n * sxx - sx * sx
    if den == 0:                      # every call was the same size — no line to fit
        return {"n": n, "slope": None, "intercept": None, "r2": None, "slope_stderr": None}
    slope = (n * sxy - sx * sy) / den
    intercept = (sy - slope * sx) / n
    ybar = sy / n
    sst = sum((p[1] - ybar) ** 2 for p in points)
    ssr = sum((p[1] - (slope * p[0] + intercept)) ** 2 for p in points)
    stderr = None
    if n > 2:
        # se(slope) = sqrt( (ssr/(n-2)) / sum((x - xbar)^2) ); den/n is that sum of squares.
        stderr = ((ssr / (n - 2)) / (den / n)) ** 0.5
    return {"n": n, "slope": slope, "intercept": intercept,
            "r2": (1 - ssr / sst) if sst else None, "slope_stderr": stderr}


def history(bench_dir: Path, docs: dict[str, dict]) -> dict[tuple[str, str], dict]:
    """Fit every (model, backend) with archived extraction calls. See the module docstring for
    why this is a regression rather than a division.

    `actual` sums `input_tokens` with `cache_read_tokens`/`cache_write_tokens`: on a cached call
    the bulk of the input volume moves into those fields, and the model tokenized all of it —
    the same reasoning as `ingest_setup._real_input_tokens`."""
    groups: dict[tuple[str, str], list[tuple[float, float]]] = collections.defaultdict(list)
    unmatched = 0
    for f in sorted(bench_dir.glob("*/artifacts/*/usage/*.json")):
        try:
            calls = json.loads(f.read_text(encoding="utf-8")).get("calls", [])
        except (OSError, json.JSONDecodeError):
            continue
        for c in calls:
            if c.get("task") not in ("extract", "extract-section"):
                continue
            m = _PAGES_RE.search(c.get("detail") or "")
            doc = docs.get(c.get("filename"))
            if not m or doc is None:
                unmatched += 1
                continue
            est, _ = slice_pages(doc, int(m.group(1)), int(m.group(2)))
            actual = ((c.get("input_tokens") or 0) + (c.get("cache_read_tokens") or 0)
                      + (c.get("cache_write_tokens") or 0))
            if est and actual:
                groups[(c.get("model"), c.get("backend"))].append((est, actual))
    if unmatched:
        print(f"note: {unmatched} archived call(s) skipped — no page range or no matching "
              f"document in the chew", file=sys.stderr)
    out = {}
    for k, pts in groups.items():
        # `pooled` is the naive sum(actual)/sum(est) division — the contaminated figure the
        # pre-#617 `_model_tokenizer_calibration` computed. Reported beside the slope because the
        # gap between the two columns IS the prompt overhead, and seeing it is what makes the
        # regression's point without having to take it on faith.
        out[k] = {**fit(pts),
                  "pooled": sum(p[1] for p in pts) / sum(p[0] for p in pts)}
    return out


async def _count_anthropic(model: str, texts: list[str], key: str) -> list[int]:
    """Real input-token counts via `POST /v1/messages/count_tokens` — free and non-generative.

    Counts are model-specific (that is the whole point of the endpoint here), so each catalog
    Claude id gets its own pass rather than one Claude number standing in for the tier."""
    import anthropic
    client = anthropic.AsyncAnthropic(api_key=key)

    async def one(text: str) -> int:
        r = await client.messages.count_tokens(
            model=model, messages=[{"role": "user", "content": text}])
        return r.input_tokens

    base = await one(_BASELINE)
    return [await one(_BASELINE + t) - base for t in texts]


async def _count_gemini(model: str, texts: list[str], key: str) -> list[int]:
    """Real input-token counts via the native `models/<id>:countTokens` — free, non-generative.

    Uses the native v1beta endpoint rather than the OpenAI-compatibility base URL the extraction
    path calls (`model_client._OPENAI_BASE["gemini"]`), which exposes no counting route. Verifies
    against the OS trust store for the same reason `model_client` does — a TLS-inspecting
    corporate proxy's root CA is trusted by the OS but absent from certifi."""
    import httpx
    import truststore
    ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:countTokens"

    async with httpx.AsyncClient(timeout=120, verify=ctx) as client:
        async def one(text: str) -> int:
            r = await client.post(url, headers={"x-goog-api-key": key},
                                  json={"contents": [{"parts": [{"text": text}]}]})
            r.raise_for_status()
            return r.json()["totalTokens"]

        base = await one(_BASELINE)
        return [await one(_BASELINE + t) - base for t in texts]


async def count(docs: dict[str, dict], models: list[dict]) -> dict[str, dict]:
    """Count every document of the corpus on every countable model.

    One call per document, not per page: #617 settled that per-page variance is not what this
    measurement is for, and per-document already spans the corpus's born-digital/OCR split."""
    from watchdog.cmd import auth
    names = sorted(docs)
    ests = [docs[n]["est"] for n in names]
    out: dict[str, dict] = {}
    for entry in models:
        provider = entry["provider"]
        if provider not in _COUNTABLE:
            continue
        key = (auth.resolve_auth("anthropic").get("key") if provider == "anthropic"
               else auth.get_api_key(provider))
        if not key:
            print(f"skip {entry['id']}: no {provider} credential configured", file=sys.stderr)
            continue
        counter = _count_anthropic if provider == "anthropic" else _count_gemini
        print(f"counting {entry['id']} ({len(names)} documents)…", file=sys.stderr)
        try:
            actuals = await counter(entry["id"], [docs[n]["text"] for n in names], key)
        except Exception as e:                                    # noqa: BLE001 — report, continue
            print(f"skip {entry['id']}: {type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
            continue
        out[entry["id"]] = {
            "provider": provider,
            "corpus_ratio": sum(actuals) / sum(ests),
            "documents": [{"filename": n, "pages": docs[n]["page_count"], "ocr": docs[n]["ocr"],
                           "est": e, "actual": a, "ratio": a / e}
                          for n, e, a in zip(names, ests, actuals)],
        }
    return out


def _fmt(v, spec: str) -> str:
    return "—".rjust(len(format(0, spec))) if v is None else format(v, spec)


def report(counted: dict, fitted: dict, docs: dict) -> str:
    total_pages = sum(d["page_count"] for d in docs.values())
    total_est = sum(d["est"] for d in docs.values())
    lines = [f"# Token density vs corpus-v1 — {len(docs)} documents, {total_pages} pages, "
             f"{total_est:,} est tokens", ""]

    if counted:
        lines += ["## Measured (free count endpoints)", "",
                  "Real provider token counts for the corpus's own chewed markdown, with message "
                  "framing subtracted out. No prompt scaffolding in the sample — this is the "
                  "tokenizer ratio directly.", "",
                  "| model | corpus ratio | per-document spread |", "|---|---|---|"]
        for mid, r in counted.items():
            ratios = [d["ratio"] for d in r["documents"]]
            lines.append(f"| `{mid}` | **{r['corpus_ratio']:.3f}** | "
                         f"{min(ratios):.3f}–{max(ratios):.3f} |")
        lines.append("")
        lines += ["### Per document", "",
                  "| model | document | pages | OCR | est | actual | ratio |", "|---|---|---|---|---|---|---|"]
        for mid, r in counted.items():
            for d in r["documents"]:
                lines.append(f"| `{mid}` | {d['filename'][:44]} | {d['pages']} | "
                             f"{'yes' if d['ocr'] else 'no'} | {d['est']:,} | {d['actual']:,} | "
                             f"{d['ratio']:.3f} |")
        lines.append("")

    if fitted:
        lines += ["## Fitted from archived benchmark history", "",
                  "`actual = slope x est + intercept` over past extraction calls. The slope is "
                  "the tokenizer ratio; the intercept is the rendered prompt's scaffolding, in "
                  "real tokens. `pooled` is the contaminated `actual/est` division that "
                  "`_model_tokenizer_calibration` used to compute, shown for contrast.", "",
                  "| model | backend | n | pooled | slope | ± | intercept | r² |",
                  "|---|---|---|---|---|---|---|---|"]
        for (mid, backend), r in sorted(fitted.items(), key=lambda kv: -kv[1]["n"]):
            lines.append(f"| `{mid}` | {backend} | {r['n']} | {_fmt(r['pooled'], '.3f')} | "
                         f"**{_fmt(r['slope'], '.3f')}** | {_fmt(r['slope_stderr'], '.3f')} | "
                         f"{_fmt(r['intercept'], ',.0f')} | {_fmt(r['r2'], '.3f')} |")
        lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--vault", type=Path, default=REPO / "benchmarks" / ".vaults" / "bench-master",
                   help="vault holding the master chew of the corpus (default: bench-master)")
    p.add_argument("--runs", type=Path, default=REPO / "benchmarks",
                   help="directory holding archived <run-id>/artifacts/*/usage/*.json")
    p.add_argument("--history", action="store_true", help="fit archived history only (offline)")
    p.add_argument("--count", action="store_true",
                   help="query the free count endpoints only (Anthropic, Gemini)")
    p.add_argument("--json", type=Path, help="also write the raw measurements here")
    args = p.parse_args(argv)

    # Neither flag means both — the two modes cover different halves of the catalog.
    do_history, do_count = (args.history, args.count) if (args.history or args.count) else (True, True)

    docs = load_chew(args.vault)
    fitted = history(args.runs, docs) if do_history else {}
    counted = {}
    if do_count:
        from watchdog.model_catalog import all_models
        counted = asyncio.run(count(docs, all_models()))

    print(report(counted, fitted, docs))
    if args.json:
        args.json.write_text(json.dumps(
            {"counted": counted,
             "fitted": {f"{m}|{b}": r for (m, b), r in fitted.items()}}, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
