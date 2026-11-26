"""Name the account for the old database client, for a stretch of code.

The old database client (``app.core.postgres_adapter.Client``) opens a
connection for each statement and takes the account (tenant) from two context
variables. When nothing is set it uses the nil account, and under row security
a read then finds no rows and a write changes none, with no error. Code that
runs outside a signed-in request (a signed web hook, a live call's task, a
background worker) has nothing set, so it must name the account itself.

``tenant_scope(tenant_id)`` does that for the ``with`` block and, on the way
out, puts back exactly what the caller had before (an earlier account, an
earlier bypass), whatever it was. It never turns the bypass on.

Enter and leave it in the same task: it restores through context variable
tokens, which only work in the context that created them. It is synchronous
on purpose, so it can wrap an ``await`` inside an ``async`` function.

``tenant_id`` must be a UUID. Anything else raises ``ValueError`` before any
context variable is touched, because the old client writes the value into a
``SET LOCAL`` statement.
"""
from __future__ import annotations

import contextvars
import uuid
from contextlib import contextmanager, nullcontext
from typing import ContextManager, Iterator, Optional

from app.core.security.tenant_isolation import (
    _bypass_rls,
    _tenant_context,
    clear_tenant_context,
)


@contextmanager
def tenant_scope(tenant_id: object) -> Iterator[str]:
    canonical = str(uuid.UUID(str(tenant_id)))
    tenant_token = _tenant_context.set(canonical)
    try:
        bypass_token = _bypass_rls.set(False)
    except BaseException:
        _tenant_context.reset(tenant_token)
        raise
    try:
        yield canonical
    finally:
        try:
            _bypass_rls.reset(bypass_token)
            _tenant_context.reset(tenant_token)
        except ValueError:
            # Closed from another context (a task destroyed while it was still
            # waiting, at shutdown). That context never had these values, so
            # there is nothing to put back.
            pass


@contextmanager
def bypass_scope(tenant_id: object = None) -> Iterator[Optional[str]]:
    """Bypass row security for the ``with`` block, then put back what was there.

    For a call's final settlement, which must run with the bypass on and the
    call's account named. A value that is empty or not a UUID names no account
    (None); it never raises. On the way out the earlier bypass and account are
    put back, also on an exception, so a long-lived task (a watchdog) does not
    keep the last call's account.
    """
    try:
        canonical = str(uuid.UUID(str(tenant_id))) if tenant_id else None
    except ValueError:
        canonical = None
    tenant_token = _tenant_context.set(canonical)
    try:
        bypass_token = _bypass_rls.set(True)
    except BaseException:
        _tenant_context.reset(tenant_token)
        raise
    try:
        yield canonical
    finally:
        try:
            _bypass_rls.reset(bypass_token)
            _tenant_context.reset(tenant_token)
        except ValueError:
            # Closed from another context: nothing to put back there.
            pass


def context_without_account() -> contextvars.Context:
    """A copy of the current context with no account, no bypass and no user.

    For a long-lived task started from inside a signed-in request (a listener
    or a watchdog). ``asyncio.create_task`` copies the creator's context, so a
    task started by a platform admin's request would keep that admin's bypass
    and account until the process restarts. Start it like this instead:
    ``asyncio.create_task(coro, context=context_without_account())``
    (Python 3.11 and later). Every other context variable (logging, tracing)
    is kept; the caller's own context is not changed.
    """
    ctx = contextvars.copy_context()
    ctx.run(clear_tenant_context)
    return ctx


def tenant_scope_if_account(tenant_id: object) -> ContextManager:
    """``tenant_scope`` for an account id; for anything else, name nothing.

    For callers that are handed an id they did not make themselves. A value
    that is not a UUID never reaches a ``SET LOCAL`` statement: the block then
    runs exactly as it did before (no account named, so row security shows it
    no rows, and ``rls_context_missing`` is logged).
    """
    try:
        uuid.UUID(str(tenant_id))
    except ValueError:
        return nullcontext()
    return tenant_scope(tenant_id)
