"""
Vonage Voice Provider Adapter.

Implements ``TelephonyProviderAdapter`` using the official Vonage Voice API.

Call model (per Vonage docs):
  1. Outbound: ``POST https://api.nexmo.com/v1/calls`` with an NCCO body.
  2. Inbound: Vonage hits ``answer_url`` → we return NCCO JSON.
  3. Audio: NCCO ``connect`` action opens a WebSocket from Vonage **to us**.
  4. Events: Vonage POSTs status updates to ``event_url``.

The NCCO ``connect`` action with a ``websocket`` endpoint is the official
pattern for real-time AI voice processing (speech recognition, TTS playback).
See: https://developer.vonage.com/en/voice/voice-api/concepts/websockets

SDK: ``vonage`` v4.x  (``pip install vonage``)
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, Optional

from app.domain.interfaces.telephony_provider_adapter import TelephonyProviderAdapter
from app.domain.models.voice_contract import TelephonyProvider

logger = logging.getLogger(__name__)

# Bound every Vonage REST round-trip. A stalled/black-holed TCP connection to
# Vonage must never wedge the awaiting coroutine indefinitely — that wedges
# origination/teardown for the whole call. asyncio.wait_for() around every
# executor call guarantees the calling coroutine is unblocked on schedule and
# the operation fails cleanly instead of hanging forever, independent of
# whatever timeout behaviour the underlying SDK's HTTP transport does or
# doesn't honour.
_REST_TIMEOUT_SECONDS = 10.0


class VonageProviderAdapter(TelephonyProviderAdapter):
    """
    TelephonyProviderAdapter for Vonage Voice API (cloud telephony).

    Configuration is read from environment variables:
      VONAGE_API_KEY, VONAGE_API_SECRET, VONAGE_APP_ID, VONAGE_PRIVATE_KEY_PATH

    The adapter lazily initialises the Vonage SDK client on first use.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        app_id: Optional[str] = None,
        private_key: Optional[str] = None,
        private_key_path: Optional[str] = None,
        from_number: Optional[str] = None,
    ) -> None:
        """
        Construct with explicit per-tenant creds OR fall back to env vars.

        ``private_key`` is the PEM body (e.g. value pulled from DB). Either
        ``private_key`` or ``private_key_path`` may be provided; the SDK
        accepts either.
        """
        self._api_key: str = api_key or os.getenv("VONAGE_API_KEY", "")
        self._api_secret: str = api_secret or os.getenv("VONAGE_API_SECRET", "")
        self._app_id: str = app_id or os.getenv("VONAGE_APP_ID", "")
        self._private_key: Optional[str] = private_key
        self._private_key_path: str = private_key_path or os.getenv(
            "VONAGE_PRIVATE_KEY_PATH", "./config/private.key"
        )
        self._from_number: str = from_number or ""
        self._client = None

    def _get_client(self):
        """Lazy-initialise the Vonage SDK client."""
        if self._client is not None:
            return self._client
        try:
            from vonage import Vonage, Auth
            # Prefer in-memory key (per-tenant DB-stored) over a filesystem path.
            private_key_arg = self._private_key or self._private_key_path
            auth = Auth(
                api_key=self._api_key,
                api_secret=self._api_secret,
                application_id=self._app_id,
                private_key=private_key_arg,
            )
            options = None
            try:
                # Client-level timeout: bounds the underlying HTTP transport
                # so a black-holed connection doesn't sit forever even
                # before our asyncio.wait_for() guard kicks in.
                from vonage_http_client import HttpClientOptions
                options = HttpClientOptions(timeout=_REST_TIMEOUT_SECONDS)
            except Exception as exc:  # pragma: no cover - defensive only
                logger.debug("Vonage HttpClientOptions timeout setup failed: %s", exc)
            self._client = Vonage(auth=auth, options=options) if options else Vonage(auth=auth)
            return self._client
        except ImportError:
            raise RuntimeError(
                "vonage SDK not installed. Run: pip install vonage"
            )
        except Exception as exc:
            raise RuntimeError(f"Failed to initialise Vonage client: {exc}")

    # ------------------------------------------------------------------
    # Call lifecycle
    # ------------------------------------------------------------------

    async def originate_call(
        self,
        destination: str,
        caller_id: str,
        webhook_base_url: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Originate an outbound call via Vonage Voice API.

        The NCCO instructs Vonage to connect the call audio to our WebSocket
        endpoint so the AI pipeline can process it in real time.
        """
        client = self._get_client()

        ws_url = f"{webhook_base_url.replace('http', 'ws', 1)}/api/v1/vonage/ws-audio"

        ncco = [
            {
                "action": "talk",
                "text": "Please wait while we connect you.",
                "language": "en-US",
                "style": 0,
            },
            {
                "action": "connect",
                "endpoint": [
                    {
                        "type": "websocket",
                        "uri": ws_url,
                        "content-type": "audio/l16;rate=16000",
                        "headers": {
                            "caller_id": caller_id,
                            "destination": destination,
                            **(metadata or {}),
                        },
                    }
                ],
            },
        ]

        def _create_call():
            try:
                from vonage_voice import CreateCallRequest, Phone, ToPhone
                to_phone = ToPhone(type="phone", number=destination)
                request = CreateCallRequest(
                    to=[to_phone],
                    from_=Phone(type="phone", number=caller_id),
                    ncco=ncco,
                    event_url=[f"{webhook_base_url}/api/v1/vonage/event"],
                )
                response = client.voice.create_call(request)
                return response.uuid
            except ImportError:
                response = client.voice.create_call({
                    "to": [{"type": "phone", "number": destination}],
                    "from": {"type": "phone", "number": caller_id},
                    "ncco": ncco,
                    "event_url": [f"{webhook_base_url}/api/v1/vonage/event"],
                })
                return response.get("uuid", response.get("conversation_uuid", ""))

        loop = asyncio.get_running_loop()
        try:
            call_uuid = await asyncio.wait_for(
                loop.run_in_executor(None, _create_call), timeout=_REST_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"Vonage originate_call timed out after {_REST_TIMEOUT_SECONDS}s"
            ) from exc
        logger.info("VonageProviderAdapter: originated call %s → %s", call_uuid, destination)
        return str(call_uuid)

    async def hangup(self, call_id: str) -> None:
        client = self._get_client()

        def _hangup():
            try:
                from vonage_voice import UpdateCallRequest
                client.voice.update_call(call_id, UpdateCallRequest(action="hangup"))
            except ImportError:
                client.voice.update_call(call_id, {"action": "hangup"})

        loop = asyncio.get_running_loop()
        try:
            await asyncio.wait_for(
                loop.run_in_executor(None, _hangup), timeout=_REST_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"Vonage hangup timed out after {_REST_TIMEOUT_SECONDS}s"
            ) from exc
        logger.info("VonageProviderAdapter: hung up %s", call_id)

    async def transfer(
        self,
        call_id: str,
        destination: str,
        mode: str = "blind",
    ) -> Dict[str, Any]:
        client = self._get_client()
        ncco = [{"action": "connect", "endpoint": [{"type": "phone", "number": destination}]}]

        def _transfer():
            try:
                from vonage_voice import UpdateCallRequest
                client.voice.update_call(
                    call_id,
                    UpdateCallRequest(action="transfer", destination={"type": "ncco", "ncco": ncco}),
                )
            except ImportError:
                client.voice.update_call(call_id, {
                    "action": "transfer",
                    "destination": {"type": "ncco", "ncco": ncco},
                })

        loop = asyncio.get_running_loop()
        try:
            await asyncio.wait_for(
                loop.run_in_executor(None, _transfer), timeout=_REST_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"Vonage transfer timed out after {_REST_TIMEOUT_SECONDS}s"
            ) from exc
        return {"status": "transferred", "attempt_id": call_id, "mode": mode}

    # ------------------------------------------------------------------
    # Audio configuration
    # ------------------------------------------------------------------

    async def get_audio_config(self) -> Dict[str, Any]:
        return {
            "type": "websocket",
            "sample_rate": 16000,
            "encoding": "audio/l16;rate=16000",
            "channels": 1,
        }

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        if not self._api_key or not self._app_id:
            return False
        try:
            self._get_client()
            return True
        except Exception:
            return False

    async def ping_with_detail(self) -> Dict[str, Any]:
        """
        Verbose credential check for the Settings "Test" button.

        Validates we can build a Vonage client with the supplied creds.
        A full API round-trip is intentionally avoided to keep this cheap
        and to not require the test creds to have any specific scopes.
        Returns ``{ok, latency_ms, error?}``.
        """
        import time
        start = time.perf_counter()
        if not self._api_key or not self._app_id:
            return {
                "ok": False,
                "latency_ms": 0,
                "error": "Missing api_key or app_id",
            }
        try:
            self._get_client()
            latency_ms = int((time.perf_counter() - start) * 1000)
            return {"ok": True, "latency_ms": latency_ms}
        except Exception as exc:
            latency_ms = int((time.perf_counter() - start) * 1000)
            return {"ok": False, "latency_ms": latency_ms, "error": str(exc)}

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "vonage"

    @property
    def provider_type(self) -> TelephonyProvider:
        return TelephonyProvider.VONAGE
