"""Real adapters with synthetic wire ports; no provider requests or content storage."""
import asyncio
import json
import socket
from contextlib import asynccontextmanager
from types import SimpleNamespace as NS
from uuid import uuid4

import aiohttp
import pytest

from app.infrastructure.metrics import provider_observations as observations
from app.infrastructure.stt import assemblyai, deepgram, deepgram_nova, deepgram_flux
from app.infrastructure.tts import cartesia, elevenlabs_tts, deepgram_tts
from tests.unit.test_assemblyai_adapter import setup_provider, audio, turn
from tests.unit.test_flux_confidence_not_turn_boundary import _drive_flux
from tests.unit.test_nova_barge_in_parity import _FakeConn, _FakeClient, _Results
from tests.unit.test_bh_providers_elevenlabs_conn_retry import _Session, _Resp, _Chunks, _provider
from tests.unit.test_cartesia_bargein_context_isolation import _FakeWS, _FakeWSMessage, _audio_frame, _done_frame
from tests.unit.test_bh_providers_deepgram_prewarm_race import _WS


@pytest.fixture(autouse=True)
def isolated_observations(monkeypatch):
    rows = []
    monkeypatch.setattr(observations, "_signing_key", lambda: b"synthetic-observation-signing")
    monkeypatch.setattr(observations, "publish", lambda item: rows.append(item))
    real_connect = socket.socket.connect
    def no_network(sock, address):
        # Windows asyncio implements its internal self-pipe with loopback sockets.
        if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1"):
            return real_connect(sock, address)
        raise AssertionError("External sockets forbidden by this synthetic adapter test")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(elevenlabs_tts, "_EL_RETRY_BASE_DELAY", 0)
    return rows


@pytest.mark.parametrize("kind", ["final", "empty", "partial"])
async def test_assembly_actual_final_only_and_bounded_records(monkeypatch, isolated_observations, kind):
    p, ws, _ = await setup_provider(monkeypatch)
    ws.on_terminate = [turn(i, "private spoken content" if kind != "empty" else "", kind != "partial") for i in range(5)]
    await asyncio.wait_for(collect(p.stream_transcribe(audio(b"\x00" * 1600))), 2)
    rows = isolated_observations
    assert len(rows) == (2 if kind == "final" else 1)
    assert {row.outcome for row in rows} == ({"recognized"} if kind == "final" else {"connected"})
    assert all(row.first_output_ms is None for row in rows)
    assert all("private spoken content" not in row.to_json() and "private-test-key" not in row.to_json() for row in rows)
    await p.cleanup()


async def test_assembly_recognition_does_not_hide_later_cancel(monkeypatch, isolated_observations):
    p, ws, _ = await setup_provider(monkeypatch)
    async def active_audio():
        await asyncio.Event().wait()
        yield
    ws.incoming.put_nowait(turn(1, "private spoken content", True))
    stream = p.stream_transcribe(active_audio())
    await asyncio.wait_for(anext(stream), 2)
    await stream.aclose()
    assert [row.outcome for row in isolated_observations] == ["recognized", "cancelled"]
    await p.cleanup()


async def test_assembly_concurrent_cleanup_cannot_publish_a_successful_session(monkeypatch, isolated_observations):
    p, ws, _ = await setup_provider(monkeypatch)
    async def active_audio():
        await asyncio.Event().wait()
        yield
    ws.incoming.put_nowait(turn(1, "private spoken content", True))
    stream = p.stream_transcribe(active_audio())
    await asyncio.wait_for(anext(stream), 2)
    await p.cleanup()
    await stream.aclose()
    assert [row.outcome for row in isolated_observations] == ["recognized", "cancelled"]


async def test_assembly_recognition_does_not_hide_transport_failure(monkeypatch, isolated_observations):
    p, ws, _ = await setup_provider(monkeypatch)
    async def active_audio():
        await asyncio.Event().wait()
        yield
    ws.incoming.put_nowait(turn(1, "private spoken content", True))
    ws.incoming.put_nowait(ConnectionError("private upstream error"))
    with pytest.raises(RuntimeError):
        await collect(p.stream_transcribe(active_audio()))
    assert [row.outcome for row in isolated_observations] == ["recognized", "failed"]
    assert isolated_observations[-1].error_category == "transport"
    assert "private upstream error" not in isolated_observations[-1].to_json()
    await p.cleanup()


async def test_assembly_each_retry_uses_actual_key_and_drops_unrelated_tenant_identity(monkeypatch, isolated_observations):
    p, ws, _ = await setup_provider(monkeypatch, observation_context={
        "source": "tenant", "tenant_id": str(uuid4()), "credential_id": str(uuid4())})
    seen = []
    class Refused(Exception):
        response = NS(status_code=401)
    async def connect(url, **kwargs):
        seen.append(kwargs["additional_headers"]["Authorization"])
        if len(seen) == 1:
            raise Refused()
        return ws
    monkeypatch.setattr(assemblyai.websockets, "connect", connect)
    monkeypatch.setattr(assemblyai, "platform_key_rotation", lambda _: NS(bench=lambda *a: True, retry_key=lambda **kw: "rotated-synthetic-key"))
    await p.pre_connect("call")
    await p.cleanup()
    assert seen == ["private-test-key", "rotated-synthetic-key"]
    assert [row.outcome for row in isolated_observations] == ["failed", "connected"]
    assert [row.credential_source for row in isolated_observations] == ["tenant", "unattributed"]
    assert isolated_observations[0].error_category == "authentication"
    assert len({row.credential_revision for row in isolated_observations}) == 2


@pytest.mark.parametrize("text", ["recognized words", ""])
async def test_flux_final_text_not_empty_end_marker_certifies_recognition(isolated_observations, text):
    await _drive_flux([{"type": "TurnInfo", "event": "EndOfTurn", "transcript": text}])
    assert [row.outcome for row in isolated_observations] == (["recognized", "recognized"] if text else ["connected"])
    assert all(row.provider == "flux" and row.first_output_ms is None for row in isolated_observations)


async def test_flux_failed_handshake_does_not_become_connected(monkeypatch, isolated_observations):
    p = deepgram_flux.DeepgramFluxSTTProvider()
    p._api_key = "synthetic-flux-key"
    async def failed_connect(*args, **kwargs):
        raise TimeoutError()
    monkeypatch.setattr(deepgram_flux.websockets, "connect", failed_connect)
    with pytest.raises(TimeoutError):
        await p.pre_connect("call")
    assert [row.outcome for row in isolated_observations] == ["failed"]
    assert isolated_observations[0].error_category == "timeout"
    await p.cleanup()


@pytest.mark.parametrize("final", [True, False])
async def test_nova_interim_promoted_at_teardown_is_not_recognition(isolated_observations, final):
    p = deepgram_nova.DeepgramNovaSTTProvider()
    p._api_key = "synthetic-nova-key"
    p._client = _FakeClient(_FakeConn([_Results("spoken private words", is_final=final)]))
    await collect(p.stream_transcribe(audio()))
    assert [row.outcome for row in isolated_observations] == (["recognized", "recognized"] if final else ["connected"])
    assert all(row.provider == "deepgram_nova" and row.first_output_ms is None for row in isolated_observations)


@pytest.mark.parametrize("event,text", [("EndOfTurn", "spoken content"), ("Update", "spoken content"), ("EndOfTurn", "  \t")])
async def test_standard_deepgram_observes_actual_protocol_final(isolated_observations, event, text):
    p = deepgram.DeepgramSTT()
    p._primary_key = "synthetic-standard-key"
    conn = _FakeConn([NS(transcript=text, event=event)])
    conn._send = conn.send_media
    @asynccontextmanager
    async def connect(**kwargs):
        yield conn
    p._client = NS(listen=NS(v2=NS(connect=connect)))
    await collect(p.stream_transcribe(audio()))
    assert [row.outcome for row in isolated_observations] == (["recognized", "recognized"] if event == "EndOfTurn" and text.strip() else ["connected"])


async def test_standard_sdk_error_cannot_be_overwritten_by_late_result(isolated_observations):
    from deepgram.core.events import EventType
    p = deepgram.DeepgramSTT()
    p._primary_key = "synthetic-standard-key"
    conn = _FakeConn([])
    conn._send = conn.send_media
    async def listen():
        conn._handlers[EventType.ERROR](ConnectionError("synthetic"))
        conn._handlers[EventType.MESSAGE](NS(transcript="late words", event="EndOfTurn"))
    conn.start_listening = listen
    @asynccontextmanager
    async def connect(**kwargs):
        yield conn
    p._client = NS(listen=NS(v2=NS(connect=connect)))
    await collect(p.stream_transcribe(audio()))
    assert [row.outcome for row in isolated_observations] == ["failed"]


async def test_elevenlabs_retry_keeps_primary_failure_and_actual_key(isolated_observations):
    session = _Session([aiohttp.ServerDisconnectedError("synthetic"), _Resp(_Chunks([b"audio"]))])
    p = _provider(session)
    keys = iter(["first-synthetic-key", "next-synthetic-key"])
    @asynccontextmanager
    async def lease():
        yield NS(key=next(keys), report_failure=lambda **kw: None, report_success=lambda: None)
    p._pool = NS(acquire=lease)
    await collect(p.stream_synthesize("private prompt", "voice"))
    assert [row.outcome for row in isolated_observations] == ["failed", "completed"]
    assert len({row.credential_revision for row in isolated_observations}) == 2
    assert all("private prompt" not in row.to_json() for row in isolated_observations)


@pytest.mark.parametrize("kind", ["empty", "partial_failure", "cancelled"])
async def test_elevenlabs_empty_partial_failure_cancel_are_not_completed(isolated_observations, kind):
    error = TimeoutError() if kind == "partial_failure" else None
    p = _provider(_Session([_Resp(_Chunks([] if kind == "empty" else [b"audio"], raise_exc=error))]))
    stream = p.stream_synthesize("private", "voice")
    if kind == "cancelled":
        await anext(stream)
        await stream.aclose()
    elif kind == "partial_failure":
        with pytest.raises(elevenlabs_tts.ElevenLabsPartialAudioError):
            await collect(stream)
    else:
        await collect(stream)
    assert [row.outcome for row in isolated_observations] == [{"empty": "incomplete", "partial_failure": "failed", "cancelled": "cancelled"}[kind]]


@pytest.mark.parametrize("kind", ["done", "empty", "stale", "cancel", "eof"])
async def test_cartesia_actual_context_done_audio_and_pinned_key(isolated_observations, kind):
    p = cartesia.CartesiaTTSProvider()
    p._api_key = "default-synthetic-key"
    frames = [] if kind == "empty" else [_FakeWSMessage(aiohttp.WSMsgType.TEXT, _audio_frame("other" if kind == "stale" else "ours", 4))]
    if kind != "eof":
        frames.append(_FakeWSMessage(aiohttp.WSMsgType.TEXT, _done_frame("ours")))
    stream = p._stream_over_ws(_FakeWS(frames), {"context_id": "ours"}, 24000,
        observation=p._observation("pinned-synthetic-key"))
    if kind == "cancel":
        await anext(stream)
        await stream.aclose()
    elif kind == "eof":
        with pytest.raises(RuntimeError):
            await collect(stream)
    else:
        await collect(stream)
    row, = isolated_observations
    assert row.outcome == {"done": "completed", "empty": "incomplete", "stale": "incomplete", "cancel": "cancelled", "eof": "failed"}[kind]
    assert row.credential_revision == observations.credential_revision("cartesia", "pinned-synthetic-key")


@pytest.mark.parametrize("kind", ["done", "empty", "cancel", "eof"])
async def test_deepgram_tts_requires_audio_and_flushed(isolated_observations, kind):
    p = deepgram_tts.DeepgramTTSProvider()
    p._api_key = "synthetic-deepgram-key"
    p._session = NS()
    p._synthesis_lock = asyncio.Lock()
    frames = [] if kind == "empty" else [NS(type=aiohttp.WSMsgType.BINARY, data=b"audio")]
    if kind != "eof":
        frames.append(NS(type=aiohttp.WSMsgType.TEXT, data=json.dumps({"type": "Flushed"})))
    p._warm_ws = _WS(frames)
    p._warm_ws_voice, p._warm_ws_rate = "voice", 24000
    async def no_prewarm(*args):
        pass
    p._prewarm_after_interrupt = no_prewarm
    stream = p.stream_synthesize("private", "voice", 24000)
    if kind == "cancel":
        await anext(stream)
        await stream.aclose()
    else:
        await collect(stream)
    if p._prewarm_task:
        await p._prewarm_task
    row, = isolated_observations
    assert row.outcome == {"done": "completed", "empty": "incomplete", "cancel": "cancelled", "eof": "incomplete"}[kind]


async def test_deepgram_handshake_authentication_survives_wrapping(monkeypatch, isolated_observations):
    p = deepgram_tts.DeepgramTTSProvider()
    p._api_key = "synthetic-deepgram-key"
    p._synthesis_lock = asyncio.Lock()
    async def failed_connect(*args, **kwargs):
        raise aiohttp.WSServerHandshakeError(NS(real_url="synthetic"), (), status=401)
    p._session = NS(ws_connect=failed_connect)
    async def no_prewarm(*args):
        pass
    p._prewarm_after_interrupt = no_prewarm
    with pytest.raises(RuntimeError):
        await collect(p.stream_synthesize("private", "voice"))
    await p._prewarm_task
    assert all(row.outcome == "failed" and row.error_category == "authentication" for row in isolated_observations)


async def test_missing_context_stays_unattributed_and_failed_telemetry_does_not_break_audio(monkeypatch, isolated_observations):
    p = _provider(_Session([_Resp(_Chunks([b"audio"]))]))
    await collect(p.stream_synthesize("private", "voice"))
    assert isolated_observations[0].credential_source == "unattributed"
    def unavailable(_):
        raise RuntimeError("synthetic telemetry failure")
    monkeypatch.setattr(observations, "publish", unavailable)
    p._session = _Session([_Resp(_Chunks([b"audio"]))])
    assert len(await collect(p.stream_synthesize("private", "voice"))) == 1


async def collect(stream):
    return [item async for item in stream]
