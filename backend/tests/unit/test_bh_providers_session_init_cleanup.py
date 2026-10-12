"""create_voice_session initialises STT/LLM/TTS/gateway concurrently. When one
fails, the ones that DID initialise must be cleaned up (sockets / client
sessions) and the original exception type must still propagate."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.services.voice_eligibility import VoiceEligibilityError
from app.domain.services.voice_orchestrator import VoiceOrchestrator, VoiceSessionConfig


def _provider():
    return MagicMock(cleanup=AsyncMock())


def _orch(*, stt=None, llm=None, tts=None, gw=None):
    orch = VoiceOrchestrator()
    made = {"stt": _provider(), "llm": _provider(), "tts": _provider(), "gw": _provider()}

    def _factory(key, outcome):
        async def _create(config):
            await asyncio.sleep(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return made[key]
        return _create

    orch._create_stt_provider = _factory("stt", stt)
    orch._create_llm_provider = _factory("llm", llm)
    orch._create_tts_provider = _factory("tts", tts)
    orch._create_media_gateway = _factory("gw", gw)
    return orch, made


@pytest.mark.asyncio
async def test_tts_eligibility_failure_cleans_up_the_other_providers():
    err = VoiceEligibilityError("voice_not_available", "Voice is not available for this tenant", 403)
    orch, made = _orch(tts=err)
    with pytest.raises(VoiceEligibilityError) as exc:
        await orch.create_voice_session(VoiceSessionConfig())
    assert exc.value is err
    for key in ("stt", "llm", "gw"):
        made[key].cleanup.assert_awaited_once()
    made["tts"].cleanup.assert_not_called()


@pytest.mark.asyncio
async def test_stt_failure_cleans_up_a_tts_that_opened_a_socket():
    orch, made = _orch(stt=RuntimeError("stt init failed"))
    with pytest.raises(RuntimeError, match="stt init failed"):
        await orch.create_voice_session(VoiceSessionConfig())
    made["tts"].cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_failure_does_not_mask_the_original_error():
    orch, made = _orch(llm=RuntimeError("llm init failed"))
    made["tts"].cleanup.side_effect = RuntimeError("cleanup boom")
    with pytest.raises(RuntimeError, match="llm init failed"):
        await orch.create_voice_session(VoiceSessionConfig())
    made["stt"].cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancellation_still_propagates_and_cleans_up():
    orch, made = _orch(tts=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await orch.create_voice_session(VoiceSessionConfig())
    made["stt"].cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_ask_ai_singletons_are_never_cleaned_up_when_only_tts_fails():
    orch = VoiceOrchestrator()
    shared = (_provider(), _provider(), None, _provider())
    orch._ask_ai_providers = shared
    orch._create_tts_provider = AsyncMock(side_effect=RuntimeError("tts failed"))
    with pytest.raises(RuntimeError, match="tts failed"):
        await orch.create_voice_session(VoiceSessionConfig(session_type="ask_ai"))
    for p in (shared[0], shared[1], shared[3]):
        p.cleanup.assert_not_called()
