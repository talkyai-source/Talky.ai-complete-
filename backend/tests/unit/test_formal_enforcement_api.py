"""Actual ASGI contracts with synthetic service ports; no network or database."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.domain.services.suspension_service import (
    SuspensionService,
    SuspensionStatusResponse,
    SuspensionType,
    validate_duration_hours,
)
from tests.unit.test_formal_account_hold_api import make_case
from tests.unit.test_bh_tenancy_admin_suspensions import _Pool, _event_row


@pytest.mark.parametrize("supplied", [False, True])
async def test_user_restore_preserves_optional_exact_event_boundary(supplied):
    app, service, _, identity, receipt = make_case("user")
    body = {"reason": "Reviewed"}
    if supplied:
        body["expected_suspension_id"] = str(receipt.suspension_id)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://synthetic.invalid"
    ) as client:
        response = await client.post(f"/admin/suspensions/users/{identity}/restore", json=body)
    assert response.status_code == 200
    assert service.restore_user.await_args.kwargs["expected_suspension_id"] == (
        receipt.suspension_id if supplied else None
    )
    assert response.json()["audit_event_id"] == str(receipt.audit_event_id)


@pytest.mark.parametrize("target", ["user", "tenant", "partner"])
async def test_status_forwards_history_completeness_and_exact_identity(target):
    app, service, _, identity, _ = make_case(target)
    remaining = uuid4()
    service.get_status = AsyncMock(
        return_value=SuspensionStatusResponse(
            target_type=target,
            target_id=identity,
            status="restored",
            is_suspended=True,
            active_suspension=None,
            suspension_history=[],
            effective_status="suspended",
            remaining_hold_ids=[remaining],
            history_complete=False,
            history_limit=200,
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://synthetic.invalid"
    ) as client:
        response = await client.get(f"/admin/suspensions/{target}s/{identity}/status")
    assert response.status_code == 200
    body = response.json()
    assert body["target_type"] == target and body["target_id"] == str(identity)
    assert body["history_complete"] is False and body["history_limit"] == 200
    assert (
        body["remaining_hold_ids"] == [str(remaining)] and body["effective_status"] == "suspended"
    )


@pytest.mark.parametrize("target", ["user", "tenant", "partner"])
@pytest.mark.parametrize("duration", [0, -1, True, "1", 10**30])
async def test_invalid_duration_is_422_without_service_mutation(target, duration):
    app, service, _, identity, _ = make_case(target)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://synthetic.invalid"
    ) as client:
        response = await client.post(
            f"/admin/suspensions/{target}s/{identity}/suspend",
            json={
                "suspension_type": "TEMPORARY",
                "reason_category": "review",
                "reason_description": "Reviewed",
                "duration_hours": duration,
            },
        )
    assert response.status_code == 422
    getattr(service, f"suspend_{target}").assert_not_awaited()


@pytest.mark.parametrize("target", ["user", "tenant", "partner"])
async def test_direct_service_duration_overflow_is_rejected_before_database(target):
    service = SuspensionService(None)
    with pytest.raises(HTTPException) as error:
        await getattr(service, f"suspend_{target}")(
            uuid4(),
            SuspensionType.TEMPORARY,
            "review",
            "Reviewed",
            suspended_by=uuid4(),
            duration_hours=10**30,
        )
    assert error.value.status_code == 422


def test_duration_boundary_depends_on_datetime_representability_not_business_limit():
    now = datetime(9999, 12, 31, 22, tzinfo=timezone.utc)
    assert validate_duration_hours(1, now=now) == 1
    with pytest.raises(ValueError, match="date range"):
        validate_duration_hours(2, now=now)


async def test_history_limit_signals_truncation_instead_of_false_reconciliation():
    row = _event_row(datetime.now(timezone.utc) + timedelta(hours=1))
    rows = [dict(row, suspension_id=uuid4()) for _ in range(201)]
    result = await SuspensionService(_Pool(row, rows)).get_status(user_id=row["target_id"])
    assert len(result.suspension_history) == 200
    assert result.history_complete is False and result.history_limit == 200
