"""Actual ASGI appeal contracts with explicit synthetic principal/service ports."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from app.api.v1 import dependencies
from app.api.v1.endpoints import suspensions as api
from app.domain.services.suspension_service import SuspensionService


def make_app(role="platform_admin"):
    actor, hold, audit = uuid4(), uuid4(), uuid4()
    receipt = {"suspension_id":str(hold),"appeal_submitted":True,"status":"pending_review",
               "audit_event_id":str(audit),"changed":True,"hint_status":"failed",
               "notification_delivery_status":"unverified"}
    service = SimpleNamespace(submit_appeal=AsyncMock(return_value=receipt),
        review_appeal=AsyncMock(return_value={"decision":"denied","audit_event_id":str(audit),"changed":True}))
    app = FastAPI()
    principal = dependencies.CurrentUser(id=str(actor), email="synthetic@example.invalid", role=role)
    app.dependency_overrides[dependencies.get_current_user] = lambda: principal
    app.dependency_overrides[api.get_suspension_service] = lambda: service
    app.include_router(api.router)
    return app, service, actor, hold, receipt


@pytest.mark.parametrize("role", ["user", "tenant_admin", "partner_admin"])
@pytest.mark.parametrize("review", [False, True])
async def test_existing_platform_admin_boundary_is_explicit(role, review):
    app, service, actor, hold, receipt = make_app(role)
    suffix = "/review" if review else ""
    body = {"decision":"denied","response":"Reviewed"} if review else {"appeal_reason":"Review evidence"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://synthetic.invalid") as client:
        result = await client.post(f"/admin/suspensions/{hold}/appeal{suffix}", json=body)
    assert result.status_code == 403
    service.submit_appeal.assert_not_awaited()
    service.review_appeal.assert_not_awaited()


async def test_submission_passes_actual_operator_and_preserves_saved_failure_hint_receipt():
    app, service, actor, hold, receipt = make_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://synthetic.invalid") as client:
        result = await client.post(f"/admin/suspensions/{hold}/appeal", json={"appeal_reason":"Review evidence"})
    assert result.status_code == 200 and result.json() == receipt
    assert service.submit_appeal.await_args.kwargs == {
        "suspension_id":hold,"appeal_reason":"Review evidence","submitted_by":str(actor)}


async def test_review_returns_service_receipt_without_a_second_audit_dependency():
    app, service, actor, hold, receipt = make_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://synthetic.invalid") as client:
        result = await client.post(f"/admin/suspensions/{hold}/appeal/review", json={"decision":"denied","response":"Reviewed"})
    assert result.status_code == 200 and result.json()["audit_event_id"] == receipt["audit_event_id"]
    assert service.review_appeal.await_args.kwargs["reviewed_by"] == str(actor)


@pytest.mark.parametrize("reason", ["", "x" * 4001])
async def test_api_rejects_empty_or_excessive_reason_before_mutation(reason):
    app, service, actor, hold, receipt = make_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://synthetic.invalid") as client:
        result = await client.post(f"/admin/suspensions/{hold}/appeal", json={"appeal_reason":reason})
    assert result.status_code == 422
    service.submit_appeal.assert_not_awaited()


@pytest.mark.parametrize("reason", ["", " \t\n", "x" * 4001])
async def test_internal_service_also_validates_reason_without_database_use(reason):
    with pytest.raises(HTTPException) as invalid:
        await SuspensionService(None).submit_appeal(uuid4(), reason, submitted_by=uuid4())
    assert invalid.value.status_code == 422
