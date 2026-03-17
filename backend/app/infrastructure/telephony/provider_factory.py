"""
Telephony Provider Factory — config-driven provider selection.

Reads ``providers.yaml → telephony.active`` to decide which provider to use.
In the future this will be per-tenant from the database.

Usage
-----
    provider = await TelephonyProviderFactory.create()       # uses config
    provider = await TelephonyProviderFactory.create("vonage")  # explicit

Supported provider types:
    "sip"       — self-hosted PBX stack (Asterisk / FreeSWITCH)
    "vonage"    — Vonage Voice API (cloud)
    "auto"      — checks available credentials: SIP first, then Vonage
"""
from __future__ import annotations

import logging
import os

from app.domain.interfaces.telephony_provider_adapter import TelephonyProviderAdapter

logger = logging.getLogger(__name__)


class TelephonyProviderFactory:
    """
    Creates a ``TelephonyProviderAdapter`` based on the active config.

    All provider classes are imported lazily to avoid circular imports
    and to keep the factory usable in test contexts.
    """

    @classmethod
    async def create(
        cls,
        provider_type: str | None = None,
    ) -> TelephonyProviderAdapter:
        """
        Return a TelephonyProviderAdapter for the requested provider.

        Parameters
        ----------
        provider_type:
            "sip"     — SIPProviderAdapter (Asterisk / FreeSWITCH)
            "vonage"  — VonageProviderAdapter
            "auto"    — probe SIP first, then Vonage (default)
            None      — read from TELEPHONY_PROVIDER env or providers.yaml
        """
        effective = (
            provider_type
            or os.getenv("TELEPHONY_PROVIDER")
            or cls._read_config_active()
            or "auto"
        ).lower()

        if effective == "sip":
            return cls._make_sip()
        elif effective == "vonage":
            return cls._make_vonage()
        elif effective == "auto":
            return await cls._auto_detect()
        else:
            raise ValueError(
                f"Unknown telephony provider: {effective}. "
                f"Available: {cls.list_providers()}"
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @classmethod
    def _read_config_active(cls) -> str | None:
        """Read ``telephony.active`` from providers.yaml via ConfigManager."""
        try:
            from app.core.config import ConfigManager
            config = ConfigManager()
            return config.get("providers.telephony.active")
        except Exception:
            return None

    @classmethod
    def _make_sip(cls) -> TelephonyProviderAdapter:
        from app.infrastructure.telephony.sip_provider_adapter import SIPProviderAdapter
        return SIPProviderAdapter()

    @classmethod
    def _make_vonage(cls) -> TelephonyProviderAdapter:
        from app.infrastructure.telephony.vonage_provider_adapter import VonageProviderAdapter
        return VonageProviderAdapter()

    @classmethod
    async def _auto_detect(cls) -> TelephonyProviderAdapter:
        """
        Probe SIP stack first (it's the primary), then Vonage.
        """
        sip = cls._make_sip()
        try:
            if await sip.health_check():
                logger.info("TelephonyProviderFactory: SIP stack healthy → using SIPProviderAdapter")
                return sip
        except Exception as exc:
            logger.debug(f"SIP probe failed: {exc}")

        vonage = cls._make_vonage()
        try:
            if await vonage.health_check():
                logger.info("TelephonyProviderFactory: Vonage healthy → using VonageProviderAdapter")
                return vonage
        except Exception as exc:
            logger.debug(f"Vonage probe failed: {exc}")

        logger.warning(
            "TelephonyProviderFactory: no provider available. "
            "Falling back to SIPProviderAdapter (will fail on first call)."
        )
        return sip

    @classmethod
    def list_providers(cls) -> list[str]:
        """Return the list of supported provider type strings."""
        return ["auto", "sip", "vonage"]
