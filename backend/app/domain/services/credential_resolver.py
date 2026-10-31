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
- The resolver keeps a small per-process cache of resolved values, each
  with a short TTL (`_FOUND_TTL_S` for a tenant key, `_ABSENT_TTL_S` for
  "no BYOK row"). The cache is per process, so a key added, rotated or
  revoked through another process only takes effect once the TTL lapses;
  `invalidate_cache()` drops entries early within the calling process.
  Lookup/decrypt FAILURES are never cached - only a clean "found" or a clean
  "no active row".
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.db_utils import acquire_with_tenant
from app.infrastructure.providers.key_pool import POOLED_ENV_VARS, platform_key_rotation

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedCredential:
    """Internal credential plus provenance; the public resolve() stays str-only."""
    secret: str = field(repr=False)
    source: str = "unattributed"
    tenant_id: str | None = None
    credential_id: str | None = None

    @property
    def observation_context(self) -> dict[str, str | None]:
        return {"source": self.source, "tenant_id": self.tenant_id,
                "credential_id": self.credential_id}


def _tenant_material(value, tenant_id: str) -> ResolvedCredential | None:
    if isinstance(value, ResolvedCredential):
        return value
    # Compatibility for existing resolver ports returning just the key. No
    # retained row identity is inferred from a plaintext string.
    return ResolvedCredential(value, tenant_id=tenant_id) if value else None


def _env_material(provider: str, env_var: str | None, tenant_id: str | None) -> ResolvedCredential | None:
    value = resolve_sync_env_only(provider, env_var=env_var)
    return ResolvedCredential(value, source="platform", tenant_id=tenant_id) if value else None

# Cache lifetimes. The cache is per process, so these bound how long another
# process (or a rotation/revocation) can be out of date.
_FOUND_TTL_S = 300.0
_ABSENT_TTL_S = 45.0

# _resolve_tenant outcomes: a transient failure must be distinguishable from
# "the tenant has no BYOK row" so it is never cached as the latter.
_FOUND = "found"
_ABSENT = "absent"
_ERROR = "error"


# Map from the short provider name used internally to the env var name
# holding the single-tenant default. Keep in sync with .env.example.
_ENV_VAR_BY_PROVIDER: dict[str, str] = {
    "groq": "GROQ_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "deepgram": "DEEPGRAM_API_KEY",
    "assemblyai": "ASSEMBLYAI_API_KEY",
    "cartesia": "CARTESIA_API_KEY",
    "elevenlabs": "ELEVENLABS_API_KEY",
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "xai": "XAI_API_KEY",
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
    if name in POOLED_ENV_VARS:
        # <NAME> plus the <NAME>S pool, rotated per session (2026-10-19).
        return platform_key_rotation(name).next_key()
    value = os.getenv(name)
    return value.strip() if value else None


class CredentialResolver:
    """Async resolver that consults `tenant_ai_credentials` first,
    env second. Safe to construct per-request; the underlying DB pool
    handles pooling."""

    # Short-TTL cache so we don't pay a DB roundtrip on every voice-session
    # creation. Most deploys have an empty tenant_ai_credentials table, so the
    # first resolve per (tenant, provider) stores an "absent" marker here and
    # every resolve within the TTL is an in-memory dict hit - restoring the
    # demo-ready latency profile where keys came from os.getenv() directly.
    # Values are (payload, expires_at_monotonic); payload is the plaintext key
    # or _SENTINEL_USE_ENV.
    _SENTINEL_USE_ENV = object()
    _CACHE: dict[tuple[Any, str, str, str], tuple[Any, float]] = {}

    def __init__(
        self,
        db_pool: Any = None,
        encryption_service: Any = None,
    ):
        self._db_pool = db_pool
        self._encryption = encryption_service
        # Keep the pool object itself in the process-lifetime key.  A numeric
        # id can be reused after a pool is disposed, which could otherwise
        # hand the replacement pool a previous tenant's cached credential.
        self._cache_scope = db_pool

    async def resolve(
        self,
        provider: str,
        *,
        tenant_id: Optional[str] = None,
        credential_kind: str = "api_key",
        env_var: Optional[str] = None,
    ) -> Optional[str]:
        """Return the plaintext credential; selection and cache semantics stay shared."""
        material = await self.resolve_with_metadata(
            provider, tenant_id=tenant_id, credential_kind=credential_kind, env_var=env_var,
        )
        return material.secret if material is not None else None

    async def resolve_with_metadata(
        self, provider: str, *, tenant_id: Optional[str] = None,
        credential_kind: str = "api_key", env_var: Optional[str] = None,
    ) -> Optional[ResolvedCredential]:
        """Return the same selected credential with its retained internal identity.

        Return the plaintext credential for `provider` under
        `tenant_id`, or the env-var fallback, or None.

        Never raises. On any tenant-lookup failure the method logs and
        returns the env fallback.
        """
        if tenant_id:
            if provider.strip().lower() == "assemblyai":
                # AI Options may inspect availability before a tenant adds its
                # key. Re-read on each session/catalog request so additions,
                # rotations and revocations reach every worker without a
                # process-local cache invalidation signal.
                # _resolve_tenant returns (outcome, value): use the tenant key only
                # when one was found, else the platform env key (never the tuple).
                outcome, tenant_value = await self._resolve_tenant(
                    provider=provider,
                    tenant_id=tenant_id,
                    credential_kind=credential_kind,
                )
                if outcome == _FOUND and tenant_value:
                    return _tenant_material(tenant_value, tenant_id)
                return _env_material(provider, env_var, tenant_id)

            cache_key = (
                self._cache_scope,
                tenant_id,
                provider.strip().lower(),
                credential_kind,
            )
            entry = CredentialResolver._CACHE.get(cache_key)
            if entry is not None:
                payload, expires_at = entry
                if time.monotonic() < expires_at:
                    if payload is CredentialResolver._SENTINEL_USE_ENV:
                        return _env_material(provider, env_var, tenant_id)
                    return _tenant_material(payload, tenant_id)
                CredentialResolver._CACHE.pop(cache_key, None)

            outcome, tenant_value = await self._resolve_tenant(
                provider=provider,
                tenant_id=tenant_id,
                credential_kind=credential_kind,
            )
            if outcome == _FOUND:
                CredentialResolver._CACHE[cache_key] = (
                    tenant_value,
                    time.monotonic() + _FOUND_TTL_S,
                )
                return _tenant_material(tenant_value, tenant_id)
            if outcome == _ABSENT:
                CredentialResolver._CACHE[cache_key] = (
                    CredentialResolver._SENTINEL_USE_ENV,
                    time.monotonic() + _ABSENT_TTL_S,
                )
            # _ERROR: fall back to env for THIS call only; the next resolve
            # retries the DB instead of pinning the tenant to the platform key.

        return _env_material(provider, env_var, tenant_id)

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
            _, t_id, p, _ = key
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
    ) -> tuple[str, Optional[ResolvedCredential]]:
        """Returns (outcome, material): _FOUND with the key, _ABSENT when the
        tenant has no usable BYOK row, or _ERROR on a lookup/decrypt failure."""
        if self._db_pool is None:
            return _ABSENT, None

        try:
            # `tenant_ai_credentials` is RLS-protected and FORCEd (migration
            # 0013). A bare acquire() carries no tenant GUC, so the policy
            # matches zero rows and the tenant's own BYOK key silently
            # becomes the platform key. Scope the read to this tenant.
            async with acquire_with_tenant(self._db_pool, tenant_id) as conn:
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
            return _ERROR, None

        if not row:
            # The normal no-BYOK case: the call runs on the platform key. A
            # real read failure raises above (lookup_failed) or fails to decrypt
            # below, both at WARNING/ERROR, so this stays quiet. INFO keeps the
            # platform-key substitution auditable. Providers using the cache log
            # once per (tenant, provider); AssemblyAI deliberately re-reads every
            # time. Names the tenant and provider only - never key material.
            logger.info(
                "tenant_credential_none_configured tenant=%s provider=%s kind=%s "
                "- using the platform key",
                tenant_id, provider, credential_kind,
            )
            return _ABSENT, None

        try:
            encryption = self._encryption or self._lazy_encryption()
            plaintext = encryption.decrypt(row["encrypted_key"])
        except Exception as exc:
            logger.error(
                "tenant_credential_decrypt_failed tenant=%s provider=%s id=%s err=%s "
                "— falling back to env",
                tenant_id, provider, row["id"], exc,
            )
            return _ERROR, None

        # Fire-and-forget touch of last_used_at — pooled, non-blocking.
        # Same RLS scoping as the read above: without the tenant GUC the
        # UPDATE matches zero rows and reports success.
        try:
            async with acquire_with_tenant(self._db_pool, tenant_id) as conn:
                await conn.execute(
                    "UPDATE tenant_ai_credentials SET last_used_at = NOW() WHERE id = $1",
                    row["id"],
                )
        except Exception:
            # Usage tracking is best-effort; never fail origination on it.
            pass

        value = plaintext.strip() if plaintext else None
        if not value:
            return _ABSENT, None
        return _FOUND, ResolvedCredential(value, source="tenant", tenant_id=tenant_id,
                                          credential_id=str(row["id"]))

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
