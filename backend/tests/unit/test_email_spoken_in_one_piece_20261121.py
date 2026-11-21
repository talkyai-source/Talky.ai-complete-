"""An email or web address is handed to speech inside ONE piece.

Probe capture (21 Nov 2026, real models, scenario ``email_caps``): the model's
"Maria.Gonzalez@example.com" arrived as the tokens ' Maria', '.G', 'onz', 'alez',
'@', ... and the streamer spoke "I have it as Maria." and then
"Gonzalez@example.com, ..." - a full stop and a pause inside the caller's own
address. The guard against a glued caller turn is unchanged by this fix.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.session import CallSession
from app.domain.services.voice_pipeline.action_tools import VOICE_ACTION_NAMES
from app.domain.services.voice_pipeline_service import VoicePipelineService

# Real token lists from the capture, one per model.
DEEPSEEK = ['I', "'ll", ' note', ' that', ' down', '.', ' ', 'I', ' have', ' it', ' as', ' Maria', '.G',
            'onz', 'alez', '@', 'example', '.com', ',', ' with', ' capital', ' M', ' and', ' capital',
            ' G', '.', ' Is', ' that', ' correct', '?']
GPT_OSS = ['Got', ' it', ',', ' Maria', '.G', 'onz', 'alez', '@', 'Example', '.com', '.', ' Is', ' that',
           ' the', ' email', ' you', '’d', ' like', ' us', ' to', ' use', '?']


class _Probe:
    """Streams tokens; records how many sentences had reached speech before each token."""

    supports_tools = True

    def __init__(self, tokens, error=None):
        self.tokens, self.error, self.counts, self._service = tokens, error, [], None

    def bind(self, service):
        self._service = service

    def _sent(self):
        return len(self._service.synthesize_and_send_audio.await_args_list)

    async def _stream(self, *a, **k):
        try:
            for t in self.tokens:
                self.counts.append(self._sent())
                yield t
            self.counts.append(self._sent())
            if self.error is not None:
                raise self.error
        finally:
            if len(self.counts) <= len(self.tokens):
                self.counts.append(self._sent())

    def stream_chat_with_timeout(self, *a, **k):
        return self._stream()

    def stream_chat_with_tools(self, *a, **k):
        return self._stream()


async def _run(tokens, monkeypatch, error=None):
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "60000")
    llm = _Probe(tokens, error)
    service = VoicePipelineService(
        stt_provider=AsyncMock(), llm_provider=llm, tts_provider=AsyncMock(), media_gateway=AsyncMock(),
    )
    service.latency_tracker = MagicMock()
    service.tts_provider._model_id = "deepgram-aura-2"
    service.synthesize_and_send_audio = AsyncMock(return_value=False)
    llm.bind(service)
    session = CallSession(
        call_id="call-email-one-piece", campaign_id="campaign-1", lead_id="lead-1", tenant_id="tenant-1",
        provider_call_id="provider-1", system_prompt="Use plain spoken text only.", voice_id="voice-1",
    )
    session.barge_in_event = asyncio.Event()
    session._voice_action_context_loaded = True
    session._voice_action_capabilities = {n: "synthetic test executor" for n in VOICE_ACTION_NAMES}
    service._barge_in_events[session.call_id] = session.barge_in_event
    await service._stream_llm_and_tts(session)
    spoken = [c.args[1] for c in service.synthesize_and_send_audio.await_args_list]
    return spoken, llm


@pytest.mark.asyncio
async def test_real_deepseek_capture_keeps_the_address_in_one_piece(monkeypatch):
    spoken, _ = await _run(DEEPSEEK, monkeypatch)
    # "capital G." is an initial, not a sentence end (unchanged): same piece as before.
    assert spoken == ["I'll note that down.",
                      "I have it as Maria.Gonzalez@example.com, with capital M and capital G. Is that correct?"]


@pytest.mark.asyncio
async def test_real_gpt_oss_capture_keeps_the_address_in_one_piece(monkeypatch):
    spoken, _ = await _run(GPT_OSS, monkeypatch)
    assert spoken == ["Got it, Maria.Gonzalez@Example.com.",
                      "Is that the email you’d like us to use?"]


@pytest.mark.asyncio
async def test_capitalised_address_with_a_capital_domain_is_one_piece(monkeypatch):
    spoken, _ = await _run(["Email", " us", " at", " John", ".S", "mith", "@", "Gmail", ".Com", " please", "."],
                           monkeypatch)
    assert spoken == ["Email us at John.Smith@Gmail.Com please."]


@pytest.mark.asyncio
async def test_the_same_address_in_one_chunk_is_one_piece(monkeypatch):
    spoken, _ = await _run(["Email us at John.Smith@Gmail.Com please. Thanks."], monkeypatch)
    assert spoken == ["Email us at John.Smith@Gmail.Com please.", "Thanks."]


@pytest.mark.asyncio
@pytest.mark.parametrize("tokens", [
    ["Sign", " up", " at", " https", "://", "Example", ".com", "/", "Signup", "?", "ref", "=", "Abc", " today", "."],
    ["Sign up at https://Example.com/Signup?ref=Abc today."],
])
async def test_web_address_with_a_query_is_one_piece(monkeypatch, tokens):
    spoken, _ = await _run(tokens, monkeypatch)
    assert spoken == ["Sign up at https://Example.com/Signup?ref=Abc today."]


# ---- the guard is unchanged ----

@pytest.mark.asyncio
@pytest.mark.parametrize("tokens,expected", [
    (["Is", " that", " john", "@", "gmail", ".com", "?", "Yes", " that's", " right", "."],
     ["Is that john@gmail.com?"]),
    (["So", " that's", " john", "@", "example", ".com", ".", "Yes", " that's", " right", "."],
     ["So that's john@example.com."]),
    (["Is that john@gmail.com?Yes that's right."], ["Is that john@gmail.com?"]),
    (["Is", " that", " right", "?", "Yes", ",", " that's", " right", "."], ["Is that right?"]),
    (["Is that right?Yes, that's right."], ["Is that right?"]),
])
async def test_a_glued_caller_turn_is_still_cut(monkeypatch, tokens, expected):
    spoken, _ = await _run(tokens, monkeypatch)
    assert spoken == expected


@pytest.mark.asyncio
async def test_ordinary_sentences_are_still_two_pieces_sent_as_early_as_before(monkeypatch):
    tokens = ["We", " open", " at", " 5am", ".", " We", " close", " at", " 9pm", "."]
    spoken, llm = await _run(tokens, monkeypatch)
    assert spoken == ["We open at 5am.", "We close at 9pm."]
    # counts[i] = sentences sent before token i was handed over: the first
    # sentence is sent while token 5 (" We") is processed.
    assert llm.counts[5] == 0 and llm.counts[6] == 1


# ---- the end of the stream or an error while the address is still arriving ----

@pytest.mark.asyncio
async def test_stream_end_while_the_address_is_arriving_loses_and_repeats_nothing(monkeypatch):
    spoken, _ = await _run(["Let", " me", " check", ".", " I", " have", " it", " as", " Maria", ".G",
                            "onz", "alez", "@"], monkeypatch)
    assert spoken == ["Let me check.", "I have it as Maria.Gonzalez@"]


@pytest.mark.asyncio
async def test_stream_error_while_the_address_is_arriving_repeats_nothing(monkeypatch):
    from app.infrastructure.llm.groq import LLMTimeoutError

    spoken, _ = await _run(["Let", " me", " check", ".", " I", " have", " it", " as", " Maria", ".G",
                            "onz", "alez", "@"], monkeypatch, error=LLMTimeoutError("stalled"))
    assert spoken == ["Let me check."]

def test_the_search_for_a_sentence_end_cannot_go_round_on_the_same_position():
    """A segmenter that keeps returning a position inside an address must not
    hang the call: the position is hidden once, then accepted as the end."""
    from app.domain.services.voice_pipeline.turn_streamer import _find_end

    text = "Email Maria.Gonzalez@example.com now."
    dot = text.index(".")
    calls = []

    def stubborn(masked, allow_clause=False, known_hosts=()):
        calls.append(masked)
        return dot

    assert _find_end(stubborn, text, False, ()) == (dot, -1)
    assert len(calls) == 2  # asked once, hid the letter, asked once more, stopped
