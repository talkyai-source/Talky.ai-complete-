"""Atomic inbound admission, replay, reservation, and finalization tests."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.domain.services.telephony.inbound_admission as admission_module
from app.domain.services.telephony.inbound_admission import (
    InboundAdmissionRequest,
    InboundAdmissionWatchdog,
    InboundAdmissionService,
    InboundFinalizationRequest,
    InboundFinalizationResult,
    _private_ani,
)
from app.domain.services.voice_tuning import get_voice_tuning_resolver


TENANT = "11111111-1111-1111-1111-111111111111"
CAMPAIGN = "22222222-2222-2222-2222-222222222222"
CONFIG = "33333333-3333-3333-3333-333333333333"
ASSIGNMENT = "44444444-4444-4444-4444-444444444444"
TRUNK = "55555555-5555-5555-5555-555555555555"
PHONE = "66666666-6666-6666-6666-666666666666"
CALL = "77777777-7777-7777-7777-777777777777"
LEASE = uuid.UUID("88888888-8888-8888-8888-888888888888")
RESERVATION = "99999999-9999-9999-9999-999999999999"
USAGE = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
AI_CONFIG = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


@asynccontextmanager
async def _acquire(conn):
    yield conn


def _route():
    return {
        "assignment_id": ASSIGNMENT,
        "tenant_id": TENANT,
        "phone_number_id": PHONE,
        "campaign_id": CAMPAIGN,
        "config_id": CONFIG,
        "sip_trunk_id": TRUNK,
        "canonical_did": "+15551234567",
        "route_version": 4,
        "assignment_status": "active",
        "config_version": 8,
        "config_status": "active",
        "inbound_name": "Support line",
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
        "config_checksum": "checksum-v8",
        "campaign_name": "Inbound support",
        "campaign_description": "Answer customer calls",
        "campaign_status": "active",
        "campaign_direction": "inbound",
        "system_prompt": "Listen first.",
        "voice_id": "voice-1",
        "tts_provider": "cartesia",
        "goal": "resolve",
        "script_config": {},
        "calling_config": {},
        "prompt_version_pin": None,
        "knowledge_mode": "none",
        "knowledge_model": None,
        "phone_status": "verified",
        "trunk_active": True,
        "trunk_direction": "inbound",
        "trunk_metadata": {"register": False},
        "trunk_live_registration_status": "loaded",
        "trunk_live_status_detail": None,
        "trunk_live_status_checked_at": datetime.now(timezone.utc),
        "tenant_status": "active",
        "subscription_status": "active",
        "minutes_allocated": 100,
        "tenant_inbound_enabled": True,
        "concurrency_policy_ready": True,
        "tenant_ai_config_id": AI_CONFIG,
        "tenant_ai_config_updated_at": "2026-08-26T00:00:00+00:00",
        "tenant_llm_provider": "groq",
        "tenant_llm_model": "llama",
        "tenant_llm_temperature": 0.6,
        "tenant_llm_max_tokens": 150,
        "tenant_stt_provider": "deepgram",
        "tenant_stt_model": "nova-3",
        "tenant_stt_engine": "deepgram_flux",
        "tenant_stt_language": "en",
        "tenant_tts_provider": "cartesia",
        "tenant_tts_model": "sonic",
        "tenant_tts_voice_id": "voice-1",
        "tenant_tts_sample_rate": 24000,
        "tenant_voice_tuning": {},
        "tenant_pipeline_mode": "realtime",
        "tenant_realtime_model": "gpt-realtime-2",
        "tenant_realtime_voice": "marin",
        "tenant_realtime_settings": {"turn_detection": {"type": "semantic_vad"}},
    }


def test_private_and_invalid_ani_never_create_identity():
    assert _private_ani(None) == (None, True)
    assert _private_ani("anonymous") == (None, True)
    assert _private_ani("restricted") == (None, True)
    assert _private_ani("not-a-number") == (None, True)
    assert _private_ani("+1 (555) 000-1000") == ("+15550001000", False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("admission_request", "reason"),
    [
        (
            InboundAdmissionRequest(
                provider="bad provider!", provider_call_id="call-1", called_did="+15551234567"
            ),
            "invalid_provider",
        ),
        (
            InboundAdmissionRequest(
                provider="asterisk", provider_call_id="", called_did="+15551234567"
            ),
            "invalid_provider_call_id",
        ),
        (
            InboundAdmissionRequest(
                provider="asterisk", provider_call_id="call-1", called_did="unknown"
            ),
            "invalid_did",
        ),
        (
            InboundAdmissionRequest(
                provider="asterisk",
                provider_call_id="call-1",
                called_did="+15551234567",
                reservation_seconds="not-a-number",  # type: ignore[arg-type]
            ),
            "invalid_reservation",
        ),
    ],
)
async def test_invalid_admission_input_fails_before_database(admission_request, reason):
    decision = await InboundAdmissionService(None).admit(admission_request)
    assert decision.allowed is False
    assert decision.reason == reason


class _AllowedConn:
    def __init__(
        self,
        *,
        allocated_minutes=100,
        used_seconds=0,
        reserved_seconds=0,
        route_overrides=None,
        settlement_enabled=True,
        transfer_enabled=True,
        knowledge_nodes=None,
    ):
        self.allocated_minutes = allocated_minutes
        self.used_seconds = used_seconds
        self.reserved_seconds = reserved_seconds
        self.route_overrides = route_overrides or {}
        self.settlement_enabled = settlement_enabled
        self.transfer_enabled = transfer_enabled
        self.knowledge_nodes = list(knowledge_nodes or [])
        self.call_insert_args = None
        self.executed = []
        self.fetchval_queries = []

    async def fetchrow(self, query, *args):
        if "replay_state_live" in query:
            return None
        if "FROM platform_runtime_controls" in query:
            return {
                "inbound_enabled": True,
                "inbound_recording_enabled": True,
                "inbound_transfer_enabled": self.transfer_enabled,
                "inbound_settlement_enabled": self.settlement_enabled,
                "inbound_controls_version": 2,
            }
        if "INSERT INTO calls" in query:
            self.call_insert_args = args
            return {"id": args[0]}
        if "SELECT minutes_allocated FROM tenants" in query:
            return {"minutes_allocated": self.allocated_minutes}
        if "INSERT INTO inbound_usage_transactions" in query:
            return {"id": RESERVATION}
        raise AssertionError(query)

    async def fetch(self, query, *_args):
        if "FROM inbound_did_assignments" in query:
            route = _route()
            route["minutes_allocated"] = self.allocated_minutes
            route.update(self.route_overrides)
            return [route]
        if "FROM campaign_knowledge_nodes" in query:
            return [dict(node) for node in self.knowledge_nodes]
        raise AssertionError(query)

    async def fetchval(self, query, *_args):
        self.fetchval_queries.append(query)
        if "SUM(" in query and "FROM calls c" in query:
            return self.used_seconds + self.reserved_seconds
        raise AssertionError(query)

    async def execute(self, query, *args):
        self.executed.append((query, args))
        return "UPDATE 1"


class _Limiter:
    def __init__(self):
        self.released = []

    async def acquire_lease(self, *_args, **_kwargs):
        return SimpleNamespace(accepted=True, lease_id=LEASE, reason="accepted")

    async def release_lease(self, *_args, **kwargs):
        self.released.append(kwargs)
        return True


@pytest.mark.asyncio
async def test_admission_persists_anonymous_call_before_reservation(monkeypatch):
    conn = _AllowedConn()
    monkeypatch.setattr(admission_module, "acquire_with_tenant", lambda *_args: _acquire(conn))
    decision = await InboundAdmissionService(object(), _Limiter()).admit(
        InboundAdmissionRequest(
            provider="ASTERISK",
            provider_call_id="pbx-call-123",
            provider_event_id="stasis-1",
            called_did="sip:+1 (555) 123-4567@carrier.example",
            caller_ani="anonymous",
            reservation_seconds=90,
        )
    )
    assert decision.allowed is True
    assert decision.reason == "allowed"
    assert decision.tenant_id == TENANT
    assert decision.campaign_id == CAMPAIGN
    assert decision.config_id == CONFIG
    assert decision.assignment_id == ASSIGNMENT
    assert decision.usage_reservation_id == RESERVATION
    assert decision.concurrency_lease_id == str(LEASE)
    assert decision.config_snapshot["campaign"]["id"] == CAMPAIGN
    assert decision.config_snapshot["inbound_config"]["version"] == 8
    assert decision.config_snapshot["route"]["called_did"] == "+15551234567"
    assert decision.config_snapshot["schedule_decision"]["is_after_hours"] is False
    assert decision.config_snapshot["schedule_decision"]["selected_action"] == "agent"
    assert decision.config_snapshot["tenant_ai_config"] == {
        "id": AI_CONFIG,
        "updated_at": "2026-08-26T00:00:00+00:00",
        "llm_provider": "groq",
        "llm_model": "llama",
        "llm_temperature": 0.6,
        "llm_max_tokens": 150,
        "stt_provider": "deepgram",
        "stt_model": "nova-3",
        "stt_engine": "deepgram_flux",
        "stt_language": "en",
        "tts_provider": "cartesia",
        "tts_model": "sonic",
        "tts_voice_id": "voice-1",
        "tts_sample_rate": 24000,
        "voice_tuning": asdict(get_voice_tuning_resolver().for_tenant(TENANT)),
        "pipeline_mode": "realtime",
        "realtime_model": "gpt-realtime-2",
        "realtime_voice": "marin",
        "realtime_settings": {"turn_detection": {"type": "semantic_vad"}},
    }

    args = conn.call_insert_args
    assert args is not None
    assert args[3] == "anonymous"  # calls.phone_number compatibility projection
    assert args[16] is None  # caller_ani is not invented
    assert args[17] is True  # caller_ani_private
    insert_sql = next(query for query, _ in conn.executed if "UPDATE calls" in query)
    assert "billing_status='reserved'" in insert_sql
    usage_sql = next(query for query in conn.fetchval_queries if "FROM calls c" in query)
    assert "COALESCE(c.duration_seconds,0)" in usage_sql
    assert "c.direction='inbound'" in usage_sql
    assert "c.billing_status='reserved'" in usage_sql
    assert "c.billing_status='reversed'" in usage_sql
    assert "COALESCE(c.direction" not in usage_sql  # no outbound-only filter


@pytest.mark.asyncio
async def test_admission_pins_knowledge_content_before_answer(monkeypatch):
    node = {
        "id": uuid.uuid4(),
        "depth": 0,
        "path": "1",
        "position": 0,
        "heading": "Warranty",
        "content": "The warranty lasts five years.",
        "summary": "Five-year warranty",
        "voice_answer": "It includes a five-year warranty.",
        "keywords": ["warranty"],
        "example_questions": ["How long is the warranty?"],
        "search_text": "warranty lasts five years",
        "priority": 5,
        "updated_at": "2026-08-26T00:00:00+00:00",
    }
    conn = _AllowedConn(
        route_overrides={"knowledge_mode": "retrieve"},
        knowledge_nodes=[node],
    )
    monkeypatch.setenv("CAMPAIGN_KNOWLEDGE_ENABLED", "true")
    monkeypatch.setattr(admission_module, "acquire_with_tenant", lambda *_args: _acquire(conn))

    decision = await InboundAdmissionService(object(), _Limiter()).admit(
        InboundAdmissionRequest(
            provider="asterisk",
            provider_call_id="pinned-kb-call",
            called_did="+15551234567",
            reservation_seconds=60,
        )
    )

    assert decision.allowed is True
    pinned = decision.config_snapshot["knowledge_snapshot"]
    assert pinned["enabled"] is True
    assert pinned["mode"] == "retrieve"
    assert pinned["node_count"] == 1
    assert pinned["nodes"][0]["content"] == "The warranty lasts five years."
    assert len(pinned["checksum"]) == 64

    # A later campaign edit cannot alter the already-admitted snapshot.
    conn.knowledge_nodes[0]["content"] = "The warranty now lasts one year."
    from app.services.scripts.knowledge.retrieval import retrieve_pinned_knowledge

    hits = retrieve_pinned_knowledge(pinned["nodes"], "warrantee length", k=1)
    assert hits[0]["content"] == "The warranty lasts five years."


@pytest.mark.asyncio
async def test_zero_monthly_usage_allows_next_admission(monkeypatch):
    conn = _AllowedConn(allocated_minutes=1, used_seconds=0, reserved_seconds=0)
    monkeypatch.setattr(admission_module, "acquire_with_tenant", lambda *_args: _acquire(conn))
    decision = await InboundAdmissionService(object(), _Limiter()).admit(
        InboundAdmissionRequest(
            provider="asterisk",
            provider_call_id="pbx-after-reversal",
            called_did="+15551234567",
            reservation_seconds=60,
        )
    )
    assert decision.allowed is True


@pytest.mark.asyncio
async def test_outbound_usage_exhaustion_denies_inbound_reservation(monkeypatch):
    conn = _AllowedConn(allocated_minutes=1, used_seconds=59, reserved_seconds=0)
    limiter = _Limiter()
    monkeypatch.setattr(admission_module, "acquire_with_tenant", lambda *_args: _acquire(conn))
    decision = await InboundAdmissionService(object(), limiter).admit(
        InboundAdmissionRequest(
            provider="asterisk",
            provider_call_id="quota-call",
            called_did="+15551234567",
            reservation_seconds=60,
        )
    )
    assert decision.allowed is False
    assert decision.reason == "insufficient_minutes"
    assert decision.concurrency_lease_id == str(LEASE)
    assert limiter.released == []
    denial_sql, denial_args = next(
        (query, args)
        for query, args in conn.executed
        if "admission_reason='insufficient_minutes'" in query
    )
    assert "status='termination_pending'" in denial_sql
    assert "ended_at=NULL" in denial_sql
    assert "concurrency_lease_id=$2" in denial_sql
    assert denial_args == (decision.call_id, LEASE)


@pytest.mark.asyncio
async def test_concurrency_denial_remains_nonterminal_until_pbx_proof(monkeypatch):
    class DenyingLimiter(_Limiter):
        async def acquire_lease(self, *_args, **_kwargs):
            return SimpleNamespace(
                accepted=False,
                lease_id=None,
                reason="tenant_concurrency_limit",
            )

    conn = _AllowedConn()
    monkeypatch.setattr(
        admission_module,
        "acquire_with_tenant",
        lambda *_args: _acquire(conn),
    )
    decision = await InboundAdmissionService(object(), DenyingLimiter()).admit(
        InboundAdmissionRequest(
            provider="asterisk",
            provider_call_id="concurrency-denied-live-leg",
            called_did="+15551234567",
            reservation_seconds=60,
        )
    )

    assert decision.allowed is False
    assert decision.reason == "tenant_concurrency_limit"
    denial_sql, _ = next(
        (query, args) for query, args in conn.executed if "admission_status='denied'" in query
    )
    assert "status='termination_pending'" in denial_sql
    assert "processing_status='pending'" in denial_sql
    assert "ended_at=NULL" in denial_sql
    assert "status='failed'" not in denial_sql


@pytest.mark.asyncio
async def test_non_positive_allocation_preserves_unlimited_plan_contract(monkeypatch):
    conn = _AllowedConn(
        allocated_minutes=0,
        used_seconds=10_000_000,
        reserved_seconds=10_000_000,
    )
    monkeypatch.setattr(admission_module, "acquire_with_tenant", lambda *_args: _acquire(conn))
    decision = await InboundAdmissionService(object(), _Limiter()).admit(
        InboundAdmissionRequest(
            provider="asterisk",
            provider_call_id="unlimited-call",
            called_did="+15551234567",
            reservation_seconds=3600,
        )
    )
    assert decision.allowed is True


@pytest.mark.asyncio
async def test_admission_reserves_campaign_max_and_shortens_to_remaining_quota(monkeypatch):
    conn = _AllowedConn(
        allocated_minutes=20,
        used_seconds=600,
        route_overrides={
            "transfer_policy": {"max_call_duration_seconds": 1_800},
        },
    )
    monkeypatch.setattr(admission_module, "acquire_with_tenant", lambda *_args: _acquire(conn))
    decision = await InboundAdmissionService(object(), _Limiter()).admit(
        InboundAdmissionRequest(
            provider="asterisk",
            provider_call_id="quota-backed-duration",
            called_did="+15551234567",
            reservation_seconds=14_400,
        )
    )

    assert decision.allowed is True
    assert decision.config_snapshot["route"]["max_call_duration_seconds"] == 600
    assert decision.config_snapshot["route"]["reservation_seconds"] == 600


@pytest.mark.asyncio
async def test_invalid_campaign_max_duration_fails_before_answer(monkeypatch):
    conn = _AllowedConn(
        route_overrides={
            "transfer_policy": {"max_call_duration_seconds": "unbounded"},
        },
    )
    monkeypatch.setattr(admission_module, "acquire_with_tenant", lambda *_args: _acquire(conn))
    decision = await InboundAdmissionService(object(), _Limiter()).admit(
        InboundAdmissionRequest(
            provider="asterisk",
            provider_call_id="invalid-duration",
            called_did="+15551234567",
            reservation_seconds=14_400,
        )
    )

    assert decision.allowed is False
    assert decision.reason == "invalid_max_call_duration"


@pytest.mark.asyncio
async def test_settlement_switch_does_not_block_preanswer_admission(monkeypatch):
    conn = _AllowedConn(settlement_enabled=False)
    monkeypatch.setattr(admission_module, "acquire_with_tenant", lambda *_args: _acquire(conn))
    decision = await InboundAdmissionService(object(), _Limiter()).admit(
        InboundAdmissionRequest(
            provider="asterisk",
            provider_call_id="settlement-held-later",
            called_did="+15551234567",
        )
    )
    assert decision.allowed is True
    assert decision.config_snapshot["controls"]["settlement_enabled"] is False


@pytest.mark.asyncio
async def test_missing_tenant_ai_config_fails_closed_before_answer(monkeypatch):
    conn = _AllowedConn(route_overrides={"tenant_ai_config_id": None})
    monkeypatch.setattr(admission_module, "acquire_with_tenant", lambda *_args: _acquire(conn))
    decision = await InboundAdmissionService(object(), _Limiter()).admit(
        InboundAdmissionRequest(
            provider="asterisk",
            provider_call_id="missing-ai-config",
            called_did="+15551234567",
        )
    )
    assert decision.allowed is False
    assert decision.reason == "ai_config_missing"
    assert conn.call_insert_args is None


@pytest.mark.asyncio
async def test_hot_admission_rejects_stale_or_unhealthy_trunk_evidence(monkeypatch):
    for overrides in (
        {
            "trunk_live_status_checked_at": datetime.now(timezone.utc),
            "trunk_live_registration_status": "missing_config",
        },
        {
            "trunk_live_status_checked_at": datetime(2020, 1, 1, tzinfo=timezone.utc),
            "trunk_live_registration_status": "loaded",
        },
    ):
        conn = _AllowedConn(route_overrides=overrides)
        monkeypatch.setattr(
            admission_module,
            "acquire_with_tenant",
            lambda *_args, conn=conn: _acquire(conn),
        )
        decision = await InboundAdmissionService(object(), _Limiter()).admit(
            InboundAdmissionRequest(
                provider="asterisk",
                provider_call_id=f"bad-trunk-{overrides['trunk_live_registration_status']}",
                called_did="+15551234567",
            )
        )
        assert decision.allowed is False
        assert decision.reason == "trunk_not_ready"
        assert conn.call_insert_args is None


@pytest.mark.asyncio
async def test_hot_admission_requires_bidirectional_trunk_only_for_transfer(monkeypatch):
    policy = {
        "enabled": True,
        "destinations": ["+15559876543"],
    }
    inbound_only = _AllowedConn(
        route_overrides={"transfer_policy": policy},
    )
    monkeypatch.setattr(
        admission_module,
        "acquire_with_tenant",
        lambda *_args: _acquire(inbound_only),
    )
    denied = await InboundAdmissionService(object(), _Limiter()).admit(
        InboundAdmissionRequest(
            provider="asterisk",
            provider_call_id="transfer-inbound-only-trunk",
            called_did="+15551234567",
        )
    )
    assert denied.allowed is False
    assert denied.reason == "transfer_trunk_not_bidirectional"
    assert inbound_only.call_insert_args is None

    bidirectional = _AllowedConn(
        route_overrides={
            "trunk_direction": "both",
            "transfer_policy": policy,
        },
    )
    monkeypatch.setattr(
        admission_module,
        "acquire_with_tenant",
        lambda *_args: _acquire(bidirectional),
    )
    allowed = await InboundAdmissionService(object(), _Limiter()).admit(
        InboundAdmissionRequest(
            provider="asterisk",
            provider_call_id="transfer-bidirectional-trunk",
            called_did="+15551234567",
        )
    )
    assert allowed.allowed is True


@pytest.mark.asyncio
async def test_hot_admission_requires_bidirectional_trunk_for_selected_transfer(monkeypatch):
    closed_week = [
        {"day": day, "enabled": False, "start": "09:00", "end": "17:00"} for day in range(7)
    ]
    conn = _AllowedConn(
        route_overrides={
            "business_hours": {"weekly_schedule": closed_week},
            "after_hours_action": "transfer",
            "transfer_number": "+15559876543",
            "transfer_policy": {"enabled": False},
            "trunk_direction": "inbound",
        }
    )
    monkeypatch.setattr(
        admission_module,
        "acquire_with_tenant",
        lambda *_args: _acquire(conn),
    )

    decision = await InboundAdmissionService(object(), _Limiter()).admit(
        InboundAdmissionRequest(
            provider="asterisk",
            provider_call_id="selected-transfer-inbound-only-trunk",
            called_did="+15551234567",
        )
    )

    assert decision.allowed is False
    assert decision.reason == "transfer_trunk_not_bidirectional"
    assert conn.call_insert_args is None


@pytest.mark.asyncio
async def test_after_hours_voicemail_action_is_pinned_before_answer(monkeypatch):
    closed_week = [
        {"day": day, "enabled": False, "start": "09:00", "end": "17:00"} for day in range(7)
    ]
    conn = _AllowedConn(
        route_overrides={
            "business_hours": {
                "weekly_schedule": closed_week,
                "after_hours_message": (
                    "We are closed. Please leave your name, number, and message."
                ),
            },
            "after_hours_action": "voicemail",
        }
    )
    monkeypatch.setattr(admission_module, "acquire_with_tenant", lambda *_args: _acquire(conn))
    decision = await InboundAdmissionService(object(), _Limiter()).admit(
        InboundAdmissionRequest(
            provider="asterisk",
            provider_call_id="after-hours-call",
            called_did="+15551234567",
        )
    )
    assert decision.allowed is True
    assert decision.config_snapshot["inbound_config"]["is_after_hours"] is True
    assert decision.config_snapshot["inbound_config"]["selected_action"] == "voicemail"
    assert decision.config_snapshot["schedule_decision"]["selected_action"] == "voicemail"
    assert decision.config_snapshot["inbound_config"]["after_hours_message"].startswith(
        "We are closed."
    )


@pytest.mark.asyncio
async def test_after_hours_voicemail_without_pinned_intake_message_is_denied(monkeypatch):
    closed_week = [
        {"day": day, "enabled": False, "start": "09:00", "end": "17:00"} for day in range(7)
    ]
    conn = _AllowedConn(
        route_overrides={
            "business_hours": {
                "weekly_schedule": closed_week,
                "after_hours_message": "   ",
            },
            "after_hours_action": "voicemail",
        }
    )
    monkeypatch.setattr(
        admission_module,
        "acquire_with_tenant",
        lambda *_args: _acquire(conn),
    )
    decision = await InboundAdmissionService(object(), _Limiter()).admit(
        InboundAdmissionRequest(
            provider="asterisk",
            provider_call_id="after-hours-no-intake-message",
            called_did="+15551234567",
        )
    )
    assert decision.allowed is False
    assert decision.reason == "voicemail_intake_message_required"
    assert conn.call_insert_args is None


@pytest.mark.asyncio
async def test_after_hours_hangup_action_is_pinned_before_answer(monkeypatch):
    closed_week = [
        {"day": day, "enabled": False, "start": "09:00", "end": "17:00"} for day in range(7)
    ]
    conn = _AllowedConn(route_overrides={"business_hours": {"weekly_schedule": closed_week}})
    monkeypatch.setattr(admission_module, "acquire_with_tenant", lambda *_args: _acquire(conn))
    decision = await InboundAdmissionService(object(), _Limiter()).admit(
        InboundAdmissionRequest(
            provider="asterisk",
            provider_call_id="after-hours-hangup",
            called_did="+15551234567",
        )
    )
    assert decision.allowed is True
    assert decision.config_snapshot["inbound_config"]["selected_action"] == "hangup"


@pytest.mark.asyncio
async def test_after_hours_transfer_stays_closed_until_runtime_release_gate(monkeypatch):
    closed_week = [
        {"day": day, "enabled": False, "start": "09:00", "end": "17:00"} for day in range(7)
    ]
    route = {
        "business_hours": {"weekly_schedule": closed_week},
        "after_hours_action": "transfer",
        "transfer_number": "+1 (555) 987-6543",
        "transfer_policy": {"enabled": True, "destinations": ["+15559876543"]},
        "trunk_direction": "both",
    }

    disabled_conn = _AllowedConn(
        route_overrides=route,
        transfer_enabled=False,
    )
    monkeypatch.setattr(
        admission_module,
        "acquire_with_tenant",
        lambda *_args: _acquire(disabled_conn),
    )
    denied = await InboundAdmissionService(object(), _Limiter()).admit(
        InboundAdmissionRequest(
            provider="asterisk",
            provider_call_id="after-hours-transfer-disabled",
            called_did="+15551234567",
        )
    )
    assert denied.allowed is False
    assert denied.reason == "transfer_runtime_unavailable"
    assert disabled_conn.call_insert_args is None

    enabled_conn = _AllowedConn(route_overrides=route)
    monkeypatch.setattr(
        admission_module,
        "acquire_with_tenant",
        lambda *_args: _acquire(enabled_conn),
    )
    denied_with_switch = await InboundAdmissionService(object(), _Limiter()).admit(
        InboundAdmissionRequest(
            provider="asterisk",
            provider_call_id="after-hours-transfer-enabled",
            called_did="+15551234567",
        )
    )
    assert denied_with_switch.allowed is False
    assert denied_with_switch.reason == "transfer_runtime_unavailable"
    assert enabled_conn.call_insert_args is None


@pytest.mark.asyncio
async def test_after_hours_transfer_rejects_out_of_scope_staging_campaign(monkeypatch):
    closed_week = [
        {"day": day, "enabled": False, "start": "09:00", "end": "17:00"}
        for day in range(7)
    ]
    conn = _AllowedConn(
        route_overrides={
            "business_hours": {"weekly_schedule": closed_week},
            "after_hours_action": "transfer",
            "transfer_number": "+15559876543",
            "transfer_policy": {
                "enabled": True,
                "destinations": ["+15559876543"],
            },
            "trunk_direction": "both",
        },
        transfer_enabled=True,
    )
    monkeypatch.setattr(admission_module, "acquire_with_tenant", lambda *_args: _acquire(conn))
    monkeypatch.setattr(admission_module, "inbound_transfer_runtime_available", lambda: True)
    monkeypatch.setattr(
        admission_module,
        "inbound_transfer_scope_available",
        lambda **_kwargs: False,
    )

    decision = await InboundAdmissionService(object(), _Limiter()).admit(
        InboundAdmissionRequest(
            provider="asterisk",
            provider_call_id="after-hours-transfer-outside-proof-scope",
            called_did="+15551234567",
        )
    )

    assert decision.allowed is False
    assert decision.reason == "transfer_staging_scope_mismatch"
    assert conn.call_insert_args is None


@pytest.mark.asyncio
async def test_after_hours_transfer_destination_must_be_explicitly_approved(monkeypatch):
    closed_week = [
        {"day": day, "enabled": False, "start": "09:00", "end": "17:00"} for day in range(7)
    ]
    conn = _AllowedConn(
        route_overrides={
            "business_hours": {"weekly_schedule": closed_week},
            "after_hours_action": "transfer",
            "transfer_number": "+15559876543",
            "transfer_policy": {
                "enabled": True,
                "destinations": ["+15551234567"],
            },
            "trunk_direction": "both",
        }
    )
    monkeypatch.setattr(
        admission_module,
        "acquire_with_tenant",
        lambda *_args: _acquire(conn),
    )

    decision = await InboundAdmissionService(object(), _Limiter()).admit(
        InboundAdmissionRequest(
            provider="asterisk",
            provider_call_id="after-hours-unapproved-destination",
            called_did="+15551234567",
        )
    )

    assert decision.allowed is False
    assert decision.reason == "transfer_destination_not_approved"
    assert conn.call_insert_args is None


@pytest.mark.asyncio
async def test_invalid_business_schedule_fails_closed_before_call_insert(monkeypatch):
    conn = _AllowedConn(
        route_overrides={
            "business_hours": {
                "weekly_schedule": [{"day": 0, "enabled": True, "start": "25:00", "end": "17:00"}]
            }
        }
    )
    monkeypatch.setattr(admission_module, "acquire_with_tenant", lambda *_args: _acquire(conn))
    decision = await InboundAdmissionService(object(), _Limiter()).admit(
        InboundAdmissionRequest(
            provider="asterisk",
            provider_call_id="bad-schedule-call",
            called_did="+15551234567",
        )
    )
    assert decision.allowed is False
    assert decision.reason == "invalid_schedule"
    assert conn.call_insert_args is None


@pytest.mark.asyncio
async def test_admission_rejects_unimplemented_dtmf_opt_out_promise(monkeypatch):
    conn = _AllowedConn(
        route_overrides={
            "recording_enabled": True,
            "consent_message": ("This call is recorded for quality. Press 9 to opt out."),
        }
    )
    monkeypatch.setattr(
        admission_module,
        "acquire_with_tenant",
        lambda *_args: _acquire(conn),
    )
    decision = await InboundAdmissionService(object(), _Limiter()).admit(
        InboundAdmissionRequest(
            provider="asterisk",
            provider_call_id="unsupported-dtmf-consent",
            called_did="+15551234567",
        )
    )
    assert decision.allowed is False
    assert decision.reason == "unsupported_recording_dtmf_opt_out"
    assert conn.call_insert_args is None


class _ReplayConn:
    def __init__(self, *, live=True, **overrides):
        self.live = live
        self.overrides = overrides

    async def fetchrow(self, query, *_args):
        if "replay_state_live" not in query:
            raise AssertionError(query)
        row = {
            "id": CALL,
            "tenant_id": TENANT,
            "campaign_id": CAMPAIGN,
            "assignment_id": ASSIGNMENT,
            "talklee_call_id": "IN123456789012345678",
            "admission_status": "allowed",
            "admission_reason": "allowed",
            "route_version": 4,
            "config_version": 8,
            "concurrency_lease_id": LEASE,
            "processing_status": "active",
            "billing_status": "reserved",
            "reserved_seconds": 90,
            "replay_state_live": self.live,
            "route_snapshot": {
                "route": {
                    "config_id": CONFIG,
                    "sip_trunk_id": TRUNK,
                    "usage_reservation_id": RESERVATION,
                },
                "inbound_config": {"opening_mode": "caller_first"},
            },
        }
        row.update(self.overrides)
        return row


@pytest.mark.asyncio
async def test_provider_identity_is_idempotently_replayed(monkeypatch):
    monkeypatch.setattr(
        admission_module, "acquire_with_tenant", lambda *_args: _acquire(_ReplayConn())
    )
    decision = await InboundAdmissionService(object()).admit(
        InboundAdmissionRequest(
            provider="asterisk",
            provider_call_id="pbx-call-123",
            called_did="+15551234567",
        )
    )
    assert decision.allowed is True
    assert decision.reason == "duplicate_replay"
    assert decision.is_replay is True
    assert decision.call_id == CALL
    assert decision.usage_reservation_id == RESERVATION


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"replay_state_live": False, "processing_status": "completed"},
        {"replay_state_live": False, "billing_status": "finalized"},
        {"replay_state_live": False, "concurrency_lease_id": None},
        {"replay_state_live": False, "reserved_seconds": 0},
    ],
)
async def test_terminal_or_unproven_provider_replay_is_rejected(monkeypatch, overrides):
    monkeypatch.setattr(
        admission_module,
        "acquire_with_tenant",
        lambda *_args: _acquire(_ReplayConn(**overrides)),
    )
    decision = await InboundAdmissionService(object()).admit(
        InboundAdmissionRequest(
            provider="asterisk",
            provider_call_id="pbx-call-123",
            called_did="+15551234567",
        )
    )
    assert decision.allowed is False
    assert decision.reason == "stale_provider_replay"
    assert decision.call_id == CALL


class _FinalizationConn:
    def __init__(
        self,
        *,
        billing_status="reserved",
        settlement_enabled=True,
        outcome=None,
        status="in_call",
        unsettled_transfer=False,
        duration_seconds=63,
        cost=Decimal("0.125"),
        billing_hold_reason=None,
    ):
        self.billing_status = billing_status
        self.settlement_enabled = settlement_enabled
        self.outcome = outcome
        self.status = status
        self.unsettled_transfer = unsettled_transfer
        self.duration_seconds = duration_seconds
        self.cost = cost
        self.billing_hold_reason = billing_hold_reason
        self.insert_args = None
        self.update_args = None
        self.update_query = None

    async def fetchrow(self, query, *args):
        if "SELECT * FROM calls" in query:
            return {
                "id": CALL,
                "tenant_id": TENANT,
                "billing_status": self.billing_status,
                "status": self.status,
                "outcome": self.outcome,
                "duration_seconds": self.duration_seconds,
                "cost": self.cost,
                "billing_hold_reason": self.billing_hold_reason,
                "reserved_seconds": 90,
                "concurrency_lease_id": LEASE,
            }
        if "FROM platform_runtime_controls" in query:
            return {"inbound_settlement_enabled": self.settlement_enabled}
        if "transaction_type IN" in query:
            return {"id": USAGE, "transaction_type": "finalize"}
        if "transaction_type='reserve'" in query:
            return {"id": RESERVATION, "quantity_seconds": 90}
        if "INSERT INTO inbound_usage_transactions" in query:
            self.insert_args = args
            return {"id": USAGE}
        if "SELECT id FROM inbound_usage_transactions" in query:
            return {"id": USAGE}
        raise AssertionError(query)

    async def execute(self, query, *args):
        if "UPDATE calls" in query:
            self.update_query = query
            self.update_args = args
        return "UPDATE 1"

    async def fetchval(self, query, *_args):
        if "FROM call_legs" in query:
            return self.unsettled_transfer
        raise AssertionError(query)


@pytest.mark.asyncio
async def test_finalization_appends_delta_releases_lease_and_replays(monkeypatch):
    conn = _FinalizationConn()
    limiter = _Limiter()
    monkeypatch.setattr(admission_module, "acquire_with_tenant", lambda *_args: _acquire(conn))
    result = await InboundAdmissionService(object(), limiter).finalize(
        InboundFinalizationRequest(
            call_id=CALL,
            provider="asterisk",
            provider_call_id="pbx-call-123",
            terminal_status="completed",
            duration_seconds=63,
            cost=0.125,
            outcome="answered",
        )
    )
    assert result.finalized is True
    assert result.billing_status == "finalized"
    assert result.usage_transaction_id == USAGE
    assert conn.insert_args[3] == -27  # actual 63 - reserved 90
    assert conn.insert_args[4] == Decimal("0.125")
    assert conn.update_args[3] == Decimal("0.125")
    assert conn.update_args[4] == "answered"
    assert "billing_hold_reason=NULL" in conn.update_query
    assert "billing_hold_reason=NULL" in conn.update_query
    assert limiter.released[0]["lease_id"] == LEASE

    replay_conn = _FinalizationConn(billing_status="finalized")
    monkeypatch.setattr(
        admission_module, "acquire_with_tenant", lambda *_args: _acquire(replay_conn)
    )
    replay = await InboundAdmissionService(object(), limiter).finalize(
        InboundFinalizationRequest(
            call_id=CALL,
            provider="asterisk",
            provider_call_id="pbx-call-123",
            terminal_status="completed",
            duration_seconds=63,
        )
    )
    assert replay.is_replay is True
    assert replay.reason == "duplicate_replay"
    assert replay.usage_transaction_id == USAGE


@pytest.mark.asyncio
async def test_parent_finalization_rejects_nonterminal_transfer_billing(monkeypatch):
    conn = _FinalizationConn(unsettled_transfer=True)
    limiter = _Limiter()
    monkeypatch.setattr(
        admission_module,
        "acquire_with_tenant",
        lambda *_args: _acquire(conn),
    )

    with pytest.raises(RuntimeError, match="inbound_transfer_usage_nonterminal"):
        await InboundAdmissionService(object(), limiter).finalize(
            InboundFinalizationRequest(
                call_id=CALL,
                provider="asterisk",
                provider_call_id="pbx-call-123",
                terminal_status="completed",
                duration_seconds=63,
            )
        )

    assert conn.insert_args is None
    assert conn.update_args is None
    assert limiter.released == []


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["canceled", "busy", "no_answer"])
async def test_finalization_accepts_every_canonical_terminal_status(
    monkeypatch,
    terminal_status,
):
    conn = _FinalizationConn()
    monkeypatch.setattr(
        admission_module,
        "acquire_with_tenant",
        lambda *_args: _acquire(conn),
    )

    result = await InboundAdmissionService(object(), _Limiter()).finalize(
        InboundFinalizationRequest(
            call_id=CALL,
            provider="asterisk",
            provider_call_id="pbx-call-123",
            terminal_status=terminal_status,
            duration_seconds=0,
        )
    )

    assert result.finalized is True
    assert conn.update_args[1] == terminal_status
    assert conn.update_args[7] == "failed"


@pytest.mark.asyncio
async def test_deferred_settlement_preserves_endpoint_terminal_status(monkeypatch):
    """Endpoint ``ended`` remains first-terminal truth during recovery."""

    conn = _FinalizationConn(status="ended", billing_status="reserved")
    limiter = _Limiter()
    monkeypatch.setattr(
        admission_module,
        "acquire_with_tenant",
        lambda *_args: _acquire(conn),
    )

    result = await InboundAdmissionService(object(), limiter).finalize(
        InboundFinalizationRequest(
            call_id=CALL,
            provider="asterisk",
            provider_call_id="pbx-call-123",
            # Recovery knows the call answered and would otherwise overwrite
            # the endpoint's already-committed terminal value.
            terminal_status="completed",
            duration_seconds=63,
            outcome="answered",
            reason="process_restart_recovery",
        )
    )

    assert result.finalized is True
    assert result.billing_status == "finalized"
    assert conn.update_args[1] == "ended"
    assert conn.update_args[7] == "completed"
    assert conn.update_args[8] == "finalized"
    assert limiter.released[0]["lease_id"] == LEASE


@pytest.mark.asyncio
async def test_finalization_replay_repairs_only_a_missing_outcome(monkeypatch):
    conn = _FinalizationConn(billing_status="finalized", outcome=None)
    monkeypatch.setattr(
        admission_module,
        "acquire_with_tenant",
        lambda *_args: _acquire(conn),
    )

    result = await InboundAdmissionService(object(), _Limiter()).finalize(
        InboundFinalizationRequest(
            call_id=CALL,
            provider="asterisk",
            provider_call_id="pbx-call-123",
            terminal_status="completed",
            duration_seconds=63,
            outcome="answered",
        )
    )

    assert result.is_replay is True
    assert conn.update_args == (CALL, "answered")


@pytest.mark.asyncio
async def test_finalization_rejects_unstable_outcome_identifiers():
    with pytest.raises(ValueError, match="outcome"):
        await InboundAdmissionService(None).finalize(
            InboundFinalizationRequest(
                call_id=CALL,
                provider="asterisk",
                provider_call_id="pbx-call-123",
                terminal_status="failed",
                duration_seconds=0,
                outcome="answered!",
            )
        )


@pytest.mark.asyncio
async def test_duration_over_reservation_is_held_not_silently_billed(monkeypatch):
    conn = _FinalizationConn()
    limiter = _Limiter()
    monkeypatch.setattr(admission_module, "acquire_with_tenant", lambda *_args: _acquire(conn))
    result = await InboundAdmissionService(object(), limiter).finalize(
        InboundFinalizationRequest(
            call_id=CALL,
            provider="asterisk",
            provider_call_id="pbx-call-123",
            terminal_status="completed",
            duration_seconds=91,
        )
    )

    assert result.finalized is False
    assert result.reason == "usage_exceeded_reservation"
    assert result.billing_status == "held"
    assert conn.insert_args is None
    assert limiter.released[0]["reason"] == "usage_exceeded_reservation"
    assert "billing_hold_reason='usage_exceeded_reservation'" in conn.update_query


@pytest.mark.asyncio
async def test_ambiguous_provider_answer_is_held_for_cdr_not_auto_billed(
    monkeypatch,
):
    conn = _FinalizationConn()
    limiter = _Limiter()
    monkeypatch.setattr(
        admission_module,
        "acquire_with_tenant",
        lambda *_args: _acquire(conn),
    )

    result = await InboundAdmissionService(object(), limiter).finalize(
        InboundFinalizationRequest(
            call_id=CALL,
            provider="asterisk",
            provider_call_id="pbx-call-123",
            terminal_status="completed",
            duration_seconds=37,
            outcome="answered",
            reason="process_restart_answer_ambiguous",
        )
    )

    assert result.finalized is False
    assert result.reason == "provider_answer_ambiguous"
    assert result.billing_status == "held"
    assert result.lease_released is True
    assert conn.insert_args is None
    assert conn.update_args[2] == 37
    assert "billing_hold_reason='provider_answer_ambiguous'" in conn.update_query
    assert limiter.released[0]["reason"] == "provider_answer_ambiguous"


@pytest.mark.asyncio
async def test_ambiguous_answer_hold_cannot_be_auto_finalized_by_replay(monkeypatch):
    conn = _FinalizationConn(
        billing_status="held",
        settlement_enabled=True,
        status="completed",
        duration_seconds=37,
        billing_hold_reason="provider_answer_ambiguous",
    )
    limiter = _Limiter()
    monkeypatch.setattr(
        admission_module,
        "acquire_with_tenant",
        lambda *_args: _acquire(conn),
    )

    result = await InboundAdmissionService(object(), limiter).finalize(
        InboundFinalizationRequest(
            call_id=CALL,
            provider="asterisk",
            provider_call_id="pbx-call-123",
            terminal_status="completed",
            duration_seconds=1,
            outcome="answered",
            reason="process_restart_recovery",
        )
    )

    assert result.finalized is False
    assert result.is_replay is True
    assert result.reason == "provider_answer_ambiguous"
    assert conn.insert_args is None
    assert conn.update_args is None
    assert limiter.released == []


@pytest.mark.asyncio
async def test_finalization_rejects_non_finite_or_negative_cost():
    service = InboundAdmissionService(None)
    for value in (-1, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="cost"):
            await service.finalize(
                InboundFinalizationRequest(
                    call_id=CALL,
                    provider="asterisk",
                    provider_call_id="pbx-call-123",
                    terminal_status="failed",
                    duration_seconds=0,
                    cost=value,
                )
            )


@pytest.mark.asyncio
async def test_settlement_kill_switch_holds_without_mutating_ledger(monkeypatch):
    conn = _FinalizationConn(settlement_enabled=False)
    limiter = _Limiter()
    monkeypatch.setattr(admission_module, "acquire_with_tenant", lambda *_args: _acquire(conn))
    result = await InboundAdmissionService(object(), limiter).finalize(
        InboundFinalizationRequest(
            call_id=CALL,
            provider="asterisk",
            provider_call_id="pbx-call-123",
            terminal_status="completed",
            duration_seconds=42,
            cost=0.25,
        )
    )
    assert result.finalized is False
    assert result.reason == "settlement_held"
    assert result.billing_status == "held"
    assert conn.insert_args is None
    assert conn.update_args[1] == "completed"
    assert limiter.released[0]["reason"] == "settlement_held"
    assert "'settlement_switch_disabled'" in conn.update_query


@pytest.mark.asyncio
async def test_held_replay_uses_first_terminal_billing_facts(monkeypatch):
    conn = _FinalizationConn(
        billing_status="held",
        settlement_enabled=True,
        status="completed",
        duration_seconds=42,
        cost=Decimal("0.25"),
        outcome="answered",
        billing_hold_reason="settlement_switch_disabled",
    )
    monkeypatch.setattr(
        admission_module,
        "acquire_with_tenant",
        lambda *_args: _acquire(conn),
    )

    result = await InboundAdmissionService(object(), _Limiter()).finalize(
        InboundFinalizationRequest(
            call_id=CALL,
            provider="asterisk",
            provider_call_id="pbx-call-123",
            terminal_status="failed",
            duration_seconds=1,
            cost=0.01,
            outcome="failed",
        )
    )

    assert result.finalized is True
    assert conn.insert_args[3] == -48
    assert conn.insert_args[4] == Decimal("0.25")
    assert conn.update_args[1] == "completed"
    assert conn.update_args[2] == 42
    assert conn.update_args[3] == Decimal("0.25")
    assert conn.update_args[4] == "answered"


@pytest.mark.asyncio
async def test_switch_hold_overage_becomes_manual_on_retry(monkeypatch):
    conn = _FinalizationConn(
        billing_status="held",
        settlement_enabled=True,
        status="completed",
        duration_seconds=91,
        billing_hold_reason="settlement_switch_disabled",
    )
    monkeypatch.setattr(
        admission_module,
        "acquire_with_tenant",
        lambda *_args: _acquire(conn),
    )

    result = await InboundAdmissionService(object(), _Limiter()).finalize(
        InboundFinalizationRequest(
            call_id=CALL,
            provider="asterisk",
            provider_call_id="pbx-call-123",
            terminal_status="completed",
            duration_seconds=1,
        )
    )

    assert result.finalized is False
    assert result.reason == "usage_exceeded_reservation"
    assert "billing_hold_reason='usage_exceeded_reservation'" in conn.update_query


@pytest.mark.asyncio
async def test_release_after_finalization_is_an_out_of_order_replay(monkeypatch):
    conn = _FinalizationConn(billing_status="finalized")
    monkeypatch.setattr(admission_module, "acquire_with_tenant", lambda *_args: _acquire(conn))
    result = await InboundAdmissionService(object(), _Limiter()).release(
        call_id=CALL,
        provider="asterisk",
        provider_call_id="pbx-call-123",
        reason="late_disconnect_event",
    )
    assert result.is_replay is True
    assert result.reason == "duplicate_replay"
    assert result.billing_status == "finalized"
    assert conn.update_args is None


class _ReversalConn:
    def __init__(self, *, existing=False, currency="USD"):
        self.existing = existing
        self.currency = currency
        self.insert_args = None
        self.update_query = None

    async def fetchrow(self, query, *args):
        if "SELECT * FROM calls" in query:
            return {"id": CALL, "tenant_id": TENANT, "billing_status": "finalized"}
        if "transaction_type IN ('finalize','release')" in query:
            return {
                "id": USAGE,
                "transaction_type": "finalize",
                "amount": Decimal("0.125"),
                "currency": self.currency,
            }
        if "related_transaction_id=$1" in query:
            return {"id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"} if self.existing else None
        if "INSERT INTO inbound_usage_transactions" in query:
            self.insert_args = args
            return {"id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"}
        if "idempotency_key=$2" in query:
            return {"id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"}
        raise AssertionError(query)

    async def fetchval(self, query, *_args):
        assert "transaction_type IN ('reserve','finalize')" in query
        return 63

    async def execute(self, query, *_args):
        self.update_query = query
        return "UPDATE 1"


@pytest.mark.asyncio
async def test_reversal_is_one_idempotent_compensating_entry(monkeypatch):
    conn = _ReversalConn()
    monkeypatch.setattr(admission_module, "acquire_with_tenant", lambda *_args: _acquire(conn))
    result = await InboundAdmissionService(object(), _Limiter()).reverse_finalized_usage(
        call_id=CALL,
        provider="asterisk",
        provider_call_id="pbx-call-123",
        reason="approved_refund",
    )
    assert result.billing_status == "reversed"
    assert conn.insert_args[2] == -63
    assert conn.insert_args[3] == Decimal("-0.125")
    assert conn.insert_args[4] == "USD"
    assert conn.insert_args[6] == USAGE
    assert "billing_status='reversed'" in conn.update_query

    null_currency_conn = _ReversalConn(currency=None)
    monkeypatch.setattr(
        admission_module,
        "acquire_with_tenant",
        lambda *_args: _acquire(null_currency_conn),
    )
    await InboundAdmissionService(object(), _Limiter()).reverse_finalized_usage(
        call_id=CALL,
        provider="asterisk",
        provider_call_id="pbx-call-123",
        reason="approved_refund",
    )
    assert null_currency_conn.insert_args[4] is None

    replay_conn = _ReversalConn(existing=True)
    monkeypatch.setattr(
        admission_module, "acquire_with_tenant", lambda *_args: _acquire(replay_conn)
    )
    replay = await InboundAdmissionService(object(), _Limiter()).reverse_finalized_usage(
        call_id=CALL,
        provider="asterisk",
        provider_call_id="pbx-call-123",
        reason="approved_refund",
    )
    assert replay.is_replay is True


def test_migration_allows_reverse_after_finalize():
    from pathlib import Path

    migration = (
        Path(__file__).parents[2] / "Alembic" / "versions" / "0022_inbound_calling_foundation.py"
    ).read_text(encoding="utf-8")
    assert "uq_inbound_usage_settlement_per_call" in migration
    assert "WHERE transaction_type IN ('finalize', 'release')" in migration
    assert "uq_inbound_usage_reverse_per_settlement" in migration
    assert "ON inbound_usage_transactions (related_transaction_id)" in migration


class _HeartbeatConn:
    def __init__(self, active=True, transfer_leases=None):
        self.active = active
        self.transfer_leases = list(transfer_leases or [])

    async def fetchrow(self, query, *_args):
        assert "processing_status='active'" in query
        if not self.active:
            return None
        return {"tenant_id": TENANT, "concurrency_lease_id": LEASE}

    async def fetch(self, query, *_args):
        assert "lease_kind='transfer'" in query
        return [{"id": lease_id} for lease_id in self.transfer_leases]


class _HeartbeatLimiter(_Limiter):
    def __init__(self):
        super().__init__()
        self.heartbeats = []

    async def heartbeat_lease(self, *_args, **kwargs):
        self.heartbeats.append(kwargs)
        return True


@pytest.mark.asyncio
async def test_active_admission_heartbeat_refreshes_exact_lease(monkeypatch):
    limiter = _HeartbeatLimiter()
    monkeypatch.setattr(
        admission_module,
        "acquire_with_tenant",
        lambda *_args: _acquire(_HeartbeatConn()),
    )
    refreshed = await InboundAdmissionService(object(), limiter).heartbeat_active_call(
        call_id=CALL,
        provider="ASTERISK",
        provider_call_id="pbx-call-123",
        request_id="heartbeat-1",
    )
    assert refreshed is True
    assert limiter.heartbeats == [
        {
            "tenant_id": TENANT,
            "lease_id": LEASE,
            "request_id": "heartbeat-1",
        }
    ]


@pytest.mark.asyncio
async def test_active_admission_heartbeat_also_refreshes_connected_transfer_lease(monkeypatch):
    limiter = _HeartbeatLimiter()
    transfer_lease = "99999999-9999-9999-9999-999999999999"
    monkeypatch.setattr(
        admission_module,
        "acquire_with_tenant",
        lambda *_args: _acquire(_HeartbeatConn(transfer_leases=[transfer_lease])),
    )
    refreshed = await InboundAdmissionService(object(), limiter).heartbeat_active_call(
        call_id=CALL,
        provider="asterisk",
        provider_call_id="pbx-call-123",
        request_id="heartbeat-2",
    )
    assert refreshed is True
    assert limiter.heartbeats == [
        {
            "tenant_id": TENANT,
            "lease_id": LEASE,
            "request_id": "heartbeat-2",
        },
        {
            "tenant_id": TENANT,
            "lease_id": transfer_lease,
            "request_id": "heartbeat-2:transfer",
        },
    ]


@pytest.mark.asyncio
async def test_reenabled_switch_hold_reconciler_is_bounded_and_reason_specific(
    monkeypatch,
):
    class HoldConn:
        def __init__(self):
            self.query = ""
            self.args = ()

        async def fetch(self, query, *args):
            self.query = query
            self.args = args
            return [
                {
                    "id": CALL,
                    "provider": "asterisk",
                    "provider_call_id": "pbx-call-123",
                    "status": "completed",
                    "duration_seconds": 42,
                    "cost": Decimal("0.25"),
                    "outcome": "answered",
                }
            ]

    conn = HoldConn()
    requests = []

    async def finalize(request):
        requests.append(request)
        return InboundFinalizationResult(
            finalized=True,
            reason="finalized",
            call_id=CALL,
            billing_status="finalized",
            lease_released=True,
            usage_transaction_id=USAGE,
        )

    service = InboundAdmissionService(object(), _Limiter())
    service.finalize = finalize
    monkeypatch.setattr(
        admission_module,
        "acquire_with_tenant",
        lambda *_args: _acquire(conn),
    )

    result = await service.reconcile_reenabled_settlement_holds(limit=9999)

    assert result["reconciled"] == 1
    assert conn.args[1] == 500
    assert "billing_status='held'" in conn.query
    assert "billing_hold_reason='settlement_switch_disabled'" in conn.query
    assert "usage_exceeded_reservation" not in conn.query
    assert "settlement_controls.inbound_settlement_enabled" in conn.query
    assert "ended_at IS NOT NULL" in conn.query
    assert "BTRIM(provider_call_id) <> ''" in conn.query
    assert len(requests) == 1
    assert requests[0].duration_seconds == 42
    assert requests[0].cost == Decimal("0.25")
    assert requests[0].reason == "settlement_switch_reenabled"


@pytest.mark.asyncio
async def test_failed_switch_hold_batch_rotates_so_row_101_is_not_starved(
    monkeypatch,
):
    def call_uuid(number: int) -> str:
        return f"00000000-0000-0000-0000-{number:012d}"

    rows = [
        {
            "id": call_uuid(number),
            "provider": "asterisk",
            "provider_call_id": f"pbx-{number}",
            "status": "completed",
            "duration_seconds": 1,
            "cost": Decimal("0.01"),
            "outcome": "answered",
        }
        for number in range(1, 102)
    ]

    class HoldConn:
        def __init__(self):
            self.rotated: list[str] = []

        async def fetch(self, _query, *_args):
            rotated = set(self.rotated)
            ordered = [row for row in rows if row["id"] not in rotated]
            ordered.extend(row for row in rows if row["id"] in rotated)
            return ordered[:100]

        async def execute(self, query, call_id):
            assert "billing_status='held'" in query
            assert "billing_hold_reason='settlement_switch_disabled'" in query
            self.rotated.append(str(call_id))
            return "UPDATE 1"

    conn = HoldConn()
    attempted: list[str] = []

    async def finalize(request):
        attempted.append(request.call_id)
        if request.call_id != call_uuid(101):
            raise RuntimeError("permanent projection failure")
        return InboundFinalizationResult(
            finalized=True,
            reason="finalized",
            call_id=request.call_id,
            billing_status="finalized",
            lease_released=True,
            usage_transaction_id=USAGE,
        )

    service = InboundAdmissionService(object(), _Limiter())
    service.finalize = finalize
    monkeypatch.setattr(
        admission_module,
        "acquire_with_tenant",
        lambda *_args: _acquire(conn),
    )

    first = await service.reconcile_reenabled_settlement_holds(limit=100)
    second = await service.reconcile_reenabled_settlement_holds(limit=100)

    assert first["reconciled"] == 0
    assert second["reconciled"] == 1
    assert call_uuid(101) not in attempted[:100]
    assert call_uuid(101) in attempted[100:]
    assert set(conn.rotated[:100]) == {call_uuid(i) for i in range(1, 101)}


class _Reconciler:
    def __init__(self):
        self.calls = []

    async def reconcile_reenabled_settlement_holds(self, **kwargs):
        self.calls.append(("holds", kwargs))
        return {"reconciled": 1, "items": [{"call_id": "held"}]}

    async def reconcile_stale_reservations(self, **kwargs):
        self.calls.append(("stale", kwargs))
        return {"reconciled": 0, "items": []}


@pytest.mark.asyncio
async def test_stale_reservation_only_queues_confirmed_pbx_recovery(monkeypatch):
    """A stale heartbeat cannot prove that a still-billable PBX leg is gone."""

    class StaleConn:
        def __init__(self):
            self.select_query = ""
            self.executions = []

        async def fetch(self, query, *args):
            self.select_query = query
            assert args[:2] == (60, 100)
            assert set(args[2]) == set(admission_module.TERMINAL_CALL_STATUSES)
            return [
                {
                    "id": CALL,
                    "tenant_id": TENANT,
                    "status": "in_call",
                    "billing_status": "reserved",
                    "processing_status": "active",
                    "concurrency_lease_id": LEASE,
                    "ended_at": None,
                    "duration_seconds": None,
                    "reserved_seconds": 90,
                }
            ]

        async def execute(self, query, *args):
            self.executions.append((query, args))
            return "UPDATE 1"

        async def fetchrow(self, query, *args):
            raise AssertionError(f"stale discovery must not write/read settlement ledger: {query}")

    conn = StaleConn()
    limiter = _Limiter()
    monkeypatch.setattr(
        admission_module,
        "acquire_with_tenant",
        lambda *_args: _acquire(conn),
    )

    result = await InboundAdmissionService(object(), limiter).reconcile_stale_reservations(
        max_age_seconds=60, limit=100
    )

    assert result == {
        "reconciled": 1,
        "items": [
            {
                "call_id": CALL,
                "billing_status": "reserved",
                "transaction_type": "termination_pending",
                "status": "termination_pending",
            }
        ],
    }
    assert limiter.released == []
    assert len(conn.executions) == 1
    update_sql, update_args = conn.executions[0]
    assert "status='termination_pending'" in update_sql
    assert "billing_status='reserved'" in update_sql
    assert "ended_at" not in update_sql
    assert "processing_status" not in update_sql
    assert update_args[0] == CALL
    assert set(update_args[1]) == set(admission_module.TERMINAL_CALL_STATUSES)
    assert "INSERT INTO inbound_usage_transactions" not in update_sql
    assert "LOWER(COALESCE(c.status,'')) = ANY($3::text[])" in conn.select_query


@pytest.mark.asyncio
async def test_watchdog_is_bounded_single_flight_and_stoppable():
    reconciler = _Reconciler()
    watchdog = InboundAdmissionWatchdog(
        reconciler, interval_seconds=10, max_age_seconds=1, batch_limit=9999
    )
    assert await watchdog.run_once() == {
        "reconciled": 1,
        "items": [{"call_id": "held"}],
    }
    assert reconciler.calls == [
        ("holds", {"limit": 500}),
        ("stale", {"max_age_seconds": 60, "limit": 500}),
    ]

    task = watchdog.start()
    assert watchdog.start() is task
    assert watchdog.running is True
    await watchdog.stop()
    assert watchdog.running is False
