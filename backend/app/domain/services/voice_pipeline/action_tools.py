"""Deterministic action-tool contract shared by both live voice pipelines.

The voice agent must never turn prose such as "I'll email that" into an
imaginary side effect.  Every action in this module returns the same bounded,
machine-readable result shape.  A model may describe an action as completed
only when ``confirmation_allowed`` is true in that result.

Campaign-authorized actions use existing business services and durable receipts.
``end_call`` records an accepted request on the existing session flag; the
normal turn finisher performs hangup after the short closing line has played.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from typing import Any

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
        "Transfer this live call to another person. First say one short line such "
        "as \"One moment while I try them.\", then call this; it can ring for up to a "
        "minute. Never say the transfer has started or the caller is connected until "
        "the result says the person answered."
    ),
    ACTION_END_CALL: (
        "Request that this live call end after a short goodbye. Use only when "
        "the caller clearly ended the conversation or asked not to be contacted "
        "again, and then in the same reply as your goodbye: without it the line "
        "stays open. Do not claim the line is already disconnected while you are "
        "still speaking."
    ),
}

_ACTION_PARAMETERS = {
    ACTION_SCHEDULE_CALLBACK: {
        "type": "object",
        "properties": {
            "requested_time": {
                "type": "string",
                "description": "Proposed exact ISO8601 callback datetime, read back before execution.",
            },
            "timezone": {"type": "string", "description": "Caller-confirmed IANA timezone, such as Europe/London."},
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
            },
            "do_not_call": {
                "type": "boolean",
                "description": "True only when the caller asked not to be contacted again, in any wording.",
            },
            "opt_out_quote": {
                "type": "string",
                "description": "With do_not_call: the caller's exact words asking not to be contacted.",
            },
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
        "I can't confirm a scheduled callback from this call."
    ),
    ACTION_SEND_EMAIL: (
        "I can't confirm that the email was sent."
    ),
    ACTION_SUBMIT_FORM: (
        "I can't confirm that the form was submitted."
    ),
    ACTION_TRANSFER_CALL: (
        "I can't transfer this call right now."
    ),
    ACTION_END_CALL: "I can't end the line from here; you can hang up whenever you're ready.",
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


def end_call_intent_present(text: str, *, previous_assistant_text: str | None = None) -> bool:
    """Fail-closed proof that the caller, not the model, ended the conversation."""
    from app.domain.services.end_session_action import caller_signaled_end
    return caller_signaled_end(text, previous_assistant_text=previous_assistant_text)


async def run_voice_action(
    session: Any,
    action: str,
    arguments: Mapping[str, Any] | None = None,
    *,
    user_text: str = "",
    previous_assistant_text: str | None = None,
) -> dict[str, Any]:
    """Execute one voice action and always return a deterministic result.

    The arguments are accepted for a stable provider contract but are not
    persisted or logged while no corresponding action executor exists.
    """
    if action in _UNAVAILABLE_MESSAGES:
        from app.domain.services.voice_pipeline.action_execution import execute_connected_voice_action
        result = await execute_connected_voice_action(session, action, dict(arguments or {}), user_text)
    elif action == ACTION_END_CALL:
        from app.domain.services.end_session_action import verified_opt_out
        if previous_assistant_text is None:
            from app.domain.services.end_session_action import previous_assistant_turn
            previous_assistant_text = previous_assistant_turn(getattr(session, "conversation_history", ()))
        call_args = dict(arguments or {})
        opted_out = call_args.get("do_not_call") is True and verified_opt_out(call_args, user_text)
        opt_out_recorded = False
        flagged_dnc = call_args.get("do_not_call") is True
        if opted_out:
            # Do it before the goodbye can say it was done (same rule as the
            # end-session path). Teardown retries an unconfirmed write.
            from app.domain.services.dialer.opt_out import purge_opt_out_before_farewell
            try:
                session._caller_opted_out = True
            except Exception:
                pass
            opt_out_recorded = await purge_opt_out_before_farewell(session)
            logger.info(
                "caller_opt_out_detected call_id=%s evidence=end_call_tool recorded=%s",
                str(getattr(session, "call_id", "?"))[:12], opt_out_recorded,
            )
        # Same evidence as the text-marker path (end_call.model_end_call_allowed):
        # a mailbox/screening flag, unless the caller asks to stay.
        from app.domain.services.caller_assertions import continuation_after
        machine = (
            (getattr(session, "_amd_voicemail", False) is True
             or getattr(session, "_machine_screening", False) is True)
            and not continuation_after(user_text)
        )
        if not opted_out and not machine and not end_call_intent_present(
            user_text, previous_assistant_text=previous_assistant_text
        ):
            result = _result(
                action,
                success=False,
                status="caller_intent_unconfirmed",
                confirmation_allowed=False,
                message="The caller has not clearly ended the conversation.",
            )
            # Words the model spoke with this decision were its closing; the
            # call stays open and the caller answers them (stream_tool_turn).
            result["ends_turn"] = True
            # The whole result reaches the model as the tool message; this key
            # tells it what to do next (message is never spoken to the caller).
            result["next_step"] = (
                "The call stays open. If you have not already spoken in this turn, "
                "reply to what the caller just said in one short sentence. "
                "Do not say goodbye."
                + (" No opt-out was recorded; do not say they will not be "
                   "contacted again." if flagged_dnc else "")
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
                    message="End-call request accepted; say one short goodbye now." + (
                        " Opt-out recorded; you may say they won't be contacted again."
                        if opted_out and opt_out_recorded else
                        " Opt-out not yet confirmed; do not say they were removed."
                        if opted_out else
                        " No opt-out was recorded; do not say they were removed."
                        if call_args.get("do_not_call") is True else ""
                    ),
                )
                if opted_out:
                    result["opt_out"] = "recorded" if opt_out_recorded else "unconfirmed"
                elif call_args.get("do_not_call") is True:
                    result["opt_out"] = "not_recorded"
                # A goodbye spoken with the request is not said twice (test
                # call f5dcac8e); with nothing spoken, the message asks for one.
                result["ends_turn"] = True
    else:
        result = _result(
            str(action or "unknown"),
            success=False,
            status="unknown_action",
            confirmation_allowed=False,
            message="That action is not part of the connected voice-action contract.",
        )

    _record_result(session, result)
    log_line = "voice_action_result call=%s action=%s success=%s status=%s"
    log_args: tuple[Any, ...] = (
        str(getattr(session, "call_id", "?"))[:12],
        result["action"],
        result["success"],
        result["status"],
    )
    if action == ACTION_END_CALL:
        # What happened to the ending and to a do-not-call flag; never words.
        opt_out_log = (
            ("recorded" if opt_out_recorded else "write_unconfirmed") if opted_out
            else "set_earlier" if getattr(session, "_caller_opted_out", False) is True
            else "quote_not_verified" if flagged_dnc else "none"
        )
        log_line += " do_not_call=%s opt_out=%s"
        log_args += (flagged_dnc, opt_out_log)
    logger.info(log_line, *log_args)
    return result


def safe_failure_speech(
    action: str,
    result: Mapping[str, Any] | None = None,
) -> str:
    """Truthful fixed speech used when the model invents action completion."""
    if isinstance(result, Mapping) and result.get("status") in {"unknown", "in_progress"}:
        return "I couldn't confirm the outcome of that request. It needs to be checked before trying again."
    if (action in {ACTION_SEND_EMAIL, ACTION_SUBMIT_FORM} and isinstance(result, Mapping)
            and result.get("success") is True and result.get("status") in {"accepted", "provider_accepted"}):
        return "The provider accepted it for sending, but I can't confirm delivery."
    if (
        action == ACTION_END_CALL
        and isinstance(result, Mapping)
        and result.get("success") is True
        and result.get("status") == "accepted"
    ):
        return "The call will end now. Goodbye."
    return _SAFE_FAILURE_SPEECH.get(
        action,
        "I can't confirm that action from this call.",
    )


def action_from_validation_reason(reason: str | None) -> str | None:
    if not reason:
        return None
    prefix, separator, remainder = reason.partition(":")
    if separator and prefix in {"unconfirmed_action", "action_failed", "unavailable_action"}:
        return remainder.split(":", 1)[0]
    return None


def _named_transfer_destinations(session: Any) -> list[dict[str, Any]]:
    context = getattr(session, "_voice_action_context", None) if session is not None else None
    if not context:
        return []
    from app.domain.services.voice_pipeline.action_execution import offered_transfer_destinations

    return [item for item in offered_transfer_destinations(context) if item.get("name")]


def action_parameters(action: str, session: Any = None) -> dict[str, Any]:
    """Argument schema shared by the cascaded and Realtime tool definitions."""
    named = _named_transfer_destinations(session) if action == ACTION_TRANSFER_CALL else []
    if len(named) < 2:
        return _ACTION_PARAMETERS[action]
    return {
        "type": "object",
        "properties": {
            "destination": {
                "type": "string",
                "enum": [item["name"] for item in named],
                "description": "The team to transfer to, chosen by the conditions in the tool description.",
            }
        },
        "required": ["destination"],
        "additionalProperties": False,
    }


def action_description_suffix(action: str, session: Any = None) -> str:
    """Per-call detail appended to a tool description (transfer destinations)."""
    named = _named_transfer_destinations(session) if action == ACTION_TRANSFER_CALL else []
    if not named:
        return ""
    # One clause per destination, so a condition written without a full stop
    # cannot run into the next name.
    return " Destinations: " + "; ".join(
        f"{item['name']}: {str(item['when']).rstrip('. ')}" if item.get("when") else item["name"]
        for item in named
    ) + "."


def _chat_tool_spec(action: str, session: Any = None) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": action,
            "description": _ACTION_DESCRIPTIONS[action] + action_description_suffix(action, session),
            "parameters": action_parameters(action, session),
        },
    }


def _provider_supports_action_tools(provider: Any) -> bool:
    return (
        getattr(provider, "supports_tools", False) is True
        and callable(getattr(provider, "stream_chat_with_tools", None))
    )


def action_tools_for_turn(
    messages: Iterable[Any], provider: Any, *, session=None, now=None
) -> list[dict[str, Any]]:
    """Offer the connected actions; the model interprets the caller's intent."""
    if not _provider_supports_action_tools(provider):
        return []
    actions = VOICE_ACTION_NAMES
    if session is not None:
        from app.domain.services.voice_pipeline.action_execution import enabled_voice_actions
        enabled = enabled_voice_actions(session, now)
        actions = [action for action in actions if action in enabled]
    return [_chat_tool_spec(action, session) for action in actions]


def action_tool_system_addendum(available_actions: Iterable[str] | None = None) -> str:
    """Trusted instruction governing every action result."""
    capabilities = ""
    if available_actions is not None:
        names = ", ".join(sorted(set(available_actions))) or "none"
        capabilities = (
            f"Available actions for this call: {names}. Offer only those actions; "
            "an unlisted transfer or team follow-up route is unavailable. "
        )
    return (
        "## Connected actions\n"
        + capabilities +
        "When an offered action tool matches the caller's request, call it before "
        "saying the action happened. Wait for its result. A result with success=false "
        "or confirmation_allowed=false must never be described as completed; state "
        "the limitation honestly and offer only the next step the result permits. "
        "Never replace a failed tool with a promise that the action was done. "
        "Offer a brochure, download link or other resource only when approved "
        "company facts or a tool result actually provide it; do not invent a "
        "fallback resource after a failure. You may note a caller-requested "
        "follow-up for review; recording a request does not arrange or "
        "guarantee follow-up and does not create a handoff route."
    )
