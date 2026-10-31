"""Deepgram prerecorded response/error handling without network calls."""

from __future__ import annotations

import pytest

from app.infrastructure.stt.deepgram_prerecorded import (
    DeepgramPrerecordedTranscriber,
    PrerecordedTranscriptionError,
)


class FakeCredentials:
    async def resolve(self, provider: str, *, tenant_id: str):
        assert provider == "deepgram"
        return "test-key"


class FakeResponse:
    def __init__(self, status: int, payload: dict) -> None:
        self.status = status
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self, content_type=None):
        return self.payload


class FakeSession:
    def __init__(self, response: FakeResponse | None = None, *, timeout=None) -> None:
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def post(self, *args, **kwargs):
        assert kwargs["headers"]["Authorization"] == "Token test-key"
        assert kwargs["headers"]["Content-Type"] == "audio/webm"
        assert kwargs["params"]["model"] == "nova-3"
        return self.response


def _factory(response: FakeResponse):
    return lambda **kwargs: FakeSession(response, **kwargs)


async def test_parses_transcript_request_id_and_duration():
    response = FakeResponse(
        200,
        {
            "metadata": {"request_id": "req-123", "duration": 3.25},
            "results": {"channels": [{"alternatives": [{"transcript": "  Clear note.  "}]}]},
        },
    )
    transcriber = DeepgramPrerecordedTranscriber(
        object(),
        credential_resolver=FakeCredentials(),
        session_factory=_factory(response),
    )

    result = await transcriber.transcribe(
        audio=b"audio",
        mime_type="audio/webm",
        tenant_id="tenant-1",
    )

    assert result.text == "Clear note."
    assert result.provider_request_id == "req-123"
    assert result.duration_seconds == 3.25


async def test_http_error_is_safe_and_actionable():
    response = FakeResponse(429, {"err_msg": "rate limited"})
    transcriber = DeepgramPrerecordedTranscriber(
        object(),
        credential_resolver=FakeCredentials(),
        session_factory=_factory(response),
    )

    with pytest.raises(
        PrerecordedTranscriptionError,
        match=r"HTTP 429.*rate limited",
    ):
        await transcriber.transcribe(
            audio=b"audio",
            mime_type="audio/webm",
            tenant_id="tenant-1",
        )


async def test_timeout_becomes_retryable_provider_error():
    class TimeoutContext:
        async def __aenter__(self):
            raise TimeoutError

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class TimeoutSession(FakeSession):
        def post(self, *args, **kwargs):
            return TimeoutContext()

    transcriber = DeepgramPrerecordedTranscriber(
        object(),
        credential_resolver=FakeCredentials(),
        timeout_seconds=0.1,
        session_factory=lambda **kwargs: TimeoutSession(**kwargs),
    )

    with pytest.raises(PrerecordedTranscriptionError, match="timed out"):
        await transcriber.transcribe(
            audio=b"audio",
            mime_type="audio/webm",
            tenant_id="tenant-1",
        )
