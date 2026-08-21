"""HTTP contract tests for call-feedback submission."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.dependencies import CurrentUser, get_current_user
from app.api.v1.endpoints.call_feedback import (
    get_call_feedback_service,
    router,
)
from app.domain.services.call_feedback_service import FeedbackStorageError

CALL_ID = "22222222-2222-2222-2222-222222222222"
TENANT_ID = "11111111-1111-1111-1111-111111111111"
USER_ID = "33333333-3333-3333-3333-333333333333"


def _failed_row() -> dict:
    now = datetime.now(UTC)
    return {
        "id": "44444444-4444-4444-4444-444444444444",
        "call_id": CALL_ID,
        "audio_mime_type": "audio/webm",
        "audio_size_bytes": 12,
        "duration_seconds": 1.5,
        "transcript": None,
        "transcript_status": "failed",
        "transcript_error": "Deepgram timed out after 10 seconds",
        "transcription_attempts": 1,
        "created_at": now,
        "updated_at": now,
    }


class FakeService:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.submissions: list[dict] = []

    async def submit(self, **kwargs):
        self.submissions.append(kwargs)
        if self.error:
            raise self.error
        row = _failed_row()
        row["audio_size_bytes"] = len(kwargs["audio"])
        row["audio_mime_type"] = kwargs["mime_type"]
        return row


def _client(service: FakeService) -> TestClient:
    app = FastAPI()
    app.include_router(router)

    async def current_user() -> CurrentUser:
        return CurrentUser(
            id=USER_ID,
            email="reviewer@example.com",
            tenant_id=TENANT_ID,
            role="user",
        )

    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_call_feedback_service] = lambda: service
    return TestClient(app)


def test_transcription_failure_is_http_200_because_audio_is_saved():
    service = FakeService()
    client = _client(service)

    response = client.post(
        f"/calls/{CALL_ID}/feedback",
        files={
            "audio": (
                "note.webm",
                b"audio-bytes",
                "audio/webm;codecs=opus",
            )
        },
        data={"duration_seconds": "1.5"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["transcript_status"] == "failed"
    assert payload["retryable"] is True
    assert payload["audio_url"] == f"/api/v1/calls/{CALL_ID}/feedback/audio"
    assert service.submissions[0]["mime_type"] == "audio/webm"


def test_storage_failure_is_http_503_because_note_is_not_durable():
    service = FakeService(error=FeedbackStorageError("Could not store feedback audio"))
    client = _client(service)

    response = client.post(
        f"/calls/{CALL_ID}/feedback",
        files={"audio": ("note.webm", b"audio-bytes", "audio/webm")},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Could not store feedback audio"}


def test_unsupported_media_type_is_rejected_before_service_call():
    service = FakeService()
    client = _client(service)

    response = client.post(
        f"/calls/{CALL_ID}/feedback",
        files={"audio": ("note.txt", b"not audio", "text/plain")},
    )

    assert response.status_code == 415
    assert service.submissions == []
