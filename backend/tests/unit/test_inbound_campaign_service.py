"""Focused policy tests for inbound campaign administration."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

# Load the dependency module first: this repository intentionally re-exports
# RBAC dependencies from its footer, while rbac.py imports CurrentUser.
from app.api.v1 import dependencies as _dependencies  # noqa: F401
import app.api.v1.endpoints.inbound_campaigns as inbound_endpoints
import app.domain.services.inbound_campaign_service as campaign_module
from app.api.v1.dependencies import CurrentUser
from app.api.v1.schemas.inbound_campaigns import (
    InboundCampaignCreateRequest,
    InboundCampaignUpdateRequest,
    InboundDidAssignmentRequest,
    InboundVersionRequest,
)
from app.core.security.rbac import Permission, ROLE_DEFAULT_PERMISSIONS, UserRole
from app.domain.services.inbound_campaign_service import (
    InboundCampaignError,
    InboundCampaignService,
    InboundConflictError,
    InboundNotFoundError,
)
from app.domain.services.telephony.inbound_overrides import apply_qualification_overrides


TENANT = "11111111-1111-1111-1111-111111111111"
ACTOR = "22222222-2222-2222-2222-222222222222"
CONFIG = "33333333-3333-3333-3333-333333333333"


def _ready_bundle(**overrides):
    values = {
        "tenant_id": TENANT,
        "config_id": CONFIG,
        "campaign_direction": "inbound",
        "campaign_status": "active",
        "config_status": "draft",
        "tenant_status": "active",
        "subscription_status": "active",
        "tenant_inbound_enabled": True,
        "platform_inbound_enabled": True,
        "platform_settlement_enabled": True,
        "platform_recording_enabled": True,
        "platform_transfer_enabled": True,
        "system_prompt": "Ask one question at a time.",
        "script_config": {},
        "tenant_ai_config_id": CONFIG,
        "llm_provider": "groq",
        "llm_model": "llama",
        "stt_provider": "deepgram",
        "stt_model": "nova-3",
        "voice_id": "voice-1",
        "tts_provider": "cartesia",
        "tts_model": "sonic-2",
        "tenant_tts_provider": "cartesia",
        "tenant_tts_model": "sonic-3",
        "tenant_pipeline_mode": "cascaded",
        "phone_status": "verified",
        "trunk_active": True,
        "trunk_direction": "inbound",
        "trunk_metadata": {"register": False},
        "trunk_live_registration_status": "loaded",
        "trunk_live_status_detail": None,
        "trunk_live_status_checked_at": datetime.now(timezone.utc),
        "minutes_allocated": 100,
        "concurrency_policy_ready": True,
        "assignment_status": "paused",
        "active_did_conflict": False,
        "timezone": "UTC",
        "business_hours": {
            "weekly_schedule": [],
            "after_hours_message": "Please leave your message.",
        },
        "after_hours_action": "hangup",
        "transfer_number": None,
        "recording_enabled": False,
        "consent_message": None,
        "qualification_config": {},
    }
    values.update(overrides)
    return values


def test_readiness_is_stable_and_actionable():
    ready = InboundCampaignService._readiness(_ready_bundle())
    assert ready["ready"] is True
    assert ready["blockers"] == []
    assert all(check["passed"] for check in ready["checks"])

    draft_base = InboundCampaignService._readiness(
        _ready_bundle(campaign_status="draft", config_status="draft")
    )
    assert draft_base["ready"] is True
    assert (
        next(check for check in draft_base["checks"] if check["key"] == "campaign_active")["passed"]
        is True
    )

    blocked = InboundCampaignService._readiness(
        _ready_bundle(
            phone_status="pending_verification",
            recording_enabled=True,
            consent_message=None,
            after_hours_action="transfer",
            transfer_number=None,
        )
    )
    assert blocked["ready"] is False
    blockers = {item["code"]: item for item in blocked["blockers"]}
    assert {"did_verified", "recording_consent", "after_hours_complete"} <= blockers.keys()
    assert all(item["message"] and item["remediation"] for item in blockers.values())


def test_readiness_requires_fresh_type_appropriate_asterisk_trunk_proof():
    stale = InboundCampaignService._readiness(
        _ready_bundle(
            trunk_live_status_checked_at=datetime.now(timezone.utc) - timedelta(minutes=2)
        )
    )
    assert "trunk_ready" in {item["code"] for item in stale["blockers"]}

    rejected = InboundCampaignService._readiness(
        _ready_bundle(
            trunk_metadata={"register": True},
            trunk_live_registration_status="rejected",
            trunk_live_status_detail="403 Forbidden",
        )
    )
    check = next(item for item in rejected["checks"] if item["key"] == "trunk_ready")
    assert check["passed"] is False
    assert "403 Forbidden" in check["detail"]

    registered = InboundCampaignService._readiness(
        _ready_bundle(
            trunk_metadata={"register": True},
            trunk_live_registration_status="registered",
        )
    )
    assert registered["ready"] is True


def test_request_contract_rejects_unverified_shape_before_service_call():
    base = {
        "name": "Main inbound",
        "did_number": "+15551234567",
        "campaign_id": CONFIG,
        "sip_trunk_id": ACTOR,
    }
    assert InboundCampaignCreateRequest(**base).did_number == "+15551234567"

    with pytest.raises(ValidationError):
        InboundCampaignCreateRequest(**{**base, "did_number": "15551234567"})
    with pytest.raises(ValidationError):
        InboundCampaignCreateRequest(**{**base, "recording_enabled": True, "consent_message": None})
    with pytest.raises(ValidationError):
        InboundCampaignCreateRequest(
            **{**base, "after_hours_action": "transfer", "transfer_number": None}
        )
    with pytest.raises(ValidationError):
        InboundCampaignUpdateRequest(name="No expected version")
    with pytest.raises(ValidationError):
        InboundVersionRequest()


def test_only_tenant_admin_and_higher_receive_inbound_mutation_permissions():
    for role in (UserRole.READONLY, UserRole.USER):
        permissions = ROLE_DEFAULT_PERMISSIONS[role]
        assert Permission.INBOUND_READ in permissions
        assert Permission.INBOUND_MANAGE not in permissions
        assert Permission.INBOUND_ASSIGN not in permissions
        assert Permission.INBOUND_CONTROLS not in permissions

    for role in (UserRole.TENANT_ADMIN, UserRole.PARTNER_ADMIN, UserRole.PLATFORM_ADMIN):
        permissions = ROLE_DEFAULT_PERMISSIONS[role]
        assert {
            Permission.INBOUND_READ,
            Permission.INBOUND_MANAGE,
            Permission.INBOUND_ASSIGN,
            Permission.INBOUND_CONTROLS,
        } <= permissions


def test_create_and_assignment_routes_require_database_effective_assign_grant():
    source = __import__("inspect").getsource(inbound_endpoints)
    create_section = source[
        source.index('@router.post(\n    "/",') : source.index("# Literal paths must be registered")
    ]
    assert "Permission.INBOUND_MANAGE" in create_section
    assert "Permission.INBOUND_ASSIGN" in create_section

    assign_route = next(
        route
        for route in inbound_endpoints.router.routes
        if route.path == "/inbound-campaigns/{config_id}/assign"
    )
    assert len(assign_route.dependant.dependencies) >= 3

    paths = [route.path for route in inbound_endpoints.router.routes]
    assert paths.index("/inbound-campaigns/capabilities") < paths.index(
        "/inbound-campaigns/{config_id}"
    )


class _EffectivePermissionConn:
    def __init__(self, *, role_permissions=(), direct_permissions=()):
        self.role_permissions = list(role_permissions)
        self.direct_permissions = list(direct_permissions)

    async def fetch(self, query, *_args):
        if "FROM tenant_users" in query:
            return [{"name": name} for name in self.role_permissions]
        if "FROM user_permissions" in query:
            return [{"name": name} for name in self.direct_permissions]
        raise AssertionError(query)


class _EffectivePermissionAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_args):
        return False


class _EffectivePermissionPool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _EffectivePermissionAcquire(self.conn)


@pytest.mark.asyncio
async def test_inbound_mutation_denies_revoked_tenant_admin_role_grant():
    user = CurrentUser(
        id=ACTOR,
        email="admin@example.test",
        tenant_id=TENANT,
        role="tenant_admin",
    )
    dependency = inbound_endpoints._require_inbound_permission(Permission.INBOUND_MANAGE)
    pool = _EffectivePermissionPool(_EffectivePermissionConn())

    with pytest.raises(HTTPException) as exc:
        await dependency(user=user, db_pool=pool)

    assert exc.value.status_code == 403
    assert exc.value.detail == {
        "code": "permission_denied",
        "required": "inbound:manage",
    }


@pytest.mark.asyncio
async def test_inbound_mutation_honors_direct_user_grant():
    user = CurrentUser(
        id=ACTOR,
        email="user@example.test",
        tenant_id=TENANT,
        role="user",
    )
    dependency = inbound_endpoints._require_inbound_permission(Permission.INBOUND_MANAGE)
    pool = _EffectivePermissionPool(_EffectivePermissionConn(direct_permissions=["inbound:manage"]))

    assert await dependency(user=user, db_pool=pool) is user
    assert user.has_permission("inbound:manage") is True


def test_fresh_schema_and_0022_seed_all_inbound_role_permissions():
    root = Path(__file__).parents[2]
    migration = (root / "Alembic" / "versions" / "0022_inbound_calling_foundation.py").read_text(
        encoding="utf-8"
    )
    fresh_schema = (root / "database" / "complete_schema.sql").read_text(encoding="utf-8")
    permissions = (
        "inbound:read",
        "inbound:manage",
        "inbound:assign",
        "inbound:controls",
    )
    for permission in permissions:
        assert f"('{permission}'," in migration
        assert f"('{permission}'," in fresh_schema

    expected_grants = {
        ("readonly", "inbound:read"),
        ("user", "inbound:read"),
    }
    expected_grants.update(
        (role, permission)
        for role in ("tenant_admin", "partner_admin", "platform_admin")
        for permission in permissions
    )
    for role, permission in expected_grants:
        grant = f"('{role}', '{permission}')"
        assert grant in migration
        assert grant in fresh_schema


class _IdempotencyConn:
    def __init__(self, *, inserted=None, existing=None):
        self.inserted = inserted
        self.existing = existing
        self.calls = []

    async def fetchrow(self, query, *args):
        self.calls.append((query, args))
        if "INSERT INTO inbound_operation_idempotency" in query:
            return self.inserted
        if "SELECT request_hash" in query:
            return self.existing
        raise AssertionError(query)


@pytest.mark.asyncio
async def test_idempotency_replays_exact_request_and_rejects_key_reuse():
    service = InboundCampaignService(None)
    payload = {"value": 1}
    request_hash = campaign_module._stable_hash(payload)
    replay_conn = _IdempotencyConn(
        existing={
            "request_hash": request_hash,
            "response_body": json.dumps({"id": CONFIG}),
            "status_code": 200,
        }
    )
    replay = await service._claim_operation(
        replay_conn,
        tenant_id=TENANT,
        scope_key=f"tenant:{TENANT}",
        operation="test",
        idempotency_key="key-12345",
        actor_id=ACTOR,
        request_payload=payload,
    )
    assert replay == {"id": CONFIG}

    mismatch_conn = _IdempotencyConn(
        existing={
            "request_hash": "not-the-request-hash",
            "response_body": {"id": CONFIG},
            "status_code": 200,
        }
    )
    with pytest.raises(InboundConflictError) as exc:
        await service._claim_operation(
            mismatch_conn,
            tenant_id=TENANT,
            scope_key=f"tenant:{TENANT}",
            operation="test",
            idempotency_key="key-12345",
            actor_id=ACTOR,
            request_payload=payload,
        )
    assert exc.value.code == "idempotency_mismatch"


class _CasConn:
    def __init__(self, version: int, status: str = "paused"):
        self.version = version
        self.status = status
        self.queries = []

    async def fetchrow(self, query, *_args):
        self.queries.append(query)
        if "SELECT id, version, status FROM inbound_campaign_configs" in query:
            return {"id": CONFIG, "version": self.version, "status": self.status}
        if "SELECT version, status FROM inbound_campaign_configs" in query:
            return {"version": self.version, "status": self.status}
        if "FROM inbound_did_assignments" in query and "FOR UPDATE" in query:
            return {
                "id": CONFIG,
                "tenant_id": TENANT,
                "config_id": CONFIG,
                "status": "paused",
                "version": 5,
            }
        if "UPDATE inbound_campaign_configs" in query:
            return {"version": self.version + 1}
        raise AssertionError(query)


@pytest.mark.asyncio
async def test_update_fails_on_optimistic_version_conflict(monkeypatch):
    conn = _CasConn(version=9)

    @asynccontextmanager
    async def acquire(_pool, _tenant):
        yield conn

    monkeypatch.setattr(campaign_module, "acquire_with_tenant", acquire)
    service = InboundCampaignService(object())
    service._claim_operation = AsyncMock(return_value=None)
    service._lock_assignment_for_config = AsyncMock(
        return_value={"id": CONFIG, "status": "paused", "version": 2}
    )
    with pytest.raises(InboundConflictError) as exc:
        await service.update_campaign(
            tenant_id=TENANT,
            config_id=CONFIG,
            actor_id=ACTOR,
            actor_role="tenant_admin",
            payload={"expected_version": 8, "name": "Updated"},
            idempotency_key="update-key-123",
        )
    assert exc.value.code == "version_conflict"
    assert "current 9" in str(exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected_code"),
    [("active", "pause_before_edit"), ("archived", "campaign_archived")],
)
async def test_update_rejects_live_and_archived_configs(monkeypatch, status, expected_code):
    conn = _CasConn(version=3, status=status)

    @asynccontextmanager
    async def acquire(_pool, _tenant):
        yield conn

    monkeypatch.setattr(campaign_module, "acquire_with_tenant", acquire)
    service = InboundCampaignService(object())
    service._claim_operation = AsyncMock(return_value=None)
    service._lock_assignment_for_config = AsyncMock(
        return_value={"id": CONFIG, "status": "paused", "version": 2}
    )
    with pytest.raises(InboundConflictError) as exc:
        await service.update_campaign(
            tenant_id=TENANT,
            config_id=CONFIG,
            actor_id=ACTOR,
            actor_role="tenant_admin",
            payload={"expected_version": 3, "name": "Unsafe live edit"},
            idempotency_key=f"edit-{status}-123",
        )
    assert exc.value.code == expected_code


@pytest.mark.asyncio
async def test_paused_campaign_can_be_edited(monkeypatch):
    conn = _CasConn(version=3, status="paused")
    conn.execute = AsyncMock(return_value="UPDATE 1")

    @asynccontextmanager
    async def acquire(_pool, _tenant):
        yield conn

    monkeypatch.setattr(campaign_module, "acquire_with_tenant", acquire)
    service = InboundCampaignService(object())
    service._claim_operation = AsyncMock(return_value=None)
    service._load_bundle = AsyncMock(side_effect=[object(), object()])
    before = {
        "name": "Paused line",
        "campaign_id": CONFIG,
        "opening_mode": "caller_first",
        "greeting": None,
        "timezone": "UTC",
        "business_hours": {},
        "after_hours_action": "hangup",
        "transfer_number": None,
        "recording_enabled": False,
        "consent_message": None,
        "recording_policy": {},
        "transfer_policy": {},
        "qualification_config": {},
        "did_number": "+15551234567",
        "sip_trunk_id": ACTOR,
        "assignment_id": CONFIG,
    }
    service._serialize_bundle = Mock(
        side_effect=[before, {**before, "name": "Edited while paused"}]
    )
    service._verified_phone = AsyncMock(return_value={"id": ACTOR})
    service._active_trunk = AsyncMock(return_value={"id": ACTOR})
    service._assert_did_free = AsyncMock(return_value=None)
    service._audit = AsyncMock(return_value=None)
    service._store_operation = AsyncMock(return_value=None)

    result = await service.update_campaign(
        tenant_id=TENANT,
        config_id=CONFIG,
        actor_id=ACTOR,
        actor_role="tenant_admin",
        payload={
            "expected_version": 3,
            "name": "Edited while paused",
            # The frontend sends the selected trunk on every edit. Merely
            # echoing the current assignment is not a route mutation.
            "did_number": "+15551234567",
            "sip_trunk_id": ACTOR,
        },
        idempotency_key="edit-paused-123",
    )

    assert result["name"] == "Edited while paused"
    assert any("UPDATE inbound_campaign_configs" in query for query in conn.queries)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "assignment_change",
    [
        {"did_number": "+15557654321"},
        {"sip_trunk_id": "44444444-4444-4444-4444-444444444444"},
    ],
)
async def test_generic_update_rejects_did_or_trunk_assignment_mutation(
    monkeypatch, assignment_change
):
    conn = _CasConn(version=3, status="paused")

    @asynccontextmanager
    async def acquire(_pool, _tenant):
        yield conn

    monkeypatch.setattr(campaign_module, "acquire_with_tenant", acquire)
    service = InboundCampaignService(object())
    service._claim_operation = AsyncMock(return_value=None)
    service._load_bundle = AsyncMock(return_value=object())
    service._serialize_bundle = Mock(
        return_value={
            "campaign_id": CONFIG,
            "did_number": "+15551234567",
            "sip_trunk_id": ACTOR,
        }
    )
    service._verified_phone = AsyncMock()

    with pytest.raises(InboundConflictError) as exc:
        await service.update_campaign(
            tenant_id=TENANT,
            config_id=CONFIG,
            actor_id=ACTOR,
            actor_role="tenant_admin",
            payload={"expected_version": 3, **assignment_change},
            idempotency_key="generic-assignment-change-123",
        )
    assert exc.value.code == "assignment_workflow_required"
    service._verified_phone.assert_not_awaited()


@pytest.mark.asyncio
async def test_active_campaign_must_be_paused_before_archive(monkeypatch):
    conn = _CasConn(version=3, status="active")

    @asynccontextmanager
    async def acquire(_pool, _tenant):
        yield conn

    monkeypatch.setattr(campaign_module, "acquire_with_tenant", acquire)
    service = InboundCampaignService(object())
    service._claim_operation = AsyncMock(return_value=None)
    with pytest.raises(InboundConflictError) as exc:
        await service.set_lifecycle(
            tenant_id=TENANT,
            config_id=CONFIG,
            target_status="archived",
            actor_id=ACTOR,
            actor_role="tenant_admin",
            idempotency_key="archive-live-123",
            expected_version=3,
        )
    assert exc.value.code == "pause_before_archive"


def test_readiness_blocks_disabled_platform_and_unsupported_overrides():
    result = InboundCampaignService._readiness(
        _ready_bundle(
            platform_inbound_enabled=False,
            platform_settlement_enabled=False,
            qualification_config={"knowledge_base_id": "not-wired"},
        )
    )
    assert result["ready"] is False
    blockers = {item["code"] for item in result["blockers"]}
    assert {
        "platform_inbound_enabled",
        "platform_settlement_enabled",
        "qualification_runtime_supported",
    } <= blockers


def test_neutral_frontend_qualification_defaults_do_not_block_and_supported_values_apply():
    neutral = {
        "purpose": None,
        "persona": " ",
        "system_prompt": None,
        "knowledge_base_id": None,
        "voice_id": None,
        "allowed_tools": [],
        "silence_timeout_seconds": 8,
    }
    assert (
        InboundCampaignService._readiness(_ready_bundle(qualification_config=neutral))["ready"]
        is True
    )

    merged = apply_qualification_overrides(
        {"voice_id": "base-voice", "script_config": {"persona_type": "lead_gen"}},
        {"system_prompt": "Handle support questions only.", "voice_id": "inbound-voice"},
    )
    assert merged["voice_id"] == "inbound-voice"
    assert merged["script_config"]["persona_type"] == "lead_gen"
    assert "Handle support questions only." in merged["script_config"]["additional_instructions"]


@pytest.mark.parametrize("value", [True, "1800", 59, 14_401])
def test_readiness_rejects_invalid_max_call_duration(value):
    result = InboundCampaignService._readiness(
        _ready_bundle(
            transfer_policy={"max_call_duration_seconds": value},
        )
    )
    blockers = {item["code"] for item in result["blockers"]}
    assert "max_call_duration_valid" in blockers


@pytest.mark.parametrize(
    "policy", [{}, {"max_call_duration_seconds": 60}, {"max_call_duration_seconds": 14_400}]
)
def test_readiness_accepts_default_and_bounded_max_call_duration(policy):
    result = InboundCampaignService._readiness(_ready_bundle(transfer_policy=policy))
    check = next(item for item in result["checks"] if item["key"] == "max_call_duration_valid")
    assert check["passed"] is True


def test_readiness_requires_after_hours_destination_in_explicit_allowlist():
    mismatched = InboundCampaignService._readiness(
        _ready_bundle(
            trunk_direction="both",
            after_hours_action="transfer",
            transfer_number="+15559876543",
            transfer_policy={
                "enabled": True,
                "destinations": ["+15551234567"],
            },
        )
    )
    mismatch_check = next(
        item for item in mismatched["checks"] if item["key"] == "after_hours_complete"
    )
    assert mismatch_check["passed"] is False

    normalized_match = InboundCampaignService._readiness(
        _ready_bundle(
            trunk_direction="both",
            after_hours_action="transfer",
            transfer_number="+1 (555) 987-6543",
            transfer_policy={
                "enabled": True,
                "destinations": ["+15559876543"],
            },
        )
    )
    match_check = next(
        item for item in normalized_match["checks"] if item["key"] == "after_hours_complete"
    )
    assert match_check["passed"] is True


def test_readiness_requires_bidirectional_trunk_only_when_transfer_requested():
    ordinary_inbound = InboundCampaignService._readiness(_ready_bundle())
    ordinary_check = next(
        item for item in ordinary_inbound["checks"] if item["key"] == "transfer_trunk_bidirectional"
    )
    assert ordinary_check["passed"] is True
    assert ordinary_inbound["ready"] is True

    policy_enabled = InboundCampaignService._readiness(
        _ready_bundle(
            trunk_direction="inbound",
            transfer_policy={
                "enabled": True,
                "destinations": ["+15551234567"],
            },
        )
    )
    assert "transfer_trunk_bidirectional" in {item["code"] for item in policy_enabled["blockers"]}

    bidirectional = InboundCampaignService._readiness(
        _ready_bundle(
            trunk_direction="both",
            transfer_policy={
                "enabled": True,
                "destinations": ["+15551234567"],
            },
        )
    )
    bidirectional_check = next(
        item for item in bidirectional["checks"] if item["key"] == "transfer_trunk_bidirectional"
    )
    assert bidirectional_check["passed"] is True
    assert "transfer_runtime_supported" in {item["code"] for item in bidirectional["blockers"]}


def test_readiness_accepts_safe_actions_and_blocks_unproven_transfer_runtime():
    for action in ("hangup", "voicemail"):
        assert (
            InboundCampaignService._readiness(_ready_bundle(after_hours_action=action))["ready"]
            is True
        )

    intake_without_greeting = InboundCampaignService._readiness(
        _ready_bundle(after_hours_action="voicemail", business_hours={})
    )
    assert "after_hours_message_valid" in {
        item["code"] for item in intake_without_greeting["blockers"]
    }
    intake_runtime = next(
        check
        for check in InboundCampaignService._readiness(
            _ready_bundle(after_hours_action="voicemail")
        )["checks"]
        if check["key"] == "after_hours_runtime_supported"
    )
    assert "Conversational AI message intake" in intake_runtime["detail"]
    assert "not a one-way voicemail artifact" in intake_runtime["detail"]

    blocked = InboundCampaignService._readiness(
        _ready_bundle(
            after_hours_action="transfer",
            transfer_number="+15551234567",
            transfer_policy={"enabled": False},
        )
    )
    assert "after_hours_complete" in {item["code"] for item in blocked["blockers"]}

    blocked_with_policy = InboundCampaignService._readiness(
        _ready_bundle(
            after_hours_action="transfer",
            transfer_number="+15551234567",
            transfer_policy={"enabled": True, "destinations": ["+15551234567"]},
        )
    )
    assert blocked_with_policy["ready"] is False
    blockers = {item["code"] for item in blocked_with_policy["blockers"]}
    assert "after_hours_runtime_supported" in blockers
    assert "transfer_runtime_supported" in blockers

    agent_path_with_transfer_enabled = InboundCampaignService._readiness(
        _ready_bundle(
            after_hours_action="hangup",
            transfer_policy={"enabled": True, "destinations": ["+15551234567"]},
        )
    )
    assert "transfer_runtime_supported" in {
        item["code"] for item in agent_path_with_transfer_enabled["blockers"]
    }


def test_readiness_allows_transfer_only_with_runtime_and_platform_gates(monkeypatch):
    from app.domain.services.telephony import inbound_transfer

    monkeypatch.setattr(
        inbound_transfer,
        "inbound_transfer_scope_available",
        lambda **_kwargs: True,
    )
    configured = _ready_bundle(
        trunk_direction="both",
        after_hours_action="transfer",
        transfer_number="+15551234567",
        transfer_policy={"enabled": True, "destinations": ["+15551234567"]},
        platform_transfer_enabled=True,
    )

    assert InboundCampaignService._readiness(configured)["ready"] is True

    configured["platform_transfer_enabled"] = False
    blocked = InboundCampaignService._readiness(configured)
    assert blocked["ready"] is False
    assert "platform_transfer_enabled" in {item["code"] for item in blocked["blockers"]}


def test_campaign_transfer_write_gate_allows_disable_and_approved_staging(monkeypatch):
    from app.domain.services.telephony import inbound_transfer

    transfer_payload = {
        "after_hours_action": "transfer",
        "transfer_policy": {"enabled": True},
    }
    monkeypatch.setattr(inbound_transfer, "inbound_transfer_runtime_available", lambda: False)
    with pytest.raises(InboundConflictError) as exc:
        campaign_module._require_transfer_runtime(transfer_payload)
    assert exc.value.code == "transfer_runtime_unavailable"

    campaign_module._require_transfer_runtime(
        {"after_hours_action": "hangup", "transfer_policy": {"enabled": False}}
    )

    monkeypatch.setattr(inbound_transfer, "inbound_transfer_runtime_available", lambda: True)
    campaign_module._require_transfer_runtime(transfer_payload)


@pytest.mark.asyncio
async def test_campaign_transfer_write_requires_platform_gate(monkeypatch):
    class Conn:
        def __init__(self, enabled):
            self.enabled = enabled

        async def fetchval(self, query, *_args):
            assert "inbound_transfer_enabled" in query
            return self.enabled

    from app.domain.services.telephony import inbound_transfer

    monkeypatch.setattr(inbound_transfer, "inbound_transfer_runtime_available", lambda: True)
    monkeypatch.setattr(
        inbound_transfer,
        "inbound_transfer_scope_available",
        lambda **_kwargs: True,
    )
    transfer_payload = {
        "after_hours_action": "transfer",
        "transfer_policy": {"enabled": True},
    }
    with pytest.raises(InboundConflictError) as exc:
        await campaign_module._require_transfer_configuration(
            Conn(False),
            transfer_payload,
            tenant_id=TENANT,
            config_id=CONFIG,
        )
    assert exc.value.code == "transfer_platform_disabled"

    await campaign_module._require_transfer_configuration(
        Conn(True),
        transfer_payload,
        tenant_id=TENANT,
        config_id=CONFIG,
    )
    await campaign_module._require_transfer_configuration(
        Conn(False),
        {"after_hours_action": "hangup", "transfer_policy": {"enabled": False}},
        tenant_id=TENANT,
        config_id=CONFIG,
    )

    monkeypatch.setattr(
        inbound_transfer,
        "inbound_transfer_scope_available",
        lambda **_kwargs: False,
    )
    with pytest.raises(InboundConflictError) as scope_exc:
        await campaign_module._require_transfer_configuration(
            Conn(True),
            transfer_payload,
            tenant_id=TENANT,
            config_id=CONFIG,
        )
    assert scope_exc.value.code == "transfer_staging_scope_mismatch"


@pytest.mark.asyncio
async def test_transfer_create_replay_survives_gate_closure(monkeypatch):
    @asynccontextmanager
    async def acquire(_pool, _tenant):
        yield object()

    from app.domain.services.telephony import inbound_transfer

    monkeypatch.setattr(campaign_module, "acquire_with_tenant", acquire)
    monkeypatch.setattr(inbound_transfer, "inbound_transfer_runtime_available", lambda: False)
    service = InboundCampaignService(object())
    service._claim_operation = AsyncMock(return_value={"id": CONFIG, "status": "draft"})
    result = await service.create_campaign(
        tenant_id=TENANT,
        actor_id=ACTOR,
        actor_role="tenant_admin",
        payload={
            "name": "Transfer proof",
            "did_number": "+15551234567",
            "campaign_id": ACTOR,
            "sip_trunk_id": CONFIG,
            "timezone": "UTC",
            "after_hours_action": "transfer",
            "transfer_number": "+15557654321",
            "transfer_policy": {"enabled": True},
        },
        idempotency_key="transfer-create-replay",
    )
    assert result["idempotent_replay"] is True
    assert result["id"] == CONFIG


@pytest.mark.asyncio
async def test_admin_assignment_list_uses_complete_readiness_bundle(monkeypatch):
    class Conn:
        query = ""

        async def fetch(self, query, *_args):
            self.query = query
            return [
                {
                    **_ready_bundle(),
                    "assignment_id": CONFIG,
                    "tenant_id": TENANT,
                    "tenant_name": "Example tenant",
                    "canonical_did": "+15551234567",
                    "campaign_id": ACTOR,
                    "campaign_name": "Support",
                    "sip_trunk_id": CONFIG,
                    "assignment_status": "paused",
                    "assignment_version": 3,
                    "config_version": 7,
                    "last_call_at": None,
                    "last_error": None,
                }
            ]

    conn = Conn()

    @asynccontextmanager
    async def acquire(_pool, _tenant):
        yield conn

    monkeypatch.setattr(campaign_module, "acquire_with_tenant", acquire)
    result = await InboundCampaignService(object()).admin_list_assignments()
    assert result["total"] == 1
    assert result["items"][0]["readiness"]["ready"] is True
    assert "tenant_ai_config_id" in conn.query
    assert "platform_settlement_enabled" in conn.query


@pytest.mark.asyncio
async def test_missing_tenant_control_is_reported_fail_closed(monkeypatch):
    class Conn:
        async def fetchrow(self, _query, *_args):
            return None

    @asynccontextmanager
    async def acquire(_pool, _tenant):
        yield Conn()

    monkeypatch.setattr(campaign_module, "acquire_with_tenant", acquire)
    result = await InboundCampaignService(object()).get_tenant_controls(tenant_id=TENANT)
    assert result == {
        "inbound_enabled": False,
        "version": 1,
        "reason": None,
        "updated_at": None,
    }


@pytest.mark.asyncio
async def test_production_off_to_on_platform_transition_requires_live_asterisk(
    monkeypatch,
):
    class Conn:
        async def fetchrow(self, query, *_args):
            if "SELECT * FROM platform_runtime_controls" in query:
                return {"inbound_enabled": False, "inbound_controls_version": 4}
            raise AssertionError(query)

    @asynccontextmanager
    async def acquire(_pool, _tenant):
        yield Conn()

    monkeypatch.setattr(campaign_module, "acquire_with_tenant", acquire)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("TELEPHONY_ADAPTER", "asterisk")
    from app.api.v1.endpoints import telephony_bridge

    monkeypatch.setattr(telephony_bridge, "_adapter", object())
    service = InboundCampaignService(object())
    service._claim_operation = AsyncMock(return_value=None)
    with pytest.raises(InboundConflictError) as exc:
        await service.set_platform_controls(
            actor_id=ACTOR,
            actor_role="platform_admin",
            payload={
                "inbound_enabled": True,
                "recording_enabled": False,
                "transfer_enabled": False,
                "settlement_enabled": True,
                "expected_version": 4,
                "reason": "controlled canary enable",
            },
            idempotency_key="platform-enable-123",
        )
    assert exc.value.code == "inbound_runtime_restart_required"


@pytest.mark.asyncio
async def test_production_off_to_on_requires_strict_redis_ownership(monkeypatch):
    class Conn:
        async def fetchrow(self, query, *_args):
            if "SELECT * FROM platform_runtime_controls" in query:
                return {"inbound_enabled": False, "inbound_controls_version": 4}
            raise AssertionError(query)

    @asynccontextmanager
    async def acquire(_pool, _tenant):
        yield Conn()

    class StateBackend:
        heartbeat_started = False

        async def acquire_telephony_ownership_strict(self):
            return False

        async def start_heartbeat_strict(self):
            self.heartbeat_started = True

        def is_telephony_owner(self):
            return False

    state = StateBackend()
    monkeypatch.setattr(campaign_module, "acquire_with_tenant", acquire)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("TELEPHONY_ADAPTER", "asterisk")
    monkeypatch.setenv("TELEPHONY_STATE_BACKEND", "redis")
    from app.api.v1.endpoints import telephony_bridge
    from app.core import inbound_startup
    from app.domain.services.telephony import state_backend as state_module

    monkeypatch.setattr(telephony_bridge, "_adapter", object())
    monkeypatch.setattr(
        inbound_startup, "validate_live_production_inbound_adapter", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        inbound_startup, "validate_production_inbound_state_backend", lambda **_kwargs: None
    )
    monkeypatch.setattr(state_module, "get_state_backend", lambda: state)
    service = InboundCampaignService(object())
    service._claim_operation = AsyncMock(return_value=None)

    with pytest.raises(InboundConflictError) as exc:
        await service.set_platform_controls(
            actor_id=ACTOR,
            actor_role="platform_admin",
            payload={
                "inbound_enabled": True,
                "recording_enabled": False,
                "transfer_enabled": False,
                "settlement_enabled": True,
                "expected_version": 4,
                "reason": "controlled canary enable",
            },
            idempotency_key="platform-owner-123",
        )

    assert exc.value.code == "inbound_runtime_restart_required"
    assert state.heartbeat_started is False


class _PhoneConn:
    def __init__(self, row):
        self.row = row
        self.query = ""

    async def fetchrow(self, query, *_args):
        self.query = query
        return self.row


@pytest.mark.asyncio
async def test_assignment_requires_existing_verified_tenant_phone_row():
    service = InboundCampaignService(None)
    missing = _PhoneConn(None)
    with pytest.raises(InboundConflictError) as exc:
        await service._verified_phone(missing, tenant_id=TENANT, did="+15551234567")
    assert exc.value.code == "did_not_verified"
    assert "SELECT id, status FROM tenant_phone_numbers" in missing.query
    assert "INSERT" not in missing.query.upper()

    pending = _PhoneConn({"id": CONFIG, "status": "pending_verification"})
    with pytest.raises(InboundConflictError):
        await service._verified_phone(pending, tenant_id=TENANT, did="+15551234567")


def test_invalid_timezone_has_stable_422_error():
    with pytest.raises(InboundCampaignError) as exc:
        campaign_module._timezone("Mars/Olympus_Mons")
    assert exc.value.status_code == 422
    assert exc.value.code == "invalid_timezone"


@pytest.mark.parametrize(
    ("overrides", "blocker"),
    [
        ({"tenant_tts_model": ""}, "ai_providers_configured"),
        ({"tenant_pipeline_mode": "unsupported"}, "pipeline_mode_valid"),
        (
            {"business_hours": {"after_hours_message": {"not": "text"}}},
            "after_hours_message_valid",
        ),
    ],
)
def test_readiness_matches_admission_ai_and_after_hours_contract(overrides, blocker):
    readiness = InboundCampaignService._readiness(_ready_bundle(**overrides))
    assert readiness["ready"] is False
    assert blocker in {item["code"] for item in readiness["blockers"]}


def test_live_did_uniqueness_and_archive_loading_are_database_backed():
    migration = (
        Path(__file__).parents[2] / "Alembic" / "versions" / "0022_inbound_calling_foundation.py"
    ).read_text(encoding="utf-8")
    assert "CREATE UNIQUE INDEX uq_inbound_live_canonical_did" in migration
    assert "WHERE status <> 'archived'" in migration
    assert "ORDER BY (candidate.status <> 'archived') DESC" in campaign_module._BUNDLE_SQL
    assert "WHERE candidate.config_id = cfg.id\n        ORDER BY" in campaign_module._BUNDLE_SQL


@pytest.mark.asyncio
async def test_expired_idempotency_claim_is_atomically_reclaimed():
    conn = _IdempotencyConn(inserted={"id": CONFIG})
    result = await InboundCampaignService(None)._claim_operation(
        conn,
        tenant_id=TENANT,
        scope_key=f"tenant:{TENANT}",
        operation="inbound_campaign.update",
        idempotency_key="expired-key-123",
        actor_id=ACTOR,
        request_payload={"expected_version": 3},
    )
    assert result is None
    claim_sql = conn.calls[0][0]
    assert "ON CONFLICT (scope_key, operation, idempotency_key) DO UPDATE" in claim_sql
    assert "expires_at = NOW() + INTERVAL '24 hours'" in claim_sql
    assert "WHERE inbound_operation_idempotency.expires_at <= NOW()" in claim_sql


@pytest.mark.asyncio
async def test_did_availability_requires_verified_tenant_ownership(monkeypatch):
    class Conn:
        def __init__(self):
            self.queries = []

        async def fetchrow(self, query, *_args):
            self.queries.append(query)
            if "FROM tenant_phone_numbers" in query:
                return None
            raise AssertionError("global assignment inventory must not be queried")

    conn = Conn()

    @asynccontextmanager
    async def acquire(_pool, _tenant):
        yield conn

    monkeypatch.setattr(campaign_module, "acquire_with_tenant", acquire)
    with pytest.raises(InboundConflictError) as exc:
        await InboundCampaignService(object()).did_availability(
            tenant_id=TENANT,
            did_number="+15551234567",
        )
    assert exc.value.code == "did_not_verified"
    assert len(conn.queries) == 1


@pytest.mark.asyncio
async def test_platform_cannot_enable_unavailable_transfer_runtime(monkeypatch):
    class Conn:
        async def fetchrow(self, query, *_args):
            if "SELECT * FROM platform_runtime_controls" in query:
                return {
                    "inbound_controls_version": 4,
                    "inbound_enabled": True,
                    "inbound_transfer_enabled": False,
                }
            raise AssertionError(query)

    @asynccontextmanager
    async def acquire(_pool, _tenant):
        yield Conn()

    from app.domain.services.telephony import inbound_transfer

    monkeypatch.setattr(campaign_module, "acquire_with_tenant", acquire)
    monkeypatch.setattr(inbound_transfer, "inbound_transfer_runtime_available", lambda: False)
    service = InboundCampaignService(object())
    service._claim_operation = AsyncMock(return_value=None)
    with pytest.raises(InboundConflictError) as exc:
        await service.set_platform_controls(
            actor_id=ACTOR,
            actor_role="platform_admin",
            payload={
                "inbound_enabled": True,
                "recording_enabled": False,
                "transfer_enabled": True,
                "settlement_enabled": True,
                "expected_version": 4,
                "reason": "unsafe enable attempt",
            },
            idempotency_key="transfer-enable-123",
        )
    assert exc.value.code == "transfer_runtime_unavailable"


@pytest.mark.asyncio
async def test_runtime_capabilities_require_code_and_platform_transfer_gates(monkeypatch):
    class Conn:
        def __init__(self, *, configuration_owned=True):
            self.configuration_owned = configuration_owned

        async def fetchval(self, query, *_args):
            if "inbound_transfer_enabled" in query:
                return True
            if "FROM inbound_campaign_configs" in query:
                return self.configuration_owned
            raise AssertionError(query)

    @asynccontextmanager
    async def acquire(_pool, _tenant):
        yield Conn()

    from app.domain.services.telephony import inbound_transfer

    monkeypatch.setattr(campaign_module, "acquire_with_tenant", acquire)
    monkeypatch.setattr(
        inbound_transfer,
        "inbound_transfer_scope_available",
        lambda **_kwargs: False,
    )
    closed = await InboundCampaignService(object()).get_runtime_capabilities(
        tenant_id=TENANT,
        config_id=CONFIG,
    )
    assert closed == {
        "transfer_runtime_available": False,
        "transfer_platform_enabled": True,
        "transfer_configuration_available": False,
    }

    monkeypatch.setattr(
        inbound_transfer,
        "inbound_transfer_scope_available",
        lambda **_kwargs: True,
    )
    opened = await InboundCampaignService(object()).get_runtime_capabilities(
        tenant_id=TENANT,
        config_id=CONFIG,
    )
    assert opened["transfer_configuration_available"] is True

    @asynccontextmanager
    async def acquire_stale(_pool, _tenant):
        yield Conn(configuration_owned=False)

    monkeypatch.setattr(campaign_module, "acquire_with_tenant", acquire_stale)
    stale = await InboundCampaignService(object()).get_runtime_capabilities(
        tenant_id=TENANT,
        config_id=CONFIG,
    )
    assert stale == {
        "transfer_runtime_available": False,
        "transfer_platform_enabled": True,
        "transfer_configuration_available": False,
    }


@pytest.mark.asyncio
async def test_platform_can_disable_transfer_when_runtime_is_unavailable(monkeypatch):
    class Conn:
        async def fetchrow(self, query, *_args):
            if "SELECT * FROM platform_runtime_controls" in query:
                return {
                    "inbound_controls_version": 4,
                    "inbound_enabled": True,
                    "inbound_transfer_enabled": True,
                }
            if "UPDATE platform_runtime_controls" in query:
                return {
                    "inbound_enabled": True,
                    "inbound_recording_enabled": False,
                    "inbound_transfer_enabled": False,
                    "inbound_settlement_enabled": True,
                    "inbound_controls_version": 5,
                    "inbound_controls_reason": "emergency disable",
                    "inbound_controls_updated_by": ACTOR,
                    "inbound_controls_updated_at": datetime.now(timezone.utc),
                }
            raise AssertionError(query)

    @asynccontextmanager
    async def acquire(_pool, _tenant):
        yield Conn()

    from app.domain.services.telephony import inbound_transfer

    monkeypatch.setattr(campaign_module, "acquire_with_tenant", acquire)
    monkeypatch.setattr(inbound_transfer, "inbound_transfer_runtime_available", lambda: False)
    service = InboundCampaignService(object())
    service._claim_operation = AsyncMock(return_value=None)
    service._audit = AsyncMock(return_value=None)
    service._store_operation = AsyncMock(return_value=None)
    result = await service.set_platform_controls(
        actor_id=ACTOR,
        actor_role="platform_admin",
        payload={
            "inbound_enabled": True,
            "recording_enabled": False,
            "transfer_enabled": False,
            "settlement_enabled": True,
            "expected_version": 4,
            "reason": "emergency disable",
        },
        idempotency_key="transfer-disable-123",
    )
    assert result["transfer_enabled"] is False
    assert result["version"] == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["update", "lifecycle"])
async def test_tenant_mutations_reject_platform_quarantine(monkeypatch, operation):
    class Conn:
        async def fetchrow(self, query, *_args):
            if "FROM inbound_campaign_configs" in query and "FOR UPDATE" in query:
                return {"id": CONFIG, "version": 3, "status": "paused"}
            if "FROM inbound_did_assignments" in query and "FOR UPDATE" in query:
                return {
                    "id": CONFIG,
                    "tenant_id": TENANT,
                    "config_id": CONFIG,
                    "status": "quarantined",
                    "version": 7,
                }
            raise AssertionError(query)

    @asynccontextmanager
    async def acquire(_pool, _tenant):
        yield Conn()

    monkeypatch.setattr(campaign_module, "acquire_with_tenant", acquire)
    service = InboundCampaignService(object())
    service._claim_operation = AsyncMock(return_value=None)
    with pytest.raises(InboundConflictError) as exc:
        if operation == "update":
            await service.update_campaign(
                tenant_id=TENANT,
                config_id=CONFIG,
                actor_id=ACTOR,
                actor_role="tenant_admin",
                payload={"expected_version": 3, "name": "blocked edit"},
                idempotency_key="quarantine-edit-123",
            )
        else:
            await service.set_lifecycle(
                tenant_id=TENANT,
                config_id=CONFIG,
                target_status="paused",
                actor_id=ACTOR,
                actor_role="tenant_admin",
                expected_version=3,
                idempotency_key="quarantine-pause-123",
            )
    assert exc.value.code == "assignment_quarantined"


@pytest.mark.asyncio
async def test_quarantine_locks_config_before_assignment_and_checks_returning(monkeypatch):
    class Conn:
        def __init__(self):
            self.queries = []

        async def fetchrow(self, query, *_args):
            self.queries.append(query)
            if "SELECT config_id FROM inbound_did_assignments" in query:
                return {"config_id": CONFIG}
            if "SELECT id FROM inbound_campaign_configs" in query:
                return {"id": CONFIG}
            if "SELECT * FROM inbound_did_assignments" in query:
                return {
                    "id": CONFIG,
                    "config_id": CONFIG,
                    "tenant_id": TENANT,
                    "canonical_did": "+15551234567",
                    "status": "paused",
                    "status_before_quarantine": None,
                    "version": 2,
                }
            if "UPDATE inbound_did_assignments" in query:
                return {
                    "id": CONFIG,
                    "canonical_did": "+15551234567",
                    "status": "quarantined",
                    "quarantine_reason": "fraud signal",
                    "version": 3,
                }
            raise AssertionError(query)

    conn = Conn()

    @asynccontextmanager
    async def acquire(_pool, _tenant):
        yield conn

    monkeypatch.setattr(campaign_module, "acquire_with_tenant", acquire)
    service = InboundCampaignService(object())
    service._claim_operation = AsyncMock(return_value=None)
    service._audit = AsyncMock(return_value=None)
    service._store_operation = AsyncMock(return_value=None)
    result = await service.set_assignment_quarantine(
        assignment_id=CONFIG,
        quarantined=True,
        expected_version=2,
        reason="fraud signal",
        actor_id=ACTOR,
        actor_role="platform_admin",
        idempotency_key="quarantine-lock-123",
    )
    assert result["status"] == "quarantined"
    config_lock = next(
        index
        for index, query in enumerate(conn.queries)
        if "SELECT id FROM inbound_campaign_configs" in query
    )
    assignment_lock = next(
        index
        for index, query in enumerate(conn.queries)
        if "SELECT * FROM inbound_did_assignments" in query
    )
    assert config_lock < assignment_lock
    assert "RETURNING *" in conn.queries[-1]


@pytest.mark.asyncio
async def test_archived_status_filters_are_retrievable_for_tenant_and_admin(monkeypatch):
    class Conn:
        def __init__(self):
            self.queries = []

        async def fetch(self, query, *_args):
            self.queries.append(query)
            return []

    conn = Conn()

    @asynccontextmanager
    async def acquire(_pool, _tenant):
        yield conn

    monkeypatch.setattr(campaign_module, "acquire_with_tenant", acquire)
    service = InboundCampaignService(object())
    assert await service.list_campaigns(tenant_id=TENANT, include_archived=True) == []
    await service.admin_list_assignments(status="archived")
    await service.admin_list_campaigns(status="archived")
    assert "cfg.status <> 'archived'" not in conn.queries[0]
    assert "WHERE a.status = $1" in conn.queries[1]
    assert "WHERE cfg.status = $1" in conn.queries[2]


def test_archive_response_is_reloaded_from_committed_rows():
    source = __import__("inspect").getsource(InboundCampaignService.set_lifecycle)
    assert "result = dict(before)" not in source
    assert "await self._load_bundle" in source
    assert "RETURNING version, status, updated_at" in source


@pytest.mark.asyncio
async def test_assignment_reason_reaches_versioned_update_without_checksum(monkeypatch):
    captured = {}

    async def update(
        config_id,
        payload,
        user,
        idempotency_key,
        db_pool,
        assignment_workflow=False,
    ):
        captured.update(
            config_id=config_id,
            payload=payload,
            user=user,
            idempotency_key=idempotency_key,
            db_pool=db_pool,
            assignment_workflow=assignment_workflow,
        )
        return {"id": config_id}

    monkeypatch.setattr(inbound_endpoints, "_update", update)
    user = CurrentUser(
        id=ACTOR,
        email="admin@example.test",
        tenant_id=TENANT,
        role="tenant_admin",
    )
    result = await inbound_endpoints.assign_inbound_did(
        CONFIG,
        InboundDidAssignmentRequest(
            did_number="+15551234567",
            sip_trunk_id=ACTOR,
            expected_version=4,
            reason="Carrier port completed",
        ),
        user=user,
        idempotency_key="assign-key-123",
        db_pool=object(),
    )
    assert result == {"id": CONFIG}
    assert captured["payload"].expected_version == 4
    assert captured["payload"].reason == "Carrier port completed"
    assert captured["assignment_workflow"] is True
    assert campaign_module._config_checksum(
        {"name": "A", "reason": "first audit reason"}
    ) == campaign_module._config_checksum({"name": "A", "reason": "a different audit reason"})


class _CreateReassignmentConn:
    def __init__(self):
        self.source_tenant = TENANT
        self.target_tenant = "44444444-4444-4444-4444-444444444444"
        self.request_id = "55555555-5555-5555-5555-555555555555"
        self.assignment_id = "66666666-6666-6666-6666-666666666666"
        self.target_campaign = "77777777-7777-7777-7777-777777777777"
        self.target_config = "88888888-8888-8888-8888-888888888888"
        self.source_config = CONFIG
        self.target_config_exists = True
        self.target_campaign_direction = "inbound"
        self.queries = []
        self.assignment = {
            "id": self.assignment_id,
            "tenant_id": self.source_tenant,
            "config_id": self.source_config,
            "canonical_did": "+15551234567",
            "status": "active",
            "version": 3,
        }

    async def fetchrow(self, query, *args):
        self.queries.append((query, args))
        if "SELECT config_id FROM inbound_did_assignments" in query:
            return {"config_id": self.source_config}
        if "SELECT id FROM inbound_campaign_configs" in query:
            return {"id": self.source_config}
        if "SELECT * FROM inbound_did_assignments" in query:
            return self.assignment
        if "FROM campaigns" in query:
            return {
                "id": self.target_campaign,
                "tenant_id": self.target_tenant,
                "direction": self.target_campaign_direction,
                "status": "running",
            }
        if "FROM inbound_campaign_configs" in query:
            if not self.target_config_exists:
                return None
            return {
                "id": self.target_config,
                "tenant_id": self.target_tenant,
                "campaign_id": self.target_campaign,
                "status": "paused",
            }
        if "UPDATE inbound_did_assignments" in query:
            return {**self.assignment, "status": "quarantined", "version": 4}
        if "INSERT INTO inbound_reassignment_requests" in query:
            return {
                "id": self.request_id,
                "tenant_id": self.source_tenant,
                "source_tenant_id": self.source_tenant,
                "assignment_id": self.assignment_id,
                "approved_assignment_id": None,
                "target_tenant_id": self.target_tenant,
                "target_campaign_id": self.target_campaign,
                "target_config_id": self.target_config,
                "expected_assignment_version": 4,
                "status": "pending",
                "reason": "Customer ownership transfer",
                "decision_reason": None,
                "requested_by": ACTOR,
                "approved_by": None,
                "requested_at": datetime.now(timezone.utc),
                "decided_at": None,
            }
        raise AssertionError(query)

    async def fetchval(self, query, *_args):
        self.queries.append((query, _args))
        if "FROM inbound_reassignment_requests" in query:
            return None
        if "FROM inbound_did_assignments" in query:
            return False
        raise AssertionError(query)


@pytest.mark.asyncio
async def test_reassignment_request_only_references_target_owned_config(monkeypatch):
    conn = _CreateReassignmentConn()

    @asynccontextmanager
    async def acquire(_pool, _tenant):
        yield conn

    monkeypatch.setattr(campaign_module, "acquire_with_tenant", acquire)
    service = InboundCampaignService(object())
    service._claim_operation = AsyncMock(return_value=None)
    service._audit = AsyncMock()
    service._store_operation = AsyncMock()

    result = await service.create_reassignment_request(
        assignment_id=conn.assignment_id,
        target_tenant_id=conn.target_tenant,
        target_campaign_id=conn.target_campaign,
        expected_version=3,
        reason="Customer ownership transfer",
        actor_id=ACTOR,
        actor_role="platform_admin",
        idempotency_key="request-key-123",
    )

    assert result["status"] == "pending"
    statements = [" ".join(query.split()) for query, _ in conn.queries]
    mutations = [query for query in statements if query.upper().startswith(("INSERT", "UPDATE"))]
    assert len(mutations) == 2
    assert "UPDATE inbound_did_assignments" in mutations[0]
    assert "INSERT INTO inbound_reassignment_requests" in mutations[1]
    assert not any("UPDATE campaigns" in query for query in statements)
    assert not any("INSERT INTO inbound_campaign_configs" in query for query in statements)
    assert not any("tenant_phone_numbers" in query for query in statements)
    assert not any(
        secret_field in query
        for query in statements
        for secret_field in (
            "greeting",
            "recording_policy",
            "transfer_policy",
            "qualification_config",
        )
    )
    request_insert = next(
        args for query, args in conn.queries if "INSERT INTO inbound_reassignment_requests" in query
    )
    assert request_insert[3:5] == (conn.target_campaign, conn.target_config)


@pytest.mark.asyncio
async def test_reassignment_request_never_converts_or_clones_target(monkeypatch):
    conn = _CreateReassignmentConn()
    conn.target_campaign_direction = "outbound"

    @asynccontextmanager
    async def acquire(_pool, _tenant):
        yield conn

    monkeypatch.setattr(campaign_module, "acquire_with_tenant", acquire)
    service = InboundCampaignService(object())
    service._claim_operation = AsyncMock(return_value=None)

    with pytest.raises(InboundConflictError) as exc_info:
        await service.create_reassignment_request(
            assignment_id=conn.assignment_id,
            target_tenant_id=conn.target_tenant,
            target_campaign_id=conn.target_campaign,
            expected_version=3,
            reason="Customer ownership transfer",
            actor_id=ACTOR,
            actor_role="platform_admin",
            idempotency_key="request-key-outbound",
        )

    assert exc_info.value.code == "target_campaign_not_inbound"
    assert not any(
        query.lstrip().upper().startswith(("INSERT", "UPDATE")) for query, _ in conn.queries
    )


@pytest.mark.asyncio
async def test_reassignment_request_requires_target_owned_config_without_cloning(
    monkeypatch,
):
    conn = _CreateReassignmentConn()
    conn.target_config_exists = False

    @asynccontextmanager
    async def acquire(_pool, _tenant):
        yield conn

    monkeypatch.setattr(campaign_module, "acquire_with_tenant", acquire)
    service = InboundCampaignService(object())
    service._claim_operation = AsyncMock(return_value=None)

    with pytest.raises(InboundNotFoundError):
        await service.create_reassignment_request(
            assignment_id=conn.assignment_id,
            target_tenant_id=conn.target_tenant,
            target_campaign_id=conn.target_campaign,
            expected_version=3,
            reason="Customer ownership transfer",
            actor_id=ACTOR,
            actor_role="platform_admin",
            idempotency_key="request-key-no-config",
        )

    assert not any(
        query.lstrip().upper().startswith(("INSERT", "UPDATE")) for query, _ in conn.queries
    )
    assert not any(
        secret_field in query
        for query, _ in conn.queries
        for secret_field in (
            "greeting",
            "recording_policy",
            "transfer_policy",
            "qualification_config",
        )
    )


class _ReassignmentConn:
    def __init__(self):
        self.source_tenant = TENANT
        self.target_tenant = "44444444-4444-4444-4444-444444444444"
        self.request_id = "55555555-5555-5555-5555-555555555555"
        self.assignment_id = "66666666-6666-6666-6666-666666666666"
        self.target_campaign = "77777777-7777-7777-7777-777777777777"
        self.target_config = "88888888-8888-8888-8888-888888888888"
        self.source_phone = "99999999-9999-9999-9999-999999999999"
        self.target_phone = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        self.target_trunk = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        self.new_assignment = "dddddddd-dddd-dddd-dddd-dddddddddddd"
        self.requested_by = "cccccccc-cccc-cccc-cccc-cccccccccccc"
        self.executed = []
        self.queries = []
        self.target_campaign_direction = "inbound"
        self.target_campaign_status = "running"
        self.target_config_status = "paused"
        self.target_config_exists = True
        self.target_occupied = False
        self.request = {
            "id": self.request_id,
            "tenant_id": self.source_tenant,
            "source_tenant_id": self.source_tenant,
            "assignment_id": self.assignment_id,
            "approved_assignment_id": None,
            "target_tenant_id": self.target_tenant,
            "target_campaign_id": self.target_campaign,
            "target_config_id": self.target_config,
            "expected_assignment_version": 4,
            "status": "pending",
            "reason": "Customer ownership transfer",
            "decision_reason": None,
            "requested_by": self.requested_by,
            "approved_by": None,
            "requested_at": datetime.now(timezone.utc),
            "decided_at": None,
        }
        self.assignment = {
            "id": self.assignment_id,
            "tenant_id": self.source_tenant,
            "phone_number_id": self.source_phone,
            "campaign_id": CONFIG,
            "config_id": CONFIG,
            "canonical_did": "+15551234567",
            "status": "quarantined",
            "version": 4,
        }

    async def fetchrow(self, query, *args):
        self.queries.append((query, args))
        if "SELECT * FROM inbound_reassignment_requests" in query:
            return self.request
        if "SELECT * FROM inbound_did_assignments" in query:
            return self.assignment
        if "SELECT config_id FROM inbound_did_assignments" in query:
            return {"config_id": self.assignment["config_id"]}
        if "SELECT id FROM inbound_campaign_configs" in query:
            return {"id": self.assignment["config_id"]}
        if "FROM campaigns" in query:
            return {
                "id": self.target_campaign,
                "tenant_id": self.target_tenant,
                "direction": self.target_campaign_direction,
                "status": self.target_campaign_status,
            }
        if "FROM inbound_campaign_configs" in query:
            if not self.target_config_exists:
                return None
            return {
                "id": self.target_config,
                "tenant_id": self.target_tenant,
                "campaign_id": self.target_campaign,
                "status": self.target_config_status,
            }
        if "WHERE tenant_id=$1 AND e164=$2" in query:
            return {"id": self.target_phone, "status": "verified"}
        if "UPDATE inbound_did_assignments" in query:
            return {
                **self.assignment,
                "status": "archived",
                "version": 5,
            }
        if "INSERT INTO inbound_did_assignments" in query:
            return {
                "id": self.new_assignment,
                "tenant_id": self.target_tenant,
                "phone_number_id": self.target_phone,
                "campaign_id": self.target_campaign,
                "config_id": self.target_config,
                "sip_trunk_id": self.target_trunk,
                "canonical_did": "+15551234567",
                "status": "paused",
                "version": 1,
            }
        if "UPDATE inbound_reassignment_requests" in query:
            return {
                **self.request,
                "approved_assignment_id": args[3],
                "status": "approved",
                "approved_by": args[1],
                "decision_reason": args[2],
                "decided_at": datetime.now(timezone.utc),
            }
        raise AssertionError(query)

    async def fetchval(self, query, *_args):
        self.queries.append((query, _args))
        if "FROM inbound_did_assignments" in query:
            return self.target_occupied
        raise AssertionError(query)

    async def fetch(self, query, *_args):
        self.queries.append((query, _args))
        if "FROM tenant_sip_trunks" in query:
            return [
                {
                    "id": self.target_trunk,
                    "is_active": True,
                    "direction": "inbound",
                    "metadata": {},
                    "live_registration_status": "loaded",
                    "live_status_detail": "endpoint loaded",
                    "live_status_checked_at": datetime.now(timezone.utc),
                }
            ]
        raise AssertionError(query)

    async def execute(self, query, *args):
        self.queries.append((query, args))
        self.executed.append((query, args))
        return "UPDATE 1"


@pytest.mark.asyncio
async def test_four_eye_approval_moves_current_owner_but_preserves_source(monkeypatch):
    conn = _ReassignmentConn()

    @asynccontextmanager
    async def acquire(_pool, _tenant):
        yield conn

    monkeypatch.setattr(campaign_module, "acquire_with_tenant", acquire)
    service = InboundCampaignService(object())
    service._claim_operation = AsyncMock(return_value=None)
    service._audit = AsyncMock()
    service._store_operation = AsyncMock()
    result = await service.approve_reassignment(
        request_id=conn.request_id,
        reason="Ownership evidence reviewed",
        actor_id=ACTOR,
        actor_role="platform_admin",
        idempotency_key="approve-key-123",
    )
    assert result["status"] == "approved"
    assert result["source_tenant_id"] == conn.source_tenant
    assert result["target_tenant_id"] == conn.target_tenant
    assert result["approved_assignment_id"] == conn.new_assignment
    assert not any("SET tenant_id=" in query for query, _ in conn.executed)
    normalized_queries = [" ".join(query.split()) for query, _ in conn.queries]
    target_campaign_lock = next(
        index
        for index, query in enumerate(normalized_queries)
        if "FROM campaigns" in query and "FOR UPDATE" in query
    )
    target_config_lock = next(
        index
        for index, query in enumerate(normalized_queries)
        if "FROM inbound_campaign_configs" in query
        and "tenant_id=$2 AND campaign_id=$3" in query
        and "FOR UPDATE" in query
    )
    occupancy_check = next(
        index
        for index, query in enumerate(normalized_queries)
        if "SELECT EXISTS(" in query and "config_id=$1" in query
    )
    first_transfer_write = next(
        index
        for index, query in enumerate(normalized_queries)
        if query.upper().startswith(("INSERT", "UPDATE"))
    )
    assert target_campaign_lock < target_config_lock < occupancy_check < first_transfer_write
    # Historical source phone rows stay in place for call FKs and are revoked;
    # the assignment points at the target tenant's independently-owned row.
    assert any("status='revoked'" in query for query, _ in conn.executed)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("attribute", "value", "expected_code"),
    [
        ("target_campaign_direction", "outbound", "target_campaign_not_inbound"),
        ("target_campaign_status", "completed", "target_campaign_terminal"),
        ("target_config_exists", False, "target_config_changed"),
        ("target_config_status", "archived", "target_config_archived"),
        ("target_occupied", True, "target_campaign_occupied"),
    ],
)
async def test_four_eye_approval_fails_closed_when_target_changes(
    monkeypatch,
    attribute,
    value,
    expected_code,
):
    conn = _ReassignmentConn()
    setattr(conn, attribute, value)

    @asynccontextmanager
    async def acquire(_pool, _tenant):
        yield conn

    monkeypatch.setattr(campaign_module, "acquire_with_tenant", acquire)
    service = InboundCampaignService(object())
    service._claim_operation = AsyncMock(return_value=None)

    with pytest.raises(InboundConflictError) as exc_info:
        await service.approve_reassignment(
            request_id=conn.request_id,
            reason="Ownership evidence reviewed",
            actor_id=ACTOR,
            actor_role="platform_admin",
            idempotency_key=f"approve-{attribute}",
        )

    assert exc_info.value.code == expected_code
    assert not any(
        query.lstrip().upper().startswith(("INSERT", "UPDATE")) for query, _ in conn.queries
    )


@pytest.mark.asyncio
async def test_four_eye_approval_rejects_target_trunk_without_live_asterisk_proof(
    monkeypatch,
):
    conn = _ReassignmentConn()

    async def unready_trunks(query, *_args):
        if "FROM tenant_sip_trunks" in query:
            return [
                {
                    "id": conn.target_trunk,
                    "is_active": True,
                    "direction": "inbound",
                    "metadata": {},
                    "live_registration_status": "missing_config",
                    "live_status_detail": "endpoint was not loaded",
                    "live_status_checked_at": datetime.now(timezone.utc),
                }
            ]
        raise AssertionError(query)

    conn.fetch = unready_trunks

    @asynccontextmanager
    async def acquire(_pool, _tenant):
        yield conn

    monkeypatch.setattr(campaign_module, "acquire_with_tenant", acquire)
    service = InboundCampaignService(object())
    service._claim_operation = AsyncMock(return_value=None)

    with pytest.raises(InboundConflictError) as exc_info:
        await service.approve_reassignment(
            request_id=conn.request_id,
            reason="Ownership evidence reviewed",
            actor_id=ACTOR,
            actor_role="platform_admin",
            idempotency_key="approve-key-runtime-proof",
        )

    assert exc_info.value.code == "target_trunk_not_ready"


def test_reassignment_schema_preserves_source_and_links_new_target_assignment():
    migration = (
        Path(__file__).parents[2] / "Alembic" / "versions" / "0022_inbound_calling_foundation.py"
    ).read_text(encoding="utf-8")
    assert "source_tenant_id UUID NOT NULL" in migration
    assert "approved_assignment_id UUID" in migration
    assert "inbound_reassignment_approved_assignment_tenant_fk" in migration
    assert "inbound_reassignment_source_tenant_stable" in migration
