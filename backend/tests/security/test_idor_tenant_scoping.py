"""Cross-tenant IDOR regression tests (P0 object-level authz).

Each test proves the SAME shape of fix: a read/mutate that previously keyed
on an id ALONE now also carries the caller's tenant, so a cross-tenant id
resolves to *not-found* (never another tenant's row) while a same-tenant id
still works.

Sites covered (see the fix commit):
  2. secrets_manager.rotate / revoke / mark_compromised  (tenant_secrets, no RLS)
  2b. secrets_manager.get_metadata                       (sibling read)
  3. secrets_manager.get_expiring_secrets + /expiring route
  4. security_events update / resolve / escalate endpoints
  5. telephony/recording._save_call_recording external_call_uuid lookup

Site 1 (call_summary/store) is enforced by Postgres RLS (calls/leads have a
tenant-isolation policy and the store goes through acquire_with_tenant); the
explicit predicate added there is defense-in-depth and is exercised by the
existing tests in tests/unit/call_summary/test_store.py.

The fakes below emulate Postgres' tenant filtering by honouring the exact
`($N::uuid IS NULL OR tenant_id = $N)` predicate the production queries now
carry, so a passing test means the real SQL threads the tenant correctly.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

# ---------------------------------------------------------------------------
# Shared tenant ids
# ---------------------------------------------------------------------------

OWNER = uuid4()      # the tenant that owns the resource
ATTACKER = uuid4()   # a different, authenticated tenant


# ---------------------------------------------------------------------------
# Generic acquire() context + pool
# ---------------------------------------------------------------------------

class _AcquireCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self, *args, **kwargs):
        return _AcquireCtx(self._conn)


# ===========================================================================
# Site 2 / 2b / 3 — secrets_manager
# ===========================================================================

def _secret_row(secret_id, tenant_id):
    now = datetime.utcnow()
    return {
        "secret_id": secret_id,
        "tenant_id": tenant_id,
        "secret_type": "WEBHOOK_HMAC",
        "secret_name": "hmac",
        "description": "d",
        "version": 1,
        "created_at": now,
        "updated_at": now,
        "expires_at": now + timedelta(days=3),
        "last_accessed_at": None,
        "access_count": 0,
        "is_active": True,
        "is_compromised": False,
        "permissions": {},
        "rotated_from": None,
        "rotated_to": None,
        "revoked_at": None,
    }


class SecretsConn:
    """Emulates `tenant_secrets` with the production tenant predicate."""

    def __init__(self, row):
        self.row = row
        self.executed = []

    def _visible(self, secret_id, tenant):
        r = self.row
        if r is None or str(r["secret_id"]) != str(secret_id):
            return None
        if tenant is not None and str(r["tenant_id"]) != str(tenant):
            return None  # cross-tenant → indistinguishable from missing
        return r

    async def fetchrow(self, query, *args):
        if "tenant_secrets" in query:
            tenant = args[1] if len(args) > 1 else None
            return self._visible(args[0], tenant)
        return None

    async def fetch(self, query, *args):
        if "tenant_secrets" in query and "expires_at" in query:
            tenant = args[1] if len(args) > 1 else None
            r = self.row
            if r is None:
                return []
            if tenant is not None and str(r["tenant_id"]) != str(tenant):
                return []
            return [r]
        return []

    async def execute(self, query, *args):
        self.executed.append((query, args))
        return "UPDATE 1"


def _manager(row):
    from app.domain.services.secrets_manager import SecretsManager

    return SecretsManager(FakePool(SecretsConn(row)), redis_client=None)


class TestSecretsManagerTenantScoping:
    async def test_rotate_same_tenant_succeeds(self):
        sid = uuid4()
        mgr = _manager(_secret_row(sid, OWNER))
        new_id = await mgr.rotate(secret_id=sid, rotated_by=uuid4(), tenant_id=OWNER)
        assert isinstance(new_id, UUID)

    async def test_rotate_cross_tenant_is_not_found(self):
        sid = uuid4()
        mgr = _manager(_secret_row(sid, OWNER))
        # Attacker (a real, authenticated tenant) targets OWNER's secret id.
        with pytest.raises(ValueError):
            await mgr.rotate(secret_id=sid, rotated_by=uuid4(), tenant_id=ATTACKER)

    async def test_revoke_same_tenant_true_cross_tenant_false(self):
        sid = uuid4()
        assert await _manager(_secret_row(sid, OWNER)).revoke(
            secret_id=sid, revoked_by=uuid4(), reason="r", tenant_id=OWNER
        ) is True
        assert await _manager(_secret_row(sid, OWNER)).revoke(
            secret_id=sid, revoked_by=uuid4(), reason="r", tenant_id=ATTACKER
        ) is False

    async def test_mark_compromised_same_true_cross_false(self):
        sid = uuid4()
        assert await _manager(_secret_row(sid, OWNER)).mark_compromised(
            secret_id=sid, reported_by=uuid4(), reason="r", tenant_id=OWNER
        ) is True
        assert await _manager(_secret_row(sid, OWNER)).mark_compromised(
            secret_id=sid, reported_by=uuid4(), reason="r", tenant_id=ATTACKER
        ) is False

    async def test_get_metadata_cross_tenant_returns_none(self):
        sid = uuid4()
        mgr = _manager(_secret_row(sid, OWNER))
        assert await mgr.get_metadata(sid, tenant_id=OWNER) is not None
        assert await mgr.get_metadata(sid, tenant_id=ATTACKER) is None

    async def test_get_expiring_secrets_scoped_to_tenant(self):
        sid = uuid4()
        owner_view = await _manager(_secret_row(sid, OWNER)).get_expiring_secrets(
            days=7, tenant_id=OWNER
        )
        attacker_view = await _manager(_secret_row(sid, OWNER)).get_expiring_secrets(
            days=7, tenant_id=ATTACKER
        )
        assert len(owner_view) == 1
        assert attacker_view == []


class TestSecretsRouteThreadsAuthenticatedTenant:
    """Q3: the tenant fed to the service is the authenticated one (path/JWT),
    never an attacker-supplied body value."""

    async def test_rotate_route_passes_path_tenant(self):
        from app.api.v1.endpoints import secrets as sec

        mgr = MagicMock()
        mgr.rotate = AsyncMock(return_value=uuid4())
        await sec.rotate_secret(
            tenant_id=OWNER,
            secret_id=uuid4(),
            data=sec.RotateSecretRequest(),
            current_user={"id": uuid4(), "tenant_id": str(OWNER)},
            secrets_manager=mgr,
            audit_logger=AsyncMock(),
        )
        assert str(mgr.rotate.call_args.kwargs["tenant_id"]) == str(OWNER)

    async def test_rotate_route_missing_secret_is_404(self):
        from fastapi import HTTPException

        from app.api.v1.endpoints import secrets as sec

        mgr = MagicMock()
        mgr.rotate = AsyncMock(side_effect=ValueError("Secret not found"))
        with pytest.raises(HTTPException) as ei:
            await sec.rotate_secret(
                tenant_id=OWNER,
                secret_id=uuid4(),
                data=sec.RotateSecretRequest(),
                current_user={"id": uuid4(), "tenant_id": str(OWNER)},
                secrets_manager=mgr,
                audit_logger=AsyncMock(),
            )
        assert ei.value.status_code == 404

    async def test_expiring_route_scopes_to_caller_tenant(self):
        from app.api.v1.endpoints import secrets as sec

        mgr = MagicMock()
        mgr.get_expiring_secrets = AsyncMock(return_value=[])
        await sec.get_expiring_secrets(
            days=7,
            current_user={"id": uuid4(), "tenant_id": str(OWNER)},
            secrets_manager=mgr,
        )
        assert str(mgr.get_expiring_secrets.call_args.kwargs["tenant_id"]) == str(OWNER)


# ===========================================================================
# Site 4 — security_events endpoints
# ===========================================================================

def _event_row(event_id, tenant_id):
    now = datetime.utcnow()
    return {
        "event_id": event_id,
        "created_at": now,
        "event_type": "suspicious_login",
        "severity": "HIGH",
        "status": "resolved",
        "tenant_id": tenant_id,
        "user_id": None,
        "title": "t",
        "description": "d",
        "detection_source": "manual",
        "evidence": None,
        "assigned_to": None,
        "resolved_at": now,
        "resolved_by": None,
        "resolution_notes": "note",
        "auto_action_taken": None,
        "sla_deadline": now + timedelta(hours=4),
    }


class EventsConn:
    def __init__(self, row):
        self.row = row

    async def fetchrow(self, query, *args):
        if "security_events" not in query:
            return None
        if "tenant_id = $" in query:
            event_id, tenant = args[-2], args[-1]
        else:
            event_id, tenant = args[-1], None
        r = self.row
        if r is None or str(r["event_id"]) != str(event_id):
            return None
        if tenant is not None and str(r["tenant_id"]) != str(tenant):
            return None
        return r

    async def execute(self, *a, **k):
        return "UPDATE 1"


def _events_pool(row):
    return FakePool(EventsConn(row))


class TestSecurityEventsTenantScoping:
    async def test_update_cross_tenant_is_404_same_ok(self):
        from fastapi import HTTPException

        from app.api.v1.endpoints import security_events as se

        eid = uuid4()
        data = se.SecurityEventUpdate(status="investigating")

        # same tenant → returns the row
        ok = await se.update_security_event(
            event_id=eid, data=data,
            current_user={"id": uuid4(), "tenant_id": str(OWNER)},
            audit_logger=AsyncMock(), db_pool=_events_pool(_event_row(eid, OWNER)),
        )
        assert str(ok.event_id) == str(eid)

        # cross tenant → not-found (no leak, not a 403 that confirms existence)
        with pytest.raises(HTTPException) as ei:
            await se.update_security_event(
                event_id=eid, data=data,
                current_user={"id": uuid4(), "tenant_id": str(ATTACKER)},
                audit_logger=AsyncMock(), db_pool=_events_pool(_event_row(eid, OWNER)),
            )
        assert ei.value.status_code == 404

    async def test_resolve_cross_tenant_is_404_same_ok(self):
        from fastapi import HTTPException

        from app.api.v1.endpoints import security_events as se

        eid = uuid4()
        ok = await se.resolve_security_event(
            event_id=eid, resolution_notes="done",
            current_user={"id": uuid4(), "tenant_id": str(OWNER)},
            audit_logger=AsyncMock(), db_pool=_events_pool(_event_row(eid, OWNER)),
        )
        assert str(ok.event_id) == str(eid)

        with pytest.raises(HTTPException) as ei:
            await se.resolve_security_event(
                event_id=eid, resolution_notes="done",
                current_user={"id": uuid4(), "tenant_id": str(ATTACKER)},
                audit_logger=AsyncMock(), db_pool=_events_pool(_event_row(eid, OWNER)),
            )
        assert ei.value.status_code == 404

    async def test_escalate_cross_tenant_is_404_same_ok(self):
        from fastapi import HTTPException

        from app.api.v1.endpoints import security_events as se

        eid = uuid4()
        ok = await se.escalate_event(
            event_id=eid, reason="urgent",
            current_user={"id": uuid4(), "tenant_id": str(OWNER)},
            audit_logger=AsyncMock(), db_pool=_events_pool(_event_row(eid, OWNER)),
        )
        assert str(ok.event_id) == str(eid)

        with pytest.raises(HTTPException) as ei:
            await se.escalate_event(
                event_id=eid, reason="urgent",
                current_user={"id": uuid4(), "tenant_id": str(ATTACKER)},
                audit_logger=AsyncMock(), db_pool=_events_pool(_event_row(eid, OWNER)),
            )
        assert ei.value.status_code == 404


# ===========================================================================
# Site 5 — recording external_call_uuid lookup
# ===========================================================================

class TestRecordingSessionTenant:
    def test_session_tenant_priority_and_fallbacks(self):
        from app.domain.services.telephony.recording import _session_tenant_uuid

        # 1. dialer-stamped tenant wins
        vs = MagicMock(_dialer_tenant_id=str(OWNER), config=None, call_session=None)
        assert _session_tenant_uuid(vs) == OWNER

        # 2. falls back to config.tenant_id
        vs = MagicMock(
            _dialer_tenant_id=None,
            config=MagicMock(tenant_id=str(OWNER)),
            call_session=None,
        )
        assert _session_tenant_uuid(vs) == OWNER

        # 3. falls back to call_session.tenant_id
        vs = MagicMock(
            _dialer_tenant_id=None,
            config=MagicMock(tenant_id=None),
            call_session=MagicMock(tenant_id=str(OWNER)),
        )
        assert _session_tenant_uuid(vs) == OWNER

    def test_session_tenant_none_when_unknown_or_invalid(self):
        from app.domain.services.telephony.recording import _session_tenant_uuid

        vs = MagicMock(_dialer_tenant_id=None, config=None, call_session=None)
        assert _session_tenant_uuid(vs) is None
        # a non-UUID sentinel (e.g. "default") must not raise, just yield None
        vs = MagicMock(_dialer_tenant_id="default", config=None, call_session=None)
        assert _session_tenant_uuid(vs) is None


class _RecFakeGateway:
    def __init__(self, caller, agent, rate=16000):
        self._c, self._a, self._sample_rate = caller, agent, rate

    def get_recording_buffer(self, _):
        return self._c

    def get_tts_recording_buffer(self, _):
        return self._a

    def clear_recording_buffer(self, _):
        pass


class _LookupConn:
    """Emulates two `calls` rows that COLLIDE on external_call_uuid across two
    tenants (the exact non-uniqueness the fix addresses). Honours the tenant
    predicate + ORDER BY created_at DESC LIMIT 1."""

    def __init__(self, rows):
        # rows already ordered newest-first
        self.rows = rows
        self.captured = None

    async def fetchrow(self, query, *args):
        self.captured = (query, args)
        tenant = args[1]
        matched = [
            r for r in self.rows
            if tenant is None or str(r["tenant_id"]) == str(tenant)
        ]
        return matched[0] if matched else None


@pytest.mark.asyncio
async def test_recording_lookup_cannot_resolve_other_tenant_call():
    from app.domain.services.telephony import recording as rec

    call_a = {"id": uuid4(), "tenant_id": ATTACKER, "campaign_id": None}  # older, other tenant
    call_b = {"id": uuid4(), "tenant_id": OWNER, "campaign_id": None}     # newer, THIS session
    conn = _LookupConn([call_b, call_a])  # both share the external_call_uuid

    gateway = _RecFakeGateway([b"\x01\x00" * 10000], [(0, b"\x02\x00" * 10000)])
    vs = MagicMock(
        media_gateway=gateway,
        call_id="voice-id",
        _dialer_tenant_id=str(OWNER),
        config=None,
        call_session=None,
    )

    captured_save = {}

    class _FakeRecordingSvc:
        def __init__(self, pool):
            pass

        async def save_and_link(self, call_id, buffer, tenant_id, campaign_id):
            captured_save["call_id"] = call_id
            captured_save["tenant_id"] = tenant_id
            return "rec-id"

    container = MagicMock()
    container.is_initialized = True

    @asynccontextmanager
    async def fake_get_db():
        yield conn

    with patch("app.core.container.get_container", return_value=container), patch(
        "app.domain.services.recording_service.RecordingService", _FakeRecordingSvc
    ), patch("app.core.db.get_db", fake_get_db):
        await rec._save_call_recording(vs, "pbx-channel-shared")

    # The query is tenant-scoped + ordered, and bound to THIS session's tenant.
    q, args = conn.captured
    assert "tenant_id = $2" in q and "ORDER BY created_at DESC" in q
    assert args[1] == OWNER
    # Resolution landed on OWNER's row, never the attacker's colliding row.
    assert captured_save["call_id"] == str(call_b["id"])
    assert captured_save["tenant_id"] == str(OWNER)


@pytest.mark.asyncio
async def test_recording_lookup_same_tenant_resolves_its_own_call():
    from app.domain.services.telephony import recording as rec

    call = {"id": uuid4(), "tenant_id": OWNER, "campaign_id": None}
    conn = _LookupConn([call])

    gateway = _RecFakeGateway([b"\x01\x00" * 10000], [(0, b"\x02\x00" * 10000)])
    vs = MagicMock(
        media_gateway=gateway, call_id="voice-id",
        _dialer_tenant_id=str(OWNER), config=None, call_session=None,
    )

    captured_save = {}

    class _FakeRecordingSvc:
        def __init__(self, pool):
            pass

        async def save_and_link(self, call_id, buffer, tenant_id, campaign_id):
            captured_save["call_id"] = call_id
            return "rec-id"

    container = MagicMock()
    container.is_initialized = True

    @asynccontextmanager
    async def fake_get_db():
        yield conn

    with patch("app.core.container.get_container", return_value=container), patch(
        "app.domain.services.recording_service.RecordingService", _FakeRecordingSvc
    ), patch("app.core.db.get_db", fake_get_db):
        await rec._save_call_recording(vs, "pbx-channel")

    assert captured_save["call_id"] == str(call["id"])
