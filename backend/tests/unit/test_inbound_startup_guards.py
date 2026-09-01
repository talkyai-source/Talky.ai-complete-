"""Production inbound startup must prove ownership and adapter capabilities."""

from __future__ import annotations

import pytest

from app.core.inbound_startup import (
    platform_inbound_enabled,
    validate_live_production_inbound_adapter,
    validate_production_inbound_adapter,
    validate_production_inbound_database_role,
    validate_production_inbound_state_backend,
)


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _Connection:
    def __init__(self, value=True, *, failure=None):
        self.value = value
        self.failure = failure

    def transaction(self):
        return _Transaction()

    async def execute(self, *_args):
        return "SET"

    async def fetchval(self, query):
        assert "platform_runtime_controls" in query
        if self.failure:
            raise self.failure
        return self.value


class _RoleConnection:
    def __init__(self, *, superuser=False, bypass_rls=False, failure=None):
        self.superuser = superuser
        self.bypass_rls = bypass_rls
        self.failure = failure

    async def fetchrow(self, query):
        assert "rolsuper" in query
        assert "rolbypassrls" in query
        if self.failure:
            raise self.failure
        return {
            "role_name": "talky_runtime",
            "rolsuper": self.superuser,
            "rolbypassrls": self.bypass_rls,
        }


class _Acquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_args):
        return False


class _Pool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return _Acquire(self.connection)


@pytest.mark.asyncio
async def test_production_inbound_switch_lookup_fails_closed():
    assert (
        await platform_inbound_enabled(_Pool(_Connection(True)), environment="production") is True
    )
    with pytest.raises(RuntimeError, match="Cannot validate"):
        await platform_inbound_enabled(
            _Pool(_Connection(failure=OSError("db unavailable"))),
            environment="production",
        )


@pytest.mark.asyncio
async def test_enabled_production_inbound_requires_rls_enforced_role():
    await validate_production_inbound_database_role(
        _Pool(_RoleConnection()),
        environment="production",
        inbound_enabled=True,
    )
    for connection in (
        _RoleConnection(superuser=True),
        _RoleConnection(bypass_rls=True),
    ):
        with pytest.raises(RuntimeError, match="NOSUPERUSER NOBYPASSRLS"):
            await validate_production_inbound_database_role(
                connection,
                environment="production",
                inbound_enabled=True,
            )
    with pytest.raises(RuntimeError, match="cannot validate"):
        await validate_production_inbound_database_role(
            _Pool(_RoleConnection(failure=OSError("db unavailable"))),
            environment="production",
            inbound_enabled=True,
        )


@pytest.mark.asyncio
async def test_disabled_or_nonproduction_inbound_skips_role_constraint():
    await validate_production_inbound_database_role(
        None,
        environment="production",
        inbound_enabled=False,
    )
    await validate_production_inbound_database_role(
        None,
        environment="development",
        inbound_enabled=True,
    )


def test_enabled_production_inbound_rejects_auto_or_wrong_adapter():
    with pytest.raises(RuntimeError, match="TELEPHONY_ADAPTER=asterisk"):
        validate_production_inbound_adapter(
            environment="production",
            inbound_enabled=True,
            configured_adapter="auto",
            adapter=object(),
        )
    with pytest.raises(RuntimeError, match="non-Asterisk"):
        validate_production_inbound_adapter(
            environment="production",
            inbound_enabled=True,
            configured_adapter="asterisk",
            adapter=object(),
        )


def test_disabled_or_nonproduction_inbound_does_not_constrain_adapter():
    validate_production_inbound_adapter(
        environment="production",
        inbound_enabled=False,
        configured_adapter="auto",
        adapter=object(),
    )


def test_production_adapter_requires_answer_persistence_capability():
    from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter

    adapter = AsteriskAdapter()
    adapter.set_inbound_answered_persist_callback = None
    with pytest.raises(
        RuntimeError,
        match="set_inbound_answered_persist_callback",
    ):
        validate_production_inbound_adapter(
            environment="production",
            inbound_enabled=True,
            configured_adapter="asterisk",
            adapter=adapter,
        )


def test_production_adapter_requires_terminal_proof_persistence_capability():
    from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter

    adapter = AsteriskAdapter()
    adapter.set_inbound_terminal_proof_persist_callback = None
    with pytest.raises(
        RuntimeError,
        match="set_inbound_terminal_proof_persist_callback",
    ):
        validate_production_inbound_adapter(
            environment="production",
            inbound_enabled=True,
            configured_adapter="asterisk",
            adapter=adapter,
        )


def test_live_production_adapter_requires_all_answer_callbacks_wired():
    from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter

    adapter = AsteriskAdapter()
    adapter._connected_flag = True
    with pytest.raises(RuntimeError, match="callbacks are not wired"):
        validate_live_production_inbound_adapter(
            environment="production",
            inbound_enabled=True,
            configured_adapter="asterisk",
            adapter=adapter,
        )

    async def callback(*_args, **_kwargs):
        return None

    adapter.set_inbound_admission_callback(callback)
    adapter.set_inbound_answered_persist_callback(callback)
    adapter.set_inbound_terminal_proof_persist_callback(callback)
    adapter.set_inbound_admission_finalizer(callback)
    validate_live_production_inbound_adapter(
        environment="production",
        inbound_enabled=True,
        configured_adapter="asterisk",
        adapter=adapter,
    )


def test_live_validator_cannot_be_bypassed_by_an_existing_wrong_adapter():
    class AlreadyConnectedWrongAdapter:
        connected = True

    with pytest.raises(RuntimeError, match="non-Asterisk"):
        validate_live_production_inbound_adapter(
            environment="production",
            inbound_enabled=True,
            configured_adapter="asterisk",
            adapter=AlreadyConnectedWrongAdapter(),
        )


@pytest.mark.parametrize("configured", ["memory", "auto", ""])
def test_enabled_production_inbound_rejects_local_state(configured):
    from app.domain.services.telephony.state_backend import LocalOnlyStateBackend

    with pytest.raises(RuntimeError, match="TELEPHONY_STATE_BACKEND=redis"):
        validate_production_inbound_state_backend(
            environment="production",
            inbound_enabled=True,
            configured_backend=configured,
            state_backend=LocalOnlyStateBackend(),
        )


def test_redis_configuration_rejects_local_fallback():
    from app.domain.services.telephony.state_backend import LocalOnlyStateBackend

    with pytest.raises(RuntimeError, match="fell back"):
        validate_production_inbound_state_backend(
            environment="production",
            inbound_enabled=True,
            configured_backend="redis",
            state_backend=LocalOnlyStateBackend(),
        )
    validate_production_inbound_adapter(
        environment="development",
        inbound_enabled=True,
        configured_adapter="freeswitch",
        adapter=object(),
    )


def test_production_redis_backend_requires_atomic_inverse_claim():
    from app.domain.services.telephony.state_backend import RedisBackedStateBackend

    backend = object.__new__(RedisBackedStateBackend)
    backend.claim_cleanup_obligation_if_absent = None

    with pytest.raises(RuntimeError, match="claim_cleanup_obligation_if_absent"):
        validate_production_inbound_state_backend(
            environment="production",
            inbound_enabled=True,
            configured_backend="redis",
            state_backend=backend,
        )
