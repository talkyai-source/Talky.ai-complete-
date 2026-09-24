"""A barge-in with no words behind it must not leave the caller in silence.

Live call c54579ea (2026-09-24 07:51:45, softphone -> ext 940003): after a
Flux stall the call ran on Nova. 0.5 s into the reply to "I'm the existing
patient." Nova's acoustic SpeechStarted cancelled it; no caller words ever
followed, no turn ran again, and after 14 s of silence the caller hung up.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.models.session import CallSession
from app.domain.services.voice_pipeline_service import VoicePipelineService


def _service():
    svc = VoicePipelineService(
        stt_provider=AsyncMock(),
        llm_provider=AsyncMock(),
        tts_provider=AsyncMock(),
        media_gateway=AsyncMock(),
    )
    svc.handle_turn_end = AsyncMock()
    return svc


def _session():
    s = CallSession(
        call_id="call-fbi-1",
        campaign_id="campaign-1",
        lead_id="lead-1",
        provider_call_id="provider-1",
        system_prompt="x",
        voice_id="v",
    )
    s.conversation_history = [
        Message(role=MessageRole.ASSISTANT, content="Are you a new or existing patient?"),
        Message(role=MessageRole.USER, content="I'm the existing patient."),
    ]
    return s


@pytest.mark.asyncio
async def test_a_wordless_barge_in_reissues_the_cancelled_reply(monkeypatch):
    monkeypatch.setenv("VOICE_FALSE_BARGE_IN_WINDOW_S", "0.05")
    svc, s = _service(), _session()
    svc._barge_in_events[s.call_id] = asyncio.Event()
    await svc._resume_after_false_barge_in(s, None, "I'm the existing patient.", time.monotonic())
    await asyncio.sleep(0)
    svc.handle_turn_end.assert_awaited_once()
    assert svc.handle_turn_end.await_args.kwargs["user_text"] == "I'm the existing patient."
    # The kept copy is dropped so the re-run does not put the line in twice.
    assert [m.content for m in s.conversation_history] == ["Are you a new or existing patient?"]
    assert svc._pending_llm_tasks[s.call_id]._turn_type == "final"


@pytest.mark.asyncio
async def test_real_words_after_the_barge_in_are_left_to_drive_the_next_turn(monkeypatch):
    monkeypatch.setenv("VOICE_FALSE_BARGE_IN_WINDOW_S", "0.05")
    svc, s = _service(), _session()
    svc._barge_in_events[s.call_id] = asyncio.Event()
    barge_at = time.monotonic()
    s._caller_last_text_at = barge_at + 0.01
    await svc._resume_after_false_barge_in(s, None, "I'm the existing patient.", barge_at)
    svc.handle_turn_end.assert_not_awaited()
    assert len(s.conversation_history) == 2


@pytest.mark.asyncio
async def test_no_resume_after_the_call_has_ended(monkeypatch):
    monkeypatch.setenv("VOICE_FALSE_BARGE_IN_WINDOW_S", "0.05")
    svc, s = _service(), _session()
    await svc._resume_after_false_barge_in(s, None, "I'm the existing patient.", time.monotonic())
    svc.handle_turn_end.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_resume_while_another_turn_is_already_running(monkeypatch):
    monkeypatch.setenv("VOICE_FALSE_BARGE_IN_WINDOW_S", "0.05")
    svc, s = _service(), _session()
    svc._barge_in_events[s.call_id] = asyncio.Event()
    running = asyncio.create_task(asyncio.sleep(1))
    svc._pending_llm_tasks[s.call_id] = running
    await svc._resume_after_false_barge_in(s, None, "I'm the existing patient.", time.monotonic())
    svc.handle_turn_end.assert_not_awaited()
    running.cancel()
