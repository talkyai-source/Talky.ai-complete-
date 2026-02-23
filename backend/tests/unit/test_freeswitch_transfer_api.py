import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from app.api.v1.endpoints import freeswitch_bridge
from app.infrastructure.telephony.freeswitch_esl import (
    TransferResult,
    TransferMode,
    TransferLeg,
    TransferStatus,
)


class FreeSwitchTransferApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_blind_transfer_endpoint(self) -> None:
        mock_esl = MagicMock()
        mock_esl.connected = True
        mock_esl.request_transfer = AsyncMock(
            return_value=TransferResult(
                attempt_id="attempt-1",
                uuid="call-1",
                mode=TransferMode.BLIND,
                destination="1002",
                leg=TransferLeg.ALEG,
                status=TransferStatus.SUCCESS,
                started_at=datetime.now(timezone.utc),
                finished_at=datetime.now(timezone.utc),
                reason="bridge_established",
                command="uuid_transfer call-1 1002 XML default",
                response="+OK accepted",
            )
        )

        with patch.object(freeswitch_bridge, "_esl_client", mock_esl):
            payload = freeswitch_bridge.TransferPayload(
                call_uuid="call-1",
                destination="1002",
            )
            response = await freeswitch_bridge.transfer_blind(payload)

        self.assertEqual(response.mode, "blind")
        self.assertEqual(response.status, "success")
        self.assertEqual(response.call_uuid, "call-1")
        mock_esl.request_transfer.assert_awaited_once()

    async def test_transfer_endpoint_requires_connection(self) -> None:
        with patch.object(freeswitch_bridge, "_esl_client", None):
            payload = freeswitch_bridge.TransferPayload(call_uuid="call-1", destination="1002")
            with self.assertRaises(HTTPException) as ctx:
                await freeswitch_bridge.transfer_attended(payload)
            self.assertEqual(ctx.exception.status_code, 400)

    async def test_get_transfer_attempt_not_found(self) -> None:
        mock_esl = MagicMock()
        mock_esl.connected = True
        mock_esl.get_transfer_result.return_value = None

        with patch.object(freeswitch_bridge, "_esl_client", mock_esl):
            with self.assertRaises(HTTPException) as ctx:
                await freeswitch_bridge.get_transfer_attempt("missing")
            self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
