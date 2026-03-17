"""
Telephony Provider Adapter Interface.

High-level abstraction for ANY telephony provider — cloud-based (Vonage, Twilio)
or self-hosted SIP (Asterisk/FreeSWITCH via the CallControlAdapter stack).

This sits *above* ``CallControlAdapter`` (which is PBX-specific: ESL, ARI).
Cloud providers use entirely different call models (webhooks + NCCO/TwiML),
so they cannot share the same interface.

Implementing classes:
  - SIPProviderAdapter       (wraps CallControlAdapterFactory)
  - VonageProviderAdapter    (wraps Vonage Voice API SDK)
  - TwilioProviderAdapter    (future — wraps Twilio Programmable Voice)

The ``TelephonyProviderFactory`` selects the concrete adapter at runtime based
on ``providers.yaml → telephony.active`` (or per-tenant config in the future).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from app.domain.models.voice_contract import TelephonyProvider


class TelephonyProviderAdapter(ABC):
    """
    Abstract interface for a telephony provider.

    Every provider must implement:
    - Call origination (outbound dialer)
    - Call teardown
    - Call transfer
    - Audio configuration introspection
    - Health checking (credential / connectivity validation)
    """

    # ------------------------------------------------------------------
    # Call lifecycle
    # ------------------------------------------------------------------

    @abstractmethod
    async def originate_call(
        self,
        destination: str,
        caller_id: str,
        webhook_base_url: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Originate an outbound call to *destination*.

        Parameters
        ----------
        destination:
            Phone number or SIP URI to call.
        caller_id:
            Caller ID / from-number to present.
        webhook_base_url:
            Base URL for event/answer webhooks (e.g. ``https://api.talky.ai``).
            Cloud providers use this to construct callback URLs.
            SIP providers may ignore it.
        metadata:
            Optional per-call metadata (campaign_id, lead_id, etc.)

        Returns
        -------
        str
            Provider-assigned call identifier (UUID, Vonage conversation_uuid, etc.)
        """

    @abstractmethod
    async def hangup(self, call_id: str) -> None:
        """Terminate the call identified by *call_id*."""

    @abstractmethod
    async def transfer(
        self,
        call_id: str,
        destination: str,
        mode: str = "blind",
    ) -> Dict[str, Any]:
        """
        Transfer *call_id* to *destination*.

        Parameters
        ----------
        mode:
            "blind", "attended", or "deflect" (provider support varies).

        Returns
        -------
        dict
            At minimum ``{"status": str, "attempt_id": str}``.
        """

    # ------------------------------------------------------------------
    # Audio configuration
    # ------------------------------------------------------------------

    @abstractmethod
    async def get_audio_config(self) -> Dict[str, Any]:
        """
        Describe how this provider delivers/receives audio.

        Returns
        -------
        dict with at least:
            type         : "websocket" | "http_callback"
            sample_rate  : int  (e.g. 8000, 16000)
            encoding     : str  (e.g. "linear16", "audio/l16;rate=16000", "pcmu")
            channels     : int  (typically 1)
        """

    # ------------------------------------------------------------------
    # Health / status
    # ------------------------------------------------------------------

    @abstractmethod
    async def health_check(self) -> bool:
        """
        Return True if the provider is reachable and credentials are valid.

        For SIP: probes the PBX (Asterisk ARI / FreeSWITCH ESL).
        For Vonage: validates API key against the account endpoint.
        For Twilio: validates account SID / auth token.
        """

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name, e.g. ``"sip"``, ``"vonage"``, ``"twilio"``."""

    @property
    @abstractmethod
    def provider_type(self) -> TelephonyProvider:
        """The canonical ``TelephonyProvider`` enum value."""
