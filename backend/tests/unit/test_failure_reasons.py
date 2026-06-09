"""Tests for telephony failure-reason humanization (Phase 1).

humanize_failure() turns raw pipeline error strings into a coarse category +
a short caller-facing message for the dashboard.
"""
from app.domain.services.telephony.failure_reasons import humanize_failure


def test_elevenlabs_quota_is_tts_with_actionable_message():
    cat, msg = humanize_failure(
        "RuntimeError('ElevenLabs API error: 401 "
        '{"detail":{"code":"quota_exceeded","message":"You have 0 credits remaining"}}'
        "')"
    )
    assert cat == "tts"
    assert "ElevenLabs" in msg and ("credit" in msg.lower() or "top up" in msg.lower())


def test_tts_auth_error_is_tts():
    cat, msg = humanize_failure("ElevenLabs API error: 401 unauthorized invalid api key")
    assert cat == "tts"


def test_gemini_circuit_open_is_llm():
    cat, msg = humanize_failure("Circuit 'gemini-llm' is OPEN — retry in 12.0s")
    assert cat == "llm"


def test_stt_error_is_stt():
    cat, _ = humanize_failure("Deepgram Flux pre-connect failed: handshake timeout")
    assert cat == "stt"


def test_carrier_error_is_telephony():
    cat, _ = humanize_failure("PJSIP originate failed: 408 request timeout from carrier")
    assert cat == "telephony"


def test_empty_reason_is_safe():
    cat, msg = humanize_failure(None)
    assert cat == "prewarm"
    assert msg  # non-empty, human-readable


def test_unknown_reason_is_truncated_and_generic():
    long = "Weird unexpected error " * 50
    cat, msg = humanize_failure(long)
    assert cat == "prewarm"
    assert len(msg) <= 220  # truncated, no raw log dump
