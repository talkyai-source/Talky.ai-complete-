"""Compose the per-turn system prompt.

Per Groq 2026 prompting docs, the model weighs early tokens most heavily,
so the CAPTURED block lives at the very top of the system message. The
static persona/style rules follow. An 8B model is far less likely to
re-ask for data it can see stated as a fact in the first 200 tokens of
its own system message.
"""
from __future__ import annotations

from typing import Optional

from app.domain.services.voice_pipeline.contact_capture import CaptureStatus
from app.services.scripts.call_state_tracker import CallState
from app.services.scripts.spoken_email_normalizer import (
    natural_email_readback,
    natural_phone_readback,
)

# The runtime passes the actual campaign capability. With no executor, a
# preferred time is only a request; team handoff also needs a real route.
_NO_CALLBACK_EXECUTOR_POLICY = (
    "CALLBACK POLICY: No callback scheduling executor is available. You may "
    "ask for and note the caller's preferred callback day or time as a request, "
    "never as a scheduled callback. Offer a team follow-up only if the runtime "
    "provides that route, and describe only its returned status. Otherwise say "
    "you cannot schedule a callback from this call; do not promise that details "
    "will be passed on or that someone will follow up. A preferred appointment "
    "time is also just a request until an available booking tool confirms it.\n"
    "------------------------------------------------------------\n"
)


def _pending_actions(state: CallState) -> tuple[list[str], list[str]]:
    """The contact actions still open this turn, and call details to keep
    in hand. Shared by the prompt header and the trailing turn directive."""
    if state.contact_capture_paused:
        return [], []
    pending: list[str] = []
    # Facts to use only when the moment comes -- not an action for this turn.
    call_details: list[str] = []
    for capture in (state.email_capture, state.phone_capture):
        if capture is None or capture.status not in {
            CaptureStatus.NEEDS_CLARIFICATION,
            CaptureStatus.INVALID,
        }:
            continue
        queued = (
            state.active_contact_kind is not None
            and capture.kind != state.active_contact_kind
        )
        if state.contact_ask_objections and not capture.normalized_value:
            # The caller objected to being asked. An unresolved address from
            # before that is not something to keep chasing.
            continue
        instruction = capture.clarification_prompt or (
            "Please ask the caller to repeat that contact detail clearly."
        )
        candidate = (
            f" Current candidate (not confirmed): {capture.normalized_value}."
            if capture.normalized_value
            else ""
        )
        if queued:
            # Settled one at a time, but never forgotten: 5dfa4416 lost a
            # phone request that arrived while the email was being confirmed.
            pending.append(
                f"- Once that is settled, next: {instruction}{candidate}"
            )
            continue
        pending.append(f"- {instruction}{candidate} Do not save or rely on it yet.")

    if (
        state.email
        and not state.email_confirmed
        and (
            state.email_capture is None
            or state.email_capture.status is CaptureStatus.AWAITING_CONFIRMATION
        )
        and state.active_contact_kind in {None, "email"}
    ):
        readback = natural_email_readback(state.email)
        if state.email_readback_attempts >= 2:
            # Bounded fallback: don't keep re-reading the same value forever.
            pending.append(
                "- You've tried a few times to confirm the caller's email without a "
                "clear yes. Change tack: offer to take it a different way — ask them "
                "to spell only the unclear part, or offer another approved channel "
                "if available. Otherwise leave it unconfirmed and move on. Do not "
                f"keep re-reading the same value: {state.email}"
            )
        else:
            # Payload-first single imperative (2026-07-02 A/B: both menu models
            # reproduced the exact read-back 4/4 with no letter-spelling). The
            # old 78-word run-on with dueling NATURALLY/EXACTLY sometimes
            # stalled the confirm loop.
            if readback:
                pending.append(
                    f'- Say EXACTLY: "So that\'s {readback} — did I get that '
                    f'right?" Then stop and wait for their answer. Treat the '
                    f"email as final only once they say yes; if they correct it, "
                    f"capture the new value they give: {state.email}"
                )
            else:
                pending.append(
                    "- Read the caller's email back to them as natural spoken "
                    "words and ask if you got it right. Treat it as final only "
                    f"once they say yes: {state.email}"
                )

    # Phone / callback number — SAME confirm-before-commit surfacing as email.
    if (
        state.phone
        and not state.phone_confirmed
        and (
            state.phone_capture is None
            or state.phone_capture.status is CaptureStatus.AWAITING_CONFIRMATION
        )
        and state.active_contact_kind in {None, "phone"}
    ):
        readback = natural_phone_readback(state.phone)
        if state.phone_readback_attempts >= 2:
            pending.append(
                "- You've tried a few times to confirm the caller's phone number "
                "without a clear yes. Change tack: ask them to say it once more "
                "slowly digit by digit, or offer another approved channel if available. "
                f"Otherwise leave it unconfirmed. Do not keep re-reading the same value: {state.phone}"
            )
        elif readback:
            pending.append(
                f'- Say EXACTLY: "So that\'s {readback} — did I get that right?" '
                f"Then stop and wait for their answer. Treat the number as final "
                f"only once they say yes; if they correct it, capture the new value "
                f"they give: {state.phone}"
            )
        else:
            pending.append(
                "- Read the caller's phone number back to them digit by digit and "
                "ask if you got it right. Treat it as final only once they say "
                f"yes: {state.phone}"
            )
    elif (
        not state.phone
        and not state.phone_confirmed
        and getattr(state, "line_phone", None)
        and state.active_contact_kind in {None, "phone"}
    ):
        # 2026-09-30: confirm the number the call is already on instead of
        # taking digits by voice -- the common case needs one yes/no.
        call_details.append(
            "- The caller is on "
            f"{natural_phone_readback(state.line_phone)}. If you need a number to "
            'reach them, ask: "Is this number the best one to reach you on?" '
            "Only ask them to say a number if they say no."
        )
    # A read-back waiting behind the other contact is queued, not dropped.
    for kind, value, confirmed, speak in (
        ("email", state.email, state.email_confirmed, natural_email_readback),
        ("phone", state.phone, state.phone_confirmed, natural_phone_readback),
    ):
        capture = getattr(state, f"{kind}_capture")
        if (
            value
            and not confirmed
            and state.active_contact_kind not in {None, kind}
            and (capture is None or capture.status is CaptureStatus.AWAITING_CONFIRMATION)
        ):
            spoken = speak(value) or value
            pending.append(
                f'- Once that is settled, next: confirm their {kind} -- say '
                f'"So that\'s {spoken} -- did I get that right?"'
            )
    # The action for THIS turn first; queued ones after it.
    pending.sort(key=lambda line: line.startswith("- Once that is settled"))
    return pending, call_details


def turn_directive(state: Optional[CallState]) -> Optional[str]:
    """The one open contact action, worded to be read LAST, after the caller's
    latest words -- or None.

    Test call 5dfa4416 (2026-09-29): the pending action sat in the system
    prompt above ~12k tokens of script and knowledge, and the model followed
    the script's next line instead ("What time works best for you?") three
    turns running. The last thing read is what gets done.
    """
    if state is None:
        return None
    pending, _details = _pending_actions(state)
    if not pending:
        return None
    first = pending[0].lstrip("- ").strip()
    return (
        "Before anything else in this reply -- ahead of the next line of your "
        f"script -- do this: {first} Do not say goodbye while this is open."
    )


def with_turn_directive(messages: list, directive: Optional[str]) -> list:
    """``messages`` with ``directive`` added to the caller's latest message as
    a clearly marked note, so it is the last thing the model reads.

    A trailing system-role message was the first idea; it breaks the Groq
    GPT-OSS path (which keeps system roles out on purpose) and Gemini folds it
    into the caller's words unmarked. A marked note on the user turn is read
    the same way by every provider. The stored history is not touched.
    """
    if not directive or not messages:
        return messages
    from app.domain.models.conversation import MessageRole

    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if getattr(m, "role", None) == MessageRole.USER:
            note = (
                f"{m.content}\n\n(Note for you, the agent -- the caller did not "
                f"say this: {directive})"
            )
            return [*messages[:i], m.model_copy(update={"content": note}), *messages[i + 1:]]
    return messages


def compose_system_prompt(
    base_prompt: str,
    state: CallState,
    *,
    has_callback_executor: bool = False,
) -> str:
    """Return base_prompt with a CAPTURED-slots header prepended when state
    has any filled slot; otherwise return base_prompt unchanged.

    The header is deterministic and short (<= 120 tokens) so it never
    crowds out the persona rules.

    ``has_callback_executor`` defaults to False. The live turn path passes the
    resolved campaign capability. When False, CALLBACK POLICY distinguishes
    a preferred time from real scheduling; when True, tool receipts govern.
    """
    # Confirm-before-commit (issue #1): only a CONFIRMED email is a settled
    # "do not re-ask" CAPTURED fact. An unconfirmed email is surfaced as an
    # action-this-turn: read it back, confirm, and do NOT save it until the
    # caller says yes. This stops a first-utterance mishear being locked as truth.
    pending, call_details = _pending_actions(state)

    lines: list[str] = []
    for earlier in getattr(state, "earlier_email_captures", ()) or ():
        lines.append(
            "- Caller email (confirmed earlier; they asked you to take "
            f"another one as well): {earlier.normalized_value}."
        )
    for earlier in getattr(state, "earlier_phone_captures", ()) or ():
        lines.append(
            "- Caller phone number (confirmed earlier; they asked you "
            f"to take another one as well): {earlier.normalized_value}."
        )
    if state.email and state.email_confirmed:
        readback = natural_email_readback(state.email)
        say = f' If you read it back, say it naturally as EXACTLY: "{readback}".' if readback else ""
        lines.append(
            "- Caller email (confirmed — use this EXACT value, never re-transcribe "
            f"what you heard): {state.email}.{say} Do not spell it letter by "
            "letter unless the caller asks."
        )
    if state.phone and state.phone_confirmed:
        readback = natural_phone_readback(state.phone)
        say = f' If you read it back, say it digit by digit as: "{readback}".' if readback else ""
        lines.append(
            "- Caller phone/callback number (confirmed — use this EXACT value, never "
            f"re-transcribe what you heard): {state.phone}.{say}"
        )
    if state.follow_up:
        lines.append(
            f"- Caller-requested follow-up time (not scheduled): {state.follow_up}"
        )
    if state.bidding_active is True:
        lines.append("- Caller confirmed they are actively bidding on projects.")
    elif state.bidding_active is False:
        lines.append("- Caller said they are NOT actively bidding right now.")
    if state.declined_count >= 2:
        lines.append(
            "- Caller has declined twice. Close politely and end the call."
        )

    # Conduct this turn. Deterministic, derived from what the caller actually
    # said, and placed FIRST so it outranks an operator goal such as "capture
    # email on every call" -- which is what drove call 2427af7e to ask for an
    # email before answering anything and to keep asking after three
    # objections.
    conduct: list[str] = []
    if state.contact_ask_objections:
        conduct.append(
            "- The caller objected to being asked for contact details. Do NOT "
            "ask for their email or phone number again. Only take one if they "
            "offer it or ask you to send them something."
        )
    elif state.contact_capture_paused:
        conduct.append(
            "- Contact confirmation is paused at the caller's request. Do not "
            "read back or ask for contact details unless they volunteer them "
            "or explicitly resume. Existing candidates remain unconfirmed. "
            "If they said goodbye, close without another question."
        )
    contact_given = bool(state.email or state.phone)
    if state.caller_asked_question and not contact_given:
        conduct.append(
            "- The caller just asked you a question. Answer it directly and "
            "specifically, from what you know, as the first thing you say. Do "
            "not ask for contact details in this reply."
        )

    blocks: list[str] = []
    if conduct:
        blocks.append(
            "ACTION THIS TURN:\n"
            + "\n".join(conduct)
            + "\n"
            + "------------------------------------------------------------\n"
        )
    if pending:
        blocks.append(
            "ACTION THIS TURN — confirm before you rely on it:\n"
            + "\n".join(pending)
            + "\n"
            + "------------------------------------------------------------\n"
        )
    if call_details:
        blocks.append(
            "CALL DETAILS (use only when you need them):\n"
            + "\n".join(call_details)
            + "\n"
        )
    if lines:
        blocks.append(
            "CAPTURED (facts from this call — these are TRUE, "
            "do not re-ask, do not contradict):\n"
            + "\n".join(lines)
            + "\n"
            + "If a CAPTURED fact exists, acknowledge it and move on — never "
              "ask the same question again.\n"
            + "------------------------------------------------------------\n"
        )

    # Standing constraint, not tied to any captured slot -- see the constant's
    # own comment. Appended after CAPTURED so that block still leads the
    # message when both are present.
    if not has_callback_executor:
        blocks.append(_NO_CALLBACK_EXECUTOR_POLICY)

    if not blocks:
        return base_prompt
    return "".join(blocks) + base_prompt
