"""Recordings are stored compressed (MP3 by default) with the right MIME type,
and the tenant can configure the recording policy that gates them."""
from __future__ import annotations

import io
import subprocess
import wave
from types import SimpleNamespace

import pytest

import app.domain.services.recording_service as rs
from app.api.v1.endpoints import recordings as rec_ep


def _wav(seconds: float = 0.2, rate: int = 16000, channels: int = 2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x10" * int(seconds * rate * channels))
    return buf.getvalue()


def test_default_codec_is_mp3_and_wav_is_the_fallback():
    assert rs.RECORDING_AUDIO_CODEC in ("mp3", "opus", "wav")
    assert rs._CODEC_SPECS["mp3"][1:] == (".mp3", "audio/mpeg")
    assert rs._CODEC_SPECS["opus"][1:] == (".ogg", "audio/ogg")


def test_encode_falls_back_to_wav_when_ffmpeg_is_missing(monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    wav = _wav()
    data, ext, mime = rs.encode_recording_audio(wav, "mp3")
    assert (data, ext, mime) == (wav, ".wav", "audio/wav")


def test_encode_falls_back_to_wav_on_encoder_failure(monkeypatch):
    def failing_run(cmd, **kwargs):
        return SimpleNamespace(returncode=1, stdout=b"", stderr=b"boom")

    monkeypatch.setattr(subprocess, "run", failing_run)
    wav = _wav()
    data, ext, mime = rs.encode_recording_audio(wav, "mp3", ffmpeg_path="/usr/bin/ffmpeg")
    assert (data, ext, mime) == (wav, ".wav", "audio/wav")


def test_encode_invokes_ffmpeg_with_the_codec_spec_and_returns_its_output(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["input"] = kwargs.get("input")
        return SimpleNamespace(returncode=0, stdout=b"ID3mp3bytes", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    wav = _wav()
    data, ext, mime = rs.encode_recording_audio(wav, "mp3", ffmpeg_path="/usr/bin/ffmpeg")
    assert (data, ext, mime) == (b"ID3mp3bytes", ".mp3", "audio/mpeg")
    assert seen["input"] == wav
    assert seen["cmd"][:1] == ["/usr/bin/ffmpeg"]
    assert "libmp3lame" in seen["cmd"] and "pipe:0" in seen["cmd"] and seen["cmd"][-1] == "pipe:1"

    data, ext, mime = rs.encode_recording_audio(wav, "opus", ffmpeg_path="/usr/bin/ffmpeg")
    assert (ext, mime) == (".ogg", "audio/ogg") and "libopus" in seen["cmd"]


def test_wav_codec_is_a_passthrough():
    wav = _wav()
    assert rs.encode_recording_audio(wav, "wav") == (wav, ".wav", "audio/wav")


@pytest.mark.skipif(__import__("shutil").which("ffmpeg") is None, reason="ffmpeg not installed here")
def test_real_ffmpeg_produces_a_much_smaller_file():
    wav = _wav(seconds=2.0)
    data, ext, mime = rs.encode_recording_audio(wav, "mp3")
    assert ext == ".mp3" and mime == "audio/mpeg"
    assert len(data) < len(wav) / 4


@pytest.mark.asyncio
async def test_local_save_stores_the_encoded_file_with_its_mime(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    monkeypatch.setenv("LOCAL_RECORDINGS_DIR", str(tmp_path))
    monkeypatch.setattr(rs, "encode_recording_audio", lambda wav, codec=None, **kw: (b"MP3DATA", ".mp3", "audio/mpeg"))
    buf = rs.RecordingBuffer(call_id="call-9", sample_rate=16000, channels=2, bit_depth=16)
    buf.add_chunk(b"\x01\x02" * 400)
    svc = rs.RecordingService(db_pool=AsyncMock())
    inserted = {}

    async def fake_insert(**kwargs):
        inserted.update(kwargs)
        return None

    monkeypatch.setattr(svc, "_insert_recording_record", fake_insert)
    result = await svc._save_local(call_id="call-9", buffer=buf, tenant_id="t1", campaign_id="c1")
    assert (tmp_path / "call-9.mp3").read_bytes() == b"MP3DATA"
    assert inserted["mime_type"] == "audio/mpeg"
    assert inserted["file_size_bytes"] == len(b"MP3DATA")
    assert inserted["s3_key"].endswith("call-9.mp3")
    assert result is not None


def test_s3_key_carries_the_storage_extension():
    assert rs.RecordingService._s3_key("t", "c", "call", ".mp3").endswith("/call.mp3")
    assert rs.RecordingService._s3_key("t", "c", "call").endswith("/call.wav")


def test_download_extension_follows_the_stored_mime():
    assert rec_ep._download_extension("audio/mpeg") == "mp3"
    assert rec_ep._download_extension("audio/ogg") == "ogg"
    assert rec_ep._download_extension("audio/wav") == "wav"
    assert rec_ep._download_extension(None) == "wav"


# ---------------------------------------------------------------------------
# Recording policy endpoint models
# ---------------------------------------------------------------------------

def test_policy_body_validates_modes_digits_and_country_codes():
    body = rec_ep.RecordingPolicyBody(
        default_consent_mode=" Two_Party ", announcement_text="  This call  is recorded. ",
        opt_out_dtmf_digit="9", two_party_country_codes=["gb", "us-ca", "GB", ""], retention_days=30,
    )
    assert body.default_consent_mode == "two_party"
    assert body.announcement_text == "This call is recorded."
    assert body.two_party_country_codes == ["GB", "US-CA"]
    with pytest.raises(Exception):
        rec_ep.RecordingPolicyBody(default_consent_mode="maybe")
    with pytest.raises(Exception):
        rec_ep.RecordingPolicyBody(default_consent_mode="one_party", two_party_country_codes=["United Kingdom"])
    with pytest.raises(Exception):
        rec_ep.RecordingPolicyBody(default_consent_mode="one_party", opt_out_dtmf_digit="x")


def test_policy_response_explains_the_absent_state():
    out = rec_ep._policy_response(None)
    assert out.configured is False and "NOT recorded" in out.effect
    out = rec_ep._policy_response({"default_consent_mode": "one_party", "retention_days": 90, "two_party_country_codes": None})
    assert out.configured and out.default_consent_mode == "one_party" and out.two_party_country_codes == []


def test_policy_routes_are_registered_before_the_dynamic_recording_routes():
    paths = [getattr(r, "path", "") for r in rec_ep.router.routes]
    assert "/recordings/policy" in paths
    assert paths.index("/recordings/policy") < paths.index("/recordings/{recording_id}/stream")
