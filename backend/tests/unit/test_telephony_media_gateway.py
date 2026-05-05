from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.infrastructure.telephony.telephony_media_gateway import TelephonyMediaGateway


@pytest.mark.asyncio
async def test_hangup_call_uses_adapter_pbx_call_id():
    gateway = TelephonyMediaGateway()
    adapter = AsyncMock()

    await gateway.on_call_started(
        "pipeline-call-id",
        {"adapter": adapter, "pbx_call_id": "pbx-call-id"},
    )

    result = await gateway.hangup_call("pipeline-call-id", "user_goodbye")

    assert result is True
    adapter.hangup.assert_awaited_once_with("pbx-call-id")


@pytest.mark.asyncio
async def test_hangup_call_returns_false_for_missing_session():
    gateway = TelephonyMediaGateway()

    result = await gateway.hangup_call("missing-call-id", "user_goodbye")

    assert result is False
