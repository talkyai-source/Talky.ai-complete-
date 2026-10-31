"""Direction-aware call-history projections must preserve privacy and state."""

import inspect
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest

import app.api.v1.endpoints.calls as calls_module
from app.api.v1.dependencies import CurrentUser

from app.api.v1.endpoints.calls import (
    _display_from_number,
    _display_caller_ani,
    _inbound_config_id,
    _media_state,
    _recording_state,
    _route_metadata,
    _transcript_state,
    get_call,
    list_rejected_inbound_calls,
    list_calls,
)


def test_private_inbound_ani_is_never_recovered_from_phone_number():
    call = {
        "direction": "inbound",
        "caller_ani": "+15551234567",
        "phone_number": "+15551234567",
        "caller_ani_private": True,
    }

    assert _display_caller_ani(call) is None


def test_public_inbound_projection_uses_pinned_route_snapshot():
    call = {
        "direction": "inbound",
        "caller_ani": "+15551234567",
        "caller_ani_private": False,
        "assignment_id": "assignment-fallback",
        "route_snapshot": {
            "route": {"assignment_id": "assignment-pinned"},
            "inbound_config": {
                "checksum": "a" * 64,
                "recording_enabled": True,
            },
            "controls": {"recording_enabled": True},
        },
        "admission_status": "allowed",
        "processing_status": "active",
        "consent_status": "granted",
        "status": "in_call",
        "has_transcript": False,
        "recording_status": None,
    }

    snapshot, checksum, route_id = _route_metadata(call)

    assert _display_caller_ani(call) == "+15551234567"
    assert snapshot["route"]["assignment_id"] == "assignment-pinned"
    assert route_id == "assignment-pinned"
    assert checksum == "a" * 64
    assert _media_state(call) == "active"
    assert _recording_state(call, snapshot) == "pending"
    assert _transcript_state(call) == "pending"


def test_denied_inbound_call_never_claims_media_or_transcript_started():
    call = {
        "direction": "inbound",
        "admission_status": "denied",
        "processing_status": "pending",
        "status": "failed",
        "has_transcript": False,
    }

    assert _media_state(call) == "not_started"
    assert _recording_state(call, {}) == "not_started"
    assert _transcript_state(call) == "not_started"


def test_disabled_recording_is_independent_of_live_media():
    call = {
        "direction": "inbound",
        "admission_status": "allowed",
        "processing_status": "active",
        "status": "in_call",
    }
    snapshot = {
        "inbound_config": {"recording_enabled": True},
        "controls": {"recording_enabled": False},
    }

    assert _media_state(call) == "active"
    assert _recording_state(call, snapshot) == "disabled"


def test_base_campaign_and_inbound_config_id_remain_distinct():
    call = {
        "direction": "inbound",
        "campaign_id": "base-campaign-id",
        "route_snapshot": {
            "inbound_config": {"id": "inbound-config-id"},
            "route": {"config_id": "route-fallback-id"},
        },
    }

    assert call["campaign_id"] == "base-campaign-id"
    assert _inbound_config_id(call) == "inbound-config-id"


def test_inbound_config_id_uses_pinned_route_fallback_for_older_snapshots():
    call = {
        "direction": "inbound",
        "route_snapshot": {"route": {"config_id": "inbound-config-id"}},
    }

    assert _inbound_config_id(call) == "inbound-config-id"
    assert _inbound_config_id({"direction": "outbound"}) is None


def test_outbound_from_number_comes_from_durable_call_leg_projection():
    assert _display_from_number(
        {
            "direction": "outbound",
            "outbound_from_number": "+15550001111",
            "caller_ani": "+15559999999",
        }
    ) == "+15550001111"


def test_inbound_history_filter_targets_config_snapshot_not_base_campaign():
    source = inspect.getsource(list_calls)
    assert "inbound_config,id" in source
    assert "route,config_id" in source
    assert 'conditions.append(f"c.campaign_id =' not in source
    assert "FROM call_legs leg" in source


def test_call_detail_parent_billing_projection_excludes_child_leg_ledger():
    source = inspect.getsource(get_call)

    assert "FROM inbound_usage_transactions u" in source
    assert "u.call_leg_id IS NULL" in source


@pytest.mark.asyncio
async def test_rejected_inbound_feed_unions_pre_row_and_after_hours(monkeypatch):
    tenant_id = UUID("11111111-1111-1111-1111-111111111111")
    now = datetime.now(timezone.utc)
    rows = [
        {
            "id": UUID("22222222-2222-2222-2222-222222222222"),
            "source": "pre_row",
            "occurred_at": now,
            "status": "denied",
            "reason": "unknown_did",
            "provider": "asterisk",
            "provider_call_id": "pbx-1",
            "caller_ani": None,
            "caller_ani_private": True,
            "called_did": "+15550001111",
            "campaign_id": None,
            "campaign_name": None,
            "inbound_config_id": None,
            "assignment_id": None,
            "total_rows": 2,
        },
        {
            "id": UUID("33333333-3333-3333-3333-333333333333"),
            "source": "call",
            "occurred_at": now,
            "status": "after_hours",
            "reason": "after_hours_closed",
            "provider": "asterisk",
            "provider_call_id": "pbx-2",
            "caller_ani": "+15550002222",
            "caller_ani_private": False,
            "called_did": "+15550001111",
            "campaign_id": UUID("44444444-4444-4444-4444-444444444444"),
            "campaign_name": "Reception",
            "inbound_config_id": None,
            "assignment_id": None,
            "total_rows": 2,
        },
    ]

    class Conn:
        async def fetch(self, query, *args):
            assert "FROM inbound_rejections" in query
            assert "c.admission_reason='after_hours_closed'" in query
            assert args == (tenant_id, 50, 0)
            return rows

    @asynccontextmanager
    async def acquire(_pool, actual_tenant):
        assert actual_tenant == tenant_id
        yield Conn()

    monkeypatch.setattr(calls_module, "acquire_with_tenant", acquire)
    result = await list_rejected_inbound_calls(
        page=1,
        page_size=50,
        campaign_id=None,
        current_user=CurrentUser(
            id="user-1",
            email="user@example.com",
            tenant_id=str(tenant_id),
        ),
        db_client=SimpleNamespace(pool=object()),
    )

    assert result.total == 2
    assert [item.source for item in result.items] == ["pre_row", "call"]
    assert result.items[0].caller_ani is None
    assert result.items[1].status == "after_hours"
    assert result.items[1].caller_ani == "+15550002222"
