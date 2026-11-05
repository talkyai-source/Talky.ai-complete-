"""QA F3-05: an outbound call ends at the account's own maximum call length.

The account's ``max_call_duration_seconds`` (tenant_call_limits) was applied
to inbound calls only; outbound calls ran to the fixed platform caps.
"""
from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.domain.services.telephony import lifecycle

TENANT = "20000000-0000-4000-8000-0000000000f3"


def _account_limit(monkeypatch, value=None, error=None):
    queries = []

    class _Conn:
        async def fetchval(self, query, *args):
            queries.append((query, args))
            if error is not None:
                raise error
            return value

    @asynccontextmanager
    async def scoped(pool, tenant_id, **kwargs):
        assert tenant_id == TENANT
        yield _Conn()

    monkeypatch.setattr("app.core.db_utils.acquire_with_tenant", scoped)
    monkeypatch.setattr(
        "app.core.container.get_container", lambda: SimpleNamespace(db_pool=object())
    )
    return queries


@pytest.mark.asyncio
async def test_outbound_call_takes_the_accounts_shorter_maximum(monkeypatch):
    queries = _account_limit(monkeypatch, value=120)
    vs = SimpleNamespace(call_id="call-f305", _dialer_tenant_id=TENANT)
    await lifecycle._apply_account_call_cap(vs)
    assert vs._max_call_duration_seconds == 120
    # Explicit tenant predicate: never rely on RLS alone.
    assert "tenant_id = $1" in queries[0][0] and queries[0][1] == (TENANT,)
    # The watchdog's deadline is now the account's limit, below the soft cap.
    assert lifecycle._effective_deadline_seconds(vs, max_duration_s=600, soft_cap_s=300) == 120


@pytest.mark.asyncio
async def test_platform_cap_stands_when_the_account_allows_longer(monkeypatch):
    _account_limit(monkeypatch, value=3600)
    vs = SimpleNamespace(call_id="call-f305", _dialer_tenant_id=TENANT)
    await lifecycle._apply_account_call_cap(vs)
    assert not hasattr(vs, "_max_call_duration_seconds")


@pytest.mark.asyncio
async def test_unreadable_account_limits_leave_the_platform_cap(monkeypatch):
    _account_limit(monkeypatch, error=RuntimeError("database unavailable"))
    vs = SimpleNamespace(call_id="call-f305", _dialer_tenant_id=TENANT)
    await lifecycle._apply_account_call_cap(vs)  # never raises
    assert not hasattr(vs, "_max_call_duration_seconds")


def test_outbound_answer_path_starts_the_account_cap_lookup():
    """Guard, not behaviour: _on_new_call starts the lookup for calls that are
    not true inbound (inbound pins its own maximum at admission)."""
    source = inspect.getsource(lifecycle._on_new_call)
    assert "if not is_true_inbound:" in source
    assert "_apply_account_call_cap(voice_session)" in source
