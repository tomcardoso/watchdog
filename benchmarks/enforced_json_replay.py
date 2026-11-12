#!/usr/bin/env python3
"""Replay one extraction request with and without API-enforced JSON output (#804).

Haiku 5.5 at `high` effort sometimes ends an extraction early: valid JSON, stop reason
`end_turn`, one to three `key_facts`, often a placeholder `quote_locator` ("x", "[]"). The
suspect is structured output (`output_config.format`). This script settles it by replay:

  --capture   drive Watchdog's own extraction code (`orchestrate._simple_extract`) on one
              document with the Anthropic client replaced by a recorder, and save the exact
              request kwargs (no call is made, nothing is billed). Page text comes from
              `--pages-json` (a JSON list of per-page strings, one OCR reading per page).
  --reanalyze re-read the saved raw responses and recompute every per-run field (no API call)
  --runs N    replay the captured request N times in each of two modes, interleaved and at most
              4 at a time: A "enforced" exactly as captured; B "prompted", with `output_config.
              format` removed (effort kept) and the schema written into the prompt the way
              `model_client._openai_complete_async` does for a backend that cannot enforce one.
              Prints and saves per-run records and summary tables.

The key is read from WATCHDOG_ANTHROPIC_API_KEY (never printed or written). `--budget` stops
launching calls once the running cost passes it. Collapsed = at most 3 key_facts, or any
`quote_locator` with fewer than three letters or digits (`postflight._ENDED_EARLY_MAX_FACTS` and
`_placeholder_locator`); the stricter post-flight rule (both together) is reported beside it.

Example:
  benchmarks/enforced_json_replay.py --capture --pages-json pages.json --workdir /tmp/replay
  benchmarks/enforced_json_replay.py --runs 20 --workdir /tmp/replay --budget 1.2
"""
import argparse
import asyncio
import copy
import json
import math
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

DOC = ROOT / "benchmarks/corpora/extract/CV-21-00656040-00CL Laurentian U Initial Order 1 FEB 2021.pdf"
BRIEF = ROOT / "benchmarks/briefs/corpus-v1.md"
SKILL = "bankruptcy"          # the document's sidecar pin
MODEL_TIER, EFFORT = "haiku-5.5", "high"
KEY_ENV = "WATCHDOG_ANTHROPIC_API_KEY"
PRIOR = {"enforced": (1, 11), "prompted": (0, 10)}   # the earlier probe: (collapsed, runs)


class _Captured(BaseException):
    """Raised by the recording client to stop the real code path once the request is saved."""


# ── capture ───────────────────────────────────────────────────────────────────────────────────

def capture(pages_json: Path, out: Path) -> dict:
    import anthropic

    from watchdog import config, model_client, skills_catalog
    from watchdog.pipeline import harvest, orchestrate, preflight, schemas, section

    work = Path(tempfile.mkdtemp(prefix="replay-vault-"))
    config.HOME = work / "home"
    texts = json.loads(pages_json.read_text(encoding="utf-8"))
    pages = [{"page": i + 1, "markdown": t} for i, t in enumerate(texts)]
    sha = "0" * 64
    queue = work / ".watchdog" / "queue"
    queue.mkdir(parents=True)
    (work / ".watchdog" / "tmp").mkdir()
    record = {"sha256": sha, "filename": DOC.name, "source_path": str(DOC), "page_count": len(pages),
              "pages": pages, "char_count": sum(len(t) for t in texts),
              "metadata": {"ocr_used": True, "garbled_detected": False, "source_type": "docling"},
              "harvest": harvest.harvest_pages(pages)}
    (queue / f"{sha}.json").write_text(json.dumps(record), encoding="utf-8")
    brief = BRIEF.read_text(encoding="utf-8")
    skill_text = skills_catalog.read_skill(SKILL)
    pf = preflight.run(work, sha)
    plan = section.run(work, sha, model=MODEL_TIER, backend="claude-api", pf=pf,
                       skill_text=skill_text, brief=brief)
    if plan.get("sectioned"):
        raise SystemExit("the real pipeline would section this document; this replay covers the "
                         "whole-document request only")

    saved: list[dict] = []

    class _Stream:
        def __init__(self, **kw):
            saved.append(kw)
            raise _Captured

    class _Client:
        def __init__(self, *a, **k):
            self.messages = type("M", (), {"stream": staticmethod(lambda **kw: _Stream(**kw))})()

    anthropic.AsyncAnthropic = _Client
    model_client._resolve_backend_auth = lambda requested: (
        "claude-api", "anthropic", "not-a-key", "api-key", None)
    try:
        asyncio.run(orchestrate._simple_extract(
            work, sha, pf, skill_text, brief, MODEL_TIER, SKILL, EFFORT, "claude-api"))
    except _Captured:
        pass
    if not saved:
        raise SystemExit("no request was captured")
    req = saved[0]
    schema_matches = req["output_config"]["format"]["schema"] == schemas.EXTRACTION
    data = {"request": req, "schema_equals_project_schema": schema_matches,
            "pages": len(pages), "ocr_chars": record["char_count"],
            "harvest_names_present": record["harvest"].get("names") is not None,
            "plan": {k: plan[k] for k in ("sectioned",) if k in plan}}
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data


# ── replay ────────────────────────────────────────────────────────────────────────────────────

def schema_note(schema: dict) -> str:
    """The wording `model_client._openai_complete_async` appends where a schema is not enforced,
    plus one sentence asking for a bare JSON object."""
    return (f"\n\nReturn JSON matching this schema:\n{json.dumps(schema)}\n\n"
            "Answer with only a JSON object that matches this schema: no prose, no markdown fences.")


def prompted_request(req: dict) -> dict:
    r = copy.deepcopy(req)
    schema = r["output_config"]["format"]["schema"]
    del r["output_config"]["format"]
    note = schema_note(schema)
    msg = r["messages"][-1]
    if isinstance(msg["content"], str):
        msg["content"] += note
    else:
        blocks = msg["content"]
        last = next(b for b in reversed(blocks) if b.get("type") == "text")
        last["text"] += note
    return r


def lenient_parse(text: str):
    """(object, how) for a response that did not parse as returned; (None, None) if nothing works."""
    t = text.strip()
    if t.startswith("```"):
        inner = t.split("\n", 1)[1] if "\n" in t else ""
        inner = inner.rsplit("```", 1)[0]
        try:
            return json.loads(inner), "stripped markdown fence"
        except ValueError:
            pass
    try:
        obj, end = json.JSONDecoder().raw_decode(t)
        if isinstance(obj, dict):
            return obj, f"first JSON object kept, {len(t) - end} trailing characters ignored"
    except ValueError:
        pass
    for tail in ("}", "]}"):
        try:
            return json.loads(t + tail), f"appended missing {tail!r}"
        except ValueError:
            pass
    i, j = t.find("{"), t.rfind("}")
    if 0 <= i < j:
        try:
            return json.loads(t[i:j + 1]), "cut to outermost braces"
        except ValueError:
            pass
    return None, None


def placeholder_locator(facts: list) -> bool:
    """`postflight._placeholder_locator`, same rule."""
    for f in facts:
        if not isinstance(f, dict) or "quote_locator" not in f or f["quote_locator"] is None:
            continue
        loc = f["quote_locator"]
        if not isinstance(loc, str) or sum(c.isalnum() for c in loc) < 3:
            return True
    return False


def analyse(rec: dict, text: str, validator) -> None:
    for k in ("repair", "facts", "entities", "locator_lens", "placeholder", "collapsed",
              "collapsed_postflight", "schema_errors"):
        rec.pop(k, None)
    obj = None
    try:
        obj = json.loads(text)
        rec["parsed_as_returned"] = True
    except ValueError:
        rec["parsed_as_returned"] = False
        obj, how = lenient_parse(text)
        rec["repair"] = how
    rec["parsed_any"] = obj is not None
    if not isinstance(obj, dict):
        return
    doc = obj.get("document") if isinstance(obj.get("document"), dict) else {}
    facts = doc.get("key_facts") if isinstance(doc.get("key_facts"), list) else []
    rec["facts"] = len(facts)
    rec["entities"] = len(obj.get("entities") or [])
    rec["locator_lens"] = [len(f["quote_locator"]) if isinstance(f.get("quote_locator"), str) else None
                           for f in facts if isinstance(f, dict) and "quote_locator" in f]
    rec["placeholder"] = placeholder_locator(facts)
    rec["collapsed"] = rec["facts"] <= 3 or rec["placeholder"]
    rec["collapsed_postflight"] = rec["facts"] <= 3 and rec["placeholder"]
    errs = sorted(validator.iter_errors(obj), key=lambda e: list(e.absolute_path))
    rec["schema_errors"] = [{"kind": e.validator, "path": "/".join(map(str, e.absolute_path))[:80],
                             "message": e.message[:140]} for e in errs]


def cost_of(usage: dict) -> float:
    from watchdog import model_client
    return model_client._api_cost("claude-haiku-5-5", usage) or 0.0


async def one_run(client, idx: int, mode: str, req: dict, validator, raw_dir: Path) -> dict:
    rec = {"run": idx, "mode": mode}
    t0 = time.monotonic()
    try:
        async with client.messages.stream(**req) as stream:
            msg = await stream.get_final_message()
        rec["request_id"] = getattr(msg, "_request_id", None) or getattr(stream.response, "headers", {}).get("request-id")
    except Exception as e:   # noqa: BLE001 — recorded, never retried beyond the SDK's own two
        rec["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        rec["status_code"] = getattr(e, "status_code", None)
        return rec
    rec["seconds"] = round(time.monotonic() - t0, 1)
    rec["stop_reason"] = msg.stop_reason
    usage = msg.usage.model_dump()
    rec["usage"] = {k: usage.get(k) for k in ("input_tokens", "output_tokens",
                                              "cache_creation_input_tokens", "cache_read_input_tokens")}
    rec["thinking_tokens"] = (usage.get("output_tokens_details") or {}).get("thinking_tokens")
    rec["thinking_blocks"] = sum(1 for b in msg.content if b.type in ("thinking", "redacted_thinking"))
    rec["cost_usd"] = cost_of(usage)
    text = next((b.text for b in msg.content if b.type == "text"), "")
    rec["text_chars"] = len(text)
    (raw_dir / f"{idx:02d}-{mode}.txt").write_text(text, encoding="utf-8")
    analyse(rec, text, validator)
    return rec


async def replay(captured: dict, runs: int, budget: float, workdir: Path) -> list[dict]:
    import anthropic
    import jsonschema

    from watchdog.pipeline import schemas

    key = os.environ.get(KEY_ENV)
    if not key:
        raise SystemExit(f"{KEY_ENV} is not set")
    req_a = captured["request"]
    req_b = prompted_request(req_a)
    validator = jsonschema.Draft202012Validator(schemas.EXTRACTION)
    raw_dir = workdir / "raw"
    raw_dir.mkdir(exist_ok=True)
    client = anthropic.AsyncAnthropic(api_key=key)   # SDK default: up to 2 retries on 429/5xx/transport
    jobs = [(i, "enforced" if i % 2 == 0 else "prompted") for i in range(2 * runs)]
    sem = asyncio.Semaphore(4)
    spent = 0.0
    out: list[dict] = []

    async def go(i: int, mode: str):
        nonlocal spent
        async with sem:
            if spent > budget:
                return
            rec = await one_run(client, i, mode, req_a if mode == "enforced" else req_b, validator, raw_dir)
            spent += rec.get("cost_usd") or 0.0
            out.append(rec)
            print(f"{i:02d} {mode:9s} facts={rec.get('facts')} stop={rec.get('stop_reason')} "
                  f"out={rec.get('usage', {}).get('output_tokens')} collapsed={rec.get('collapsed')} "
                  f"err={rec.get('error')} spent=${spent:.3f}", flush=True)
            (workdir / "results.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
            if rec.get("status_code") in (401, 403):
                spent = budget + 1   # stop launching: key or proxy refused

    await asyncio.gather(*(go(i, m) for i, m in jobs))
    return sorted(out, key=lambda r: r["run"])


# ── summary ───────────────────────────────────────────────────────────────────────────────────

def fisher_two_sided(a: int, n1: int, b: int, n2: int) -> float:
    """Two-sided Fisher exact p for collapsed a of n1 against b of n2 (sum of tables no likelier
    than the observed one)."""
    k, n = a + b, n1 + n2

    def p(x):
        return math.comb(n1, x) * math.comb(n2, k - x) / math.comb(n, k)

    obs = p(a)
    return min(1.0, sum(p(x) for x in range(max(0, k - n2), min(n1, k) + 1) if p(x) <= obs * (1 + 1e-9)))


def summarize(recs: list[dict]) -> str:
    lines = []
    done = [r for r in recs if "error" not in r]
    for mode in ("enforced", "prompted"):
        rs = [r for r in done if r["mode"] == mode]
        facts = [r["facts"] for r in rs if "facts" in r]
        outs = [r["usage"]["output_tokens"] for r in rs]
        lines.append(f"{mode}: runs={len(rs)} collapsed={sum(bool(r.get('collapsed')) for r in rs)} "
                     f"(post-flight rule {sum(bool(r.get('collapsed_postflight')) for r in rs)}) "
                     f"facts min/median/max={min(facts)}/{statistics.median(facts)}/{max(facts)} "
                     f"output tokens min/median/max={min(outs)}/{statistics.median(outs)}/{max(outs)} "
                     f"not parsed as returned={sum(not r['parsed_as_returned'] for r in rs)} "
                     f"unparseable={sum(not r['parsed_any'] for r in rs)} "
                     f"schema failures={sum(bool(r.get('schema_errors')) for r in rs)}")
    a = [r for r in done if r["mode"] == "enforced"]
    b = [r for r in done if r["mode"] == "prompted"]
    ca, cb = sum(bool(r.get("collapsed")) for r in a), sum(bool(r.get("collapsed")) for r in b)
    lines.append(f"fisher these runs: {fisher_two_sided(ca, len(a), cb, len(b)):.4f}")
    lines.append("fisher pooled with 1/11 vs 0/10: "
                 f"{fisher_two_sided(ca + PRIOR['enforced'][0], len(a) + PRIOR['enforced'][1], cb + PRIOR['prompted'][0], len(b) + PRIOR['prompted'][1]):.4f}")
    kinds: dict[str, int] = {}
    for r in b:
        for e in r.get("schema_errors", []):
            kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    lines.append(f"mode B schema error kinds: {kinds}")
    lines.append(f"total cost: ${sum(r.get('cost_usd') or 0 for r in recs):.4f}; "
                 f"errors: {[r for r in recs if 'error' in r]}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--capture", action="store_true")
    ap.add_argument("--pages-json", type=Path, help="with --capture: JSON list of per-page text")
    ap.add_argument("--reanalyze", action="store_true")
    ap.add_argument("--runs", type=int, default=0, help="runs per mode")
    ap.add_argument("--budget", type=float, default=1.2, help="stop launching calls past this many dollars")
    ap.add_argument("--workdir", type=Path, required=True, help="scratch directory (request, raw responses)")
    args = ap.parse_args()
    args.workdir.mkdir(parents=True, exist_ok=True)
    cap_path = args.workdir / "captured_request.json"
    if args.capture:
        if not args.pages_json:
            ap.error("--capture needs --pages-json")
        data = capture(args.pages_json, cap_path)
        req = data["request"]
        print("captured:", {k: (v if k not in ("system", "messages") else "...") for k, v in req.items()
                            if k != "output_config"}, "output_config keys:", list(req["output_config"]),
              "effort:", req["output_config"].get("effort"), "schema == project schema:",
              data["schema_equals_project_schema"])
    if args.reanalyze:
        import jsonschema

        from watchdog.pipeline import schemas
        validator = jsonschema.Draft202012Validator(schemas.EXTRACTION)
        recs = json.loads((args.workdir / "results.json").read_text(encoding="utf-8"))
        for r in recs:
            if "error" not in r:
                analyse(r, (args.workdir / "raw" / f"{r['run']:02d}-{r['mode']}.txt").read_text(encoding="utf-8"),
                        validator)
        (args.workdir / "results.json").write_text(json.dumps(recs, indent=1), encoding="utf-8")
        print(summarize(recs))
    if args.runs:
        captured = json.loads(cap_path.read_text(encoding="utf-8"))
        recs = asyncio.run(replay(captured, args.runs, args.budget, args.workdir))
        print(summarize(recs))


if __name__ == "__main__":
    main()
