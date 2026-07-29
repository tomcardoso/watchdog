"""Tests for the Message Batches API integration (#214) — state persistence, submit/status/
collect. The Anthropic SDK client is mocked at the `_client` boundary, matching how
test_model_client.py mocks _ABACKENDS rather than the real (uninstalled) anthropic package."""

import asyncio
import datetime
import json

import pytest

from watchdog.pipeline import batch_extract as be


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
