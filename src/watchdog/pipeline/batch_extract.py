"""Message Batches API integration for bulk whole-document extraction (#214).

Batch mode submits every whole-document (non-sectioned) extraction request in one Anthropic
Message Batch — 50% off all token usage (stacking with the prompt caching wired in #213), at
the cost of asynchronous completion (typically under an hour, up to 24h). `orchestrate._run_batch`
is the sole caller; this module only knows the batch's own lifecycle (state, submit, status,
collect) — it never touches the vault or calls postflight/write_vault, matching the existing
preflight/postflight separation of concerns.

State is a single `.watchdog/registry/batch-pending.json` per vault — only one batch is ever in
flight at a time (mirroring `has_pending_finalization`'s "resolve the pending thing first"
precedent) — durable across interruption, mirroring the research URL worklist (D46).
"""

import datetime
import json
from pathlib import Path

from watchdog import model_client
from watchdog.pipeline import schemas

STATE_REL = Path(".watchdog") / "registry" / "batch-pending.json"


def state_path(vault: Path) -> Path:
    return vault / STATE_REL


def read_state(vault: Path) -> dict | None:
    """The pending batch's persisted state, or None if no batch is in flight."""
    p = state_path(vault)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_state(vault: Path, state: dict) -> None:
    p = state_path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def clear_state(vault: Path) -> None:
    state_path(vault).unlink(missing_ok=True)


def _client(api_key: str):
    import anthropic
    return anthropic.AsyncAnthropic(api_key=api_key)


async def submit(vault: Path, docs: list[dict], *, model: str, effort: str | None,
                 skills: dict[str, str], api_key: str) -> str:
    """Submit one Anthropic Message Batch covering every doc in `docs` (each
    `{"sha": ..., "prompt": [...content blocks...]}`, from `prompts.build_extract_prompt`),
    persist the batch's state, and return its id.

    `model` is a tier name or raw id (resolved via `model_client.resolve_model_id`); `effort`
    is the abstract per-stage intent (D36), mapped to Claude's native value the same way a
    single call would be. Reuses `model_client`'s task-budget/system-prompt constants directly
    rather than duplicating them — this is the same request shape `_api_complete_async` builds
    for a single call, just submitted as a batch of them.

    `skills` maps each sha to the record-skill label its prompt was built with (D144). One batch
    may mix skills — the skill is baked into that request's own prompt blocks, and the API treats
    each request independently — but collection runs in a later process, so the mapping has to be
    persisted rather than recomputed.
    """
    model_id = model_client.resolve_model_id(model)
    effort_arg = model_client._resolve_effort("anthropic", model_id, effort)
    output_config = {"format": {"type": "json_schema", "schema": schemas.EXTRACTION}}
    if effort_arg:
        output_config["effort"] = effort_arg
    max_tokens = model_client._TASK_MAX_TOKENS.get("extract", model_client._API_MAX_TOKENS)

    requests = [
        {
            "custom_id": d["sha"],
            "params": {
                "model": model_id,
                "max_tokens": max_tokens,
                "system": model_client._SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": d["prompt"]}],
                "output_config": output_config,
            },
        }
        for d in docs
    ]
    client = _client(api_key)
    batch = await client.messages.batches.create(requests=requests)
    write_state(vault, {
        "batch_id": batch.id,
        "shas": [d["sha"] for d in docs],
        "model": model_id,
        "effort": effort,
        "skills": dict(skills),
        "submitted_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    return batch.id


def _model_dump(obj) -> dict:
    """Coerce an SDK response object (pydantic model or plain dict) to a plain dict — the same
    defensive pattern `_api_complete_async` uses for `usage`."""
    if obj is None:
        return {}
    return obj.model_dump() if hasattr(obj, "model_dump") else dict(obj)


async def status(batch_id: str, api_key: str) -> dict:
    """Current `{"processing_status", "request_counts"}` for a submitted batch. Status starts
    `in_progress` and becomes `ended` once every request has finished (succeeded/errored/
    canceled/expired) and results are ready to collect."""
    client = _client(api_key)
    b = await client.messages.batches.retrieve(batch_id)
    return {"processing_status": b.processing_status, "request_counts": _model_dump(b.request_counts)}


async def collect(batch_id: str, api_key: str, model_id: str) -> dict[str, dict]:
    """Retrieve every result of an ended batch, keyed by sha (the `custom_id`). Each entry is
    `{"ok", "parsed", "usage", "cost_usd", "error"}` — the same JSON-extraction/schema-validation
    treatment a single call gets (`model_client._extract_json`/`_validate`), so the caller
    (`orchestrate._run_batch`) can feed a result straight into the same post-flight path used for
    any other extraction. `ok=True` only means schema-valid JSON came back — post-flight's own
    business-rule validation still runs afterward, exactly as it does for a synchronous call."""
    client = _client(api_key)
    out: dict[str, dict] = {}
    async for item in client.messages.batches.results(batch_id):
        sha = item.custom_id
        rtype = item.result.type
        if rtype != "succeeded":
            out[sha] = {"ok": False, "parsed": None, "usage": None, "cost_usd": None,
                       "error": f"batch result was '{rtype}', not 'succeeded'"}
            continue
        message = item.result.message
        text = next((b.text for b in message.content if getattr(b, "type", None) == "text"), "")
        parsed = model_client._extract_json(text)
        usage_dict = _model_dump(message.usage)
        cost = model_client._batch_cost(model_id, message.usage)
        if parsed is None:
            out[sha] = {"ok": False, "parsed": None, "usage": usage_dict, "cost_usd": cost,
                       "error": "batch response was not valid JSON"}
            continue
        errors = model_client._validate(parsed, schemas.EXTRACTION)
        out[sha] = {"ok": not errors, "parsed": parsed, "usage": usage_dict, "cost_usd": cost,
                   "error": "; ".join(errors[:3]) if errors else None}
    return out
