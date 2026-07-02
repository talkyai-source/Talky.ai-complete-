"""Compose the per-turn system prompt.

Per Groq 2026 prompting docs, the model weighs early tokens most heavily,
so the CAPTURED block lives at the very top of the system message. The
static persona/style rules follow. An 8B model is far less likely to
re-ask for data it can see stated as a fact in the first 200 tokens of
its own system message.
"""
from __future__ import annotations

from app.services.scripts.call_state_tracker import CallState
from app.services.scripts.spoken_email_normalizer import (
    natural_email_readback,
    natural_phone_readback,
)


def compose_system_prompt(base_prompt: str, state: CallState) -> str:
    """Return base_prompt with a CAPTURED-slots header prepended when state
    has any filled slot; otherwise return base_prompt unchanged.

    The header is deterministic and short (<= 120 tokens) so it never
    crowds out the persona rules.
    """
    # Confirm-before-commit (issue #1): only a CONFIRMED email is a settled
    # "do not re-ask" CAPTURED fact. An unconfirmed email is surfaced as an
    # action-this-turn: read it back, confirm, and do NOT save it until the
    # caller says yes. This stops a first-utterance mishear being locked as truth.
    pending: list[str] = []
    if state.email and not state.email_confirmed:
        readback = natural_email_readback(state.email)
        if state.email_readback_attempts >= 3:
            # Bounded fallback: don't keep re-reading the same value forever.
            pending.append(
                "- You've tried a few times to confirm the caller's email without a "
                "clear yes. Change tack: offer to take it a different way — ask them "
                "to spell it slowly one letter at a time, or offer to confirm it by "
                "text/another channel, or note it and move on to follow up. Do not "
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
    if state.phone and not state.phone_confirmed:
        readback = natural_phone_readback(state.phone)
        if state.phone_readback_attempts >= 3:
            pending.append(
                "- You've tried a few times to confirm the caller's phone number "
                "without a clear yes. Change tack: ask them to say it once more "
                "slowly digit by digit, or offer to confirm it another way, or note "
                f"it and move on. Do not keep re-reading the same value: {state.phone}"
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

    lines: list[str] = []
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
            f"- Follow-up time (already agreed): {state.follow_up}"
        )
    if state.bidding_active is True:
        lines.append("- Caller confirmed they are actively bidding on projects.")
    elif state.bidding_active is False:
        lines.append("- Caller said they are NOT actively bidding right now.")
    if state.declined_count >= 2:
        lines.append(
            "- Caller has declined twice. Close politely and end the call."
        )

    blocks: list[str] = []
    if pending:
        blocks.append(
            "ACTION THIS TURN — confirm before you rely on it:\n"
            + "\n".join(pending)
            + "\n"
            + "------------------------------------------------------------\n"
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

    if not blocks:
        return base_prompt
    return "".join(blocks) + base_prompt
