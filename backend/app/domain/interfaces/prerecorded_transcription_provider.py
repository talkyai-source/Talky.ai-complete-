"""Contract for transcribing an already-recorded audio blob."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class PrerecordedTranscript:
    """Provider-neutral result for a single prerecorded audio file."""

    text: str
    provider_request_id: str | None = None
    duration_seconds: float | None = None


class PrerecordedTranscriptionProvider(ABC):
    """A short, request/response transcription provider.

    This is intentionally separate from ``STTProvider``: that interface owns
    the long-lived streaming contract used by live calls, while feedback notes
    are complete containerized files and use Deepgram's prerecorded API.
    """

    @abstractmethod
    async def transcribe(
        self,
        *,
        audio: bytes,
        mime_type: str,
        tenant_id: str,
    ) -> PrerecordedTranscript:
        """Return a final transcript or raise a provider-specific error."""
