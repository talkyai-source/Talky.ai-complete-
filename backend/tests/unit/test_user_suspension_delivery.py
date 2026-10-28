"""Committed user security changes never depend on an absent delivery queue."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from app.domain.services.suspension_service import SuspensionService,TargetType


@pytest.mark.parametrize("redis_available",[False,True])
async def test_current_user_enforcement_is_pending_without_queue_or_service_ack(redis_available):
    pool=SimpleNamespace(acquire=AsyncMock(side_effect=AssertionError("No queue access allowed")))
    redis=SimpleNamespace(publish=AsyncMock(return_value=0)) if redis_available else None
    service=SuspensionService(pool,redis_client=redis)
    result=await service._propagate_to_services(uuid4(),TargetType.USER,uuid4(),"suspend")
    assert result=={"success":[],"failed":[],"pending":["assistant_session_check"]}
    pool.acquire.assert_not_called()
    if redis:
        redis.publish.assert_not_awaited()


async def test_stalled_postcommit_user_hint_is_cancelled_and_reported_separately():
    cancelled=asyncio.Event()
    async def stalled(*args):
        try:
            await asyncio.Future()
        finally:
            cancelled.set()
    service=SuspensionService(None,redis_client=SimpleNamespace(publish=stalled))
    result=await asyncio.wait_for(
        service._propagate_committed_user_change(uuid4(),uuid4(),"suspend",{"type":"synthetic"}),2)
    assert cancelled.is_set()
    assert result=={"success":[],"failed":["suspension_events"],"pending":["assistant_session_check"]}
