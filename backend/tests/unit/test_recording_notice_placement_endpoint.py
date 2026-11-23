"""PUT /recordings/policy/outbound-notice-placement and the two new response fields."""
from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import UUID, uuid4

import asyncpg
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.v1 import routes
from app.api.v1.dependencies import CurrentUser, require_admin_tenant
from app.api.v1.endpoints import recording_notice_placement as ep
from app.api.v1.endpoints import recordings
from app.domain.services.audit_logger import AuditEvent
from tests.unit.test_endpoint_auth_audit import _collect_api_routes


class _Audit:
    def __init__(self):
        self.events = []

    async def lock_transaction(self, conn):
        self.events.append({"locked": True})

    async def log_in_transaction(self, conn, **kwargs):
        assert "event_type" in kwargs and "action" in kwargs
        self.events.append(kwargs)


@pytest.fixture(autouse=True)
def _the_platform_has_opened_the_choice(monkeypatch):
    # The closed state is tested in test_notice_placement_switch.py.
    monkeypatch.setenv("OUTBOUND_NOTICE_PLACEMENT_CHOICE_ENABLED", "true")


def _user():
    tenant = str(uuid4())
    return tenant, CurrentUser(id=str(uuid4()), email="a@example.com", tenant_id=tenant, role="tenant_admin")


def _patch(monkeypatch, tenant, fetchrow):
    @asynccontextmanager
    async def acquire(_pool, t):
        assert t == tenant
        yield SimpleNamespace(fetchrow=fetchrow)
    monkeypatch.setattr(ep, "acquire_with_tenant", acquire)


async def test_the_update_is_scoped_and_audited(monkeypatch):
    tenant, user = _user()
    seen = []

    async def fetchrow(query, *args):
        seen.append((" ".join(query.split()), args))
        return {"default_consent_mode": "two_party", "announcement_text": "Acme records calls.",
                "opt_out_dtmf_digit": None, "two_party_country_codes": [], "retention_days": 90,
                "outbound_notice_placement": "in_opening", "updated_at": None}

    _patch(monkeypatch, tenant, fetchrow)
    audit = _Audit()
    response = await ep.put_outbound_notice_placement(
        body=ep.NoticePlacementBody(outbound_notice_placement="in_opening"),
        user=user, db_pool=object(), audit_logger=audit)
    assert seen == [(
        "UPDATE tenant_recording_policy SET outbound_notice_placement = $2, updated_at = NOW() "
        "WHERE tenant_id = $1 RETURNING *", (UUID(tenant), "in_opening"))]
    assert response.outbound_notice_placement == "in_opening"
    assert response.outbound_notice_own_sentence == "Acme records calls."
    assert audit.events[0] == {"locked": True}
    (event,) = audit.events[1:]
    assert event["event_type"] == AuditEvent.CONFIG_CHANGED
    assert event["action"] == "recording_notice_placement_updated"
    assert event["resource_type"] == "tenant_recording_policy" and event["tenant_id"] == tenant
    assert event["metadata"] == {"outbound_notice_placement": "in_opening"}


async def test_no_saved_policy_is_409_and_audits_nothing(monkeypatch):
    tenant, user = _user()

    async def fetchrow(query, *args):
        return None

    _patch(monkeypatch, tenant, fetchrow)
    audit = _Audit()
    with pytest.raises(HTTPException) as err:
        await ep.put_outbound_notice_placement(
            body=ep.NoticePlacementBody(outbound_notice_placement="in_opening"),
            user=user, db_pool=object(), audit_logger=audit)
    assert err.value.status_code == 409 and err.value.detail == {"code": "recording_policy_not_saved"}
    assert all(e == {"locked": True} for e in audit.events)  # nothing audited


async def test_a_database_without_the_column_is_503_and_writes_nothing(monkeypatch):
    tenant, user = _user()

    async def fetchrow(query, *args):
        raise asyncpg.UndefinedColumnError("column does not exist")

    _patch(monkeypatch, tenant, fetchrow)
    audit = _Audit()
    with pytest.raises(HTTPException) as err:
        await ep.put_outbound_notice_placement(
            body=ep.NoticePlacementBody(outbound_notice_placement="in_opening"),
            user=user, db_pool=object(), audit_logger=audit)
    assert err.value.status_code == 503 and err.value.detail == {"code": "notice_placement_unavailable"}
    assert all(e == {"locked": True} for e in audit.events)  # nothing audited


def test_an_unknown_value_is_rejected_by_the_body():
    with pytest.raises(ValidationError):
        ep.NoticePlacementBody(outbound_notice_placement="loud")
    with pytest.raises(ValidationError):
        ep.NoticePlacementBody()


def test_the_route_is_admin_only_and_registered_once():
    matches = [r for path, r in _collect_api_routes(routes.api_router.routes)
               if path == "/recordings/policy/outbound-notice-placement"]
    assert len(matches) == 1 and matches[0].methods == {"PUT"}
    deps = [d.call for d in matches[0].dependant.dependencies]
    assert require_admin_tenant in deps


def test_policy_response_fields():
    platform = recordings._policy_response({
        "default_consent_mode": "two_party",
        "announcement_text": "This call may be recorded for quality and training purposes.",
        "two_party_country_codes": [], "retention_days": 90, "outbound_notice_placement": "own_sentence_first"})
    assert platform.outbound_notice_placement == "own_sentence_first"
    assert platform.outbound_notice_own_sentence is None
    stripped = recordings._policy_response({
        "default_consent_mode": "two_party",
        "announcement_text": "Acme records calls. Press 9 to opt out.",
        "two_party_country_codes": [], "retention_days": 90})
    assert stripped.outbound_notice_own_sentence == "Acme records calls."
    assert stripped.outbound_notice_placement is None          # a server before the migration
    absent = recordings._policy_response(None)
    assert absent.outbound_notice_placement is None and absent.outbound_notice_own_sentence is None
