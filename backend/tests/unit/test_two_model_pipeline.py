"""The voice pipeline tuned for the two production models — Cerebras
gpt-oss-120b and Groq openai/gpt-oss-20b — after the 2026-09-06 audit.

Covers: the Cerebras reasoning reserve (F01), two-way failover between the
pair (F06), the campaign cache key on every turn path (F10) and the knowledge
entry fitting that keeps a node's spoken answer when its source is trimmed.
"""
from __future__ import annotations

import inspect

import pytest

from app.domain.models.conversation import Message, MessageRole
from app.domain.services.voice_orchestrator import VoiceOrchestrator
from app.domain.services.voice_pipeline import kb_budget
from app.domain.services.voice_pipeline.kb_budget import fit_kb_body
from app.infrastructure.llm import cerebras as cerebras_module
from app.infrastructure.llm.cerebras import CerebrasLLMProvider
from app.services.scripts.knowledge.retrieval import render_node_answer


# ---------------------------------------------------------------------------
# Cerebras: reasoning reserve
# ---------------------------------------------------------------------------

def _provider(model: str, max_tokens: int = 90) -> CerebrasLLMProvider:
    p = CerebrasLLMProvider()
    p._model = model
    p._temperature = 0.6
    p._max_tokens = max_tokens
    return p


def test_gpt_oss_gets_the_reasoning_reserve_on_top_of_the_answer_budget():
    req = _provider("gpt-oss-120b")._build_request(
        messages=[Message(role=MessageRole.USER, content="hi")],
        system_prompt="s", temperature=None, max_tokens=90, model=None, tools=None,
    )
    assert req["reasoning_effort"] == "low"
    assert req["max_completion_tokens"] == 90 + cerebras_module._THINKING_RESERVE_TOKENS


def test_confirmation_probe_budget_is_no_longer_three_tokens_total():
    """confirm_llm asks for max_tokens=3; on Cerebras that returned an EMPTY
    'length' completion because reasoning consumed the whole cap."""
    req = _provider("gpt-oss-120b")._build_request(
        messages=[Message(role=MessageRole.USER, content="yes or no?")],
        system_prompt="s", temperature=0.0, max_tokens=3, model=None, tools=None,
    )
    assert req["max_completion_tokens"] >= 3 + 1024


def test_models_with_reasoning_off_keep_the_exact_budget():
    req = _provider("gemma-4-31b")._build_request(
        messages=[Message(role=MessageRole.USER, content="hi")],
        system_prompt="s", temperature=None, max_tokens=120, model=None, tools=None,
    )
    assert req["reasoning_effort"] == "none"
    assert req["max_completion_tokens"] == 120


# ---------------------------------------------------------------------------
# Failover pairing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "primary, env, expected",
    [
        # prod today: Cerebras primary, env secondary = Groq 20B
        (("cerebras", "gpt-oss-120b"), ("groq", "openai/gpt-oss-20b"), ("groq", "openai/gpt-oss-20b")),
        # tenant picks Groq 20B while env still names Groq 20B -> counterpart
        (("groq", "openai/gpt-oss-20b"), ("groq", "openai/gpt-oss-20b"), ("cerebras", "gpt-oss-120b")),
        # no env at all: same-provider default equals the primary -> counterpart
        (("groq", "openai/gpt-oss-20b"), (None, None), ("cerebras", "gpt-oss-120b")),
        (("cerebras", "gpt-oss-120b"), (None, None), ("groq", "openai/gpt-oss-20b")),
        # explicit different secondary is honoured verbatim
        (("cerebras", "gpt-oss-120b"), ("gemini", "gemini-2.5-flash"), ("gemini", "gemini-2.5-flash")),
    ],
)
def test_secondary_selection_pairs_the_two_models_both_ways(primary, env, expected):
    picked = VoiceOrchestrator._pick_secondary_llm(
        primary_provider=primary[0], primary_model=primary[1],
        env_provider=env[0], env_model=env[1],
    )
    assert picked == expected


def test_secondary_selection_gives_up_only_when_nothing_distinct_exists():
    # Unknown provider, env points at itself, no counterpart known.
    assert VoiceOrchestrator._pick_secondary_llm(
        primary_provider="mystery", primary_model="m1", env_provider="mystery", env_model="m1",
    ) is None


# ---------------------------------------------------------------------------
# Cache key on every turn path
# ---------------------------------------------------------------------------

def test_turn_streamer_threads_the_campaign_cache_key_on_both_llm_paths():
    from app.domain.services.voice_pipeline.turn_streamer import TurnStreamer

    src = inspect.getsource(TurnStreamer)
    assert src.count('campaign_id=getattr(session, "campaign_id", None)') >= 2


# ---------------------------------------------------------------------------
# Knowledge entry fitting
# ---------------------------------------------------------------------------

_RATES_NODE = {
    "heading": '"What are your rates?"',
    "content": (
        "CRITICAL RULE: NEVER quote a specific rate without knowing monthly volume. "
        "Rates are not publicly published. This is by design - your rate depends on: "
        "- Monthly card turnover - Card mix (debit vs credit vs corporate vs international) "
        "- Business type - Contract type. "
        "Script: Good question, and I want to be straight with you. Dojo does not publish "
        "a single rate because it depends on your monthly volume and card mix. "
        "Typical blended rates land between 1.2% and 1.9% for most restaurants."
    ),
    "voice_answer": "We don't publish a single rate as it depends on your monthly volume and card mix. I can get the right number for your setup.",
}


def test_fit_keeps_the_spoken_answer_when_the_source_is_trimmed():
    rendered = render_node_answer(_RATES_NODE)
    body = fit_kb_body(rendered, _RATES_NODE, 350)
    assert len(body) <= 350 + 2
    assert "…" in body                                   # the model is told the source was cut
    assert "depends on your monthly volume" in body      # the answer itself survived
    # The old path (trim only) lost it:
    assert "depends on your monthly volume" not in kb_budget._trim_kb_body(rendered, 350)


def test_fit_leaves_short_nodes_untouched():
    node = {"content": "We are open 9 to 5.", "voice_answer": "Open nine to five."}
    rendered = render_node_answer(node)
    assert fit_kb_body(rendered, node, 600) == rendered.replace("\n", " ")


def test_fit_without_a_voice_answer_degrades_to_the_plain_trim():
    node = {"content": "x " * 400}
    assert fit_kb_body(render_node_answer(node), node, 100) == kb_budget._trim_kb_body("x " * 400, 100)


def test_budget_defaults_fit_the_live_rates_node():
    # 600 chars of a 1,501-char node reaches the script; 350 did not.
    assert kb_budget._KB_CHUNK_CHARS >= 600
    assert kb_budget._KB_TOTAL_CHARS >= 3 * 600 + 200 or kb_budget._KB_TOTAL_CHARS >= 2000


# ---------------------------------------------------------------------------
# F03 — a stall after tokens is an incomplete stream, never a normal end
# ---------------------------------------------------------------------------

import asyncio  # noqa: E402

from app.infrastructure.llm.groq import GroqLLMProvider, LLMStreamStalled, LLMTimeoutError  # noqa: E402


def _stalling_stream(first_delay: float, stall: float):
    async def _gen(messages, **kwargs):
        await asyncio.sleep(first_delay)
        yield "Your appointment is"
        await asyncio.sleep(stall)
        yield " on Tuesday."
    return _gen


@pytest.mark.asyncio
async def test_groq_stall_after_tokens_raises_incomplete_not_eof():
    provider = GroqLLMProvider()
    provider.stream_chat = _stalling_stream(0.0, 0.3)
    got = []
    with pytest.raises(LLMStreamStalled):
        async for tok in provider.stream_chat_with_timeout(
            [Message(role=MessageRole.USER, content="when?")], timeout_seconds=0.1
        ):
            got.append(tok)
    assert got == ["Your appointment is"]      # what was yielded stays yielded
    assert issubclass(LLMStreamStalled, LLMTimeoutError)  # turn streamer's handler catches it


@pytest.mark.asyncio
async def test_cerebras_stall_after_tokens_raises_incomplete_not_eof():
    provider = CerebrasLLMProvider()
    provider.stream_chat = _stalling_stream(0.0, 0.3)
    got = []
    with pytest.raises(LLMStreamStalled):
        async for tok in provider.stream_chat_with_timeout(
            [Message(role=MessageRole.USER, content="when?")], timeout_seconds=0.1
        ):
            got.append(tok)
    assert got == ["Your appointment is"]


@pytest.mark.asyncio
async def test_a_stream_that_finishes_inside_the_budget_is_untouched():
    provider = GroqLLMProvider()
    provider.stream_chat = _stalling_stream(0.0, 0.0)
    got = [t async for t in provider.stream_chat_with_timeout(
        [Message(role=MessageRole.USER, content="when?")], timeout_seconds=1.0
    )]
    assert got == ["Your appointment is", " on Tuesday."]


# ---------------------------------------------------------------------------
# F04 / F05 — one retry owner, deterministic client close
# ---------------------------------------------------------------------------

def test_groq_sdk_retries_are_off_so_the_provider_owns_retries(monkeypatch):
    from app.infrastructure.llm import groq as groq_module

    seen = {}

    class _Recorder:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr(groq_module, "AsyncGroq", _Recorder)
    provider = GroqLLMProvider()
    provider._http_timeout = 7.0
    provider._client_for("key-1")
    assert seen["max_retries"] == 0
    assert seen["api_key"] == "key-1"


@pytest.mark.asyncio
async def test_groq_cleanup_closes_every_client_once():
    from unittest.mock import AsyncMock

    provider = GroqLLMProvider()
    a, b = AsyncMock(), AsyncMock()
    provider._clients_by_key = {"k1": a, "k2": b}
    provider._client = a
    await provider.cleanup()
    a.close.assert_awaited_once()
    b.close.assert_awaited_once()
    assert provider._client is None and provider._clients_by_key == {}


@pytest.mark.asyncio
async def test_cerebras_cleanup_closes_the_client():
    from unittest.mock import AsyncMock

    provider = CerebrasLLMProvider()
    client = AsyncMock()
    provider._client = client
    await provider.cleanup()
    client.close.assert_awaited_once()
    assert provider._client is None


# ---------------------------------------------------------------------------
# F09 — the saved STT language is carried, and non-English forces Nova-3
# ---------------------------------------------------------------------------

def test_session_config_default_language_is_english_on_flux():
    from app.domain.services.voice_orchestrator import VoiceSessionConfig

    cfg = VoiceSessionConfig()
    assert cfg.stt_language == "en" and cfg.stt_model == "flux-general-en"


def test_call_session_carries_stt_language():
    from app.domain.models.session import CallSession

    assert "stt_language" in CallSession.model_fields


def test_audio_ingest_passes_the_session_language_to_the_stt_stream():
    from app.domain.services.voice_pipeline import audio_ingest

    src = inspect.getsource(audio_ingest)
    assert 'language=getattr(session, "stt_language", None) or "en"' in src


def test_builder_forces_nova_for_non_english_and_carries_the_language():
    from app.domain.services import telephony_session_config as tsc

    src = inspect.getsource(tsc.build_telephony_session_config)
    assert "stt_language=_stt_language" in src
    assert "stt_language_forces_nova" in src


# ---------------------------------------------------------------------------
# F07 — cross-vendor TTS fallback keeps the primary's PCM format
# ---------------------------------------------------------------------------

from app.domain.services.resilient_tts import ResilientTTSProvider, TTSFailoverPolicy  # noqa: E402


class _TTSStub:
    def __init__(self, name, chunks=None, fail=False):
        self.name = name
        self._chunks = chunks or []
        self._fail = fail
        self.seen_voice = None

    async def initialize(self, config):
        pass

    async def cleanup(self):
        pass

    async def get_available_voices(self):
        return []

    async def stream_synthesize(self, text, voice_id, sample_rate=16000, **kwargs):
        self.seen_voice = voice_id
        if self._fail:
            raise RuntimeError("primary down")
        for c in self._chunks:
            yield c


def test_pcm_format_classification():
    assert ResilientTTSProvider._pcm_format("cartesia") == "f32le"
    assert ResilientTTSProvider._pcm_format("google") == "f32le"
    assert ResilientTTSProvider._pcm_format("deepgram") == "s16le"
    assert ResilientTTSProvider._pcm_format("elevenlabs") == "s16le"
    assert ResilientTTSProvider._pcm_format("resilient(cartesia)") == "f32le"


@pytest.mark.asyncio
async def test_secondary_float32_is_converted_to_the_primary_int16_contract():
    import numpy as np
    from app.domain.models.conversation import AudioChunk

    floats = np.array([0.0, 0.5, -0.5, 1.0], dtype=np.float32).tobytes()
    primary = _TTSStub("deepgram", fail=True)
    secondary = _TTSStub("cartesia", chunks=[AudioChunk(data=floats, sample_rate=16000, channels=1)])
    wrapper = ResilientTTSProvider(primary, secondary, policy=TTSFailoverPolicy(voice_id_map={"aura-2-thalia-en": "cartesia-voice"}))
    out = [c async for c in wrapper.stream_synthesize("hi", "aura-2-thalia-en", 16000)]
    assert len(out) == 1
    ints = np.frombuffer(out[0].data, dtype=np.int16)
    assert list(ints) == [0, 16383, -16383, 32767]      # 4 samples in, 4 samples out
    assert secondary.seen_voice == "cartesia-voice"      # mapped, not the Deepgram id


@pytest.mark.asyncio
async def test_same_format_vendors_pass_bytes_through_unchanged():
    from app.domain.models.conversation import AudioChunk

    raw = b"\x01\x00\x02\x00"
    primary = _TTSStub("deepgram", fail=True)
    secondary = _TTSStub("elevenlabs", chunks=[AudioChunk(data=raw, sample_rate=16000, channels=1)])
    wrapper = ResilientTTSProvider(primary, secondary)
    out = [c async for c in wrapper.stream_synthesize("hi", "v", 16000)]
    assert out[0].data == raw
