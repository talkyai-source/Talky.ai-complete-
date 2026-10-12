"""ResilientTTSProvider must keep the primary's per-call hooks reachable:
connect_for_call (pre-warm), disconnect_for_call (orphan sweep / call end) and
clear_queue (barge-in), while getattr(..., None) stays None when the primary
lacks a hook."""
from __future__ import annotations

from typing import AsyncIterator

import pytest

from app.domain.interfaces.tts_provider import TTSProvider
from app.domain.models.conversation import AudioChunk
from app.domain.services.resilient_tts import ResilientTTSProvider


class _TTS(TTSProvider):
    def __init__(self, name: str):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    async def initialize(self, config: dict) -> None:
        pass

    async def cleanup(self) -> None:
        pass

    async def get_available_voices(self) -> list[dict]:
        return []

    async def stream_synthesize(self, text, voice_id, sample_rate=16000, **kw) -> AsyncIterator[AudioChunk]:
        yield AudioChunk(data=b"\0" * 10, sample_rate=sample_rate)


class _HookedTTS(_TTS):
    def __init__(self, name: str):
        super().__init__(name)
        self.events: list[tuple] = []

    async def connect_for_call(self, call_id: str) -> None:
        self.events.append(("connect", call_id))

    async def disconnect_for_call(self, call_id: str) -> None:
        self.events.append(("disconnect", call_id))

    async def clear_queue(self) -> None:
        self.events.append(("clear",))


@pytest.mark.asyncio
async def test_hooks_forward_to_primary_only():
    primary, secondary = _HookedTTS("cartesia"), _HookedTTS("elevenlabs")
    wrapper = ResilientTTSProvider(primary=primary, secondary=secondary)

    await getattr(wrapper, "connect_for_call", None)("call-1")
    await getattr(wrapper, "clear_queue", None)()
    await getattr(wrapper, "disconnect_for_call", None)("call-1")

    assert primary.events == [("connect", "call-1"), ("clear",), ("disconnect", "call-1")]
    assert secondary.events == [], "must not open two vendors"


def test_missing_primary_hooks_still_read_as_absent():
    wrapper = ResilientTTSProvider(primary=_TTS("deepgram"), secondary=_TTS("cartesia"))
    for hook in ("connect_for_call", "disconnect_for_call", "clear_queue"):
        assert getattr(wrapper, hook, None) is None


def test_unrelated_missing_attributes_still_raise():
    wrapper = ResilientTTSProvider(primary=_HookedTTS("cartesia"))
    with pytest.raises(AttributeError):
        wrapper.not_a_hook  # noqa: B018
