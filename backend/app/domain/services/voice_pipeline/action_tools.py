"""Deterministic action-tool contract shared by both live voice pipelines.

The voice agent must never turn prose such as "I'll email that" into an
imaginary side effect.  Every action in this module returns the same bounded,
machine-readable result shape.  A model may describe an action as completed
only when ``confirmation_allowed`` is true in that result.

Callback scheduling, email delivery, form submission, and controlled transfer
do not currently have a live executor in the voice runtime, so they fail
closed.  ``end_call`` is the sole executable action: it records an accepted
request on the existing session flag; the normal turn finisher performs the
PBX hangup after the model's short closing line has played.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable, Mapping
from typing import Any

from app.domain.models.conversation import MessageRole

logger = logging.getLogger(__name__)

ACTION_SCHEDULE_CALLBACK = "schedule_callback"
ACTION_SEND_EMAIL = "send_email"
ACTION_SUBMIT_FORM = "submit_form"
ACTION_TRANSFER_CALL = "transfer_call"
ACTION_END_CALL = "end_call"

VOICE_ACTION_NAMES = (
    ACTION_SCHEDULE_CALLBACK,
    ACTION_SEND_EMAIL,
    ACTION_SUBMIT_FORM,
    ACTION_TRANSFER_CALL,
    ACTION_END_CALL,
)

_ACTION_DESCRIPTIONS = {
    ACTION_SCHEDULE_CALLBACK: (
        "Schedule a future callback. Call this before saying a callback was "
        "scheduled, booked, arranged, or confirmed."
    ),
    ACTION_SEND_EMAIL: (
        "Send information by email. Call this before saying an email or other "
        "information was sent or delivered."
    ),
    ACTION_SUBMIT_FORM: (
        "Submit a form or application. Call this before saying a form, request, "
        "or application was submitted."
    ),
    ACTION_TRANSFER_CALL: (
        "Transfer this live call to another person. Call this before saying a "
        "transfer or connection has started."
    ),
    ACTION_END_CALL: (
        "Request that this live call end after a short goodbye. Use only when "
        "the caller clearly ended the conversation; do not claim the line is "
        "already disconnected while you are still speaking."
    ),
}

_ACTION_PARAMETERS = {
    ACTION_SCHEDULE_CALLBACK: {
        "type": "object",
        "properties": {
            "requested_time": {
                "type": "string",
                "description": "The caller's requested callback time, if known.",
            }
        },
        "additionalProperties": False,
    },
    ACTION_SEND_EMAIL: {
        "type": "object",
        "properties": {
            "recipient": {
                "type": "string",
                "description": "The confirmed recipient address, if known.",
            },
            "purpose": {
                "type": "string",
                "description": "A short description of what should be sent.",
            },
        },
        "additionalProperties": False,
    },
    ACTION_SUBMIT_FORM: {
        "type": "object",
        "properties": {
            "form_name": {
                "type": "string",
                "description": "The form or application the caller requested.",
            }
        },
        "additionalProperties": False,
    },
    ACTION_TRANSFER_CALL: {
        "type": "object",
        "properties": {
            "destination": {
                "type": "string",
                "description": "The requested team or person, not a guessed number.",
            }
        },
        "additionalProperties": False,
    },
    ACTION_END_CALL: {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "A short reason the caller clearly ended the conversation.",
            }
        },
        "additionalProperties": False,
    },
}

_UNAVAILABLE_MESSAGES = {
    ACTION_SCHEDULE_CALLBACK: "Callback scheduling is not connected for this call.",
    ACTION_SEND_EMAIL: "Email delivery is not connected for this call.",
    ACTION_SUBMIT_FORM: "Form submission is not connected for this call.",
    ACTION_TRANSFER_CALL: "Call transfer is not available in this runtime.",
}

_SAFE_FAILURE_SPEECH = {
    ACTION_SCHEDULE_CALLBACK: (
        "I can't schedule a callback from this call, but I can take the details "
        "for the team."
    ),
    ACTION_SEND_EMAIL: (
        "I can't send an email from this call, but I can take the address for the team."
    ),
    ACTION_SUBMIT_FORM: (
        "I can't submit that form from this call, but I can take the details for the team."
    ),
    ACTION_TRANSFER_CALL: (
        "I can't transfer the call right now, but I can take a message for the team."
    ),
    ACTION_END_CALL: "I can't end the line from here; you can hang up whenever you're ready.",
}

_INTENT_PATTERNS = {
    ACTION_SCHEDULE_CALLBACK: re.compile(
        r"\b(?:call\s*back|callback|follow[- ]?up\s+call|"
        r"call me (?:back|again|later))\b",
        re.IGNORECASE,
    ),
    ACTION_SEND_EMAIL: re.compile(
        r"\b(?:e-?mail|send (?:it|that|this|the (?:details|information|quote|estimate)))\b",
        re.IGNORECASE,
    ),
    ACTION_SUBMIT_FORM: re.compile(
        r"\b(?:(?:submit|send|complete|file) (?:the |that |this )?"
        r"(?:form|application|request)|form submission)\b",
        re.IGNORECASE,
    ),
    ACTION_TRANSFER_CALL: re.compile(
        r"\b(?:transfer|connect me|put me through|speak (?:to|with)|talk (?:to|with))\b"
        r".{0,35}\b(?:human|person|agent|representative|manager|team|someone)\b|"
        r"\b(?:transfer|put me through)\b",
        re.IGNORECASE,
    ),
    ACTION_END_CALL: re.compile(
        r"\b(?:goodbye|bye(?: bye)?|hang\s*up|end (?:this |the )?call|"
        r"stop calling|do not call|don't call|not interested|no thanks|"
        r"that's all|that is all|we(?:'re| are) done|i(?:'m| am) done)\b",
        re.IGNORECASE,
    ),
}


def _result(
    action: str,
    *,
    success: bool,
    status: str,
    confirmation_allowed: bool,
    message: str,
) -> dict[str, Any]:
    """Build the one stable result envelope used on every provider path."""
    return {
        "version": 1,
        "action": action,
        "success": success,
        "status": status,
        "confirmation_allowed": confirmation_allowed,
        "message": message,
    }


def result_json(result: Mapping[str, Any]) -> str:
    """Serialize a result deterministically for chat-completion tool messages."""
    return json.dumps(dict(result), sort_keys=True, separators=(",", ":"))


def execution_failure_result(
    session: Any,
    action: str,
    *,
    message: str = "The action could not be executed.",
) -> dict[str, Any]:
    """Return and record the canonical unexpected-executor failure envelope."""
    result = _result(
        action,
        success=False,
        status="execution_failed",
        confirmation_allowed=False,
        message=message,
    )
    _record_result(session, result)
    return result


def action_results_for_session(session: Any) -> dict[str, dict[str, Any]]:
    """Return a defensive copy of this call's latest result per action."""
    raw = getattr(session, "_voice_action_results", None)
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(name): dict(value)
        for name, value in raw.items()
        if isinstance(value, Mapping)
    }


def _record_result(session: Any, result: dict[str, Any]) -> None:
    if session is None:
        return
    current = action_results_for_session(session)
    current[result["action"]] = dict(result)
    try:
        session._voice_action_results = current
    except Exception:  # pragma: no cover - defensive for foreign session doubles
        try:
            object.__setattr__(session, "_voice_action_results", current)
        except Exception:
            pass


def end_call_intent_present(text: str) -> bool:
    """Fail-closed proof that the caller, not the model, ended the conversation."""
    return bool(_INTENT_PATTERNS[ACTION_END_CALL].search(text or ""))


async def run_voice_action(
    session: Any,
    action: str,
    arguments: Mapping[str, Any] | None = None,
    *,
    user_text: str = "",
) -> dict[str, Any]:
    """Execute one voice action and always return a deterministic result.

    The arguments are accepted for a stable provider contract but are not
    persisted or logged while no corresponding action executor exists.
    """
    del arguments

    if action in _UNAVAILABLE_MESSAGES:
        result = _result(
            action,
            success=False,
            status="unavailable",
            confirmation_allowed=False,
            message=_UNAVAILABLE_MESSAGES[action],
        )
    elif action == ACTION_END_CALL:
        if not end_call_intent_present(user_text):
            result = _result(
                action,
                success=False,
                status="caller_intent_unconfirmed",
                confirmation_allowed=False,
                message="The caller has not clearly ended the conversation.",
            )
        else:
            try:
                session._end_call_requested = True
            except Exception:
                try:
                    object.__setattr__(session, "_end_call_requested", True)
                except Exception:
                    pass
            if getattr(session, "_end_call_requested", False) is not True:
                result = _result(
                    action,
                    success=False,
                    status="execution_failed",
                    confirmation_allowed=False,
                    message="The end-call request could not be armed.",
                )
            else:
                result = _result(
                    action,
                    success=True,
                    status="accepted",
                    # The request is accepted, but a speaking agent cannot truthfully
                    # claim the line is already disconnected.
                    confirmation_allowed=False,
                    message="End-call request accepted; say one short goodbye now.",
                )
    else:
        result = _result(
            str(action or "unknown"),
            success=False,
            status="unknown_action",
            confirmation_allowed=False,
            message="That action is not part of the connected voice-action contract.",
        )

    _record_result(session, result)
    logger.info(
        "voice_action_result call=%s action=%s success=%s status=%s",
        str(getattr(session, "call_id", "?"))[:12],
        result["action"],
        result["success"],
        result["status"],
    )
    return result


def safe_failure_speech(
    action: str,
    result: Mapping[str, Any] | None = None,
) -> str:
    """Truthful fixed speech used when the model invents action completion."""
    if (
        action == ACTION_END_CALL
        and isinstance(result, Mapping)
        and result.get("success") is True
        and result.get("status") == "accepted"
    ):
        return "The call will end now. Goodbye."
    return _SAFE_FAILURE_SPEECH.get(
        action,
        "I can't confirm that action from this call, but I can take the details for the team.",
    )


def action_from_validation_reason(reason: str | None) -> str | None:
    if not reason:
        return None
    prefix, separator, remainder = reason.partition(":")
    if separator and prefix in {"unconfirmed_action", "action_failed"}:
        return remainder.split(":", 1)[0]
    return None


def _chat_tool_spec(action: str) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": action,
            "description": _ACTION_DESCRIPTIONS[action],
            "parameters": _ACTION_PARAMETERS[action],
        },
    }


def realtime_voice_action_tools() -> list[dict[str, Any]]:
    """All actions in the flattened OpenAI/xAI Realtime tool shape."""
    tools: list[dict[str, Any]] = []
    for action in VOICE_ACTION_NAMES:
        tools.append(
            {
                "type": "function",
                "name": action,
                "description": _ACTION_DESCRIPTIONS[action],
                "parameters": _ACTION_PARAMETERS[action],
            }
        )
    return tools


def _provider_supports_action_tools(provider: Any) -> bool:
    base = getattr(provider, "_primary", provider)
    return (
        str(getattr(base, "name", "")).lower() in {"groq", "gemini"}
        and callable(getattr(provider, "stream_chat_with_tools", None))
    )


def _last_turn_text(messages: Iterable[Any]) -> tuple[str, str]:
    items = list(messages)
    user_index = next(
        (
            index
            for index in range(len(items) - 1, -1, -1)
            if getattr(items[index], "role", None) == MessageRole.USER
        ),
        -1,
    )
    if user_index < 0:
        return "", ""
    user_text = str(getattr(items[user_index], "content", "") or "")
    previous_assistant = next(
        (
            str(getattr(items[index], "content", "") or "")
            for index in range(user_index - 1, -1, -1)
            if getattr(items[index], "role", None) == MessageRole.ASSISTANT
        ),
        "",
    )
    return user_text, previous_assistant


def action_tools_for_turn(messages: Iterable[Any], provider: Any) -> list[dict[str, Any]]:
    """Offer only actions relevant to the current exchange.

    Tool-enabled turns buffer the model's first pass until it is known whether
    a tool call exists.  Restricting that cost to an explicit action exchange
    preserves the low-latency streaming path for ordinary conversation.
    """
    if not _provider_supports_action_tools(provider):
        return []
    user_text, previous_assistant = _last_turn_text(messages)
    context = f"{previous_assistant}\n{user_text}"
    actions = [
        action for action in VOICE_ACTION_NAMES
        if _INTENT_PATTERNS[action].search(context)
    ]
    return [_chat_tool_spec(action) for action in actions]


def action_tool_system_addendum() -> str:
    """Trusted instruction governing every action result."""
    return (
        "## Connected actions\n"
        "When an offered action tool matches the caller's request, call it before "
        "saying the action happened. Wait for its result. A result with success=false "
        "or confirmation_allowed=false must never be described as completed; state "
        "the limitation honestly and offer only the next step the result permits. "
        "Never replace a failed tool with a promise that the action was done."
    )
