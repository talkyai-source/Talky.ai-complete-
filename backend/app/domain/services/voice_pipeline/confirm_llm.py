"""LLM fallback for CORE-field (email) read-back confirmation — the ambiguous tail.

The deterministic regex classifier (`_classify_core_confirmation`) resolves the
clear cases instantly (0 added latency). For the ambiguous minority it returns
'unclear'; this module asks a small, tightly-bounded LLM call to resolve just
those — semantic understanding for the phrasings regex can't enumerate.

FAIL-CLOSED by contract: a disabled flag, missing provider, timeout, error, or an
unrecognised answer all return 'unclear', so the value stays PENDING (re-read
back) — the fallback can only UPGRADE an ambiguous case to a confident verdict,
never corrupt a clear one. Bounded by a short timeout so it can't hang the call.
"""
from __future__ import annotations

import asyncio
import logging
import os

from app.domain.models.conversation import Message, MessageRole

logger = logging.getLogger(__name__)

# Toggle (default on) + tight timeout so the fallback only adds latency on the
# rare ambiguous confirmation turn and never hangs the live call.
LLM_CONFIRM_FALLBACK_ENABLED = os.getenv(
    "EMAIL_CONFIRM_LLM_FALLBACK", "true"
).strip().lower() in {"1", "true", "yes", "on"}
_TIMEOUT_S = float(os.getenv("EMAIL_CONFIRM_LLM_TIMEOUT_S", "1.5"))

_SYSTEM = (
    "You classify a caller's reply on a phone call. The agent just read an email "
    "address back and asked the caller to confirm it is correct. Decide ONLY "
    "whether the caller confirmed the address is correct.\n"
    "- yes: a clear, unreserved confirmation that the address is correct.\n"
    "- no: they said it is wrong or needs ANY change, including a partial "
    "correction.\n"
    "- unclear: anything else. If the reply contains any hedge, reservation, "
    "condition, or you are at all torn, answer unclear — never guess yes.\n"
    "Examples:\n"
    '- "yeah you got it" -> yes\n'
    '- "yes but that is my old email" -> no\n'
    '- "close, it is dot smith not smith" -> no\n'
    '- "uh hold on a second" -> unclear\n'
    'Reply with exactly one word: "yes", "no", or "unclear".'
)


async def llm_confirmation_verdict(provider, utterance: str, email: str) -> str:
    """Return 'affirm' | 'reject' | 'unclear' for an ambiguous read-back reply.

    Fail-closed: 'unclear' on disabled flag / missing provider / timeout / error /
    unrecognised answer.
    """
    if not LLM_CONFIRM_FALLBACK_ENABLED or provider is None or not (utterance or "").strip():
        return "unclear"

    user = (
        f'The agent read back the email "{email}" and asked if it is correct. '
        f'The caller replied: "{utterance}". Did the caller confirm the address is '
        f"correct? Answer yes, no, or unclear."
    )
    messages = [Message(role=MessageRole.USER, content=user)]
    try:
        buf = ""

        async def _collect() -> None:
            nonlocal buf
            async for tok in provider.stream_chat_with_timeout(
                messages, system_prompt=_SYSTEM, max_tokens=3, temperature=0.0
            ):
                buf += tok
                if len(buf) > 24:  # a one-word answer is here well before this
                    break

        await asyncio.wait_for(_collect(), timeout=_TIMEOUT_S)
        # Exact label match only (strip quotes/punctuation). Prefix matching is
        # unsafe here: "not sure" starts with "no" but is NOT a rejection.
        t = buf.strip().lower().strip("\"'.!, \t\n")
        if t == "yes":
            return "affirm"
        if t == "no":
            return "reject"
        return "unclear"
    except Exception as exc:  # timeout / provider error — fail closed
        logger.debug("llm_confirmation_verdict fell back to unclear: %s", exc)
        return "unclear"
