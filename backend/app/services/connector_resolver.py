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
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional, Tuple
from uuid import UUID

import httpx

from app.infrastructure.connectors.base import (
    BaseConnector,
    ConnectorFactory,
    ConnectorProviderError,
)
from app.infrastructure.connectors.encryption import get_encryption_service

logger = logging.getLogger(__name__)

_TOKEN_REFRESH_SAFETY_SECONDS = 90


class ReviewedConnectorChanged(ValueError):
    """A reviewed account is missing or no longer the current connected account."""


def connector_identity(connector, connector_id, provider) -> dict:
    """Small, credential-free identity; a reconnectable row ID alone is not proof."""
    values = {"connector_id": connector_id, "provider": provider,
              "external_account_id": getattr(connector, "external_account_id", None)}
    if any(not isinstance(value, str) or not value.strip() for value in values.values()):
        raise ReviewedConnectorChanged("The connected account identity is unavailable. Reconnect and review a new proposal.")
    return {key: value.strip() for key, value in values.items()}


def verify_reviewed_connector(connector, connector_id, provider, reviewed) -> dict:
    current = connector_identity(connector, connector_id, provider)
    if not isinstance(reviewed, dict) or any(reviewed.get(key) != value for key, value in current.items()):
        raise ReviewedConnectorChanged("The connected account changed after review. No action was sent; review a new proposal.")
    return current


REVIEWED_AUTHORIZATION_VERSION = "authorization_row_v1"


def reviewed_authorization_identity(connector, connector_id, provider) -> dict:
    """Email/calendar approval binds a local authorization, not an invented subject."""
    proof = {"identity_version": REVIEWED_AUTHORIZATION_VERSION,
             "tenant_id": getattr(connector, "tenant_id", None),
             "connector_id": connector_id, "provider": provider,
             "account_row_id": getattr(connector, "account_row_id", None)}
    if any(not isinstance(value, str) or not value.strip() for value in proof.values()):
        raise ReviewedConnectorChanged("The original authorization is unavailable. Review a new proposal.")
    proof = {key: value.strip() for key, value in proof.items()}
    external = getattr(connector, "external_account_id", None)
    if isinstance(external, str) and external.strip():
        proof["external_account_id"] = external.strip()
    return proof


def verify_reviewed_authorization(connector, connector_id, provider, reviewed) -> dict:
    current = reviewed_authorization_identity(connector, connector_id, provider)
    if not isinstance(reviewed, dict):
        raise ReviewedConnectorChanged("The original authorization proof is unavailable.")
    if "identity_version" not in reviewed:
        # Real historical provider identity remains usable. Missing originals
        # are never replaced with the currently connected account.
        verify_reviewed_connector(connector, connector_id, provider, reviewed)
    elif (reviewed.get("identity_version") != REVIEWED_AUTHORIZATION_VERSION
          or any(reviewed.get(key) != current[key] for key in
                 ("tenant_id", "connector_id", "provider", "account_row_id"))
          or reviewed.get("external_account_id") != current.get("external_account_id")):
        raise ReviewedConnectorChanged("The authorization changed after review. Review a new proposal.")
    return current


def reviewed_authorization_label(proof) -> str:
    """A descriptive label is never substituted for a provider account ID."""
    if isinstance(proof.get("display_label"), str) and proof["display_label"].strip():
        return proof["display_label"].strip()
    if proof.get("external_account_id"):
        return proof["external_account_id"]
    provider = str(proof.get("provider") or "Provider").replace("_", " ")
    return f"{provider} authorization ({str(proof.get('account_row_id') or '')[:8]})"


def _reviewed_account_row(db_client, tenant_id, connector_id, provider, account_id=None):
    """Newest creation-time authorization for reviewed effects only.

    Creation time is stable under canonical writers, not DB-immutable. A token
    refresh must not promote an older authorization over a newer reconnect.
    These local admission reads cannot atomically cancel a later remote effect.
    """
    parent = db_client.table("connectors").select("id").eq("tenant_id", tenant_id).eq(
        "id", connector_id).eq("provider", provider).eq("status", "active").execute()
    if getattr(parent, "error", None):
        raise ConnectorLookupError("reviewed authorization")
    if not getattr(parent, "data", None):
        raise ReviewedConnectorChanged("The reviewed connection is no longer active.")
    response = db_client.table("connector_accounts").select(
        "id,created_at,external_account_id,account_email,access_token_encrypted,refresh_token_encrypted,"
        "token_expires_at,last_refreshed_at"
    ).eq("tenant_id", tenant_id).eq("connector_id", connector_id).eq(
        "status", "active").order("created_at", desc=True).limit(2).execute()
    if getattr(response, "error", None):
        raise ConnectorLookupError("reviewed authorization")
    rows = getattr(response, "data", None) or []
    if not isinstance(rows, list) or not rows:
        raise ReviewedConnectorChanged("The reviewed authorization is no longer active.")
    try:
        dates = []
        for row in rows:
            value = row["created_at"]
            if not isinstance(value, (str, datetime)):
                raise ValueError("missing creation time")
            parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            dates.append(parsed.astimezone(timezone.utc))
        if len(dates) > 1 and dates[0] <= dates[1]:
            raise ValueError("ambiguous creation order")
        row_id = rows[0]["id"]
        if isinstance(row_id, UUID):
            row_id = str(row_id)
        if not isinstance(row_id, str) or not row_id.strip():
            raise ValueError("missing authorization row")
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ReviewedConnectorChanged("The current authorization cannot be established. Reconnect and review.") from exc
    if account_id is not None and row_id != str(account_id):
        raise ReviewedConnectorChanged("The connected authorization changed after review.")
    return {**rows[0], "id": row_id}


def check_reviewed_authorization_current(db_client, tenant_id, connector, connector_id, provider, proof):
    identity = verify_reviewed_authorization(connector, connector_id, provider, proof)
    if identity["tenant_id"] != str(tenant_id):
        raise ReviewedConnectorChanged("The reviewed authorization belongs to another tenant.")
    row = _reviewed_account_row(db_client, tenant_id, connector_id, provider, identity["account_row_id"])
    stored_external = row.get("external_account_id")
    stored_external = stored_external.strip() if isinstance(stored_external, str) and stored_external.strip() else None
    if identity.get("external_account_id") != stored_external:
        raise ReviewedConnectorChanged("The provider account changed after review.")
    return identity


@dataclass(frozen=True, repr=False)
class ConnectorAuthorizationSnapshot:
    """Private persistence proof; never a public or provider identity receipt."""

    tenant_id: str
    connector_id: str
    provider: str
    account_row_id: str
    generation_sha256: str


def _authorization_snapshot_for_row(tenant_id, connector_id, provider, row):
    """Hash the exact stored generation, without retaining credential material."""
    try:
        identity = [tenant_id, connector_id, provider, str(row["id"])]
        if any(not isinstance(value, str) or not value.strip() for value in identity):
            return None
        stored = {
            key: row[key]
            for key in (
                "access_token_encrypted",
                "refresh_token_encrypted",
                "external_account_id",
                "token_expires_at",
                "last_refreshed_at",
            )
        }
        if (
            not isinstance(stored["access_token_encrypted"], str)
            or not stored["access_token_encrypted"]
        ):
            return None
        for key in ("refresh_token_encrypted", "external_account_id"):
            if stored[key] is not None and not isinstance(stored[key], str):
                return None
        for key in ("token_expires_at", "last_refreshed_at"):
            value = stored[key]
            if value is not None:
                parsed = (
                    value
                    if isinstance(value, datetime)
                    else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                )
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                stored[key] = parsed.astimezone(timezone.utc).isoformat()
        digest = hashlib.sha256(
            json.dumps(stored, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return ConnectorAuthorizationSnapshot(*identity, digest)
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


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
        authorization_snapshot: ConnectorAuthorizationSnapshot | None = None,
    ):
        self.connector_type = connector_type
        self.connector_id = connector_id
        self.provider_confirmed = provider_confirmed
        self.reason = reason
        self.authorization_snapshot = authorization_snapshot
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
    *,
    existing_config: Optional[dict] = None,
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
    # Providers that return per-org state with the tokens (Salesforce's
    # instance_url) may move; keep connectors.config current. Best-effort:
    # a failed config write never invalidates a successful token refresh.
    try:
        extra = connector.config_from_tokens(new_tokens)
        if isinstance(extra, dict) and extra:
            base = dict(existing_config) if isinstance(existing_config, dict) else {}
            if any(base.get(k) != v for k, v in extra.items()):
                base.update(extra)
                db_client.table("connectors").update({"config": base}).eq("id", connector_id).eq(
                    "tenant_id", tenant_id
                ).execute()
    except Exception as exc:  # noqa: BLE001 - config write is auxiliary
        logger.warning("connector_resolver: config write-back failed for %s: %s", connector_id, exc)
    try:
        write = db_client.table("connector_accounts").update({
            "access_token_encrypted": enc.encrypt(new_tokens.access_token),
            "refresh_token_encrypted": enc.encrypt(new_tokens.refresh_token or refresh_token),
            "token_expires_at": new_tokens.expires_at.isoformat() if getattr(new_tokens, "expires_at", None) else None,
            "last_refreshed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", account_id).eq("connector_id", connector_id).eq(
            "tenant_id", tenant_id
        ).eq("status", "active").execute()
    except Exception as exc:
        logger.error("connector_resolver: token write-back raised for %s: %s", connector_id, exc)
        raise _ConnectorTokenStoreError(str(exc)) from exc
    if getattr(write, "error", None) or not getattr(write, "data", None):
        detail = str(getattr(write, "error", None) or "no matching account row")
        logger.error("connector_resolver: token write-back failed for %s: %s", connector_id, detail)
        raise _ConnectorTokenStoreError(detail)
    # The returned row is the database's acknowledged generation. Never
    # re-encrypt to construct proof: encryption can produce different ciphertext.
    stored_rows = write.data if isinstance(write.data, list) else [write.data]
    stored_row = stored_rows[0] if len(stored_rows) == 1 else None
    connector._authorization_snapshot = (
        _authorization_snapshot_for_row(tenant_id, connector_id, connector.provider_name, stored_row)
        if isinstance(stored_row, dict) and str(stored_row.get("id")) == account_id
        else None
    )
    return new_tokens.access_token


def list_active_connector_providers(
    db_client: Any,
    tenant_id: str,
    connector_type: str,
) -> List[str]:
    """Distinct providers with an active connector of ``connector_type``
    (newest first). Used by the CRM sync so a tenant with BOTH HubSpot and
    Salesforce connected gets the call logged in each."""
    resp = (
        db_client.table("connectors")
        .select("id, provider, status, created_at")
        .eq("tenant_id", tenant_id)
        .eq("type", connector_type)
        .eq("status", "active")
        .order("created_at", desc=True)
        .execute()
    )
    if getattr(resp, "error", None):
        raise ConnectorLookupError(connector_type, str(resp.error))
    seen: List[str] = []
    for row in resp.data or []:
        provider = str(row.get("provider") or "")
        if provider and provider not in seen:
            seen.append(provider)
    return seen


def _coerce_config(raw: Any) -> Optional[dict]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            import json as _json
            parsed = _json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except ValueError:
            return None
    return None


async def resolve_active_connector(
    db_client: Any,
    tenant_id: str,
    connector_type: str,
    *,
    force_refresh: bool = False,
    provider: Optional[str] = None,
    connector_id: Optional[str] = None,
    account_id: Optional[str] = None,
    reviewed_authorization: bool = False,
    read_only: bool = False,
    external_account_id: Optional[str] = None,
) -> Tuple[BaseConnector, str, str]:
    """Return ``(connector, connector_id, provider)`` for the tenant's active
    connector of ``connector_type`` ("email" | "drive" | "calendar" | ...),
    with a valid (refreshed if needed) access token installed.

    ``provider`` narrows the lookup to one provider (the CRM type can hold a
    HubSpot AND a Salesforce connector at once). Any persisted
    ``connectors.config`` is handed to the connector via ``apply_config``.
    ``account_id`` pins an existing active authorization row; a missing pinned row never
    falls back to another account. Returned ``account_row_id`` identifies that
    local authorization, not a provider-stable external account identity.
    Reviewed email/calendar effects opt into unique creation-time selection;
    ordinary reads and CRM retain their existing refresh-time ordering.
    ``read_only`` is an inspection-only opt-in: require the original connector,
    provider and either authorization row or external account, one active match, and a
    safely unexpired token. It never refreshes or writes connector state.

    Raises ``ConnectorNotConnectedError`` when nothing is connected/usable.
    """
    if read_only and (force_refresh or reviewed_authorization or any(
        not isinstance(value, str) or not value.strip()
        for value in (connector_id, provider)
    ) or not any(isinstance(value, str) and value.strip() for value in (account_id, external_account_id))):
        raise ConnectorNotConnectedError(connector_type, reason="original_account_unavailable")
    if external_account_id is not None and not read_only:
        raise ValueError("External account pinning requires read-only inspection")
    query = (
        db_client.table("connectors")
        .select("id, provider, status, created_at, config")
        .eq("tenant_id", tenant_id)
        .eq("type", connector_type)
        .eq("status", "active")
    )
    if provider:
        query = query.eq("provider", provider)
    if connector_id:
        query = query.eq("id", connector_id)
    resp = query.order("created_at", desc=True).execute()  # newest-first, matching the UI's choice
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
        raise ConnectorNotConnectedError(
            connector_type, connector_id=connector_id,
            reason="account_unavailable" if account_id is not None else None,
        )

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
    first_failure_snapshot = None
    for row in rows[:1]:
        cid = str(row["id"])
        account_query = (
            db_client.table("connector_accounts")
            .select("id, access_token_encrypted, refresh_token_encrypted, token_expires_at, last_refreshed_at, external_account_id")
            .eq("connector_id", cid)
            .eq("tenant_id", tenant_id)
            .eq("status", "active")
        )
        if account_id is not None:
            account_query = account_query.eq("id", account_id)
        if read_only and external_account_id is not None:
            account_query = account_query.eq("external_account_id", external_account_id)
        if reviewed_authorization:
            selected = _reviewed_account_row(db_client, tenant_id, cid, row["provider"], account_id)
            account_rows = [selected]
        else:
            acc = account_query.order("last_refreshed_at", desc=True).limit(2 if read_only else 1).execute()
            if getattr(acc, "error", None):
                logger.error("resolve_active_connector: connector_accounts query error cid=%s err=%s", cid, acc.error)
                raise ConnectorLookupError(connector_type, str(acc.error))
            adata = acc.data
            account_rows = adata if isinstance(adata, list) else ([adata] if isinstance(adata, dict) else [])
            if read_only and len(account_rows) != 1:
                raise ConnectorNotConnectedError(connector_type, reason="original_authorization_unavailable")
        if account_id is not None and not account_rows:
            first_failure_reason = "account_unavailable"
        for arow in account_rows:
            first_failure_snapshot = _authorization_snapshot_for_row(tenant_id, cid, row["provider"], arow)
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
            if read_only and (not arow.get("token_expires_at") or candidate_should_refresh):
                raise ConnectorNotConnectedError(connector_type, reason="inspection_token_unavailable")
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
            authorization_snapshot=first_failure_snapshot,
        )

    connector = ConnectorFactory.create(provider=provider, tenant_id=tenant_id, connector_id=connector_id)
    connector.external_account_id = str(acc_data.get("external_account_id") or "") or None
    connector.account_row_id = str(acc_data["id"])
    if reviewed_authorization:
        connector.account_email = acc_data.get("account_email")
    connector._authorization_snapshot = _authorization_snapshot_for_row(tenant_id, connector_id, provider, acc_data)
    row_config = _coerce_config(rows[0].get("config") if isinstance(rows[0], dict) else None)
    if row_config is not None:
        connector.apply_config(row_config)

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
                existing_config=row_config,
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
                    authorization_snapshot=connector._authorization_snapshot,
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
    if reviewed_authorization:
        check_reviewed_authorization_current(db_client, tenant_id, connector, connector_id, provider,
                                             reviewed_authorization_identity(connector, connector_id, provider))
    return connector, connector_id, provider
