"""Live-risk voice packet (audit 2026-10-20): voice-1 .. voice-10."""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.services.voice_pipeline import tool_filler as tool_filler_mod
from app.domain.services.voice_pipeline.figure_grounding import ungrounded_figures
from app.domain.services.voice_pipeline.speech_guard import UNBACKED_ACTION_LINE, guard_spoken_sentence
from app.domain.services.voice_pipeline.tool_filler import ToolFiller


def _session(**results):
    return SimpleNamespace(
        _voice_action_results={k: {"action": k, "confirmation_allowed": v} for k, v in results.items()},
        conversation_history=[],
    )


# -- voice-1: passive claims and per-action confirmation ---------------------

@pytest.mark.parametrize("sentence", [
    "The email has been sent to you.",
    "Your callback has been scheduled for 3pm.",
    "You're all booked in for Tuesday.",
    "You're being transferred now.",
])
def test_passive_done_claims_are_replaced_without_a_result(sentence):
    assert guard_spoken_sentence(_session(), sentence) == UNBACKED_ACTION_LINE


@pytest.mark.parametrize("sentence", [
    "I've booked you in for Tuesday.",
    "Your callback has been scheduled for 3pm.",
])
def test_a_confirmed_email_does_not_back_a_booking_claim(sentence):
    assert guard_spoken_sentence(_session(send_email=True), sentence) == UNBACKED_ACTION_LINE


def test_a_confirmed_callback_does_not_back_an_email_claim():
    assert guard_spoken_sentence(_session(schedule_callback=True), "I have sent the email.") == UNBACKED_ACTION_LINE


def test_each_claim_passes_with_its_own_confirmed_action():
    assert guard_spoken_sentence(_session(send_email=True), "The email has been sent to you.") \
        == "The email has been sent to you."
    assert guard_spoken_sentence(_session(schedule_callback=True), "I've booked you in for Tuesday.") \
        == "I've booked you in for Tuesday."
    assert guard_spoken_sentence(_session(transfer_call=True), "I've transferred you.") == "I've transferred you."
    assert guard_spoken_sentence(_session(send_email=False), "I have sent the email.") == UNBACKED_ACTION_LINE


@pytest.mark.parametrize("sentence", [
    "The demo is scheduled every Monday.",
    "Your appointment was booked last week.",
    "Is the email sent automatically?",
])
def test_ordinary_statements_are_not_claims(sentence):
    assert guard_spoken_sentence(_session(), sentence) == sentence


# -- voice-2: currency glued to the digits is still a figure -----------------

@pytest.mark.parametrize("sentence", [
    "The fare is Rs.1760 per seat.",
    "It costs PKR1760.",
    "Total is Rs.1,760.",
    "Total is Rs1760.",
])
def test_glued_currency_figures_are_checked(sentence):
    figures, _ = ungrounded_figures(sentence, ["nothing 5"])
    assert len(figures) == 1 and "1" in figures[0]


def test_glued_currency_figure_that_is_sourced_passes():
    assert ungrounded_figures("The fare is Rs.1760 per seat.", ["Fare: Rs. 1,760"]) == ([], [])


# -- voice-4 / voice-5: the filler follows the turn --------------------------

async def test_cancelling_the_turn_cancels_a_pending_filler():
    spoken = []

    async def speak(text):
        spoken.append(text)
        return False

    loop = asyncio.get_running_loop()
    filler = ToolFiller(speak, turn_start=loop.time(), turn_index=0, after_s=0.1, clock=loop.time)
    filler.arm()
    filler.cancel()
    await asyncio.sleep(0.25)
    assert spoken == []


async def test_cancelling_the_turn_stops_a_speaking_filler():
    state = []

    async def speak(text):
        state.append("start")
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            state.append("cancelled")
            raise
        return False

    loop = asyncio.get_running_loop()
    filler = ToolFiller(speak, turn_start=loop.time() - 5, turn_index=0, after_s=0.1, clock=loop.time)
    filler.arm()
    await asyncio.sleep(0.05)
    filler.cancel()
    await asyncio.sleep(0.05)
    assert state == ["start", "cancelled"]


async def test_an_interrupted_filler_is_reported():
    async def speak(text):
        return True  # the caller talked over it

    loop = asyncio.get_running_loop()
    filler = ToolFiller(speak, turn_start=loop.time() - 5, turn_index=0, after_s=0.1, clock=loop.time)
    filler.arm()
    await asyncio.sleep(0.01)
    await filler.settle()
    assert filler.interrupted is True


async def _turn_with_tool(monkeypatch, tool_name, *, filler_interrupts=False, cancel=False, after_s=0.0,
                          filler_fails=False):
    from tests.unit.test_model_driven_voice_turn import setup_turn
    from app.domain.services.voice_pipeline import turn_streamer

    monkeypatch.setattr(tool_filler_mod, "TOOL_FILLER_AFTER_S", after_s)
    service, session, rounds = setup_turn(monkeypatch, "Okay, bye.", [])
    provider = service.llm_provider

    async def model(messages, **kwargs):
        rounds.append(kwargs)
        if len(rounds) == 1:
            kwargs["tool_calls_sink"].append({
                "id": "c1", "name": tool_name, "arguments": {}, "arguments_raw": "{}",
                "arguments_valid": True,
            })
        else:
            await asyncio.sleep(0.3)  # the slow second round after the tool
            yield "Goodbye."

    monkeypatch.setattr(provider, "stream_chat_with_timeout", model)
    ran = asyncio.Event()

    async def run_action(*a, **k):
        ran.set()
        return {"action": tool_name, "success": True}

    monkeypatch.setattr(turn_streamer, "run_voice_action", run_action)
    monkeypatch.setattr(
        turn_streamer, "action_tools_for_turn",
        lambda *a, **k: [{"type": "function", "function": {
            "name": tool_name, "parameters": {"type": "object", "properties": {}}}}],
    )
    results = []

    async def synth(sess, text, ws, **kw):
        results.append(text)
        if filler_fails and text in tool_filler_mod.FILLER_PHRASES:
            sess._tts_delivery_failed = True  # TTS failure also returns True
            return True
        return filler_interrupts and text in tool_filler_mod.FILLER_PHRASES

    service.synthesize_and_send_audio = AsyncMock(side_effect=synth)
    task = asyncio.ensure_future(service._stream_llm_and_tts(session))
    if cancel:
        await asyncio.wait_for(ran.wait(), 10)
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.5)
        return results
    await task
    assert ran.is_set(), "the tool never ran, so this proves nothing"
    return results


async def test_end_call_never_arms_the_filler(monkeypatch):
    spoken = await _turn_with_tool(monkeypatch, "end_call")
    assert not [s for s in spoken if s in tool_filler_mod.FILLER_PHRASES]


async def test_a_slow_action_still_gets_the_filler(monkeypatch):
    spoken = await _turn_with_tool(monkeypatch, "send_email")
    assert [s for s in spoken if s in tool_filler_mod.FILLER_PHRASES]


async def test_barge_in_during_the_filler_stops_the_real_answer(monkeypatch):
    spoken = await _turn_with_tool(monkeypatch, "send_email", filler_interrupts=True)
    assert spoken and spoken[0] in tool_filler_mod.FILLER_PHRASES
    assert "Goodbye." not in spoken


async def test_cancelling_the_turn_task_silences_the_filler(monkeypatch):
    spoken = await _turn_with_tool(monkeypatch, "send_email", cancel=True, after_s=0.2)
    assert spoken == []


# -- voice-7: only consecutive TTS failures are terminal ---------------------

async def test_isolated_tts_failures_far_apart_do_not_hang_up():
    from tests.unit.test_tts_empty_stream import _Pipeline, _Provider, _session as tts_session
    from app.domain.services.voice_pipeline.tts_playback import TtsPlayback

    good = [b"\x01\x02" * 80]
    provider = _Provider([[], [], good, [], []])  # fail (2 tries), ok, fail (2 tries)
    pipe = _Pipeline(provider)
    playback = TtsPlayback(pipeline=pipe)
    session = tts_session()
    await playback.synthesize_and_send(session, "First", None, track_latency=False)
    await playback.synthesize_and_send(session, "Fine", None, track_latency=False)
    await playback.synthesize_and_send(session, "Third", None, track_latency=False)
    assert pipe.media_gateway.hangups == []
    assert len(pipe.media_gateway.clips) == 2


# -- voice-8: figures from a multi-page section are grounded -----------------

async def test_source_page_text_grounds_figures(monkeypatch):
    from app.domain.services.voice_pipeline import knowledge_tool

    page = {"status": "source_page", "passages": [], "text": "",
            "source_text": "Sleeper fare: 1,760 rupees"}
    monkeypatch.setattr(knowledge_tool, "run_section_request", lambda catalog, args: page)
    monkeypatch.setattr(knowledge_tool, "_session_catalog", lambda s: None)
    monkeypatch.setattr(knowledge_tool, "serialize_section_result", lambda r: "{}")
    monkeypatch.setattr(knowledge_tool, "fence_kb_result", lambda t, **k: t)
    session = SimpleNamespace(call_id="c")
    await knowledge_tool.run_knowledge_lookup(session, {})
    assert session._knowledge_grounding == ["Sleeper fare: 1,760 rupees"]
    assert ungrounded_figures("It is 1,760 rupees.", session._knowledge_grounding) == ([], [])


# -- voice-9: an unknown model's 8k default is loud --------------------------

def test_unknown_model_window_is_logged(caplog):
    from app.services.scripts.knowledge import budget

    budget._WARNED_UNKNOWN_WINDOW.discard("totally-unknown-model") if hasattr(budget, "_WARNED_UNKNOWN_WINDOW") else None
    with caplog.at_level(logging.WARNING):
        assert budget.context_window_for("totally-unknown-model") == 8192
    assert "context_window_unknown" in caplog.text


# -- voice-10: vendor names the campaign's own knowledge states --------------

def test_vendor_named_in_approved_knowledge_is_not_scrubbed():
    from app.services.scripts.prompts import prompt_safety
    from app.services.scripts.prompts.prompt_safety import scan_output_for_leakage

    text = "We integrate with Twilio for SMS. Happy to help."
    assert scan_output_for_leakage(text)[0] is True  # default net unchanged
    exempt = prompt_safety.vendor_names_in("Our product plugs into Twilio SIP trunks.")
    leaked, safe = scan_output_for_leakage(text, exempt)
    assert leaked is False and safe == text
    assert prompt_safety.vendor_names_in("no vendors here") == ()


def test_vendor_exemption_does_not_cover_self_disclosure():
    from app.services.scripts.prompts import prompt_safety
    from app.services.scripts.prompts.prompt_safety import scan_output_for_leakage

    exempt = prompt_safety.vendor_names_in("Our product plugs into Twilio SIP trunks.")
    for text in (
        "I am powered by Twilio.",
        "I'm built on Twilio.",
        "This assistant runs on Twilio.",
        "My voice is made by Twilio.",
    ):
        leaked, safe = scan_output_for_leakage(text, exempt)
        assert leaked is True and "Twilio" not in safe, text
    # the business-fact case still passes
    assert scan_output_for_leakage("We integrate with Twilio for SMS.", exempt)[0] is False


# -- voice-3: a silent primary on the tool path fails over at the deadline ---

class _ToolFake:
    supports_streaming = True
    supports_tools = True

    def __init__(self, name, delay, tokens=("hi",), run_tool=False):
        self.name, self.delay, self.tokens, self.run_tool = name, delay, tokens, run_tool

    async def stream_chat_with_tools(self, *a, **kw):
        if self.run_tool:
            await kw["tool_runner"]("x", {})
        await asyncio.sleep(self.delay)
        for t in self.tokens:
            yield t


def _resilient(primary, secondary, deadline=0.2):
    from app.domain.services.resilient_llm import LLMFailoverPolicy, ResilientLLMProvider
    return ResilientLLMProvider(primary, secondary, LLMFailoverPolicy(first_token_deadline_seconds=deadline))


async def test_silent_primary_on_tool_path_fails_over_at_the_deadline():
    w = _resilient(_ToolFake("p", 2.0, ("late",)), _ToolFake("s", 0.0, ("ok",)))
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    out = [t async for t in w.stream_chat_with_tools([], tool_runner=AsyncMock())]
    assert out == ["ok"] and loop.time() - t0 < 1.0


async def test_a_running_tool_is_never_cut_off_by_the_deadline():
    runner = AsyncMock(return_value="done")
    w = _resilient(_ToolFake("p", 0.4, ("answer",), run_tool=True), _ToolFake("s", 0.0, ("other",)))
    out = [t async for t in w.stream_chat_with_tools([], tool_runner=runner)]
    assert out == ["answer"] and runner.await_count == 1


async def test_fast_primary_on_tool_path_is_untouched():
    w = _resilient(_ToolFake("p", 0.0, ("a", "b")), _ToolFake("s", 0.0, ("x",)))
    assert [t async for t in w.stream_chat_with_tools([], tool_runner=AsyncMock())] == ["a", "b"]


async def test_a_filler_whose_tts_fails_is_not_a_barge_in(monkeypatch):
    results = await _turn_with_tool(monkeypatch, "send_email", filler_fails=True)
    assert any(r in tool_filler_mod.FILLER_PHRASES for r in results)
    assert "Goodbye." in results, "the real answer must still be spoken"
