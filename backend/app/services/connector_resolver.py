"""Generic active-connector resolver for tenant integrations.

One place that turns (tenant_id, connector_type) into a ready-to-use connector
instance with a fresh access token — the SAME canonical path the OAuth callback
writes to (``connectors`` + ``connector_accounts``) and that ``EmailService``
already uses for email. Assistant tools that READ from Gmail / Google Drive /
Calendar resolve through here so token handling (decrypt + expiry refresh +
write-back) lives in exactly one spot instead of being re-implemented per tool.

Only READ/util access needs this; the mutating email/meeting send paths keep
their existing dedicated services.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Tuple

import httpx

from app.infrastructure.connectors.base import (
    BaseConnector,
    ConnectorFactory,
    ConnectorProviderError,
)
from app.infrastructure.connectors.encryption import get_encryption_service

logger = logging.getLogger(__name__)

_TOKEN_REFRESH_SAFETY_SECONDS = 90


class ConnectorNotConnectedError(Exception):
    """No active connector of the requested type for this tenant."""

    def __init__(
        self,
        connector_type: str,
        message: str | None = None,
        *,
        connector_id: str | None = None,
        provider_confirmed: bool = False,
        reason: str | None = None,
    ):
        self.connector_type = connector_type
        self.connector_id = connector_id
        self.provider_confirmed = provider_confirmed
        self.reason = reason
        self.message = message or (
            f"No {connector_type} integration is connected. "
            f"Connect it from the Connectors page (left sidebar)."
        )
        super().__init__(self.message)


class ConnectorLookupError(Exception):
    """The connector lookup itself failed (DB/RLS/query error) — this is NOT the
    same as "not connected". Surfacing it distinctly stops a transient database
    error from telling the user to reconnect an integration that IS connected."""

    def __init__(self, connector_type: str, detail: str = ""):
        self.connector_type = connector_type
        self.message = (
            f"I couldn't check your {connector_type} connection just now "
            f"(a temporary lookup error). Please try again in a moment."
        )
        self.detail = detail
        super().__init__(self.message)


class _ConnectorTokenStoreError(Exception):
    """A refreshed token could not be durably written back."""


def _token_needs_refresh(expires_at: Any, *, force: bool = False) -> bool:
    """Refresh before expiry, treating malformed stored expiries as unsafe."""
    if force:
        return True
    if not expires_at:
        return False
    try:
        parsed = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            parsed = parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return True
    refresh_at = parsed - timedelta(seconds=_TOKEN_REFRESH_SAFETY_SECONDS)
    return datetime.now(timezone.utc) >= refresh_at


async def _refresh_and_store(
    db_client: Any,
    connector: BaseConnector,
    connector_id: str,
    account_id: str,
    tenant_id: str,
    refresh_token: str,
) -> str:
    """Refresh the OAuth token, persist the new tokens, return the access token."""
    enc = get_encryption_service()
    new_tokens = await connector.refresh_tokens(refresh_token)
    if not getattr(new_tokens, "access_token", None):
        raise ConnectorProviderError(
            provider=connector.provider_name,
            operation="refresh_tokens",
            category="authentication",
            message="The provider returned no access token.",
        )
    try:
        write = db_client.table("connector_accounts").update({
            "access_token_encrypted": enc.encrypt(new_tokens.access_token),
            "refresh_token_encrypted": enc.encrypt(new_tokens.refresh_token or refresh_token),
            "token_expires_at": new_tokens.expires_at.isoformat() if getattr(new_tokens, "expires_at", None) else None,
            "last_refreshed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", account_id).eq("connector_id", connector_id).eq(
            "tenant_id", tenant_id
        ).execute()
    except Exception as exc:
        logger.error("connector_resolver: token write-back raised for %s: %s", connector_id, exc)
        raise _ConnectorTokenStoreError(str(exc)) from exc
    if getattr(write, "error", None) or not getattr(write, "data", None):
        detail = str(getattr(write, "error", None) or "no matching account row")
        logger.error("connector_resolver: token write-back failed for %s: %s", connector_id, detail)
        raise _ConnectorTokenStoreError(detail)
    return new_tokens.access_token


async def resolve_active_connector(
    db_client: Any,
    tenant_id: str,
    connector_type: str,
    *,
    force_refresh: bool = False,
) -> Tuple[BaseConnector, str, str]:
    """Return ``(connector, connector_id, provider)`` for the tenant's active
    connector of ``connector_type`` ("email" | "drive" | "calendar" | ...),
    with a valid (refreshed if needed) access token installed.

    Raises ``ConnectorNotConnectedError`` when nothing is connected/usable.
    """
    resp = (
        db_client.table("connectors")
        .select("id, provider, status, created_at")
        .eq("tenant_id", tenant_id)
        .eq("type", connector_type)
        .eq("status", "active")
        .order("created_at", desc=True)  # newest-first, matching the UI's choice
        .execute()
    )
    # A DB/RLS/connectivity error must NOT masquerade as "not connected" — the
    # adapter swallows exceptions into resp.error with data=None (agent finding).
    if getattr(resp, "error", None):
        logger.error(
            "resolve_active_connector: connectors query error tenant=%s type=%s err=%s",
            str(tenant_id)[:8], connector_type, resp.error,
        )
        raise ConnectorLookupError(connector_type, str(resp.error))
    rows = resp.data or []
    logger.info(
        "resolve_active_connector tenant=%s type=%s active_connector_rows=%d",
        str(tenant_id)[:8], connector_type, len(rows),
    )
    if not rows:
        raise ConnectorNotConnectedError(connector_type)

    # Repeat "Connect" clicks can leave several active connector rows. The
    # newest connector is authoritative: falling back across connector IDs can
    # expose a different mailbox (for example, old personal Gmail after a work
    # Gmail reconnect). Validate it and fail visibly rather than crossing
    # account identity boundaries.
    enc = get_encryption_service()
    connector_id = None
    provider = None
    acc_data = None
    access_token = None
    refresh_token = None
    should_refresh = False
    first_failure_id = str(rows[0]["id"])
    first_failure_reason = "access_unusable"
    for row in rows[:1]:
        cid = str(row["id"])
        acc = (
            db_client.table("connector_accounts")
            .select("id, access_token_encrypted, refresh_token_encrypted, token_expires_at, last_refreshed_at")
            .eq("connector_id", cid)
            .eq("status", "active")
            .order("last_refreshed_at", desc=True)
            .limit(1)
            .execute()
        )
        if getattr(acc, "error", None):
            logger.error(
                "resolve_active_connector: connector_accounts query error cid=%s err=%s",
                cid, acc.error,
            )
            raise ConnectorLookupError(connector_type, str(acc.error))
        adata = acc.data
        account_rows = adata if isinstance(adata, list) else ([adata] if isinstance(adata, dict) else [])
        for arow in account_rows:
            try:
                candidate_access = enc.decrypt(arow["access_token_encrypted"])
                if not candidate_access:
                    raise ValueError("empty access token")
            except Exception as exc:
                logger.error(
                    "connector_resolver: skipping undecryptable access token connector=%s type=%s",
                    cid,
                    type(exc).__name__,
                )
                continue

            candidate_should_refresh = _token_needs_refresh(
                arow.get("token_expires_at"), force=force_refresh
            )
            candidate_refresh = None
            if candidate_should_refresh:
                encrypted_refresh = arow.get("refresh_token_encrypted")
                if not encrypted_refresh:
                    if cid == first_failure_id:
                        first_failure_reason = "refresh_unavailable"
                    continue
                try:
                    candidate_refresh = enc.decrypt(encrypted_refresh)
                    if not candidate_refresh:
                        raise ValueError("empty refresh token")
                except Exception as exc:
                    logger.error(
                        "connector_resolver: skipping undecryptable refresh token connector=%s type=%s",
                        cid,
                        type(exc).__name__,
                    )
                    if cid == first_failure_id:
                        first_failure_reason = "refresh_unavailable"
                    continue

            connector_id, provider, acc_data = cid, row["provider"], arow
            access_token = candidate_access
            refresh_token = candidate_refresh
            should_refresh = candidate_should_refresh
            break
        if acc_data is not None:
            break

    if acc_data is None:
        unavailable_message = (
            f"Your {connector_type} credentials cannot be refreshed. Please reconnect."
            if first_failure_reason == "refresh_unavailable"
            else f"Your {connector_type} connection needs to be reconnected."
        )
        raise ConnectorNotConnectedError(
            connector_type,
            unavailable_message,
            connector_id=first_failure_id,
            reason=first_failure_reason,
        )

    connector = ConnectorFactory.create(provider=provider, tenant_id=tenant_id, connector_id=connector_id)

    # Refresh slightly before expiry so the token cannot die during a provider
    # round trip.  ``force_refresh`` is used for one bounded retry after a 401.
    if should_refresh:
        try:
            access_token = await _refresh_and_store(
                db_client,
                connector,
                connector_id,
                str(acc_data["id"]),
                tenant_id,
                refresh_token,
            )
        except _ConnectorTokenStoreError as exc:
            raise ConnectorLookupError(connector_type, str(exc)) from exc
        except ConnectorProviderError as exc:
            logger.error(
                "connector_resolver: refresh failed for %s category=%s status=%s",
                connector_id,
                exc.category,
                exc.status_code,
            )
            if exc.category == "authentication":
                raise ConnectorNotConnectedError(
                    connector_type,
                    f"Your {connector_type} authorization expired. Please reconnect.",
                    connector_id=connector_id,
                    provider_confirmed=True,
                ) from exc
            raise
        except (httpx.TimeoutException, httpx.RequestError):
            raise
        except Exception as exc:
            logger.error(
                "connector_resolver: refresh failed for %s type=%s",
                connector_id,
                type(exc).__name__,
            )
            raise ConnectorLookupError(connector_type, str(exc)) from exc

    await connector.set_access_token(access_token)
    return connector, connector_id, provider
