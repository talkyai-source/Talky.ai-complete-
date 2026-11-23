"""A refused ending is never answered with "I didn't catch that" (24 Nov 2026).

Inspection of the 23 Nov calls: the model asked to end the call, the code
refused (the caller's own words did not end it), the model then returned
nothing, and the agent said "Sorry, I didn't quite catch that" although the
caller had just been understood. Three fixes, all asserted here:

D1 the stand-in line after a refused ending (tool or text marker);
D2 the refused result tells the model what to do next;
D3 the log shows what happened to an ending and to a do-not-call flag.
"""
from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.models.session import CallSession
from app.domain.services.end_session_action import caller_signaled_end
from app.domain.services.voice_pipeline.action_tools import (
    ACTION_END_CALL, result_json, run_voice_action,
)
from app.domain.services.voice_pipeline import turn_streamer
from app.domain.services.voice_pipeline_service import VoicePipelineService
from tests.unit.test_end_call_closing_words import END_CALL, _groq, _tools
from tests.unit.test_model_verified_opt_out import PARAPHRASE
from tests.unit.test_voice_action_contract import _session as _action_session

REFUSED_ENDING_LINE = getattr(turn_streamer, "REFUSED_ENDING_LINE", "<constant missing>")
NOT_CAUGHT_LINE = getattr(turn_streamer, "NOT_CAUGHT_LINE", "<constant missing>")
NO_END_WORDS = "Tell me more about it."
OLD_SENTENCE = "Sorry, I didn't quite catch that — could you say it again?"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    monkeypatch.setenv("GROQ_API_KEY", "test")


class _PlainLLM:
    def __init__(self, chunks):
        self._chunks = chunks

    async def stream_chat_with_timeout(self, *args, **kwargs):
        for chunk in self._chunks:
            yield chunk


class _ToolLLM:
    """Asks end_call through the real tool runner and yields no words."""
    name = "groq"
    supports_tools = True

    def __init__(self):
        self.seen = None

    async def stream_chat_with_tools(self, *args, **kwargs):
        self.seen = json.loads(await kwargs["tool_runner"](ACTION_END_CALL, {"reason": "x"}))
        if False:
            yield ""

    async def stream_chat_with_timeout(self, *args, **kwargs):
        if False:
            yield ""


async def _turn(llm, said, session=None):
    service = VoicePipelineService(
        stt_provider=AsyncMock(), llm_provider=llm,
        tts_provider=AsyncMock(), media_gateway=AsyncMock(),
    )
    service.latency_tracker = MagicMock()
    service.synthesize_and_send_audio = AsyncMock(return_value=False)
    if session is None:
        session = CallSession(
            call_id="call-refused-end-1", campaign_id="c", lead_id="l",
            provider_call_id="p", system_prompt="Plain.", voice_id="v",
        )
        session.barge_in_event = asyncio.Event()
    session.conversation_history.append(Message(role=MessageRole.USER, content=said))
    service._barge_in_events[session.call_id] = session.barge_in_event
    await asyncio.wait_for(service._stream_llm_and_tts(session), 5)
    spoken = [c.args[1] for c in service.synthesize_and_send_audio.await_args_list]
    return spoken, session


def _zero_token_lines(caplog):
    return [r.getMessage() for r in caplog.records if "zero_token_turn" in r.getMessage()]


async def test_refused_end_call_tool_then_nothing_does_not_claim_a_mishearing(caplog):
    caplog.set_level(logging.INFO)
    llm = _ToolLLM()
    spoken, _ = await _turn(llm, NO_END_WORDS, _action_session())
    assert llm.seen["status"] == "caller_intent_unconfirmed"  # the REAL executor refused
    assert spoken == [REFUSED_ENDING_LINE]
    assert "catch" not in spoken[0].lower()
    (line,) = _zero_token_lines(caplog)
    assert "cause=end_call_refused line=refused_ending" in line
    assert "raw_chars=0" in line
    assert NO_END_WORDS not in caplog.text


async def test_nothing_and_no_request_keeps_the_old_sentence_byte_for_byte(caplog):
    caplog.set_level(logging.INFO)
    spoken, _ = await _turn(_PlainLLM([]), NO_END_WORDS)
    assert NOT_CAUGHT_LINE == OLD_SENTENCE
    assert spoken == [OLD_SENTENCE]
    (line,) = _zero_token_lines(caplog)
    assert line.startswith("zero_token_turn call=call-refused-end-1 — LLM produced no speech; spoke recovery line")
    assert "cause=no_action line=not_caught" in line


async def test_caller_signalled_end_and_nothing_still_says_goodbye(caplog):
    caplog.set_level(logging.INFO)
    spoken, session = await _turn(_PlainLLM([]), "Okay, thank you. Goodbye.")
    assert spoken == ["Goodbye."]
    assert session._end_call_requested is True
    (line,) = _zero_token_lines(caplog)
    assert "cause=no_action line=goodbye" in line


async def test_refused_text_marker_alone_is_visible_and_logged_once(caplog):
    caplog.set_level(logging.INFO)
    spoken, session = await _turn(_PlainLLM(["[[END_CALL]]"]), NO_END_WORDS)
    assert spoken == [REFUSED_ENDING_LINE]
    assert getattr(session, "_end_call_requested", False) is False
    assert sum("end_call_marker_refused" in r.getMessage() for r in caplog.records
               if "zero_token_turn" not in r.getMessage()) == 1
    (line,) = _zero_token_lines(caplog)
    assert "cause=end_call_marker_refused line=refused_ending" in line


@pytest.mark.parametrize("reply", ["No thanks", "Nothing else", "That's all", "Not interested"])
def test_the_products_detector_accepts_a_decline_after_the_new_line(reply):
    assert caller_signaled_end(reply, previous_assistant_text=REFUSED_ENDING_LINE) is True


def test_the_products_detector_does_not_end_on_a_question():
    assert caller_signaled_end("Who is this?", previous_assistant_text=REFUSED_ENDING_LINE) is False


def _opt_session():
    return SimpleNamespace(call_id="call-optout-log-1", conversation_history=[])


def _result_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("voice_action_result")]
