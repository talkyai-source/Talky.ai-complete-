"""Synthetic lifecycle port for existing email/calendar consumer tests only.

These tests exercise downstream identity admission, not lifecycle atomicity.
Actual refresh claims, DB faults, concurrency and OAuth retirement are covered
using migrated PostgreSQL in integration/test_connector_lifecycle.py.
"""
from datetime import datetime, timezone
from uuid import uuid4

from app.services import connector_resolver


def seed_authorization(db, connector_id, tenant_id, tokens, *, account_email=None):
    row = {"id": str(uuid4()), "connector_id": connector_id, "tenant_id": tenant_id,
           "status": "active", "created_at": datetime.now(timezone.utc).isoformat(),
           "last_refreshed_at": datetime.now(timezone.utc).isoformat(),
           "access_token_encrypted": "synthetic:" + tokens.access_token,
           "refresh_token_encrypted": "synthetic:" + (tokens.refresh_token or ""),
           "token_expires_at": tokens.expires_at.isoformat(), "external_account_id": None,
           "account_email": account_email, "refresh_state": "idle"}
    db.rows["connector_accounts"] = [row]
    for parent in db.rows["connectors"]:
        if parent["id"] == connector_id:
            parent["status"] = "active"
    return row


def install_refresh_port(monkeypatch):
    async def refresh(db, connector, connector_id, account_id, tenant_id, refresh_token, **_):
        tokens = await connector.refresh_tokens(refresh_token)
        result = db.table("connector_accounts").update({
            "access_token_encrypted": "synthetic:" + tokens.access_token,
            "refresh_token_encrypted": "synthetic:" + (tokens.refresh_token or refresh_token),
            "token_expires_at": tokens.expires_at.isoformat() if tokens.expires_at else None,
            "last_refreshed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", account_id).eq("connector_id", connector_id).eq("tenant_id", tenant_id).eq("status", "active").execute()
        if result.error or not result.data:
            raise connector_resolver._ConnectorTokenStoreError("synthetic store failure")
        connector._authorization_snapshot = connector_resolver._authorization_snapshot_for_row(
            tenant_id, connector_id, connector.provider_name, result.data[0])
        return tokens.access_token
    monkeypatch.setattr(connector_resolver, "_refresh_and_store", refresh)
