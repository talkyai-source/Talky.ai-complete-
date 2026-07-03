"""Regression tests for the OpenAI Realtime (gpt-realtime-2) audio pipeline.

These guard the audio FORMAT + SAMPLE RATE contract at every boundary of the
realtime path, which was never exercised by the Phase-1 text-in smoke test and
broke on the first live call:

  1. The session.update MUST request μ-law 8 kHz (audio/pcmu) for BOTH input
     and output — that is exactly what the RealtimeBridge encodes/decodes with
     pcm_to_ulaw / ulaw_to_pcm. If the session ever drifts to pcm16/24k while
     the bridge keeps treating bytes as μ-law 8k, the OUTPUT is the "heavy /
     ghost" garbage the user heard and the model can't hear the caller.

  2. The realtime media gateway MUST run at a single 8 kHz rate in BOTH
     directions. The bridge only knows one internal rate (gateway._sample_rate)
     and assumes the caller-audio queue is at that rate. Leaving input at the
     cascaded 16 kHz STT rate feeds the model a half-speed / garbled caller
     ("my voice is not flowing into the model").

  3. The caller pump MUST read from gateway.get_audio_queue(call_id) and hand
     μ-law to the session; the model pump MUST decode μ-law and hand PCM to
     gateway.send_audio.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.infrastructure.realtime.openai_realtime import OpenAIRealtimeSession
from app.domain.services.voice_pipeline.realtime_bridge import RealtimeBridge, _WIRE_RATE
from app.domain.services.voice_orchestrator import VoiceOrchestrator, VoiceSessionConfig
from app.utils.audio_utils import pcm_to_ulaw, ulaw_to_pcm


# ---------------------------------------------------------------------------
# 1. Session format contract: μ-law 8 kHz in AND out.
# ---------------------------------------------------------------------------

def test_session_update_requests_mulaw_both_directions():
    session = OpenAIRealtimeSession(api_key="sk-test", call_id="fmt-test")
    payload = session._build_session_update()

    s = payload["session"]
    assert payload["type"] == "session.update"
    assert s["type"] == "realtime"
    # Input AND output must both be audio/pcmu (G.711 μ-law @ 8 kHz) — the exact
    # format the bridge produces with pcm_to_ulaw and consumes with ulaw_to_pcm.
    assert s["audio"]["input"]["format"] == {"type": "audio/pcmu"}
    assert s["audio"]["output"]["format"] == {"type": "audio/pcmu"}
    # Guard against a silent regression back to the API default (pcm/24000) or
    # the rejected legacy string form ("g711_ulaw").
    assert s["audio"]["input"]["format"]["type"] == "audio/pcmu"
    assert "rate" not in s["audio"]["input"]["format"]


def test_session_format_matches_bridge_codec():
    """The bridge's codec round-trips a μ-law frame through PCM16 and back with
    no rate change — proving the session's audio/pcmu contract needs zero
    resampling on the bridge side (the whole point of choosing pcmu)."""
    session = OpenAIRealtimeSession(api_key="sk-test")
    fmt = session._build_session_update()["session"]["audio"]["output"]["format"]
    assert fmt == {"type": "audio/pcmu"}

    # A μ-law frame the model would emit: 160 bytes = 20 ms @ 8 kHz.
    mulaw_frame = bytes(range(160))
    pcm16 = ulaw_to_pcm(mulaw_frame)          # bridge model-pump decode
    assert len(pcm16) == len(mulaw_frame) * 2  # 8-bit μ-law -> 16-bit PCM, same 160 samples
    back = pcm_to_ulaw(pcm16)                  # bridge caller-pump encode
    # μ-law decode/encode is a bijection except the ±0 code (0x7F/0xFF) — the
    # round-trip must otherwise be exact so no distortion is injected.
    changed = sum(1 for a, b in zip(mulaw_frame, back) if a != b)
    assert changed <= 1


# ---------------------------------------------------------------------------
# 2. Realtime gateway rate: 8 kHz in BOTH directions.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_realtime_gateway_forces_8k_both_directions():
    """Realtime must init the gateway at 8 kHz for output AND input so the
    bridge's single-rate assumption holds. Regression guard for the caller-
    audio half-speed bug."""
    orch = VoiceOrchestrator(db_client=None)
    mock_gw = AsyncMock()
    mock_gw.initialize = AsyncMock()

    with patch(
        "app.infrastructure.telephony.factory.MediaGatewayFactory.create",
        return_value=mock_gw,
    ):
        await orch._create_media_gateway(
            VoiceSessionConfig(
                pipeline_mode="realtime",
                gateway_sample_rate=24000,
                gateway_input_sample_rate=16000,
                stt_sample_rate=16000,
            )
        )

    (init_config,), _ = mock_gw.initialize.await_args
    assert init_config["sample_rate"] == 8000
    assert init_config["input_sample_rate"] == 8000, (
        "realtime input rate must be forced to 8 kHz; leaving it at the STT "
        "rate feeds the model a half-speed caller"
    )
    # Realtime bridge always feeds linear16 — never Float32.
    assert init_config["tts_source_format"] == "s16le"


@pytest.mark.asyncio
async def test_cascaded_gateway_keeps_stt_input_rate():
    """The realtime override must NOT touch the cascaded path."""
    orch = VoiceOrchestrator(db_client=None)
    mock_gw = AsyncMock()
    mock_gw.initialize = AsyncMock()

    with patch(
        "app.infrastructure.telephony.factory.MediaGatewayFactory.create",
        return_value=mock_gw,
    ):
        await orch._create_media_gateway(
            VoiceSessionConfig(
                gateway_sample_rate=24000,
                gateway_input_sample_rate=16000,
                stt_sample_rate=16000,
                tts_provider_type="deepgram",
            )
        )

    (init_config,), _ = mock_gw.initialize.await_args
    assert init_config["sample_rate"] == 24000
    assert init_config["input_sample_rate"] == 16000


# ---------------------------------------------------------------------------
# 3. Caller pump reads gateway.get_audio_queue and encodes μ-law;
#    model pump decodes μ-law and writes PCM to gateway.send_audio.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_caller_pump_reads_queue_and_sends_mulaw():
    """The realtime caller pump must drain gateway.get_audio_queue(call_id) and
    forward μ-law to the session (symptom: caller never heard if it doesn't)."""
    queue: asyncio.Queue = asyncio.Queue()
    # One 20 ms PCM16 @ 8 kHz frame from the gateway (160 samples * 2 bytes).
    pcm16_frame = ulaw_to_pcm(bytes(160))  # 160 μ-law -> 320-byte PCM16
    await queue.put(pcm16_frame)

    gw = MagicMock()
    gw.get_audio_queue = MagicMock(return_value=queue)

    rt = AsyncMock()
    rt.closed = MagicMock(return_value=False)
    rt.send_caller_audio = AsyncMock()

    bridge = RealtimeBridge(
        call_id="call-1",
        realtime_session=rt,
        media_gateway=gw,
        internal_sample_rate=8000,
    )

    # Run the pump briefly, then stop it.
    task = asyncio.create_task(bridge._pump_caller_audio())
    await asyncio.sleep(0.05)
    bridge._stop.set()
    await queue.put(b"")  # unblock any pending get
    await asyncio.wait_for(task, timeout=1.0)

    gw.get_audio_queue.assert_called_once_with("call-1")
    assert rt.send_caller_audio.await_count >= 1
    sent = rt.send_caller_audio.await_args_list[0].args[0]
    # 320-byte PCM16 @ 8 kHz -> 160-byte μ-law (2:1), no resample at 8 kHz.
    assert len(sent) == 160


@pytest.mark.asyncio
async def test_caller_pump_aborts_when_no_queue():
    """If the gateway has no session/queue, the pump logs and exits (it must not
    spin) — surfaces the 'no audio queue' wiring failure loudly."""
    gw = MagicMock()
    gw.get_audio_queue = MagicMock(return_value=None)
    rt = AsyncMock()
    rt.closed = MagicMock(return_value=False)

    bridge = RealtimeBridge(
        call_id="call-2",
        realtime_session=rt,
        media_gateway=gw,
        internal_sample_rate=8000,
    )
    await asyncio.wait_for(bridge._pump_caller_audio(), timeout=1.0)
    gw.get_audio_queue.assert_called_once_with("call-2")


@pytest.mark.asyncio
async def test_model_pump_decodes_mulaw_to_pcm_for_gateway():
    """The model pump must μ-law-decode OpenAI audio and hand PCM16 (same 8 kHz
    rate) to gateway.send_audio — no resample when internal_rate == 8 kHz."""
    from app.infrastructure.realtime.openai_realtime import RealtimeEvent

    mulaw_audio = bytes(range(160))  # one 20 ms model frame

    async def _events():
        yield RealtimeEvent(kind="audio", audio=mulaw_audio)

    rt = MagicMock()
    rt.events = _events
    rt.closed = MagicMock(return_value=False)

    gw = MagicMock()
    gw.send_audio = AsyncMock()

    bridge = RealtimeBridge(
        call_id="call-3",
        realtime_session=rt,
        media_gateway=gw,
        internal_sample_rate=8000,
    )
    await asyncio.wait_for(bridge._pump_model_events(), timeout=1.0)

    gw.send_audio.assert_awaited_once()
    _, pcm = gw.send_audio.await_args.args
    assert pcm == ulaw_to_pcm(mulaw_audio)   # decoded, not resampled
    assert len(pcm) == len(mulaw_audio) * 2
