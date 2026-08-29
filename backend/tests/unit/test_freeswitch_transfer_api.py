"""
Tests for the generic telephony_bridge transfer endpoints.

Previously tested freeswitch_bridge directly; now tests the PBX-agnostic
telephony_bridge which delegates to whichever CallControlAdapter is active.

The transfer endpoints accept a valid internal service token OR an
authenticated tenant administrator with the call-control permission. These
tests cover the permission boundary as well as adapter wiring.
"""

import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from app.api.v1.endpoints import telephony_bridge
from app.infrastructure.telephony.freeswitch_esl import (
    TransferResult,
    TransferMode,
    TransferLeg,
    TransferStatus,
)

TEST_TENANT = "11111111-1111-1111-1111-111111111111"
TEST_USER = "22222222-2222-2222-2222-222222222222"


class _PermissionConnection:
    def __init__(self):
        self.role_permissions = ["calls:delete"]
        self.direct_permissions = []
        # The deployment-seeding probe (rbac_data_is_seeded). These tests model
        # a SEEDED deployment whose grant was revoked, so both legs answer True
        # and an empty grant set stays a denial rather than an unseeded
        # fallback to role defaults.
        self.seeded = True

    async def fetch(self, query, *_args):
        if "FROM tenant_users" in query:
            return [{"name": name} for name in self.role_permissions]
        if "FROM user_permissions" in query:
            return [{"name": name} for name in self.direct_permissions]
        raise AssertionError(query)

    async def fetchrow(self, query, *_args):
        if "role_permissions" in query and "tenant_users" in query:
            return {
                "has_role_grants": self.seeded,
                "has_memberships": self.seeded,
            }
        raise AssertionError(query)


class _AcquirePermissionConnection:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_args):
        return False


class _PermissionPool:
    def __init__(self, conn):
        self.conn = conn
        self.acquires = 0

    def acquire(self):
        self.acquires += 1
        return _AcquirePermissionConnection(self.conn)


def _authed_request(role: str = "tenant_admin"):
    """A request that passes require_internal_or_tenant via the JWT (tenant)
    path — no internal token, but request.state.tenant_id is set (as
    TenantMiddleware would after validating a session)."""
    req = MagicMock()
    req.headers = {
        "idempotency-key": "transfer-test-key-001",
    }  # no X-Internal-Service-Token → forces the tenant path
    req.state.tenant_id = TEST_TENANT
    req.state.user_id = TEST_USER
    req.state.user_role = role
    return req


async def _reset_probe_cache():
    from app.core.security.rbac import reset_rbac_seeding_probe_cache

    reset_rbac_seeding_probe_cache()


def _unauthed_request():
    """A request with neither an internal token nor an authenticated tenant."""
    req = MagicMock()
    req.headers = {}
    req.state.tenant_id = None
    req.state.user_id = None
    req.state.user_role = None
    return req


class TelephonyBridgeTransferApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        # The seeding probe answer is cached per process for 30s; drop it so a
        # neighbouring test cannot decide this one's authorization outcome.
        from app.core.security.rbac import reset_rbac_seeding_probe_cache

        reset_rbac_seeding_probe_cache()
        self.addAsyncCleanup(_reset_probe_cache)
        self.permission_conn = _PermissionConnection()
        self.permission_pool = _PermissionPool(self.permission_conn)
        self._permission_pool_patch = patch(
            "app.core.container.get_db_pool_from_container",
            return_value=self.permission_pool,
        )
        self._permission_pool_patch.start()
        # P0-6 added a call-ownership check (calls.external_call_uuid -> tenant)
        # to the transfer/hangup routes. These tests exercise the transfer
        # MECHANICS (adapter wiring), not ownership — which has its own tests in
        # test_telephony_bridge_auth.py — and there is no live container here.
        # No-op the ownership check so these keep testing what they test.
        self._own_patch = patch.object(telephony_bridge, "_verify_call_ownership", new=AsyncMock())
        self._own_patch.start()
        # Transfer-policy persistence has its own focused service tests. This
        # file deliberately isolates endpoint authorization and adapter
        # delegation from the application container/database.
        self._policy_patch = patch.object(
            telephony_bridge,
            "_enforce_inbound_transfer_policy",
            new=AsyncMock(return_value=None),
        )
        self._complete_patch = patch.object(
            telephony_bridge, "_complete_transfer_attempt", new=AsyncMock()
        )
        self.policy_mock = self._policy_patch.start()
        self._complete_patch.start()

    async def asyncTearDown(self) -> None:
        self._complete_patch.stop()
        self._policy_patch.stop()
        self._own_patch.stop()
        self._permission_pool_patch.stop()

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
        policy_kwargs = self.policy_mock.await_args.kwargs
        self.assertEqual(policy_kwargs["idempotency_key"], "transfer-test-key-001")
        self.assertEqual(policy_kwargs["actor_id"], TEST_USER)
        self.assertEqual(policy_kwargs["actor_role"], "tenant_admin")
        self.assertEqual(policy_kwargs["actor_type"], "user")

    async def test_idempotent_in_progress_replay_never_reinvokes_adapter(self) -> None:
        attempt_id = "33333333-3333-3333-3333-333333333333"
        self.policy_mock.return_value = SimpleNamespace(
            inbound=True,
            is_replay=True,
            replay_result=None,
            leg_id=attempt_id,
        )
        mock_adapter = MagicMock()
        mock_adapter.connected = True
        mock_adapter.transfer = AsyncMock()

        with patch.object(telephony_bridge, "_adapter", mock_adapter):
            response = await telephony_bridge.transfer_blind(
                telephony_bridge.TransferPayload(
                    call_id="call-1",
                    destination="1002",
                ),
                _authed_request(),
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            response.headers["location"],
            f"/api/v1/sip/telephony/transfer/attempts/{attempt_id}",
        )
        mock_adapter.transfer.assert_not_awaited()

    async def test_completed_idempotent_replay_returns_stored_response(self) -> None:
        attempt_id = "33333333-3333-3333-3333-333333333333"
        stored = {
            "status": "completed",
            "attempt_id": attempt_id,
            "handoff_confirmed": True,
        }
        self.policy_mock.return_value = SimpleNamespace(
            inbound=True,
            is_replay=True,
            replay_result=stored,
            leg_id=attempt_id,
        )
        mock_adapter = MagicMock()
        mock_adapter.connected = True
        mock_adapter.transfer = AsyncMock()

        with patch.object(telephony_bridge, "_adapter", mock_adapter):
            response = await telephony_bridge.transfer_blind(
                telephony_bridge.TransferPayload(
                    call_id="call-1",
                    destination="1002",
                ),
                _authed_request(),
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["location"].split("/")[-1], attempt_id)
        mock_adapter.transfer.assert_not_awaited()

    def test_transfer_payload_rejects_protocol_and_token_injection(self) -> None:
        for values in (
            {"call_id": "call-1\napi status", "destination": "1002"},
            {"call_id": "call-1", "destination": "1002\r\napi status"},
            {"call_id": "call-1", "destination": "1002 XML public"},
            {
                "call_id": "call-1",
                "destination": "sip:1002@example.com\napi status",
                "mode": "deflect",
            },
        ):
            with self.subTest(values=values):
                with self.assertRaises(ValidationError):
                    telephony_bridge.TransferPayload(**values)

    async def test_transfer_endpoint_requires_idempotency_key(self) -> None:
        mock_adapter = MagicMock()
        mock_adapter.connected = True
        mock_adapter.transfer = AsyncMock()
        request = _authed_request()
        request.headers = {}
        payload = telephony_bridge.TransferPayload(
            call_id="call-1",
            destination="1002",
        )

        with patch.object(telephony_bridge, "_adapter", mock_adapter):
            with self.assertRaises(HTTPException) as ctx:
                await telephony_bridge.transfer_blind(payload, request)
        self.assertEqual(ctx.exception.status_code, 400)
        mock_adapter.transfer.assert_not_awaited()

    async def test_transfer_route_rejects_payload_mode_mismatch(self) -> None:
        mock_adapter = MagicMock()
        mock_adapter.connected = True
        mock_adapter.transfer = AsyncMock()
        payload = telephony_bridge.TransferPayload(
            call_id="call-1",
            destination="1002",
            mode="blind",
        )

        with patch.object(telephony_bridge, "_adapter", mock_adapter):
            with self.assertRaises(HTTPException) as ctx:
                await telephony_bridge.transfer_attended(
                    payload,
                    _authed_request(),
                )
        self.assertEqual(ctx.exception.status_code, 422)
        mock_adapter.transfer.assert_not_awaited()

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

    async def test_transfer_endpoint_rejects_tenant_without_call_control_permission(self) -> None:
        # The JWT still claims tenant_admin; the database role grant has been
        # revoked, so static role defaults must not authorize the request.
        self.permission_conn.role_permissions = []
        mock_adapter = MagicMock()
        mock_adapter.connected = True
        mock_adapter.transfer = AsyncMock()
        with patch.object(telephony_bridge, "_adapter", mock_adapter):
            payload = telephony_bridge.TransferPayload(call_id="call-1", destination="1002")
            with self.assertRaises(HTTPException) as ctx:
                await telephony_bridge.transfer_blind(payload, _authed_request(role="tenant_admin"))
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(
            ctx.exception.detail,
            {"error": "permission_denied", "required": "calls:delete"},
        )
        mock_adapter.transfer.assert_not_awaited()

    async def test_internal_service_token_bypasses_tenant_call_permission(self) -> None:
        req = MagicMock()
        req.headers = {"x-internal-service-token": "service-secret-123"}
        req.state.tenant_id = None
        req.state.user_role = None
        with patch.dict("os.environ", {"INTERNAL_SERVICE_TOKEN": "service-secret-123"}):
            ctx = await telephony_bridge._require_call_control(req)
        self.assertTrue(ctx.is_internal)
        self.assertEqual(self.permission_pool.acquires, 0)

    async def test_direct_user_grant_allows_call_control(self) -> None:
        self.permission_conn.role_permissions = []
        self.permission_conn.direct_permissions = ["calls:delete"]
        ctx = await telephony_bridge._require_call_control(
            _authed_request(role="readonly"), db_pool=self.permission_pool
        )
        self.assertFalse(ctx.is_internal)
        self.assertEqual(ctx.tenant_id, TEST_TENANT)

    async def test_hangup_requires_same_call_control_permission(self) -> None:
        self.permission_conn.role_permissions = []
        mock_adapter = MagicMock()
        mock_adapter.hangup = AsyncMock()
        with patch.object(telephony_bridge, "_adapter", mock_adapter):
            with self.assertRaises(HTTPException) as ctx:
                await telephony_bridge.hangup_call("call-1", _authed_request(role="readonly"))
        self.assertEqual(ctx.exception.status_code, 403)
        mock_adapter.hangup.assert_not_awaited()

    async def test_inbound_transfer_cancellation_stays_cleanup_pending(self) -> None:
        """Cancellation is uncertainty, never proof that the PSTN leg is gone."""

        provider_leg_id = "talky-xfer-0000000000000000000a"
        attempt = SimpleNamespace(
            inbound=True,
            provider_leg_id=provider_leg_id,
            destination="+14155550123",
        )
        mock_adapter = MagicMock()
        mock_adapter.connected = True
        mock_adapter.transfer = AsyncMock(side_effect=asyncio.CancelledError())
        complete = AsyncMock()

        with (
            patch.object(telephony_bridge, "_adapter", mock_adapter),
            patch.object(
                telephony_bridge,
                "_enforce_inbound_transfer_policy",
                new=AsyncMock(return_value=attempt),
            ),
            patch.object(
                telephony_bridge,
                "_complete_transfer_attempt",
                new=complete,
            ),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await telephony_bridge._execute_transfer("inbound-parent", "+14155550123", "blind")

        mock_adapter.transfer.assert_awaited_once_with(
            "inbound-parent",
            "+14155550123",
            "blind",
            provider_leg_id=provider_leg_id,
        )
        complete.assert_awaited_once()
        completed_attempt, result = complete.await_args.args
        self.assertIs(completed_attempt, attempt)
        self.assertEqual(result["status"], "cleanup_pending")
        self.assertEqual(result["provider_leg_id"], provider_leg_id)
        self.assertEqual(result["error_type"], "CancelledError")

    async def test_inbound_transfer_adapter_exception_stays_cleanup_pending(self) -> None:
        provider_leg_id = "talky-xfer-0000000000000000000b"
        attempt = SimpleNamespace(
            inbound=True,
            provider_leg_id=provider_leg_id,
            destination="+14155550123",
        )
        mock_adapter = MagicMock()
        mock_adapter.connected = True
        mock_adapter.transfer = AsyncMock(side_effect=RuntimeError("ARI down"))
        complete = AsyncMock()

        with (
            patch.object(telephony_bridge, "_adapter", mock_adapter),
            patch.object(
                telephony_bridge,
                "_enforce_inbound_transfer_policy",
                new=AsyncMock(return_value=attempt),
            ),
            patch.object(
                telephony_bridge,
                "_complete_transfer_attempt",
                new=complete,
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "ARI down"):
                await telephony_bridge._execute_transfer("inbound-parent", "+14155550123", "blind")

        result = complete.await_args.args[1]
        self.assertEqual(result["status"], "cleanup_pending")
        self.assertEqual(result["provider_leg_id"], provider_leg_id)
        self.assertEqual(result["error_type"], "RuntimeError")

    async def test_return_to_agent_fallback_requires_both_provider_proofs(self) -> None:
        attempt = SimpleNamespace(
            inbound=True,
            failure_action="return_to_agent",
            call_id="11111111-1111-1111-1111-111111111111",
        )
        mark_pending = AsyncMock()

        with patch.object(
            telephony_bridge,
            "mark_termination_pending_and_load_context",
            new=mark_pending,
        ):
            unproved = await telephony_bridge._apply_inbound_transfer_failure_action(
                attempt,
                {
                    "status": "failed",
                    "target_termination_confirmed": True,
                    "caller_media_retained": False,
                },
            )
            proved = await telephony_bridge._apply_inbound_transfer_failure_action(
                attempt,
                {
                    "status": "failed",
                    "target_termination_confirmed": True,
                    "caller_media_retained": True,
                },
            )

        self.assertEqual(unproved["fallback_status"], "unconfirmed")
        self.assertEqual(proved["fallback_status"], "applied")
        mark_pending.assert_not_awaited()

    async def test_hangup_fallback_proves_every_durable_provider_leg(self) -> None:
        attempt = SimpleNamespace(
            inbound=True,
            failure_action="hangup",
            call_id="11111111-1111-1111-1111-111111111111",
            tenant_id=TEST_TENANT,
            provider_leg_id="talky-xfer-current",
        )
        context = SimpleNamespace(
            call_id=attempt.call_id,
            tenant_id=TEST_TENANT,
            provider_call_id="asterisk-parent",
            provider_leg_ids=("talky-xfer-current", "talky-xfer-stale"),
            provider="asterisk",
            campaign_id=None,
            answered_at=datetime.now(timezone.utc),
        )
        container = SimpleNamespace(db_pool=object(), redis=object())
        request_hangup = AsyncMock(
            return_value=SimpleNamespace(confirmed=True, error=None, code="confirmed")
        )
        finalize = AsyncMock()

        with (
            patch("app.core.container.get_container", return_value=container),
            patch.object(
                telephony_bridge,
                "mark_termination_pending_and_load_context",
                new=AsyncMock(return_value=context),
            ),
            patch.object(
                telephony_bridge,
                "request_confirmed_hangup",
                new=request_hangup,
            ),
            patch.object(
                telephony_bridge,
                "finalize_proven_inbound_termination",
                new=finalize,
            ),
            patch.object(telephony_bridge, "_adapter", object()),
        ):
            result = await telephony_bridge._apply_inbound_transfer_failure_action(
                attempt,
                {"status": "failed"},
            )

        self.assertEqual(result["fallback_status"], "confirmed")
        self.assertEqual(result["fallback_effective_action"], "hangup")
        self.assertEqual(
            request_hangup.await_args.kwargs["provider_leg_ids"],
            context.provider_leg_ids,
        )
        finalize.assert_awaited_once()

    async def test_unconfirmed_voicemail_fallback_is_truthful_and_retryable(self) -> None:
        attempt = SimpleNamespace(
            inbound=True,
            failure_action="voicemail",
            call_id="11111111-1111-1111-1111-111111111111",
            tenant_id=TEST_TENANT,
        )
        context = SimpleNamespace(
            call_id=attempt.call_id,
            tenant_id=TEST_TENANT,
            provider_call_id="asterisk-parent",
            provider_leg_ids=(),
            provider="asterisk",
            campaign_id=None,
            answered_at=None,
        )
        container = SimpleNamespace(db_pool=object(), redis=None)
        finalize = AsyncMock()

        with (
            patch("app.core.container.get_container", return_value=container),
            patch.object(
                telephony_bridge,
                "mark_termination_pending_and_load_context",
                new=AsyncMock(return_value=context),
            ),
            patch.object(
                telephony_bridge,
                "request_confirmed_hangup",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        confirmed=False,
                        error=None,
                        code="hangup_unconfirmed",
                    )
                ),
            ),
            patch.object(
                telephony_bridge,
                "finalize_proven_inbound_termination",
                new=finalize,
            ),
            patch.object(telephony_bridge, "_adapter", object()),
        ):
            result = await telephony_bridge._apply_inbound_transfer_failure_action(
                attempt,
                {"status": "failed", "attempt_id": "attempt-1"},
            )

        self.assertEqual(result["fallback_action"], "voicemail")
        self.assertEqual(result["fallback_effective_action"], "hangup")
        self.assertEqual(result["fallback_status"], "termination_pending")
        self.assertEqual(result["fallback_reason"], "voicemail_runtime_unavailable")
        self.assertEqual(telephony_bridge._transfer_http_response(result).status_code, 202)
        finalize.assert_not_awaited()

    def test_reconciliation_required_transfer_response_remains_pending(self) -> None:
        response = telephony_bridge._transfer_http_response(
            {
                "status": "reconciliation_required",
                "attempt_id": "33333333-3333-3333-3333-333333333333",
                "billing_status": "held",
            }
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            response.headers["location"],
            "/api/v1/sip/telephony/transfer/attempts/" "33333333-3333-3333-3333-333333333333",
        )


if __name__ == "__main__":
    unittest.main()
