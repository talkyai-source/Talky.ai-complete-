"""Answer the callee's first words on a caller-first call.

Agent-first calls speak the recording notice as the first audio of the
greeting (agent_first._send_outbound_greeting). Caller-first calls used to get
it the same way, right after the caller's first "Hello?", through the instant
opener; when that was disconnected on 2026-10-07 the notice went with it, so a
two-party tenant's recordings were all discarded
(recording_retention_suppressed ... tenant_policy_two_party_announce).
Owner decision 2026-10-21: play the notice again, then the agent's reply.

Owner decision 2026-11-05 (QA F12-03): on an OUTBOUND caller-first call the
reply to the callee's first words is the identity opener, as on agent-first
calls: "Hi, this is Sarah, an AI assistant from All State Estimation, on a
recorded line. Am I speaking with John?". Where the platform's own notice is
owed the opener says it, under the notice's proof rule; a tenant's own notice
is spoken first. The opener ends on a question, so the model does not also
answer that utterance. Inbound caller-first calls keep the notice and then the
model's reply. _speak_recording_disclosure keeps its own rules: once per call,
the policy decides whether a notice is owed, and an interrupted or unproven
notice still discards the recording.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# What the opener answers: pickup words ("Hello?", "Yes?", "Who's this?",
# "Hello, this is John"). Anything else -- an objection, a question about the
# call, an opt-out in any wording -- gets the normal turn, so the model's own
# opt-out and close handling sees it (security review of F12-03).
_PICKUP_WORDS = re.compile(
    r"^(?:\s*(?:hello|hullo|hi|hiya|hey|allo|yes|yeah|yep|speaking|there"
    r"|this\s+is\s+[\w'.-]+|[\w'.-]+\s+speaking"
    r"|who(?:'s|\s+is)\s+(?:this|that|it|calling)|who\s+are\s+you"
    r"|(?:how\s+)?(?:can|may)\s+i\s+help(?:\s+you)?|are\s+you\s+there|can\s+you\s+hear\s+me)"
    r"[\s,.!?-]*)+$",
    re.IGNORECASE,
)


def is_pickup_words(text: Optional[str]) -> bool:
    return bool(text and _PICKUP_WORDS.match(text.strip()))


async def speak_disclosure_before_first_reply(
    session, caller_text: Optional[str] = None, *, opener: bool = True,
) -> bool:
    """True when the identity opener was the reply to the caller's first words.

    Only pickup words get the opener. Anything else, or ``opener=False``
    (first words that opt out or say goodbye), gets only the notice, so the
    normal turn records an opt-out before the farewell.
    """
    if getattr(session, "_caller_first_disclosure_done", False):
        return False
    session._caller_first_disclosure_done = True
    voice_session = getattr(session, "_voice_session_ref", None)
    if voice_session is None:
        return False  # no telephony session (browser test agent): nothing to record
    from app.domain.models.conversation import Message, MessageRole
    from app.domain.services.telephony.config import (
        _build_call_greeting,
        outbound_opener_with_notice,
    )
    from app.domain.services.telephony.modes.agent_first import _speak_recording_disclosure

    # None on an inbound call, or for first words that are not pickup words.
    opener_with_notice = (
        outbound_opener_with_notice(session) if opener and is_pickup_words(caller_text) else None
    )
    if opener_with_notice is not None and (
        getattr(voice_session, "_recording_disclosure_attempted", False)
        or getattr(session, "_has_introduced", False)
        or getattr(voice_session, "_opener_spoken_as_notice", False)
    ):
        # Silent pickup already ran the greeting (notice and opener): the model
        # replies as usual. A turn-0 reprompt is not a greeting and does not count.
        return False
    if opener_with_notice is not None and caller_text:
        # Their words come before the opener, so the turn-0 floor treats their
        # answer to "Am I speaking with ...?" as an ordinary later turn.
        session.conversation_history.append(Message(role=MessageRole.USER, content=caller_text))
    if not getattr(voice_session, "_recording_disclosure_attempted", False):
        session._disclosure_in_flight = True
        try:
            await _speak_recording_disclosure(voice_session, opener_with_notice=opener_with_notice)
        finally:
            session._disclosure_in_flight = False
    if opener_with_notice is None:
        return False
    if getattr(voice_session, "_opener_spoken_as_notice", False):
        return True

    opener = _build_call_greeting(session, first_speaker="user")
    interrupted = await voice_session.pipeline.synthesize_and_send_audio(
        session, opener, websocket=None, track_latency=False,
    )
    if not interrupted and not getattr(session, "_tts_delivery_failed", False):
        session.conversation_history.append(Message(role=MessageRole.ASSISTANT, content=opener))
        session._has_introduced = True
        try:
            voice_session.pipeline.transcript_service.accumulate_turn(
                call_id=session.call_id,
                role="assistant",
                content=opener,
                talklee_call_id=getattr(session, "talklee_call_id", None),
                turn_index=getattr(session, "turn_id", 0),
                event_type="assistant_response",
                is_final=True,
                include_in_plaintext=True,
            )
        except Exception as exc:  # transcript bookkeeping must never break the call
            logger.debug("caller_first_opener_transcript_failed call_id=%s err=%s",
                         str(session.call_id)[:12], exc)
    return True
