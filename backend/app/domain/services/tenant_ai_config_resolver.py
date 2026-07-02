"""Per-tenant AI provider config resolution (model / provider / pipeline).

Companion to :mod:`voice_tuning`, and built on exactly the same pattern.

**The defect this fixes.** The model/provider/temperature/max-tokens/STT-engine/
pipeline-mode selection for a live call used to be sourced from a *process-wide*
singleton (:func:`global_ai_config.get_global_config`). That singleton was
mutated on the request path (merely opening the AI-Options page called
``set_global_config``) and restored on boot from "whichever tenant saved last".
The result: tenant B viewing/saving their AI Options overwrote the model that
tenant A's *live call* was reading — cross-tenant model bleed. Per-tenant
*credentials* were already resolved correctly (via ``tenant_id`` →
CredentialResolver); only the model/provider/pipeline *selection* leaked.

**The fix.** Each call sources its provider selection from the tenant's own
persisted row in ``tenant_ai_configs`` (keyed by ``campaign.tenant_id`` for
outbound, by the dialed DID's tenant for inbound / bridges). This resolver is
the single place that lookup lives:

Resolution priority (highest first):

1. **Per-tenant DB row** — ``tenant_ai_configs`` for the given ``tenant_id``,
   wired via :meth:`TenantAIConfigResolver.set_db_lookup` at app startup.
   Cache-bypassed: every async lookup hits the DB so an AI-Options edit lands
   on the next call without a restart.
2. **Process/env default** — :func:`global_ai_config.get_global_config`, now an
   *immutable* code default (``AIProviderConfig()``). Used only for genuinely
   tenant-less paths (Ask AI, browser tests, campaign-less dev dials) and as a
   fail-soft fallback.

The resolver NEVER raises. A missing row, a broken DB lookup, or a ``None``
tenant all fall back to the process default — a per-tenant lookup must never
take a call offline.
"""
from __future__ import annotations

import logging
import threading
from typing import Awaitable, Callable, Optional

from app.domain.models.ai_config import AIProviderConfig

logger = logging.getLogger(__name__)


# A pluggable lookup that returns the persisted AIProviderConfig for a given
# tenant_id, or ``None`` when the tenant has no row. Wired at app startup;
# tests inject a mock or leave it unset to fall back to the process default.
DBLookup = Callable[[str], Awaitable[Optional[AIProviderConfig]]]


class TenantAIConfigResolver:
    """Resolves the per-tenant :class:`AIProviderConfig` used to build a call.

    Mirrors :class:`voice_tuning.VoiceTuningResolver`: an async DB lookup wired
    once at startup, cache-bypassed so UI edits take effect on the next call,
    and a hard fail-soft fallback to the process default.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._db_lookup: Optional[DBLookup] = None

    def set_db_lookup(self, lookup: Optional[DBLookup]) -> None:
        """Wire (or clear) the per-tenant DB lookup callback.

        Called once at app startup with a function that fetches the tenant's
        ``tenant_ai_configs`` row as an :class:`AIProviderConfig`. Pass ``None``
        to revert to process-default-only resolution (useful for tests that
        don't want a DB pool).
        """
        with self._lock:
            self._db_lookup = lookup

    def default(self) -> AIProviderConfig:
        """The immutable process/env default AIProviderConfig.

        Delegates to :func:`global_ai_config.get_global_config`, which now
        returns a plain ``AIProviderConfig()`` code default (no longer mutated
        per request). Used for tenant-less paths and as the fail-soft fallback.
        """
        from app.domain.services.global_ai_config import get_global_config
        return get_global_config()

    async def for_tenant_async(self, tenant_id: Optional[str]) -> AIProviderConfig:
        """Production resolution path: tenant DB row → process default.

        DB results are not cached — an operator editing AI Options expects the
        change to take effect on the very next call, not after a restart. The
        query is one indexed lookup on a small table.

        Falls back gracefully in every failure mode:

        * No DB lookup wired → process default.
        * ``tenant_id`` is ``None`` → process default (genuinely tenant-less
          path — Ask AI, browser test, campaign-less dev dial).
        * Lookup raises → log a warning, process default. A per-tenant lookup
          must NEVER block a call from going out.
        * Lookup returns ``None`` (no row) → process default (a tenant that has
          never saved AI Options still gets sane defaults — backward compatible).
        """
        lookup = self._db_lookup
        if lookup is None or not tenant_id:
            return self.default()

        try:
            config = await lookup(str(tenant_id))
        except Exception as exc:  # noqa: BLE001 — never block a call
            logger.warning(
                "tenant_ai_config_lookup_failed tenant=%s err=%s "
                "— falling back to process default",
                tenant_id, exc,
            )
            return self.default()

        if config is None:
            return self.default()
        return config


# ---------------------------------------------------------------------
# Process-level singleton
# ---------------------------------------------------------------------

_resolver: Optional[TenantAIConfigResolver] = None
_resolver_lock = threading.Lock()


def get_tenant_ai_config_resolver() -> TenantAIConfigResolver:
    """Return the process-wide tenant AI-config resolver singleton."""
    global _resolver
    if _resolver is None:
        with _resolver_lock:
            if _resolver is None:
                _resolver = TenantAIConfigResolver()
    return _resolver


def reset_tenant_ai_config_resolver() -> None:
    """Drop the singleton. Tests use this between cases that wire a mock
    lookup; production code should not call it during normal operation."""
    global _resolver
    with _resolver_lock:
        _resolver = None


async def resolve_ai_config_for_did(
    did: Optional[str],
) -> tuple[Optional[str], AIProviderConfig]:
    """Best-effort (tenant_id, AIProviderConfig) for an inbound DID.

    Used by the Twilio / Vonage bridges, which don't carry a campaign row: the
    dialed number (DID) identifies the tenant. Resolves the DID → tenant via
    :func:`inbound_router.resolve_inbound_route`, then loads that tenant's
    persisted config. Entirely fail-soft — an unknown/unroutable DID (or any
    error) yields ``(None, process_default)`` so the bridge still places the
    call, just on default provider selection.
    """
    import os

    tenant_id: Optional[str] = None
    if did:
        try:
            from app.core.container import get_container
            from app.domain.services.telephony.inbound_router import (
                resolve_inbound_route,
            )
            pool = getattr(get_container(), "db_pool", None)
            if pool is not None:
                route = await resolve_inbound_route(
                    pool,
                    called_did=did,
                    context=None,
                    environment=os.getenv("ENVIRONMENT", "development"),
                )
                if route.resolved and route.tenant_id:
                    tenant_id = str(route.tenant_id)
        except Exception as exc:  # noqa: BLE001 — never block a call
            logger.warning(
                "resolve_ai_config_for_did_failed did=%s err=%s "
                "— using process default",
                did, exc,
            )

    config = await get_tenant_ai_config_resolver().for_tenant_async(tenant_id)
    return tenant_id, config
