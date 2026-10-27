"""The transactional boundary for canonical connector authorization writers.

Lock parent rows before accounts. Never hold a transaction over provider I/O.
Refresh ownership is durable *before* dispatch; an unknown result requires new
authorization, never a timed takeover or a rollback to old provider credentials.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from uuid import UUID, uuid4
from urllib.parse import quote

import httpx

from app.core.db_utils import acquire_with_tenant
from app.core.security.credential_issuance import lock_user_credential_issuance
from app.core.security.platform_admin_guard import lock_user_administration
from app.core.security.principal import PrincipalUnavailable, load_current_principal, load_session_principal
from app.core.security.rbac import Permission, get_user_permissions
from app.domain.services.audit_logger import AuditEvent, AuditLogger
from app.infrastructure.connectors.base import ConnectorFactory, ConnectorProviderError
from app.infrastructure.connectors.encryption import get_encryption_service

logger = logging.getLogger(__name__)


async def revoke_token_at_provider(provider, token, *, tenant_id, connector_id):
    """Best-effort existing provider revocation; False is unconfirmed, not success."""
    try:
        if provider in {"google_calendar", "gmail", "google_drive"}:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post("https://oauth2.googleapis.com/revoke", data={"token": token})
            return response.status_code == 200
        if provider == "hubspot":
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.delete(f"https://api.hubapi.com/oauth/v1/refresh-tokens/{quote(token, safe='')}")
            return response.status_code in (200, 204)
        if provider == "salesforce":
            connector = ConnectorFactory.create(provider=provider, tenant_id=tenant_id, connector_id=connector_id)
            return bool(await connector.revoke_token(token))
    except Exception as exc:
        logger.warning("Provider token revocation failed provider=%s type=%s", provider, type(exc).__name__)
    return False


class ConnectorLifecycleError(Exception):
    def __init__(self, message: str, status_code: int = 409):
        self.status_code = status_code
        super().__init__(message)


def config_dict(value):
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            pass
    return {}


async def lock_connector(conn, tenant_id, connector_id):
    """Shared parent → all-account order, also used by inbox expiry."""
    parent = await conn.fetchrow(
        "SELECT * FROM connectors WHERE id=$1 AND tenant_id=$2 FOR UPDATE",
        UUID(str(connector_id)), UUID(str(tenant_id)),
    )
    if parent is None:
        raise ConnectorLifecycleError("Connector not found", 404)
    accounts = await conn.fetch(
        """SELECT * FROM connector_accounts WHERE connector_id=$1 AND tenant_id=$2
           ORDER BY id FOR UPDATE""", UUID(str(connector_id)), UUID(str(tenant_id)),
    )
    return dict(parent), [dict(row) for row in accounts]


def _current_account(accounts, account_id=None, *, allow_expired=False):
    statuses = {"active", "expired"} if allow_expired else {"active"}
    eligible = [row for row in accounts if row["status"] in statuses]
    eligible.sort(key=lambda row: (row["created_at"], str(row["id"])), reverse=True)
    if not eligible:
        raise ConnectorLifecycleError("No usable authorization. Reauthorize the connector.")
    if len(eligible) > 1 and eligible[0]["created_at"] == eligible[1]["created_at"]:
        raise ConnectorLifecycleError("Authorization is ambiguous. Reauthorize the connector.")
    current = eligible[0]
    if account_id is not None and str(current["id"]) != str(account_id):
        raise ConnectorLifecycleError("The authorization changed. Review the current connection.")
    return current


@dataclass(repr=False)
class RefreshClaim:
    tenant_id: str
    connector_id: str
    account_id: str
    claim_id: UUID
    generation: int
    refresh_token: str


@dataclass(frozen=True)
class ConnectorActor:
    """Server-supplied attribution, never a model/request-body argument."""
    source: str
    user_id: str | None = None
    role: str | None = None
    platform_admin: bool = False
    oauth_initiator: bool = False
    session_id: str | None = field(default=None, repr=False)
    tenant_id: str | None = None

    @classmethod
    def human(cls, user, source, *, request, platform_admin=False):
        user_id = str(user.id)
        if getattr(request.state, "authenticated_user_id", None) != user_id:
            raise ConnectorLifecycleError("Current authenticated session required.", 401)
        try:
            session_id = str(UUID(str(getattr(request.state, "authenticated_session_id", None))))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ConnectorLifecycleError("Current authenticated session required.", 401) from exc
        tenant_id = getattr(user, "tenant_id", None)
        return cls(source=source, user_id=user_id, role=getattr(user, "role", None),
                   platform_admin=platform_admin, session_id=session_id,
                   tenant_id=str(tenant_id) if tenant_id is not None else None)

    @classmethod
    def oauth(cls, user_id):
        if not user_id:
            raise ConnectorLifecycleError("OAuth initiator is unavailable. Start a new authorization.")
        return cls("oauth_callback", str(user_id), oauth_initiator=True)

    @classmethod
    def system(cls, source):
        return cls(source)


@dataclass(frozen=True)
class ConnectorDisableResult:
    provider_revocation_status: str
    audit_event_id: str | None
    operation_id: str | None
    provider_result_recorded: bool = True

    @property
    def provider_revoked(self):
        return self.provider_result_recorded and self.provider_revocation_status == "confirmed"

    def receipt(self):
        return {"local_disabled": True, "provider_revoked": self.provider_revoked,
                "provider_revocation_status": self.provider_revocation_status,
                "provider_result_recorded": self.provider_result_recorded,
                "audit_event_id": self.audit_event_id, "operation_id": self.operation_id}


class ConnectorLifecycle:
    def __init__(self, db_client, *, actor: ConnectorActor, audit_logger=None):
        self.pool = db_client.pool
        if not isinstance(actor, ConnectorActor):
            raise TypeError("Explicit connector actor context is required")
        self.actor = actor
        self.audit = audit_logger or AuditLogger(self.pool)
        self.operation_id = uuid4()

    async def _begin(self, conn, tenant_id, permission=None):
        if (permission is not None and self.actor.user_id is not None
                and not self.actor.oauth_initiator
                and await conn.fetchval("SHOW transaction_isolation") != "read committed"):
            raise ConnectorLifecycleError("Current connector authority requires a fresh transaction snapshot.", 503)
        await self.audit.lock_transaction(conn)
        if permission is None or self.actor.user_id is None:
            return self.actor
        await lock_user_administration(conn)
        return await self._authorize(conn, tenant_id, permission)

    async def _authorize(self, conn, tenant_id, permission, *, require_session=False):
        if self.actor.user_id is None:
            return self.actor
        try:
            if require_session and not self.actor.oauth_initiator:
                principal = await load_session_principal(conn, {
                    "sub": self.actor.user_id, "sid": self.actor.session_id,
                    "tenant_id": self.actor.tenant_id,
                })
                if principal["tenant_id"] != self.actor.tenant_id:
                    raise PrincipalUnavailable("tenant_context_changed")
                if not self.actor.platform_admin and str(principal["tenant_id"]) != str(tenant_id):
                    raise PrincipalUnavailable("tenant_context_changed")
            else:
                principal = await load_current_principal(conn, self.actor.user_id,
                    tenant_id=None if self.actor.platform_admin else tenant_id)
        except PrincipalUnavailable as exc:
            if require_session and not self.actor.oauth_initiator:
                raise ConnectorLifecycleError("Current account or session is unavailable.", 401) from exc
            raise ConnectorLifecycleError("Current connector authority is required.", 403) from exc
        if self.actor.platform_admin and principal["role"] != "platform_admin":
            raise ConnectorLifecycleError("Current platform admin authority is required.", 403)
        if principal["role"] != "platform_admin":
            grants = await get_user_permissions(conn, self.actor.user_id, str(tenant_id))
            if permission not in grants:
                raise ConnectorLifecycleError("Current connector permission is required.", 403)
        return replace(self.actor, role=principal["role"])

    def _scope(self, tenant_id):
        # Admin actors may live outside the target tenant. Every connector
        # lookup/write remains explicitly bound to the selected tenant/id.
        return None if self.actor.platform_admin else str(tenant_id)

    async def _record(self, conn, tenant_id, connector_id, actor, action, *,
                      operation_id=None, event=AuditEvent.CONFIG_CHANGED, **facts):
        metadata = {"source": actor.source, "operation_id": str(operation_id or self.operation_id), **facts}
        if actor.oauth_initiator:
            metadata.update(initiator_id=actor.user_id, initiator_role=actor.role,
                            executing_service="oauth_callback")
        return await self.audit.log_in_transaction(conn, event_type=event, action=action,
            actor_id=None if actor.oauth_initiator else actor.user_id,
            actor_type="system" if actor.oauth_initiator or actor.user_id is None else "user",
            actor_role=None if actor.oauth_initiator else actor.role, tenant_id=tenant_id,
            resource_type="connector", resource_id=connector_id, metadata=metadata)

    async def create_pending(self, tenant_id, connector_type, provider, name=None):
        """Admit one human OAuth start and its audit in the same transaction.

        Keep authentication's profile -> session order after the audit and
        administration locks. The session lock also serializes device revocation,
        whose writer does not take the profile fence. No OAuth/provider work is
        performed while these locks are held.
        """
        if self.actor.user_id is None or self.actor.oauth_initiator or not self.actor.session_id:
            raise ConnectorLifecycleError("Current authenticated session required.", 401)
        try:
            user_id = UUID(str(self.actor.user_id))
            session_id = UUID(str(self.actor.session_id))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ConnectorLifecycleError("Current authenticated session required.", 401) from exc
        try:
            tenant_id = str(UUID(str(tenant_id)))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ConnectorLifecycleError("Current tenant context required.", 403) from exc
        if self.actor.tenant_id != tenant_id:
            raise ConnectorLifecycleError("Current tenant context required.", 403)
        connector_id = uuid4()
        try:
            async with asyncio.timeout(5.0):
                async with acquire_with_tenant(self.pool, tenant_id, user_id=str(user_id)) as conn:
                    await self._begin(conn, tenant_id, Permission.CONNECTORS_CREATE)
                    await lock_user_credential_issuance(conn, user_id)
                    session = await conn.fetchrow("""SELECT id FROM security_sessions
                        WHERE id=$1 AND user_id=$2 AND revoked=FALSE
                          AND expires_at>clock_timestamp() FOR UPDATE""", session_id, user_id)
                    if session is None:
                        raise ConnectorLifecycleError("Current account or session is unavailable.", 401)
                    actor = await self._authorize(conn, tenant_id, Permission.CONNECTORS_CREATE, require_session=True)
                    # The permissions read may itself wait. Row locks prevent
                    # revocation, but time can still expire the original session.
                    valid = await conn.fetchval("""SELECT EXISTS(SELECT 1 FROM security_sessions
                        WHERE id=$1 AND user_id=$2 AND revoked=FALSE
                          AND expires_at>clock_timestamp())""", session_id, user_id)
                    if not valid:
                        raise ConnectorLifecycleError("Current account or session is unavailable.", 401)
                    await conn.execute("""INSERT INTO connectors(id,tenant_id,type,provider,name,status)
                        VALUES($1,$2,$3,$4,$5,'pending')""",
                        connector_id, UUID(tenant_id), connector_type, provider, name)
                    audit_id = await self._record(conn, tenant_id, str(connector_id), actor,
                        "connector.oauth_pending_created", connector_type=connector_type,
                        provider=provider, local_status="pending")
            # Preparation failures are completion of this admitted operation.
            # Retain its current role for reject_pending's mandatory audit even
            # if the original browser logs out during OAuth state preparation.
            self.actor = actor
            return {"id": str(connector_id), "audit_event_id": str(audit_id),
                    "operation_id": str(self.operation_id)}
        except ConnectorLifecycleError:
            raise
        except PrincipalUnavailable as exc:
            raise ConnectorLifecycleError("Current account or session is unavailable.", 401) from exc
        except Exception as exc:
            raise ConnectorLifecycleError(
                "Connector authorization could not be confirmed. Check connection status before starting again.", 503,
            ) from exc

    async def begin_oauth(self, tenant_id, connector_id):
        """Durable intent and current initiator check before code exchange."""
        async with acquire_with_tenant(self.pool, self._scope(tenant_id)) as conn:
            actor = await self._begin(conn, tenant_id, Permission.CONNECTORS_CREATE)
            parent, _ = await lock_connector(conn, tenant_id, connector_id)
            if parent["status"] != "pending":
                raise ConnectorLifecycleError("OAuth connection is no longer pending.")
            return await self._record(conn, tenant_id, connector_id, actor, "connector.oauth_exchange_intent")

    async def refresh(self, tenant_id, connector_id, *, connector=None, account_id=None,
                      allow_expired=False):
        """Return (tokens, acknowledged account row); no unpersisted token escapes."""
        enc = get_encryption_service()
        async with acquire_with_tenant(self.pool, self._scope(tenant_id), user_id=self.actor.user_id) as conn:
            actor = await self._begin(conn, tenant_id, Permission.CONNECTORS_UPDATE)
            parent, accounts = await lock_connector(conn, tenant_id, connector_id)
            # Both parent and account locks may wait while logout commits.
            # Admit new work with the original verified session after those waits.
            actor = await self._authorize(conn, tenant_id, Permission.CONNECTORS_UPDATE, require_session=True)
            allowed = {"active", "expired", "error"} if allow_expired else {"active"}
            if parent["status"] not in allowed:
                raise ConnectorLifecycleError("Connector is disabled. Reauthorize it to connect again.")
            newer = await conn.fetchval("""SELECT EXISTS(SELECT 1 FROM connectors
                WHERE tenant_id=$1 AND type=$2 AND provider=$3 AND status='active'
                  AND (created_at,id)>($4,$5))""", UUID(str(tenant_id)), parent["type"],
                parent["provider"], parent["created_at"], parent["id"])
            if newer:
                raise ConnectorLifecycleError("A newer authorization is connected. Reload the current connection.")
            account = _current_account(accounts, account_id, allow_expired=allow_expired)
            if account["refresh_state"] != "idle":
                message = ("A token refresh is already in progress; no second request was sent. "
                           "If it does not complete, reauthorize the connector.")
                if account["refresh_state"] == "uncertain":
                    message = "The previous refresh outcome is unknown. Reauthorize the connector; do not retry token rotation."
                raise ConnectorLifecycleError(message)
            if not account.get("refresh_token_encrypted"):
                raise ConnectorLifecycleError("No refresh token is available. Reauthorize the connector.")
            token = enc.decrypt(account["refresh_token_encrypted"])
            if not token:
                raise ConnectorLifecycleError("No refresh token is available. Reauthorize the connector.")
            if connector is None:
                connector = ConnectorFactory.create(provider=parent["provider"], tenant_id=str(tenant_id),
                                                    connector_id=str(connector_id))
            if connector.provider_name != parent["provider"]:
                raise ConnectorLifecycleError("Connector provider changed. Reauthorize the connector.")
            connector.apply_config(config_dict(parent.get("config")))
            claim = RefreshClaim(str(tenant_id), str(connector_id), str(account["id"]), uuid4(),
                                 account["credential_generation"], token)
            await conn.execute("""UPDATE connector_accounts SET refresh_state='in_flight',
                refresh_claim_id=$2, refresh_claimed_at=NOW(), refresh_error=NULL WHERE id=$1""",
                UUID(claim.account_id), claim.claim_id)
            await self._record(conn, tenant_id, connector_id, actor, "connector.refresh_intent",
                operation_id=claim.claim_id, account_id=claim.account_id, generation=claim.generation,
                provider=parent["provider"], refresh_state="in_flight")

        # Everything after this boundary is potentially provider-visible. A
        # timeout, cancellation or save error must never release this claim.
        try:
            tokens = await connector.refresh_tokens(claim.refresh_token)
            if not getattr(tokens, "access_token", None):
                raise ConnectorLifecycleError("Provider returned no access token. Reauthorize the connector.")
            extra_config = connector.config_from_tokens(tokens)
            access_encrypted = enc.encrypt(tokens.access_token)
            refresh_encrypted = enc.encrypt(tokens.refresh_token or claim.refresh_token)
            async with acquire_with_tenant(self.pool, claim.tenant_id) as conn:
                await self._begin(conn, claim.tenant_id)
                parent, accounts = await lock_connector(conn, claim.tenant_id, claim.connector_id)
                current = next((row for row in accounts if str(row["id"]) == claim.account_id), None)
                if (current is None or parent["status"] not in allowed
                        or current["status"] not in {"active", "expired"}
                        or current["credential_generation"] != claim.generation
                        or current["refresh_claim_id"] != claim.claim_id
                        or current["refresh_state"] != "in_flight"):
                    raise ConnectorLifecycleError("Connection changed during refresh. No new credentials were installed.")
                row = await conn.fetchrow("""UPDATE connector_accounts SET
                    access_token_encrypted=$2, refresh_token_encrypted=$3, token_expires_at=$4,
                    last_refreshed_at=NOW(), token_last_rotated_at=NOW(), rotation_count=COALESCE(rotation_count,0)+1,
                    credential_generation=credential_generation+1, status='active',
                    refresh_state='idle', refresh_claim_id=NULL, refresh_claimed_at=NULL, refresh_error=NULL
                    WHERE id=$1 RETURNING *""", UUID(claim.account_id), access_encrypted,
                    refresh_encrypted, tokens.expires_at)
                config = config_dict(parent.get("config"))
                if isinstance(extra_config, dict):
                    config.update(extra_config)
                await conn.execute("UPDATE connectors SET status='active',config=$2::jsonb WHERE id=$1",
                                   UUID(claim.connector_id), json.dumps(config))
                audit_id = await self._record(conn, claim.tenant_id, claim.connector_id, actor,
                    "connector.refresh_installed", operation_id=claim.claim_id,
                    event=AuditEvent.SECRET_ROTATED, account_id=claim.account_id,
                    prior_generation=claim.generation, generation=row["credential_generation"],
                    refresh_state="idle")
            return tokens, {**dict(row), "audit_event_id": str(audit_id), "operation_id": str(claim.claim_id)}
        except BaseException as exc:
            # Best effort records the uncertain outcome; durable in_flight is
            # equally non-retryable if the database/process is unavailable.
            try:
                await asyncio.shield(self._uncertain(claim, "provider_rejected" if
                    isinstance(exc, ConnectorProviderError) and exc.category == "authentication"
                    else "refresh_outcome_unknown", actor))
            except Exception:
                logger.warning("Could not record refresh outcome connector=%s", claim.connector_id)
            raise

    async def _uncertain(self, claim, reason, actor):
        async with acquire_with_tenant(self.pool, claim.tenant_id) as conn:
            await self._begin(conn, claim.tenant_id)
            parent, accounts = await lock_connector(conn, claim.tenant_id, claim.connector_id)
            current = next((row for row in accounts if str(row["id"]) == claim.account_id), None)
            if current is None or current["refresh_claim_id"] != claim.claim_id:
                return
            await conn.execute("""UPDATE connector_accounts SET refresh_state='uncertain', refresh_error=$2
                WHERE id=$1""", UUID(claim.account_id), reason)
            await self._record(conn, claim.tenant_id, claim.connector_id, actor,
                "connector.refresh_uncertain", operation_id=claim.claim_id,
                account_id=claim.account_id, generation=claim.generation, reason=reason,
                refresh_state="uncertain")

    async def disable(self, tenant_id, connector_id, *, revoke_token):
        """Disable locally first; provider acknowledgement is a separate fact."""
        operation_id = uuid4()
        async with acquire_with_tenant(self.pool, self._scope(tenant_id), user_id=self.actor.user_id) as conn:
            actor = await self._begin(conn, tenant_id, Permission.CONNECTORS_DELETE)
            parent, accounts = await lock_connector(conn, tenant_id, connector_id)
            actor = await self._authorize(conn, tenant_id, Permission.CONNECTORS_DELETE, require_session=True)
            if parent["status"] in {"disconnected", "revoked"}:
                receipt = await conn.fetchrow("""SELECT event_id,metadata FROM audit_logs
                    WHERE tenant_id=$1 AND resource_type='connector' AND resource_id=$2
                      AND action='connector.disabled_locally' ORDER BY event_time DESC LIMIT 1""",
                    UUID(str(tenant_id)), UUID(str(connector_id)))
                metadata = config_dict(receipt["metadata"]) if receipt else {}
                return ConnectorDisableResult(parent.get("provider_revocation_status") or "unconfirmed",
                    str(receipt["event_id"]) if receipt else None, metadata.get("operation_id"))
            uncertain_refresh = any(row["refresh_state"] != "idle" for row in accounts)
            encrypted_tokens = [row.get("refresh_token_encrypted") or row.get("access_token_encrypted")
                                for row in accounts if row["status"] in {"active", "expired"}]
            await self._retire(conn, tenant_id, [connector_id], status="disconnected")
            await conn.execute("UPDATE connectors SET provider_revocation_status='pending' WHERE id=$1",
                               UUID(str(connector_id)))
            local_audit_id = await self._record(conn, tenant_id, connector_id, actor,
                "connector.disabled_locally", operation_id=operation_id, event=AuditEvent.SECRET_REVOKED,
                account_ids=[str(row["id"]) for row in accounts],
                generations=[row["credential_generation"] + 1 for row in accounts],
                local_status="disconnected", provider_revocation_status="pending",
                refresh_outcome_unknown=uncertain_refresh)
        confirmed = bool(encrypted_tokens) and not uncertain_refresh
        for encrypted in encrypted_tokens:
            ok = False
            try:
                if encrypted:
                    token = get_encryption_service().decrypt(encrypted)
                    ok = await revoke_token(parent["provider"], token, tenant_id=str(tenant_id),
                                            connector_id=str(connector_id))
            except Exception:
                logger.warning("Provider revocation not confirmed connector=%s", connector_id)
            confirmed = confirmed and bool(ok)
        outcome = "confirmed" if confirmed else "unconfirmed"
        try:
            async with acquire_with_tenant(self.pool, str(tenant_id)) as conn:
                await self._begin(conn, tenant_id)
                await lock_connector(conn, tenant_id, connector_id)
                saved = await conn.fetchval("""UPDATE connectors SET provider_revocation_status=$3
                    WHERE id=$1 AND tenant_id=$2 AND status IN ('disconnected','revoked')
                      AND provider_revocation_status='pending' RETURNING id""",
                    UUID(str(connector_id)), UUID(str(tenant_id)), outcome)
                if saved is None:
                    raise ConnectorLifecycleError("Connector state changed before provider result was recorded.")
                await self._record(conn, tenant_id, connector_id, actor,
                    "connector.provider_revocation_result", operation_id=operation_id,
                    local_audit_event_id=str(local_audit_id), provider_revocation_status=outcome)
        except Exception:
            logger.warning("Provider revocation result not persisted connector=%s", connector_id)
            return ConnectorDisableResult("unconfirmed", str(local_audit_id), str(operation_id), False)
        return ConnectorDisableResult(outcome, str(local_audit_id), str(operation_id))

    @staticmethod
    async def _retire(conn, tenant_id, connector_ids, *, status="revoked"):
        ids = [UUID(str(value)) for value in connector_ids]
        await conn.execute("""UPDATE connector_accounts SET status='revoked',
            access_token_encrypted=NULL, refresh_token_encrypted=NULL, revoked_at=NOW(),
            revoked_reason='connector_disconnected', credential_generation=credential_generation+1,
            refresh_state='idle', refresh_claim_id=NULL, refresh_claimed_at=NULL, refresh_error=NULL
            WHERE tenant_id=$1 AND connector_id=ANY($2::uuid[])""", UUID(str(tenant_id)), ids)
        await conn.execute("UPDATE connectors SET status=$3 WHERE tenant_id=$1 AND id=ANY($2::uuid[])",
                           UUID(str(tenant_id)), ids, status)
        await conn.execute("""UPDATE reminders SET status='cancelled', last_error='connector_disconnected'
            WHERE tenant_id=$1 AND status='pending' AND meeting_id IN
              (SELECT id FROM meetings WHERE tenant_id=$1 AND connector_id=ANY($2::uuid[]))""",
                           UUID(str(tenant_id)), ids)

    async def activate(self, tenant_id, connector_id, provider, connector_type, account_data,
                       persisted_config, *, carry_keys=()):
        """One commit installs a verified OAuth grant and retires older grants."""
        async with acquire_with_tenant(self.pool, self._scope(tenant_id)) as conn:
            actor = await self._begin(conn, tenant_id, Permission.CONNECTORS_CREATE)
            # Concurrent callbacks may involve different parent rows. Serialize
            # only this tenant/provider/type before locking all parents by id.
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))",
                               f"connector-oauth:{tenant_id}:{connector_type}:{provider}")
            rows = await conn.fetch("""SELECT * FROM connectors WHERE tenant_id=$1 AND type=$2
                AND provider=$3 ORDER BY id FOR UPDATE""", UUID(str(tenant_id)), connector_type, provider)
            parents = [dict(row) for row in rows]
            target = next((row for row in parents if str(row["id"]) == str(connector_id)), None)
            if target is None or target["status"] != "pending":
                raise ConnectorLifecycleError("OAuth connection is no longer pending. Start a new authorization.")
            # Expired/error parents can still hold refreshable credentials and
            # settings. Retire those too, or Admin refresh could revive an old
            # authorization after a successful replacement.
            usable = [row for row in parents if row["status"] in {"active", "expired", "error"}]
            order = lambda row: (row["created_at"], str(row["id"]))
            if usable and order(max(usable, key=order)) > order(target):
                raise ConnectorLifecycleError("A newer authorization is already connected. This callback was superseded.")
            ids = [row["id"] for row in parents]
            await conn.fetch("""SELECT id FROM connector_accounts WHERE tenant_id=$1
                AND connector_id=ANY($2::uuid[]) ORDER BY connector_id,id FOR UPDATE""", UUID(str(tenant_id)), ids)
            config = {}
            for old in sorted(usable, key=order):
                old_config = config_dict(old.get("config"))
                config.update({key: old_config[key] for key in carry_keys if key in old_config})
            config.update(config_dict(target.get("config")))
            config.update(persisted_config)
            retire = [str(row["id"]) for row in usable]
            if retire:
                await self._retire(conn, tenant_id, retire)
            # A pending parent is never reusable after disable; old accounts on
            # an otherwise pending row are retired atomically as well.
            await self._retire(conn, tenant_id, [connector_id])
            account = await conn.fetchrow("""INSERT INTO connector_accounts
                (connector_id,tenant_id,access_token_encrypted,refresh_token_encrypted,token_expires_at,
                 scopes,account_email,external_account_id,status,last_refreshed_at)
                VALUES($1,$2,$3,$4,$5,$6,$7,$8,'active',NOW()) RETURNING *""",
                UUID(str(connector_id)), UUID(str(tenant_id)), account_data["access_token_encrypted"],
                account_data.get("refresh_token_encrypted"), account_data.get("token_expires_at"),
                account_data.get("scopes") or [], account_data.get("account_email"), account_data.get("external_account_id"))
            await conn.execute("""UPDATE connectors SET status='active',config=$2::jsonb,
                provider_revocation_status='not_requested' WHERE id=$1""", UUID(str(connector_id)), json.dumps(config))
            audit_id = await self._record(conn, tenant_id, connector_id, actor, "connector.oauth_activated",
                account_id=str(account["id"]), generation=account["credential_generation"],
                retired_connector_ids=retire, local_status="active")
        return {**dict(account), "audit_event_id": str(audit_id), "operation_id": str(self.operation_id)}

    async def reject_pending(self, tenant_id, connector_id):
        async with acquire_with_tenant(self.pool, str(tenant_id)) as conn:
            await self._begin(conn, tenant_id)
            await lock_connector(conn, tenant_id, connector_id)
            saved = await conn.fetchval("UPDATE connectors SET status='error' WHERE id=$1 AND status='pending' RETURNING id",
                                        UUID(str(connector_id)))
            if saved:
                await self._record(conn, tenant_id, connector_id, self.actor,
                    "connector.oauth_rejected", local_status="error")

    async def update_config(self, tenant_id, connector_id, patch, *, provider):
        """Merge settings against the current active row, including token metadata."""
        async with acquire_with_tenant(self.pool, self._scope(tenant_id), user_id=self.actor.user_id) as conn:
            actor = await self._begin(conn, tenant_id, Permission.CONNECTORS_UPDATE)
            parent, _ = await lock_connector(conn, tenant_id, connector_id)
            actor = await self._authorize(conn, tenant_id, Permission.CONNECTORS_UPDATE, require_session=True)
            if parent["status"] != "active" or parent["provider"] != provider:
                raise ConnectorLifecycleError("The connection changed. Reload it before saving settings.")
            config = config_dict(parent.get("config"))
            for key, value in patch.items():
                if value is None:
                    config.pop(key, None)
                else:
                    config[key] = value
            await conn.execute("UPDATE connectors SET config=$2::jsonb WHERE id=$1",
                               UUID(str(connector_id)), json.dumps(config))
            await self._record(conn, tenant_id, connector_id, actor, "connector.config_updated",
                changed_fields=sorted(patch))
        return config
