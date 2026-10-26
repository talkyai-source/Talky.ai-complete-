"""
Admin Users Endpoints
User management: list, create, update (role / block), delete.

Mutations are restricted to platform_admin (the highest tier) so this screen
can't be used to escalate privileges (gap G1) — a tenant_admin cannot mint a
platform_admin here. Listing and single-user reads also require platform_admin
(P0-3): these queries span every tenant's users, so a tenant_admin must not be
able to read them.
"""
from __future__ import annotations

import logging
import hashlib
import hmac
import json
from typing import List, Literal, Optional
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.api.v1.dependencies import (
    CurrentUser,
    get_audit_logger,
    get_db_client,
    require_platform_admin,
)
from app.core.db_utils import acquire_with_tenant
from app.core.postgres_adapter import Client
from app.core.security.password import MAX_PASSWORD_LENGTH, hash_password
from app.core.security.sessions import revoke_all_user_sessions
from app.core.security.refresh_tokens import revoke_all_user_refresh_tokens
from app.core.security.platform_admin_guard import (
    count_active_platform_admins as _count_active_platform_admins,
    lock_user_administration,
    require_current_platform_admin,
)
from app.domain.services.audit_logger import AuditEvent, AuditLogger
from app.domain.services.telephony.call_authority import read_formal_account_authority

from ._serialization import AdminResponseModel
from .user_operations import require_prepared_operation, user_operation_terminal_metadata

logger = logging.getLogger(__name__)

router = APIRouter()

_ROLE_PATTERN = r"^(platform_admin|partner_admin|tenant_admin|campaign_manager|user|agent|billing_user|readonly)$"
_REVIEW_PATTERN = r"^v1:[0-9a-f]{64}$"


# =============================================================================
# Models
# =============================================================================

class AdminUserItem(AdminResponseModel):
    """Rich user row for the Users & Roles screen."""
    id: str
    email: str
    name: Optional[str] = None
    role: str
    # role is an assignment (historical for missing membership), not access proof.
    effective_role: Optional[str] = None
    membership_status: Optional[str] = None
    tenant_id: Optional[str] = None
    tenant_name: Optional[str] = None
    is_active: bool = True
    mfa_enabled: bool = False
    is_verified: bool = False
    last_login_at: Optional[str] = None
    created_at: Optional[str] = None
    audit_event_id: Optional[str] = None
    review_token: str = Field(pattern=_REVIEW_PATTERN)


class CreateUserRequest(BaseModel):
    operation_id: UUID
    request_id: UUID
    name: str = Field(..., min_length=1, max_length=200)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=MAX_PASSWORD_LENGTH)
    role: str = Field("user", pattern=_ROLE_PATTERN)
    tenant_id: Optional[UUID] = None

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, value):
        # Let the native string/min_length validators reject invalid input so
        # the public validation envelope stays JSON-serializable.
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _same_operation(self):
        if self.request_id != self.operation_id:
            raise ValueError("The create request must use its server-prepared operation ID")
        return self


class UpdateUserRequest(BaseModel):
    operation_id: UUID
    expected_review_token: str = Field(pattern=_REVIEW_PATTERN)
    name: Optional[str] = Field(None, max_length=200)
    role: Optional[str] = Field(None, pattern=_ROLE_PATTERN)
    expected_role: Optional[str] = Field(None, pattern=_ROLE_PATTERN)
    is_active: Optional[bool] = None


class DeleteUserRequest(BaseModel):
    operation_id: UUID
    expected_review_token: str = Field(pattern=_REVIEW_PATTERN)


class UpdateUserReceipt(AdminUserItem):
    operation: Literal["updated"] = "updated"
    operation_id: UUID
    audit_event_id: UUID


class DeleteUserReceipt(BaseModel):
    operation_id: UUID
    id: UUID
    outcome: Literal["deleted", "deactivated"]
    audit_event_id: UUID
    detail: str
    user: Optional[AdminUserItem]

    @model_validator(mode="after")
    def _validate_effect(self):
        if self.outcome == "deleted" and self.user is not None:
            raise ValueError("A deleted receipt cannot contain a current user")
        if self.outcome == "deactivated" and (
            self.user is None or UUID(self.user.id) != self.id or self.user.is_active
        ):
            raise ValueError("A deactivated receipt requires the exact inactive user")
        return self


class UserCreateIntent(BaseModel):
    name: str
    email: EmailStr
    role: str = Field(pattern=_ROLE_PATTERN)
    tenant_id: Optional[UUID]


class UserCreateRequestStatus(BaseModel):
    request_id: UUID
    state: Literal["unrecorded", "created", "cancelled"]
    audit_event_id: Optional[UUID] = None
    user_id: Optional[UUID] = None
    user: Optional[AdminUserItem] = None
    intent: Optional[UserCreateIntent] = None

    @model_validator(mode="after")
    def _validate_state(self):
        if self.state == "created":
            if self.audit_event_id is None or self.user_id is None or self.intent is None:
                raise ValueError("Created request requires its committed audit, target and original intent")
            if self.user is not None and UUID(self.user.id) != self.user_id:
                raise ValueError("Current user must match the original created target")
        elif self.state == "cancelled":
            if self.audit_event_id is None or any(value is not None for value in (self.user_id, self.user, self.intent)):
                raise ValueError("Cancelled request requires its audit and no created target")
        elif any(value is not None for value in (self.audit_event_id, self.user_id, self.user, self.intent)):
            raise ValueError("Unrecorded request cannot claim a receipt")
        return self


class CreateUserReceipt(UserCreateRequestStatus):
    state: Literal["created"] = "created"
    audit_event_id: UUID
    user_id: UUID
    intent: UserCreateIntent


class PrimaryMembershipItem(BaseModel):
    id: UUID
    role: str
    status: str
    is_primary: bool


class PrimaryMembershipTenant(BaseModel):
    id: UUID
    name: Optional[str]


class PrimaryMembershipReview(BaseModel):
    user: AdminUserItem
    tenant: PrimaryMembershipTenant
    membership: Optional[PrimaryMembershipItem]
    eligible: bool
    reason: Optional[str]
    review_token: str = Field(pattern=r"^pm1:[0-9a-f]{64}$")


class PrimaryMembershipRequest(BaseModel):
    operation_id: UUID
    tenant_id: UUID
    role: str = Field(pattern=r"^(partner_admin|tenant_admin|campaign_manager|user|agent|billing_user|readonly)$")
    expected_review_token: str = Field(pattern=r"^pm1:[0-9a-f]{64}$")


class CredentialCutoff(BaseModel):
    revoked_sessions: int = Field(ge=0)
    revoked_refresh_tokens: int = Field(ge=0)
    invalidated_mfa_challenges: int = Field(ge=0)
    invalidated_passkey_challenges: int = Field(ge=0)


class PrimaryMembershipReceipt(BaseModel):
    operation: Literal["primary_membership_configured"] = "primary_membership_configured"
    operation_id: UUID
    user: AdminUserItem
    membership: PrimaryMembershipItem
    tenant_id: UUID
    audit_event_id: UUID
    credential_cutoff: CredentialCutoff


# =============================================================================
# Helpers
# =============================================================================

_EFFECTIVE_ROLE_SQL = """CASE
    WHEN NOT COALESCE(up.is_active,FALSE) OR NOT COALESCE(up.is_verified,FALSE) THEN NULL
    WHEN up.role='platform_admin' THEN 'platform_admin'
    WHEN tu.status='active' AND r.name IN
         ('partner_admin','tenant_admin','campaign_manager','user','agent','billing_user','readonly')
    THEN r.name ELSE NULL END"""

_USER_SELECT = f"""
    SELECT up.id, up.email, up.name,
           CASE WHEN up.role='platform_admin' THEN up.role
                ELSE COALESCE(r.name,up.role) END AS role,
           {_EFFECTIVE_ROLE_SQL} AS effective_role,
           CASE WHEN up.role='platform_admin' THEN 'not_required'
                ELSE COALESCE(tu.status,'missing') END AS membership_status,
           up.tenant_id,
           t.business_name AS tenant_name,
           up.is_active, up.mfa_enabled, up.is_verified,
           up.last_login_at, up.created_at,
           up.admin_revision AS _profile_revision,
           tu.id AS _membership_id, tu.admin_revision AS _membership_revision
    FROM   user_profiles up
    LEFT   JOIN tenants t ON t.id = up.tenant_id
    LEFT   JOIN tenant_users tu ON tu.user_id=up.id AND tu.tenant_id=up.tenant_id
    LEFT   JOIN roles r ON r.id=tu.role_id
"""


def _user_item(row) -> AdminUserItem:
    data = dict(row)
    # Database-owned counters cover every profile/membership writer, including
    # same-transaction ABA. No timestamp, xmin or guessed current state acts as
    # a version. The token is a precondition, never authentication.
    version = [str(data["id"]), data.pop("_profile_revision"),
               str(data.pop("_membership_id") or ""), data.pop("_membership_revision"),
               data["role"], data["effective_role"], data["membership_status"]]
    data["review_token"] = "v1:" + hashlib.sha256(
        json.dumps(version, separators=(",", ":")).encode()).hexdigest()
    return AdminUserItem(**data)


async def _fetch_user(conn, user_id: str) -> Optional[AdminUserItem]:
    row = await conn.fetchrow(_USER_SELECT + " WHERE up.id = $1", user_id)
    return _user_item(row) if row else None


async def _require_review(conn, user_id, expected_token):
    # Profile was locked first. NO KEY UPDATE permits membership INSERT's FK
    # key-share while still serializing all credential issuance/profile writes.
    membership = await conn.fetchrow("""SELECT tu.id,tu.status FROM tenant_users tu JOIN user_profiles up
        ON up.id=tu.user_id AND up.tenant_id=tu.tenant_id
        WHERE up.id=$1 FOR NO KEY UPDATE OF tu""", user_id)
    current = await _fetch_user(conn, user_id)
    if current is None or not hmac.compare_digest(current.review_token, expected_token):
        raise HTTPException(409, detail={
            "code": "user_changed",
            "message": "This account changed after you reviewed it. Refresh and review before saving.",
        })
    return current, membership


async def _create_request_row(conn, actor_id, request_id):
    return await conn.fetchrow("""SELECT event_id,resource_id,action,metadata
        FROM audit_logs WHERE actor_id=$1::uuid AND resource_type='user'
        AND action IN ('user_created','user_create_cancelled')
        AND metadata ? 'user_create_request_id'
        AND metadata->>'user_create_request_id'=$2""", str(actor_id), str(request_id))


def _request_metadata(row):
    return json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]


async def _create_request_status(conn, request_id, row):
    if row is None:
        return UserCreateRequestStatus(request_id=request_id, state="unrecorded")
    if row["action"] == "user_create_cancelled":
        return UserCreateRequestStatus(request_id=request_id, state="cancelled", audit_event_id=row["event_id"])
    return CreateUserReceipt(request_id=request_id, audit_event_id=row["event_id"],
                             user_id=row["resource_id"], user=await _fetch_user(conn, row["resource_id"]),
                             intent=UserCreateIntent(**_request_metadata(row)["user_create_intent"]))


def _create_intent(body):
    return UserCreateIntent(name=body.name.strip(), email=str(body.email).strip().lower(),
                            role=body.role, tenant_id=body.tenant_id)


def _create_digest(body, actor_id, audit_logger):
    value = {"domain": "admin-user-create-v1", "actor_id": str(actor_id),
             **_create_intent(body).model_dump(mode="json"), "password": body.password}
    # Password is included only in a keyed digest; neither plaintext nor an
    # unkeyed password oracle is retained in audit, receipts or diagnostics.
    return hmac.new(audit_logger.signing_key.encode(),
                    json.dumps(value, sort_keys=True, separators=(",", ":")).encode(),
                    hashlib.sha256).hexdigest()


def _write_rejected(message, *, status_code=400, code="user_write_rejected"):
    """Stable no-write guards, distinct from an unclassified transport error."""
    return HTTPException(status_code, detail={"code": code, "message": message})


async def _primary_membership_review(conn, user_id, tenant_id, actor_id, *, lock=False):
    user = await _fetch_user(conn, user_id)
    if user is None:
        raise _write_rejected("User not found", status_code=404, code="user_not_found")
    tenant = await conn.fetchrow(
        "SELECT id,business_name,updated_at,status,white_label_partner_id FROM tenants WHERE id=$1", tenant_id)
    if tenant is None:
        raise _write_rejected("Tenant not found", status_code=404, code="tenant_not_found")
    # Only the selected candidate and existing primary assignments affect this
    # setup. A concurrent unrelated nonprimary membership is outside its scope.
    rows = await conn.fetch("""SELECT tu.id,tu.tenant_id,tu.role_id,tu.status,tu.is_primary,
        tu.admin_revision,r.name AS role FROM tenant_users tu JOIN roles r ON r.id=tu.role_id
        WHERE tu.user_id=$1 AND (tu.tenant_id=$2 OR tu.is_primary=TRUE)
        ORDER BY tu.id""" + (" FOR NO KEY UPDATE OF tu" if lock else ""), user_id, tenant_id)
    member = next((row for row in rows if row["tenant_id"] == tenant_id), None)
    authority = await read_formal_account_authority(conn, tenant_id)
    held = await conn.fetchval("SELECT EXISTS(SELECT 1 FROM suspension_events WHERE target_type='user' AND target_id=$1 AND is_active=TRUE)", user_id)
    reason = None
    if str(user_id) == str(actor_id):
        reason = "You cannot configure your own primary membership."
    elif not user.is_active or not user.is_verified or held:
        reason = "Resolve this account's block, verification or formal suspension first."
    elif user.tenant_id and user.tenant_id != str(tenant_id):
        reason = "Tenant transfers are not supported by initial primary membership setup."
    elif any(row["is_primary"] and row["tenant_id"] != tenant_id for row in rows):
        reason = "This account already has a different primary membership."
    elif member and member["status"] != "active":
        reason = "An inactive membership requires separate review; this action cannot reactivate it."
    elif user.tenant_id and member:
        reason = "This account already has a membership in its primary tenant. Use the existing role controls."
    elif not authority.allowed:
        reason = "The selected tenant or its formal parent currently restricts account access."
    value = {"user": user.review_token, "tenant": dict(tenant),
             "memberships": [dict(row) for row in rows], "formal_allowed": authority.allowed,
             "formal_reason": authority.reason, "user_hold": held}
    token = "pm1:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    review = PrimaryMembershipReview(user=user, tenant=PrimaryMembershipTenant(id=tenant_id,name=tenant["business_name"]),
        membership=PrimaryMembershipItem(**dict(member)) if member else None,
        eligible=reason is None,reason=reason,review_token=token)
    return review, member


# =============================================================================
# Endpoints
# =============================================================================

@router.get("/users", response_model=List[AdminUserItem])
async def list_users(
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    search: Optional[str] = Query(None, description="Match email or name"),
    role: Optional[str] = Query(None, description="Filter by current effective role"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """List users with role, tenant, status, MFA and timestamps (admin only)."""
    conditions: list[str] = []
    params: list = []

    if search:
        params.append(f"%{search}%")
        idx = len(params)
        conditions.append(f"(up.email ILIKE ${idx} OR up.name ILIKE ${idx})")
    if role:
        params.append(role)
        conditions.append(f"({_EFFECTIVE_ROLE_SQL}) = ${len(params)}")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(limit)
    limit_idx = len(params)
    params.append(offset)
    offset_idx = len(params)

    query = (
        _USER_SELECT
        + f" {where} ORDER BY up.created_at DESC NULLS LAST, up.email "
        + f"LIMIT ${limit_idx} OFFSET ${offset_idx}"
    )

    # This platform-admin directory intentionally spans every tenant.
    async with acquire_with_tenant(db_client.pool, None) as conn:
        rows = await conn.fetch(query, *params)

    return [_user_item(r) for r in rows]


@router.get("/users/create-requests/{request_id}", response_model=UserCreateRequestStatus)
async def get_create_request(
    request_id: UUID,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
):
    """Read this operator's exact retained receipt; absence is not cancellation."""
    async with acquire_with_tenant(db_client.pool, None) as conn:
        await require_current_platform_admin(conn, admin_user.id)
        row = await _create_request_row(conn, admin_user.id, request_id)
        return await _create_request_status(conn, request_id, row)


@router.post("/users/create-requests/{request_id}/cancel", response_model=UserCreateRequestStatus)
async def cancel_create_request(
    request_id: UUID,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Retire an unrecorded request before any delayed create can take effect."""
    async with acquire_with_tenant(db_client.pool, None) as conn:
        await audit_logger.lock_transaction(conn)
        await lock_user_administration(conn)
        await require_current_platform_admin(conn, admin_user.id)
        existing = await _create_request_row(conn, admin_user.id, request_id)
        if existing:
            result = await _create_request_status(conn, request_id, existing)
        else:
            event_id = await audit_logger.log_in_transaction(
                conn, event_type=AuditEvent.USER_UPDATED, actor_id=admin_user.id,
                actor_type="user", resource_type="user", resource_id=None,
                action="user_create_cancelled", description="Admin retired an unresolved user create request",
                metadata={"user_create_request_id": str(request_id)},
            )
            result = UserCreateRequestStatus(request_id=request_id, state="cancelled", audit_event_id=event_id)
    return result


@router.get("/users/{user_id}", response_model=AdminUserItem)
async def get_user(
    user_id: UUID,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
):
    """Get a single user by id (admin only)."""
    # The platform-admin route resolves an ID before its tenant is known.
    async with acquire_with_tenant(db_client.pool, None) as conn:
        user = await _fetch_user(conn, user_id)
    if not user:
        raise _write_rejected("User not found", status_code=404, code="user_not_found")
    return user


@router.get("/users/{user_id}/primary-membership", response_model=PrimaryMembershipReview)
async def get_primary_membership_review(
    user_id: UUID,
    tenant_id: UUID,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
):
    """Review one initial association; no transfer or implicit reactivation."""
    async with acquire_with_tenant(db_client.pool, None) as conn:
        await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        await require_current_platform_admin(conn, admin_user.id)
        reviewed, _ = await _primary_membership_review(conn, user_id, tenant_id, admin_user.id)
    return reviewed


@router.post("/users/{user_id}/primary-membership", response_model=PrimaryMembershipReceipt)
async def configure_primary_membership(
    user_id: UUID,
    body: PrimaryMembershipRequest,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Atomically set an initial primary membership and retire prior credentials."""
    async with acquire_with_tenant(db_client.pool, None) as conn:
        await audit_logger.lock_transaction(conn)
        await lock_user_administration(conn)
        await require_current_platform_admin(conn, admin_user.id)
        prepared = await require_prepared_operation(conn, actor_id=admin_user.id,
            operation_id=body.operation_id, kind="primary_membership", target_user_id=user_id,
            intent=body.model_dump(mode="json", exclude={"operation_id"}), audit_logger=audit_logger)
        if prepared.replay_receipt is not None:
            return PrimaryMembershipReceipt.model_validate(prepared.replay_receipt)
        await conn.fetchrow("SELECT id FROM tenants WHERE id=$1 FOR NO KEY UPDATE", body.tenant_id)
        # NO KEY UPDATE still fences every auth issuer's FOR UPDATE, while
        # allowing an existing RBAC INSERT to finish its FK key-share check.
        # FOR UPDATE here would invert that INSERT's unique-index/FK order.
        await conn.fetchrow("SELECT id FROM user_profiles WHERE id=$1 FOR NO KEY UPDATE", user_id)
        reviewed, member = await _primary_membership_review(
            conn,user_id,body.tenant_id,admin_user.id,lock=True)
        if not hmac.compare_digest(reviewed.review_token, body.expected_review_token):
            raise _write_rejected("The account or selected tenant changed. Review this association again.",
                                  status_code=409,code="primary_membership_changed")
        if not reviewed.eligible:
            raise _write_rejected(reviewed.reason,status_code=409,code="primary_membership_ineligible")
        if reviewed.user.role == "platform_admin" and await _count_active_platform_admins(conn, exclude_id=user_id) == 0:
            raise _write_rejected("Cannot remove the last active platform admin")
        role_id = await conn.fetchval("SELECT id FROM roles WHERE name=$1", body.role)
        if role_id is None:
            raise HTTPException(503,"Account role configuration is unavailable.")
        try:
            if member is None:
                membership_id = await conn.fetchval("""INSERT INTO tenant_users
                    (user_id,tenant_id,role_id,is_primary,status,joined_at)
                    VALUES($1,$2,$3,TRUE,'active',NOW()) ON CONFLICT DO NOTHING RETURNING id""",
                    user_id,body.tenant_id,role_id)
                if membership_id is None:
                    raise _write_rejected("A membership was added after review. Review this association again.",
                                          status_code=409,code="primary_membership_changed")
            else:
                membership_id = await conn.fetchval("""UPDATE tenant_users SET role_id=$1,is_primary=TRUE,updated_at=NOW()
                    WHERE id=$2 AND user_id=$3 AND tenant_id=$4 AND status='active' RETURNING id""",
                    role_id,member["id"],user_id,body.tenant_id)
        except asyncpg.UniqueViolationError as exc:
            raise _write_rejected("A different primary membership was added after review. Review again.",
                                  status_code=409,code="primary_membership_changed") from exc
        await conn.execute("UPDATE user_profiles SET tenant_id=$2,role=$3,updated_at=NOW() WHERE id=$1",
                           user_id,body.tenant_id,body.role)
        cutoff = await conn.fetchval("SELECT clock_timestamp()")
        revoked_sessions = await revoke_all_user_sessions(conn,str(user_id),reason="primary_membership_configured")
        revoked_refresh_tokens = await revoke_all_user_refresh_tokens(conn,str(user_id),reason="admin")
        mfa = await conn.execute("UPDATE mfa_challenges SET used=TRUE,used_at=clock_timestamp() WHERE user_id=$1 AND used=FALSE",user_id)
        passkeys = await conn.execute("UPDATE webauthn_challenges SET used=TRUE,used_at=clock_timestamp() WHERE user_id=$1 AND used=FALSE",user_id)
        counts = CredentialCutoff(revoked_sessions=revoked_sessions,revoked_refresh_tokens=revoked_refresh_tokens,
            invalidated_mfa_challenges=int(mfa.split()[-1]),invalidated_passkey_challenges=int(passkeys.split()[-1]))
        user = await _fetch_user(conn,user_id)
        receipt_data = {"operation": "primary_membership_configured", "operation_id": str(body.operation_id),
            "user": user.model_dump(mode="json"),
            "tenant_id": str(body.tenant_id), "membership": {
                "id": str(membership_id), "role": body.role, "status": "active", "is_primary": True},
            "credential_cutoff": counts.model_dump()}
        operation_metadata = user_operation_terminal_metadata(prepared, receipt_data)
        audit_id = await audit_logger.log_in_transaction(conn,event_type=AuditEvent.USER_UPDATED,
            actor_id=admin_user.id,actor_type="user",tenant_id=body.tenant_id,
            resource_type="user",resource_id=user_id,action="user_primary_membership_configured",
            description="Admin configured initial primary tenant membership and retired existing credentials",
            metadata={**operation_metadata,"primary_membership_version":1,"credential_cutoff_at":cutoff.isoformat(),
                      "previous_tenant_id":reviewed.user.tenant_id,"tenant_id":str(body.tenant_id),
                      "previous_role":reviewed.user.role,"role":body.role,"membership_id":str(membership_id),
                      "credential_cutoff":counts.model_dump()})
        result = PrimaryMembershipReceipt(**receipt_data, audit_event_id=audit_id)
    return result


@router.post("/users", response_model=CreateUserReceipt, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: CreateUserRequest,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
    response: Response = None,
):
    """
    Create a user with an admin-set temporary password (platform_admin only).

    The account is created verified + active so the user can sign in immediately
    with the password you set and change it afterwards.
    """
    email = body.email.strip().lower()
    name = body.name.strip()
    if body.role != "platform_admin" and not body.tenant_id:
        raise _write_rejected("Select a tenant for this account role.")
    pw_hash = hash_password(body.password)
    intent_digest = _create_digest(body, admin_user.id, audit_logger)

    # Platform administration must see the actor and the explicitly selected
    # tenant. Recheck authority after the same lock used by removal/promotion.
    async with acquire_with_tenant(db_client.pool, None) as conn:
        await audit_logger.lock_transaction(conn)
        await lock_user_administration(conn)
        await require_current_platform_admin(conn, admin_user.id)
        prepared = await require_prepared_operation(conn, actor_id=admin_user.id,
            operation_id=body.operation_id, kind="create", target_user_id=None,
            intent=_create_intent(body).model_dump(mode="json"), password=body.password, audit_logger=audit_logger)
        if prepared.replay_receipt is not None:
            if response is not None:
                response.status_code = status.HTTP_200_OK
            return CreateUserReceipt.model_validate(prepared.replay_receipt)
        existing_request = await _create_request_row(conn, admin_user.id, body.request_id)
        if existing_request:
            if existing_request["action"] == "user_create_cancelled":
                raise HTTPException(409, detail={"code": "user_create_cancelled",
                    "message": "This create request was closed. Review a new request to create an account."})
            # New writes require the prepared protocol. Legacy receipts remain
            # readable, but cannot substitute for its missing terminal record.
            raise HTTPException(503, detail="User operation recovery is inconsistent. Do not resend the change.")
        if body.tenant_id:
            tenant = await conn.fetchrow("SELECT id FROM tenants WHERE id = $1", body.tenant_id)
            if not tenant:
                raise _write_rejected("Tenant not found", status_code=404)

        role_id = None
        if body.role != "platform_admin":
            role_id = await conn.fetchval("SELECT id FROM roles WHERE name=$1", body.role)
            if role_id is None:
                raise HTTPException(status_code=503, detail="Account role configuration is unavailable.")
        existing = await conn.fetchrow(
            "SELECT id FROM user_profiles WHERE lower(email) = $1", email
        )
        if existing:
            raise _write_rejected("A user with this email already exists", status_code=409, code="user_email_exists")

        try:
            new_id = await conn.fetchval(
                """
                INSERT INTO user_profiles
                    (email, name, role, password_hash, tenant_id,
                     is_active, is_verified, mfa_enabled,
                     email_verified_at, password_changed_at)
                VALUES (lower($1), $2, $3, $4, $5, true, true, false, NOW(), NOW())
                RETURNING id
                """,
                email, name, body.role, pw_hash, body.tenant_id,
            )
        except asyncpg.UniqueViolationError:
            raise _write_rejected("A user with this email already exists", status_code=409, code="user_email_exists")

        if role_id is not None:
            await conn.execute(
                """INSERT INTO tenant_users
                   (user_id,tenant_id,role_id,is_primary,status,joined_at)
                   VALUES($1,$2,$3,TRUE,'active',NOW())""",
                new_id, body.tenant_id, role_id,
            )
        user = await _fetch_user(conn, str(new_id))
        receipt_data = {"request_id": str(body.request_id), "state": "created", "user_id": str(new_id),
                        "user": user.model_dump(mode="json"), "intent": _create_intent(body).model_dump(mode="json")}
        operation_metadata = user_operation_terminal_metadata(prepared, receipt_data)

        audit_event_id = await audit_logger.log_in_transaction(
            conn,
            event_type=AuditEvent.USER_CREATED,
            actor_id=admin_user.id,
            actor_type="user",
            tenant_id=body.tenant_id,
            resource_type="user",
            resource_id=str(new_id),
            action="user_created",
            description=f"Admin created user {email} with role {body.role}",
            metadata={**operation_metadata, "email": email, "role": body.role,
                      "user_create_request_id": str(body.request_id),
                      "user_create_intent_hmac_v1": intent_digest,
                      "user_create_intent": _create_intent(body).model_dump(mode="json")},
        )
        result = CreateUserReceipt(**receipt_data, audit_event_id=audit_event_id)
    return result


@router.patch("/users/{user_id}", response_model=UpdateUserReceipt)
async def update_user(
    user_id: UUID,
    body: UpdateUserRequest,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """Update a user's name, role, or active status (block/unblock) — platform_admin only."""
    if body.name is None and body.role is None and body.is_active is None:
        raise _write_rejected("No fields to update")

    # The platform-admin route resolves an ID before its tenant is known.
    async with acquire_with_tenant(db_client.pool, None) as conn:
        await audit_logger.lock_transaction(conn)
        await lock_user_administration(conn)
        await require_current_platform_admin(conn, admin_user.id)
        prepared = await require_prepared_operation(conn, actor_id=admin_user.id,
            operation_id=body.operation_id, kind="update", target_user_id=user_id,
            intent=body.model_dump(mode="json", exclude={"operation_id"}, exclude_none=True), audit_logger=audit_logger)
        if prepared.replay_receipt is not None:
            return UpdateUserReceipt.model_validate(prepared.replay_receipt)
        current = await conn.fetchrow(
            "SELECT id, name, role, is_active, tenant_id FROM user_profiles WHERE id = $1 FOR NO KEY UPDATE", user_id
        )
        if not current:
            raise _write_rejected("User not found", status_code=404, code="user_not_found")
        reviewed_user, reviewed_membership = await _require_review(conn, user_id, body.expected_review_token)

        if body.role is not None and body.expected_role is not None:
            # Compare the assignment the operator actually reviewed. A global
            # role overrides membership; other directory rows show the joined
            # membership role, including historical inactive assignments.
            assignment = reviewed_user.role
            if assignment != body.expected_role:
                raise HTTPException(status_code=409, detail={
                    "code": "user_role_changed",
                    "message": "This user's role changed. Refresh and review before saving.",
                    "current_role": assignment,
                })

        is_self = str(current["id"]) == str(admin_user.id)

        # Self-protection: can't block or demote yourself out of platform_admin.
        if is_self and body.is_active is False:
            raise _write_rejected("You cannot block your own account")
        if is_self and body.role is not None and body.role != current["role"]:
            raise _write_rejected("You cannot change your own role")

        if body.is_active is not None and await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM suspension_events "
            "WHERE target_type='user' AND target_id=$1 AND is_active=TRUE)", user_id
        ):
            raise HTTPException(
                status_code=409,
                detail="This account has an active formal suspension. Review and restore that suspension before changing its ordinary block status.",
            )

        # Don't strand the platform: keep at least one active platform_admin.
        demoting = (
            current["role"] == "platform_admin"
            and (
                (body.role is not None and body.role != "platform_admin")
                or body.is_active is False
            )
        )
        if demoting and await _count_active_platform_admins(conn, exclude_id=user_id) == 0:
            raise _write_rejected("Cannot remove the last active platform admin")

        if body.role is not None and body.role != "platform_admin":
            # Existing admin role edits must change the same membership used by
            # current authorization. Do not silently create or reactivate a
            # historical missing/suspended membership as a side effect.
            if reviewed_membership is None or reviewed_membership["status"] != "active":
                raise _write_rejected("Resolve this account's active tenant membership before changing its role.",
                                      status_code=409, code="user_membership_required")
            role_id = await conn.fetchval("SELECT id FROM roles WHERE name=$1", body.role)
            if role_id is None:
                raise HTTPException(status_code=503, detail="Account role configuration is unavailable.")
            updated = await conn.fetchval(
                """UPDATE tenant_users SET role_id=$1,updated_at=NOW()
                   WHERE user_id=$2 AND tenant_id=$3 AND id=$4 AND status='active' RETURNING id""",
                role_id, user_id, current["tenant_id"], reviewed_membership["id"],
            )
            if updated is None:
                raise _write_rejected("Resolve this account's active tenant membership before changing its role.",
                                      status_code=409, code="user_membership_required")

        sets: list[str] = []
        values: list = []
        normalized_name = body.name.strip() if body.name is not None else None
        if body.name is not None:
            values.append(normalized_name)
            sets.append(f"name = ${len(values)}")
        if body.role is not None:
            values.append(body.role)
            sets.append(f"role = ${len(values)}")
        if body.is_active is not None:
            values.append(body.is_active)
            sets.append(f"is_active = ${len(values)}")

        sets.append("updated_at = NOW()")
        values.append(user_id)
        await conn.execute(
            f"UPDATE user_profiles SET {', '.join(sets)} WHERE id = ${len(values)}",
            *values,
        )
        user = await _fetch_user(conn, user_id)
        receipt_data = {**user.model_dump(mode="json", exclude={"audit_event_id"}), "operation": "updated",
                        "operation_id": str(body.operation_id)}
        operation_metadata = user_operation_terminal_metadata(prepared, receipt_data)

        # Pick the most descriptive audit event.
        if body.is_active is False:
            event = AuditEvent.USER_SUSPENDED
        elif body.is_active is True:
            event = AuditEvent.USER_RESTORED
        else:
            event = AuditEvent.USER_UPDATED
        audit_event_id = await audit_logger.log_in_transaction(
            conn,
            event_type=event,
            actor_id=admin_user.id,
            actor_type="user",
            resource_type="user",
            resource_id=user_id,
            action="user_updated",
            description=f"Admin updated user {user.email if user else user_id}",
            metadata={
                **operation_metadata,
                "new_role": body.role,
                "is_active": body.is_active,
                "name_changed": body.name is not None and normalized_name != current["name"],
            },
        )
        result = UpdateUserReceipt(**receipt_data, audit_event_id=audit_event_id)
    return result


@router.delete("/users/{user_id}", response_model=DeleteUserReceipt)
async def delete_user(
    user_id: UUID,
    body: DeleteUserRequest,
    admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger),
):
    """
    Delete a user (platform_admin only).

    Attempts a hard delete. If the user is referenced by other records
    (calls, audit logs, …) the FK constraint makes a hard delete unsafe, so we
    fall back to deactivating the account and report that in the response.
    """
    # The platform-admin route resolves an ID before its tenant is known.
    async with acquire_with_tenant(db_client.pool, None) as conn:
        await audit_logger.lock_transaction(conn)
        await lock_user_administration(conn)
        await require_current_platform_admin(conn, admin_user.id)
        prepared = await require_prepared_operation(conn, actor_id=admin_user.id,
            operation_id=body.operation_id, kind="delete", target_user_id=user_id,
            intent={"expected_review_token": body.expected_review_token}, audit_logger=audit_logger)
        if prepared.replay_receipt is not None:
            return DeleteUserReceipt.model_validate(prepared.replay_receipt).model_dump(mode="json")
        current = await conn.fetchrow(
            "SELECT id, email, role FROM user_profiles WHERE id = $1 FOR NO KEY UPDATE", user_id
        )
        if not current:
            raise _write_rejected("User not found", status_code=404, code="user_not_found")
        await _require_review(conn, user_id, body.expected_review_token)

        if str(current["id"]) == str(admin_user.id):
            raise _write_rejected("You cannot delete your own account")

        if current["role"] == "platform_admin" and await _count_active_platform_admins(
            conn, exclude_id=user_id
        ) == 0:
            raise _write_rejected("Cannot delete the last active platform admin")

        if await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM suspension_events "
            "WHERE target_type='user' AND target_id=$1 AND is_active=TRUE)", user_id
        ):
            raise HTTPException(
                status_code=409,
                detail="This account has an active formal suspension. Review and restore that suspension before deleting or deactivating the account.",
            )

        # A missing membership has no row to lock. Before deletion, upgrade the
        # parent lock and recheck: a completed FK insert changes the review;
        # subsequent inserts cannot pass FK key-share until this deletion ends.
        # Existing matching rows were locked first, avoiding adoption/deletion
        # of an unreviewed membership created in the initial check's gap.
        await conn.fetchrow("SELECT id FROM user_profiles WHERE id=$1 FOR UPDATE", user_id)
        await _require_review(conn, user_id, body.expected_review_token)

        # tenant_users membership is owned by this user — clearing it is safe and
        # removes the most common FK blocker before the hard delete. Wrap both in
        # one transaction so an FK fallback rolls the membership delete back too.
        try:
            async with conn.transaction():
                await conn.execute("DELETE FROM tenant_users WHERE user_id = $1", user_id)
                await conn.execute("DELETE FROM user_profiles WHERE id = $1", user_id)
            hard_deleted = True
        except asyncpg.ForeignKeyViolationError:
            # Has linked records elsewhere — deactivate instead of orphaning them.
            await conn.execute(
                "UPDATE user_profiles SET is_active = false, updated_at = NOW() WHERE id = $1",
                user_id,
            )
            hard_deleted = False

        current_user = None if hard_deleted else await _fetch_user(conn, user_id)
        receipt_data = {"operation_id": str(body.operation_id), "id": str(user_id),
            "outcome": "deleted" if hard_deleted else "deactivated",
            "detail": "User deleted successfully" if hard_deleted else
                      "User had linked records and was deactivated instead of hard-deleted.",
            "user": current_user.model_dump(mode="json") if current_user is not None else None}
        operation_metadata = user_operation_terminal_metadata(prepared, receipt_data)

        audit_event_id = await audit_logger.log_in_transaction(
            conn,
            event_type=AuditEvent.USER_DELETED,
            actor_id=admin_user.id,
            actor_type="user",
            resource_type="user",
            resource_id=user_id,
            action="user_deleted" if hard_deleted else "user_deactivated",
            description=f"Admin {'deleted' if hard_deleted else 'deactivated'} user {current['email']}",
            metadata={**operation_metadata, "hard_deleted": hard_deleted},
        )

        result = DeleteUserReceipt(**receipt_data, audit_event_id=audit_event_id)
    return result.model_dump(mode="json")
