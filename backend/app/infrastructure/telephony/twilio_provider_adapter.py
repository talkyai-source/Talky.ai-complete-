"""
Twilio Voice Provider Adapter.

Implements ``TelephonyProviderAdapter`` against Twilio Programmable Voice.

Call model:
  1. Outbound: ``client.calls.create(to, from_, url)`` — Twilio fetches TwiML
     from ``url`` to instruct call flow.
  2. Inbound: Twilio hits ``answer_url`` → we return TwiML.
  3. Audio: ``<Connect><Stream url="wss://..."/></Connect>`` opens a
     bi-directional WebSocket from Twilio to us (Media Streams).
  4. Events: Twilio POSTs status updates to the configured webhook.

Credentials are NOT read from env — they are injected by the per-tenant
factory (``TelephonyProviderFactory.create_for_tenant``). This is what
makes the adapter usable for multi-tenant SaaS.

SDK: ``twilio>=9.0.0`` (``pip install twilio``)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from app.domain.interfaces.telephony_provider_adapter import TelephonyProviderAdapter
from app.domain.models.voice_contract import TelephonyProvider

logger = logging.getLogger(__name__)


class TwilioProviderAdapter(TelephonyProviderAdapter):
    """
    Per-tenant Twilio adapter.

    Construct with the tenant's own account_sid / auth_token / from_number.
    The Twilio REST client is lazy-initialised so an instance can be
    constructed cheaply (e.g. for a `ping()` against test credentials)
    without paying the SDK import cost up front.
    """

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str = "",
    ) -> None:
        if not account_sid or not auth_token:
            raise ValueError("TwilioProviderAdapter requires account_sid and auth_token")
        self._account_sid = account_sid
        self._auth_token = auth_token
        self._from_number = from_number
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from twilio.rest import Client
            self._client = Client(self._account_sid, self._auth_token)
            return self._client
        except ImportError as exc:
            raise RuntimeError(
                "twilio SDK not installed. Run: pip install 'twilio>=9.0.0'"
            ) from exc

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
        Place an outbound call.

        ``caller_id`` is preferred; falls back to the adapter's configured
        ``from_number`` if caller_id is empty. Twilio rejects calls without
        a valid Twilio-owned or Twilio-verified caller ID.
        """
        client = self._get_client()
        from_number = caller_id or self._from_number
        if not from_number:
            raise ValueError("Twilio call requires a caller_id or configured from_number")

        # Twilio fetches TwiML from this URL on call connect. The
        # /twilio/answer endpoint should return a <Connect><Stream/></Connect>
        # pointing at our Media Streams WebSocket. (Backend route is a
        # separate concern — adapter just hands Twilio the URL.)
        answer_url = f"{webhook_base_url}/api/v1/twilio/answer"
        status_callback = f"{webhook_base_url}/api/v1/twilio/event"

        def _create_call():
            call = client.calls.create(
                to=destination,
                from_=from_number,
                url=answer_url,
                status_callback=status_callback,
                status_callback_event=["initiated", "ringing", "answered", "completed"],
            )
            return call.sid

        loop = asyncio.get_running_loop()
        call_sid = await loop.run_in_executor(None, _create_call)
        logger.info("TwilioProviderAdapter: originated call %s → %s", call_sid, destination)
        return str(call_sid)

    async def hangup(self, call_id: str) -> None:
        client = self._get_client()

        def _hangup():
            client.calls(call_id).update(status="completed")

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _hangup)
        logger.info("TwilioProviderAdapter: hung up %s", call_id)

    async def transfer(
        self,
        call_id: str,
        destination: str,
        mode: str = "blind",
    ) -> Dict[str, Any]:
        """
        Blind transfer via Twilio: redirect the in-progress call to a new
        TwiML URL that dials the new destination.

        Attended/deflect modes are not supported in this first cut.
        """
        client = self._get_client()
        twiml = (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<Response><Dial>{destination}</Dial></Response>'
        )

        def _transfer():
            client.calls(call_id).update(twiml=twiml)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _transfer)
        return {"status": "transferred", "attempt_id": call_id, "mode": mode}

    # ------------------------------------------------------------------
    # Audio configuration
    # ------------------------------------------------------------------

    async def get_audio_config(self) -> Dict[str, Any]:
        # Twilio Media Streams default: 8kHz mulaw over WebSocket
        return {
            "type": "websocket",
            "sample_rate": 8000,
            "encoding": "audio/x-mulaw",
            "channels": 1,
        }

    # ------------------------------------------------------------------
    # Health / ping
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """
        Validate that the account_sid + auth_token are accepted by Twilio.

        Fetches the account record — the cheapest credential check available.
        """
        try:
            client = self._get_client()
        except Exception as exc:
            logger.debug("Twilio SDK init failed: %s", exc)
            return False

        def _ping():
            client.api.v2010.accounts(self._account_sid).fetch()
            return True

        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _ping)
        except Exception as exc:
            logger.debug("Twilio health_check failed: %s", exc)
            return False

    async def ping_with_detail(self) -> Dict[str, Any]:
        """
        Verbose credential check — returns ``{ok, latency_ms, error?, account_status?}``
        so the Settings UI can show actionable feedback to the user.
        """
        import time

        try:
            client = self._get_client()
        except Exception as exc:
            return {"ok": False, "error": f"SDK init failed: {exc}"}

        def _ping():
            return client.api.v2010.accounts(self._account_sid).fetch()

        start = time.perf_counter()
        try:
            loop = asyncio.get_running_loop()
            account = await loop.run_in_executor(None, _ping)
            latency_ms = int((time.perf_counter() - start) * 1000)
            return {
                "ok": True,
                "latency_ms": latency_ms,
                "account_status": getattr(account, "status", None),
                "friendly_name": getattr(account, "friendly_name", None),
            }
        except Exception as exc:
            latency_ms = int((time.perf_counter() - start) * 1000)
            # Twilio raises TwilioRestException with .status and .msg
            err = str(exc)
            status_code = getattr(exc, "status", None)
            return {
                "ok": False,
                "latency_ms": latency_ms,
                "error": err,
                "status_code": status_code,
            }

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "twilio"

    @property
    def provider_type(self) -> TelephonyProvider:
        return TelephonyProvider.TWILIO
