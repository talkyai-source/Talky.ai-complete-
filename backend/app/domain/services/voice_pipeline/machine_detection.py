"""Early machine detection on INTERIM transcripts — voicemail + call screening.

Why interims: a recorded greeting is CONTINUOUS speech, so Deepgram Flux never
fires EndOfTurn and the final-transcript AMD in ``voicemail_detector`` sits
deaf while the interim text plainly reads "…forwarded to voicemail" within
~3 seconds. Audited 2026-07-08: 9 of 12 calls were voicemail, each burning
45–134s of plan minutes before the silence timer gave up (several mislabeled
ANSWERED). This module closes that gap without waiting for a final turn.

Also handles the CALL-SCREENING flow (Apple/carrier "record your name and
reason for calling, I'll see if this person is available"): stage 1 the agent
states its business and holds (that behaviour lives in the prompts and is
kept); stage 2, when the screen comes back with "…is not available… leave a
message after the tone", we hang up immediately instead of nudging a beep.

Precision-first (a false positive hangs up on a live prospect):
  * the opening-voicemail check reuses the same high-precision phrase list as
    the final-transcript AMD, and only within the opening turns;
  * ``machine_end`` additionally requires that a screening bot was ALREADY
    heard on this call — a live receptionist saying "he's not available" can
    never trip it because no screening phrase preceded her.

Pure decision core (:func:`assess_machine_text`) + a thin async wrapper the
transcript handler calls per interim. All fail-soft: any error returns the
safe default (keep the call alive).
"""
from __future__ import annotations

import logging

from app.domain.services.voice_pipeline.transcript_heuristics import (
    is_voicemail_greeting,
)

logger = logging.getLogger(__name__)

# A voicemail greeting is the FIRST thing heard — same window as the
# final-transcript AMD in voicemail_detector.
_MAX_TURN_INDEX_FOR_OPENING = 1

# Carrier / handset call-screening wording. Marking screening only sets a
# flag — it never hangs up by itself — so this list may be a little looser
# than the voicemail one. Matched as substrings on normalised text.
_SCREENING_PHRASES = (
    "record your name and reason",
    "if you record your name",
    "see if this person is available",
    "see if this call is available",
    "please stay on the line",
    "connecting you now",  # some screens say this before the hold
)

# The screening endgame / any-time machine wording. Only consulted once
# screening was already heard on this call (see precision note above).
_MACHINE_END_PHRASES = (
    "is not available",
    "leave an additional message",
    "leave a message",
    "after the tone",
    "at the tone",
    "record your message",
    "voicemail",
    "voice mail",
)


def assess_machine_text(
    text: str,
    *,
    turn_index: int,
    screening_seen: bool,
) -> str:
    """Classify one (interim or final) caller-side transcript.

    Returns one of:
      * ``"voicemail"``    — opening-turn answering-machine greeting → hang up.
      * ``"screening"``    — a screening service answered → flag it (no hangup).
      * ``"machine_end"``  — post-screening "not available / after the tone"
                             → hang up.
      * ``"none"``         — nothing machine-like.
    """
    if not text:
        return "none"
    blob = " ".join(text.lower().split())

    if turn_index <= _MAX_TURN_INDEX_FOR_OPENING and is_voicemail_greeting(blob):
        return "voicemail"

    if screening_seen and any(p in blob for p in _MACHINE_END_PHRASES):
        return "machine_end"

    if any(p in blob for p in _SCREENING_PHRASES):
        return "screening"

    return "none"


async def handle_machine_interim(
    call_id: str, session, text: str, media_gateway=None
) -> bool:
    """Run machine detection for one caller transcript (interim or final).

    Returns True when the call is being hung up (voicemail / machine end) —
    the caller should stop processing this transcript. Sets these session
    flags:

      * ``_machine_screening`` — a screening service answered this call
        (read by the silence monitor to suppress nudges at recordings).
      * ``_amd_voicemail``     — set on the pipeline session directly AND via
        the shared voicemail flagger, so the outcome resolver records
        VOICEMAIL and the dialer reschedules the lead (+24h).

    ``media_gateway`` — the pipeline's gateway; its ``hangup_call`` maps the
    pipeline session id to the PBX channel, which is the reliable hangup
    route from this layer. Falls back to the bridge adapter.

    Fail-soft: any error returns False and never disturbs a live call.
    """
    try:
        verdict = assess_machine_text(
            text,
            turn_index=getattr(session, "turn_id", 0) or 0,
            screening_seen=bool(getattr(session, "_machine_screening", False)),
        )
        if verdict == "none":
            return False

        if verdict == "screening":
            if not getattr(session, "_machine_screening", False):
                try:
                    session._machine_screening = True
                except Exception:
                    pass
                logger.info(
                    "call_screening_detected call=%s (text=%r) — agent will "
                    "state its business and hold; endgame armed",
                    str(call_id)[:12], (text or "")[:80],
                )
            return False

        # voicemail | machine_end → hang up now, mark VOICEMAIL.
        logger.info(
            "machine_detected_interim call=%s verdict=%s turn=%s — hanging up "
            "(text=%r)",
            str(call_id)[:12], verdict, getattr(session, "turn_id", "?"),
            (text or "")[:80],
        )
        try:
            session._amd_voicemail = True
        except Exception:
            pass
        try:
            from app.domain.services.voice_pipeline.voicemail_detector import (
                _flag_session_voicemail,
            )
            _flag_session_voicemail(call_id)
        except Exception:
            pass

        hung_up = False
        if media_gateway is not None and hasattr(media_gateway, "hangup_call"):
            try:
                hung_up = bool(
                    await media_gateway.hangup_call(
                        str(call_id), reason="voicemail_detected"
                    )
                )
            except Exception as gw_exc:  # pragma: no cover - defensive
                logger.debug(
                    "machine_gateway_hangup_failed call=%s err=%s",
                    str(call_id)[:12], gw_exc,
                )
        if not hung_up:
            try:
                from app.domain.services.telephony.lifecycle import _bridge
                adapter = _bridge()._adapter
                if adapter is not None:
                    await adapter.hangup(str(call_id))
            except Exception as hang_exc:  # pragma: no cover - defensive
                logger.warning(
                    "machine_hangup_failed call=%s err=%s",
                    str(call_id)[:12], hang_exc,
                )
        return True
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("machine_detect_error call=%s err=%s", str(call_id)[:12], exc)
        return False
