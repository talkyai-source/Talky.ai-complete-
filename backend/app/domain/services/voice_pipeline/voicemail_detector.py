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


def _locate_session(call_id: str) -> tuple:
    """``(pbx_channel_key, voice_session)`` for the call, or ``(None, None)``.

    ``call_id`` here is the pipeline UUID, but the telephony registry is keyed
    by the PBX channel id, so a plain ``get_voice_session(call_id)`` misses on
    every Asterisk call. Shares the resolver the opt-out path uses.
    """
    try:
        from app.domain.services.dialer.opt_out import locate_live_voice_session

        return locate_live_voice_session(call_id)
    except Exception:
        return None, None


def _is_true_inbound(call_id: str, voice_session: object = None) -> bool:
    """Return whether the live session is a genuine caller-originated call.

    Direction comes from the resolved session itself (never from a registry
    lookup by the pipeline UUID, which always misses).
    """

    try:
        if voice_session is None:
            voice_session = _locate_session(call_id)[1]
        if voice_session is None:
            return False
        if getattr(voice_session, "_is_true_inbound", None) is True:
            return True
        call_session = getattr(voice_session, "call_session", None)
        raw = (
            getattr(call_session, "_call_direction", None)
            or getattr(voice_session, "_call_direction", None)
            or getattr(getattr(voice_session, "config", None), "direction", None)
        )
        return str(getattr(raw, "value", raw) or "").strip().lower() == "inbound"
    except Exception:
        # Unknown legacy sessions retain the historical outbound behaviour.
        return False


def _flag_session_voicemail(call_id: str, voice_session: object = None) -> None:
    """Mark the live voice session (and its call session) as voicemail so the
    outcome resolver records VOICEMAIL even before its transcript-scan fallback
    runs. Best-effort — never raises."""
    try:
        vs = voice_session if voice_session is not None else _locate_session(call_id)[1]
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
        # ``call_id`` is the pipeline UUID: resolve the live session once
        # (registry key = PBX channel id) and read direction/gateway from it.
        pbx_channel, voice_session = _locate_session(call_id)
        if _is_true_inbound(call_id, voice_session):
            return False
        if turn_index is None or turn_index > _MAX_TURN_INDEX_FOR_AMD:
            return False
        if not is_voicemail_greeting(text):
            return False

        logger.info(
            "voicemail_detected call=%s turn=%s — hanging up, leaving no message "
            "(text=%r)",
            str(call_id)[:12], turn_index, (text or "")[:80],
        )
        _flag_session_voicemail(call_id, voice_session)

        hung_up = False
        # Same route machine_detection uses: the media gateway maps the
        # pipeline UUID to the PBX channel. ARI DELETE /channels/<uuid> is a
        # 404 that reads as "already gone", so the call would stay up billed.
        hangup_call = getattr(
            getattr(voice_session, "media_gateway", None), "hangup_call", None
        )
        if callable(hangup_call):
            try:
                hung_up = bool(
                    await hangup_call(str(call_id), reason="voicemail_detected")
                )
            except Exception as gw_exc:
                logger.warning(
                    "voicemail_gateway_hangup_raised call=%s err=%s",
                    str(call_id)[:12], gw_exc,
                )
            if not hung_up:
                logger.error(
                    "voicemail_gateway_hangup_unconfirmed call=%s pbx=%s — media "
                    "gateway hangup_call returned False",
                    str(call_id)[:12], str(pbx_channel or "?")[:12],
                )
        if not hung_up and pbx_channel:
            # Fallback on the registry key (the PBX channel), never the UUID.
            try:
                from app.domain.services.telephony.adapter_registry import get_adapter

                adapter = get_adapter()
                confirmed = getattr(adapter, "hangup_confirmed", None)
                if callable(confirmed):
                    hung_up = bool(await confirmed(pbx_channel))
                elif adapter is not None:
                    await adapter.hangup(pbx_channel)
                    hung_up = True
                else:
                    logger.warning(
                        "voicemail_hangup_no_adapter call=%s — no bridge adapter "
                        "available",
                        str(call_id)[:12],
                    )
            except Exception as hang_exc:  # pragma: no cover - defensive
                logger.warning(
                    "voicemail_hangup_failed call=%s err=%s",
                    str(call_id)[:12], hang_exc,
                )
        if not hung_up:
            # Surface loudly rather than silently claiming success: the call
            # is flagged VOICEMAIL and the caller will stop processing this
            # transcript either way, but if the hangup itself did not happen
            # the call may still be live with the agent silent — that must be
            # catchable in logs/metrics, not indistinguishable from a clean
            # hangup (2026-07-14 fix).
            logger.error(
                "voicemail_hangup_failed_surfaced call=%s turn=%s — voicemail "
                "flagged but the call was NOT actually hung up",
                str(call_id)[:12], turn_index,
            )
        return True
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(
            "voicemail_detect_error call=%s err=%s", str(call_id)[:12], exc
        )
        return False
