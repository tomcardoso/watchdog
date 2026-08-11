"""Live confirmation for #586: does gpt-5.6-luna produce a PARTIAL prefix hit?

Two arms, each two calls that share a long prefix but differ after it:

  Arm FIXED   — model_id gpt-5.6-luna, so catalog_cache_breakpoints() is True and the
                request carries prompt_cache_breakpoint + prompt_cache_options.
  Arm CONTROL — the same prompts sent the pre-fix way (flattened, no breakpoint), by
                temporarily reporting the model as not breakpoint-capable.

Each arm gets its own nonce woven into the prefix, so the two arms can never share a
cache entry and both start cold. Call 2 of each arm is the measurement: a PARTIAL hit
(0 < cache_read < input) is prefix caching, which is the thing that has never once been
observed on this model. A full hit would mean whole-prompt matching, and 0 means nothing.
"""
import asyncio
import sys
import uuid

from watchdog import model_client as mc
from watchdog.cmd import auth
from watchdog.pipeline import prompts, schemas

MODEL = "gpt-5.6-luna"
BASE = mc._OPENAI_BASE["openai"]


def build(nonce: str, doc_text: str):
    """A real extract prompt: instructions+brief in block 0, skill carrying the breakpoint
    in block 1, per-document text in block 2. The nonce rides in the brief, inside the
    cacheable prefix, so each arm's prefix is unique to this run."""
    return prompts.build_extract_prompt(
        pages_text=doc_text,
        skill_text=(prompts.importlib.resources.files("watchdog")
                    / "skills" / "records" / "court-documents.md").read_text(encoding="utf-8"),
        sidecar=None,
        brief=f"Run probe {nonce}. Identify the parties and any monetary amounts.",
        known_document_types=[],
    )


DOC_A = ("IN THE SUPERIOR COURT OF JUSTICE. Between Nordvale Holdings Inc. and Petra "
         "Osei-Bonsu. Order dated 14 March 2023: the respondent shall pay $482,000 in "
         "costs within 30 days. Justice R. Calloway presiding. File CV-23-00119284.")
DOC_B = ("IN THE FEDERAL COURT. Between Arden Maritime Ltd. and the Minister of Transport. "
         "Judgment dated 2 November 2024: the application for judicial review is dismissed "
         "with costs fixed at $17,450. Justice M. Thibodeau presiding. File T-1877-24.")


async def one(prompt, api_key):
    r = await mc._openai_complete_async(prompt, MODEL, schemas.EXTRACTION, api_key,
                                        2000, effort="low", base_url=BASE)
    u = r["usage"] or {}
    d = u.get("prompt_tokens_details") or {}
    return {"input": u.get("prompt_tokens", 0), "output": u.get("completion_tokens", 0),
            "read": d.get("cached_tokens", 0) or 0, "write": d.get("cache_write_tokens", 0) or 0,
            "cost": r["cost_usd"] or 0.0}


async def arm(label, api_key):
    nonce = uuid.uuid4().hex[:12]
    rows = []
    for tag, doc in (("call 1 (cold)", DOC_A), ("call 2 (shared prefix, new doc)", DOC_B)):
        rows.append((tag, await one(build(nonce, doc), api_key)))
        await asyncio.sleep(3)   # let the write land before the read
    # Classify against the prefix call 1 actually wrote, NOT against a percentage of input.
    # A ratio threshold is wrong here: these probe documents are ~80 tokens against a ~5,800-token
    # prefix, so a perfectly ordinary prefix hit reads 98.6% of input and any "< 90% = partial"
    # rule misreads it as a whole-prompt hit. The two are distinguished by what is left UNREAD:
    # a whole-prompt match leaves ~3 tokens (the signature of every archived luna hit), a prefix
    # match leaves the whole volatile block — and the documents differ, so call 2 has no
    # whole-prompt entry to match in the first place.
    prefix = rows[0][1]["write"]
    print(f"\n  {label}")
    for tag, m in rows:
        pct = (100.0 * m["read"] / m["input"]) if m["input"] else 0.0
        unread = m["input"] - m["read"]
        kind = ("no hit" if not m["read"]
                else "whole-prompt hit" if unread <= 5
                else f"PREFIX hit (matches the {prefix}-token prefix, {unread} unread)")
        print(f"    {tag:34} in={m['input']:>6}  read={m['read']:>6} ({pct:5.1f}%)  "
              f"write={m['write']:>6}  out={m['output']:>5}  ${m['cost']:.5f}  {kind}")
    return rows


async def main():
    api_key = auth.get_api_key("openai")
    if not api_key:
        sys.exit("No OpenAI key stored — run `watchdog auth` first.")

    print(f"\nProbe: {MODEL}, two arms x two calls. Each arm's prefix carries its own nonce.")
    fixed = await arm("ARM FIXED  (prompt_cache_breakpoint + explicit mode)", api_key)

    real = mc.catalog_cache_breakpoints
    mc.catalog_cache_breakpoints = lambda _mid: False      # pre-fix behaviour
    try:
        control = await arm("ARM CONTROL (flattened, no breakpoint — pre-fix)", api_key)
    finally:
        mc.catalog_cache_breakpoints = real

    (f1, f2), (c1, c2) = [r[1] for r in fixed], [r[1] for r in control]
    total = sum(m["cost"] for _t, m in fixed + control)
    print("\n  Verdict (call 2 of each arm — the one that can only hit on a shared prefix):")
    print(f"    fixed   read={f2['read']:>6} of {f2['input']:>6} input, "
          f"vs a {f1['write']}-token prefix written cold")
    print(f"    control read={c2['read']:>6} of {c2['input']:>6} input, "
          f"vs a {c1['write']}-token prefix written cold")
    # The fixed arm passes when call 2 read back exactly the prefix call 1 wrote, on a prompt
    # whose document differs — which no whole-prompt cache entry could serve.
    ok = f2["read"] == f1["write"] > 0 and f2["input"] - f2["read"] > 5
    print(f"\n  {'PASS' if ok else 'FAIL'}: fixed arm "
          f"{'read back exactly the prefix it wrote' if ok else 'did NOT produce a prefix hit'}"
          f"; control got {c2['read']}.")
    if f1["write"] and c1["write"]:
        print(f"  Explicit mode also narrowed the cold write: {f1['write']} tokens (marked prefix "
              f"only) vs {c1['write']} (whole prompt, implicit breakpoint) — at 1.25x, the "
              f"control's extra write is pure loss.")
    print(f"  Total spend: ${total:.5f}\n")


asyncio.run(main())
