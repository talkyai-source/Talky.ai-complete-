"""Traditional-model selection and bounded account-specific catalog evidence.

No provider request is made at call setup. Only a complete, fresh observation
for the exact tenant/provider/credential can mark a known model retired.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import logging
import math
import re
import time
from uuid import UUID

from app.domain.models.ai_config import traditional_llm_default, validate_traditional_llm_selection


logger = logging.getLogger(__name__)
_FRESH_SECONDS = 30 * 3600
_CACHE_TTL = 36 * 3600
_MAX_BYTES = 1024 * 1024
_MAX_MODELS = 5000
_MODEL_ID = re.compile(r"[A-Za-z0-9_.:/-]{1,128}\Z")
_URLS = {
    "groq": "https://api.groq.com/openai/v1/models",
    "cerebras": "https://api.cerebras.ai/v1/models",
    "openai": "https://api.openai.com/v1/models",
    "deepseek": "https://api.deepseek.com/models",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/models",
}


class CatalogUnavailable(RuntimeError):
    """Fixed safe reason; never a provider response, URL or credential."""


@dataclass(frozen=True)
class ModelSelection:
    model: str
    reason: str | None = None


def get_model_cache():
    # Reading an uninitialised container returns None; this does not connect.
    from app.core.container import get_container
    return get_container().redis


def _snapshot_key(tenant_id, provider, credential):
    if credential is None or not getattr(credential, "secret", None):
        return None
    scope = json.dumps([
        str(tenant_id) if tenant_id is not None else None, provider,
        getattr(credential, "source", None), getattr(credential, "credential_id", None),
        credential.secret,
    ], separators=(",", ":"))
    return "model-catalog:v1:" + hashlib.sha256(scope.encode()).hexdigest()


def _valid_models(models):
    return (isinstance(models, (list, tuple, set, frozenset)) and 0 < len(models) <= _MAX_MODELS
            and all(isinstance(value, str) and _MODEL_ID.fullmatch(value) for value in models))


async def store_snapshot(cache, *, tenant_id, provider, credential, models, checked_at):
    key = _snapshot_key(tenant_id, provider, credential)
    if key is None or cache is None or not _valid_models(models):
        raise CatalogUnavailable("catalog_snapshot_unavailable")
    if not isinstance(checked_at, (int, float)) or isinstance(checked_at, bool) or not math.isfinite(checked_at) or checked_at <= 0:
        raise CatalogUnavailable("catalog_snapshot_invalid_time")
    value = json.dumps({"version": 1, "checked_at": checked_at, "models": sorted(set(models))}, separators=(",", ":"))
    try:
        async with asyncio.timeout(1):
            await cache.set(key, value, ex=_CACHE_TTL)
    except Exception:
        raise CatalogUnavailable("catalog_snapshot_write_failed") from None


async def _cached_models(cache, *, tenant_id, provider, credential, now):
    key = _snapshot_key(tenant_id, provider, credential)
    if key is None or cache is None:
        return None
    try:
        async with asyncio.timeout(0.15):
            raw = await cache.get(key)
        if not isinstance(raw, (str, bytes)) or len(raw) > _MAX_BYTES:
            return None
        value = json.loads(raw)
        if not isinstance(value, dict) or value.get("version") != 1:
            return None
        checked = value.get("checked_at")
        if (not isinstance(checked, (int, float)) or isinstance(checked, bool)
                or not math.isfinite(checked) or not -60 <= now - checked <= _FRESH_SECONDS):
            return None
        models = value.get("models")
        return frozenset(models) if _valid_models(models) else None
    except Exception:
        # Cache outages and malformed data do not establish model retirement.
        return None


async def resolve_call_model(*, provider, model, tenant_id, credential, cache=None, now=None):
    provider = getattr(provider, "value", provider)
    default = traditional_llm_default(provider)  # Unknown provider stays an error.
    try:
        validate_traditional_llm_selection(provider, model)
    except ValueError:
        return ModelSelection(default, "unknown_model")
    available = await _cached_models(cache, tenant_id=tenant_id, provider=provider,
                                     credential=credential, now=time.time() if now is None else now)
    if available is not None and model not in available:
        return ModelSelection(default, "retired_model")
    return ModelSelection(model)


async def record_model_fallback(pool, *, tenant_id, provider, requested_model, selection):
    """Attempt a durable receipt, bounded to one second; never abort a call.

    Cancellation is intentionally not caught. An unavailable DB cannot supply
    a receipt; a separate safe error makes that loss visible to monitoring.
    """
    try:
        safe_tenant = str(UUID(str(tenant_id))) if tenant_id is not None else None
    except (ValueError, TypeError, AttributeError):
        safe_tenant = None
    logger.error("llm_model_fallback tenant=%s provider=%s selected_model=%s reason=%s",
                 safe_tenant, provider, selection.model, selection.reason)
    if pool is None:
        logger.error("llm_model_fallback_audit_unavailable tenant=%s reason=database_unavailable", safe_tenant)
        return
    try:
        from app.core.db_utils import acquire_with_tenant
        from app.domain.services.audit_logger import AuditEvent, AuditLogger
        async with asyncio.timeout(1):
            audit = AuditLogger(pool)
            async with acquire_with_tenant(pool, safe_tenant, timeout=0.5) as conn:
                await audit.lock_transaction(conn)
                requested = str(requested_model)
                await audit.log_in_transaction(
                    conn, event_type=AuditEvent.CONFIG_CHANGED, action="ai.model.runtime_fallback",
                    actor_type="system", tenant_id=safe_tenant,
                    resource_type="tenant_ai_config", resource_id=safe_tenant,
                    before_state={"model_sha256": hashlib.sha256(requested.encode("utf-8", errors="replace")).hexdigest(),
                                  "model_length": len(requested)},
                    after_state={"provider": provider, "model": selection.model},
                    metadata={"reason": selection.reason, "saved_profile_changed": False},
                )
    except Exception as exc:
        logger.error("llm_model_fallback_audit_unavailable tenant=%s error_type=%s", safe_tenant, type(exc).__name__)


async def _read_page(client, url, *, headers, params):
    # Stream to cap decompressed bytes before JSON allocation; no redirects or
    # provider-controlled URLs. Read-timeout plus outer deadline bounds trickles.
    async with client.stream("GET", url, headers=headers, params=params, timeout=5,
                             follow_redirects=False) as response:
        if response.status_code != 200:
            raise CatalogUnavailable("provider_http_error")
        body = bytearray()
        async for chunk in response.aiter_bytes():
            if len(body) + len(chunk) > _MAX_BYTES:
                raise CatalogUnavailable("catalog_response_too_large")
            body.extend(chunk)
        try:
            value = json.loads(body)
        except (ValueError, UnicodeError):
            raise CatalogUnavailable("catalog_response_invalid") from None
        if not isinstance(value, dict):
            raise CatalogUnavailable("catalog_response_invalid")
        return value


async def fetch_provider_models(client, *, provider, credential):
    """A complete bounded catalog for one credential, or a safe unknown error."""
    if provider not in _URLS or credential is None or not getattr(credential, "secret", None):
        raise CatalogUnavailable("provider_credential_unavailable")
    headers = ({"x-goog-api-key": credential.secret} if provider == "gemini"
               else {"Authorization": f"Bearer {credential.secret}"})
    models, tokens = set(), set()
    params = {"pageSize": 1000} if provider == "gemini" else None
    try:
        async with asyncio.timeout(10):
            for _page in range(10):
                data = await _read_page(client, _URLS[provider], headers=headers, params=params)
                rows = data.get("models" if provider == "gemini" else "data")
                if not isinstance(rows, list) or len(rows) > _MAX_MODELS:
                    raise CatalogUnavailable("catalog_response_invalid")
                for row in rows:
                    if not isinstance(row, dict):
                        raise CatalogUnavailable("catalog_response_invalid")
                    model = row.get("name" if provider == "gemini" else "id")
                    if provider == "gemini":
                        if not isinstance(model, str) or not model.startswith("models/"):
                            raise CatalogUnavailable("catalog_response_invalid")
                        model = model.removeprefix("models/")
                        methods = row.get("supportedGenerationMethods")
                        if not isinstance(methods, list) or not all(isinstance(method, str) for method in methods):
                            raise CatalogUnavailable("catalog_response_invalid")
                        if "generateContent" not in methods:
                            continue
                    if not isinstance(model, str) or not _MODEL_ID.fullmatch(model):
                        raise CatalogUnavailable("catalog_response_invalid")
                    models.add(model)
                    if len(models) > _MAX_MODELS:
                        raise CatalogUnavailable("catalog_model_limit")
                token = data.get("nextPageToken")
                if provider != "gemini" and any(data.get(key) for key in ("has_more", "next", "next_page", "nextPageToken")):
                    raise CatalogUnavailable("catalog_incomplete")
                if not token:
                    if not models:
                        raise CatalogUnavailable("catalog_empty")
                    return frozenset(models)
                if not isinstance(token, str) or len(token) > 512 or token in tokens:
                    raise CatalogUnavailable("catalog_pagination_invalid")
                tokens.add(token)
                params = {"pageSize": 1000, "pageToken": token}
            raise CatalogUnavailable("catalog_page_limit")
    except CatalogUnavailable:
        raise
    except Exception:
        raise CatalogUnavailable("catalog_request_failed") from None
