from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class RealtimeSessionConfig:
    pipeline_mode: str = "cascaded"
    realtime_prompt: Optional[Dict[str, Any]] = None
    realtime_model: str = "gpt-realtime-2"
    realtime_voice: str = "marin"
    realtime_settings: Optional[Dict[str, Any]] = None

    # True-inbound opening policy for the realtime speech-to-speech path.
    # ``direction`` alone is not enough: an inbound campaign may be either
    # caller-first or agent-first, and after-hours AI message intake is always
    # agent-first.  These values are copied from the immutable pre-answer
    # admission snapshot before the realtime socket is created. ``None`` keeps
    # every legacy/outbound call on the historical direction-derived default.
    realtime_greet_on_start: Optional[bool] = None
    realtime_opening_greeting: Optional[str] = None
    realtime_message_intake: bool = False
