"""Disclosure proof through real TtsPlayback, synthetic transport/provider only."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.services import recording_policy_service as policy
from app.domain.services.telephony.modes.agent_first import _speak_recording_disclosure
from app.domain.services.voice_pipeline.tts_playback import TtsPlayback
from app.infrastructure.telephony.telephony_media_gateway import TelephonyMediaGateway

CALL = "10000000-0000-4000-8000-000000000017"
TENANT = "20000000-0000-4000-8000-000000000017"
NOTICE = "This call is being recorded."
DECISION = policy.RecordingDecision(True, True, NOTICE, None, 90, "configured_notice")


class Provider:
    name = "synthetic"
    chunks = [b"\x01\x00" * 160]

    async def stream_synthesize(self, text, **kwargs):
        for data in self.chunks:
            yield SimpleNamespace(data=data)


class Gateway:
    def __init__(self, status, evidence, *, mismatch=False):
        self.status, self.evidence, self.mismatch = status, evidence, mismatch
        self.sent = []

    async def begin_playback(self, call_id, utterance_id):
        self.utterance_id = utterance_id

    async def send_audio(self, call_id, audio):
        self.sent.append(audio)

    async def flush_tts_buffer(self, call_id):
        pass

    async def finish_playback(self, call_id, utterance_id):
        return {
            "utterance_id": "other" if self.mismatch else utterance_id,
            "status": self.status,
            "evidence": self.evidence,
        }

    async def clear_output_buffer(self, call_id):
        pass


def make_voice(monkeypatch, gateway):
    monkeypatch.setattr(
        "app.core.container.get_container",
        lambda: SimpleNamespace(is_initialized=True, db_pool=object()),
    )
    monkeypatch.setattr(policy.RecordingPolicyService, "decide", AsyncMock(return_value=DECISION))
    policy.clear_disclosure_state(CALL)
    pipe = SimpleNamespace(
        tts_provider=Provider(),
        media_gateway=gateway,
        stt_provider=object(),
        latency_tracker=MagicMock(),
        tts_sample_rate=8000,
        _record_silent_turn=MagicMock(),
    )
    playback = TtsPlayback(pipe)
    # A no-audio recovery must not generate synthetic receipt proof for NOTICE.
    monkeypatch.setattr(playback, "_try_emergency_voice_clip", AsyncMock(return_value=False))
    pipe.synthesize_and_send_audio = playback.synthesize_and_send
    session = SimpleNamespace(
        call_id=CALL, turn_id=0, voice_id="fixture", conversation_history=[], tts_active=False
    )
    voice = SimpleNamespace(
        call_id=CALL,
        call_session=session,
        pipeline=pipe,
        media_gateway=gateway,
        _dialer_tenant_id=TENANT,
    )
    return voice, playback


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,evidence,mismatch,expected",
    [
        ("completed", "transport_played", False, "spoken"),
        ("completed", "transmitted", False, "transmitted"),
        ("unknown", "unknown", False, "failed"),
        ("failed", "transport_played", False, "failed"),
        ("interrupted", "transport_played", False, "failed"),
        ("partial", "transport_played", False, "failed"),
        ("completed", "unknown", False, "failed"),
        ("completed", "transport_played", True, "failed"),
    ],
)
async def test_only_matching_complete_supported_notice_delivery_qualifies(
    monkeypatch, status, evidence, mismatch, expected
):
    gateway = Gateway(status, evidence, mismatch=mismatch)
    voice, _ = make_voice(monkeypatch, gateway)
    await _speak_recording_disclosure(voice)
    assert len(gateway.sent) == 1
    assert policy.get_disclosure_state(CALL) == expected
    assert policy.consent_satisfied(DECISION, CALL) is (expected != "failed")
    assert voice.call_session._tts_playout_completed is (expected == "spoken")
    assert bool(voice.call_session.conversation_history) is (expected != "failed")


@pytest.mark.asyncio
@pytest.mark.parametrize("next_output", ["empty", "early-interruption", "error"])
async def test_a_later_no_audio_invocation_cannot_inherit_completed_receipt(
    monkeypatch, next_output
):
    voice, playback = make_voice(monkeypatch, Gateway("completed", "transport_played"))
    await playback.synthesize_and_send(voice.call_session, "Earlier words.", track_latency=False)
    assert voice.call_session._tts_playback_receipt["status"] == "completed"
    if next_output == "early-interruption":
        import asyncio

        event = asyncio.Event()
        event.set()
        await playback.synthesize_and_send(
            voice.call_session, NOTICE, barge_in_event=event, track_latency=False
        )
    else:
        if next_output == "empty":
            voice.pipeline.tts_provider.chunks = []
        else:

            def failed(*args, **kwargs):
                raise RuntimeError("synthetic provider failure")

            voice.pipeline.tts_provider.stream_synthesize = failed
        await _speak_recording_disclosure(voice)
        assert policy.get_disclosure_state(CALL) == "failed"
    assert voice.call_session._tts_playback_receipt is None
    assert voice.call_session._tts_playout_completed is False


@pytest.mark.asyncio
async def test_actual_asterisk_gateway_transmission_is_supported_without_hearing_claim(
    monkeypatch, tmp_path
):
    gateway = TelephonyMediaGateway()
    await gateway.initialize({"sample_rate": 8000, "tts_source_format": "s16le"})
    adapter = SimpleNamespace(begin_tts_utterance=AsyncMock(), send_tts_audio=AsyncMock())

    async def finish(call_id, utterance_id):
        return {
            "utterance_id": utterance_id,
            "status": "completed",
            "evidence": "transmitted",
            "played_ms": 20,
        }

    adapter.finish_tts_utterance = finish
    await gateway.on_call_started(CALL, {"adapter": adapter, "pbx_call_id": "synthetic-pbx"})
    voice, _ = make_voice(monkeypatch, gateway)
    await _speak_recording_disclosure(voice)
    assert policy.get_disclosure_state(CALL) == "transmitted"
    assert policy.consent_satisfied(DECISION, CALL)
    assert voice.call_session._tts_playout_completed is False
    adapter.send_tts_audio.assert_awaited()

    # The same transmitted ledger proof admits the configured storage path;
    # this does not elevate it into a human-hearing or transport-played claim.
    from contextlib import asynccontextmanager
    from app.domain.services import recording_service as recording

    @asynccontextmanager
    async def owned_call(pool, tenant):
        assert tenant == TENANT
        yield SimpleNamespace(fetchrow=AsyncMock(return_value={"id": CALL}))

    monkeypatch.setattr(recording, "acquire_with_tenant", owned_call)
    monkeypatch.setenv("LOCAL_RECORDINGS_DIR", str(tmp_path))
    monkeypatch.setattr(recording, "encode_recording_audio", lambda raw: (raw, ".wav", "audio/wav"))
    service = recording.RecordingService(
        object(), s3_client=SimpleNamespace(is_available=lambda: False)
    )
    service._insert_recording_record = AsyncMock(return_value="synthetic-recording")
    service._update_call_recording_url = AsyncMock()
    buffer = recording.RecordingBuffer(call_id=CALL)
    buffer.add_chunk(b"\x01\x00" * 160)
    assert (
        await service.save_and_link(CALL, buffer, TENANT, "synthetic-campaign")
        == "synthetic-recording"
    )
    assert len(list(tmp_path.rglob("*.wav"))) == 1
