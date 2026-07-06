"""
Tests for the generic telephony_bridge transfer endpoints.

Previously tested freeswitch_bridge directly; now tests the PBX-agnostic
telephony_bridge which delegates to whichever CallControlAdapter is active.

The transfer endpoints are auth-gated (require_internal_or_tenant): a caller
must present a valid internal service token OR an authenticated tenant (JWT).
These tests exercise the endpoints on the authenticated (tenant) path and
also assert the unauthenticated path is rejected with 401.
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


def _authed_request():
    """A request that passes require_internal_or_tenant via the JWT (tenant)
    path — no internal token, but request.state.tenant_id is set (as
    TenantMiddleware would after validating a session)."""
    req = MagicMock()
    req.headers = {}  # no X-Internal-Service-Token → forces the tenant path
    req.state.tenant_id = "tenant-test"
    return req


def _unauthed_request():
    """A request with neither an internal token nor an authenticated tenant."""
    req = MagicMock()
    req.headers = {}
    req.state.tenant_id = None
    return req


class TelephonyBridgeTransferApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        # P0-6 added a call-ownership check (calls.external_call_uuid -> tenant)
        # to the transfer/hangup routes. These tests exercise the transfer
        # MECHANICS (adapter wiring), not ownership — which has its own tests in
        # test_telephony_bridge_auth.py — and there is no live container here.
        # No-op the ownership check so these keep testing what they test.
        self._own_patch = patch.object(
            telephony_bridge, "_verify_call_ownership", new=AsyncMock()
        )
        self._own_patch.start()

    async def asyncTearDown(self) -> None:
        self._own_patch.stop()

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
            response = await telephony_bridge.transfer_blind(payload, _authed_request())

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
            await telephony_bridge.transfer_attended(payload, _authed_request())

        mock_adapter.transfer.assert_awaited_once_with("call-2", "1003", "attended")

    async def test_transfer_endpoint_requires_connection(self) -> None:
        with patch.object(telephony_bridge, "_adapter", None):
            payload = telephony_bridge.TransferPayload(call_id="call-1", destination="1002")
            with self.assertRaises(HTTPException) as ctx:
                await telephony_bridge.transfer_blind(payload, _authed_request())
            self.assertEqual(ctx.exception.status_code, 400)

    async def test_transfer_endpoint_requires_connected_adapter(self) -> None:
        mock_adapter = MagicMock()
        mock_adapter.connected = False
        with patch.object(telephony_bridge, "_adapter", mock_adapter):
            payload = telephony_bridge.TransferPayload(call_id="call-1", destination="1002")
            with self.assertRaises(HTTPException) as ctx:
                await telephony_bridge.transfer_blind(payload, _authed_request())
            self.assertEqual(ctx.exception.status_code, 400)

    async def test_transfer_endpoint_requires_auth(self) -> None:
        """No internal token and no authenticated tenant → 401, before any
        adapter work."""
        mock_adapter = MagicMock()
        mock_adapter.connected = True
        with patch.object(telephony_bridge, "_adapter", mock_adapter):
            payload = telephony_bridge.TransferPayload(call_id="call-1", destination="1002")
            with self.assertRaises(HTTPException) as ctx:
                await telephony_bridge.transfer_blind(payload, _unauthed_request())
            self.assertEqual(ctx.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
