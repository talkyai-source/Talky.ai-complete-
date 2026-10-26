"""Real FastAPI contract and failure receipts, with synthetic service ports."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.endpoints import suspensions as api
from app.domain.services.suspension_service import SuspensionResult,TargetType


def make_case(target):
    target_id,hold_id,audit_id=uuid4(),uuid4(),uuid4()
    receipt=SuspensionResult(suspension_id=hold_id,target_type=TargetType(target),target_id=target_id,
        status="restored",audit_event_id=audit_id,effective_status="suspended",
        remaining_hold_ids=[uuid4()],restoration_warnings=["another_hold_remains"],
        propagated_to=[],propagation_failed=["suspension_events"],propagation_pending=["telephony_policy_check"])
    service=SimpleNamespace(**{f"restore_{target}":AsyncMock(return_value=receipt),
                               f"suspend_{target}":AsyncMock(return_value=receipt)})
    audit=SimpleNamespace(log=AsyncMock(side_effect=AssertionError("No separate postcommit audit allowed")))
    app=FastAPI()
    app.dependency_overrides[api.get_suspension_service]=lambda:service
    app.dependency_overrides[api.get_audit_logger]=lambda:audit
    for route in api.router.routes:
        for dep in route.dependant.dependencies:
            if dep.name=="current_user":
                app.dependency_overrides[dep.call]=lambda:{"id":str(uuid4())}
    app.include_router(api.router)
    return app,service,audit,target_id,receipt


@pytest.mark.parametrize("target",["tenant","partner"])
async def test_account_restore_requires_reviewed_hold_id_before_service(target):
    app,service,audit,identity,receipt=make_case(target)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="https://synthetic.invalid") as client:
        response=await client.post(f"/admin/suspensions/{target}s/{identity}/restore",json={"reason":"Reviewed"})
    assert response.status_code==422
    getattr(service,f"restore_{target}").assert_not_awaited()
    schema=app.openapi()
    contract=schema["components"]["schemas"]["AccountRestoreRequest"]
    assert "expected_suspension_id" in contract["required"]
    assert contract["properties"]["expected_suspension_id"]["format"]=="uuid"


@pytest.mark.parametrize("target",["tenant","partner"])
async def test_committed_restore_receipt_does_not_claim_effective_access_or_repeat_audit(target):
    app,service,audit,identity,receipt=make_case(target)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="https://synthetic.invalid") as client:
        response=await client.post(f"/admin/suspensions/{target}s/{identity}/restore",
            json={"reason":"Reviewed","expected_suspension_id":str(receipt.suspension_id)})
    assert response.status_code==200
    payload=response.json()
    assert payload["restored"] is True and payload["status"]=="restored"
    assert payload["effective_status"]=="suspended" and payload["remaining_hold_ids"]
    assert payload["audit_event_id"]==str(receipt.audit_event_id)
    assert payload["propagated_to"]==[] and payload["propagation_failed"]==["suspension_events"]
    assert getattr(service,f"restore_{target}").await_args.kwargs["expected_suspension_id"]==receipt.suspension_id
    audit.log.assert_not_awaited()


async def test_dormant_bulk_endpoint_is_unavailable_without_success_audit():
    app,service,audit,identity,receipt=make_case("tenant")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="https://synthetic.invalid") as client:
        response=await client.post("/admin/suspensions/bulk-suspend",
            params={"suspension_type":"ADMIN","reason_category":"review","reason_description":"Reviewed"},
            json={"tenant_id":str(identity)})
    assert response.status_code==501 and "bulk_suspend_initiated" not in response.json()
    audit.log.assert_not_awaited()


@pytest.mark.parametrize("target",["tenant","partner"])
async def test_account_suspend_preserves_required_audit_and_pending_receipt(target):
    app,service,audit,identity,receipt=make_case(target)
    receipt.status="suspended"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="https://synthetic.invalid") as client:
        response=await client.post(f"/admin/suspensions/{target}s/{identity}/suspend",
            json={"suspension_type":"ADMIN","reason_category":"review","reason_description":"Reviewed"})
    assert response.status_code==200
    assert response.json()["audit_event_id"]==str(receipt.audit_event_id)
    assert response.json()["propagation_pending"]==["telephony_policy_check"]
    audit.log.assert_not_awaited()

