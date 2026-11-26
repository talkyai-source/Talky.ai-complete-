"""A call's final settlement turns row-security bypass on and names the call's
account only around the settlement call, then puts back what the task had.

Three places in lifecycle.py do this (recovered outbound orphan, answered
outbound in ``_on_call_ended``, pre-answer outbound in ``_on_call_ended``).
They used to set the context variables and never restore them, so a long-lived
task (a watchdog) kept "bypass on + the last call's account" afterwards.
"""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.security.tenant_isolation import (
    get_bypass_rls,
    get_current_tenant_id,
    set_bypass_rls,
    set_current_tenant_id,
)
from app.core.security.tenant_scope import bypass_scope
from app.domain.services.telephony import lifecycle

T1 = "20000000-0000-4000-8000-000000000001"
T2 = "20000000-0000-4000-8000-000000000002"
CLEAN = (False, None)


def _ctx():
    return (get_bypass_rls(), get_current_tenant_id())


@pytest.fixture(autouse=True)
def _clean_task_context():
    set_bypass_rls(False)
    set_current_tenant_id(None)
    yield
    set_bypass_rls(False)
    set_current_tenant_id(None)


# ---------------------------------------------------------------- the helper


def test_helper_sets_bypass_and_account_then_restores():
    with bypass_scope(T1) as seen:
        assert seen == T1
        assert _ctx() == (True, T1)
    assert _ctx() == CLEAN


def test_helper_restores_the_tasks_own_values():
    set_bypass_rls(False)
    set_current_tenant_id(T2)
    with bypass_scope(T1):
        assert _ctx() == (True, T1)
    assert _ctx() == (False, T2)


def test_helper_restores_on_exception():
    set_current_tenant_id(T2)
    with pytest.raises(RuntimeError):
        with bypass_scope(T1):
            raise RuntimeError("boom")
    assert _ctx() == (False, T2)


def test_helper_nested_restores_each_level():
    with bypass_scope(T1):
        with bypass_scope(T2):
            assert _ctx() == (True, T2)
        assert _ctx() == (True, T1)
    assert _ctx() == CLEAN


@pytest.mark.parametrize("value", [None, "", "tenant-1", "not-a-uuid", 0])
def test_helper_names_no_account_for_empty_or_non_uuid_and_does_not_raise(value):
    set_current_tenant_id(T2)
    with bypass_scope(value) as seen:
        assert seen is None
        assert _ctx() == (True, None)
    assert _ctx() == (False, T2)


def test_helper_canonicalises_an_uppercase_uuid():
    with bypass_scope(T1.upper()) as seen:
        assert seen == T1


# ------------------------------------------------------------ shared fakes


class _RecordingCallService:
    seen: list = []
    raise_with: Exception | None = None

    def __init__(self, **_kwargs) -> None:
        pass

    async def handle_call_status(self, **_kwargs):
        type(self).seen.append(_ctx())
        if type(self).raise_with is not None:
            raise type(self).raise_with
        return SimpleNamespace(durable=True, terminal_outcome="x", error=None)


@pytest.fixture
def fake_call_service(monkeypatch):
    _RecordingCallService.seen = []
    _RecordingCallService.raise_with = None
    monkeypatch.setattr(lifecycle, "CallService", _RecordingCallService)
    return _RecordingCallService


class _Anything:
    """State backend stand-in: every method is a no-op returning None."""

    def __init__(self, voice_session=None):
        self._vs = voice_session

    def get_voice_session(self, _call_id):
        return self._vs

    def pop_voice_session(self, _call_id):
        return self._vs

    def __getattr__(self, _name):
        return lambda *a, **k: None


def _container():
    return SimpleNamespace(
        is_initialized=True,
        db_pool=object(),
        db_client=object(),
        redis=None,
        _queue_service=None,
    )


@pytest.fixture
def teardown_env(monkeypatch):
    from app.core import container as container_module
    from app.core import db_utils
    from app.domain.services import call_status, global_concurrency

    container = _container()
    monkeypatch.setattr(container_module, "get_container", lambda: container)
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: None)
    monkeypatch.setattr(global_concurrency, "release_lease", AsyncMock())
    monkeypatch.setattr(call_status, "record_call_state_by_provider_id", AsyncMock())

    def install(voice_session=None, row=None):
        monkeypatch.setattr(lifecycle, "_state", lambda: _Anything(voice_session))

        class _Conn:
            async def fetchrow(self, _query, *_args):
                return row

        @asynccontextmanager
        async def acquire_with_tenant(_pool, _tenant_id):
            yield _Conn()

        monkeypatch.setattr(db_utils, "acquire_with_tenant", acquire_with_tenant)

    return install


def _forget(call_id):
    lifecycle._ended_calls_in_flight.discard(call_id)
    lifecycle._ended_calls_logically_completed.discard(call_id)


async def _end(call_id):
    _forget(call_id)
    try:
        await lifecycle._on_call_ended(call_id)
    except Exception:
        pass
    finally:
        _forget(call_id)


# ------------------------------------------- 1. recovered outbound settlement


def _recovery(tenant):
    return {
        "durable_call_id": "00000000-0000-4000-8000-0000000000aa",
        "provider_session_id": "talky-out-recovered",
        "tenant_id": tenant,
        "duration_seconds": 3,
        "was_answered": True,
        "ledger_entry": {},
    }


@pytest.fixture
def recovered_env(monkeypatch, fake_call_service):
    from app.core import container as container_module

    monkeypatch.setattr(container_module, "get_container", lambda: _container())
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: None)
    monkeypatch.setattr(
        lifecycle,
        "_hydrate_orphan_recovery_context",
        AsyncMock(return_value={"logical_settled": True}),
    )
    return fake_call_service


async def test_recovered_settlement_sees_bypass_and_the_calls_account_inside(recovered_env):
    await lifecycle._settle_recovered_outbound_call(_recovery(T1))
    assert recovered_env.seen == [(True, T1)]


async def test_recovered_settlement_puts_back_the_tasks_context(recovered_env):
    await lifecycle._settle_recovered_outbound_call(_recovery(T1))
    assert _ctx() == CLEAN


async def test_recovered_settlement_puts_back_the_tasks_own_values(recovered_env):
    set_current_tenant_id(T2)
    await lifecycle._settle_recovered_outbound_call(_recovery(T1))
    assert _ctx() == (False, T2)


async def test_recovered_settlement_that_raises_still_puts_back_the_context(recovered_env):
    recovered_env.raise_with = RuntimeError("db down")
    with pytest.raises(RuntimeError):
        await lifecycle._settle_recovered_outbound_call(_recovery(T1))
    assert _ctx() == CLEAN


async def test_second_settlement_in_one_task_does_not_see_the_first_calls_account(recovered_env):
    await lifecycle._settle_recovered_outbound_call(_recovery(T1))
    await lifecycle._settle_recovered_outbound_call(_recovery(None))
    assert recovered_env.seen == [(True, T1), (True, None)]


# ------------------------------------------------- 2. answered outbound branch


def _answered_session(tenant):
    return SimpleNamespace(
        call_id="answered-call-1",
        _dialer_call_id="00000000-0000-4000-8000-0000000000bb",
        _dialer_tenant_id=tenant,
        _hangup_reason=None,
        pipeline=None,
    )


async def test_answered_outbound_sees_bypass_and_the_calls_account_inside(
    teardown_env, fake_call_service
):
    teardown_env(voice_session=_answered_session(T1))
    await _end("answered-call-1")
    assert fake_call_service.seen == [(True, T1)]


async def test_answered_outbound_puts_back_the_tasks_context(teardown_env, fake_call_service):
    teardown_env(voice_session=_answered_session(T1))
    await _end("answered-call-2")
    assert fake_call_service.seen == [(True, T1)]
    assert _ctx() == CLEAN


async def test_answered_outbound_puts_back_the_tasks_own_values(teardown_env, fake_call_service):
    teardown_env(voice_session=_answered_session(T1))
    set_current_tenant_id(T2)
    await _end("answered-call-3")
    assert fake_call_service.seen == [(True, T1)]
    assert _ctx() == (False, T2)


async def test_answered_outbound_that_raises_still_puts_back_the_context(
    teardown_env, fake_call_service
):
    fake_call_service.raise_with = RuntimeError("db down")
    teardown_env(voice_session=_answered_session(T1))
    await _end("answered-call-4")
    assert fake_call_service.seen == [(True, T1)]
    assert _ctx() == CLEAN


async def test_second_answered_settlement_with_unknown_account_sees_none(
    teardown_env, fake_call_service
):
    teardown_env(voice_session=_answered_session(T1))
    await _end("answered-call-5")
    teardown_env(voice_session=_answered_session(None))
    await _end("answered-call-6")
    assert fake_call_service.seen == [(True, T1), (True, None)]


# ----------------------------------------------------- 3. pre-answer outbound


def _row(tenant):
    return {
        "id": "00000000-0000-4000-8000-0000000000cc",
        "tenant_id": tenant,
        "answered_at": None,
        "db_now": None,
    }


async def test_preanswer_sees_bypass_and_the_calls_account_inside(
    teardown_env, fake_call_service
):
    teardown_env(row=_row(T1))
    await _end("talky-out-pre-1")
    assert fake_call_service.seen == [(True, T1)]


async def test_preanswer_puts_back_the_tasks_context(teardown_env, fake_call_service):
    teardown_env(row=_row(T1))
    await _end("talky-out-pre-2")
    assert fake_call_service.seen == [(True, T1)]
    assert _ctx() == CLEAN


async def test_preanswer_puts_back_the_tasks_own_values(teardown_env, fake_call_service):
    teardown_env(row=_row(T1))
    set_current_tenant_id(T2)
    await _end("talky-out-pre-3")
    assert fake_call_service.seen == [(True, T1)]
    assert _ctx() == (False, T2)


async def test_preanswer_that_raises_still_puts_back_the_context(teardown_env, fake_call_service):
    fake_call_service.raise_with = RuntimeError("db down")
    teardown_env(row=_row(T1))
    await _end("talky-out-pre-4")
    assert fake_call_service.seen == [(True, T1)]
    assert _ctx() == CLEAN


async def test_second_preanswer_settlement_sees_its_own_account(teardown_env, fake_call_service):
    teardown_env(row=_row(T1))
    await _end("talky-out-pre-5")
    teardown_env(row=_row(T2))
    await _end("talky-out-pre-6")
    assert fake_call_service.seen == [(True, T1), (True, T2)]
    assert _ctx() == CLEAN
