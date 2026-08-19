"""Tests for the Batch API integrations (#214, #530) — state persistence, submit/status/collect,
and the per-provider dispatch between them. The Anthropic SDK client is mocked at the `_client`
boundary (matching how test_model_client.py mocks _ABACKENDS rather than the real, uninstalled
`anthropic` package); the OpenAI path is mocked at `httpx.AsyncClient` (matching
test_model_client.py's own OpenAI-compatible-backend tests), since it speaks raw HTTP rather than
using an SDK."""

import asyncio
import datetime
import json

import pytest

from watchdog import model_client
from watchdog.pipeline import batch_extract as be
from watchdog.pipeline import schemas


def make_vault(tmp_path):
    vault = tmp_path / "vault"
    (vault / ".watchdog" / "registry").mkdir(parents=True)
    return vault


# ── state persistence ────────────────────────────────────────────────────────

def test_state_round_trip(tmp_path):
    vault = make_vault(tmp_path)
    assert be.read_state(vault) is None
    be.write_state(vault, {"batch_id": "b1", "shas": ["a", "b"]})
    assert be.read_state(vault) == {"batch_id": "b1", "shas": ["a", "b"]}
    be.clear_state(vault)
    assert be.read_state(vault) is None


def test_clear_state_missing_is_a_noop(tmp_path):
    be.clear_state(make_vault(tmp_path))   # no error


def test_read_state_tolerates_corrupt_json(tmp_path):
    vault = make_vault(tmp_path)
    be.state_path(vault).write_text("not json")
    assert be.read_state(vault) is None


# ── whole-prompt token estimate carried across the batch boundary (#617) ─────

def test_est_prompt_tokens_measures_whole_prompt_per_sha():
    """#617: the estimate is taken at submit time, while the rendered prompt still exists — the
    collect pass runs in a later invocation with only the parsed result and its usage in hand."""
    docs = [{"sha": "aaa", "prompt": "x" * 400}, {"sha": "bbb", "prompt": "y" * 40}]
    assert be.est_prompt_tokens(docs) == {"aaa": 100, "bbb": 10}


def test_est_prompt_tokens_handles_anthropic_content_blocks():
    """A prompt may be a list of content blocks (A1, cache_control) rather than a string. Measured
    through the same `json.dumps` normalization `orchestrate._call_model` applies, so a batch
    record's estimate lands on the same scale as a live call's rather than its own."""
    blocks = [{"type": "text", "text": "hello"}, {"type": "text", "text": "world"}]
    expected = len(json.dumps(blocks, sort_keys=True)) // 4
    assert be.est_prompt_tokens([{"sha": "aaa", "prompt": blocks}]) == {"aaa": expected}
    assert expected > 0


# ── fakes for the SDK boundary ───────────────────────────────────────────────

class _Obj:
    """A tiny attribute-bag standing in for pydantic SDK response objects."""
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def model_dump(self):
        return dict(self.__dict__)


class _FakeResultPage:
    """Stands in for the SDK's `AsyncJSONLDecoder` — an object `results()` resolves to once
    awaited, itself async-iterable. Distinct from `results()` being an async generator (the
    shape this fake used to have), since that shape is directly iterable the moment it's called
    and so hid the missing `await` in `batch_extract.collect` (the real SDK requires it)."""
    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)


class FakeBatches:
    def __init__(self, batch_id="batch_123", processing_status="ended",
                request_counts=None, results=None, created_at=None, ended_at=None):
        self.batch_id = batch_id
        self.processing_status = processing_status
        self.request_counts = request_counts or {}
        self._results = results or []
        self.created_at = created_at
        self.ended_at = ended_at
        self.create_calls = []

    async def create(self, *, requests):
        self.create_calls.append(requests)
        return _Obj(id=self.batch_id)

    async def retrieve(self, batch_id):
        return _Obj(processing_status=self.processing_status, request_counts=self.request_counts,
                    created_at=self.created_at, ended_at=self.ended_at)

    async def results(self, batch_id):
        return _FakeResultPage(self._results)


class FakeClient:
    def __init__(self, batches):
        self.messages = _Obj(batches=batches)


def _succeeded(custom_id, obj: dict, *, input_tokens=100, output_tokens=20, stop_reason="end_turn"):
    message = _Obj(
        content=[_Obj(type="text", text=json.dumps(obj))],
        usage=_Obj(input_tokens=input_tokens, output_tokens=output_tokens,
                  cache_creation_input_tokens=0, cache_read_input_tokens=0),
        stop_reason=stop_reason,
    )
    return _Obj(custom_id=custom_id, result=_Obj(type="succeeded", message=message))


def _not_succeeded(custom_id, rtype):
    return _Obj(custom_id=custom_id, result=_Obj(type=rtype, message=None))


def _errored(custom_id, error_type, error_message):
    err = _Obj(error=_Obj(type=error_type, message=error_message), type="error")
    return _Obj(custom_id=custom_id, result=_Obj(type="errored", error=err))


VALID_EXTRACTION = {
    "document": {"title": "Acme AR", "document_type": "Annual Report",
                "date_of_document": None, "summary": "s", "key_facts": []},
    "entities": [], "morgue_entity_id": "acme-corp",
    "scratchpad": "",
}


# ── submit ────────────────────────────────────────────────────────────────────

def test_submit_builds_one_request_per_doc_and_persists_state(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    fake = FakeBatches(batch_id="batch_abc")
    monkeypatch.setattr(be, "_client", lambda api_key: FakeClient(fake))

    docs = [{"sha": "sha1", "prompt": [{"type": "text", "text": "p1"}]},
           {"sha": "sha2", "prompt": [{"type": "text", "text": "p2"}]}]
    batch_id = asyncio.run(be.submit(vault, docs, model="sonnet", effort=None,
                                     skills={"sha1": "annual-report", "sha2": "bankruptcy"},
                                     api_key="sk-x"))

    assert batch_id == "batch_abc"
    reqs = fake.create_calls[0]
    assert [r["custom_id"] for r in reqs] == ["sha1", "sha2"]
    assert reqs[0]["params"]["model"] == "claude-sonnet-4-6"   # tier resolved to a real id
    assert reqs[0]["params"]["messages"][0]["content"] == [{"type": "text", "text": "p1"}]
    assert "output_config" in reqs[0]["params"]

    state = be.read_state(vault)
    assert state["batch_id"] == "batch_abc"
    assert state["shas"] == ["sha1", "sha2"]
    # One batch may mix skills (D144) — the mapping is persisted per sha, since collection
    # runs in a later process that has no other way to rebuild each document's prompt.
    assert state["skills"] == {"sha1": "annual-report", "sha2": "bankruptcy"}
    assert state["model"] == "claude-sonnet-4-6"
    # Same reasoning as `skills` above, for the whole-prompt token estimate (#617): the prompt is
    # gone by collection time, so the estimate the tokenizer calibration needs is stashed here.
    assert state["est_prompt_tokens"] == be.est_prompt_tokens(docs)
    assert set(state["est_prompt_tokens"]) == {"sha1", "sha2"}


def test_submit_passes_resolved_effort(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    fake = FakeBatches()
    monkeypatch.setattr(be, "_client", lambda api_key: FakeClient(fake))
    docs = [{"sha": "sha1", "prompt": [{"type": "text", "text": "p"}]}]
    asyncio.run(be.submit(vault, docs, model="sonnet", effort="medium",
                          skills={"sha1": "s"}, api_key="sk-x"))
    assert fake.create_calls[0][0]["params"]["output_config"]["effort"] == "medium"


def test_submit_omits_effort_when_high_ie_default(tmp_path, monkeypatch):
    vault = make_vault(tmp_path)
    fake = FakeBatches()
    monkeypatch.setattr(be, "_client", lambda api_key: FakeClient(fake))
    docs = [{"sha": "sha1", "prompt": [{"type": "text", "text": "p"}]}]
    asyncio.run(be.submit(vault, docs, model="sonnet", effort="high",
                          skills={"sha1": "s"}, api_key="sk-x"))
    assert "effort" not in fake.create_calls[0][0]["params"]["output_config"]


def test_submit_sends_thinking_for_a_model_that_defaults_off(tmp_path, monkeypatch):
    """#643: `claude-sonnet-4-6` ships thinking off by default (catalog `thinking: true`) — the
    live and agent-sdk paths already send it explicitly (#635, D206); this batch path was the one
    Claude-calling caller left off that gate, so a batch extraction never actually reasoned."""
    vault = make_vault(tmp_path)
    fake = FakeBatches()
    monkeypatch.setattr(be, "_client", lambda api_key: FakeClient(fake))
    docs = [{"sha": "sha1", "prompt": [{"type": "text", "text": "p"}]}]
    asyncio.run(be.submit(vault, docs, model="sonnet", effort=None,
                          skills={"sha1": "s"}, api_key="sk-x"))
    assert fake.create_calls[0][0]["params"]["thinking"] == model_client._THINKING_ADAPTIVE


def test_submit_omits_thinking_for_a_model_that_defaults_on(tmp_path, monkeypatch):
    """`sonnet-5` ships thinking on by default (no catalog `thinking` flag) — sending the param
    explicitly is unnecessary and, unlike the live path, has no continuation call here to worry
    about conflicting with; the gate should still say no."""
    vault = make_vault(tmp_path)
    fake = FakeBatches()
    monkeypatch.setattr(be, "_client", lambda api_key: FakeClient(fake))
    docs = [{"sha": "sha1", "prompt": [{"type": "text", "text": "p"}]}]
    asyncio.run(be.submit(vault, docs, model="sonnet-5", effort=None,
                          skills={"sha1": "s"}, api_key="sk-x"))
    assert "thinking" not in fake.create_calls[0][0]["params"]


# ── status ────────────────────────────────────────────────────────────────────

def test_status_reports_processing_state(monkeypatch):
    fake = FakeBatches(processing_status="in_progress",
                       request_counts={"processing": 5, "succeeded": 3})
    monkeypatch.setattr(be, "_client", lambda api_key: FakeClient(fake))
    s = asyncio.run(be.status("batch_abc", "sk-x"))
    assert s["processing_status"] == "in_progress"
    assert s["request_counts"] == {"processing": 5, "succeeded": 3}


def test_status_reports_created_and_ended_at(monkeypatch):
    created = datetime.datetime(2026, 7, 29, 2, 54, 46, tzinfo=datetime.timezone.utc)
    ended = datetime.datetime(2026, 7, 29, 3, 36, 2, tzinfo=datetime.timezone.utc)
    fake = FakeBatches(processing_status="ended", created_at=created, ended_at=ended)
    monkeypatch.setattr(be, "_client", lambda api_key: FakeClient(fake))
    s = asyncio.run(be.status("batch_abc", "sk-x"))
    assert s["created_at"] == "2026-07-29T02:54:46Z"
    assert s["ended_at"] == "2026-07-29T03:36:02Z"


def test_status_ended_at_is_none_while_still_processing(monkeypatch):
    fake = FakeBatches(processing_status="in_progress", ended_at=None)
    monkeypatch.setattr(be, "_client", lambda api_key: FakeClient(fake))
    s = asyncio.run(be.status("batch_abc", "sk-x"))
    assert s["ended_at"] is None


# ── collect ───────────────────────────────────────────────────────────────────

def test_collect_maps_succeeded_results_by_sha(monkeypatch):
    results = [_succeeded("sha1", VALID_EXTRACTION), _succeeded("sha2", VALID_EXTRACTION)]
    fake = FakeBatches(results=results)
    monkeypatch.setattr(be, "_client", lambda api_key: FakeClient(fake))

    out = asyncio.run(be.collect("batch_abc", "sk-x", "claude-sonnet-4-6"))
    assert set(out) == {"sha1", "sha2"}
    assert out["sha1"]["ok"] is True
    assert out["sha1"]["parsed"]["morgue_entity_id"] == "acme-corp"
    assert out["sha1"]["cost_usd"] > 0
    assert out["sha1"]["error"] is None


def test_collect_prices_at_half_the_standard_rate(monkeypatch):
    results = [_succeeded("sha1", VALID_EXTRACTION, input_tokens=1_000_000, output_tokens=0)]
    fake = FakeBatches(results=results)
    monkeypatch.setattr(be, "_client", lambda api_key: FakeClient(fake))
    out = asyncio.run(be.collect("batch_abc", "sk-x", "claude-sonnet-4-6"))
    assert out["sha1"]["cost_usd"] == pytest.approx(1.5)   # $3/MTok input, batch halves it


@pytest.mark.parametrize("rtype", ["canceled", "expired"])
def test_collect_reports_non_succeeded_results(monkeypatch, rtype):
    fake = FakeBatches(results=[_not_succeeded("sha1", rtype)])
    monkeypatch.setattr(be, "_client", lambda api_key: FakeClient(fake))
    out = asyncio.run(be.collect("batch_abc", "sk-x", "claude-sonnet-4-6"))
    assert out["sha1"]["ok"] is False
    assert rtype in out["sha1"]["error"]


def test_collect_surfaces_the_real_reason_for_an_errored_result(monkeypatch):
    """An `errored` result carries Anthropic's actual reason (ErrorResponse.error.{type,
    message}) — collapsing it to a generic "wasn't succeeded" string (the old behaviour, still
    correct for canceled/expired, which carry no further detail) throws away real debugging
    signal a live call's ModelError would have kept."""
    fake = FakeBatches(results=[_errored("sha1", "invalid_request_error", "prompt too long")])
    monkeypatch.setattr(be, "_client", lambda api_key: FakeClient(fake))
    out = asyncio.run(be.collect("batch_abc", "sk-x", "claude-sonnet-4-6"))
    assert out["sha1"]["ok"] is False
    assert "invalid_request_error" in out["sha1"]["error"]
    assert "prompt too long" in out["sha1"]["error"]


def test_collect_includes_stop_reason_in_usage(monkeypatch):
    results = [_succeeded("sha1", VALID_EXTRACTION, stop_reason="max_tokens")]
    fake = FakeBatches(results=results)
    monkeypatch.setattr(be, "_client", lambda api_key: FakeClient(fake))
    out = asyncio.run(be.collect("batch_abc", "sk-x", "claude-sonnet-4-6"))
    assert out["sha1"]["usage"]["stop_reason"] == "max_tokens"


def test_collect_surfaces_anthropic_reasoning_tokens(monkeypatch):
    """Same bug as _api_complete_async's direct-call path: a thinking-enabled batch result's
    usage carries thinking tokens under output_tokens_details.thinking_tokens, a different key
    than the completion_tokens_details.reasoning_tokens shape every reader here checks."""
    message = _Obj(
        content=[_Obj(type="text", text=json.dumps(VALID_EXTRACTION))],
        # output_tokens_details is a plain dict here, not an _Obj: real pydantic's model_dump()
        # recursively serializes a nested model into a dict, which _Obj's shallow fake doesn't
        # replicate — a dict literal matches what _fold_in_anthropic_thinking actually receives.
        usage=_Obj(input_tokens=9408, output_tokens=1500,
                  cache_creation_input_tokens=0, cache_read_input_tokens=0,
                  output_tokens_details={"thinking_tokens": 1200}),
        stop_reason="end_turn",
    )
    results = [_Obj(custom_id="sha1", result=_Obj(type="succeeded", message=message))]
    fake = FakeBatches(results=results)
    monkeypatch.setattr(be, "_client", lambda api_key: FakeClient(fake))
    out = asyncio.run(be.collect("batch_abc", "sk-x", "claude-sonnet-4-6"))
    assert out["sha1"]["usage"]["completion_tokens_details"]["reasoning_tokens"] == 1200


def test_collect_flags_unparseable_text_without_crashing(monkeypatch):
    fake = FakeBatches(results=[
        _Obj(custom_id="sha1", result=_Obj(
            type="succeeded",
            message=_Obj(content=[_Obj(type="text", text="not json at all")],
                        usage=_Obj(input_tokens=1, output_tokens=1,
                                  cache_creation_input_tokens=0, cache_read_input_tokens=0)))),
    ])
    monkeypatch.setattr(be, "_client", lambda api_key: FakeClient(fake))
    out = asyncio.run(be.collect("batch_abc", "sk-x", "claude-sonnet-4-6"))
    assert out["sha1"]["ok"] is False
    assert out["sha1"]["parsed"] is None


def test_collect_flags_schema_invalid_json_without_crashing(monkeypatch):
    bad = dict(VALID_EXTRACTION)
    del bad["morgue_entity_id"]   # required by EXTRACTION
    fake = FakeBatches(results=[_succeeded("sha1", bad)])
    monkeypatch.setattr(be, "_client", lambda api_key: FakeClient(fake))
    out = asyncio.run(be.collect("batch_abc", "sk-x", "claude-sonnet-4-6"))
    assert out["sha1"]["ok"] is False
    assert out["sha1"]["parsed"] is not None   # kept for the caller's repair-retry context
    assert out["sha1"]["error"]


# ── dispatch (#530) ───────────────────────────────────────────────────────────

def test_submit_dispatches_to_anthropic_by_default(tmp_path, monkeypatch):
    """No `backend` kwarg — the pre-#530 call shape — still routes to the Anthropic path."""
    vault = make_vault(tmp_path)
    fake = FakeBatches(batch_id="batch_default")
    monkeypatch.setattr(be, "_client", lambda api_key: FakeClient(fake))
    docs = [{"sha": "sha1", "prompt": [{"type": "text", "text": "p"}]}]
    batch_id = asyncio.run(be.submit(vault, docs, model="sonnet", effort=None,
                                     skills={"sha1": "s"}, api_key="sk-x"))
    assert batch_id == "batch_default"
    assert be.read_state(vault)["backend"] == "claude-batch"


def test_submit_dispatches_to_openai_for_openai_batch_backend(tmp_path, monkeypatch):
    """Passing backend="openai-batch" must never touch the Anthropic `_client` boundary — a
    user with only an OpenAI key configured must not need Anthropic credentials at all (D37)."""
    vault = make_vault(tmp_path)

    def _unexpected_anthropic_client(api_key):
        raise AssertionError("openai-batch must not use the Anthropic client")
    monkeypatch.setattr(be, "_client", _unexpected_anthropic_client)

    import httpx
    fake = _FakeOpenAIHttp(batch_response={"id": "batch_oai_dispatch"})
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: fake)

    docs = [{"sha": "sha1", "prompt": [{"type": "text", "text": "p"}]}]
    batch_id = asyncio.run(be.submit(vault, docs, model="gpt-5.6-luna", effort=None,
                                     skills={"sha1": "s"}, api_key="sk-oai", backend="openai-batch"))
    assert batch_id == "batch_oai_dispatch"
    assert be.read_state(vault)["backend"] == "openai-batch"


# ── OpenAI Batch API (#530) ────────────────────────────────────────────────────

class _FakeResp:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return self._json


class _FakeOpenAIHttp:
    """Stands in for `httpx.AsyncClient` against the four calls the OpenAI Batch path makes:
    upload (`POST .../files`), create (`POST .../batches`), poll (`GET .../batches/{id}`), and
    download (`GET .../files/{id}/content`) — routed by URL suffix, same fake instance reused
    across the `async with` block a real call opens once per `submit`/`status`/`collect`."""
    def __init__(self, *, batch_response=None, file_contents=None):
        self.calls = []
        self._batch_response = batch_response or {}
        self._file_contents = file_contents or {}   # file_id -> jsonl text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, data=None, json=None, files=None):
        self.calls.append({"method": "POST", "url": url, "headers": headers, "data": data,
                           "json": json, "files": files})
        if url.endswith("/files"):
            return _FakeResp(200, {"id": "file-in-1"})
        if url.endswith("/batches"):
            return _FakeResp(200, self._batch_response)
        raise AssertionError(f"unexpected POST {url}")

    async def get(self, url, headers=None):
        self.calls.append({"method": "GET", "url": url, "headers": headers})
        if url.endswith("/content"):
            file_id = url.rsplit("/files/", 1)[1].rsplit("/content", 1)[0]
            return _FakeResp(200, text=self._file_contents.get(file_id, ""))
        return _FakeResp(200, self._batch_response)


def test_openai_submit_uploads_jsonl_and_creates_batch(tmp_path, monkeypatch):
    import httpx
    vault = make_vault(tmp_path)
    fake = _FakeOpenAIHttp(batch_response={"id": "batch_oai_1"})
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: fake)

    docs = [{"sha": "sha1", "prompt": [{"type": "text", "text": "p1"}]},
           {"sha": "sha2", "prompt": [{"type": "text", "text": "p2"}]}]
    batch_id = asyncio.run(be.submit(vault, docs, model="gpt-5.6-luna", effort=None,
                                     skills={"sha1": "annual-report", "sha2": "bankruptcy"},
                                     api_key="sk-oai", backend="openai-batch"))
    assert batch_id == "batch_oai_1"

    upload, create = fake.calls[0], fake.calls[1]
    assert upload["url"].endswith("/files")
    assert upload["data"] == {"purpose": "batch"}
    assert upload["headers"]["Authorization"] == "Bearer sk-oai"
    lines = [json.loads(line) for line in upload["files"]["file"][1].decode("utf-8").splitlines()]
    assert [line["custom_id"] for line in lines] == ["sha1", "sha2"]
    assert lines[0]["method"] == "POST"
    assert lines[0]["url"] == "/v1/chat/completions"
    # gpt-5.6-luna is a catalogued reasoning model — max_completion_tokens, not max_tokens.
    assert lines[0]["body"]["model"] == "gpt-5.6-luna"
    assert "max_completion_tokens" in lines[0]["body"]
    assert "max_tokens" not in lines[0]["body"]

    assert create["url"].endswith("/batches")
    assert create["json"] == {"input_file_id": "file-in-1", "endpoint": "/v1/chat/completions",
                              "completion_window": "24h"}

    state = be.read_state(vault)
    assert state["batch_id"] == "batch_oai_1"
    assert state["backend"] == "openai-batch"
    assert state["model"] == "gpt-5.6-luna"
    assert state["shas"] == ["sha1", "sha2"]
    assert state["skills"] == {"sha1": "annual-report", "sha2": "bankruptcy"}
    assert set(state["est_prompt_tokens"]) == {"sha1", "sha2"}   # #617, as on the Anthropic path


def test_openai_submit_passes_resolved_effort(tmp_path, monkeypatch):
    import httpx
    vault = make_vault(tmp_path)
    fake = _FakeOpenAIHttp(batch_response={"id": "batch_oai_2"})
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: fake)
    docs = [{"sha": "sha1", "prompt": [{"type": "text", "text": "p"}]}]
    asyncio.run(be.submit(vault, docs, model="gpt-5.6-luna", effort="high", skills={"sha1": "s"},
                          api_key="sk-oai", backend="openai-batch"))
    upload = fake.calls[0]
    line = json.loads(upload["files"]["file"][1].decode("utf-8").splitlines()[0])
    assert line["body"]["reasoning_effort"] == "high"


def test_openai_request_body_chat_model_uses_max_tokens():
    """An uncatalogued chat-family id (fallback_is_reasoning's `gpt-4` prefix -> chat) gets the
    classic `max_tokens` field, not `max_completion_tokens` — mirrors the live single-call path's
    same is_reasoning branch (#354)."""
    response_format = model_client._openai_response_format(
        model_client._OPENAI_BASE["openai"], schemas.EXTRACTION)
    body = be._openai_request_body("gpt-4o-mini", "prompt text", 9000, None, response_format)
    assert body["max_tokens"] == 9000
    assert "max_completion_tokens" not in body
    assert "reasoning_effort" not in body


def test_openai_request_body_includes_prompt_cache_key_when_prompt_has_a_breakpoint():
    """#562: the same missing parameter as the live path — this is what makes the "sorted by
    skill label" batch ordering actually pay off on OpenAI."""
    response_format = model_client._openai_response_format(
        model_client._OPENAI_BASE["openai"], schemas.EXTRACTION)
    prompt = [{"type": "text", "text": "instructions"},
              {"type": "text", "text": "skill", "cache_control": {"type": "ephemeral"}},
              {"type": "text", "text": "document"}]
    body = be._openai_request_body("gpt-5.6-luna", prompt, 9000, None, response_format)
    assert body["prompt_cache_key"] == model_client._prompt_cache_key(prompt)


def test_openai_request_body_omits_prompt_cache_key_with_no_breakpoint():
    response_format = model_client._openai_response_format(
        model_client._OPENAI_BASE["openai"], schemas.EXTRACTION)
    body = be._openai_request_body("gpt-4o-mini", "plain string prompt", 9000, None,
                                   response_format)
    assert "prompt_cache_key" not in body


def test_openai_submit_two_docs_sharing_a_skill_get_the_same_prompt_cache_key(tmp_path, monkeypatch):
    import httpx

    from watchdog.pipeline import prompts

    vault = make_vault(tmp_path)
    fake = _FakeOpenAIHttp(batch_response={"id": "batch_oai_cache"})
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: fake)

    kwargs = dict(skill_text="SKILL", brief=None, known_document_types=[])
    prompt1 = prompts.build_extract_prompt(pages_text="document one", sidecar=None, **kwargs)
    prompt2 = prompts.build_extract_prompt(pages_text="a different, longer document",
                                           sidecar="unrelated notes", **kwargs)
    docs = [{"sha": "sha1", "prompt": prompt1}, {"sha": "sha2", "prompt": prompt2}]
    asyncio.run(be.submit(vault, docs, model="gpt-5.6-luna", effort=None,
                          skills={"sha1": "annual-report", "sha2": "annual-report"},
                          api_key="sk-oai", backend="openai-batch"))

    upload = fake.calls[0]
    lines = [json.loads(line) for line in upload["files"]["file"][1].decode("utf-8").splitlines()]
    key1 = lines[0]["body"]["prompt_cache_key"]
    key2 = lines[1]["body"]["prompt_cache_key"]
    assert key1 is not None
    assert key1 == key2


def test_openai_status_normalizes_terminal_state_and_counts(monkeypatch):
    import httpx
    fake = _FakeOpenAIHttp(batch_response={
        "status": "completed", "request_counts": {"total": 3, "completed": 2, "failed": 1},
        "created_at": 1700000000, "completed_at": 1700003600,
    })
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: fake)
    s = asyncio.run(be.status("batch_oai_1", "sk-oai", backend="openai-batch"))
    assert s["processing_status"] == "ended"
    assert s["request_counts"] == {"processing": 0, "succeeded": 2, "errored": 1}
    assert s["created_at"] == datetime.datetime.fromtimestamp(
        1700000000, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert s["ended_at"] == datetime.datetime.fromtimestamp(
        1700003600, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_openai_status_in_progress_before_terminal(monkeypatch):
    import httpx
    fake = _FakeOpenAIHttp(batch_response={
        "status": "in_progress", "request_counts": {"total": 5, "completed": 2, "failed": 0}})
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: fake)
    s = asyncio.run(be.status("batch_oai_1", "sk-oai", backend="openai-batch"))
    assert s["processing_status"] == "in_progress"
    assert s["request_counts"] == {"processing": 3, "succeeded": 2, "errored": 0}
    assert s["ended_at"] is None


@pytest.mark.parametrize("terminal_status", ["failed", "expired", "cancelled"])
def test_openai_status_treats_every_terminal_state_as_ended(monkeypatch, terminal_status):
    """A failed/expired/cancelled OpenAI batch still needs collecting — each document can carry
    a real failure reason via error_file_id rather than being silently dropped."""
    import httpx
    fake = _FakeOpenAIHttp(batch_response={"status": terminal_status, "request_counts": {}})
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: fake)
    s = asyncio.run(be.status("batch_oai_1", "sk-oai", backend="openai-batch"))
    assert s["processing_status"] == "ended"


def test_openai_collect_maps_succeeded_result_by_sha(monkeypatch):
    import httpx
    output_line = json.dumps({
        "custom_id": "sha1",
        "response": {"status_code": 200, "body": {
            "choices": [{"message": {"content": json.dumps(VALID_EXTRACTION)},
                        "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 200},
        }},
        "error": None,
    })
    fake = _FakeOpenAIHttp(
        batch_response={"status": "completed", "output_file_id": "file-out-1"},
        file_contents={"file-out-1": output_line + "\n"})
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: fake)

    out = asyncio.run(be.collect("batch_oai_1", "sk-oai", "gpt-5.6-luna", backend="openai-batch"))
    assert out["sha1"]["ok"] is True
    assert out["sha1"]["parsed"]["morgue_entity_id"] == "acme-corp"
    assert out["sha1"]["usage"]["stop_reason"] == "stop"
    assert out["sha1"]["error"] is None


def test_openai_collect_prices_at_half_the_standard_rate(monkeypatch):
    import httpx
    output_line = json.dumps({
        "custom_id": "sha1",
        "response": {"status_code": 200, "body": {
            "choices": [{"message": {"content": json.dumps(VALID_EXTRACTION)},
                        "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 0},
        }},
        "error": None,
    })
    fake = _FakeOpenAIHttp(
        batch_response={"status": "completed", "output_file_id": "file-out-1"},
        file_contents={"file-out-1": output_line})
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: fake)

    out = asyncio.run(be.collect("batch_oai_1", "sk-oai", "gpt-5.4", backend="openai-batch"))
    # gpt-5.4: $2.50/1M input, halved by the batch discount.
    assert out["sha1"]["cost_usd"] == pytest.approx(1.25)


def test_openai_collect_surfaces_error_file_reason(monkeypatch):
    """A request OpenAI rejected before it ever reached the model (validation failure) is
    collected from `error_file_id` with the real reason, not silently dropped."""
    import httpx
    error_line = json.dumps({"custom_id": "sha2", "response": None,
                             "error": {"code": "invalid_request", "message": "prompt too long"}})
    fake = _FakeOpenAIHttp(
        batch_response={"status": "failed", "error_file_id": "file-err-1"},
        file_contents={"file-err-1": error_line})
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: fake)

    out = asyncio.run(be.collect("batch_oai_1", "sk-oai", "gpt-5.6-luna", backend="openai-batch"))
    assert out["sha2"]["ok"] is False
    assert out["sha2"]["parsed"] is None
    assert "prompt too long" in out["sha2"]["error"]


def test_openai_collect_flags_unparseable_text_without_crashing(monkeypatch):
    import httpx
    output_line = json.dumps({
        "custom_id": "sha1",
        "response": {"status_code": 200, "body": {
            "choices": [{"message": {"content": "not json at all"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }},
        "error": None,
    })
    fake = _FakeOpenAIHttp(
        batch_response={"status": "completed", "output_file_id": "file-out-1"},
        file_contents={"file-out-1": output_line})
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: fake)

    out = asyncio.run(be.collect("batch_oai_1", "sk-oai", "gpt-5.6-luna", backend="openai-batch"))
    assert out["sha1"]["ok"] is False
    assert out["sha1"]["parsed"] is None
