"""The model's closing words are said once.

Test call f5dcac8e (Safar Coaches, DeepSeek, 2026-10-08 08:10:45): the model
answered the caller's goodbye with "Goodbye, Uzair! Take care." and end_call
in the same reply. The tool loop then asked for another round, the result
said "say one short goodbye now", and the caller heard "Goodbye, Uzair! Take
care. Goodbye, and thank you!" with the hang-up 6 s later (08:10:48 to
08:10:54) waiting on that extra round.

An end_call result ends the reply once words were spoken in that round.
Accepted, the call ends after them; refused, the call stays open and the
caller answers them. With nothing spoken yet, the loop still gives the model
a round to say goodbye.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.services.voice_pipeline.action_tools import (
    ACTION_END_CALL, _chat_tool_spec, result_json, run_voice_action,
)
from app.infrastructure.llm.groq import GroqLLMProvider
from app.infrastructure.llm.streaming import ends_turn
from tests.unit.test_gemini_tools import _FakeChunk, _FakeStream, _provider_with_streams, _run, rich_genai  # noqa: F401
from tests.unit.test_voice_action_contract import _session

MESSAGES = [Message(role=MessageRole.USER, content="Okay, thank you. Goodbye.")]
END_CALL = {"id": "end-1", "name": ACTION_END_CALL, "arguments": {"reason": "caller said goodbye"},
            "arguments_raw": '{"reason":"caller said goodbye"}', "arguments_valid": True}
LOOKUP = {"id": "kb-1", "name": "lookup", "arguments": {"query": "fares"},
          "arguments_raw": '{"query":"fares"}', "arguments_valid": True}


def _tools():
    return [_chat_tool_spec(ACTION_END_CALL),
            {"type": "function", "function": {"name": "lookup", "parameters": {
                "type": "object", "properties": {"query": {"type": "string"}}, "additionalProperties": False}}}]


def _groq(script):
    provider, rounds = GroqLLMProvider(), []

    async def stream(messages, **kwargs):
        rounds.append(kwargs)
        text, call = script[len(rounds) - 1]
        if text:
            yield text
        if call:
            kwargs["tool_calls_sink"].append(dict(call))

    provider.stream_chat = stream
    return provider, rounds


def _runner(status="accepted"):
    async def run(name, args):
        if name == "lookup":
            return "Lahore to Islamabad: departures every hour."
        session = _session()
        said = "Okay, thank you. Goodbye." if status == "accepted" else "Can you send me the details?"
        return result_json(await run_voice_action(session, name, args, user_text=said))
    return run


async def test_a_goodbye_spoken_with_end_call_is_not_repeated():
    provider, rounds = _groq([("Goodbye, Uzair! Take care.", END_CALL),
                              ("Goodbye, and thank you!", None)])
    out = [t async for t in provider.stream_chat_with_tools(
        MESSAGES, tools=_tools(), tool_runner=_runner(), max_tool_rounds=3)]
    assert "".join(out) == "Goodbye, Uzair! Take care."
    assert len(rounds) == 1


async def test_a_silent_end_call_still_gets_its_goodbye():
    provider, rounds = _groq([(None, END_CALL), ("Goodbye, take care!", None)])
    out = [t async for t in provider.stream_chat_with_tools(
        MESSAGES, tools=_tools(), tool_runner=_runner(), max_tool_rounds=3)]
    assert "".join(out) == "Goodbye, take care!"
    assert len(rounds) == 2


async def test_a_refused_end_call_leaves_the_spoken_words_for_the_caller_to_answer():
    provider, rounds = _groq([("I'll have the team email the details. Thanks for your time!", END_CALL),
                              ("Is there anything else I can help with?", None)])
    out = [t async for t in provider.stream_chat_with_tools(
        MESSAGES, tools=_tools(), tool_runner=_runner("refused"), max_tool_rounds=3)]
    assert "".join(out) == "I'll have the team email the details. Thanks for your time!"
    assert len(rounds) == 1


async def test_other_tools_still_continue_after_a_spoken_preamble():
    provider, rounds = _groq([("Let me check.", LOOKUP), ("Every hour, from five am.", None)])
    out = [t async for t in provider.stream_chat_with_tools(
        MESSAGES, tools=_tools(), tool_runner=_runner(), max_tool_rounds=3)]
    assert "".join(out) == "Let me check.Every hour, from five am."
    assert len(rounds) == 2


async def test_the_end_call_results_carry_the_marker():
    accepted = await run_voice_action(_session(), ACTION_END_CALL, {"reason": "bye"}, user_text="Goodbye.")
    refused = await run_voice_action(_session(), ACTION_END_CALL, {"reason": "x"}, user_text="Tell me more.")
    unknown = await run_voice_action(_session(), "not_an_action", {}, user_text="Goodbye.")
    assert accepted["status"] == "accepted" and ends_turn(result_json(accepted))
    assert refused["status"] == "caller_intent_unconfirmed" and ends_turn(result_json(refused))
    assert not ends_turn(result_json(unknown))
    assert not ends_turn("plain text") and not ends_turn(json.dumps(["ends_turn"]))


def test_gemini_does_not_repeat_the_goodbye_either(rich_genai):  # noqa: F811
    call = SimpleNamespace(name=ACTION_END_CALL, args={"reason": "caller said goodbye"})
    p = _provider_with_streams([
        _FakeStream([_FakeChunk(text="Goodbye, Uzair! Take care.", function_calls=[call])]),
        _FakeStream([_FakeChunk(text="Goodbye, and thank you!")]),
    ])
    out = _run(p.stream_chat_with_tools(MESSAGES, tools=_tools(), tool_runner=_runner(), max_tool_rounds=3))
    assert "".join(out) == "Goodbye, Uzair! Take care."
    assert len(p._test_requests) == 1


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test")
    monkeypatch.setenv("GEMINI_API_KEY", "test")
