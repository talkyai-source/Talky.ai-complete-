"""The ringing warm-up opens the provider connections it prepares.

Production, 2026-09-29 19:42:48 (talky-api): "Ringing warmup failed for
talky-out-94...: a coroutine was expected, got <_GatheringFuture pending>",
raised at lifecycle._on_ringing by asyncio.create_task(asyncio.gather(...)).
create_task only takes a coroutine, so any warm-up with an STT or TTS
connection to open failed before it was published: the connections were
never opened (their coroutines were never awaited) and the voice session it
had just created was never ended. The existing warm-up tests used providers
without connect methods, so this line was never reached.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from app.domain.services.telephony import lifecycle
from tests.unit.test_selected_warmup_lifecycle import answered, offline, unowned_ringing, warmup  # noqa: F401


def _session_with_connections():
    connected = []

    async def tts_connect(call_id):
        connected.append(("tts", call_id))

    async def stt_connect(call_id):
        connected.append(("stt", call_id))

    async def silent_synthesis(**_kwargs):
        if False:  # pragma: no cover - an empty async generator
            yield b""

    session = NS(call_id="voice-call", config=None,
                 tts_provider=NS(connect_for_call=tts_connect, stream_synthesize=silent_synthesis),
                 stt_provider=NS(pre_connect=stt_connect), llm_provider=NS(),
                 call_session=NS(call_id="voice-call"))
    return session, connected


async def test_a_ringing_warmup_with_connections_is_published_and_connects(answered, monkeypatch):  # noqa: F811
    unowned_ringing(answered)
    created, connected = _session_with_connections()
    answered.orchestrator.create_voice_session.side_effect = None
    answered.orchestrator.create_voice_session.return_value = created
    monkeypatch.setattr(lifecycle, "_build_outbound_greeting", lambda _session: "Hello?")
    await lifecycle._on_ringing("call")
    cached = answered.state.get_ringing_warmup("call")
    assert cached is not None and cached[0] is created, "the warm-up must be published"
    await asyncio.wait_for(cached[1], 2)
    assert sorted(connected) == [("stt", "voice-call"), ("tts", "voice-call")]
    answered.end.assert_not_awaited()


async def test_a_failed_warmup_ends_the_session_it_created(answered, monkeypatch):  # noqa: F811
    unowned_ringing(answered)
    created, _ = _session_with_connections()
    created.tts_provider = NS(connect_for_call=AsyncMock(side_effect=None))
    answered.orchestrator.create_voice_session.side_effect = None
    answered.orchestrator.create_voice_session.return_value = created

    def broken_publish(*_args, **_kwargs):
        raise RuntimeError("synthetic publish failure")

    monkeypatch.setattr(answered.state, "set_ringing_warmup", broken_publish)
    await lifecycle._on_ringing("call")
    answered.end.assert_awaited_once_with(created)
    assert answered.state.get_ringing_event("call").is_set()
