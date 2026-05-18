"""
Telephony Provider Factory — config-driven and per-tenant provider selection.

The global ``create()`` method reads ``providers.yaml → telephony.active``
(or the ``TELEPHONY_PROVIDER`` env) and returns the platform-default adapter.
The per-tenant ``create_for_tenant(tenant_id, db_pool)`` method reads the
tenant's ``active_telephony_provider`` column and ``tenant_telephony_credentials``
row to instantiate an adapter pre-loaded with the tenant's own creds.

Usage
-----
    provider = await TelephonyProviderFactory.create()                  # platform default
    provider = await TelephonyProviderFactory.create("vonage")          # explicit
    provider = await TelephonyProviderFactory.create_for_tenant(tid, pool)  # per-tenant

Supported provider types:
    "sip"       — self-hosted PBX stack (Asterisk / FreeSWITCH)
    "vonage"    — Vonage Voice API (cloud)
    "twilio"    — Twilio Programmable Voice (cloud)
    "auto"      — checks available credentials: SIP first, then Vonage
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional
from uuid import UUID

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
        elif effective == "twilio":
            return cls._make_twilio_from_env()
        elif effective == "auto":
            return await cls._auto_detect()
        else:
            raise ValueError(
                f"Unknown telephony provider: {effective}. "
                f"Available: {cls.list_providers()}"
            )

    # ------------------------------------------------------------------
    # Per-tenant provider resolution
    # ------------------------------------------------------------------

    @classmethod
    async def create_for_tenant(
        cls,
        tenant_id: str,
        db_pool,
    ) -> TelephonyProviderAdapter:
        """
        Resolve the active provider for *tenant_id* and return an adapter
        loaded with that tenant's own credentials.

        Resolution order:
          1. ``tenants.active_telephony_provider`` → twilio | vonage | sip | none
          2. For ``twilio`` / ``vonage`` — fetch the matching
             ``tenant_telephony_credentials`` row and decrypt.
          3. For ``sip`` — fetch the active ``tenant_sip_trunks`` row.
          4. For ``none`` or any failure — fall back to ``create()`` so
             platform-managed tenants keep working.
        """
        if not tenant_id or db_pool is None:
            return await cls.create()

        try:
            tenant_uuid = UUID(str(tenant_id))
        except (ValueError, TypeError):
            return await cls.create()

        active = "none"
        try:
            async with db_pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute("SET LOCAL app.bypass_rls = 'true'")
                    row = await conn.fetchrow(
                        "SELECT active_telephony_provider FROM tenants WHERE id = $1",
                        tenant_uuid,
                    )
                    if row and row["active_telephony_provider"]:
                        active = row["active_telephony_provider"]
        except Exception as exc:
            logger.warning(
                "create_for_tenant: failed to read tenants.active_telephony_provider "
                "for %s (%s) — falling back to platform default",
                str(tenant_id)[:8], exc,
            )
            return await cls.create()

        if active in ("twilio", "vonage"):
            creds = await cls._load_cloud_credentials(tenant_uuid, active, db_pool)
            if not creds:
                logger.warning(
                    "Tenant %s has active_telephony_provider=%s but no credentials row; "
                    "falling back to platform default",
                    str(tenant_id)[:8], active,
                )
                return await cls.create()
            if active == "twilio":
                return cls._make_twilio(creds)
            return cls._make_vonage_with_creds(creds)

        if active == "sip":
            # Existing SIP adapter already discovers trunks from the DB; no
            # extra wiring needed here. Future: pass tenant_id into a
            # tenant-scoped variant.
            return cls._make_sip()

        # 'none' or unknown → platform default
        return await cls.create()

    @classmethod
    async def _load_cloud_credentials(
        cls,
        tenant_uuid: UUID,
        provider: str,
        db_pool,
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch and decrypt the tenant's credentials row for *provider*.

        Returns the merged dict ``{**decrypted_credentials, "from_number": ...}``
        or ``None`` if no row / decryption fails.
        """
        try:
            async with db_pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute("SET LOCAL app.bypass_rls = 'true'")
                    row = await conn.fetchrow(
                        """
                        SELECT credentials_encrypted, from_number
                        FROM tenant_telephony_credentials
                        WHERE tenant_id = $1 AND provider = $2
                        """,
                        tenant_uuid,
                        provider,
                    )
        except Exception as exc:
            logger.warning("Failed to fetch %s credentials: %s", provider, exc)
            return None

        if not row:
            return None

        try:
            from app.infrastructure.connectors.encryption import get_encryption_service
            svc = get_encryption_service()
            plaintext = svc.decrypt(row["credentials_encrypted"])
            decoded = json.loads(plaintext) if plaintext else {}
        except Exception as exc:
            logger.error("Failed to decrypt %s credentials: %s", provider, exc)
            return None

        decoded["from_number"] = row["from_number"] or decoded.get("from_number", "")
        return decoded

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
    def _make_vonage_with_creds(cls, creds: Dict[str, Any]) -> TelephonyProviderAdapter:
        from app.infrastructure.telephony.vonage_provider_adapter import VonageProviderAdapter
        return VonageProviderAdapter(
            api_key=creds.get("api_key"),
            api_secret=creds.get("api_secret"),
            app_id=creds.get("app_id") or creds.get("application_id"),
            private_key=creds.get("private_key"),
            from_number=creds.get("from_number"),
        )

    @classmethod
    def _make_twilio(cls, creds: Dict[str, Any]) -> TelephonyProviderAdapter:
        from app.infrastructure.telephony.twilio_provider_adapter import TwilioProviderAdapter
        return TwilioProviderAdapter(
            account_sid=creds.get("account_sid", ""),
            auth_token=creds.get("auth_token", ""),
            from_number=creds.get("from_number", ""),
        )

    @classmethod
    def _make_twilio_from_env(cls) -> TelephonyProviderAdapter:
        return cls._make_twilio({
            "account_sid": os.getenv("TWILIO_ACCOUNT_SID", ""),
            "auth_token": os.getenv("TWILIO_AUTH_TOKEN", ""),
            "from_number": os.getenv("TWILIO_FROM_NUMBER", ""),
        })

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
        return ["auto", "sip", "vonage", "twilio"]
