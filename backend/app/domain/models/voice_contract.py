"""
Voice Contract — Day 1
Shared contract for the voice pipeline: canonical call states, leg model,
event schema, and talklee_call_id generation.

This module is the *single source of truth* for every voice-related enum and
data-class.  Existing enums (CallStatus, CallOutcome, CallState) remain
untouched; mapping helpers convert them into the canonical VoiceCallState.
"""
from __future__ import annotations

import uuid
import logging
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# =============================================================================
# 1. CANONICAL STATE MACHINE
# =============================================================================

class VoiceCallState(str, Enum):
    """
    Provider-agnostic call state.

    State machine::

        INITIATED ──► RINGING ──► ANSWERED ──► IN_PROGRESS ──► COMPLETED
                          │                                  └► FAILED
                          ├──► NO_ANSWER
                          ├──► BUSY
                          └──► REJECTED
        INITIATED ──► FAILED  (never rang)
        any ──► ERROR          (unrecoverable)
    """
    INITIATED    = "initiated"
    RINGING      = "ringing"
    ANSWERED     = "answered"
    IN_PROGRESS  = "in_progress"
    COMPLETED    = "completed"
    FAILED       = "failed"
    NO_ANSWER    = "no_answer"
    BUSY         = "busy"
    REJECTED     = "rejected"
    ERROR        = "error"


# Allowed transitions (source → set of valid targets)
_VALID_TRANSITIONS: Dict[VoiceCallState, set] = {
    VoiceCallState.INITIATED:   {VoiceCallState.RINGING, VoiceCallState.FAILED, VoiceCallState.ERROR},
    VoiceCallState.RINGING:     {VoiceCallState.ANSWERED, VoiceCallState.NO_ANSWER, VoiceCallState.BUSY,
                                 VoiceCallState.REJECTED, VoiceCallState.FAILED, VoiceCallState.ERROR},
    VoiceCallState.ANSWERED:    {VoiceCallState.IN_PROGRESS, VoiceCallState.COMPLETED,
                                 VoiceCallState.FAILED, VoiceCallState.ERROR},
    VoiceCallState.IN_PROGRESS: {VoiceCallState.COMPLETED, VoiceCallState.FAILED, VoiceCallState.ERROR},
    # Terminal states — no further transitions
    VoiceCallState.COMPLETED:   set(),
    VoiceCallState.FAILED:      set(),
    VoiceCallState.NO_ANSWER:   set(),
    VoiceCallState.BUSY:        set(),
    VoiceCallState.REJECTED:    set(),
    VoiceCallState.ERROR:       set(),
}


def is_valid_transition(from_state: VoiceCallState, to_state: VoiceCallState) -> bool:
    """Check whether a state transition is allowed."""
    return to_state in _VALID_TRANSITIONS.get(from_state, set())


def is_terminal_state(state: VoiceCallState) -> bool:
    """Return True if the state is terminal (no further transitions)."""
    return len(_VALID_TRANSITIONS.get(state, set())) == 0


# =============================================================================
# 2. LEG & EVENT TYPE ENUMS
# =============================================================================

class LegType(str, Enum):
    """Type of call leg."""
    PSTN_OUTBOUND = "pstn_outbound"
    PSTN_INBOUND  = "pstn_inbound"
    WEBSOCKET     = "websocket"
    SIP           = "sip"
    BROWSER       = "browser"


class LegDirection(str, Enum):
    """Direction of a call leg."""
    INBOUND  = "inbound"
    OUTBOUND = "outbound"


class TelephonyProvider(str, Enum):
    """Telephony provider identifiers."""
    SIP         = "sip"         # Generic SIP stack (Asterisk / FreeSWITCH)
    VONAGE      = "vonage"      # Vonage Voice API (cloud)
    TWILIO      = "twilio"      # Twilio Programmable Voice (cloud, future)
    FREESWITCH  = "freeswitch"  # Backwards compat alias for SIP
    BROWSER     = "browser"
    SIMULATION  = "simulation"


class EventType(str, Enum):
    """
    Types of events that can be recorded in the call_events log.

    Aligned with existing WebSocket MessageType values where applicable.
    """
    # — State transitions —
    STATE_CHANGE     = "state_change"
    LEG_STARTED      = "leg_started"
    LEG_ENDED        = "leg_ended"

    # — Pipeline events (mirrors websocket_messages.MessageType) —
    SESSION_START    = "session_start"
    SESSION_END      = "session_end"
    MEDIA_STARTED    = "media_started"
    AUDIO_START      = "audio_start"
    AUDIO_END        = "audio_end"
    TRANSCRIPT       = "transcript"
    LLM_START        = "llm_start"
    LLM_RESPONSE     = "llm_response"
    LLM_END          = "llm_end"
    TTS_START        = "tts_start"
    TTS_END          = "tts_end"

    # — External events —
    WEBHOOK_RECEIVED = "webhook_received"
    ERROR            = "error"


# =============================================================================
# 3. TALKLEE CALL ID
# =============================================================================

def generate_talklee_call_id() -> str:
    """
    Generate a unique, human-friendly call identifier.

    Format: ``tlk_<12-character-hex>``

    Examples::

        tlk_a1b2c3d4e5f6
        tlk_0f1e2d3c4b5a
    """
    return f"tlk_{uuid.uuid4().hex[:12]}"


# =============================================================================
# 4. PYDANTIC MODELS
# =============================================================================

class CallLeg(BaseModel):
    """
    A single leg of a call.

    A call can have 1-N legs (e.g. PSTN outbound + WebSocket audio).
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    call_id: str = Field(..., description="FK to calls table")
    talklee_call_id: Optional[str] = Field(None, description="Human-friendly call ID")

    # Leg identity
    leg_type: LegType
    direction: LegDirection
    provider: TelephonyProvider
    provider_leg_id: Optional[str] = Field(None, description="External provider leg UUID")

    # Endpoints
    from_number: Optional[str] = None
    to_number: Optional[str] = None

    # Status
    status: str = Field(default="initiated", description="Current leg status")

    # Timing
    started_at: Optional[datetime] = None
    answered_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration_seconds: Optional[int] = None

    # Flexible metadata
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)


class CallEvent(BaseModel):
    """
    An immutable event in the call lifecycle.

    Persisted to ``call_events``; the append-only log that makes every
    call traceable end-to-end.
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    call_id: str = Field(..., description="FK to calls table")
    talklee_call_id: Optional[str] = Field(None, description="Human-friendly call ID")
    leg_id: Optional[str] = Field(None, description="FK to call_legs table (nullable)")

    event_type: EventType
    previous_state: Optional[str] = None
    new_state: Optional[str] = None
    event_data: Dict[str, Any] = Field(default_factory=dict)
    source: str = Field(..., description="Origin: vonage_webhook, sip_bridge, call_service, websocket, dialer_worker, system")

    created_at: datetime = Field(default_factory=datetime.utcnow)


# =============================================================================
# 5. MAPPING HELPERS — existing enums → VoiceCallState
# =============================================================================

def map_call_status_to_voice_state(status: str) -> VoiceCallState:
    """
    Map ``CallStatus`` enum values (from ``call.py``) to ``VoiceCallState``.
    """
    _MAP = {
        "initiated":   VoiceCallState.INITIATED,
        "ringing":     VoiceCallState.RINGING,
        "answered":    VoiceCallState.ANSWERED,
        "in_progress": VoiceCallState.IN_PROGRESS,
        "completed":   VoiceCallState.COMPLETED,
        "failed":      VoiceCallState.FAILED,
        "no_answer":   VoiceCallState.NO_ANSWER,
        "busy":        VoiceCallState.BUSY,
    }
    return _MAP.get(status, VoiceCallState.ERROR)


def map_call_outcome_to_voice_state(outcome: str) -> VoiceCallState:
    """
    Map ``CallOutcome`` enum values (from ``dialer_job.py``) to
    ``VoiceCallState``.
    """
    _MAP = {
        "answered":          VoiceCallState.ANSWERED,
        "no_answer":         VoiceCallState.NO_ANSWER,
        "busy":              VoiceCallState.BUSY,
        "failed":            VoiceCallState.FAILED,
        "timeout":           VoiceCallState.NO_ANSWER,
        "spam":              VoiceCallState.REJECTED,
        "invalid":           VoiceCallState.FAILED,
        "unavailable":       VoiceCallState.FAILED,
        "disconnected":      VoiceCallState.FAILED,
        "goal_achieved":     VoiceCallState.COMPLETED,
        "goal_not_achieved": VoiceCallState.COMPLETED,
        "voicemail":         VoiceCallState.NO_ANSWER,
        "rejected":          VoiceCallState.REJECTED,
    }
    return _MAP.get(outcome, VoiceCallState.ERROR)


def map_vonage_status(vonage_status: str) -> Optional[VoiceCallState]:
    """
    Map raw Vonage event status strings to ``VoiceCallState``.

    Used by the Vonage webhook bridge to normalise provider-specific
    statuses into the canonical state machine.

    Returns ``None`` for informational statuses that do not require
    processing (``started``, ``ringing``).
    """
    _MAP = {
        "started":     VoiceCallState.INITIATED,
        "ringing":     VoiceCallState.RINGING,
        "answered":    VoiceCallState.ANSWERED,
        "completed":   VoiceCallState.COMPLETED,
        "busy":        VoiceCallState.BUSY,
        "timeout":     VoiceCallState.NO_ANSWER,
        "failed":      VoiceCallState.FAILED,
        "rejected":    VoiceCallState.REJECTED,
        "unanswered":  VoiceCallState.NO_ANSWER,
        "cancelled":   VoiceCallState.FAILED,
        "machine":     VoiceCallState.NO_ANSWER,
    }
    return _MAP.get(vonage_status)


