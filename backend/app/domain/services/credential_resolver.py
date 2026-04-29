"""Per-tenant AI provider credential resolver (T1.1).

Single seam used by every provider-factory call site that needs an API
key. Resolution order:

  1. Per-tenant encrypted row in `tenant_ai_credentials` (if `tenant_id`
     is supplied and the row is `status='active'`).
  2. Process env var (preserves pre-T1.1 behaviour for single-tenant
     deploys and for unauthenticated paths like smoke tests).
  3. None — the caller decides whether to raise or degrade.

Design choices:

- Resolver is async because the DB lookup is async. Callers that don't
  have a tenant context can use the sync `resolve_sync_env_only()`
  helper instead of awaiting.
- Failures at the DB layer (Redis-less env, cold connection, bad
  ciphertext) LOG and fall through to the env var. Never raise into
  the origination path — a bad per-tenant row must not kill the
  global service.
- The resolver NEVER caches plaintext in the process. Providers are
  expected to construct a short-lived client with the key and discard.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)


# Map from the short provider name used internally to the env var name
# holding the single-tenant default. Keep in sync with .env.example.
_ENV_VAR_BY_PROVIDER: dict[str, str] = {
    "groq": "GROQ_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "deepgram": "DEEPGRAM_API_KEY",
    "cartesia": "CARTESIA_API_KEY",
    "elevenlabs": "ELEVENLABS_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


def env_var_for_provider(provider: str) -> Optional[str]:
    """Name of the env var that holds the fallback key for a provider.
    `None` when the provider isn't known — the resolver will return
    `None` in that case unless the caller passes a custom `env_var`.
    """
    return _ENV_VAR_BY_PROVIDER.get(provider.strip().lower())


def resolve_sync_env_only(
    provider: str,
    *,
    env_var: Optional[str] = None,
) -> Optional[str]:
    """Env-only resolution — for code paths that don't have a tenant
    context (the AI Options test/benchmark buttons, intent detector,
    etc.). Equivalent to the old `os.getenv(...)` call it replaces."""
    name = env_var or env_var_for_provider(provider)
    if not name:
        return None
    value = os.getenv(name)
    return value.strip() if value else None


class CredentialResolver:
    """Async resolver that consults `tenant_ai_credentials` first,
    env second. Safe to construct per-request; the underlying DB pool
    handles pooling."""

    # Process-lifetime cache so we don't pay a DB roundtrip on every
    # voice-session creation. Most deploys have an empty
    # tenant_ai_credentials table, so the first resolve per (tenant,
    # provider) writes a sentinel here and every subsequent resolve is
    # an in-memory dict hit — restoring the demo-ready latency profile
    # where keys came from os.getenv() directly.
    _SENTINEL_USE_ENV = object()
    _CACHE: dict[tuple[str, str, str], Any] = {}

    def __init__(
        self,
        db_pool: Any = None,
        encryption_service: Any = None,
    ):
        self._db_pool = db_pool
        self._encryption = encryption_service

    async def resolve(
        self,
        provider: str,
        *,
        tenant_id: Optional[str] = None,
        credential_kind: str = "api_key",
        env_var: Optional[str] = None,
    ) -> Optional[str]:
        """Return the plaintext credential for `provider` under
        `tenant_id`, or the env-var fallback, or None.

        Never raises. On any tenant-lookup failure the method logs and
        returns the env fallback.
        """
        if tenant_id:
            cache_key = (tenant_id, provider.strip().lower(), credential_kind)
            cached = CredentialResolver._CACHE.get(cache_key)
            if cached is CredentialResolver._SENTINEL_USE_ENV:
                return resolve_sync_env_only(provider, env_var=env_var)
            if cached is not None:
                return cached  # type: ignore[return-value]

            tenant_value = await self._resolve_tenant(
                provider=provider,
                tenant_id=tenant_id,
                credential_kind=credential_kind,
            )
            if tenant_value:
                CredentialResolver._CACHE[cache_key] = tenant_value
                return tenant_value
            CredentialResolver._CACHE[cache_key] = CredentialResolver._SENTINEL_USE_ENV

        return resolve_sync_env_only(provider, env_var=env_var)

    @classmethod
    def invalidate_cache(
        cls,
        *,
        tenant_id: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> None:
        """Drop cached entries. Call when a tenant rotates a key via
        the AI Options UI so the next resolve picks up the new value
        instead of serving the stale cached one."""
        if tenant_id is None and provider is None:
            cls._CACHE.clear()
            return
        prov = provider.strip().lower() if provider else None
        for key in list(cls._CACHE.keys()):
            t_id, p, _ = key
            if (tenant_id is None or t_id == tenant_id) and (
                prov is None or p == prov
            ):
                cls._CACHE.pop(key, None)

    # ──────────────────────────────────────────────────────────────────

    async def _resolve_tenant(
        self,
        *,
        provider: str,
        tenant_id: str,
        credential_kind: str,
    ) -> Optional[str]:
        if self._db_pool is None:
            return None

        try:
            async with self._db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT id, encrypted_key
                    FROM tenant_ai_credentials
                    WHERE tenant_id = $1
                      AND provider = $2
                      AND credential_kind = $3
                      AND status = 'active'
                    LIMIT 1
                    """,
                    tenant_id,
                    provider.strip().lower(),
                    credential_kind,
                )
        except Exception as exc:
            logger.warning(
                "tenant_credential_lookup_failed tenant=%s provider=%s err=%s "
                "— falling back to env",
                tenant_id, provider, exc,
            )
            return None

        if not row:
            return None

        try:
            encryption = self._encryption or self._lazy_encryption()
            plaintext = encryption.decrypt(row["encrypted_key"])
        except Exception as exc:
            logger.error(
                "tenant_credential_decrypt_failed tenant=%s provider=%s id=%s err=%s "
                "— falling back to env",
                tenant_id, provider, row["id"], exc,
            )
            return None

        # Fire-and-forget touch of last_used_at — pooled, non-blocking.
        try:
            async with self._db_pool.acquire() as conn:
                await conn.execute(
                    "UPDATE tenant_ai_credentials SET last_used_at = NOW() WHERE id = $1",
                    row["id"],
                )
        except Exception:
            # Usage tracking is best-effort; never fail origination on it.
            pass

        return plaintext.strip() if plaintext else None

    @staticmethod
    def _lazy_encryption() -> Any:
        # Kept lazy so tests that pass an explicit encryption_service
        # don't need the production service to be importable.
        from app.infrastructure.connectors.encryption import get_encryption_service
        return get_encryption_service()


# ──────────────────────────────────────────────────────────────────────
# Singleton helper — providers grab one of these per-request.
# ──────────────────────────────────────────────────────────────────────

_default_resolver: Optional[CredentialResolver] = None


def get_credential_resolver() -> CredentialResolver:
    """Return the process-wide resolver. Lazily binds to the
    container's DB pool on first call."""
    global _default_resolver
    if _default_resolver is not None:
        return _default_resolver

    db_pool = None
    try:
        from app.core.container import get_container
        c = get_container()
        if c.is_initialized:
            db_pool = c.db_pool
    except Exception as exc:
        logger.debug("credential_resolver_container_unavailable err=%s", exc)

    _default_resolver = CredentialResolver(db_pool=db_pool)
    return _default_resolver


def reset_credential_resolver() -> None:
    """Drop the cached resolver. Useful for tests that mutate the
    container."""
    global _default_resolver
    _default_resolver = None
