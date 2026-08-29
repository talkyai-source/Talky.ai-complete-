"""Platform-admin inbound API contract and authorization tests."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

import app.api.v1.endpoints.admin.inbound as admin_inbound
from app.api.v1.dependencies import CurrentUser, require_platform_admin
from app.api.v1.schemas.inbound_campaigns import (
    AdminAssignmentVersionRequest,
    PlatformInboundControlsPatch,
)
from app.domain.services.inbound_campaign_service import InboundConflictError


ADMIN = CurrentUser(
    id="11111111-1111-1111-1111-111111111111",
    email="admin@example.test",
    role="platform_admin",
)


def test_admin_router_exposes_operational_contract():
    routes = {
        (route.path, method)
        for route in admin_inbound.router.routes
        for method in (route.methods or set())
    }
    expected = {
        ("/inbound/overview", "GET"),
        ("/inbound/assignments", "GET"),
        ("/inbound/campaigns", "GET"),
        ("/inbound/controls", "GET"),
        ("/inbound/controls", "PATCH"),
        ("/inbound/assignments/{assignment_id}/quarantine", "POST"),
        ("/inbound/assignments/{assignment_id}/unquarantine", "POST"),
        ("/inbound/reassignments", "GET"),
        ("/inbound/reassignments", "POST"),
        ("/inbound/reassignments/{request_id}/approve", "POST"),
    }
    assert expected <= routes

    for route in admin_inbound.router.routes:
        if not route.path.startswith("/inbound"):
            continue
        dependency_names = {
            dependency.call.__name__
            for dependency in route.dependant.dependencies
            if dependency.call is not None
        }
        assert "require_platform_admin" in dependency_names


@pytest.mark.asyncio
async def test_platform_admin_dependency_rejects_tenant_admin():
    tenant_admin = CurrentUser(
        id=ADMIN.id,
        email="tenant@example.test",
        tenant_id="22222222-2222-2222-2222-222222222222",
        role="tenant_admin",
    )
    with pytest.raises(HTTPException) as exc:
        await require_platform_admin(tenant_admin)
    assert exc.value.status_code == 403
    assert await require_platform_admin(ADMIN) is ADMIN


def test_admin_mutations_require_bounded_idempotency_key():
    with pytest.raises(HTTPException) as exc:
        admin_inbound._key("short")
    assert exc.value.status_code == 400
    assert admin_inbound._key("valid-key-123") == "valid-key-123"


class _Service:
    def __init__(self):
        self.calls = []

    async def set_platform_controls(self, **kwargs):
        self.calls.append(("controls", kwargs))
        return {
            "inbound_enabled": True,
            "recording_enabled": False,
            "transfer_enabled": False,
            "settlement_enabled": True,
            "version": 4,
            "reason": "controlled rollout",
            "updated_by": ADMIN.id,
            "updated_at": None,
        }

    async def set_assignment_quarantine(self, **kwargs):
        self.calls.append(("quarantine", kwargs))
        return {"id": kwargs["assignment_id"], "status": "quarantined", "version": 3}


@pytest.mark.asyncio
async def test_admin_mutations_forward_actor_version_reason_and_key(monkeypatch):
    service = _Service()
    monkeypatch.setattr(admin_inbound, "InboundCampaignService", lambda _pool: service)
    controls = await admin_inbound.patch_platform_inbound_controls(
        PlatformInboundControlsPatch(
            inbound_enabled=True,
            recording_enabled=False,
            transfer_enabled=False,
            settlement_enabled=True,
            reason="controlled rollout",
            expected_version=3,
        ),
        user=ADMIN,
        idempotency_key="controls-key-123",
        db_pool=object(),
    )
    assert controls["version"] == 4
    _, control_args = service.calls[0]
    assert control_args["actor_id"] == ADMIN.id
    assert control_args["payload"]["expected_version"] == 3
    assert control_args["idempotency_key"] == "controls-key-123"

    assignment = "33333333-3333-3333-3333-333333333333"
    await admin_inbound.quarantine_inbound_assignment(
        assignment,
        AdminAssignmentVersionRequest(expected_version=2, reason="suspected route conflict"),
        user=ADMIN,
        idempotency_key="quarantine-key-123",
        db_pool=object(),
    )
    _, quarantine_args = service.calls[1]
    assert quarantine_args["assignment_id"] == assignment
    assert quarantine_args["expected_version"] == 2
    assert quarantine_args["reason"] == "suspected route conflict"
    assert quarantine_args["quarantined"] is True


def test_service_conflicts_keep_stable_admin_error_shape():
    with pytest.raises(HTTPException) as exc:
        admin_inbound._raise_service(
            InboundConflictError("Version conflict", code="version_conflict")
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == {
        "code": "version_conflict",
        "message": "Version conflict",
    }
