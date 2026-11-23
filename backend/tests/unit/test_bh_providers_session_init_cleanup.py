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


# ---------------------------------------------------------------------------
# Cancelled / failed set-up must close what was opened.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cancelled_setup_cleans_finished_providers_and_stays_cancelled():
    orch, made = _orch()
    release = asyncio.Event()

    async def _blocked_tts(config):
        await release.wait()
        return made["tts"]

    orch._create_tts_provider = _blocked_tts
    task = asyncio.ensure_future(orch._create_session_providers(VoiceSessionConfig()))
    for _ in range(5):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()
    await _background_cleanups_done()   # the closing runs detached
    for key in ("stt", "llm", "gw"):
        made[key].cleanup.assert_awaited_once()
    made["tts"].cleanup.assert_not_called()


@pytest.mark.asyncio
async def test_cleanup_failure_on_cancel_does_not_replace_the_cancellation():
    orch, made = _orch()
    made["stt"].cleanup.side_effect = RuntimeError("cleanup boom")
    release = asyncio.Event()

    async def _blocked_tts(config):
        await release.wait()

    orch._create_tts_provider = _blocked_tts
    task = asyncio.ensure_future(orch._create_session_providers(VoiceSessionConfig()))
    for _ in range(5):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await _background_cleanups_done()
    made["stt"].cleanup.assert_awaited_once()   # it raised ...
    made["llm"].cleanup.assert_awaited_once()   # ... and the others were still closed
    made["gw"].cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_successful_setup_returns_providers_in_order_without_cleanup():
    orch, made = _orch()
    result = await orch._create_session_providers(VoiceSessionConfig())
    assert result == (made["stt"], made["llm"], made["tts"], made["gw"])
    for p in made.values():
        p.cleanup.assert_not_called()


def _patch_tts(monkeypatch, fake_cls):
    import app.infrastructure.tts.cartesia as cartesia
    import app.domain.services.credential_resolver as cr

    class _Resolver:
        async def resolve_with_metadata(self, *a, **k):
            return None

    monkeypatch.setattr(cartesia, "CartesiaTTSProvider", fake_cls)
    monkeypatch.setattr(cr, "get_credential_resolver", lambda: _Resolver())
    monkeypatch.delenv("TTS_FAILOVER_ENABLED", raising=False)


@pytest.mark.asyncio
async def test_tts_initialize_cancelled_after_session_created_is_cleaned_up(monkeypatch):
    created = []

    class _Fake:
        def __init__(self):
            self.session_open = False
            self.cleanup = AsyncMock(side_effect=self._close)
            created.append(self)

        async def _close(self):
            self.session_open = False

        async def initialize(self, cfg):
            self.session_open = True
            await asyncio.Event().wait()  # still connecting

    _patch_tts(monkeypatch, _Fake)
    orch = VoiceOrchestrator()
    task = asyncio.ensure_future(orch._create_tts_provider(VoiceSessionConfig(tts_provider_type="cartesia")))
    for _ in range(5):
        await asyncio.sleep(0)
    assert created and created[0].session_open
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await _background_cleanups_done()
    created[0].cleanup.assert_awaited_once()
    assert created[0].session_open is False


@pytest.mark.asyncio
async def test_tts_initialize_error_survives_a_failing_cleanup(monkeypatch):
    created = []

    class _Fake:
        def __init__(self):
            self.cleanup = AsyncMock(side_effect=RuntimeError("cleanup boom"))
            created.append(self)

        async def initialize(self, cfg):
            raise ValueError("init failed")

    _patch_tts(monkeypatch, _Fake)
    orch = VoiceOrchestrator()
    with pytest.raises(ValueError, match="init failed"):
        await orch._create_tts_provider(VoiceSessionConfig(tts_provider_type="cartesia"))
    created[0].cleanup.assert_awaited_once()   # without this the test passed with the fix removed


@pytest.mark.asyncio
async def test_tts_initialize_success_calls_no_cleanup(monkeypatch):
    created = []

    class _Fake:
        name = "cartesia"

        def __init__(self):
            self.cleanup = AsyncMock()
            created.append(self)

        async def initialize(self, cfg):
            pass

    _patch_tts(monkeypatch, _Fake)
    orch = VoiceOrchestrator()
    got = await orch._create_tts_provider(VoiceSessionConfig(tts_provider_type="cartesia"))
    assert got is created[0]
    created[0].cleanup.assert_not_called()


# ---------------------------------------------------------------------------
# 23 Nov 2026 review: the closing after a CANCELLED set-up runs detached, so the
# cancellation is not delayed and a second cancel cannot abandon it; the failover
# (secondary) provider is closed too.
# ---------------------------------------------------------------------------
async def _background_cleanups_done():
    import app.domain.services.voice_orchestrator as vo

    pending = list(vo._BACKGROUND_CLEANUPS)
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    # gather() of tasks that had already finished returns without a loop turn
    # (Python 3.12), so give each task's own done-callback its turn first.
    await asyncio.sleep(0)
    assert not vo._BACKGROUND_CLEANUPS  # the strong references are released


@pytest.mark.asyncio
async def test_cancellation_does_not_wait_for_a_slow_provider_close():
    import time

    orch, made = _orch()

    async def _slow_close():
        await asyncio.sleep(0.3)

    made["stt"].cleanup.side_effect = _slow_close
    release = asyncio.Event()

    async def _blocked_tts(config):
        await release.wait()

    orch._create_tts_provider = _blocked_tts
    task = asyncio.ensure_future(orch._create_session_providers(VoiceSessionConfig()))
    for _ in range(5):
        await asyncio.sleep(0)
    started = time.monotonic()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert time.monotonic() - started < 0.15, "the cancel waited behind a provider close"
    await _background_cleanups_done()
    for key in ("stt", "llm", "gw"):
        made[key].cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_second_cancel_cannot_abandon_the_closing():
    orch, made = _orch()
    gate = asyncio.Event()

    async def _held_close():
        await gate.wait()

    made["stt"].cleanup.side_effect = _held_close
    release = asyncio.Event()

    async def _blocked_tts(config):
        await release.wait()

    orch._create_tts_provider = _blocked_tts
    task = asyncio.ensure_future(orch._create_session_providers(VoiceSessionConfig()))
    for _ in range(5):
        await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()  # a second cancel while the first provider is still closing
    with pytest.raises(asyncio.CancelledError):
        # Bounded: if the closing ever runs inline again, the cancel would wait
        # behind the held close and this must FAIL here, not hang the suite.
        await asyncio.wait_for(task, 1.0)
    gate.set()
    await _background_cleanups_done()
    for key in ("stt", "llm", "gw"):
        made[key].cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_all_four_finished_when_the_cancel_lands_are_all_closed(monkeypatch):
    import app.domain.services.voice_orchestrator as vo

    orch, made = _orch()
    real_gather = asyncio.gather

    async def _gather_then_cancelled(*aws, **kw):
        await real_gather(*aws, **kw)       # every child has finished ...
        raise asyncio.CancelledError()      # ... and only now the cancel lands

    monkeypatch.setattr(vo.asyncio, "gather", _gather_then_cancelled)
    with pytest.raises(asyncio.CancelledError):
        await orch._create_session_providers(VoiceSessionConfig())
    monkeypatch.setattr(vo.asyncio, "gather", real_gather)
    await _background_cleanups_done()
    for key in ("stt", "llm", "tts", "gw"):
        made[key].cleanup.assert_awaited_once()


def _patch_tts_with_failover(monkeypatch, primary_cls, secondary_cls):
    import app.infrastructure.tts.cartesia as cartesia
    import app.infrastructure.tts.deepgram_tts as dg
    import app.domain.services.credential_resolver as cr

    class _Resolver:
        async def resolve_with_metadata(self, *a, **k):
            return None

    monkeypatch.setattr(cartesia, "CartesiaTTSProvider", primary_cls)
    monkeypatch.setattr(dg, "DeepgramTTSProvider", secondary_cls)
    monkeypatch.setattr(cr, "get_credential_resolver", lambda: _Resolver())
    monkeypatch.setenv("TTS_FAILOVER_ENABLED", "true")
    monkeypatch.setenv("TTS_SECONDARY_PROVIDER", "deepgram")
    monkeypatch.setenv("TTS_SECONDARY_VOICE_MAP", "v1=aura-x")


@pytest.mark.asyncio
async def test_cancel_while_the_failover_provider_connects_closes_both(monkeypatch):
    made = {}

    class _Primary:
        name = "cartesia"

        def __init__(self):
            self.cleanup = AsyncMock()
            made["primary"] = self

        async def initialize(self, cfg):
            pass

    class _Secondary:
        name = "deepgram"

        def __init__(self):
            self.session_open = False
            self.cleanup = AsyncMock()
            made["secondary"] = self

        async def initialize(self, cfg):
            self.session_open = True          # its HTTP session exists ...
            await asyncio.Event().wait()      # ... while its socket is still connecting

    _patch_tts_with_failover(monkeypatch, _Primary, _Secondary)
    orch = VoiceOrchestrator()
    task = asyncio.ensure_future(
        orch._create_tts_provider(VoiceSessionConfig(tts_provider_type="cartesia", voice_id="v1"))
    )
    for _ in range(10):
        await asyncio.sleep(0)
    assert made.get("secondary") is not None and made["secondary"].session_open
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await _background_cleanups_done()
    made["primary"].cleanup.assert_awaited_once()
    made["secondary"].cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_failing_failover_provider_is_closed_and_the_primary_is_kept(monkeypatch):
    made = {}

    class _Primary:
        name = "cartesia"

        def __init__(self):
            self.cleanup = AsyncMock()
            made["primary"] = self

        async def initialize(self, cfg):
            pass

    class _Secondary:
        name = "deepgram"

        def __init__(self):
            self.cleanup = AsyncMock()
            made["secondary"] = self

        async def initialize(self, cfg):
            raise RuntimeError("secondary cannot connect")

    _patch_tts_with_failover(monkeypatch, _Primary, _Secondary)
    orch = VoiceOrchestrator()
    got = await orch._create_tts_provider(VoiceSessionConfig(tts_provider_type="cartesia", voice_id="v1"))
    assert got is made["primary"]                 # as before: the call goes on with the primary alone
    made["primary"].cleanup.assert_not_called()
    made["secondary"].cleanup.assert_awaited_once()  # what the failed one opened is closed
