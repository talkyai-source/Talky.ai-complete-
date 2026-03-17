"""
Tests for Day 1: Voice Contract & Call Logging

Tests cover:
- talklee_call_id generation (format + uniqueness)
- VoiceCallState enum and transition validation
- CallLeg / CallEvent model validation
- Mapping helpers (CallStatus → VoiceCallState, CallOutcome → VoiceCallState, Vonage → VoiceCallState)
- Terminal state detection
"""
import re
import pytest
from datetime import datetime

from app.domain.models.voice_contract import (
    VoiceCallState,
    LegType,
    LegDirection,
    TelephonyProvider,
    EventType,
    CallLeg,
    CallEvent,
    generate_talklee_call_id,
    is_valid_transition,
    is_terminal_state,
    map_call_status_to_voice_state,
    map_call_outcome_to_voice_state,
    map_vonage_status,
)


# =============================================================================
# talklee_call_id
# =============================================================================

class TestTalkleeCallId:
    """Tests for the talklee_call_id generator."""

    def test_format(self):
        """ID must match tlk_<12 hex chars>."""
        call_id = generate_talklee_call_id()
        assert re.fullmatch(r"tlk_[0-9a-f]{12}", call_id), f"Bad format: {call_id}"

    def test_uniqueness(self):
        """1000 generated IDs should all be unique."""
        ids = {generate_talklee_call_id() for _ in range(1000)}
        assert len(ids) == 1000

    def test_prefix(self):
        """All IDs start with the tlk_ prefix."""
        for _ in range(10):
            assert generate_talklee_call_id().startswith("tlk_")

    def test_length(self):
        """ID total length is 16 chars (tlk_ = 4 + 12 hex)."""
        assert len(generate_talklee_call_id()) == 16


# =============================================================================
# VoiceCallState
# =============================================================================

class TestVoiceCallState:
    """Tests for the canonical state machine."""

    def test_all_states_exist(self):
        """Verify all 10 expected states are defined."""
        expected = {
            "initiated", "ringing", "answered", "in_progress",
            "completed", "failed", "no_answer", "busy",
            "rejected", "error",
        }
        actual = {s.value for s in VoiceCallState}
        assert actual == expected

    def test_valid_transitions(self):
        """Happy-path transitions should be valid."""
        assert is_valid_transition(VoiceCallState.INITIATED, VoiceCallState.RINGING)
        assert is_valid_transition(VoiceCallState.RINGING, VoiceCallState.ANSWERED)
        assert is_valid_transition(VoiceCallState.ANSWERED, VoiceCallState.IN_PROGRESS)
        assert is_valid_transition(VoiceCallState.IN_PROGRESS, VoiceCallState.COMPLETED)

    def test_invalid_transitions(self):
        """Backward and illegal transitions should be rejected."""
        assert not is_valid_transition(VoiceCallState.COMPLETED, VoiceCallState.INITIATED)
        assert not is_valid_transition(VoiceCallState.ANSWERED, VoiceCallState.RINGING)
        assert not is_valid_transition(VoiceCallState.BUSY, VoiceCallState.ANSWERED)

    def test_error_from_any_non_terminal(self):
        """ERROR should be reachable from all non-terminal states."""
        for state in [VoiceCallState.INITIATED, VoiceCallState.RINGING,
                      VoiceCallState.ANSWERED, VoiceCallState.IN_PROGRESS]:
            assert is_valid_transition(state, VoiceCallState.ERROR)

    def test_terminal_states(self):
        """Terminal states should have no outgoing transitions."""
        for state in [VoiceCallState.COMPLETED, VoiceCallState.FAILED,
                      VoiceCallState.NO_ANSWER, VoiceCallState.BUSY,
                      VoiceCallState.REJECTED, VoiceCallState.ERROR]:
            assert is_terminal_state(state), f"{state} should be terminal"

    def test_non_terminal_states(self):
        """Non-terminal states should NOT be flagged as terminal."""
        for state in [VoiceCallState.INITIATED, VoiceCallState.RINGING,
                      VoiceCallState.ANSWERED, VoiceCallState.IN_PROGRESS]:
            assert not is_terminal_state(state), f"{state} should not be terminal"


# =============================================================================
# Mapping helpers
# =============================================================================

class TestMappingHelpers:
    """Tests for existing enum → VoiceCallState mapping functions."""

    # --- CallStatus → VoiceCallState ---

    @pytest.mark.parametrize("status,expected", [
        ("initiated",   VoiceCallState.INITIATED),
        ("ringing",     VoiceCallState.RINGING),
        ("answered",    VoiceCallState.ANSWERED),
        ("in_progress", VoiceCallState.IN_PROGRESS),
        ("completed",   VoiceCallState.COMPLETED),
        ("failed",      VoiceCallState.FAILED),
        ("no_answer",   VoiceCallState.NO_ANSWER),
        ("busy",        VoiceCallState.BUSY),
    ])
    def test_call_status_mapping(self, status, expected):
        assert map_call_status_to_voice_state(status) == expected

    def test_call_status_unknown_returns_error(self):
        assert map_call_status_to_voice_state("unknown_status") == VoiceCallState.ERROR

    # --- CallOutcome → VoiceCallState ---

    @pytest.mark.parametrize("outcome,expected", [
        ("answered",          VoiceCallState.ANSWERED),
        ("no_answer",         VoiceCallState.NO_ANSWER),
        ("busy",              VoiceCallState.BUSY),
        ("failed",            VoiceCallState.FAILED),
        ("timeout",           VoiceCallState.NO_ANSWER),
        ("spam",              VoiceCallState.REJECTED),
        ("goal_achieved",     VoiceCallState.COMPLETED),
        ("goal_not_achieved", VoiceCallState.COMPLETED),
        ("voicemail",         VoiceCallState.NO_ANSWER),
        ("rejected",          VoiceCallState.REJECTED),
    ])
    def test_call_outcome_mapping(self, outcome, expected):
        assert map_call_outcome_to_voice_state(outcome) == expected

    def test_call_outcome_unknown_returns_error(self):
        assert map_call_outcome_to_voice_state("unknown") == VoiceCallState.ERROR

    # --- Vonage status → VoiceCallState ---

    @pytest.mark.parametrize("vonage_status,expected", [
        ("started",    VoiceCallState.INITIATED),
        ("ringing",    VoiceCallState.RINGING),
        ("answered",   VoiceCallState.ANSWERED),
        ("completed",  VoiceCallState.COMPLETED),
        ("busy",       VoiceCallState.BUSY),
        ("timeout",    VoiceCallState.NO_ANSWER),
        ("failed",     VoiceCallState.FAILED),
        ("rejected",   VoiceCallState.REJECTED),
        ("unanswered", VoiceCallState.NO_ANSWER),
        ("cancelled",  VoiceCallState.FAILED),
        ("machine",    VoiceCallState.NO_ANSWER),
    ])
    def test_vonage_status_mapping(self, vonage_status, expected):
        assert map_vonage_status(vonage_status) == expected

    def test_vonage_unknown_returns_none(self):
        """Unknown Vonage statuses should return None (not processed)."""
        assert map_vonage_status("some_new_vonage_status") is None


# =============================================================================
# Pydantic models
# =============================================================================

class TestCallLeg:
    """Tests for the CallLeg pydantic model."""

    def test_minimal_construction(self):
        leg = CallLeg(
            call_id="call-123",
            leg_type=LegType.PSTN_OUTBOUND,
            direction=LegDirection.OUTBOUND,
            provider=TelephonyProvider.VONAGE,
        )
        assert leg.call_id == "call-123"
        assert leg.leg_type == LegType.PSTN_OUTBOUND
        assert leg.status == "initiated"
        assert leg.talklee_call_id is None
        assert leg.id is not None  # auto-generated

    def test_full_construction(self):
        leg = CallLeg(
            call_id="c-1",
            talklee_call_id="tlk_abc123def456",
            leg_type=LegType.WEBSOCKET,
            direction=LegDirection.INBOUND,
            provider=TelephonyProvider.VONAGE,
            provider_leg_id="vonage-uuid-123",
            from_number="+15551234567",
            to_number="+15559876543",
            status="answered",
            started_at=datetime(2026, 1, 1, 12, 0, 0),
            answered_at=datetime(2026, 1, 1, 12, 0, 5),
        )
        assert leg.provider_leg_id == "vonage-uuid-123"
        assert leg.from_number == "+15551234567"


class TestCallEvent:
    """Tests for the CallEvent pydantic model."""

    def test_minimal_construction(self):
        event = CallEvent(
            call_id="call-456",
            event_type=EventType.STATE_CHANGE,
            source="call_service",
        )
        assert event.call_id == "call-456"
        assert event.event_type == EventType.STATE_CHANGE
        assert event.event_data == {}
        assert event.id is not None

    def test_state_change_event(self):
        event = CallEvent(
            call_id="call-789",
            talklee_call_id="tlk_aabbccddeeff",
            event_type=EventType.STATE_CHANGE,
            previous_state="ringing",
            new_state="answered",
            event_data={"vonage_status": "answered"},
            source="vonage_webhook",
        )
        assert event.previous_state == "ringing"
        assert event.new_state == "answered"
        assert event.talklee_call_id == "tlk_aabbccddeeff"

    def test_webhook_event(self):
        event = CallEvent(
            call_id="call-100",
            event_type=EventType.WEBHOOK_RECEIVED,
            event_data={"vonage_status": "completed", "duration": 45},
            source="vonage_webhook",
        )
        assert event.event_data["duration"] == 45


# =============================================================================
# Enum value coverage
# =============================================================================

class TestEnumValues:
    """Ensure enum values are string-serializable and complete."""

    def test_leg_types(self):
        expected = {"pstn_outbound", "pstn_inbound", "websocket", "sip", "browser"}
        assert {lt.value for lt in LegType} == expected

    def test_leg_directions(self):
        assert {d.value for d in LegDirection} == {"inbound", "outbound"}

    def test_telephony_providers(self):
        expected = {"sip", "vonage", "twilio", "freeswitch", "browser", "simulation"}
        assert {p.value for p in TelephonyProvider} == expected

    def test_event_types(self):
        """EventType should have at least 15 types."""
        assert len(EventType) >= 15

    def test_event_types_are_strings(self):
        """All EventType values should be plain strings."""
        for et in EventType:
            assert isinstance(et.value, str)
