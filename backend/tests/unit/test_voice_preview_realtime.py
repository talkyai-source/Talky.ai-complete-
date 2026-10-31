"""Realtime samples use the actual speech-to-speech model, never a TTS substitute."""
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
from fastapi import HTTPException
from app.realtime import preview
from app.realtime.openai import RealtimeEvent


@pytest.mark.asyncio
async def test_preview_uses_realtime_and_closes_session(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    async def events():
        yield RealtimeEvent(kind="response_candidate", audio=b"\xff" * 160, text="Hello")
    session = SimpleNamespace(connect=AsyncMock(return_value=True), send_text=AsyncMock(), events=events, close=AsyncMock())
    factory = MagicMock(return_value=session)
    monkeypatch.setattr(preview, "OpenAIRealtimeSession", factory)
    result = await preview.preview_voice("ash", "Hello")
    assert factory.call_args.kwargs["model"] == "gpt-realtime-2"
    assert result["duration_seconds"] == 0.02
    assert len(base64.b64decode(result["audio_base64"])) == 480 * 4
    session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_preview_connection_failure_is_explicit(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    session = SimpleNamespace(connect=AsyncMock(return_value=False), close=AsyncMock())
    monkeypatch.setattr(preview, "OpenAIRealtimeSession", lambda **kwargs: session)
    with pytest.raises(HTTPException) as error:
        await preview.preview_voice("ash", "Hello")
    assert error.value.status_code == 502
    session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_voice_and_missing_key_fail_before_connection(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(HTTPException) as error:
        await preview.preview_voice("unknown", "Hello")
    assert error.value.status_code == 400
    with pytest.raises(HTTPException) as error:
        await preview.preview_voice("ash", "Hello")
    assert error.value.status_code == 503
