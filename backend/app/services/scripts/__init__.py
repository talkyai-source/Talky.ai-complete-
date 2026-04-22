"""Small, focused scripts supporting the call-transcript feature.

Every module in this package MUST stay <=600 lines. Add new modules instead
of growing an existing one. See backend/docs/script/README.md for docs.
"""

from app.services.scripts.call_transcript_persister import (
    CallBinding,
    bind_telephony_call,
    save_call_transcript_on_hangup,
)
from app.services.scripts.campaign_transcript_query import (
    fetch_campaign_transcripts,
)
from app.services.scripts.transcript_formatting import (
    format_transcript_turn,
    format_transcript_turns,
)
from app.services.scripts.spoken_email_normalizer import (
    extract_email_from_speech,
)
from app.services.scripts.call_state_tracker import (
    CallState,
    update_state_from_user_turn,
)
from app.services.scripts.prompt_builder import (
    compose_system_prompt,
)

__all__ = [
    "CallBinding",
    "bind_telephony_call",
    "save_call_transcript_on_hangup",
    "fetch_campaign_transcripts",
    "format_transcript_turn",
    "format_transcript_turns",
    "extract_email_from_speech",
    "CallState",
    "update_state_from_user_turn",
    "compose_system_prompt",
]
