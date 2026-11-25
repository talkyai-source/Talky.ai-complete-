"""run_crm_sync names its account with the bypass OFF and puts the caller's
context back afterwards."""
import types

import pytest

from app.core.security.tenant_isolation import (
    _bypass_rls,
    _tenant_context,
    get_bypass_rls,
    get_current_tenant_id,
)
from app.services import crm_sync_service as svc

X = "40000000-0000-4000-8000-000000000001"
Y = "40000000-0000-4000-8000-000000000002"


@pytest.fixture
def caller_context():
    tokens = (_tenant_context.set(Y), _bypass_rls.set(True))
    yield
    _bypass_rls.reset(tokens[1])
    _tenant_context.reset(tokens[0])


@pytest.fixture
def fake_sync(monkeypatch):
    seen = {}

    class FakeService:
        def __init__(self, *a, **k):
            pass

        async def sync_call(self, tenant, call_id, reason=""):
            seen["tenant"] = get_current_tenant_id()
            seen["bypass"] = get_bypass_rls()
            return types.SimpleNamespace(success=False, providers=[], crm_call_id=None)

    container = types.SimpleNamespace(
        is_initialized=True, db_pool=object(), db_client=object()
    )
    monkeypatch.setattr("app.core.container.get_container", lambda: container)
    monkeypatch.setattr(svc, "CRMSyncService", FakeService)
    return seen


async def test_sync_runs_for_its_account_with_bypass_off_then_restores(caller_context, fake_sync):
    result = await svc.run_crm_sync("call-1", tenant_id=X)
    assert result is not None
    assert fake_sync == {"tenant": X, "bypass": False}
    assert get_current_tenant_id() == Y
    assert get_bypass_rls() is True


async def test_tenant_that_is_not_a_uuid_returns_none_and_touches_nothing(caller_context, fake_sync):
    assert await svc.run_crm_sync("call-1", tenant_id="not-a-uuid") is None
    assert fake_sync == {}
    assert get_current_tenant_id() == Y
    assert get_bypass_rls() is True
