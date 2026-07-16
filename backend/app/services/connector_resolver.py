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
from datetime import datetime
from typing import Any, Tuple

from app.infrastructure.connectors.base import BaseConnector, ConnectorFactory
from app.infrastructure.connectors.encryption import get_encryption_service

logger = logging.getLogger(__name__)


class ConnectorNotConnectedError(Exception):
    """No active connector of the requested type for this tenant."""

    def __init__(self, connector_type: str, message: str | None = None):
        self.connector_type = connector_type
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


async def _refresh_and_store(
    db_client: Any,
    connector: BaseConnector,
    connector_id: str,
    refresh_token_encrypted: str,
) -> str:
    """Refresh the OAuth token, persist the new tokens, return the access token."""
    enc = get_encryption_service()
    refresh_token = enc.decrypt(refresh_token_encrypted)
    new_tokens = await connector.refresh_tokens(refresh_token)
    try:
        db_client.table("connector_accounts").update({
            "access_token_encrypted": enc.encrypt(new_tokens.access_token),
            "refresh_token_encrypted": enc.encrypt(new_tokens.refresh_token or refresh_token),
            "token_expires_at": new_tokens.expires_at.isoformat() if getattr(new_tokens, "expires_at", None) else None,
            "last_refreshed_at": datetime.utcnow().isoformat(),
        }).eq("connector_id", connector_id).execute()
    except Exception as exc:  # persistence is best-effort; the token still works now
        logger.warning("connector_resolver: token write-back failed for %s: %s", connector_id, exc)
    return new_tokens.access_token


async def resolve_active_connector(
    db_client: Any,
    tenant_id: str,
    connector_type: str,
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

    # Repeat "Connect" clicks can leave several active connector rows (there is
    # no unique (tenant,type) constraint). The UI shows the NEWEST; pick the
    # newest one that actually has a usable token row so resolver + UI agree.
    enc = get_encryption_service()
    connector_id = None
    provider = None
    acc_data = None
    for row in rows:
        cid = str(row["id"])
        acc = (
            db_client.table("connector_accounts")
            .select("access_token_encrypted, refresh_token_encrypted, token_expires_at, last_refreshed_at")
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
        arow = (adata[0] if isinstance(adata, list) and adata else (adata if isinstance(adata, dict) else None))
        if arow:
            connector_id, provider, acc_data = cid, row["provider"], arow
            break

    if acc_data is None:
        raise ConnectorNotConnectedError(
            connector_type,
            f"Your {connector_type} connection expired. Please reconnect from the Connectors page (left sidebar).",
        )
    acc = type("_Acc", (), {"data": acc_data})()  # keep the existing .data accessors below working

    try:
        access_token = enc.decrypt(acc.data["access_token_encrypted"])
    except Exception as exc:
        logger.error("connector_resolver: token decrypt failed for %s: %s", connector_id, exc)
        raise ConnectorNotConnectedError(
            connector_type, f"Your {connector_type} connection needs to be reconnected."
        )

    connector = ConnectorFactory.create(provider=provider, tenant_id=tenant_id, connector_id=connector_id)

    # Refresh a stale token before handing the connector back.
    expires_at = acc.data.get("token_expires_at")
    is_expired = False
    if expires_at:
        try:
            exp = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            is_expired = datetime.utcnow() >= exp.replace(tzinfo=None)
        except Exception:
            is_expired = False
    if is_expired and acc.data.get("refresh_token_encrypted"):
        try:
            access_token = await _refresh_and_store(
                db_client, connector, connector_id, acc.data["refresh_token_encrypted"]
            )
        except Exception as exc:
            logger.error("connector_resolver: refresh failed for %s: %s", connector_id, exc)
            raise ConnectorNotConnectedError(
                connector_type, f"Your {connector_type} connection expired. Please reconnect."
            )

    await connector.set_access_token(access_token)
    return connector, connector_id, provider
