#!/usr/bin/env python3
"""Compare saved cascaded profiles with credential-scoped provider catalogs.

Never changes a saved profile. DATABASE_URL comes from the process environment;
existing credential resolution/settings may load application configuration.
Exit 0 = complete/healthy, 2 = model/coverage problem, 1 = startup failure.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from uuid import UUID

import asyncpg
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db_utils import acquire_with_tenant
from app.domain.models.ai_config import traditional_llm_default
from app.domain.services.credential_resolver import CredentialResolver, ResolvedCredential, env_var_for_provider
from app.domain.services.model_availability import CatalogUnavailable, fetch_provider_models, store_snapshot
from app.infrastructure.providers.key_pool import POOLED_ENV_VARS, platform_key_rotation


_TENANTS = "SELECT id FROM tenants WHERE ($1::uuid IS NULL OR id > $1::uuid) ORDER BY id LIMIT $2"
_PROFILE = "SELECT tenant_id,llm_provider,llm_model,pipeline_mode FROM tenant_ai_configs WHERE tenant_id = $1::uuid"


def _reference(tenant, provider, model):
    # A corrupt/attacker-controlled saved model is not trusted log content.
    value = str(model)
    return {"tenant_id": tenant, "provider": provider,
            "model_sha256": hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()}


def _profile_credentials(provider, material):
    """Enumerate the resolver's real platform pools without rotating them.

    BYOK never broadens into platform accounts. The configured list parser
    deduplicates single/CSV keys; unlike next_key(), keys() changes no cursor
    or bench state. Only configured pooled providers use this extension.
    """
    if material is None or not getattr(material, "secret", None):
        return [], None
    env_var = env_var_for_provider(provider)
    if material.source != "platform" or env_var not in POOLED_ENV_VARS:
        return [material], None
    keys = platform_key_rotation(env_var).keys()
    if not keys:
        # Configuration changed after resolution; the selected credential is
        # still concrete, but do not certify an empty configured pool.
        return [material], "credential_pool_changed"
    return [ResolvedCredential(key, source=material.source, tenant_id=material.tenant_id,
                               credential_id=material.credential_id) for key in keys[:16]], (
                                   "credential_limit_reached" if len(keys) > 16 else None)


async def audit_saved_models(pool, *, resolver, cache, client, now=None, page_size=100,
                             max_tenants=1000, max_seconds=300):
    if not 1 <= page_size <= 100 or not 1 <= max_tenants <= 10000 or not 1 <= max_seconds <= 600:
        raise ValueError("Model report bounds are outside the supported range")
    result = {"complete": True, "checked": 0, "credentials_checked": 0, "skipped_realtime": 0,
              "retired": [], "default_unavailable": [], "unknown": [],
              "issues": [], "saved_profiles_changed": False}
    cursor, visited = None, 0
    try:
        async with asyncio.timeout(max_seconds):
            while True:
                # System directory discovery only. Profile reads below always
                # have an explicit tenant predicate and tenant transaction.
                async with acquire_with_tenant(pool, None) as conn:
                    tenants = await conn.fetch(_TENANTS, cursor, page_size)
                if not tenants:
                    break
                for entry in tenants:
                    tenant_uuid = UUID(str(entry["id"]))
                    if cursor is not None and tenant_uuid <= cursor:
                        raise RuntimeError("Tenant pagination did not advance")
                    if visited >= max_tenants:
                        result["complete"] = False
                        result["issues"].append("tenant_limit_reached")
                        return result
                    cursor, visited = tenant_uuid, visited + 1
                    tenant = str(tenant_uuid)
                    try:
                        async with acquire_with_tenant(pool, tenant) as conn:
                            row = await conn.fetchrow(_PROFILE, tenant_uuid)
                    except Exception:
                        result["complete"] = False
                        result["unknown"].append({"tenant_id": tenant, "reason": "profile_read_failed"})
                        continue
                    if row is None:
                        continue
                    if str(row["tenant_id"]) != tenant:
                        raise RuntimeError("Profile ownership did not match")
                    if row["pipeline_mode"] == "realtime":
                        result["skipped_realtime"] += 1
                        continue
                    provider = str(row["llm_provider"])
                    model = row["llm_model"]
                    result["checked"] += 1
                    try:
                        default = traditional_llm_default(provider)
                    except ValueError:
                        result["complete"] = False
                        result["unknown"].append({"tenant_id": tenant, "reason": "unknown_provider"})
                        continue
                    reference = _reference(tenant, provider, model)
                    try:
                        # Resolution and provider I/O hold no profile DB lease.
                        async with asyncio.timeout(15):
                            material = await resolver.resolve_with_metadata(provider, tenant_id=tenant)
                        materials, pool_issue = _profile_credentials(provider, material)
                    except Exception:
                        result["complete"] = False
                        result["unknown"].append({**reference, "reason": "credential_resolution_failed"})
                        continue
                    if not materials:
                        result["complete"] = False
                        result["unknown"].append({**reference, "reason": "provider_credential_unavailable"})
                    if pool_issue:
                        result["complete"] = False
                        result["unknown"].append({**reference, "reason": pool_issue})
                    for material in materials:
                        scoped = {**reference, "credential_fingerprint": hashlib.sha256(material.secret.encode()).hexdigest()}
                        result["credentials_checked"] += 1
                        try:
                            models = await fetch_provider_models(client, provider=provider, credential=material)
                        except Exception as exc:
                            result["complete"] = False
                            reason = str(exc) if isinstance(exc, CatalogUnavailable) else "catalog_unavailable"
                            result["unknown"].append({**scoped, "reason": reason})
                            continue
                        try:
                            await store_snapshot(cache, tenant_id=tenant, provider=provider, credential=material,
                                                 models=models, checked_at=time.time() if now is None else now)
                        except CatalogUnavailable:
                            result["complete"] = False
                            result["unknown"].append({**scoped, "reason": "snapshot_write_failed"})
                        if model not in models:
                            result["retired"].append({**scoped, "default_model": default})
                        if default not in models:
                            result["default_unavailable"].append({**scoped, "default_model": default})
    except TimeoutError:
        result["complete"] = False
        result["issues"].append("report_deadline_reached")
    except Exception:
        result["complete"] = False
        result["issues"].append("profile_scan_failed")
    return result


def report_exit_code(result):
    return 0 if (result["complete"] and not result["retired"]
                 and not result["default_unavailable"] and not result["unknown"]) else 2


async def _redis_client():
    # Reuse deployed connection topology/URL parsing without starting any
    # queue, provider, voice worker, pubsub listener or application service.
    from app.core.container import _build_cluster_client, _build_redis_url, _build_sentinel_client
    import redis.asyncio as redis
    mode = (os.getenv("REDIS_MODE") or "single").lower()
    if mode == "sentinel":
        return await _build_sentinel_client()
    if mode == "cluster":
        return await _build_cluster_client()
    if mode != "single":
        raise ValueError("Unsupported Redis mode")
    return redis.from_url(_build_redis_url(), decode_responses=True,
                          socket_timeout=2, socket_connect_timeout=2, max_connections=2)


async def run(args):
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise ValueError("DATABASE_URL is required in the process environment")
    pool, cache = None, None
    try:
        pool = await asyncpg.create_pool(
            dsn.replace("postgresql+asyncpg://", "postgresql://", 1),
            min_size=1, max_size=2, timeout=5, command_timeout=10, statement_cache_size=0,
        )
        async with asyncio.timeout(5):
            cache = await _redis_client()
            await cache.ping()
        async with httpx.AsyncClient(follow_redirects=False, timeout=5) as client:
            report = await audit_saved_models(
                pool, resolver=CredentialResolver(db_pool=pool), cache=cache, client=client,
                max_tenants=args.max_tenants, max_seconds=args.max_seconds,
            )
        print(json.dumps(report, sort_keys=True), flush=True)
        return report_exit_code(report)
    finally:
        if cache is not None:
            try:
                async with asyncio.timeout(5):
                    await cache.aclose()
            except Exception:
                pass
        if pool is not None:
            try:
                async with asyncio.timeout(5):
                    await pool.close()
            except Exception:
                pool.terminate()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-tenants", type=int, default=1000)
    parser.add_argument("--max-seconds", type=int, default=300)
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except Exception as exc:
        print(f"Model catalog report unavailable ({type(exc).__name__}); no saved profile was changed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
