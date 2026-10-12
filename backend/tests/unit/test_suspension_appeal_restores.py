"""A granted appeal restores the suspended tenant or user.

Bug hunt 2026-10-10 (db-schema-core-3): review_appeal wrote the decision, left
the ``async with pool.acquire()`` block, then called ``conn.fetchrow`` on the
released connection. asyncpg raises InterfaceError ("connection has been
released back to the pool"), so the request returned 500 after recording
'granted' and the customer stayed suspended.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

from app.domain.services.suspension_service import SuspensionService


class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class _Conn:
    def __init__(self, target):
        self.target, self.released = target, False

    def _check(self):
        if self.released:
            raise RuntimeError("cannot call Connection.fetchrow(): connection has been released back to the pool")

    def transaction(self):
        return _Tx()

    async def execute(self, *_):
        self._check()
        return "OK"

    async def fetchrow(self, sql, *_):
        self._check()
        return self.target


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_):
        self.conn.released = True
        return False


class _Pool:
    def __init__(self, target):
        self.target = target

    def acquire(self):
        return _Acquire(_Conn(self.target))


async def test_a_granted_tenant_appeal_restores_the_tenant():
    tenant, admin = uuid.uuid4(), uuid.uuid4()
    service = SuspensionService(db_pool=_Pool({"target_type": "tenant", "target_id": tenant}))
    service.restore_tenant, service.restore_user = AsyncMock(), AsyncMock()

    result = await service.review_appeal(uuid.uuid4(), admin, "granted", "Reviewed, reinstated")

    assert result["decision"] == "granted"
    service.restore_tenant.assert_awaited_once()
    assert service.restore_tenant.await_args.args[0] == tenant
    service.restore_user.assert_not_awaited()


async def test_a_denied_appeal_restores_nothing():
    service = SuspensionService(db_pool=_Pool({"target_type": "tenant", "target_id": uuid.uuid4()}))
    service.restore_tenant, service.restore_user = AsyncMock(), AsyncMock()
    await service.review_appeal(uuid.uuid4(), uuid.uuid4(), "denied", "Upheld")
    service.restore_tenant.assert_not_awaited()
