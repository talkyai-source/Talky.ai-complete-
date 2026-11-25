"""A long-lived task started from inside an admin's request must not keep the
admin's account and bypass for ever (asyncio.create_task copies the context)."""
import asyncio
import contextvars

import pytest

from app.core.security.tenant_isolation import (
    _bypass_rls,
    _tenant_context,
    _user_context,
    get_bypass_rls,
    get_current_tenant_id,
    get_current_user_context,
)
from app.core.security.tenant_scope import context_without_account

X = "30000000-0000-4000-8000-000000000001"
_other = contextvars.ContextVar("other_var_20261125", default="unset")


@pytest.fixture
def admin_context():
    tokens = (
        _tenant_context.set(X),
        _bypass_rls.set(True),
        _user_context.set(object()),
        _other.set("kept"),
    )
    yield
    _other.reset(tokens[3])
    _user_context.reset(tokens[2])
    _bypass_rls.reset(tokens[1])
    _tenant_context.reset(tokens[0])


def test_context_without_account_clears_only_the_copy(admin_context):
    user = get_current_user_context()
    ctx = context_without_account()
    assert ctx.run(get_current_tenant_id) is None
    assert ctx.run(get_bypass_rls) is False
    assert ctx.run(get_current_user_context) is None
    assert ctx.run(_other.get) == "kept"
    assert get_current_tenant_id() == X
    assert get_bypass_rls() is True
    assert get_current_user_context() is user


async def test_adapter_listener_task_carries_no_account(admin_context):
    from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter

    adapter = AsteriskAdapter()
    seen = {}

    async def fake_listener():
        seen["tenant"] = get_current_tenant_id()
        seen["bypass"] = get_bypass_rls()
        seen["user"] = get_current_user_context()

    adapter._ari_event_listener = fake_listener
    adapter._start_listener_task()
    await adapter._ws_task
    assert seen == {"tenant": None, "bypass": False, "user": None}


async def test_watchdog_tasks_carry_no_account(admin_context, monkeypatch):
    from app.api.v1.endpoints import telephony_bridge as tb

    seen = {}

    def recorder(name):
        async def _run():
            seen[name] = (get_current_tenant_id(), get_bypass_rls())
        return _run

    monkeypatch.setattr(tb, "_session_watchdog", recorder("session"))
    monkeypatch.setattr(tb, "_media_session_watchdog", recorder("media"))
    monkeypatch.setattr(tb, "_account_call_authority_watchdog", recorder("authority"))
    monkeypatch.setattr(tb, "_watchdog_task", None)
    monkeypatch.setattr(tb, "_media_watchdog_task", None)
    monkeypatch.setattr(tb, "_account_authority_watchdog_task", None)
    try:
        tb.ensure_session_management_started()
        tasks = [
            tb._watchdog_task,
            tb._media_watchdog_task,
            tb._account_authority_watchdog_task,
        ]
        await asyncio.gather(*tasks)
    finally:
        for t in (tb._watchdog_task, tb._media_watchdog_task, tb._account_authority_watchdog_task):
            if t is not None and not t.done():
                t.cancel()
    assert seen == {
        "session": (None, False),
        "media": (None, False),
        "authority": (None, False),
    }
