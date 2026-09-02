"""Batch API integration for bulk whole-document extraction (#214, #530).

Batch mode submits every whole-document (non-sectioned) extraction request in one provider batch
— 50% off token usage, stacking with prompt caching (#213) — at the cost of asynchronous
completion (typically under an hour, up to 24h). `orchestrate._run_batch` is the sole caller;
this module only knows the batch's own lifecycle (state, submit, status, collect), never the
vault, matching the preflight/postflight separation of concerns.

Two providers are wired behind `backend` (`submit`/`status`/`collect`'s dispatch): Anthropic's
Message Batches API (`claude-batch`, #214) and OpenAI's Batch API (`openai-batch`, #530, JSONL
over raw httpx, D37) — different enough shapes to be two real implementations, not a thin
wrapper. State is a single `.watchdog/registry/batch-pending.json` per vault (only one batch in
flight at a time), durable across interruption like the research URL worklist (D46); it records
which `backend` submitted it so a later `watchdog dig` resumes against the right provider
(state from before this field existed defaults to `claude-batch` on read)."""

import datetime
import json
from pathlib import Path

from watchdog import model_client
from watchdog.model_catalog import catalog_cache_breakpoints, catalog_needs_thinking_param
from watchdog.pipeline import schemas, section
from watchdog.pipeline.json_io import _read_json_or

STATE_REL = Path(".watchdog") / "registry" / "batch-pending.json"
_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


def state_path(vault: Path) -> Path:
    return vault / STATE_REL


def read_state(vault: Path) -> dict | None:
    """The pending batch's persisted state, or None if no batch is in flight."""
    return _read_json_or(state_path(vault), None)


def write_state(vault: Path, state: dict) -> None:
    p = state_path(vault)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def clear_state(vault: Path) -> None:
    state_path(vault).unlink(missing_ok=True)


def est_prompt_tokens(docs: list[dict]) -> dict[str, int]:
    """`{sha: chars/4 estimate of that doc's whole rendered prompt}`, persisted in the batch state
    at submit time (#617). The live path computes this inside `_call_model`, which has the
    rendered prompt in scope; a batch doesn't, since collection happens in a *later* invocation
    with only the parsed result and usage left — without carrying it across, a vault extracting
    exclusively via batch could never calibrate its tokenizer ratio
    (`ingest_setup._model_tokenizer_calibration`). Mirrors `_call_model`'s own normalization
    (string or Anthropic content-block list, A1) so a batch record's estimate is comparable to a
    live one's."""
    return {d["sha"]: section.est_tokens(
        d["prompt"] if isinstance(d["prompt"], str) else json.dumps(d["prompt"], sort_keys=True))
        for d in docs}


def _client(api_key: str):
    import anthropic
    return anthropic.AsyncAnthropic(api_key=api_key)


# ── public dispatch ────────────────────────────────────────────────────────────

async def submit(vault: Path, docs: list[dict], *, model: str, effort: str | None,
                 skills: dict[str, str], api_key: str, backend: str = "claude-batch") -> str:
    """Submit one batch covering every doc in `docs` (each `{"sha": ..., "prompt": [...content
    blocks...]}`, from `prompts.build_extract_prompt`), persist the batch's state (including
    `backend`, so a later resume knows which provider to poll), and return its id. Dispatches to
    the provider named by `backend` — see `_anthropic_submit`/`_openai_submit`."""
    if model_client.provider_for_backend(backend) == "openai":
        return await _openai_submit(vault, docs, model=model, effort=effort, skills=skills,
                                    api_key=api_key, backend=backend)
    return await _anthropic_submit(vault, docs, model=model, effort=effort, skills=skills,
                                   api_key=api_key, backend=backend)


async def status(batch_id: str, api_key: str, backend: str = "claude-batch") -> dict:
    """Current `{"processing_status", "request_counts", "created_at", "ended_at"}` for a
    submitted batch, normalised the same way regardless of provider — `processing_status` is
    `"in_progress"` until every request has finished, then `"ended"` and ready to collect."""
    if model_client.provider_for_backend(backend) == "openai":
        return await _openai_status(batch_id, api_key)
    return await _anthropic_status(batch_id, api_key)


async def collect(batch_id: str, api_key: str, model_id: str, backend: str = "claude-batch") -> dict[str, dict]:
    """Retrieve every result of an ended batch, keyed by sha (the `custom_id`) — same
    `{"ok", "parsed", "usage", "cost_usd", "error"}` shape regardless of provider, so the caller
    (`orchestrate._run_batch`) can feed a result straight into the same post-flight path used for
    any other extraction."""
    if model_client.provider_for_backend(backend) == "openai":
        return await _openai_collect(batch_id, api_key, model_id)
    return await _anthropic_collect(batch_id, api_key, model_id)


# ── Anthropic: Message Batches API (#214) ───────────────────────────────────────

async def _anthropic_submit(vault: Path, docs: list[dict], *, model: str, effort: str | None,
                            skills: dict[str, str], api_key: str, backend: str) -> str:
    """`model`/`effort` follow the same tier-resolution and abstract-intent mapping (D36) a
    single call would use; this is the same request shape `_api_complete_async` builds, just
    submitted as a batch. `skills` maps each sha to the record-skill label its prompt was built
    with (D144) and must be persisted, since one batch may mix skills but collection runs in a
    later process with no prompt left to inspect. Sends `thinking` per request on the same gate
    the live and agent-sdk paths use (#635, D206) — this path was the one left off that gate
    until #643, so a thinking-default-off model extracted via `claude-batch` never actually
    reasoned even after #635 shipped."""
    model_id = model_client.resolve_model_id(model)
    effort_arg = model_client._resolve_effort("anthropic", model_id, effort)
    output_config = {"format": {"type": "json_schema", "schema": schemas.EXTRACTION}}
    if effort_arg:
        output_config["effort"] = effort_arg
    # The per-model wire envelope (#598) — not `_wire_max_tokens`'s streaming-guard rationale:
    # the Batches API is asynchronous and holds no connection open, so the non-streaming timeout
    # guard that shapes the synchronous claude-api path never applied here in the first place.
    # `_output_envelope` directly rather than `_wire_max_tokens`: the only thing the latter adds is
    # the DeepSeek `-thinking` strip, and there is no DeepSeek batch path (anthropic/openai only).
    max_tokens = model_client._output_envelope(model_id)
    params: dict = {
        "model": model_id,
        "max_tokens": max_tokens,
        "system": model_client._SYSTEM_PROMPT,
        "output_config": output_config,
    }
    if catalog_needs_thinking_param(model_id):
        params["thinking"] = model_client._THINKING_ADAPTIVE

    requests = [
        {
            "custom_id": d["sha"],
            "params": {**params, "messages": [{"role": "user", "content": d["prompt"]}]},
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
        "est_prompt_tokens": est_prompt_tokens(docs),
        "backend": backend,
        "submitted_at": datetime.datetime.now(datetime.timezone.utc).strftime(_TS_FMT),
    })
    return batch.id


def _model_dump(obj) -> dict:
    """Coerce an SDK response object (pydantic model or plain dict) to a plain dict — the same
    defensive pattern `_api_complete_async` uses for `usage`."""
    if obj is None:
        return {}
    return obj.model_dump() if hasattr(obj, "model_dump") else dict(obj)


def _iso(dt: datetime.datetime | None) -> str | None:
    """A `MessageBatch` timestamp in the same string shape `write_state` stamps `submitted_at`
    with, so the two are directly comparable. `None` (before processing ends) stays `None`
    rather than a placeholder string, so it can't be mistaken for a real timestamp."""
    return dt.strftime(_TS_FMT) if dt is not None else None


async def _anthropic_status(batch_id: str, api_key: str) -> dict:
    """Status starts `in_progress` and becomes `ended` once every request has finished
    (succeeded/errored/canceled/expired) and results are ready to collect. `ended_at` is None
    until then — Anthropic's own record of when processing finished, distinct from
    `submitted_at` (this run's own clock, in the persisted batch state) and from whenever a
    *later* run happens to notice `ended` and calls `collect` (D52's submit-and-exit design
    means those two moments routinely differ by hours)."""
    client = _client(api_key)
    b = await client.messages.batches.retrieve(batch_id)
    return {"processing_status": b.processing_status, "request_counts": _model_dump(b.request_counts),
           "created_at": _iso(b.created_at), "ended_at": _iso(b.ended_at)}


def _batch_error_text(err) -> str:
    """Human string for a `MessageBatchErroredResult.error` (an `ErrorResponse` wrapping an
    `ErrorObject` variant — every variant carries `type`/`message`) — falls back to `str(err)`
    if the SDK's shape doesn't match what's expected here, since collection must never crash
    over an error shape it didn't anticipate."""
    inner = getattr(err, "error", None)
    msg = getattr(inner, "message", None) if inner is not None else None
    etype = getattr(inner, "type", None) if inner is not None else None
    if msg:
        return f"{etype}: {msg}" if etype else msg
    return str(err)


async def _anthropic_collect(batch_id: str, api_key: str, model_id: str) -> dict[str, dict]:
    """Each entry is the same JSON-extraction/schema-validation treatment a single call gets
    (`model_client._extract_json`/`_validate`). `ok=True` only means schema-valid JSON came back
    — post-flight's own business-rule validation still runs afterward, exactly as it does for
    any other extraction.

    A succeeded item's `usage` carries `stop_reason` alongside the token counts (D125-style
    truncation visibility — a batch call has no continuation/repair path the way a live call
    does, so `"max_tokens"` here is the only signal that a result may be an incomplete JSON
    fragment rather than genuinely malformed). An `errored` item's `error` is the real reason
    Anthropic gave, not a generic "wasn't succeeded" placeholder — `canceled`/`expired` have no
    further detail to give, so those stay generic."""
    client = _client(api_key)
    out: dict[str, dict] = {}
    result_stream = await client.messages.batches.results(batch_id)
    async for item in result_stream:
        sha = item.custom_id
        rtype = item.result.type
        if rtype != "succeeded":
            reason = (f"batch result errored: {_batch_error_text(item.result.error)}"
                      if rtype == "errored" else f"batch result was '{rtype}', not 'succeeded'")
            out[sha] = {"ok": False, "parsed": None, "usage": None, "cost_usd": None,
                       "error": reason}
            continue
        message = item.result.message
        text = next((b.text for b in message.content if getattr(b, "type", None) == "text"), "")
        parsed = model_client._extract_json(text)
        usage_dict = model_client._fold_in_anthropic_thinking(_model_dump(message.usage))
        stop_reason = getattr(message, "stop_reason", None)
        if stop_reason:
            usage_dict["stop_reason"] = stop_reason
        cost = model_client._batch_cost(model_id, message.usage)
        if parsed is None:
            out[sha] = {"ok": False, "parsed": None, "usage": usage_dict, "cost_usd": cost,
                       "error": "batch response was not valid JSON"}
            continue
        errors = model_client._validate(parsed, schemas.EXTRACTION)
        out[sha] = {"ok": not errors, "parsed": parsed, "usage": usage_dict, "cost_usd": cost,
                   "error": "; ".join(errors[:3]) if errors else None}
    return out


# ── OpenAI: Batch API (#530) ─────────────────────────────────────────────────────
#
# https://platform.openai.com/docs/guides/batch — a JSONL file of `{"custom_id", "method", "url",
# "body"}` lines is uploaded via `/v1/files` (purpose "batch"), then `/v1/batches` is created
# against that file id. Each `body` is the same Chat Completions shape
# `model_client._openai_complete_async` builds for a single live call — same
# response-format/reasoning-model/max-token handling — so a batch prices and behaves identically
# to a live call against the same model, just at half price. No new SDK dependency, matching D37's
# choice for the live OpenAI-compatible path: raw httpx over the OS trust store (truststore).

_OPENAI_BATCH_TERMINAL = {"completed", "failed", "expired", "cancelled"}


def _openai_ssl_context():
    import ssl
    import truststore
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def _openai_headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}"}


def _openai_request_body(model_id: str, prompt, max_tokens: int, effort: str | None,
                         response_format: dict) -> dict:
    """One Chat Completions request body — the same construction
    `model_client._openai_complete_async` does for a single call, minus the parts that don't
    apply to a batch line (no retry/pagination, no `prefix` continuation — OpenAI never supports
    that even live, `_BACKEND_META["openai"].supports_continuation` is False)."""
    # Explicit cache breakpoints on the GPT-5.6 family and later (#586, D195), exactly as the live
    # path does — without them that family never falls back to a longest matching prefix, so the
    # skill-label ordering below has nothing to pay off against. Always the real OpenAI endpoint
    # here (this builds an OpenAI Batch API line), so no base-url gate is needed.
    cache_blocks = (model_client._openai_cache_blocks(prompt)
                    if catalog_cache_breakpoints(model_id) else None)
    messages = [
        {"role": "system", "content": model_client._SYSTEM_PROMPT},
        {"role": "user", "content": cache_blocks if cache_blocks is not None
                                   else model_client._flatten_prompt(prompt)},
    ]
    body = {"model": model_id, "messages": messages, "response_format": response_format}
    if cache_blocks is not None:
        body["prompt_cache_options"] = {"mode": "explicit"}
    # Same missing parameter as the live path (#562) — this is what makes the "requests sorted
    # by skill label so adjacent same-skill ones share the cached prefix" ordering actually pay
    # off on OpenAI's Batch API.
    cache_key = model_client._prompt_cache_key(prompt)
    if cache_key is not None:
        body["prompt_cache_key"] = cache_key
    if model_client._openai_is_reasoning(model_id):
        body["max_completion_tokens"] = max_tokens
    else:
        body["max_tokens"] = max_tokens
    if effort:
        body["reasoning_effort"] = effort
    return body


async def _openai_submit(vault: Path, docs: list[dict], *, model: str, effort: str | None,
                         skills: dict[str, str], api_key: str, backend: str) -> str:
    """Build one JSONL file (one Chat Completions request per doc), upload it, then create the
    batch against it. `model` is a raw OpenAI model id (no tier resolution — `resolve_model_id`
    passes an uncatalogued id through unchanged, same as every other OpenAI-routed call)."""
    import httpx

    model_id = model_client.resolve_model_id(model)
    effort_arg = model_client._resolve_effort("openai", model_id, effort)
    # The per-model wire envelope (#598) — task/effort-independent, so `effort_arg` is threaded
    # through below only for the request body's own `reasoning_effort` field, not for sizing
    # `max_tokens`.
    # `_output_envelope` directly rather than `_wire_max_tokens`, for the same reason as the
    # Anthropic path above: no DeepSeek batch path exists, so the `-thinking` strip is moot.
    max_tokens = model_client._output_envelope(model_id)
    response_format = model_client._openai_response_format(
        model_client._OPENAI_BASE["openai"], schemas.EXTRACTION)

    lines = []
    for d in docs:
        body = _openai_request_body(model_id, d["prompt"], max_tokens, effort_arg, response_format)
        lines.append(json.dumps({"custom_id": d["sha"], "method": "POST",
                                 "url": "/v1/chat/completions", "body": body}))
    jsonl = ("\n".join(lines) + "\n").encode("utf-8")

    base = model_client._OPENAI_BASE["openai"]
    headers = _openai_headers(api_key)
    async with httpx.AsyncClient(timeout=600, verify=_openai_ssl_context()) as client:
        upload = await client.post(f"{base}/files", headers=headers,
                                   data={"purpose": "batch"},
                                   files={"file": ("batch.jsonl", jsonl, "application/jsonl")})
        upload.raise_for_status()
        file_id = upload.json()["id"]
        created = await client.post(f"{base}/batches", headers=headers,
                                    json={"input_file_id": file_id,
                                          "endpoint": "/v1/chat/completions",
                                          "completion_window": "24h"})
        created.raise_for_status()
    batch = created.json()

    write_state(vault, {
        "batch_id": batch["id"],
        "shas": [d["sha"] for d in docs],
        "model": model_id,
        "effort": effort,
        "skills": dict(skills),
        "est_prompt_tokens": est_prompt_tokens(docs),
        "backend": backend,
        "submitted_at": datetime.datetime.now(datetime.timezone.utc).strftime(_TS_FMT),
    })
    return batch["id"]


def _openai_unix_iso(ts: int | None) -> str | None:
    if ts is None:
        return None
    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).strftime(_TS_FMT)


async def _openai_status(batch_id: str, api_key: str) -> dict:
    """OpenAI's own `status` (`validating`/`in_progress`/`finalizing`/`completed`/`failed`/
    `expired`/`cancelling`/`cancelled`) collapses to `processing_status`'s two-value contract —
    anything not yet terminal is `"in_progress"`; any terminal state, including a failed/expired/
    cancelled batch, is `"ended"` — those still need collecting so each document gets a real
    failure reason rather than being silently dropped. `request_counts`
    (`{"total", "completed", "failed"}`) is remapped to Anthropic's `{"processing", "succeeded",
    "errored"}` keys so `orchestrate._resume_batch`'s progress notice stays provider-agnostic."""
    import httpx

    base = model_client._OPENAI_BASE["openai"]
    async with httpx.AsyncClient(timeout=60, verify=_openai_ssl_context()) as client:
        resp = await client.get(f"{base}/batches/{batch_id}", headers=_openai_headers(api_key))
    resp.raise_for_status()
    b = resp.json()

    counts = b.get("request_counts") or {}
    total, completed, failed = counts.get("total", 0), counts.get("completed", 0), counts.get("failed", 0)
    ended_at = (b.get("completed_at") or b.get("failed_at") or b.get("expired_at")
               or b.get("cancelled_at"))
    return {
        "processing_status": "ended" if b.get("status") in _OPENAI_BATCH_TERMINAL else "in_progress",
        "request_counts": {"processing": max(total - completed - failed, 0),
                          "succeeded": completed, "errored": failed},
        "created_at": _openai_unix_iso(b.get("created_at")),
        "ended_at": _openai_unix_iso(ended_at),
    }


async def _openai_collect(batch_id: str, api_key: str, model_id: str) -> dict[str, dict]:
    """Retrieve a terminal batch's `output_file_id` (succeeded requests) and `error_file_id`
    (requests OpenAI rejected before ever reaching the model — a malformed body, say) and parse
    both JSONL files, keyed by `custom_id`. A denied-at-validation batch (OpenAI `status`
    `"failed"`) can still carry an `error_file_id` with a real per-document reason, collected the
    same way a per-item error would be.

    Each output line's `response.body` is a normal Chat Completions response — `choices[0]
    .message.content` is denormalised (D151's strict-mode null-for-omitted-field convention,
    undone via `model_client._denormalize_strict_json`) before the shared
    `_extract_json`/`_validate` path sees it, exactly like the live single-call OpenAI path.
    `usage` is `_openai_cost`'s own expected shape (`prompt_tokens`/`completion_tokens`/
    `prompt_tokens_details.cached_tokens`), so `model_client._openai_batch_cost` prices it
    directly with no reshaping."""
    import httpx

    base = model_client._OPENAI_BASE["openai"]
    headers = _openai_headers(api_key)
    out: dict[str, dict] = {}

    async with httpx.AsyncClient(timeout=120, verify=_openai_ssl_context()) as client:
        resp = await client.get(f"{base}/batches/{batch_id}", headers=headers)
        resp.raise_for_status()
        b = resp.json()

        async def _collect_file(file_id: str, *, is_error_file: bool) -> None:
            r = await client.get(f"{base}/files/{file_id}/content", headers=headers)
            r.raise_for_status()
            for line in r.text.splitlines():
                if not line.strip():
                    continue
                item = json.loads(line)
                sha = item.get("custom_id")
                if sha is None:
                    continue
                err = item.get("error")
                response = item.get("response") or {}
                if is_error_file or err or response.get("status_code") != 200:
                    reason = None
                    if isinstance(err, dict):
                        reason = err.get("message")
                    if not reason:
                        reason = ((response.get("body") or {}).get("error") or {}).get("message")
                    out[sha] = {"ok": False, "parsed": None, "usage": None, "cost_usd": None,
                               "error": reason or "batch request failed"}
                    continue
                resp_body = response.get("body") or {}
                choices = resp_body.get("choices") or []
                text = (choices[0].get("message", {}).get("content") or "") if choices else ""
                text = model_client._denormalize_strict_json(text)
                parsed = model_client._extract_json(text)
                usage_dict = dict(resp_body.get("usage") or {})
                finish_reason = choices[0].get("finish_reason") if choices else None
                if finish_reason:
                    usage_dict["stop_reason"] = finish_reason
                cost = model_client._openai_batch_cost(model_id, resp_body.get("usage"))
                if parsed is None:
                    out[sha] = {"ok": False, "parsed": None, "usage": usage_dict, "cost_usd": cost,
                               "error": "batch response was not valid JSON"}
                    continue
                errors = model_client._validate(parsed, schemas.EXTRACTION)
                out[sha] = {"ok": not errors, "parsed": parsed, "usage": usage_dict, "cost_usd": cost,
                           "error": "; ".join(errors[:3]) if errors else None}

        if b.get("output_file_id"):
            await _collect_file(b["output_file_id"], is_error_file=False)
        if b.get("error_file_id"):
            await _collect_file(b["error_file_id"], is_error_file=True)

    return out
