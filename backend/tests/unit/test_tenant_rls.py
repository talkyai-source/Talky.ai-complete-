from __future__ import annotations

import pytest

from app.core.tenant_rls import apply_tenant_rls_context


class FakeConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def execute(self, query: str, value: str):
        self.calls.append((" ".join(query.split()), value))
        return "SELECT 1"


@pytest.mark.asyncio
async def test_apply_tenant_rls_context_sets_tenant_and_user():
    conn = FakeConn()
    await apply_tenant_rls_context(
        conn,
        tenant_id="tenant-1",
        user_id="user-1",
        request_id="req-1",
    )
    assert conn.calls == [
        ("SELECT set_config('app.current_tenant_id', $1, false)", "tenant-1"),
        ("SELECT set_config('app.current_user_id', $1, false)", "user-1"),
        ("SELECT set_config('app.current_request_id', $1, false)", "req-1"),
    ]


@pytest.mark.asyncio
async def test_apply_tenant_rls_context_clears_optional_context():
    conn = FakeConn()
    await apply_tenant_rls_context(conn, tenant_id="tenant-1")
    assert conn.calls == [
        ("SELECT set_config('app.current_tenant_id', $1, false)", "tenant-1"),
        ("SELECT set_config('app.current_user_id', $1, false)", ""),
        ("SELECT set_config('app.current_request_id', $1, false)", ""),
    ]


@pytest.mark.asyncio
async def test_apply_tenant_rls_context_requires_tenant_id():
    conn = FakeConn()
    with pytest.raises(ValueError, match="tenant_id is required"):
        await apply_tenant_rls_context(conn, tenant_id="")
