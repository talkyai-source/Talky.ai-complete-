import asyncio
import unittest
from unittest.mock import AsyncMock

from app.infrastructure.telephony.freeswitch_esl import (
    FreeSwitchESL,
    TransferRequest,
    TransferMode,
    TransferLeg,
    TransferStatus,
)


class FreeSwitchTransferControlTests(unittest.IsolatedAsyncioTestCase):
    def test_transfer_request_validation(self) -> None:
        with self.assertRaises(ValueError):
            TransferRequest(uuid="", destination="1001").validate()
        with self.assertRaises(ValueError):
            TransferRequest(uuid="abc", destination="").validate()
        with self.assertRaises(ValueError):
            TransferRequest(uuid="abc", destination="1001", timeout_seconds=0).validate()

    async def test_build_blind_transfer_command(self) -> None:
        esl = FreeSwitchESL()
        request = TransferRequest(
            uuid="call-1",
            destination="1002",
            mode=TransferMode.BLIND,
            leg=TransferLeg.BLEG,
            context="public",
        )
        command = esl._build_transfer_command(request)
        self.assertEqual(command, "uuid_transfer call-1 -bleg 1002 XML public")

    async def test_build_attended_transfer_command(self) -> None:
        esl = FreeSwitchESL()
        request = TransferRequest(
            uuid="call-1",
            destination="1003",
            mode=TransferMode.ATTENDED,
            leg=TransferLeg.ALEG,
        )
        command = esl._build_transfer_command(request)
        self.assertEqual(command, "uuid_transfer call-1 att_xfer::1003 inline")

    async def test_deflect_requires_answered_call(self) -> None:
        esl = FreeSwitchESL()
        esl.api = AsyncMock(return_value="+OK")  # uuid_getvar fallback won't contain "answered"

        result = await esl.request_transfer(
            TransferRequest(
                uuid="call-1",
                destination="sip:1002@example.com",
                mode=TransferMode.DEFLECT,
                timeout_seconds=0.2,
            )
        )

        self.assertEqual(result.status, TransferStatus.FAILED)
        self.assertEqual(result.reason, "deflect_requires_answered_call")

    async def test_blind_transfer_success_on_bridge_event(self) -> None:
        esl = FreeSwitchESL()
        commands = []

        async def fake_api(command: str) -> str:
            commands.append(command)
            return "+OK accepted"

        esl.api = fake_api

        request = TransferRequest(
            uuid="call-bridge",
            destination="1004",
            mode=TransferMode.BLIND,
            leg=TransferLeg.ALEG,
            timeout_seconds=1.0,
        )

        task = asyncio.create_task(esl.request_transfer(request))
        await asyncio.sleep(0.05)
        esl._update_transfer_state_from_event("CHANNEL_BRIDGE", {"Unique-ID": "call-bridge"})
        result = await task

        self.assertIn("uuid_setvar call-bridge hangup_after_bridge false", commands)
        self.assertIn("uuid_transfer call-bridge 1004 XML default", commands)
        self.assertEqual(result.status, TransferStatus.SUCCESS)

    async def test_transfer_times_out_without_terminal_event(self) -> None:
        esl = FreeSwitchESL()
        esl.api = AsyncMock(return_value="+OK accepted")

        result = await esl.request_transfer(
            TransferRequest(
                uuid="call-timeout",
                destination="1005",
                mode=TransferMode.BLIND,
                timeout_seconds=0.05,
            )
        )
        self.assertEqual(result.status, TransferStatus.TIMED_OUT)
        self.assertIn("no_terminal_event_within_", result.reason or "")


if __name__ == "__main__":
    unittest.main()
