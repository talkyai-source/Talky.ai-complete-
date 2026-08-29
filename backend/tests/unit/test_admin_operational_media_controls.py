"""Focused coverage for the Admin call/media operational controls."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.v1.dependencies import CurrentUser
from app.api.v1.endpoints import telephony_bridge
from app.api.v1.endpoints.admin import calls, media
from app.domain.services import call_guard as call_guard_module
from app.domain.services.call_guard import CallGuard, GuardCheck, GuardDecision
from app.domain.services.platform_runtime_controls import (
    OutboundCallPause,
    get_outbound_call_pause,
    set_outbound_call_pause,
)
from app.domain.services.call_status import TERMINAL_CALL_STATUSES
from app.domain.services.telephony.termination import TerminationContext


class _Acquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_args):
        return None


class _Pool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _Acquire(self._conn)


class _ControlConn:
    def __init__(self, row):
        self.row = row
        self.calls = []

    async def fetchrow(self, query, *args):
        self.calls.append((query, args))
        return self.row


@pytest.mark.asyncio
async def test_pause_control_round_trips_the_persisted_row():
    paused_at = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
    actor_id = str(uuid4())
    row = {
        "outbound_calls_paused": True,
        "paused_at": paused_at,
        "paused_by": actor_id,
        "pause_reason": "Provider incident",
    }
    conn = _ControlConn(row)
    pool = _Pool(conn)

    read_state = await get_outbound_call_pause(pool)
    written_state = await set_outbound_call_pause(
        pool,
        paused=True,
        actor_id=actor_id,
        reason="  Provider incident  ",
    )

    assert read_state == OutboundCallPause(True, paused_at, actor_id, "Provider incident")
    assert written_state == read_state
    assert conn.calls[1][1] == (True, actor_id, "Provider incident")
    assert "ON CONFLICT (id) DO UPDATE" in conn.calls[1][0]


async def _none():
    return None


@pytest.mark.asyncio
async def test_call_guard_blocks_at_the_first_check_when_platform_is_paused(monkeypatch):
    async def paused(_pool):
        return OutboundCallPause(paused=True, reason="Maintenance")

    monkeypatch.setattr(call_guard_module, "get_outbound_call_pause", paused)
    guard = CallGuard(db_pool=object(), redis_client=None)
    guard._get_tenant_limits = lambda _tenant_id: _none()
    guard._get_partner_limits = lambda _tenant_id: _none()
    guard._get_partner_id = lambda _tenant_id: _none()
    guard._log_decision = lambda *_args, **_kwargs: _none()

    result = await guard.evaluate(
        tenant_id="tenant-1",
        phone_number="+15551234567",
        call_type="outbound",
    )

    assert result.decision == GuardDecision.BLOCK
    assert result.failed_checks == [GuardCheck.PLATFORM_CALLS_ENABLED]
    assert len(result.check_results) == 1
    assert result.check_results[0].reason == "platform_outbound_calls_paused"
    assert result.check_results[0].details["reason"] == "Maintenance"


@pytest.mark.asyncio
async def test_platform_pause_does_not_block_inbound_calls(monkeypatch):
    async def must_not_read(_pool):
        raise AssertionError("inbound calls must not consult the outbound pause row")

    monkeypatch.setattr(call_guard_module, "get_outbound_call_pause", must_not_read)
    guard = CallGuard(db_pool=object(), redis_client=None)

    result = await guard._check_platform_calls_enabled(
        tenant_id="tenant-1",
        call_type="inbound",
    )

    assert result.passed is True
    assert result.reason == "not_outbound"


class _TerminateConn:
    def __init__(self, call_row):
        self.call_row = call_row
        self.queries = []

    async def fetchrow(self, query, *args):
        self.queries.append((query, args))
        if "SELECT id, tenant_id, external_call_uuid" in query:
            return self.call_row
        if "UPDATE calls" in query:
            return {
                "status": "ended",
                "outcome": "agent_hung_up",
                "duration_seconds": 12,
            }
        raise AssertionError(f"Unexpected query: {query}")

    async def fetchval(self, query, *args):
        self.queries.append((query, args))
        if "SELECT status FROM calls" in query:
            return self.call_row["status"]
        raise AssertionError(f"Unexpected query: {query}")


class _Audit:
    def __init__(self):
        self.events = []

    async def log(self, **kwargs):
        self.events.append(kwargs)


class _Adapter:
    def __init__(self):
        self.hung_up = []

    async def hangup_confirmed(self, external_id):
        self.hung_up.append(external_id)
        return True


def _stub_admin_termination_context(monkeypatch, conn: _TerminateConn) -> None:
    async def mark(_pool, *, call_reference, tenant_id=None, timeout_s=5.0):
        del tenant_id, timeout_s
        row = conn.call_row
        previous_status = str(row["status"] or "")
        if previous_status not in TERMINAL_CALL_STATUSES:
            row["status"] = "termination_pending"
            conn.queries.append(("UPDATE calls SET status='termination_pending'", ()))
        return TerminationContext(
            call_id=str(call_reference),
            tenant_id=str(row["tenant_id"]),
            provider_call_id=(
                row.get("provider_call_id") or row.get("external_call_uuid")
            ),
            previous_status=previous_status,
            provider_leg_ids=(),
        )

    monkeypatch.setattr(calls, "mark_termination_pending_and_load_context", mark)

    async def finalize(*_args, **_kwargs):
        return None

    monkeypatch.setattr(calls, "finalize_proven_inbound_termination", finalize)


@pytest.mark.asyncio
async def test_admin_terminate_requests_provider_hangup_and_closes_row(monkeypatch):
    call_id = str(uuid4())
    tenant_id = uuid4()
    conn = _TerminateConn({
        "id": call_id,
        "tenant_id": tenant_id,
        "external_call_uuid": "provider-channel-42",
        "provider_call_id": None,
        "provider": "asterisk",
        "direction": "outbound",
        "status": "in_call",
        "answered_at": datetime.now(timezone.utc),
    })

    @asynccontextmanager
    async def acquire(_pool, tenant_context):
        assert tenant_context is None
        yield conn

    adapter = _Adapter()
    audit = _Audit()
    monkeypatch.setattr(calls, "acquire_with_tenant", acquire)
    monkeypatch.setattr(telephony_bridge, "_adapter", adapter)
    _stub_admin_termination_context(monkeypatch, conn)

    result = await calls.terminate_call(
        call_id=call_id,
        admin_user=CurrentUser(
            id=str(uuid4()),
            email="operator@example.com",
            tenant_id=None,
            role="platform_admin",
        ),
        db_client=SimpleNamespace(pool=object()),
        audit_logger=audit,
    )

    assert adapter.hung_up == ["provider-channel-42"]
    assert result["provider_hangup_requested"] is True
    assert result["provider_hangup_confirmed"] is True
    assert result["termination_status"] == "confirmed"
    assert result["new_status"] == "ended"
    assert any("duration_seconds" in query for query, _args in conn.queries)
    assert audit.events[0]["action"] == "admin_ended_call"


@pytest.mark.asyncio
async def test_admin_terminate_keeps_call_live_without_provider_confirmation(monkeypatch):
    call_id = str(uuid4())
    conn = _TerminateConn({
        "id": call_id,
        "tenant_id": uuid4(),
        "external_call_uuid": "provider-channel-unconfirmed",
        "provider_call_id": None,
        "provider": "asterisk",
        "direction": "outbound",
        "status": "in_call",
        "answered_at": datetime.now(timezone.utc),
    })

    @asynccontextmanager
    async def acquire(_pool, tenant_context):
        assert tenant_context is None
        yield conn

    class UnconfirmedAdapter:
        async def hangup_confirmed(self, _external_id):
            return False

    audit = _Audit()
    monkeypatch.setattr(calls, "acquire_with_tenant", acquire)
    monkeypatch.setattr(telephony_bridge, "_adapter", UnconfirmedAdapter())
    _stub_admin_termination_context(monkeypatch, conn)

    with pytest.raises(HTTPException) as exc_info:
        await calls.terminate_call(
            call_id=call_id,
            admin_user=CurrentUser(
                id=str(uuid4()),
                email="operator@example.com",
                tenant_id=None,
                role="platform_admin",
            ),
            db_client=SimpleNamespace(pool=object()),
            audit_logger=audit,
        )

    assert exc_info.value.status_code == 504
    assert exc_info.value.detail["error"] == "termination_unconfirmed"
    assert exc_info.value.detail["termination_status"] == "requested"
    assert exc_info.value.detail["call_status"] == "termination_pending"
    assert any("status='termination_pending'" in query for query, _args in conn.queries)
    assert not any("duration_seconds" in query for query, _args in conn.queries)
    assert audit.events[0]["action"] == "admin_call_termination_unconfirmed"


@pytest.mark.asyncio
async def test_admin_terminal_replay_still_requires_provider_absence_proof(monkeypatch):
    call_id = str(uuid4())
    conn = _TerminateConn({
        "id": call_id,
        "tenant_id": uuid4(),
        "external_call_uuid": "provider-channel-terminal",
        "provider_call_id": None,
        "provider": "asterisk",
        "direction": "outbound",
        "status": "ended",
        "answered_at": datetime.now(timezone.utc),
    })

    @asynccontextmanager
    async def acquire(_pool, tenant_context):
        assert tenant_context is None
        yield conn

    class UnconfirmedAdapter:
        async def hangup_confirmed(self, _external_id):
            return False

    audit = _Audit()
    monkeypatch.setattr(calls, "acquire_with_tenant", acquire)
    monkeypatch.setattr(telephony_bridge, "_adapter", UnconfirmedAdapter())
    _stub_admin_termination_context(monkeypatch, conn)

    with pytest.raises(HTTPException) as exc_info:
        await calls.terminate_call(
            call_id=call_id,
            admin_user=CurrentUser(
                id=str(uuid4()),
                email="operator@example.com",
                tenant_id=None,
                role="platform_admin",
            ),
            db_client=SimpleNamespace(pool=object()),
            audit_logger=audit,
        )

    assert exc_info.value.status_code == 504
    assert exc_info.value.detail["call_status"] == "ended"
    assert not any("UPDATE calls" in query for query, _args in conn.queries)
    assert audit.events[0]["action"] == "admin_call_termination_unconfirmed"


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["ended", "cancelled", "rejected"])
async def test_admin_terminal_replay_reports_confirmation_only_after_pbx_proof(
    monkeypatch,
    terminal_status,
):
    call_id = str(uuid4())
    conn = _TerminateConn({
        "id": call_id,
        "tenant_id": uuid4(),
        "external_call_uuid": "provider-channel-terminal",
        "provider_call_id": None,
        "provider": "asterisk",
        "direction": "outbound",
        "status": terminal_status,
        "answered_at": datetime.now(timezone.utc),
    })

    @asynccontextmanager
    async def acquire(_pool, tenant_context):
        assert tenant_context is None
        yield conn

    adapter = _Adapter()
    monkeypatch.setattr(calls, "acquire_with_tenant", acquire)
    monkeypatch.setattr(telephony_bridge, "_adapter", adapter)
    _stub_admin_termination_context(monkeypatch, conn)

    result = await calls.terminate_call(
        call_id=call_id,
        admin_user=CurrentUser(
            id=str(uuid4()),
            email="operator@example.com",
            tenant_id=None,
            role="platform_admin",
        ),
        db_client=SimpleNamespace(pool=object()),
        audit_logger=_Audit(),
    )

    assert adapter.hung_up == ["provider-channel-terminal"]
    assert result["status"] == "already_terminal"
    assert result["call_status"] == terminal_status
    assert result["provider_hangup_requested"] is True
    assert result["provider_hangup_confirmed"] is True
    assert not any("UPDATE calls" in query for query, _args in conn.queries)


def test_admin_media_models_coerce_database_scalars_and_expose_availability():
    now = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
    item = media._recording_item({
        "id": uuid4(),
        "call_id": uuid4(),
        "tenant_id": uuid4(),
        "tenant_name": "Tenant",
        "phone_number": "+15551234567",
        "campaign_id": None,
        "campaign_name": None,
        "status": "uploaded",
        "mime_type": "audio/wav",
        "s3_bucket": "local",
        "duration_seconds": 30,
        "file_size_bytes": 1024,
        "created_at": now,
        "updated_at": now,
    })

    assert item.playable is True
    assert item.storage == "local"
    assert item.created_at == now.isoformat()
    assert len(item.id) == 36


def test_admin_media_date_filter_uses_an_exclusive_next_day_boundary():
    start, end = media._date_bounds(
        datetime(2026, 8, 1).date(),
        datetime(2026, 8, 22).date(),
    )

    assert start == datetime(2026, 8, 1, tzinfo=timezone.utc)
    assert end == datetime(2026, 8, 23, tzinfo=timezone.utc)


def test_admin_media_rejects_an_inverted_date_range():
    with pytest.raises(HTTPException) as exc:
        media._date_bounds(
            datetime(2026, 8, 23).date(),
            datetime(2026, 8, 22).date(),
        )

    assert getattr(exc.value, "status_code", None) == 422


def test_admin_media_permanent_delete_requires_a_meaningful_reason():
    with pytest.raises(ValidationError):
        media.AdminMediaDeleteRequest(reason="delete")
    with pytest.raises(ValidationError):
        media.AdminMediaDeleteRequest(reason="12345678")

    request = media.AdminMediaDeleteRequest(
        reason="  Customer approved data erasure after account closure.  "
    )
    assert request.reason == "Customer approved data erasure after account closure."


class _DeletionIntentConn:
    def __init__(self, *, legal_hold: bool = False):
        self.legal_hold = legal_hold
        self.recording_id = uuid4()
        self.call_id = uuid4()
        self.tenant_id = uuid4()
        self.intent_id = uuid4()
        self.queries: list[str] = []

    async def fetchrow(self, query, *args):
        self.queries.append(query)
        if "FROM admin_media_deletion_request_keys k" in query:
            return None
        if "WHERE actor_id = $1 AND idempotency_key = $2" in query:
            return None
        if "FROM recordings_s3" in query and "FOR UPDATE" in query:
            return {
                "id": self.recording_id,
                "call_id": self.call_id,
                "tenant_id": self.tenant_id,
                "s3_bucket": "local",
                "s3_key": "/recordings/example.wav",
                "status": "uploaded",
                "mime_type": "audio/wav",
            }
        if "WHERE resource_type = $1 AND resource_id = $2" in query:
            return None
        if "INSERT INTO admin_media_deletion_intents" in query:
            return {
                "id": self.intent_id,
                "status": "intent_committed",
                "resource_snapshot": args[-1],
                "response_body": None,
            }
        if "INSERT INTO admin_media_deletion_request_keys" in query:
            return {
                "intent_id": args[0],
                "request_reason": args[-1],
            }
        raise AssertionError(f"Unexpected query: {query}")

    async def fetchval(self, query, *args):
        self.queries.append(query)
        if "media-delete-key" in query:
            return True
        assert "suspension_events" in query
        assert "se.target_type = 'partner'" in query
        assert "held_tenant.white_label_partner_id = se.target_id" in query
        assert args == (self.tenant_id,)
        return self.legal_hold


@pytest.mark.asyncio
async def test_admin_media_claim_commits_intent_only_after_legal_hold_check(monkeypatch):
    conn = _DeletionIntentConn()

    @asynccontextmanager
    async def acquire(_pool, tenant_context):
        assert tenant_context is None
        yield conn

    monkeypatch.setattr(media, "acquire_with_tenant", acquire)
    claim = await media._claim_media_deletion(
        SimpleNamespace(pool=object()),
        resource_type="recording",
        resource_id=conn.recording_id,
        actor_id=str(uuid4()),
        idempotency_key="admin-media:test:one",
        reason="Approved customer erasure request",
    )

    assert claim is not None
    assert claim.intent_id == conn.intent_id
    hold_index = next(i for i, query in enumerate(conn.queries) if "suspension_events" in query)
    insert_index = next(
        i for i, query in enumerate(conn.queries)
        if "INSERT INTO admin_media_deletion_intents" in query
    )
    assert hold_index < insert_index


@pytest.mark.asyncio
async def test_admin_media_claim_blocks_active_compliance_legal_hold(monkeypatch):
    conn = _DeletionIntentConn(legal_hold=True)

    @asynccontextmanager
    async def acquire(_pool, _tenant_context):
        yield conn

    monkeypatch.setattr(media, "acquire_with_tenant", acquire)
    with pytest.raises(HTTPException) as exc:
        await media._claim_media_deletion(
            SimpleNamespace(pool=object()),
            resource_type="recording",
            resource_id=conn.recording_id,
            actor_id=str(uuid4()),
            idempotency_key="admin-media:test:hold",
            reason="Pending litigation requires preserved evidence",
        )

    assert exc.value.status_code == 423
    assert not any("INSERT INTO admin_media_deletion_intents" in query for query in conn.queries)


@pytest.mark.asyncio
async def test_failed_media_delete_can_be_resumed_by_another_authorized_actor(
    monkeypatch,
):
    origin_actor = uuid4()
    recovery_actor = uuid4()
    tenant_id = uuid4()
    recording_id = uuid4()
    intent_id = uuid4()
    call_id = uuid4()
    snapshot = {
        "id": str(recording_id),
        "call_id": str(call_id),
        "tenant_id": str(tenant_id),
        "s3_bucket": "recordings",
        "s3_key": "tenant/call.wav",
    }
    updates: list[tuple[str, tuple]] = []

    class Conn:
        async def fetchrow(self, query, *args):
            if "FROM admin_media_deletion_request_keys k" in query:
                return None
            if "WHERE actor_id = $1 AND idempotency_key = $2" in query:
                return None
            if "FROM recordings_s3" in query and "FOR UPDATE" in query:
                return None
            if "WHERE resource_type = $1 AND resource_id = $2" in query:
                return {
                    "id": intent_id,
                    "actor_id": origin_actor,
                    "tenant_id": tenant_id,
                    "call_id": call_id,
                    "resource_type": "recording",
                    "resource_id": recording_id,
                    "idempotency_key": "origin-delete-key",
                    "reason": "Original approved erasure request",
                    "status": "failed",
                    "resource_snapshot": snapshot,
                    "response_body": None,
                    "attempt_count": 1,
                    "attempt_actor_ids": [origin_actor],
                }
            if "UPDATE admin_media_deletion_intents" in query:
                updates.append((query, args))
                assert args == (intent_id, "intent_committed", recovery_actor)
                return {
                    "id": intent_id,
                    "actor_id": origin_actor,
                    "status": "intent_committed",
                    "resource_snapshot": snapshot,
                    "response_body": None,
                    "attempt_count": 2,
                    "attempt_actor_ids": [origin_actor, recovery_actor],
                }
            if "INSERT INTO admin_media_deletion_request_keys" in query:
                assert args == (
                    intent_id,
                    recovery_actor,
                    "recovery-delete-key",
                    "Recovery by replacement administrator",
                )
                return {
                    "intent_id": intent_id,
                    "request_reason": args[-1],
                }
            raise AssertionError(f"Unexpected query: {query}")

        async def fetchval(self, query, *args):
            if "media-delete-key" in query:
                assert args == (recovery_actor, "recovery-delete-key")
                return True
            assert "suspension_events" in query
            assert args == (tenant_id,)
            return False

    @asynccontextmanager
    async def acquire(_pool, tenant_context):
        assert tenant_context is None
        yield Conn()

    monkeypatch.setattr(media, "acquire_with_tenant", acquire)
    claim = await media._claim_media_deletion(
        SimpleNamespace(pool=object()),
        resource_type="recording",
        resource_id=recording_id,
        actor_id=str(recovery_actor),
        idempotency_key="recovery-delete-key",
        reason="Recovery by replacement administrator",
    )

    assert claim is not None
    assert claim.status == "intent_committed"
    assert claim.origin_actor_id == str(origin_actor)
    assert claim.attempt_actor_id == str(recovery_actor)
    assert claim.resumed_by_different_actor is True
    assert len(updates) == 1
    assert "array_append(attempt_actor_ids, $3)" in updates[0][0]


@pytest.mark.asyncio
async def test_recovery_actor_idempotency_key_cannot_bind_two_media_resources(
    monkeypatch,
):
    actor_id = uuid4()
    tenant_id = uuid4()
    first_resource = uuid4()
    second_resource = uuid4()
    intent_id = uuid4()
    queries: list[str] = []

    class Conn:
        async def fetchval(self, query, *args):
            assert "media-delete-key" in query
            assert args == (actor_id, "recovery-shared-key")
            return True

        async def fetchrow(self, query, *args):
            queries.append(query)
            assert "FROM admin_media_deletion_request_keys k" in query
            assert args == (actor_id, "recovery-shared-key")
            return {
                "id": intent_id,
                "actor_id": uuid4(),
                "tenant_id": tenant_id,
                "resource_type": "recording",
                "resource_id": first_resource,
                "reason": "Original reason",
                "bound_request_reason": "Recovery authorization reason",
                "status": "failed",
                "resource_snapshot": {},
                "response_body": None,
            }

    @asynccontextmanager
    async def acquire(_pool, _tenant_context):
        yield Conn()

    monkeypatch.setattr(media, "acquire_with_tenant", acquire)
    with pytest.raises(HTTPException) as exc:
        await media._claim_media_deletion(
            SimpleNamespace(pool=object()),
            resource_type="recording",
            resource_id=second_resource,
            actor_id=str(actor_id),
            idempotency_key="recovery-shared-key",
            reason="Recovery authorization reason",
        )

    assert exc.value.status_code == 409
    assert len(queries) == 1


@pytest.mark.asyncio
async def test_serialized_media_deletion_try_locks_then_checks_partner_hold_before_yield(
    monkeypatch,
):
    tenant_id = uuid4()
    claim = media._DeletionClaim(
        intent_id=uuid4(),
        status="intent_committed",
        snapshot={"tenant_id": tenant_id},
    )
    order: list[str] = []

    class Conn:
        async def fetchval(self, query, *args):
            assert args == (tenant_id,)
            if "pg_try_advisory_xact_lock" in query:
                order.append("advisory_try_lock")
                return True
            assert "suspension_events" in query
            assert "se.target_type = 'tenant'" in query
            assert "se.target_type = 'partner'" in query
            assert "held_tenant.white_label_partner_id = se.target_id" in query
            order.append("tenant_and_partner_hold_query")
            return False

    conn = Conn()

    @asynccontextmanager
    async def acquire(_pool, tenant_context):
        assert tenant_context is None
        yield conn

    monkeypatch.setattr(media, "acquire_with_tenant", acquire)
    async with media._serialized_media_deletion(
        SimpleNamespace(pool=object()),
        claim,
    ) as yielded_conn:
        assert yielded_conn is conn
        order.append("yielded_for_storage")

    assert order == [
        "advisory_try_lock",
        "tenant_and_partner_hold_query",
        "yielded_for_storage",
    ]


@pytest.mark.asyncio
async def test_serialized_media_deletion_busy_lock_fails_without_waiting_or_yielding(
    monkeypatch,
):
    tenant_id = uuid4()
    claim = media._DeletionClaim(
        intent_id=uuid4(),
        status="intent_committed",
        snapshot={"tenant_id": tenant_id},
    )
    queries: list[str] = []

    class Conn:
        async def fetchval(self, query, *args):
            queries.append(query)
            assert args == (tenant_id,)
            assert "pg_try_advisory_xact_lock" in query
            return False

    @asynccontextmanager
    async def acquire(_pool, tenant_context):
        assert tenant_context is None
        yield Conn()

    monkeypatch.setattr(media, "acquire_with_tenant", acquire)
    with pytest.raises(HTTPException) as exc:
        async with media._serialized_media_deletion(
            SimpleNamespace(pool=object()),
            claim,
        ):
            raise AssertionError("busy deletion guard must not yield")

    assert exc.value.status_code == 409
    assert len(queries) == 1


@pytest.mark.asyncio
async def test_serialized_media_deletion_blocks_cross_table_object_reference(
    monkeypatch,
):
    tenant_id = uuid4()
    recording_id = uuid4()
    claim = media._DeletionClaim(
        intent_id=uuid4(),
        status="intent_committed",
        snapshot={
            "id": recording_id,
            "tenant_id": tenant_id,
            "s3_bucket": "persisted-bucket",
            "s3_key": "tenant/call.wav",
        },
    )

    class Conn:
        async def fetchval(self, query, *args):
            if "media-hold" in query:
                return True
            if "suspension_events" in query:
                return False
            if "media-object" in query:
                assert args == ("persisted-bucket", "tenant/call.wav")
                return True
            assert "FROM recordings_s3" in query
            assert "FROM call_feedback" in query
            assert args == (
                "persisted-bucket",
                "tenant/call.wav",
                "recording",
                recording_id,
            )
            return True

    @asynccontextmanager
    async def acquire(_pool, _tenant_context):
        yield Conn()

    monkeypatch.setattr(media, "acquire_with_tenant", acquire)
    with pytest.raises(HTTPException) as exc:
        async with media._serialized_media_deletion(
            SimpleNamespace(pool=object()),
            claim,
        ):
            raise AssertionError("shared object deletion guard must not yield")

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_cancelled_delete_keeps_guard_until_object_stage_is_durable(monkeypatch):
    claim = media._DeletionClaim(
        intent_id=uuid4(),
        status="intent_committed",
        snapshot={"tenant_id": uuid4()},
    )
    operation_started = asyncio.Event()
    allow_operation_to_finish = asyncio.Event()
    order: list[str] = []
    deletion_conn = object()

    @asynccontextmanager
    async def serialized_deletion(_db, actual_claim):
        assert actual_claim is claim
        order.append("guard_entered")
        try:
            yield deletion_conn
        finally:
            order.append("guard_committed_and_released")

    async def delete_operation():
        order.append("storage_started")
        operation_started.set()
        await allow_operation_to_finish.wait()
        order.append("storage_finished")

    async def mark_stage(conn, intent_id, status, **_kwargs):
        assert conn is deletion_conn
        assert intent_id == claim.intent_id
        assert status == "object_deleted"
        order.append("stage:object_deleted")

    monkeypatch.setattr(media, "_serialized_media_deletion", serialized_deletion)
    monkeypatch.setattr(media, "_mark_deletion_stage_on_connection", mark_stage)

    request_task = asyncio.create_task(
        media._execute_serialized_media_deletion(
            SimpleNamespace(pool=object()),
            claim,
            delete_operation,
        )
    )
    await operation_started.wait()
    request_task.cancel()
    await asyncio.sleep(0)

    assert not request_task.done()
    assert "guard_committed_and_released" not in order

    allow_operation_to_finish.set()
    with pytest.raises(asyncio.CancelledError):
        await request_task

    assert order == [
        "guard_entered",
        "storage_started",
        "storage_finished",
        "stage:object_deleted",
        "guard_committed_and_released",
    ]


@pytest.mark.asyncio
async def test_completed_delete_idempotency_replay_returns_without_storage(monkeypatch):
    intent_id = uuid4()
    recording_id = uuid4()
    cached = {
        "detail": "Recording permanently deleted",
        "deletion_intent_id": str(intent_id),
    }

    async def claim(*_args, **_kwargs):
        return media._DeletionClaim(
            intent_id=intent_id,
            status="completed",
            snapshot={},
            cached_response=cached,
        )

    async def must_not_run(*_args, **_kwargs):
        raise AssertionError("completed replay must not touch storage or metadata")

    @asynccontextmanager
    async def must_not_enter_guard(*_args, **_kwargs):
        raise AssertionError("completed replay must not enter deletion guard")
        yield  # pragma: no cover - async-contextmanager marker

    monkeypatch.setattr(media, "_claim_media_deletion", claim)
    monkeypatch.setattr(media, "_serialized_media_deletion", must_not_enter_guard)
    monkeypatch.setattr(media, "_mark_deletion_stage_on_connection", must_not_run)
    monkeypatch.setattr(media, "_complete_recording_deletion", must_not_run)

    response = await media.delete_admin_recording(
        recording_id=recording_id,
        payload=media.AdminMediaDeleteRequest(reason="Approved customer erasure request"),
        idempotency_key="admin-media:test:replay",
        admin_user=CurrentUser(
            id=str(uuid4()),
            email="operator@example.com",
            role="platform_admin",
        ),
        db_client=SimpleNamespace(pool=object()),
        audit_logger=_Audit(),
    )

    assert response == cached


def test_live_duration_accepts_native_and_serialized_database_timestamps():
    started = datetime.now(timezone.utc) - timedelta(seconds=10)

    native = calls._live_duration_seconds(started)
    serialized = calls._live_duration_seconds(started.isoformat().replace("+00:00", "Z"))

    assert 9 <= native <= 11
    assert 9 <= serialized <= 11
