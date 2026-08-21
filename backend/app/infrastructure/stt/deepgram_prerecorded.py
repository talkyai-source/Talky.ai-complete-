"""Deepgram prerecorded transcription for short call-feedback notes."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any

import aiohttp

from app.domain.interfaces.prerecorded_transcription_provider import (
    PrerecordedTranscript,
    PrerecordedTranscriptionProvider,
)
from app.domain.services.credential_resolver import CredentialResolver

logger = logging.getLogger(__name__)


class PrerecordedTranscriptionError(RuntimeError):
    """A safe-to-store description of a prerecorded transcription failure."""


def _positive_float_env(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


class DeepgramPrerecordedTranscriber(PrerecordedTranscriptionProvider):
    """Send one complete audio file to Deepgram's ``POST /v1/listen`` API."""

    API_URL = "https://api.deepgram.com/v1/listen"

    def __init__(
        self,
        db_pool: Any,
        *,
        credential_resolver: CredentialResolver | None = None,
        timeout_seconds: float | None = None,
        model: str | None = None,
        language: str | None = None,
        session_factory: Callable[..., aiohttp.ClientSession] = aiohttp.ClientSession,
    ) -> None:
        self._credentials = credential_resolver or CredentialResolver(db_pool=db_pool)
        self._timeout_seconds = timeout_seconds or _positive_float_env(
            "CALL_FEEDBACK_TRANSCRIPTION_TIMEOUT_SECONDS", 10.0
        )
        self._model = model or os.getenv("CALL_FEEDBACK_DEEPGRAM_MODEL", "nova-3")
        self._language = (
            language if language is not None else os.getenv("CALL_FEEDBACK_DEEPGRAM_LANGUAGE", "en")
        )
        self._session_factory = session_factory

    async def transcribe(
        self,
        *,
        audio: bytes,
        mime_type: str,
        tenant_id: str,
    ) -> PrerecordedTranscript:
        if not audio:
            raise PrerecordedTranscriptionError("Audio is empty")

        api_key = await self._credentials.resolve(
            "deepgram",
            tenant_id=tenant_id,
        )
        if not api_key:
            raise PrerecordedTranscriptionError("Deepgram API key is not configured")

        params = {
            "model": self._model,
            "smart_format": "true",
            # Match the privacy posture used by the streaming providers.
            "mip_opt_out": "true",
        }
        if self._language:
            params["language"] = self._language

        headers = {
            "Authorization": f"Token {api_key}",
            "Content-Type": mime_type,
        }
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)

        try:
            async with (
                self._session_factory(timeout=timeout) as session,
                session.post(
                    self.API_URL,
                    params=params,
                    headers=headers,
                    data=audio,
                ) as response,
            ):
                try:
                    payload = await response.json(content_type=None)
                except Exception as exc:
                    raise PrerecordedTranscriptionError(
                        f"Deepgram returned an invalid response (HTTP {response.status})"
                    ) from exc

                if response.status < 200 or response.status >= 300:
                    provider_message = ""
                    if isinstance(payload, dict):
                        provider_message = str(
                            payload.get("err_msg")
                            or payload.get("error")
                            or payload.get("message")
                            or ""
                        ).strip()
                    suffix = f": {provider_message[:300]}" if provider_message else ""
                    raise PrerecordedTranscriptionError(
                        f"Deepgram request failed (HTTP {response.status}){suffix}"
                    )
        except TimeoutError as exc:
            raise PrerecordedTranscriptionError(
                f"Deepgram timed out after {self._timeout_seconds:g} seconds"
            ) from exc
        except aiohttp.ClientError as exc:
            raise PrerecordedTranscriptionError("Deepgram network request failed") from exc

        if not isinstance(payload, dict):
            raise PrerecordedTranscriptionError("Deepgram returned an invalid response")

        try:
            alternatives = payload["results"]["channels"][0]["alternatives"]
            transcript = str(alternatives[0].get("transcript") or "").strip()
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise PrerecordedTranscriptionError(
                "Deepgram response did not contain a transcript"
            ) from exc

        metadata = payload.get("metadata") or {}
        request_id = metadata.get("request_id") if isinstance(metadata, dict) else None
        duration = metadata.get("duration") if isinstance(metadata, dict) else None
        try:
            parsed_duration = float(duration) if duration is not None else None
        except (TypeError, ValueError):
            parsed_duration = None

        logger.info(
            "feedback_transcription_provider_succeeded tenant=%s request_id=%s bytes=%d",
            tenant_id,
            request_id,
            len(audio),
        )
        return PrerecordedTranscript(
            text=transcript,
            provider_request_id=str(request_id) if request_id else None,
            duration_seconds=parsed_duration,
        )
