"""Voice actions must be proved by a tool result before the agent confirms them."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.models.session import CallSession
from app.domain.models.agent_config import ConversationRule
from app.domain.services.llm_guardrails import LLMGuardrails
from app.domain.services.voice_pipeline.action_tools import (
    ACTION_END_CALL,
    ACTION_SCHEDULE_CALLBACK,
    ACTION_SEND_EMAIL,
    ACTION_SUBMIT_FORM,
    ACTION_TRANSFER_CALL,
    VOICE_ACTION_NAMES,
    action_results_for_session,
    action_tools_for_turn,
    realtime_voice_action_tools,
    result_json,
    run_voice_action,
)
from app.domain.services.voice_pipeline.realtime_bridge import RealtimeBridge
from app.domain.services.voice_pipeline_service import VoicePipelineService
from app.infrastructure.llm.groq import GroqLLMProvider


_COMPLETION_CLAIMS = {
    ACTION_SCHEDULE_CALLBACK: "I've scheduled your callback for tomorrow.",
    ACTION_SEND_EMAIL: "The email was sent to you.",
    ACTION_SUBMIT_FORM: "Your application was submitted.",
    ACTION_TRANSFER_CALL: "I'm transferring you now.",
    ACTION_END_CALL: "I've ended the call.",
}


def _session() -> CallSession:
    session = CallSession(
        call_id="call-1",
        campaign_id="campaign-1",
        lead_id="lead-1",
        tenant_id="tenant-1",
        provider_call_id="provider-1",
        system_prompt="Use plain spoken text only.",
        voice_id="voice-1",
    )
    session.barge_in_event = asyncio.Event()
    return session


def test_guardrail_rejects_callback_confirmation_without_tool_result():
    valid, reason = LLMGuardrails().validate_response(
        "I've scheduled your callback for tomorrow.",
        None,
        action_results={},
    )

    assert valid is False
    assert reason == "unconfirmed_action:schedule_callback"


@pytest.mark.parametrize("action,claim", _COMPLETION_CLAIMS.items())
def test_guardrail_rejects_all_unproved_action_completion_claims(action, claim):
    valid, reason = LLMGuardrails().validate_response(
        claim,
        None,
        action_results={},
    )

    assert valid is False
    assert reason == f"unconfirmed_action:{action}"


@pytest.mark.parametrize(
    "honest_failure",
    [
        "The callback was not scheduled.",
        "The email wasn't sent.",
        "The form wasn't submitted.",
        "The transfer was not started.",
        "The call has not ended.",
    ],
)
def test_guardrail_allows_honest_action_failure(honest_failure):
    assert LLMGuardrails().validate_response(
        honest_failure,
        None,
        action_results={},
    ) == (True, None)


@pytest.mark.parametrize(
    "claim,action",
    [
        ("I didn't fail; the email was sent.", ACTION_SEND_EMAIL),
        ("Not only was the email sent, it was delivered.", ACTION_SEND_EMAIL),
        ("I didn't forget; your callback is scheduled.", ACTION_SCHEDULE_CALLBACK),
        (
            "The email wasn't sent at first, but the email was sent now.",
            ACTION_SEND_EMAIL,
        ),
    ],
)
def test_unrelated_negation_cannot_bypass_action_confirmation_gate(claim, action):
    valid, reason = LLMGuardrails().validate_response(
        claim,
        None,
        action_results={},
    )

    assert valid is False
    assert reason == f"unconfirmed_action:{action}"


@pytest.mark.parametrize(
    "claim,action",
    [
        ("I've booked it for tomorrow.", ACTION_SCHEDULE_CALLBACK),
        ("I've sent it already.", ACTION_SEND_EMAIL),
        ("I've submitted it now.", ACTION_SUBMIT_FORM),
        ("I've transferred you now.", ACTION_TRANSFER_CALL),
        ("I'm ending the call now.", ACTION_END_CALL),
    ],
)
def test_pronoun_action_completion_claims_still_require_result(claim, action):
    valid, reason = LLMGuardrails().validate_response(
        claim,
        None,
        action_results={},
    )

    assert valid is False
    assert reason == f"unconfirmed_action:{action}"


@pytest.mark.parametrize(
    "honest_failure",
    [
        "I haven't booked the callback.",
        "I haven't sent the email.",
        "I haven't submitted the form.",
        "I haven't transferred you.",
        "I haven't ended the call.",
    ],
)
def test_first_person_action_negation_is_not_a_completion_claim(honest_failure):
    assert LLMGuardrails().validate_response(
        honest_failure,
        None,
        action_results={},
    ) == (True, None)


def test_guardrail_requires_explicit_confirmation_permission_from_result():
    claim = _COMPLETION_CLAIMS[ACTION_SEND_EMAIL]
    failed = {
        ACTION_SEND_EMAIL: {
            "success": False,
            "status": "unavailable",
            "confirmation_allowed": False,
        }
    }
    assert LLMGuardrails().validate_response(
        claim, None, action_results=failed
    ) == (False, "action_failed:send_email:unavailable")

    completed = {
        ACTION_SEND_EMAIL: {
            "success": True,
            "status": "completed",
            "confirmation_allowed": True,
        }
    }
    assert LLMGuardrails().validate_response(
        claim, None, action_results=completed
    ) == (True, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action",
    [
        ACTION_SCHEDULE_CALLBACK,
        ACTION_SEND_EMAIL,
        ACTION_SUBMIT_FORM,
        ACTION_TRANSFER_CALL,
    ],
)
async def test_unwired_actions_fail_closed_and_are_recorded(action):
    session = _session()

    first = await run_voice_action(session, action, {"untrusted": "value"})
    second = await run_voice_action(session, action, {"different": "value"})

    assert first == second
    assert first["success"] is False
    assert first["status"] == "unavailable"
    assert first["confirmation_allowed"] is False
    assert action_results_for_session(session)[action] == first
    assert result_json(first) == json.dumps(first, sort_keys=True, separators=(",", ":"))


@pytest.mark.asyncio
async def test_end_call_requires_caller_intent_then_uses_existing_finisher_flag():
    session = _session()

    denied = await run_voice_action(
        session,
        ACTION_END_CALL,
        {"reason": "model decided"},
        user_text="Tell me more about the service.",
    )
    assert denied["success"] is False
    assert denied["status"] == "caller_intent_unconfirmed"
    assert getattr(session, "_end_call_requested", False) is False

    accepted = await run_voice_action(
        session,
        ACTION_END_CALL,
        {"reason": "caller goodbye"},
        user_text="No thanks, goodbye.",
    )
    assert accepted["success"] is True
    assert accepted["status"] == "accepted"
    assert accepted["confirmation_allowed"] is False
    assert session._end_call_requested is True


@pytest.mark.asyncio
async def test_end_call_cannot_succeed_when_finisher_flag_cannot_be_proved():
    class _NonExtensibleSession:
        __slots__ = ()

    result = await run_voice_action(
        _NonExtensibleSession(),
        ACTION_END_CALL,
        {"reason": "caller goodbye"},
        user_text="Goodbye.",
    )

    assert result["success"] is False
    assert result["status"] == "execution_failed"
    assert result["confirmation_allowed"] is False


class _ToolCapableGroq:
    name = "groq"

    async def stream_chat_with_tools(self, *args, **kwargs):
        if False:
            yield ""


@pytest.mark.parametrize(
    "user_text,expected",
    [
        ("Please call me back tomorrow.", ACTION_SCHEDULE_CALLBACK),
        ("Can you email that to me?", ACTION_SEND_EMAIL),
        ("Submit the application.", ACTION_SUBMIT_FORM),
        ("Transfer me to a person.", ACTION_TRANSFER_CALL),
        ("No thanks, goodbye.", ACTION_END_CALL),
    ],
)
def test_cascaded_action_tool_is_offered_only_for_relevant_turn(user_text, expected):
    messages = [Message(role=MessageRole.USER, content=user_text)]

    tools = action_tools_for_turn(messages, _ToolCapableGroq())

    assert [tool["function"]["name"] for tool in tools] == [expected]


def test_realtime_schema_exposes_each_action_once_and_transfer_stays_gated():
    names = [tool["name"] for tool in realtime_voice_action_tools()]
    assert names == list(VOICE_ACTION_NAMES)
    assert len(names) == len(set(names))

    from app.domain.services.telephony.inbound_transfer import (
        CONTROLLED_INBOUND_TRANSFER_RUNTIME_AVAILABLE,
    )

    assert CONTROLLED_INBOUND_TRANSFER_RUNTIME_AVAILABLE is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,message",
    [
        (
            ACTION_SCHEDULE_CALLBACK,
            "Callback scheduling is not connected for this call.",
        ),
        (ACTION_SEND_EMAIL, "Email delivery is not connected for this call."),
        (ACTION_SUBMIT_FORM, "Form submission is not connected for this call."),
        (ACTION_TRANSFER_CALL, "Call transfer is not available in this runtime."),
    ],
)
async def test_realtime_unwired_actions_return_same_deterministic_failure(
    action,
    message,
):
    realtime = SimpleNamespace(send_function_result=AsyncMock())
    bridge = RealtimeBridge(
        call_id="call-1",
        realtime_session=realtime,
        media_gateway=SimpleNamespace(),
    )
    function_call = SimpleNamespace(
        name=action,
        call_id="tool-1",
        parsed_arguments=lambda: {"requested_time": "tomorrow"},
    )

    await bridge._handle_function_call(function_call)

    realtime.send_function_result.assert_awaited_once_with(
        "tool-1",
        {
            "version": 1,
            "action": action,
            "success": False,
            "status": "unavailable",
            "confirmation_allowed": False,
            "message": message,
        },
    )


@pytest.mark.asyncio
async def test_realtime_preserves_knowledge_lookup_dispatch():
    realtime = SimpleNamespace(send_function_result=AsyncMock())
    bridge = RealtimeBridge(
        call_id="call-1",
        realtime_session=realtime,
        media_gateway=SimpleNamespace(),
    )
    bridge._lookup_knowledge = AsyncMock(return_value="Verified hours")
    function_call = SimpleNamespace(
        name="knowledge_lookup",
        call_id="kb-1",
        parsed_arguments=lambda: {"query": "hours"},
    )

    await bridge._handle_function_call(function_call)

    bridge._lookup_knowledge.assert_awaited_once_with("hours")
    realtime.send_function_result.assert_awaited_once_with("kb-1", "Verified hours")


@pytest.mark.asyncio
async def test_realtime_end_call_sends_result_before_requesting_hangup():
    order = []

    async def send_result(call_id, result):
        order.append(("result", call_id, result["status"]))

    async def hangup(call_id, reason):
        order.append(("hangup", call_id, reason))
        return True

    session = _session()
    bridge = RealtimeBridge(
        call_id="call-1",
        realtime_session=SimpleNamespace(send_function_result=send_result),
        media_gateway=SimpleNamespace(hangup_call=hangup),
        action_session=session,
    )
    bridge._latest_caller_text = "Goodbye."
    function_call = SimpleNamespace(
        name=ACTION_END_CALL,
        call_id="end-1",
        parsed_arguments=lambda: {"reason": "caller goodbye"},
    )

    await bridge._handle_function_call(function_call)

    assert order == [
        ("result", "end-1", "accepted"),
        ("hangup", "call-1", "agent_end_call"),
    ]


@pytest.mark.asyncio
async def test_realtime_action_exception_still_returns_versioned_failure(monkeypatch):
    from app.domain.services.voice_pipeline import action_tools

    async def fail_action(*args, **kwargs):
        raise RuntimeError("executor exploded")

    monkeypatch.setattr(action_tools, "run_voice_action", fail_action)
    realtime = SimpleNamespace(send_function_result=AsyncMock())
    bridge = RealtimeBridge(
        call_id="call-1",
        realtime_session=realtime,
        media_gateway=SimpleNamespace(),
        action_session=_session(),
    )
    function_call = SimpleNamespace(
        name=ACTION_SEND_EMAIL,
        call_id="email-1",
        parsed_arguments=lambda: {},
    )

    await bridge._handle_function_call(function_call)

    realtime.send_function_result.assert_awaited_once_with(
        "email-1",
        {
            "version": 1,
            "action": ACTION_SEND_EMAIL,
            "success": False,
            "status": "execution_failed",
            "confirmation_allowed": False,
            "message": "The action could not be executed.",
        },
    )


@pytest.mark.asyncio
async def test_normal_cascaded_stream_blocks_unproved_claim_before_tts(monkeypatch):
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")

    class _StreamingLLM:
        async def stream_chat_with_timeout(self, *args, **kwargs):
            yield "I've scheduled your callback for tomorrow."

    service = VoicePipelineService(
        stt_provider=AsyncMock(),
        llm_provider=_StreamingLLM(),
        tts_provider=AsyncMock(),
        media_gateway=AsyncMock(),
    )
    service.latency_tracker = MagicMock()
    service.synthesize_and_send_audio = AsyncMock(return_value=False)
    session = _session()
    service._barge_in_events[session.call_id] = session.barge_in_event

    response, _, _ = await service._stream_llm_and_tts(session)

    spoken = [call.args[1] for call in service.synthesize_and_send_audio.await_args_list]
    assert all("scheduled your callback" not in text.lower() for text in spoken)
    assert spoken == [
        "I can't schedule a callback from this call, but I can take the details for the team."
    ]
    assert response == spoken[0]


@pytest.mark.asyncio
async def test_live_action_gate_does_not_activate_legacy_rule_keyword_heuristic(monkeypatch):
    """C1 must not turn the never-live fuzzy rule matcher into a speech outage."""
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")

    class _StreamingLLM:
        async def stream_chat_with_timeout(self, *args, **kwargs):
            yield "We can close this conversation politely."

    service = VoicePipelineService(
        stt_provider=AsyncMock(),
        llm_provider=_StreamingLLM(),
        tts_provider=AsyncMock(),
        media_gateway=AsyncMock(),
    )
    service.latency_tracker = MagicMock()
    service.synthesize_and_send_audio = AsyncMock(return_value=False)
    session = _session()
    session.agent_config = SimpleNamespace(
        response_max_sentences=2,
        rules=ConversationRule(
            do_not_say_rules=[
                "Never push too hard - if rejected twice, close politely",
            ]
        ),
    )
    service._barge_in_events[session.call_id] = session.barge_in_event

    response, _, _ = await service._stream_llm_and_tts(session)

    assert response == "We can close this conversation politely."
    service.synthesize_and_send_audio.assert_awaited_once_with(
        session,
        response,
        None,
        track_latency=True,
    )


@pytest.mark.asyncio
async def test_action_turn_feeds_failed_result_before_guarded_reply(monkeypatch):
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")

    class _ActionLLM:
        name = "groq"

        def __init__(self):
            self.seen_result = None
            self.require_strict = None

        async def stream_chat_with_tools(self, *args, **kwargs):
            self.require_strict = kwargs["require_tool_result_before_content"]
            self.seen_result = json.loads(
                await kwargs["tool_runner"](ACTION_SEND_EMAIL, {"recipient": "a@example.com"})
            )
            yield "The email was sent to you."

        async def stream_chat_with_timeout(self, *args, **kwargs):
            yield "unused"

    llm = _ActionLLM()
    service = VoicePipelineService(
        stt_provider=AsyncMock(),
        llm_provider=llm,
        tts_provider=AsyncMock(),
        media_gateway=AsyncMock(),
    )
    service.latency_tracker = MagicMock()
    service.synthesize_and_send_audio = AsyncMock(return_value=False)
    session = _session()
    session.conversation_history.append(
        Message(role=MessageRole.USER, content="Please email that to me.")
    )
    service._barge_in_events[session.call_id] = session.barge_in_event

    response, _, _ = await service._stream_llm_and_tts(session)

    assert llm.require_strict is True
    assert llm.seen_result["success"] is False
    assert llm.seen_result["status"] == "unavailable"
    assert response == "I can't send an email from this call, but I can take the address for the team."
    service.synthesize_and_send_audio.assert_awaited_once_with(
        session,
        response,
        None,
        track_latency=True,
    )


@pytest.mark.asyncio
async def test_cascaded_action_exception_returns_versioned_failure_to_model(monkeypatch):
    from app.domain.services.voice_pipeline import turn_streamer

    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")

    async def fail_action(*args, **kwargs):
        raise RuntimeError("executor exploded")

    monkeypatch.setattr(turn_streamer, "run_voice_action", fail_action)

    class _ActionLLM:
        name = "groq"

        def __init__(self):
            self.seen_result = None

        async def stream_chat_with_tools(self, *args, **kwargs):
            self.seen_result = json.loads(
                await kwargs["tool_runner"](ACTION_SEND_EMAIL, {})
            )
            yield "I can't send an email from this call."

        async def stream_chat_with_timeout(self, *args, **kwargs):
            yield "unused"

    llm = _ActionLLM()
    service = VoicePipelineService(
        stt_provider=AsyncMock(),
        llm_provider=llm,
        tts_provider=AsyncMock(),
        media_gateway=AsyncMock(),
    )
    service.latency_tracker = MagicMock()
    service.synthesize_and_send_audio = AsyncMock(return_value=False)
    session = _session()
    session.conversation_history.append(
        Message(role=MessageRole.USER, content="Please email that to me.")
    )
    service._barge_in_events[session.call_id] = session.barge_in_event

    await service._stream_llm_and_tts(session)

    assert llm.seen_result == {
        "version": 1,
        "action": ACTION_SEND_EMAIL,
        "success": False,
        "status": "execution_failed",
        "confirmation_allowed": False,
        "message": "The action could not be executed.",
    }


@pytest.mark.asyncio
async def test_groq_strict_tool_turn_discards_premature_round_zero_claim(monkeypatch):
    provider = GroqLLMProvider()
    rounds = 0

    async def fake_timeout(messages, **kwargs):
        nonlocal rounds
        rounds += 1
        sink = kwargs.get("tool_calls_sink")
        if sink is not None:
            sink.append(
                {
                    "id": "tool-1",
                    "name": ACTION_SEND_EMAIL,
                    "arguments_raw": "{}",
                    "arguments": {},
                }
            )
            yield "I've sent it already."
        else:
            yield "I can't send an email from this call."

    monkeypatch.setattr(provider, "stream_chat_with_timeout", fake_timeout)

    async def runner(name, arguments):
        assert name == ACTION_SEND_EMAIL
        return result_json(await run_voice_action(_session(), name, arguments))

    output = [
        token
        async for token in provider.stream_chat_with_tools(
            [],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": ACTION_SEND_EMAIL,
                        "description": "send",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
            tool_runner=runner,
            require_tool_result_before_content=True,
        )
    ]

    assert output == ["I can't send an email from this call."]
    assert "sent it already" not in "".join(output).lower()
    assert rounds == 2
