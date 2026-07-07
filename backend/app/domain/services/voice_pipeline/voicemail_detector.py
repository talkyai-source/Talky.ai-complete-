"""Real-time answering-machine (voicemail) detection.

When an outbound call connects to a voicemail / answering machine instead of a
live person, the agent must NOT hold a conversation with the recording — it
burns minutes and we never leave a message (product policy 2026-07-07). This
module inspects the FIRST caller-side transcript; if it reads like a voicemail
greeting we hang up immediately and mark the call so the outcome resolver
records VOICEMAIL (which reschedules the lead +24h, not the same day).

Kept separate and import-light so both the cascaded pipeline
(``transcript_handler``) and the realtime pipeline (``realtime_bridge``) can
call it with a single await.
"""
from __future__ import annotations

import logging

from app.domain.services.voice_pipeline.transcript_heuristics import (
    is_voicemail_greeting,
)

logger = logging.getLogger(__name__)

# Only the opening caller turn(s) can be a machine greeting — a voicemail
# greeting is the first thing you hear. Matching later turns would let a live
# person who happens to say "leave a message" trip detection mid-conversation.
_MAX_TURN_INDEX_FOR_AMD = 1


def _flag_session_voicemail(call_id: str) -> None:
    """Mark the live voice session (and its call session) as voicemail so the
    outcome resolver records VOICEMAIL even before its transcript-scan fallback
    runs. Best-effort — never raises."""
    try:
        from app.domain.services.telephony.lifecycle import _state
        vs = _state().get_voice_session(str(call_id))
        if vs is not None:
            try:
                vs._amd_voicemail = True
            except Exception:
                pass
            cs = getattr(vs, "call_session", None)
            if cs is not None:
                try:
                    cs._amd_voicemail = True
                except Exception:
                    pass
    except Exception:
        pass


async def detect_and_hang_up_voicemail(
    call_id: str, text: str, turn_index: int
) -> bool:
    """If the caller's turn ``turn_index`` looks like a voicemail greeting on
    the first turn, mark the call as voicemail and hang up immediately.

    Returns ``True`` when voicemail was detected and the hangup was triggered —
    the caller should then stop processing this turn (skip the LLM response so
    the agent stays silent). Fail-soft: any error returns ``False`` so a
    detection hiccup can never break a real, live call.
    """
    try:
        if turn_index is None or turn_index > _MAX_TURN_INDEX_FOR_AMD:
            return False
        if not is_voicemail_greeting(text):
            return False

        logger.info(
            "voicemail_detected call=%s turn=%s — hanging up, leaving no message "
            "(text=%r)",
            str(call_id)[:12], turn_index, (text or "")[:80],
        )
        _flag_session_voicemail(call_id)

        try:
            from app.domain.services.telephony.lifecycle import _bridge
            adapter = _bridge()._adapter
            if adapter is not None:
                await adapter.hangup(str(call_id))
        except Exception as hang_exc:  # pragma: no cover - defensive
            logger.warning(
                "voicemail_hangup_failed call=%s err=%s",
                str(call_id)[:12], hang_exc,
            )
        return True
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(
            "voicemail_detect_error call=%s err=%s", str(call_id)[:12], exc
        )
        return False
