"""Per-turn LLM+TTS execution with atomic conversation-history management.

Extracted from VoicePipelineService._run_turn (item 2, slice 4). Holds a
reference to the pipeline and reads its collaborators (_stream_llm_and_tts,
_supports_llm_end_session_action, _shutdown_session_for_end_action,
transcript_service) at CALL time — same pattern as TtsPlayback, so
attribute patching/mocking keeps working and the runtime path is
identical. The service keeps _run_turn() as a thin delegator (tests call
it directly).
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import is_dataclass, replace
from typing import Optional

from fastapi import WebSocket

from app.domain.models.conversation import Message, MessageRole
from app.domain.models.session import CallSession
from app.domain.services.end_session_action import (
    parse_end_session_action,
    should_honor_end_session,
    previous_assistant_turn,
)
from app.domain.services.voice_pipeline import capture_mode
from app.services.scripts import (
    CallState as CapturedSlotsState,
    update_state_from_user_turn,
)
from app.services.scripts.call_state_tracker import _classify_core_confirmation
from app.services.scripts.spoken_email_normalizer import (
    extract_email_from_agent_readback,
    extract_email_from_speech,
    extract_phone_from_speech,
    natural_email_readback,
    natural_phone_readback,
)
from app.domain.services.voice_pipeline.confirm_llm import llm_confirmation_verdict
from app.domain.services.voice_pipeline.identity_disposition import (
    IdentityDisposition,
    contains_dnc,
    contains_explicit_goodbye,
)

logger = logging.getLogger(__name__)

# Last resort when a phantom end-session is suppressed AND the model left the
# turn with nothing to speak: it tried to hang up, the caller never signalled
# they were done, and it emitted only the internal envelope. Keeps the call
# alive instead of leaving dead air or an unwanted goodbye.
#
# A TUPLE, not one string, because the guard can fire repeatedly on one call and
# the same canned sentence twice running is what a caller hears as the agent
# looping. Indexed by how often it has already fired, so it stays deterministic.
_PHANTOM_GOODBYE_RECOVERIES = (
    "Sorry, I'm still here — what else can I help you with?",
    "I'm still on the line. What else can I do for you?",
    "Still with you — was there anything else?",
)
# The canonical first line, kept under its old name for callers and log reading.
_PHANTOM_GOODBYE_RECOVERY = _PHANTOM_GOODBYE_RECOVERIES[0]

# Appended for ONE retry when a suppressed end-session left nothing to say. The
# canned line above answers no question the caller actually asked, so before
# falling back to it we tell the model the call is continuing and let it reply
# properly. Removed again immediately: it is scaffolding for this turn only.
_PHANTOM_RETRY_INSTRUCTION = (
    "SYSTEM: Your previous reply tried to end the call, but the caller has not "
    "finished and the call is continuing. Do not end the session and do not "
    "emit any JSON. Answer the caller's last message now, in one or two short "
    "spoken sentences."
)


def _spoken_remainder(response_text) -> str:
    """The part of a turn the caller actually HEARD.

    The streamer speaks prose up to the internal action envelope and swallows
    everything from that opening brace on (see
    ``turn_streamer._find_action_envelope_start``), so an EMPTY remainder means
    the model emitted only the envelope and the turn was silent. What to do
    about a suppressed hangup depends entirely on which of those two happened.
    """
    from app.domain.services.voice_pipeline.turn_streamer import (
        _find_action_envelope_start,
    )

    text = str(response_text or "")
    idx = _find_action_envelope_start(text)
    return (text if idx < 0 else text[:idx]).strip()


def _drop_last_message(history, content) -> None:
    """Remove the most recent message whose content is exactly ``content``."""
    for i in range(len(history) - 1, -1, -1):
        if getattr(history[i], "content", None) == content:
            del history[i]
            return


def _note_unheard_greeting_bargein(session) -> None:
    """A barge-in cancelled a turn before ANY audio reached the caller (issue #23).

    Keep interruption separate from delivery. The live prompt uses this count
    to follow the caller's latest words without restarting the opening; an
    unheard introduction must never become delivered identity evidence.
    """
    if getattr(session, "_has_introduced", False):
        return
    n = getattr(session, "_greeting_bargein_count", 0) + 1
    try:
        session._greeting_bargein_count = n
    except Exception:  # pragma: no cover - defensive
        pass


# The silence monitor speaks these; they are NOT read-backs and must be skipped
# when looking for the agent's real prior turn (else they mask the read-back).
_SILENCE_CHECK_RE = re.compile(
    r"\b(still\s+(there|with\s+me|on\s+the\s+line)|are\s+you\s+(still\s+)?there|"
    r"you\s+(still\s+)?there|can\s+you\s+hear\s+me|did\s+i\s+lose\s+you|lost\s+you|"
    r"you\s+on\s+the\s+line)\b",
    re.IGNORECASE,
)


def _last_agent_turn(history) -> str:
    """The most recent assistant line, interstitials included. Empty if none."""
    for m in reversed(history or []):
        if getattr(m, "role", None) == MessageRole.ASSISTANT:
            return str(getattr(m, "content", "") or "")
    return ""


def _same_utterance(a, b) -> bool:
    """True if two agent lines are the same sentence bar spacing, case and
    trailing punctuation -- i.e. the caller heard the identical thing twice."""
    def norm(t):
        t = re.sub(r"[^a-z0-9 ]+", " ", str(t or "").lower())
        return " ".join(t.split())

    na, nb = norm(a), norm(b)
    return bool(na) and na == nb


def _is_interstitial_agent_turn(text) -> bool:
    """True for an agent line the PIPELINE injected rather than the model wrote.

    Silence checks and the phantom-goodbye recovery lines are both spoken to keep
    a call alive; neither is a reply and neither is a read-back. Treating one as
    "the agent's most recent real turn" masks the read-back the caller is
    actually answering, so a correction or a "yes" lands against the wrong turn.
    Observed live on 2026-09-21: the recovery line hid an email read-back and the
    caller's corrected address was discarded.
    """
    c = str(text or "").lower()
    if _SILENCE_CHECK_RE.search(c):
        return True
    return any(line.lower() in c for line in _PHANTOM_GOODBYE_RECOVERIES)


def _is_email_correction(utterance, current_email) -> bool:
    """True if the caller restated a DIFFERENT email — a correction, which the
    capture path (not the confirmation path) handles. Used to skip the LLM
    confirmation call when the turn is actually a re-capture."""
    if not current_email:
        return False
    parsed = extract_email_from_speech(utterance)
    return bool(parsed and parsed != current_email)


# A turn that explicitly asks the caller to confirm ("did I get that right?").
# Required to promote a DOMAIN-ONLY match to a read-back (issue #4): naming the
# domain ("reach you at your gmail dot com address?") is NOT a read-back unless
# the turn also names the local part or asks for confirmation.
_CONFIRM_QUESTION_RE = re.compile(
    r"\b(did\s+i\s+(get|say|hear)\s+(that|it|this)|is\s+(that|this|it)\s+(right|correct)|"
    r"got\s+(that|it)\s+right|that\s+right\?|is\s+that\s+ok(ay)?\?|"
    r"sounds?\s+right|correct\?|right\?)",
    re.IGNORECASE,
)


def _is_phone_correction(utterance, current_phone) -> bool:
    """True if the caller restated a DIFFERENT phone number — a correction handled
    by the capture path, so we skip the confirmation classification for it."""
    if not current_phone:
        return False
    parsed = extract_phone_from_speech(utterance)
    return bool(parsed and parsed != current_phone)


# The agent asking to confirm the number the caller is already on:
# "Is this number the best one to reach you on?", "...on the number you're
# calling from?", "Can we reach you on this line?".
_THIS_NUMBER_RE = re.compile(
    r"\b(?:this|that|the\s+same)\s+(?:number|line)\b[^?]{0,80}\?"
    r"|\bnumber\s+(?:you'?re|you\s+are)\s+(?:calling\s+from|on|using)\b[^?]{0,60}\?",
    re.IGNORECASE,
)


def phone_on_the_table(pending, history):
    """Offer the number the call is on when the agent just asked to confirm it.

    2026-09-30: most callers can be reached on the number they're on, so the
    agent asks one yes/no instead of taking digits by voice. When the agent's
    latest turn is that question, the line number becomes the value the
    caller's yes confirms. Returns ``(pending_state, offered)``.
    """
    line = getattr(pending, "line_phone", None)
    if (
        not line
        or getattr(pending, "phone", None)
        or getattr(pending, "phone_confirmed", False)
    ):
        return pending, False
    for m in reversed(history or []):
        if getattr(m, "role", None) != MessageRole.ASSISTANT:
            continue
        text = m.content or ""
        if _is_interstitial_agent_turn(text.lower()):
            continue
        if _THIS_NUMBER_RE.search(text):
            return (
                replace(pending, phone=line, phone_confirmed=False, phone_readback_attempts=0),
                True,
            )
        return pending, False
    return pending, False


async def known_line_number(session) -> Optional[str]:
    """The E.164 number this call is on, or None. One small read per call.

    Outbound: the number that was dialled. Inbound: caller ID. Extensions,
    withheld numbers and anything that is not a real E.164 number give None,
    so the agent simply asks for a number as before.
    """
    try:
        from app.core.container import get_container
        from app.domain.services.phone_number_normalizer import normalize_phone_for_capture
        from app.domain.services.voice_pipeline.lead_slot_capture import resolve_call_binding

        binding = resolve_call_binding(session)
        call_id, tenant_id = binding.get("call_id"), binding.get("tenant_id")
        container = get_container()
        if not (call_id and tenant_id and getattr(container, "is_initialized", False)):
            return None
        from app.core.db_utils import acquire_with_tenant

        async with acquire_with_tenant(container.db_pool, str(tenant_id), timeout=0.5) as conn:
            row = await asyncio.wait_for(
                conn.fetchrow(
                    "SELECT direction, phone_number, caller_ani FROM calls "
                    "WHERE id = $1::uuid AND tenant_id = $2::uuid",
                    str(call_id), str(tenant_id),
                ),
                timeout=0.5,
            )
        if not row:
            return None
        raw = row["caller_ani"] if row["direction"] == "inbound" else row["phone_number"]
        raw = str(raw or "").strip()
        if not raw.startswith("+"):
            return None
        return normalize_phone_for_capture(raw, region=None)
    except Exception as exc:  # noqa: BLE001 - never block a turn on this
        logger.debug("known_line_number unavailable: %s", exc)
        return None


def email_on_the_table(pending, full_transcript, history):
    """The email the caller is answering about, and whether it was read back.

    The agent's LATEST read-back is the value on the table: the caller's yes
    to an address read back to them word for word is what makes it correct,
    not the deterministic parser. A confirmed email is never replaced, and a
    caller who just said a fresh address is parsed from their own words.
    Returns ``(pending_state, readback_issued)``.
    """
    pending_email = getattr(pending, "email", None)
    latest = None
    if (
        not getattr(pending, "email_confirmed", False)
        and extract_email_from_speech(full_transcript) is None
    ):
        latest = _email_from_recent_agent_readback(history)
        if latest and latest != pending_email:
            pending = replace(
                pending, email=latest, email_confirmed=False,
                email_readback_attempts=0,
            )
            pending_email = latest
            if _spelled_by_caller(latest, history) and pending.email_capture:
                # Every letter of it came from the caller's own recent words
                # ("Allstate estimation at Gmail dot com" + "one word"); the
                # agent only chose the separator they asked for. It is the
                # caller's, stored unconfirmed. Test call 5dfa4416 stored
                # nothing because this was marked agent-invented.
                pending = replace(
                    pending,
                    email_capture=replace(pending.email_capture, from_caller=True),
                )
    readback_issued = _agent_read_back_email(history, pending_email) or bool(
        latest and latest == pending_email
    )
    return pending, readback_issued


def _spelled_by_caller(email: str, history) -> bool:
    """True when the caller SAID this address in one of their last few turns:
    its name part is exactly the one to four words just before an "at", and
    its domain is the words after it ("dot" as the dot). Only the separator
    between the name words may differ -- which is what the agent asks about.
    """
    email = str(email or "").lower()
    if "@" not in email:
        return False
    local, domain = email.split("@", 1)
    want_local = re.sub(r"[^a-z0-9]", "", local)
    want_domain = re.sub(r"[^a-z0-9]", "", domain)
    if not want_local or not want_domain:
        return False
    said = [
        str(m.content or "").lower()
        for m in (history or [])[-8:]
        if getattr(m, "role", None) == MessageRole.USER
    ][-4:]
    for text in said:
        for word, digit in _READBACK_DIGIT_WORDS.items():
            text = re.sub(rf"\b{word}\b", digit, text)
        for sep in re.finditer(r"\s(?:at\s+the\s+rate|at)\s|@", text):
            before = re.findall(r"[a-z0-9]+", text[: sep.start()])
            after = re.findall(r"[a-z0-9]+", text[sep.end():])
            after = [w for w in after if w != "dot"]
            if "".join(after).startswith(want_domain) and any(
                "".join(before[-n:]) == want_local for n in range(1, 5)
            ):
                return True
    return False


def _email_from_recent_agent_readback(history):
    """Parse an ASSEMBLED email out of the agent's most recent REAL turn (gap #2).

    Only inspects the latest non-silence-check assistant turn — the read-back the
    caller is replying to right now — and returns the assembled address only when
    that turn is unmistakably a read-back-for-confirmation (see
    ``extract_email_from_agent_readback``). None otherwise.
    """
    for m in reversed(history or []):
        if getattr(m, "role", None) != MessageRole.ASSISTANT:
            continue
        c = m.content or ""
        if _is_interstitial_agent_turn(c):
            continue
        return extract_email_from_agent_readback(c)
    return None


_READBACK_DIGIT_WORDS = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
}


def _normalize_readback_words(text: str) -> str:
    normalized = str(text or "").lower()
    for word, digit in _READBACK_DIGIT_WORDS.items():
        normalized = re.sub(rf"\b{word}\b", digit, normalized)
    normalized = re.sub(r"[-‐‑‒–—]", " ", normalized)
    return " ".join(normalized.split())


def _agent_read_back_email(history, email) -> bool:
    """True if the agent's most recent REAL turn read the pending email back — so
    the caller's current turn can safely be interpreted as a confirmation reply.

    Robust to how the address is actually spoken: the agent voices separators and
    digits as words ("j dot smith", "seven eight"), so a literal match of the
    glyph-laden read-back string fails for dotted/underscored/digit local parts.
    We therefore also match the domain-as-words (always spoken the same way,
    e.g. "gmail dot com") — but ONLY when the local part is ALSO signalled, or the
    turn asks for confirmation. A bare domain mention ("reach you at your gmail
    dot com address?") does NOT verify the LOCAL part, so it must not let a "yeah"
    confirm an unheard local (issue #4). Silence-check turns are skipped so an
    interposed "are you still there?" can't mask the read-back (re-audit flow #1).
    """
    if not email or "@" not in email:
        return False
    spoken = natural_email_readback(email).lower()
    normalized_spoken = _normalize_readback_words(spoken)
    for m in reversed(history or []):
        if getattr(m, "role", None) != MessageRole.ASSISTANT:
            continue
        c = (m.content or "").lower()
        if _is_interstitial_agent_turn(c):
            continue  # not a real turn — keep scanning back for the read-back
        confirm_question = bool(_CONFIRM_QUESTION_RE.search(c))
        normalized_content = _normalize_readback_words(c)
        full_value = (
            bool(normalized_spoken) and normalized_spoken in normalized_content
        ) or (email.lower() in c)
        if full_value:
            return confirm_question
        return False
    return False


def _agent_read_back_phone(history, phone) -> bool:
    """True if the agent's most recent REAL turn read the pending phone number
    back. Matches on the digit string regardless of formatting (the agent may
    speak "555-123-4567", "5 5 5 …", or grouped), so a caller "yes" only counts
    once the digits were actually voiced. Silence checks are skipped."""
    if not phone:
        return False
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 7:
        return False
    spoken = natural_phone_readback(phone).lower()
    for m in reversed(history or []):
        if getattr(m, "role", None) != MessageRole.ASSISTANT:
            continue
        c = (m.content or "").lower()
        if _is_interstitial_agent_turn(c):
            continue
        c_digits = re.sub(r"\D", "", c)
        full_value = (digits in c_digits) or (bool(spoken) and spoken in c)
        return full_value and bool(_CONFIRM_QUESTION_RE.search(c))
    return False


def _unwrap_verdict(result, field: str) -> str:
    """Fail-closed unwrap of one branch of a gathered confirmation verdict.

    ``llm_confirmation_verdict`` already swallows provider errors and timeouts and
    returns 'unclear', so an Exception surfacing here means something outside its
    own guard went wrong — treat it the same way, leaving the value PENDING rather
    than guessing. CancelledError is NOT a failure: it is a barge-in cancelling the
    turn, so re-raise it and let ``run()`` unwind exactly as it did when the two
    calls were sequential.
    """
    if isinstance(result, asyncio.CancelledError):
        raise result
    if isinstance(result, BaseException):
        logger.debug(
            "%s_confirm verdict failed, failing closed to unclear: %s", field, result
        )
        return "unclear"
    return result


class TurnRunner:
    """Runs one user turn: append history → stream LLM+TTS → commit/rollback."""

    def __init__(self, pipeline) -> None:
        self._p = pipeline

    async def _recover_suppressed_turn(
        self,
        session: CallSession,
        websocket: Optional[WebSocket],
        response_text: str,
    ) -> str:
        """Return what the caller should end up having heard on a turn whose
        end-session action was suppressed.

        Three cases, in order of preference:

        1. The model wrote prose AND the envelope. The prose was already
           streamed to the caller, so the turn is complete — only the hangup
           needed suppressing. Speaking a canned line on top would talk over a
           finished answer with a non-sequitur.
        2. The model emitted ONLY the envelope, so the caller heard nothing at
           all. Ask once more, telling it the call is continuing. This is the
           case that cost a live caller their answer on 2026-09-21: the guard
           replaced the whole turn with a fixed sentence, so the question went
           unanswered and the correction it contained was lost.
        3. The retry also produced nothing. Fall back to the canned line, which
           at least keeps the call alive — varied per firing so a repeat guard
           does not read the identical sentence twice.
        """
        call_id = session.call_id

        spoken = _spoken_remainder(response_text)
        if spoken:
            logger.info(
                "phantom_goodbye_kept_prose call_id=%s chars=%d — answer already spoken",
                call_id, len(spoken),
            )
            return spoken

        retry_text = ""
        try:
            session.conversation_history.append(
                Message(role=MessageRole.SYSTEM, content=_PHANTOM_RETRY_INSTRUCTION)
            )
            try:
                retry_text, _, _ = await self._p._stream_llm_and_tts(session, websocket)
            finally:
                # Drop the nudge whether or not it worked — it is scaffolding
                # for this turn, not conversation the model should keep seeing.
                _drop_last_message(
                    session.conversation_history, _PHANTOM_RETRY_INSTRUCTION
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive
            logger.warning(
                "phantom_goodbye_retry_failed call_id=%s", call_id, exc_info=True
            )

        retry_spoken = _spoken_remainder(retry_text)
        if retry_spoken:
            logger.info(
                "phantom_goodbye_retry_spoke call_id=%s chars=%d", call_id, len(retry_spoken)
            )
            return retry_spoken

        fired = int(getattr(session, "_phantom_recovery_count", 0) or 0)
        line = _PHANTOM_GOODBYE_RECOVERIES[
            min(fired, len(_PHANTOM_GOODBYE_RECOVERIES) - 1)
        ]
        try:
            session._phantom_recovery_count = fired + 1
        except Exception:  # pragma: no cover - defensive
            pass
        logger.info(
            "phantom_goodbye_recovery_line call_id=%s fired=%d — retry produced nothing",
            call_id, fired + 1,
        )
        session.tts_active = True
        await self._p.synthesize_and_send_audio(
            session, line, websocket, track_latency=False,
        )
        return line

    async def run(
        self,
        session: CallSession,
        full_transcript: str,
        websocket: Optional[WebSocket] = None,
        turn_id: int = 0,
    ) -> tuple[str, float, float]:
        """
        Execute the LLM+TTS cycle for one user turn.

        Manages conversation history atomically:
        - User message is appended before LLM starts.
        - Rolled back on empty response, LLM error, or asyncio.CancelledError.
        - Assistant message is appended only when a non-empty response is produced.

        Returns (response_text, llm_latency_ms, tts_latency_ms).
        """
        call_id = session.call_id
        # The finisher also handles plain goodbye replies. Record when this
        # runner owns the close so the same turn cannot shut down twice.
        session._end_session_action_handled = False
        history_snapshot = len(session.conversation_history)
        session.conversation_history.append(
            Message(role=MessageRole.USER, content=full_transcript)
        )

        # This user turn is the one we may have relaxed STT for (e.g. they just
        # spelled an email). It has arrived, so revert to normal turn-detection.
        capture_mode.maybe_exit(getattr(self._p, "stt_provider", None), call_id)

        captured_slots = getattr(session, "captured_slots", None)
        if captured_slots is None or not is_dataclass(captured_slots):
            session.captured_slots = CapturedSlotsState()
        # Confirmation of a pending email only counts when the agent's last turn
        # actually read it back (see _agent_read_back_email). HYBRID classifier:
        # the fast deterministic regex resolves the clear cases with zero added
        # latency; only the ambiguous tail asks a small, tightly-bounded LLM —
        # fail-closed, so an unresolved verdict leaves the value pending.
        _pending = session.captured_slots
        _pending_email = getattr(_pending, "email", None)
        # Gap #2: a multi-word / carrier-prefixed spoken email never enters
        # CallState via the deterministic user-turn extractor (it refuses to guess
        # a word boundary), so the HARDEST emails bypassed the gate. When nothing
        # is pinned yet and this turn isn't itself a fresh email, seed the address
        # the AGENT assembled and read back in its prior turn as UNCONFIRMED — so
        # the SAME read-back → verdict → commit loop runs over it.
        #
        # 2026-09-29 (test call 56578fa2): this used to seed ONLY when nothing
        # was pending, so once a first read-back was pinned, every correction
        # the agent read back afterwards ("remove the dot" -> "allstateestimation
        # at gmail dot com, is that correct?") was ignored and the caller's
        # "yes" confirmed nothing. The agent's LATEST read-back is what the
        # caller is answering, so it is the value on the table -- whatever the
        # deterministic parser made of the earlier words. The caller's yes to a
        # value read back to them word for word is the guarantee; nothing is
        # persisted before it (lead_slot_capture keeps agent-assembled values
        # in memory until confirmed).
        if getattr(_pending, "line_phone", None) is None and not getattr(
            session, "_line_phone_checked", False
        ):
            session._line_phone_checked = True
            _line = await known_line_number(session)
            if _line:
                _pending = replace(_pending, line_phone=_line)
        _pending, _readback_issued = email_on_the_table(
            _pending, full_transcript, session.conversation_history
        )
        _pending, _line_offered = phone_on_the_table(_pending, session.conversation_history)
        session.captured_slots = _pending
        _pending_email = getattr(_pending, "email", None)
        # Phone / callback number — SAME gate as email, resolved independently.
        _pending_phone = getattr(_pending, "phone", None)
        _phone_readback_issued = _line_offered or _agent_read_back_phone(
            session.conversation_history, _pending_phone
        )

        _email_gate_open = bool(
            _pending_email
            and not getattr(_pending, "email_confirmed", False)
            and _readback_issued
            and not _is_email_correction(full_transcript, _pending_email)
        )
        _phone_gate_open = bool(
            _pending_phone
            and not getattr(_pending, "phone_confirmed", False)
            and _phone_readback_issued
            and not _is_phone_correction(full_transcript, _pending_phone)
        )

        # Fast deterministic pass first — pure, zero-latency, and identical for
        # both fields, so running it for BOTH gates up front changes nothing
        # except that the ambiguous tail is now known before any await.
        _confirm_verdict = (
            _classify_core_confirmation(full_transcript) if _email_gate_open else None
        )
        _phone_verdict = (
            _classify_core_confirmation(full_transcript) if _phone_gate_open else None
        )
        _email_via_llm = _confirm_verdict == "unclear"
        _phone_via_llm = _phone_verdict == "unclear"

        if _email_via_llm and _phone_via_llm:
            # LATENCY: these two bounded LLM calls used to run SEQUENTIALLY, so a
            # turn that was ambiguous on BOTH fields stacked two 1.5s timeouts —
            # up to 3s of dead air before the caller's real answer started
            # streaming. The fields are independent (neither verdict feeds the
            # other; both are applied together below), so resolve them
            # CONCURRENTLY: worst case is now one timeout, not two. Each call
            # keeps its own timeout and return_exceptions means one failing can
            # never abort or discard the other.
            _results = await asyncio.gather(
                llm_confirmation_verdict(
                    self._p.llm_provider, full_transcript, _pending_email
                ),
                llm_confirmation_verdict(
                    self._p.llm_provider, full_transcript, _pending_phone,
                    subject="phone number",
                ),
                return_exceptions=True,
            )
            _confirm_verdict = _unwrap_verdict(_results[0], "email")
            _phone_verdict = _unwrap_verdict(_results[1], "phone")
        elif _email_via_llm:
            # Single-field case: awaited directly — no gather/task overhead.
            _confirm_verdict = await llm_confirmation_verdict(
                self._p.llm_provider, full_transcript, _pending_email
            )
        elif _phone_via_llm:
            _phone_verdict = await llm_confirmation_verdict(
                self._p.llm_provider, full_transcript, _pending_phone,
                subject="phone number",
            )

        if _email_gate_open:
            logger.info(
                "email_confirm call=%s via_llm=%s verdict=%s",
                call_id[:8], _email_via_llm, _confirm_verdict,
            )
        if _phone_gate_open:
            logger.info("phone_confirm call=%s verdict=%s", call_id[:8], _phone_verdict)

        session.captured_slots = update_state_from_user_turn(
            _pending,
            full_transcript,
            readback_issued=_readback_issued,
            confirmation_verdict=_confirm_verdict,
            phone_readback_issued=_phone_readback_issued,
            phone_confirmation_verdict=_phone_verdict,
            phone_region=getattr(session, "contact_phone_region", None),
            # Flux deliberately supplies None. The state machine treats None as
            # "signal unavailable", never as low recognition confidence.
            transcript_confidence=getattr(
                session,
                "_active_turn_transcript_confidence",
                getattr(session, "_last_transcript_confidence", None),
            ),
            transcript_alternatives=getattr(
                session,
                "_active_turn_transcript_alternatives",
                getattr(session, "_last_transcript_alternatives", ()),
            ),
        )

        response_text = ""
        llm_latency_ms = 0.0
        tts_latency_ms = 0.0

        try:
            response_text, llm_latency_ms, tts_latency_ms = await self._p._stream_llm_and_tts(
                session, websocket
            )

            ask_ai_end_action = (
                parse_end_session_action(response_text)
                if self._p._supports_llm_end_session_action(session)
                else None
            )
            if ask_ai_end_action and ask_ai_end_action.get("do_not_call") and not contains_dnc(full_transcript):
                ask_ai_end_action = {**ask_ai_end_action, "do_not_call": False}

            # Phantom-goodbye guard: the model emitted an end-session action but
            # the caller never actually signalled they were done. Suppress the
            # hangup and keep the call going with a short re-engagement line.
            if ask_ai_end_action:
                user_turns = sum(
                    1 for m in session.conversation_history if m.role == MessageRole.USER
                )
                # Two declines = the persona legitimately closes (issue #16), so
                # honor end-session rather than re-opening with the recovery line.
                _declined = getattr(getattr(session, "captured_slots", None), "declined_count", 0)
                # F-15 fix (2026-07-20): this JSON end-session path is the OTHER
                # hangup gate, and it never consulted the deterministic
                # disposition — so a model that chose the JSON format instead of
                # the [[END_CALL]] sentinel bypassed turn_ender's wrong-person
                # reverse gate entirely and could hang up on a valid prospect.
                # Mirror that gate here: on a WRONG_PERSON turn (right business,
                # wrong person → pivot) suppress the hangup unless the caller
                # explicitly said goodbye. do_not_call is EXEMPT — a genuine
                # opt-out always ends (and a DNC utterance classifies as DNC,
                # not WRONG_PERSON, so this can never swallow an opt-out).
                _wrong_person_block = (
                    not ask_ai_end_action.get("do_not_call")
                    and getattr(session, "_turn_disposition", IdentityDisposition.NONE)
                    == IdentityDisposition.WRONG_PERSON
                    and not contains_explicit_goodbye(full_transcript)
                )
                if _wrong_person_block or not should_honor_end_session(
                    ask_ai_end_action, full_transcript, user_turns, declined_count=_declined,
                    previous_assistant_text=previous_assistant_turn(session.conversation_history),
                ):
                    logger.info(
                        "phantom_goodbye_suppressed call_id=%s reason=%s user_turns=%d "
                        "wrong_person_block=%s transcript_chars=%d — keeping call alive",
                        call_id, ask_ai_end_action.get("reason"), user_turns,
                        _wrong_person_block, len(full_transcript or ""),
                    )
                    # From here this is an ORDINARY turn. Dropping the action
                    # skips the shutdown path below and lets the shared
                    # post-turn block run, which is the point: the old early
                    # return hand-rolled its own history append and transcript
                    # write and therefore skipped update_state_from_agent_turn,
                    # _has_introduced AND capture_mode.maybe_enter. A suppressed
                    # turn that asked for an email never relaxed endpointing, so
                    # the caller's spell-out was cut off mid-address.
                    ask_ai_end_action = None
                    response_text = await self._recover_suppressed_turn(
                        session, websocket, response_text
                    )

            if ask_ai_end_action:
                # Compliance: caller asked never to be contacted again. Flag
                # the session so the call-end teardown runs the opt-out purge
                # (DNC + cancel scheduled jobs + mark lead DNC). We only set
                # the flag here; the side effects run once, at hangup.
                if ask_ai_end_action.get("do_not_call") and contains_dnc(full_transcript):
                    try:
                        session._caller_opted_out = True
                    except Exception:
                        pass
                    logger.info(
                        "caller_opt_out_detected call_id=%s — will purge at hangup",
                        getattr(session, "call_id", "?"),
                    )
                session._end_session_action_handled = True
                await self._p._shutdown_session_for_end_action(
                    session,
                    websocket,
                    ask_ai_end_action["reason"],
                    ask_ai_end_action["farewell"],
                )
                return "", llm_latency_ms, tts_latency_ms

            if response_text and response_text.strip():
                # The caller hearing the identical sentence twice running is the
                # single most obvious way the agent sounds broken. It cannot be
                # unspoken here -- the streamer already sent it -- so record it
                # where it can be counted per call rather than only heard.
                if _same_utterance(
                    _last_agent_turn(session.conversation_history), response_text
                ):
                    logger.warning(
                        "agent_repeated_turn call_id=%s turn=%s chars=%d — "
                        "identical to the previous agent line",
                        call_id, getattr(session, "turn_id", "?"), len(response_text),
                    )
                session.conversation_history.append(
                    Message(role=MessageRole.ASSISTANT, content=response_text)
                )
                from app.services.scripts.call_state_tracker import (
                    update_state_from_agent_turn,
                )

                session.captured_slots = update_state_from_agent_turn(
                    session.captured_slots,
                    response_text,
                )
                # The agent has now delivered a real reply — since 2026-08-11
                # that is the turn AFTER the bare pickup greeting, not turn 1
                # (turn 1 is TTS-only and runs no LLM call at all). Flip the
                # LIVE STATE flag so later turns are told NOT to re-introduce
                # (see prompts/live_state.py). Idempotent: harmless to re-set.
                session._has_introduced = True
                self._p.transcript_service.accumulate_turn(
                    call_id=call_id,
                    role="assistant",
                    content=response_text,
                    talklee_call_id=session.talklee_call_id,
                    turn_index=session.turn_id,
                    event_type="assistant_response",
                    is_final=True,
                    include_in_plaintext=True,
                )
                # If the agent just asked for an email / to spell something,
                # relax STT for the caller's upcoming spell-out turn.
                capture_mode.maybe_enter(
                    getattr(self._p, "stt_provider", None), call_id, response_text
                )
            else:
                logger.warning(
                    f"Empty LLM response for call {call_id} — rolling back user message"
                )
                session.conversation_history = session.conversation_history[:history_snapshot]

        except asyncio.CancelledError:
            # P3: a barge-in cancels the turn mid-reply. If the agent actually
            # spoke some sentences before the interrupt, KEEP the user turn and
            # commit ONLY what the caller heard (+ marker) so the model has
            # correct context. Discarding it (or committing the full unheard
            # reply) is what produced "absurd" replies after a few interrupts.
            spoken = " ".join(getattr(session, "_spoken_sentences", []) or []).strip()
            if spoken:
                # Keep the user message (at history_snapshot), drop anything the
                # cancelled task appended after it, then add the spoken partial.
                session.conversation_history = session.conversation_history[:history_snapshot + 1]
                session.conversation_history.append(
                    Message(role=MessageRole.ASSISTANT, content=spoken + " [interrupted by caller]")
                )
                # The agent spoke a partial reply (possibly its opening), so it
                # counts as introduced — don't make it re-introduce next turn.
                session._has_introduced = True
                # Tell handle_barge_in we already committed the correct partial,
                # so it does NOT roll it back or double-annotate.
                session._speculative_history_len = None
            else:
                # KEEP the user message (at history_snapshot) — dropping it
                # discarded the caller's own words permanently. Each Flux
                # EndOfTurn dispatches only its own segment (turn_ender.py
                # builds full_transcript fresh per turn from just that
                # segment), so nothing downstream ever re-queues or merges a
                # rolled-back fragment into a later turn: once dropped here it
                # was gone for the rest of the call. On a caller who speaks in
                # fragments this produced half-heard TTS AND an agent that
                # answers as though the caller never spoke (51450718: "I want
                # full body checkup" never engaged with, "which day" asked 3x;
                # b97ce4c5: 8 replies cancelled, agent later said "I'm sorry I
                # missed that" to a caller who explained their problem twice).
                # Only discard what THIS cancelled task appended after the
                # user's own message.
                session.conversation_history = session.conversation_history[:history_snapshot + 1]
                # Nothing was heard. If this keeps happening on the opening, stop
                # looping the intro (issue #23).
                _note_unheard_greeting_bargein(session)
            raise
        except Exception as e:
            logger.error(f"Turn error for call {call_id}: {e}", exc_info=True)
            session.conversation_history = session.conversation_history[:history_snapshot]

        return response_text, llm_latency_ms, tts_latency_ms
