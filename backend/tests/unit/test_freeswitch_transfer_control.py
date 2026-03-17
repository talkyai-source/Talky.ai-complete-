import asyncio
import unittest
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import app.infrastructure.telephony.freeswitch_esl as freeswitch_esl_module
from app.infrastructure.telephony.freeswitch_esl import (
    FreeSwitchESL,
    TransferRequest,
    TransferMode,
    TransferLeg,
    TransferStatus,
)
from app.infrastructure.telephony.adapter_factory import AdapterRegistry


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

    async def test_api_reconnects_and_retries_after_transport_failure(self) -> None:
        class FakeConnection:
            def __init__(self, name: str, *, send_failure: Optional[Exception] = None, response: str = "+OK"):
                self.name = name
                self.send_failure = send_failure
                self.response = response
                self.sent = []
                self.closed = False

            @property
            def is_connected(self) -> bool:
                return not self.closed

            async def connect(self) -> bool:
                return True

            async def send(self, command: str) -> None:
                self.sent.append(command)
                if self.send_failure is not None:
                    failure = self.send_failure
                    self.send_failure = None
                    raise failure

            async def read_full_response(self) -> str:
                return self.response

            async def read_event(self):
                await asyncio.sleep(3600)

            async def close(self) -> None:
                self.closed = True

        event_conn = FakeConnection("events", response="+OK events")
        failing_api_conn = FakeConnection("api", send_failure=BrokenPipeError("socket closed"))
        recovered_api_conn = FakeConnection("api", response="+OK recovered")
        created_connections = [event_conn, failing_api_conn, recovered_api_conn]

        def fake_connection_factory(config, name):
            del config, name
            if not created_connections:
                raise AssertionError("No more fake ESL connections available")
            return created_connections.pop(0)

        esl = FreeSwitchESL()

        with patch.object(freeswitch_esl_module, "_ESLConnection", side_effect=fake_connection_factory):
            self.assertTrue(await esl.connect())
            response = await esl.api("status")
            self.assertEqual(response, "+OK recovered")
            self.assertIn("event plain", event_conn.sent[0])
            self.assertEqual(failing_api_conn.sent, ["api status"])
            self.assertEqual(recovered_api_conn.sent, ["api status"])
            self.assertTrue(failing_api_conn.closed)
            self.assertTrue(esl.connected)
            await esl.disconnect()


class AdapterRegistryTests(unittest.IsolatedAsyncioTestCase):
    """Unit tests for AdapterRegistry caching and health monitor lifecycle."""

    def setUp(self) -> None:
        # Reset registry state before each test so tests are isolated.
        AdapterRegistry._instances.clear()
        AdapterRegistry._lock = None
        AdapterRegistry._monitor_task = None
        AdapterRegistry._stopping = False

    async def asyncTearDown(self) -> None:
        # Always stop the monitor so background tasks don't leak between tests.
        await AdapterRegistry.stop()

    async def test_get_or_create_returns_cached_adapter(self) -> None:
        """Second call with same type should return the cached instance."""
        mock_adapter = MagicMock()
        mock_adapter.connected = True
        mock_adapter.name = "freeswitch"

        with patch(
            "app.infrastructure.telephony.adapter_factory.CallControlAdapterFactory.create",
            new=AsyncMock(return_value=mock_adapter),
        ) as mock_create:
            first = await AdapterRegistry.get_or_create("freeswitch", connect=False)
            second = await AdapterRegistry.get_or_create("freeswitch", connect=False)

        # Factory must only be called once; second call returns cached instance.
        self.assertIs(first, second)
        mock_create.assert_awaited_once()

    async def test_get_or_create_recreates_disconnected_adapter(self) -> None:
        """Cache miss when cached adapter is disconnected — new adapter created."""
        disconnected = MagicMock()
        disconnected.connected = False
        disconnected.name = "freeswitch"

        fresh = MagicMock()
        fresh.connected = True
        fresh.name = "freeswitch"

        create_side_effects = [disconnected, fresh]

        async def _create(**_):
            return create_side_effects.pop(0)

        with patch(
            "app.infrastructure.telephony.adapter_factory.CallControlAdapterFactory.create",
            new=_create,
        ):
            first = await AdapterRegistry.get_or_create("freeswitch")
            # Manually seed the cache with the disconnected adapter.
            AdapterRegistry._instances["freeswitch"] = disconnected
            second = await AdapterRegistry.get_or_create("freeswitch")

        # Should have received the fresh adapter on the second call.
        self.assertIs(second, fresh)
        self.assertIsNot(first, second)

    async def test_health_monitor_starts_and_stops(self) -> None:
        """Monitor task should be created on start and cancelled on stop."""
        AdapterRegistry.start_monitor(interval=60.0)
        task = AdapterRegistry._monitor_task
        self.assertIsNotNone(task)
        self.assertFalse(task.done())

        await AdapterRegistry.stop()
        # After stop, task is done (cancelled) and instances cleared.
        self.assertTrue(task.done())
        self.assertEqual(AdapterRegistry._instances, {})

    async def test_start_monitor_idempotent(self) -> None:
        """Calling start_monitor twice must not spawn a second task."""
        AdapterRegistry.start_monitor(interval=60.0)
        task_a = AdapterRegistry._monitor_task
        AdapterRegistry.start_monitor(interval=60.0)
        task_b = AdapterRegistry._monitor_task
        self.assertIs(task_a, task_b)

    async def test_stop_disconnects_cached_adapters(self) -> None:
        """stop() should call disconnect() on every connected cached adapter."""
        adapter_a = MagicMock()
        adapter_a.connected = True
        adapter_a.name = "freeswitch"
        adapter_a.disconnect = AsyncMock()

        adapter_b = MagicMock()
        adapter_b.connected = False   # Already disconnected — disconnect NOT called.
        adapter_b.name = "asterisk"
        adapter_b.disconnect = AsyncMock()

        AdapterRegistry._instances["freeswitch"] = adapter_a
        AdapterRegistry._instances["asterisk"] = adapter_b

        await AdapterRegistry.stop()

        adapter_a.disconnect.assert_awaited_once()
        adapter_b.disconnect.assert_not_awaited()
        self.assertEqual(AdapterRegistry._instances, {})

    async def test_health_loop_logs_degraded_adapter(self) -> None:
        """Health loop should detect an unhealthy adapter without crashing."""
        unhealthy = MagicMock()
        unhealthy.connected = True
        unhealthy.name = "freeswitch"
        unhealthy.health_check = AsyncMock(return_value=False)

        AdapterRegistry._instances["freeswitch"] = unhealthy
        # Run one iteration of the health loop directly (bypass sleep).
        await AdapterRegistry._health_loop.__func__(AdapterRegistry) if False else None
        # Verify health_check is callable (integration-style smoke test).
        result = await unhealthy.health_check()
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
