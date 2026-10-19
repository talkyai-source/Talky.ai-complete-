"""One short line when a tool step leaves the caller in silence (2026-10-20).

The knowledge lookup itself is instant (a call snapshot), but the second model
round after it is not, and the caller heard nothing meanwhile. Vapi speaks a
"request-response-delayed" line only when a tool is slow; LiveKit speaks a
filler only when the model's reply is a bare tool call. Both rules apply here:
a filler is spoken only when a tool starts before the caller heard anything
and the silence has lasted TOOL_FILLER_AFTER_S. It is never in the model's
history and never overlaps the real answer.
"""
from __future__ import annotations

import asyncio

import pytest

from app.domain.services.voice_pipeline import tool_filler
from app.domain.services.voice_pipeline.tool_filler import FILLER_PHRASES, ToolFiller
from tests.unit.test_model_driven_voice_turn import setup_turn


def _refund_id(session):
    return next(r["section_id"] for r in session._knowledge_catalog.nodes if r["id"] == "refund")


def _spoken(service):
    return [(c.args[1], c.kwargs.get("track_latency")) for c in service.synthesize_and_send_audio.call_args_list]


def _slow_second_round(monkeypatch, service, delay_s):
    model = service.llm_provider.stream_chat_with_timeout
    calls = {"n": 0}

    async def slow(messages, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            await asyncio.sleep(delay_s)
        async for token in model(messages, **kwargs):
            yield token

    monkeypatch.setattr(service.llm_provider, "stream_chat_with_timeout", slow)


async def test_a_slow_tool_round_gets_one_filler_before_the_answer(monkeypatch):
    monkeypatch.setattr(tool_filler, "TOOL_FILLER_AFTER_S", 0.05)
    steps = []
    service, session, _ = setup_turn(monkeypatch, "When will my money come back?", steps)
    steps.extend([{"section_ids": [_refund_id(session)]}, "Approved refunds take five working days."])
    _slow_second_round(monkeypatch, service, 0.3)

    response, _, _ = await service._stream_llm_and_tts(session)

    spoken = _spoken(service)
    assert spoken[0][0] in FILLER_PHRASES and spoken[0][1] is False  # not the measured reply
    assert spoken[1] == ("Approved refunds take five working days.", True)
    # The model never sees the filler, so it can't start copying it.
    assert response == "Approved refunds take five working days."
    assert all(s not in FILLER_PHRASES for s in session._spoken_sentences)


async def test_a_fast_tool_round_gets_no_filler(monkeypatch):
    monkeypatch.setattr(tool_filler, "TOOL_FILLER_AFTER_S", 5.0)
    steps = []
    service, session, _ = setup_turn(monkeypatch, "When will my money come back?", steps)
    steps.extend([{"section_ids": [_refund_id(session)]}, "Approved refunds take five working days."])

    await service._stream_llm_and_tts(session)

    assert [s for s, _ in _spoken(service)] == ["Approved refunds take five working days."]


async def test_no_filler_when_the_model_already_spoke_before_the_tool(monkeypatch):
    monkeypatch.setattr(tool_filler, "TOOL_FILLER_AFTER_S", 0.05)
    steps = []
    service, session, _ = setup_turn(monkeypatch, "When will my money come back?", steps)
    steps.extend([("Let me check that for you. ", {"section_ids": [_refund_id(session)]}),
                  "Approved refunds take five working days."])
    _slow_second_round(monkeypatch, service, 0.3)

    await service._stream_llm_and_tts(session)

    assert not any(s in FILLER_PHRASES for s, _ in _spoken(service))


async def test_at_most_once_per_turn_and_cancelled_if_the_answer_arrives_first():
    spoken = []

    async def speak(text):
        spoken.append(text)
        return False

    loop = asyncio.get_running_loop()
    filler = ToolFiller(speak, turn_start=loop.time(), turn_index=0, after_s=0.2, clock=loop.time)
    filler.arm()
    filler.arm()  # a second tool round in the same turn
    await filler.settle()  # the answer is ready before the deadline
    await asyncio.sleep(0.3)
    assert spoken == []


async def test_a_started_filler_finishes_before_the_answer_speaks():
    order = []

    async def speak(text):
        order.append("filler-start")
        await asyncio.sleep(0.05)
        order.append("filler-end")
        return False

    loop = asyncio.get_running_loop()
    filler = ToolFiller(speak, turn_start=loop.time() - 5, turn_index=1, after_s=0.2, clock=loop.time)
    filler.arm()  # silence already longer than the threshold: speaks at once
    await asyncio.sleep(0.01)
    await filler.settle()
    order.append("answer")
    assert order == ["filler-start", "filler-end", "answer"]


def test_phrases_rotate_by_turn():
    picks = {ToolFiller(None, turn_start=0, turn_index=i).phrase for i in range(len(FILLER_PHRASES))}
    assert picks == set(FILLER_PHRASES)
