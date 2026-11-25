"""tenant_scope: names the account for the old database client, then puts back
exactly what the caller had.

The old client takes the account from two context variables and interpolates
the value into a ``SET LOCAL`` statement, so a value that is not a UUID must be
refused before any variable is touched.
"""
import pytest

from app.core.security.tenant_isolation import (
    _bypass_rls,
    _tenant_context,
    get_bypass_rls,
    get_current_tenant_id,
)
from app.core.security.tenant_scope import tenant_scope

T1 = "20000000-0000-4000-8000-000000000001"
T2 = "20000000-0000-4000-8000-000000000002"


@pytest.fixture
def restore_context():
    tokens = (_tenant_context.set(None), _bypass_rls.set(False))
    yield
    _bypass_rls.reset(tokens[1])
    _tenant_context.reset(tokens[0])


def test_sets_tenant_and_bypass_false_inside(restore_context):
    _bypass_rls.set(True)
    with tenant_scope(T1) as seen:
        assert seen == T1
        assert get_current_tenant_id() == T1
        assert get_bypass_rls() is False


def test_accepts_a_uuid_object_and_canonicalises(restore_context):
    import uuid

    with tenant_scope(uuid.UUID(T1)):
        assert get_current_tenant_id() == T1
    with tenant_scope(T1.upper().replace("-", "")):
        assert get_current_tenant_id() == T1


def test_restores_nothing_set(restore_context):
    with tenant_scope(T1):
        pass
    assert get_current_tenant_id() is None
    assert get_bypass_rls() is False


def test_restores_previous_tenant_and_previous_bypass_true(restore_context):
    _tenant_context.set(T2)
    _bypass_rls.set(True)
    with tenant_scope(T1):
        assert get_current_tenant_id() == T1
        assert get_bypass_rls() is False
    assert get_current_tenant_id() == T2
    assert get_bypass_rls() is True


def test_restores_after_an_exception(restore_context):
    _tenant_context.set(T2)
    _bypass_rls.set(True)
    with pytest.raises(RuntimeError):
        with tenant_scope(T1):
            raise RuntimeError("boom")
    assert get_current_tenant_id() == T2
    assert get_bypass_rls() is True


def test_nested_scopes_restore_in_order(restore_context):
    with tenant_scope(T1):
        with tenant_scope(T2):
            assert get_current_tenant_id() == T2
        assert get_current_tenant_id() == T1
        assert get_bypass_rls() is False
    assert get_current_tenant_id() is None


@pytest.mark.parametrize(
    "bad",
    [
        "",
        None,
        "not-a-uuid",
        "123",
        T1 + "x",
        "' OR 1=1 --",
        T1 + "'; DROP TABLE calls; --",
        "00000000-0000-0000-0000-000000000000'",
    ],
)
def test_non_uuid_raises_value_error_and_leaves_context_untouched(restore_context, bad):
    _tenant_context.set(T2)
    _bypass_rls.set(True)
    with pytest.raises(ValueError):
        with tenant_scope(bad):
            pytest.fail("the block must not run")
    assert get_current_tenant_id() == T2
    assert get_bypass_rls() is True


def test_if_account_names_a_uuid_and_restores(restore_context):
    from app.core.security.tenant_scope import tenant_scope_if_account

    _tenant_context.set(T2)
    _bypass_rls.set(True)
    with tenant_scope_if_account(T1):
        assert get_current_tenant_id() == T1
        assert get_bypass_rls() is False
    assert get_current_tenant_id() == T2
    assert get_bypass_rls() is True


@pytest.mark.parametrize("bad", ["", None, "test-tenant", "' OR 1=1 --", T1 + "'; DROP TABLE calls; --"])
def test_if_account_names_nothing_for_a_value_that_is_not_a_uuid(restore_context, bad):
    from app.core.security.tenant_scope import tenant_scope_if_account

    ran = []
    with tenant_scope_if_account(bad):
        # The block runs, with the context exactly as the caller left it: the
        # value never reaches a context variable, so never a SET LOCAL.
        ran.append((get_current_tenant_id(), get_bypass_rls()))
    assert ran == [(None, False)]
    assert get_current_tenant_id() is None
    assert get_bypass_rls() is False


def test_closing_a_waiting_scope_from_another_context_does_not_raise(restore_context):
    # What happens to a task destroyed while it waits inside the scope (at
    # shutdown): its clean-up runs in a context that never had these values.
    # It used to raise "was created in a different Context" (review, 25 Nov).
    import asyncio
    import contextvars

    async def body():
        with tenant_scope(T1):
            await asyncio.sleep(0)

    coro = body()
    contextvars.copy_context().run(coro.send, None)  # enter the scope, then wait
    other = contextvars.copy_context()
    other.run(coro.close)  # must not raise
    assert other.run(get_current_tenant_id) is None
    assert get_current_tenant_id() is None
