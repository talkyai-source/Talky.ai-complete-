from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.v1.dependencies import CurrentUser, require_admin, require_admin_tenant
from app.api.v1.endpoints.telephony_sip import router


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": "/telephony/sip/trunks",
            "raw_path": b"/telephony/sip/trunks",
            "query_string": b"",
            "headers": [],
            "client": ("test", 1),
            "server": ("test", 443),
        }
    )


def _admin_dependency():
    included = router.routes[0]
    dependency = next(
        item.dependency
        for item in included.include_context.dependencies
        if item.dependency is require_admin_tenant
    )
    return dependency


@pytest.mark.asyncio
async def test_sip_router_denies_readonly_and_regular_tenant_users():
    for role in ("readonly", "user"):
        user = CurrentUser(
            id=str(uuid4()),
            email=f"{role}@example.com",
            tenant_id=str(uuid4()),
            role=role,
        )
        with pytest.raises(HTTPException) as exc:
            await require_admin(user)
        assert exc.value.status_code == 403
        assert "Admin access required" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_sip_router_allows_tenant_admin_role():
    user = CurrentUser(
        id=str(uuid4()),
        email="admin@example.com",
        tenant_id=str(uuid4()),
        role="tenant_admin",
    )
    assert await require_admin(user) is user
    assert await _admin_dependency()(user) is user


def test_every_sip_route_inherits_the_admin_dependency():
    assert router.routes
    for route in router.routes:
        assert any(
            dependency.dependency is require_admin_tenant
            for dependency in route.include_context.dependencies
        )
