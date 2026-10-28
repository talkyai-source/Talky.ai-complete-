"""Caller attribution and pre-provider intent ordering; all ports synthetic."""
from datetime import UTC, datetime
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from starlette.requests import Request

from app.api.v1.endpoints import connectors, salesforce
from app.api.v1.endpoints.admin import connectors as admin
from app.infrastructure.connectors.crm.salesforce import SalesforceConnector
from app.services import connector_lifecycle as lifecycle
from app.services import connector_resolver
from tests.unit.test_bh_meetings_reminders_connector_disconnect import DB
from tests.unit.test_bh_meetings_reminders_salesforce_reconnect import callback_world, run_callback


def person():
    return NS(id=str(uuid4()), tenant_id=str(uuid4()), role="tenant_admin")


def authenticated_request(user):
    """Synthetic verified request context; these caller tests do not log in."""
    request = Request({"type": "http", "method": "POST", "path": "/connectors", "headers": []})
    request.state.authenticated_user_id = user.id
    request.state.authenticated_session_id = str(uuid4())
    return request


@pytest.mark.parametrize("operation,source", [("refresh", "admin_reconnect"), ("disable", "admin_revoke")])
async def test_admin_routes_forward_verified_actor_and_receipt(monkeypatch, operation, source):
    user = person()
    user.role = "platform_admin"
    cid = str(uuid4())
    receipt = str(uuid4())
    op = str(uuid4())
    service = NS(refresh=AsyncMock(return_value=(None, {"last_refreshed_at": datetime.now(UTC),
        "audit_event_id": receipt, "operation_id": op})),
        disable=AsyncMock(return_value=lifecycle.ConnectorDisableResult("unconfirmed", receipt, op, False)))
    seen = []
    def factory(_db, *, actor):
        seen.append(actor)
        return service
    monkeypatch.setattr(admin, "ConnectorLifecycle", factory)
    db = DB({("connectors", "select"): [{"id": cid, "tenant_id": user.tenant_id}]})
    handler = admin.force_reconnect_connector if operation == "refresh" else admin.revoke_connector
    request = authenticated_request(user)
    result = await handler(cid, admin_user=user, db_client=db, request=request)
    assert seen == [lifecycle.ConnectorActor.human(user, source, request=request, platform_admin=True)]
    assert seen[0].session_id == request.state.authenticated_session_id
    assert result["audit_event_id"] == receipt and result["operation_id"] == op
    if operation == "disable":
        assert result["local_disabled"] and not result["provider_revoked"]
        assert not result["provider_result_recorded"]


async def test_tenant_refresh_and_salesforce_settings_keep_human_attribution(monkeypatch):
    user, seen = person(), []
    service = NS(refresh=AsyncMock(return_value=(None, {"audit_event_id": "receipt", "operation_id": "operation"})),
        update_config=AsyncMock(return_value={"log_calls": False}))
    def factory(_db, *, actor):
        seen.append(actor)
        return service
    monkeypatch.setattr(connectors, "ConnectorLifecycle", factory)
    monkeypatch.setattr(lifecycle, "ConnectorLifecycle", factory)
    request = authenticated_request(user)
    await connectors.refresh_connector_tokens("connector", current_user=user, db_client=NS(), request=request)
    await salesforce._update_config(NS(), "connector", user.tenant_id, {"log_calls": False}, current_user=user, request=request)
    assert [(actor.source, actor.user_id, actor.platform_admin) for actor in seen] == [
        ("tenant_refresh", user.id, False), ("salesforce_settings", user.id, False)]
    assert all(actor.session_id == request.state.authenticated_session_id for actor in seen)


async def test_resolver_refresh_is_explicitly_system_attributed(monkeypatch):
    seen = []
    service = NS(refresh=AsyncMock(return_value=(NS(access_token="synthetic"), {"id": "account"})))
    def factory(_db, *, actor):
        seen.append(actor)
        return service
    monkeypatch.setattr(lifecycle, "ConnectorLifecycle", factory)
    await connector_resolver._refresh_and_store(NS(), NS(provider_name="gmail"), "connector",
        "account", "tenant", "ignored")
    assert seen == [lifecycle.ConnectorActor.system("connector_resolver")]


@pytest.mark.parametrize("intent_fails", [False, True])
async def test_oauth_commits_intent_before_exchange_and_preserves_initiator(monkeypatch, intent_fails):
    db = callback_world(monkeypatch)
    order, seen = [], []
    async def begin(*args):
        order.append("intent")
        if intent_fails:
            raise RuntimeError("synthetic audit commit failure")
    remote = SalesforceConnector.exchange_code
    remote.side_effect = lambda **kwargs: order.append("provider") or remote.return_value
    service = NS(begin_oauth=AsyncMock(side_effect=begin), activate=AsyncMock(return_value={"id": "new-account"}))
    def factory(_db, *, actor):
        seen.append(actor)
        return service
    monkeypatch.setattr(connectors, "ConnectorLifecycle", factory)
    response = await run_callback(db)
    assert seen == [lifecycle.ConnectorActor.oauth("u")]
    if intent_fails:
        assert order == ["intent"]
        remote.assert_not_awaited()
        service.activate.assert_not_awaited()
        assert "status=error" in response.headers["location"]
    else:
        assert order == ["intent", "provider"]
        service.activate.assert_awaited_once()


def test_lifecycle_requires_explicit_attribution():
    with pytest.raises(TypeError):
        lifecycle.ConnectorLifecycle(NS(pool=object()))


@pytest.mark.parametrize("state", ["missing_actor", "wrong_actor", "missing_session", "invalid_session"])
def test_human_actor_requires_matching_verified_request_state(state):
    user = person()
    request = authenticated_request(user)
    if state == "missing_actor":
        del request.state.authenticated_user_id
    elif state == "wrong_actor":
        request.state.authenticated_user_id = str(uuid4())
    elif state == "missing_session":
        del request.state.authenticated_session_id
    else:
        request.state.authenticated_session_id = "not-a-session-uuid"
    with pytest.raises(lifecycle.ConnectorLifecycleError) as error:
        lifecycle.ConnectorActor.human(user, "tenant_refresh", request=request)
    assert error.value.status_code == 401


def test_human_actor_pins_the_verified_session_and_keeps_it_out_of_repr():
    user = person()
    request = authenticated_request(user)
    original_session = request.state.authenticated_session_id
    original_tenant = user.tenant_id
    actor = lifecycle.ConnectorActor.human(user, "tenant_refresh", request=request)
    request.state.authenticated_session_id = str(uuid4())
    user.tenant_id = str(uuid4())
    assert actor.session_id == original_session
    assert actor.tenant_id == original_tenant
    assert original_session not in repr(actor)


@pytest.mark.parametrize("isolation", ["repeatable read", "serializable"])
async def test_human_admission_rejects_stale_snapshot_before_locks_or_principal_reads(monkeypatch, isolation):
    user = person()
    actor = lifecycle.ConnectorActor.human(user, "tenant_refresh", request=authenticated_request(user))
    forbidden = AsyncMock(side_effect=AssertionError("Rejected isolation must not acquire locks or read authority"))
    conn = NS(fetchval=AsyncMock(return_value=isolation), fetchrow=forbidden, execute=forbidden)
    service = lifecycle.ConnectorLifecycle(NS(pool=object()), actor=actor,
        audit_logger=NS(lock_transaction=forbidden))
    for name in ("lock_user_administration", "load_current_principal", "load_session_principal"):
        monkeypatch.setattr(lifecycle, name, forbidden)

    with pytest.raises(lifecycle.ConnectorLifecycleError) as error:
        await service._begin(conn, user.tenant_id, lifecycle.Permission.CONNECTORS_UPDATE)

    assert error.value.status_code == 503
    conn.fetchval.assert_awaited_once()
    forbidden.assert_not_awaited()


async def test_raw_human_actor_without_session_is_denied_before_database_access():
    user = person()
    actor = lifecycle.ConnectorActor(source="tenant_refresh", user_id=user.id, tenant_id=user.tenant_id)
    forbidden = AsyncMock(side_effect=AssertionError("A missing session must fail before database access"))
    conn = NS(fetchval=forbidden, fetchrow=forbidden, execute=forbidden)
    service = lifecycle.ConnectorLifecycle(NS(pool=object()), actor=actor,
        audit_logger=NS(lock_transaction=forbidden))

    # Keep the real load_session_principal: a direct dataclass caller cannot
    # bypass session validation merely by skipping the HTTP actor factory.
    with pytest.raises(lifecycle.ConnectorLifecycleError) as error:
        await service._authorize(conn, user.tenant_id, lifecycle.Permission.CONNECTORS_UPDATE,
            require_session=True)

    assert error.value.status_code == 401
    forbidden.assert_not_awaited()


@pytest.mark.parametrize("actor_kind", ["system", "admitted_human"])
async def test_system_admission_and_admitted_finalization_preserve_existing_authority_boundary(monkeypatch, actor_kind):
    user = person()
    actor = (lifecycle.ConnectorActor.system("connector_resolver") if actor_kind == "system"
        else lifecycle.ConnectorActor.human(user, "tenant_refresh", request=authenticated_request(user)))
    permission = lifecycle.Permission.CONNECTORS_UPDATE if actor_kind == "system" else None
    forbidden = AsyncMock(side_effect=AssertionError("This phase must not recheck isolation or login authority"))
    conn = NS(fetchval=forbidden, fetchrow=forbidden, execute=forbidden)
    audit = NS(lock_transaction=AsyncMock())
    service = lifecycle.ConnectorLifecycle(NS(pool=object()), actor=actor, audit_logger=audit)
    for name in ("lock_user_administration", "load_current_principal", "load_session_principal"):
        monkeypatch.setattr(lifecycle, name, forbidden)

    assert await service._begin(conn, user.tenant_id, permission) is actor
    if actor_kind == "system":
        assert await service._authorize(conn, user.tenant_id, permission, require_session=True) is actor

    audit.lock_transaction.assert_awaited_once_with(conn)
    forbidden.assert_not_awaited()

