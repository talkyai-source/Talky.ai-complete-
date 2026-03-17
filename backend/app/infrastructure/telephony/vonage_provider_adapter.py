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

import logging
import os
from typing import Any, Dict, Optional

from app.domain.interfaces.telephony_provider_adapter import TelephonyProviderAdapter
from app.domain.models.voice_contract import TelephonyProvider

logger = logging.getLogger(__name__)


class VonageProviderAdapter(TelephonyProviderAdapter):
    """
    TelephonyProviderAdapter for Vonage Voice API (cloud telephony).

    Configuration is read from environment variables:
      VONAGE_API_KEY, VONAGE_API_SECRET, VONAGE_APP_ID, VONAGE_PRIVATE_KEY_PATH

    The adapter lazily initialises the Vonage SDK client on first use.
    """

    def __init__(self) -> None:
        self._api_key: str = os.getenv("VONAGE_API_KEY", "")
        self._api_secret: str = os.getenv("VONAGE_API_SECRET", "")
        self._app_id: str = os.getenv("VONAGE_APP_ID", "")
        self._private_key_path: str = os.getenv("VONAGE_PRIVATE_KEY_PATH", "./config/private.key")
        self._client = None

    def _get_client(self):
        """Lazy-initialise the Vonage SDK client."""
        if self._client is not None:
            return self._client
        try:
            from vonage import Vonage, Auth
            auth = Auth(
                api_key=self._api_key,
                api_secret=self._api_secret,
                application_id=self._app_id,
                private_key=self._private_key_path,
            )
            self._client = Vonage(auth=auth)
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
        import asyncio

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
        call_uuid = await loop.run_in_executor(None, _create_call)
        logger.info("VonageProviderAdapter: originated call %s → %s", call_uuid, destination)
        return str(call_uuid)

    async def hangup(self, call_id: str) -> None:
        import asyncio

        client = self._get_client()

        def _hangup():
            try:
                from vonage_voice import UpdateCallRequest
                client.voice.update_call(call_id, UpdateCallRequest(action="hangup"))
            except ImportError:
                client.voice.update_call(call_id, {"action": "hangup"})

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _hangup)
        logger.info("VonageProviderAdapter: hung up %s", call_id)

    async def transfer(
        self,
        call_id: str,
        destination: str,
        mode: str = "blind",
    ) -> Dict[str, Any]:
        import asyncio

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
        await loop.run_in_executor(None, _transfer)
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

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "vonage"

    @property
    def provider_type(self) -> TelephonyProvider:
        return TelephonyProvider.VONAGE
