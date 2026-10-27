"""Actual attempt semantics and cross-worker bounded storage; no provider traffic."""
import asyncio
import json
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import fakeredis
import pytest

from app.infrastructure.metrics import provider_observations as obs


@pytest.fixture
def captured(monkeypatch):
    values = []
    monkeypatch.setattr(obs, "_signing_key", lambda: b"synthetic-observation-signing-key")
    monkeypatch.setattr(obs, "publish", values.append)
    return values


def attempt(key="synthetic-key", **context):
    return obs.ProviderAttempt("llm", "openai", key, context or {"source": "platform"})


def completed(captured, key="synthetic-key", **context):
    with attempt(key, **context) as event:
        event.accepted()
        event.first_output()
        event.complete()
    return captured[-1]


@pytest.mark.parametrize("outcome", [None, "completed", "recognized", "connected"])
def test_acceptance_or_empty_completion_does_not_prove_output(captured, outcome):
    event = attempt()
    event.accepted()
    event.complete()
    event.finish(outcome=outcome)
    assert captured[-1].outcome == ("connected" if outcome == "connected" else "incomplete")
    assert not captured[-1].output_seen
    assert captured[-1].first_output_ms is None


def test_partial_output_without_terminal_is_incomplete(captured):
    with attempt() as event:
        event.first_output()
    assert captured[-1].outcome == "incomplete"


def test_stt_recognition_does_not_invent_engine_latency(captured):
    event = obs.ProviderAttempt("stt", "deepgram_flux", "synthetic-key")
    event.accepted()
    event.recognized()
    event.finish(outcome="recognized")
    item = captured[-1]
    assert item.outcome == "recognized" and item.output_seen
    assert item.first_output_ms is None
    assert obs._decode(item.to_json(), item.reference, time.time_ns() // 1000) == item


def test_partial_failure_survives_fallback_on_different_credential(captured):
    error = RuntimeError("private provider body, transcript and synthetic-key")
    with pytest.raises(RuntimeError, match="private provider"):
        with attempt("primary-secret") as event:
            event.first_output()
            raise error
    successful = completed(captured, "fallback-secret")
    failed = captured[0]
    assert failed.outcome == "failed" and failed.output_seen
    assert failed.error_category == "provider"
    assert successful.outcome == "completed"
    assert failed.reference != successful.reference
    assert all(secret not in failed.to_json() + successful.to_json()
               for secret in ("primary-secret", "fallback-secret", str(error)))


@pytest.mark.parametrize("error", [asyncio.CancelledError(), GeneratorExit()])
def test_cancel_is_neutral_and_finish_is_idempotent(captured, error):
    event = attempt()
    event.first_output()
    event.finish(error)
    event.finish(RuntimeError("late error"))
    assert len(captured) == 1
    assert captured[0].outcome == "cancelled" and captured[0].error_category == "none"


@pytest.mark.parametrize("status,category", [(401, "authentication"), (403, "authentication"), (429, "rate_limit"), (400, "request"), (503, "provider")])
def test_only_safe_error_categories_retained(captured, status, category):
    error = RuntimeError("secret response")
    error.response = SimpleNamespace(status_code=status)
    attempt().finish(error)
    assert captured[-1].error_category == category
    assert "secret response" not in captured[-1].to_json()


def test_aiohttp_style_status_is_classified_without_body(captured):
    error = RuntimeError("private response")
    error.status = 429
    attempt().finish(error)
    assert captured[-1].error_category == "rate_limit"


def test_scope_separates_tenants_rows_and_revisions(captured):
    tenant, row = str(uuid4()), str(uuid4())
    a = completed(captured, source="tenant", tenant_id=tenant, credential_id=row)
    same = completed(captured, source="tenant", tenant_id=tenant, credential_id=row)
    rotated = completed(captured, "rotated-key", source="tenant", tenant_id=tenant, credential_id=row)
    other = completed(captured, source="tenant", tenant_id=str(uuid4()), credential_id=row)
    replacement = completed(captured, source="tenant", tenant_id=tenant, credential_id=str(uuid4()))
    platform = completed(captured, source="platform", tenant_id=tenant, credential_id=row)
    assert a.reference == same.reference
    assert len({item.reference for item in (a, rotated, other, replacement, platform)}) == 5
    assert platform.credential_id is None
    assert len(a.credential_revision) == 32


def test_unverified_identity_is_not_invented(captured):
    item = completed(captured, source="tenant", tenant_id="unknown", credential_id="bad")
    assert item.credential_source == "unattributed"
    assert item.tenant_id is None and item.credential_id is None


def test_missing_signing_material_drops_observation(captured, monkeypatch):
    monkeypatch.setattr(obs, "_signing_key", lambda: None)
    with attempt() as event:
        event.first_output()
        event.complete()
    assert captured == []


def test_telemetry_error_cannot_replace_provider_error(captured, monkeypatch):
    def broken(_item):
        raise RuntimeError("storage unavailable")
    monkeypatch.setattr(obs, "publish", broken)
    with pytest.raises(ValueError, match="original provider error"):
        with attempt():
            raise ValueError("original provider error")


@pytest.mark.asyncio
async def test_two_workers_share_latest_scope_but_out_of_order_delivery_cannot_revert(captured):
    server = fakeredis.FakeServer()
    writer = fakeredis.aioredis.FakeRedis(server=server)
    reader = fakeredis.aioredis.FakeRedis(server=server)
    item = completed(captured)
    old = replace(item, observed_at_us=item.observed_at_us - 1_000_000, outcome="failed", error_category="transport")
    try:
        await obs._write_observation(writer, item)
        await obs._write_observation(writer, old)
        result = await obs.read_recent(reader)
        assert result == [item]
        # An actual later failure replaces the earlier success, even with output.
        new = replace(old, observed_at_us=item.observed_at_us + 1)
        await obs._write_observation(writer, new)
        assert await obs.read_recent(reader) == [new]
    finally:
        await writer.aclose()
        await reader.aclose()


@pytest.mark.asyncio
async def test_store_caps_both_index_and_payload_and_rejects_stale_future(captured):
    client = fakeredis.aioredis.FakeRedis()
    try:
        for i in range(obs.MAX_RECORDS + 4):
            await obs._write_observation(client, completed(captured, f"synthetic-key-{i}"))
        assert await client.zcard(obs.INDEX_KEY) == obs.MAX_RECORDS
        assert await client.hlen(obs.RECORDS_KEY) == obs.MAX_RECORDS
        assert len(await obs.read_recent(client)) == obs.MAX_RECORDS
        assert 0 < await client.ttl(obs.INDEX_KEY) <= obs.TTL_SECONDS
        item = completed(captured, "stale-key")
        for offset in (-obs.TTL_SECONDS - 1, 31):
            await obs._write_observation(client, replace(item, observed_at_us=(time.time_ns() // 1000) + offset * 1_000_000))
            assert await client.hget(obs.RECORDS_KEY, item.reference) is None
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_expired_samples_are_unknown_even_if_storage_ttl_renewed(captured):
    client = fakeredis.aioredis.FakeRedis()
    item = completed(captured)
    stale = replace(item, observed_at_us=(time.time_ns() // 1000) - (obs.TTL_SECONDS + 1) * 1_000_000)
    try:
        await client.zadd(obs.INDEX_KEY, {stale.reference: stale.observed_at_us})
        await client.hset(obs.RECORDS_KEY, stale.reference, stale.to_json())
        assert await obs.read_recent(client) == []
        fresh = completed(captured, "new-key")
        await obs._write_observation(client, fresh)
        assert await client.hget(obs.RECORDS_KEY, stale.reference) is None
        assert await obs.read_recent(client) == [fresh]
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("patch", [{"tenant_id": str(uuid4())}, {"observed_at_us": 10**18}, {"first_output_ms": -1}, {"output_seen": False}, {"elapsed_ms": None}, {"outcome": "invented"}, {"error_category": "raw secret"}, {"first_output_ms": 999999}])
async def test_malformed_or_tampered_read_is_unavailable(captured, patch):
    item = completed(captured)
    value = json.loads(item.to_json())
    value.update(patch)
    client = SimpleNamespace(zrevrange=AsyncMock(return_value=[item.reference]), hmget=AsyncMock(return_value=[json.dumps(value)]))
    assert await obs.read_recent(client) is None


@pytest.mark.asyncio
async def test_missing_read_storage_differs_from_empty_evidence(monkeypatch):
    monkeypatch.setattr(obs, "_redis_client", lambda: None)
    assert await obs.read_recent() is None
    assert await obs.read_recent(SimpleNamespace(zrevrange=AsyncMock(return_value=[]))) == []


@pytest.mark.asyncio
async def test_publication_is_bounded_and_never_waits_for_storage(captured, monkeypatch):
    item = completed(captured)
    # Restore the real function replaced by the captured fixture.
    monkeypatch.undo()
    gate = asyncio.Event()
    async def slow(*args):
        await gate.wait()
    client = SimpleNamespace(eval=slow)
    monkeypatch.setattr(obs, "_redis_client", lambda: client)
    monkeypatch.setattr(obs, "MAX_PENDING_WRITES", 2)
    assert obs.publish(item) is True
    assert obs.publish(item) is True
    assert obs.publish(item) is False
    assert len(obs._pending) == 2
    gate.set()
    await asyncio.gather(*tuple(obs._pending))
    await asyncio.sleep(0)
    assert len(obs._pending) == 0


@pytest.mark.asyncio
async def test_storage_timeout_does_not_escape_or_leave_unbounded_task(captured, monkeypatch):
    item = completed(captured)
    async def stall(*args):
        await asyncio.Event().wait()
    monkeypatch.setattr(obs, "IO_TIMEOUT_SECONDS", .001)
    await obs._write_observation(SimpleNamespace(eval=stall), item)
    assert await obs.read_recent(SimpleNamespace(zrevrange=stall)) is None


@pytest.mark.asyncio
async def test_admin_projects_scoped_primary_failure_and_fallback_success_separately(captured, monkeypatch):
    from app.api.v1.endpoints.admin.health import _shared
    tenant = str(uuid4())
    attempt("primary-secret", source="tenant", tenant_id=tenant, credential_id=str(uuid4())).finish(TimeoutError())
    completed(captured, "fallback-secret", source="platform", tenant_id=tenant)
    monkeypatch.setattr(obs, "read_recent", AsyncMock(return_value=captured))
    result = await _shared._observed_providers()
    records = [item for item in result if item.name.startswith("LLM /")]
    assert {item.status for item in records} == {"observed_success", "observed_failure"}
    assert len({item.name for item in records}) == 2
    assert all(tenant in item.scope and "observed revision" in item.scope for item in records)
    assert "timeout" in records[0].detail
    assert all(item.last_check is not None for item in records)
    assert all(item.status == "unknown" for item in result if item.name in {"STT", "TTS", "REALTIME"})


@pytest.mark.asyncio
async def test_admin_neutral_outcomes_and_stt_do_not_invent_success_or_latency(captured, monkeypatch):
    from app.api.v1.endpoints.admin.health import _shared
    for outcome in ("connected", "incomplete", "cancelled"):
        attempt(outcome).finish(outcome=outcome)
    event = obs.ProviderAttempt("stt", "flux", "stt-secret")
    event.recognized()
    event.finish(outcome="recognized")
    monkeypatch.setattr(obs, "read_recent", AsyncMock(return_value=captured))
    result = await _shared._observed_providers()
    assert all(item.status == "unknown" for item in result if item.name.startswith("LLM /"))
    stt = next(item for item in result if item.name.startswith("STT /"))
    assert stt.status == "observed_success" and stt.latency_ms is None
    assert "accuracy is not measured" in stt.detail


@pytest.mark.asyncio
async def test_admin_realtime_latency_is_labelled_from_response_acceptance(captured, monkeypatch):
    from app.api.v1.endpoints.admin.health import _shared
    with obs.ProviderAttempt("realtime", "openai", "synthetic-realtime-key") as event:
        event.accepted()
        event.first_output()
        event.complete()
    monkeypatch.setattr(obs, "read_recent", AsyncMock(return_value=captured))
    result = await _shared._observed_providers()
    realtime = next(item for item in result if item.name.startswith("REALTIME /"))
    assert realtime.status == "observed_success"
    assert realtime.latency_ms is not None
    assert "after response acceptance" in realtime.latency_display
    assert "to first output" not in realtime.latency_display


@pytest.mark.asyncio
@pytest.mark.parametrize("records", [None, []])
async def test_admin_missing_observations_never_get_new_check_timestamp(monkeypatch, records):
    from app.api.v1.endpoints.admin.health import _shared
    monkeypatch.setattr(obs, "read_recent", AsyncMock(return_value=records))
    result = await _shared._observed_providers()
    assert len(result) == 4
    assert all(item.status == "unknown" and item.last_check is None and item.latency_ms is None for item in result)
    assert all(("storage is unavailable" in item.detail) == (records is None) for item in result)
