"""Direction-aware call-history projections must preserve privacy and state."""

import inspect

from app.api.v1.endpoints.calls import (
    _display_from_number,
    _display_caller_ani,
    _inbound_config_id,
    _media_state,
    _recording_state,
    _route_metadata,
    _transcript_state,
    get_call,
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
