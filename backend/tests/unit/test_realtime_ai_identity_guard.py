"""Realtime path: a held reply that claims to be human is never played.

The speech-to-speech bridge already holds a reply's audio until the whole reply
and its transcript are complete. This guard tests that transcript with the same
`_claims_human` the cascaded path uses, before any audio reaches the phone.
Fake model events and a fake gateway; the transcript is the model's own account
of its audio, so this proves the bridge logic, not the live service.
"""
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.realtime.bridge import RealtimeBridge
from app.realtime.openai import OpenAIRealtimeSession, RealtimeEvent

IDENTITY = "I'm an AI assistant for Acme."
CLAIMS = [
    "I'm a real person, not a robot.",
    "I am a real person, yes.",
    "No, I'm not a robot.",
    "I'm not an AI.",
    "This is a real person speaking.",
]
PASSES = [
    "I'm an AI assistant calling from Acme.",
    "I'm not recording this call.",
    "I can connect you to a real person.",
    "I'm not a real person, I'm an AI assistant.",
    "Our plans start with a free trial.",
    "I'm not sure about that.",
]


def make(company="Acme", opted_out=False):
    sent = []

    async def send_audio(call_id, pcm):
        sent.append(pcm)

    gw = SimpleNamespace(
        send_audio=send_audio, begin_playback=AsyncMock(), flush_audio_buffer=AsyncMock(),
        finish_playback=AsyncMock(return_value={"utterance_id": "rt-1", "status": "completed",
                                                "evidence": "transport_played", "played_ms": 40}),
        send_control_event=AsyncMock())
    rt = SimpleNamespace(repair_unspoken_response=AsyncMock(), supports_response_metadata=False)
    session = SimpleNamespace(agent_config=SimpleNamespace(company_name=company),
                              _caller_opted_out=opted_out)
    bridge = RealtimeBridge(call_id="call-identity-1234567890", realtime_session=rt, media_gateway=gw,
                            action_session=session, greet_on_start=False)
    return bridge, rt, gw, sent


async def feed(bridge, *texts):
    async def events():
        for text in texts:
            yield RealtimeEvent(kind="response_candidate", text=text, audio=b"\xff" * 320,
                                raw={"response": {"id": "r1", "output": []}})
            if bridge._playback_task:
                await bridge._playback_task
    bridge._rt.events = events
    await bridge._pump_model_events()
    if bridge._playback_task:
        await bridge._playback_task


@pytest.mark.asyncio
@pytest.mark.parametrize("text", CLAIMS)
async def test_human_claim_is_never_played_and_is_repaired(text, caplog):
    bridge, rt, gw, sent = make()
    with caplog.at_level(logging.WARNING):
        await feed(bridge, text)
    assert sent == []
    gw.begin_playback.assert_not_awaited()
    gw.send_control_event.assert_not_awaited()
    rt.repair_unspoken_response.assert_awaited_once()
    assert rt.repair_unspoken_response.await_args.kwargs["required_text"] == IDENTITY
    lines = [r.getMessage() for r in caplog.records if "ai_identity" in r.getMessage()]
    assert len(lines) == 1 and "repaired" in lines[0]
    assert text not in lines[0] and "call-identity-1234567890" not in lines[0]
    assert bridge._repair_attempted is True


@pytest.mark.asyncio
async def test_identity_line_without_company_uses_the_functions_fallback():
    bridge, rt, _, _ = make(company="")
    await feed(bridge, "I'm a real person.")
    assert rt.repair_unspoken_response.await_args.kwargs["required_text"] == "I'm an AI assistant."


@pytest.mark.asyncio
@pytest.mark.parametrize("text", PASSES)
async def test_ordinary_replies_play_with_no_repair(text):
    bridge, rt, gw, sent = make()
    await feed(bridge, text)
    assert len(sent) == 1
    gw.begin_playback.assert_awaited_once()
    rt.repair_unspoken_response.assert_not_awaited()
    assert bridge._repair_attempted is False


@pytest.mark.asyncio
async def test_repaired_identity_reply_is_played():
    bridge, rt, _, sent = make()
    await feed(bridge, "I'm a real person.", IDENTITY)
    assert len(sent) == 1
    rt.repair_unspoken_response.assert_awaited_once()


@pytest.mark.asyncio
async def test_repaired_reply_that_trips_again_is_never_played_and_does_not_loop(caplog):
    bridge, rt, gw, sent = make()
    with caplog.at_level(logging.WARNING):
        await feed(bridge, "I'm a real person.", "No really, I'm not a robot.")
    assert sent == []
    gw.begin_playback.assert_not_awaited()
    rt.repair_unspoken_response.assert_awaited_once()
    assert "repair budget" in bridge._failure_reason
    lines = [r.getMessage() for r in caplog.records if "ai_identity" in r.getMessage()]
    assert len(lines) == 2 and "unrepaired" in lines[1]


@pytest.mark.asyncio
async def test_budget_spent_for_another_reason_means_no_repair_and_no_audio():
    bridge, rt, _, sent = make()
    bridge._repair_attempted = True
    await feed(bridge, "I'm a real person.")
    assert sent == []
    rt.repair_unspoken_response.assert_not_awaited()
    assert bridge._failure_reason


@pytest.mark.asyncio
async def test_opt_out_gate_wins_when_a_reply_trips_both():
    bridge, rt, _, sent = make(opted_out=True)
    bridge._latest_caller_text = "Please don't call me again."
    await feed(bridge, "I'm a real person.")
    assert sent == []
    rt.repair_unspoken_response.assert_awaited_once()
    kwargs = rt.repair_unspoken_response.await_args.kwargs
    assert kwargs["required_text"] != IDENTITY
    assert "required_reason" not in kwargs  # the opt-out caller keeps its own default wording
    assert bridge._opt_out_repair["text"] == kwargs["required_text"]
    assert not bridge._failure_reason


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [""])
async def test_empty_transcript_is_unchanged(text):
    bridge, rt, _, sent = make()
    await feed(bridge, text)
    assert len(sent) == 1
    rt.repair_unspoken_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_do_not_call_repair_instruction_is_unchanged_and_identity_reuses_the_path():
    provider = OpenAIRealtimeSession(api_key="test", instructions="POLICY")
    provider._ws = SimpleNamespace(send=AsyncMock())
    await provider.repair_unspoken_response({}, required_text="X")
    sent = json.loads(provider._ws.send.await_args.args[0])["response"]["instructions"]
    assert sent == (
        "POLICY\nREPAIR THIS TURN: Your previous reply was withheld and was not heard. Give one short honest "
        "reply. Keep required contact readbacks and source qualifications. Reuse completed tool results; do "
        "not repeat actions. Do not claim an email, booking, submission or transfer succeeded unless a "
        "matching successful tool result permits confirmation."
        "\nThe do-not-call write is not acknowledged. Say exactly the following text, "
        "with no additions and no tool calls: X")
    await provider.repair_unspoken_response({}, required_text=IDENTITY, required_reason="Your previous reply was not allowed.")
    sent = json.loads(provider._ws.send.await_args.args[0])["response"]["instructions"]
    assert "do-not-call" not in sent
    assert sent.endswith("\nYour previous reply was not allowed. Say exactly the following text, "
                         "with no additions and no tool calls: " + IDENTITY)
