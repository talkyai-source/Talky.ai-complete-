"""
SIP Provider Adapter — wraps the existing CallControlAdapter stack.

Makes the self-hosted Asterisk/FreeSWITCH PBX stack conform to the
``TelephonyProviderAdapter`` interface so it can be selected at runtime
alongside cloud providers (Vonage, Twilio).

Internally delegates to ``CallControlAdapterFactory`` for auto-detection
and to the concrete ``CallControlAdapter`` (AsteriskAdapter / FreeSwitchAdapter)
for call operations.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.domain.interfaces.telephony_provider_adapter import TelephonyProviderAdapter
from app.domain.models.voice_contract import TelephonyProvider

logger = logging.getLogger(__name__)


class SIPProviderAdapter(TelephonyProviderAdapter):
    """
    TelephonyProviderAdapter implementation for the native SIP stack.

    Wraps ``CallControlAdapterFactory`` → ``CallControlAdapter``
    (Asterisk ARI or FreeSWITCH ESL, auto-detected at connect time).
    """

    def __init__(self) -> None:
        self._adapter = None  # Lazily created CallControlAdapter

    async def _ensure_adapter(self) -> None:
        """Create and connect the PBX adapter if not already done."""
        if self._adapter is not None:
            return
        from app.infrastructure.telephony.adapter_factory import CallControlAdapterFactory
        self._adapter = await CallControlAdapterFactory.create(connect=True)
        logger.info("SIPProviderAdapter: connected via %s", self._adapter.name)

    async def _ensure_adapter_no_connect(self) -> None:
        """Create the PBX adapter without connecting (for health checks)."""
        if self._adapter is not None:
            return
        from app.infrastructure.telephony.adapter_factory import CallControlAdapterFactory
        self._adapter = await CallControlAdapterFactory.create(connect=False)

    # ------------------------------------------------------------------
    # TelephonyProviderAdapter — call lifecycle
    # ------------------------------------------------------------------

    async def originate_call(
        self,
        destination: str,
        caller_id: str,
        webhook_base_url: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        await self._ensure_adapter()
        return await self._adapter.originate_call(destination, caller_id)

    async def hangup(self, call_id: str) -> None:
        await self._ensure_adapter()
        await self._adapter.hangup(call_id)

    async def transfer(
        self,
        call_id: str,
        destination: str,
        mode: str = "blind",
    ) -> Dict[str, Any]:
        await self._ensure_adapter()
        return await self._adapter.transfer(call_id, destination, mode)

    # ------------------------------------------------------------------
    # Audio configuration
    # ------------------------------------------------------------------

    async def get_audio_config(self) -> Dict[str, Any]:
        await self._ensure_adapter()
        adapter_name = self._adapter.name
        if adapter_name == "asterisk":
            return {
                "type": "http_callback",
                "sample_rate": 8000,
                "encoding": "pcmu",
                "channels": 1,
            }
        return {
            "type": "websocket",
            "sample_rate": 8000,
            "encoding": "pcmu",
            "channels": 1,
        }

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        try:
            # Reuse the already-connected adapter when available so we avoid
            # spinning up a brand-new ESL connection on every health probe.
            if self._adapter is not None:
                return await self._adapter.health_check()
            # No adapter yet — do a lightweight probe via the factory (no connect).
            from app.infrastructure.telephony.adapter_factory import CallControlAdapterFactory
            probe = await CallControlAdapterFactory.create(connect=False)
            return await probe.health_check()
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        if self._adapter:
            return f"sip/{self._adapter.name}"
        return "sip"

    @property
    def provider_type(self) -> TelephonyProvider:
        return TelephonyProvider.SIP

    @property
    def pbx_adapter(self):
        """Expose the underlying CallControlAdapter for bridge-level operations."""
        return self._adapter
