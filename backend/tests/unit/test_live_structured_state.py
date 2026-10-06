"""C2: evidence-backed structured state on every voice-model turn."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import asyncio
import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.models.session import CallSession
from app.domain.services.voice_pipeline.live_structured_state import MAX_LIVE_STATE_BLOCK_CHARS, ConfirmedContactsEvidence, IdentityEvidence, LiveConversationState, ToolResultEvidence, reduce_live_state, replace_live_state_block, render_live_state_block
from app.realtime.bridge import RealtimeBridge
from app.domain.services.voice_pipeline.turn_streamer import TurnStreamer
from app.realtime.openai import (
    OpenAIRealtimeSession,
    RealtimeEvent,
)
from app.services.scripts.prompts.live_state import build_live_state_block
from app.services.scripts.call_state_tracker import CallState as CapturedCallState
from app.realtime.prompts import (
    RealtimePersona,
    build_realtime_instructions,
)


def test_initial_state_is_explicit_deterministic_and_bounded():
    state = LiveConversationState()

    first = render_live_state_block(state)
    second = render_live_state_block(state)

    assert first == second
    assert "identity_introduced=unknown" in first
    assert "decision_maker=" not in first
    assert "current_provider=" not in first
    assert "pain_priority=" not in first
    assert "interest_level=" not in first
    assert "refusal_count=" not in first
    assert "requested_next_action=" not in first
    assert "confirmed_contacts=none" in first
    assert "last_tool_result=unknown" in first
    assert "sales_stage=" not in first
    assert len(first) <= MAX_LIVE_STATE_BLOCK_CHARS


def test_only_confirmed_contact_values_enter_the_state_block():
    state = reduce_live_state(
        LiveConversationState(),
        ConfirmedContactsEvidence(
            email="wrong@example.com",
            email_confirmed=False,
            phone="+442012345678",
            phone_confirmed=True,
        ),
    )
    block = render_live_state_block(state)

    assert "wrong@example.com" not in block
    assert "+442012345678" in block

    corrected_pending = reduce_live_state(
        state,
        ConfirmedContactsEvidence(
            email="right@example.com",
            email_confirmed=False,
            phone="+442099999999",
            phone_confirmed=False,
        ),
    )
    assert "confirmed_contacts=none" in render_live_state_block(corrected_pending)


def test_tool_result_requires_a_deterministic_boolean_outcome():
    state = reduce_live_state(
        LiveConversationState(),
        ToolResultEvidence(tool_name="send_email", success=False, code="unavailable"),
    )
    block = render_live_state_block(state)
    assert "last_tool_result=send_email:failed:unavailable" in block
    assert "sales_stage=next_step" not in block

    state = reduce_live_state(
        state,
        ToolResultEvidence(tool_name="schedule_callback", success=True, code="scheduled"),
    )
    block = render_live_state_block(state)
    assert "last_tool_result=schedule_callback:succeeded:scheduled" in block
    assert "sales_stage=" not in block


def test_state_replacement_rejects_unmarked_or_oversized_blocks():
    with pytest.raises(ValueError, match="invalid live structured state block"):
        replace_live_state_block("BASE", "decision_maker=yes")
    with pytest.raises(ValueError, match="invalid live structured state block"):
        replace_live_state_block("BASE", "x" * (MAX_LIVE_STATE_BLOCK_CHARS + 1))


def test_existing_cascaded_live_block_carries_structured_state_once():
    structured = render_live_state_block(LiveConversationState())
    block = build_live_state_block(
        agent_name="Sarah",
        company_name="Acme",
        has_introduced=False,
        structured_state_block=structured,
    )

    assert block.count("LIVE STRUCTURED STATE v1") == 1
    assert "decision_maker=" not in block


class _Latency:
    def mark_llm_first_token(self, _call_id):
        pass

    def mark_llm_end(self, _call_id):
        pass

    def mark_tts_start(self, _call_id):
        pass


class _CapturingLLM:
    _model = "fake-model"
    _primary = None
    _secondary = None

    def __init__(self):
        self.system_prompt = ""

    def stream_chat_with_timeout(self, _messages, *, system_prompt, temperature, max_tokens, **_kwargs):
        self.system_prompt = system_prompt

        async def _tokens():
            yield "Thanks."

        return _tokens()


class _Pipeline:
    def __init__(self):
        self._barge_in_events = {}
        self._barge_in_epoch = {}
        self.llm_provider = _CapturingLLM()
        self.latency_tracker = _Latency()

    def _supports_llm_end_session_action(self, _session):
        return False


    @staticmethod
    def _find_sentence_end(buf, allow_clause=False, *, known_hosts=()):
        from app.domain.services.voice_pipeline.sentence_segmentation import find_sentence_end
        return find_sentence_end(buf, allow_clause=allow_clause, known_hosts=known_hosts)

    async def synthesize_and_send_audio(self, _session, _sentence, _websocket, track_latency=False):
        return False


@pytest.mark.asyncio
async def test_cascaded_turn_injects_current_structured_state(monkeypatch):
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    session = CallSession(
        call_id="call-1",
        campaign_id="campaign-1",
        lead_id="lead-1",
        provider_call_id="provider-1",
        system_prompt="BASE",
        voice_id="voice-1",
        conversation_history=[
            Message(
                role=MessageRole.USER,
                content="I'm the decision maker and I'm very interested; call me back.",
            )
        ],
    )
    session.captured_slots = CapturedCallState(
        email="me@example.com",
        email_confirmed=True,
        phone=None,
        phone_confirmed=False,
        declined_count=0,
    )
    pipeline = _Pipeline()

    await TurnStreamer(pipeline).stream(session)

    prompt = pipeline.llm_provider.system_prompt
    assert prompt.count("LIVE STRUCTURED STATE v1") == 1
    assert "decision_maker=" not in prompt
    assert "interest_level=" not in prompt
    assert "requested_next_action=" not in prompt
    assert session._live_structured_state.decision_maker.value == "unknown"
    assert session._live_structured_state.interest_level.value == "unknown"
    assert "email:me@example.com" in prompt


def test_realtime_base_instructions_always_include_initial_state():
    instructions = build_realtime_instructions(
        RealtimePersona(agent_name="Sarah", company_name="Acme")
    )
    assert instructions.count("LIVE STRUCTURED STATE v1") == 1
    assert "decision_maker=" not in instructions


class _RecordingWS:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(json.loads(payload))


@pytest.mark.asyncio
async def test_realtime_session_replaces_state_without_prompt_growth():
    session = OpenAIRealtimeSession(
        api_key="sk-test",
        instructions=build_realtime_instructions(RealtimePersona()),
    )
    session._ws = _RecordingWS()
    updated = render_live_state_block(
        reduce_live_state(LiveConversationState(), IdentityEvidence(introduced=True))
    )

    await session.update_live_state(updated)
    await session.update_live_state(updated)

    assert len(session._ws.sent) == 1
    payload = session._ws.sent[0]
    assert payload["type"] == "session.update"
    instructions = payload["session"]["instructions"]
    assert instructions.count("LIVE STRUCTURED STATE v1") == 1
    assert "identity_introduced=yes" in instructions
    assert len(instructions) < 10_000


@pytest.mark.asyncio
async def test_realtime_bridge_reduces_final_user_turn_and_publishes_before_next_turn():
    blocks = []

    async def _events():
        yield RealtimeEvent(
            kind="caller_transcript",
            text="I'm the decision maker and please email me the details.",
            is_final=True,
        )

    class _RT:
        def events(self):
            return _events()

        async def update_live_state(self, block):
            blocks.append(block)

    bridge = RealtimeBridge(
        call_id="call-rt",
        realtime_session=_RT(),
        media_gateway=object(),
        greet_on_start=False,
    )

    await bridge._pump_model_events()

    assert blocks
    assert "decision_maker=" not in blocks[-1]
    assert "requested_next_action=" not in blocks[-1]
    assert bridge._live_state.decision_maker.value == "unknown"
    assert bridge._latest_caller_text == "I'm the decision maker and please email me the details."


@pytest.mark.asyncio
async def test_realtime_transcript_alone_does_not_interpret_or_confirm_contacts():
    rt = type("RT", (), {"update_live_state": AsyncMock(), "interrupt_with_text": AsyncMock()})()
    bridge = RealtimeBridge(call_id="contact-binding", realtime_session=rt, media_gateway=object())
    await bridge._observe_contact_turn("My email is bob@example.com")
    await bridge._observe_contact_turn("yes")
    assert bridge._contact_session.captured_slots.email is None
    assert bridge._live_state.confirmed_email is None
    rt.interrupt_with_text.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("interrupted", "expects_identity"),
    [(False, True), (True, False)],
)
async def test_realtime_identity_requires_uninterrupted_opening_delivery(
    interrupted, expects_identity
):
    blocks = []
    started = asyncio.Event()

    async def _events():
        yield RealtimeEvent(kind="response_candidate", text="Hello, I'm Sarah from Acme.", audio=b"\xff" * 320)
        await started.wait()
        if interrupted:
            yield RealtimeEvent(kind="interrupted", raw={"during_response": True})

    class _RT:
        def events(self):
            return _events()

        async def update_live_state(self, block):
            blocks.append(block)

    class _Gateway:
        async def begin_playback(self, _call_id, _utterance_id):
            return None

        async def send_audio(self, *_args):
            started.set()
            if interrupted:
                await asyncio.Event().wait()

        async def finish_playback(self, _call_id, utterance_id):
            return {"utterance_id": utterance_id, "status": "completed",
                    "evidence": "transport_played", "played_ms": 40}

        async def clear_output_buffer(self, _call_id):
            return None

    bridge = RealtimeBridge(
        call_id="call-opening-proof", realtime_session=_RT(),
        media_gateway=_Gateway(), greet_on_start=True,
    )
    await asyncio.wait_for(bridge._pump_model_events(), 1)
    await asyncio.gather(bridge._playback_task, return_exceptions=True)
    if expects_identity:
        assert blocks and "identity_introduced=yes" in blocks[-1]
    else:
        assert blocks and "identity_introduced=unknown" in blocks[-1]
        assert "opening=interrupted" in replace_live_state_block("BASE", blocks[-1])
        assert bridge._live_state.identity_introduced is None


@pytest.mark.asyncio
async def test_realtime_tool_result_is_published_before_model_continuation():
    order = []

    class _FC:
        name = "knowledge_lookup"
        call_id = "tool-1"

        def parsed_arguments(self):
            return {"section_ids": ["hours-ref"]}

    class _RT:
        async def update_live_state(self, block):
            order.append(("state", block))

        async def send_function_result(self, _call_id, output):
            order.append(("result", output))

    bridge = RealtimeBridge(
        call_id="call-rt",
        realtime_session=_RT(),
        media_gateway=object(),
    )
    knowledge = {"status": "available", "text": "We open at nine.", "sources": [], "source_policy": "current_lookup"}
    bridge._lookup_knowledge = AsyncMock(return_value=knowledge)

    await bridge._handle_function_call(_FC())

    assert order[0][0] == "state"
    assert "last_tool_result=knowledge_lookup:succeeded:available" in order[0][1]
    assert order[1] == ("result", knowledge)


@pytest.mark.asyncio
async def test_realtime_action_result_is_published_before_model_continuation():
    order = []

    class _FC:
        name = "send_email"
        call_id = "tool-action-1"

        def parsed_arguments(self):
            return {"recipient": "confirmed@example.com", "purpose": "details"}

    class _RT:
        async def update_live_state(self, block):
            order.append(("state", block))

        async def send_function_result(self, _call_id, output):
            order.append(("result", output))

    bridge = RealtimeBridge(
        call_id="call-rt-action",
        realtime_session=_RT(),
        media_gateway=object(),
    )

    await bridge._handle_function_call(_FC())

    assert order[0][0] == "state"
    assert "last_tool_result=send_email:failed:unavailable" in order[0][1]
    assert order[1][0] == "result"
    assert order[1][1]["action"] == "send_email"
    assert order[1][1]["success"] is False
    assert order[1][1]["confirmation_allowed"] is False
