"""
Tests for the generic telephony_bridge transfer endpoints.

Previously tested freeswitch_bridge directly; now tests the PBX-agnostic
telephony_bridge which delegates to whichever CallControlAdapter is active.
"""
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from app.api.v1.endpoints import telephony_bridge
from app.infrastructure.telephony.freeswitch_esl import (
    TransferResult,
    TransferMode,
    TransferLeg,
    TransferStatus,
)


class TelephonyBridgeTransferApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_blind_transfer_endpoint(self) -> None:
        mock_adapter = MagicMock()
        mock_adapter.connected = True
        mock_adapter.transfer = AsyncMock(
            return_value={
                "attempt_id": "attempt-1",
                "uuid": "call-1",
                "mode": "blind",
                "destination": "1002",
                "leg": "aleg",
                "status": "success",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "reason": "bridge_established",
                "command": "uuid_transfer call-1 1002 XML default",
                "response": "+OK accepted",
                "context": "default",
            }
        )

        with patch.object(telephony_bridge, "_adapter", mock_adapter):
            payload = telephony_bridge.TransferPayload(
                call_id="call-1",
                destination="1002",
                mode="blind",
            )
            response = await telephony_bridge.transfer_blind(payload)

        self.assertEqual(response.status_code, 200)
        import json
        body = json.loads(response.body)
        self.assertEqual(body["mode"], "blind")
        self.assertEqual(body["status"], "success")
        mock_adapter.transfer.assert_awaited_once_with("call-1", "1002", "blind")

    async def test_attended_transfer_endpoint(self) -> None:
        mock_adapter = MagicMock()
        mock_adapter.connected = True
        mock_adapter.transfer = AsyncMock(
            return_value={
                "attempt_id": "attempt-2",
                "uuid": "call-2",
                "mode": "attended",
                "destination": "1003",
                "status": "success",
            }
        )

        with patch.object(telephony_bridge, "_adapter", mock_adapter):
            payload = telephony_bridge.TransferPayload(
                call_id="call-2",
                destination="1003",
                mode="attended",
            )
            await telephony_bridge.transfer_attended(payload)

        mock_adapter.transfer.assert_awaited_once_with("call-2", "1003", "attended")

    async def test_transfer_endpoint_requires_connection(self) -> None:
        with patch.object(telephony_bridge, "_adapter", None):
            payload = telephony_bridge.TransferPayload(call_id="call-1", destination="1002")
            with self.assertRaises(HTTPException) as ctx:
                await telephony_bridge.transfer_blind(payload)
            self.assertEqual(ctx.exception.status_code, 400)

    async def test_transfer_endpoint_requires_connected_adapter(self) -> None:
        mock_adapter = MagicMock()
        mock_adapter.connected = False
        with patch.object(telephony_bridge, "_adapter", mock_adapter):
            payload = telephony_bridge.TransferPayload(call_id="call-1", destination="1002")
            with self.assertRaises(HTTPException) as ctx:
                await telephony_bridge.transfer_blind(payload)
            self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
