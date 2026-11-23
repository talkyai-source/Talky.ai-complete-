"""The one sentence that tells an outgoing call's person it is an AI.

On a caller-first outgoing call the opening ("Hi, this is Sarah, an AI assistant
from Acme, on a recorded line. Who am I speaking with?") is said only when the
person's first words are a greeting. For any other first words, or a phone's
call-screening assistant, only the recording notice was said and the model then
answered in its own words; on 23 Nov 2026 it never once said it is an AI (0 of 31
replies). This module builds the sentence that now goes first in those cases:

    "This is Sarah, an AI assistant from Acme, on a recorded line."

The words come from ONE clause (``identity_clause``) that is also used for the
call-screening message; a test pins it to ``build_identity_opener`` so the two
cannot drift apart. Incoming calls are never touched here.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_ATTEMPTED_ATTR = "_first_line_identity_attempted"


def identity_clause(
    agent_name: Optional[str], company_name: Optional[str], *, company_fallback: bool = True,
) -> str:
    """"this is Sarah, an AI assistant from Acme" (lower case, no end punctuation).

    Same words as the clause inside ``build_identity_opener``. No agent name:
    "this is an AI assistant from Acme". No company: the opener's placeholder,
    unless ``company_fallback`` is False (the screening message), where the
    company part is simply left out.
    """
    agent = (agent_name or "").strip()
    company = (company_name or "").strip()
    if not company and company_fallback:
        from app.domain.services.telephony_session_config import TELEPHONY_COMPANY_NAME

        company = TELEPHONY_COMPANY_NAME
    tail = f" from {company}" if company else ""
    return f"this is {agent}, an AI assistant{tail}" if agent else f"this is an AI assistant{tail}"


def identity_line(agent_name: Optional[str], company_name: Optional[str], *, recorded_line: bool) -> str:
    """One sentence: "This is Sarah, an AI assistant from Acme[, on a recorded line]."."""
    clause = identity_clause(agent_name, company_name)
    return f"{clause[:1].upper()}{clause[1:]}{', on a recorded line' if recorded_line else ''}."


def outbound_identity_line(session, *, recorded_line: bool) -> Optional[str]:
    """The line for this call's names, or None on an incoming call.

    Names are taken the way the opening takes them (``_build_call_greeting``).
    """
    from app.domain.services.telephony.config import _call_direction, _resolve_greeting_context

    if _call_direction(session) == "inbound":
        return None
    _placeholder_name, company = _resolve_greeting_context(session)
    agent_config = getattr(session, "agent_config", None)
    agent = getattr(agent_config, "agent_name", None) if agent_config else None
    return identity_line(agent, company, recorded_line=recorded_line)


def _log(session, said: str, notice: Optional[str], delivered: bool) -> None:
    # notice= is the notice ledger's state as it stands when this line is written
    # (spoken / transmitted / failed / not_required / none); "failed" covers both a
    # notice that was cut off and one that could not be decided.
    logger.info("first_line_identity call_id=%s said=%s notice=%s delivered=%s",
                str(getattr(session, "call_id", ""))[:12], said, notice or "none",
                "true" if delivered else "false")


async def say_outbound_identity_line(session, voice_session) -> None:
    """Right after the notice step of an outgoing call's non-greeting first turn,
    which was handed the identity sentence: make sure it has been said, once.

    - the notice step said it as the notice (platform wording): only the log line;
    - the notice step said the account's own sentence, or failed: the plain sentence after it;
    - no notice owed: the plain sentence alone.
    Called only when the opening was NOT spoken on this call
    (caller_first_disclosure decides that), and never twice (a cut-off attempt is
    not restarted). Recorded and ``_has_introduced`` only when delivered.
    """
    line = outbound_identity_line(session, recorded_line=False)
    if line is None or getattr(session, _ATTEMPTED_ATTR, False):
        return
    session._first_line_identity_attempted = True

    from app.domain.models.conversation import Message, MessageRole
    from app.domain.services.recording_policy_service import DISCLOSURE_NOT_REQUIRED, get_disclosure_state
    from app.domain.services.voice_pipeline.premade_opener import premade_kwargs

    state = get_disclosure_state(getattr(voice_session, "call_id", None), getattr(session, "call_id", None))
    if getattr(voice_session, "_opener_spoken_as_notice", False):
        # The notice step spoke it; that step recorded it when it was delivered.
        _log(session, "in_notice", state, bool(getattr(session, "_has_introduced", False)))
        return
    if getattr(session, "_has_introduced", False):
        return
    # after_notice only when a notice step really decided and spoke (or tried to):
    # a step that failed before deciding (policy unreadable) said nothing.
    said = (
        "after_notice"
        if state not in (None, DISCLOSURE_NOT_REQUIRED) and getattr(voice_session, "_recording_policy_decided", False)
        else "alone"
    )
    session._tts_delivery_failed = False
    interrupted = await voice_session.pipeline.synthesize_and_send_audio(
        session, line, websocket=None, track_latency=False,
        **premade_kwargs(voice_session, session, line),
    )
    delivered = not interrupted and not getattr(session, "_tts_delivery_failed", False)
    _log(session, said, state, delivered)
    if not delivered:
        return
    session.conversation_history.append(Message(role=MessageRole.ASSISTANT, content=line))
    session._has_introduced = True
    try:
        voice_session.pipeline.transcript_service.accumulate_turn(
            call_id=session.call_id,
            role="assistant",
            content=line,
            talklee_call_id=getattr(session, "talklee_call_id", None),
            turn_index=getattr(session, "turn_id", 0),
            event_type="assistant_response",
            is_final=True,
            include_in_plaintext=True,
        )
    except Exception as exc:  # transcript bookkeeping must never break the call
        logger.debug("first_line_identity_transcript_failed call_id=%s err=%s",
                     str(session.call_id)[:12], exc)
