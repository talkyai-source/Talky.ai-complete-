"""Prepared, actor-owned recovery for the existing Admin user mutations.

There is no worker or replay engine. Preparation records intent before a client
may dispatch. Existing mutation transactions consume it under audit -> admin
locks and attach their original validated receipt to the same mandatory audit.
Cancellation takes those same locks. Expiry limits dispatch, never proves that
an already admitted transaction did not commit. Mount this router before /users/{id}.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import hmac
import json
import logging
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, EmailStr, Field, StrictBool, ValidationError, field_validator, model_validator

from app.api.v1.dependencies import CurrentUser, get_audit_logger, get_db_client, require_platform_admin
from app.core.db_utils import acquire_with_tenant
from app.core.postgres_adapter import Client
from app.core.security.password import MAX_PASSWORD_LENGTH
from app.core.security.platform_admin_guard import lock_user_administration, require_current_platform_admin
from app.domain.services.audit_logger import AuditEvent, AuditLogger

logger = logging.getLogger(__name__)
router = APIRouter()
DISPATCH_WINDOW = timedelta(minutes=15)
PREPARED_ACTION = "user_operation_prepared"
CANCELLED_ACTION = "user_operation_cancelled"
TERMINAL_ACTIONS = ("user_created", "user_updated", "user_deleted", "user_deactivated",
                    "user_primary_membership_configured", CANCELLED_ACTION)
OperationKind = Literal["create", "update", "delete", "primary_membership"]
_ROLE = r"^(platform_admin|partner_admin|tenant_admin|campaign_manager|user|agent|billing_user|readonly)$"
_MEMBER_ROLE = r"^(partner_admin|tenant_admin|campaign_manager|user|agent|billing_user|readonly)$"
_REVIEW = r"^v1:[0-9a-f]{64}$"
_VERSION_SQL = "metadata->'user_operation_version'='1'::jsonb"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateIntent(_StrictModel):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    role: str = Field(pattern=_ROLE)
    tenant_id: UUID | None

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class UpdateIntent(_StrictModel):
    expected_review_token: str = Field(pattern=_REVIEW)
    name: str | None = Field(default=None, max_length=200)
    role: str | None = Field(default=None, pattern=_ROLE)
    expected_role: str | None = Field(default=None, pattern=_ROLE)
    is_active: StrictBool | None = None

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def has_change(self):
        if all(value is None for value in (self.name, self.role, self.is_active)):
            raise ValueError("At least one reviewed user change is required")
        return self


class DeleteIntent(_StrictModel):
    expected_review_token: str = Field(pattern=_REVIEW)


class PrimaryMembershipIntent(_StrictModel):
    tenant_id: UUID
    role: str = Field(pattern=_MEMBER_ROLE)
    expected_review_token: str = Field(pattern=r"^pm1:[0-9a-f]{64}$")


_INTENTS = {"create": CreateIntent, "update": UpdateIntent, "delete": DeleteIntent,
            "primary_membership": PrimaryMembershipIntent}


def canonical_user_operation_intent(kind: str, intent: dict) -> dict:
    """Normalize only the existing mutation fields; never accept arbitrary audit metadata."""
    model = _INTENTS[kind].model_validate(intent)
    data = model.model_dump(mode="json", exclude_none=kind == "update")
    if kind == "create":
        data["email"] = data["email"].strip().lower()
    return data


class PrepareUserOperationRequest(_StrictModel):
    kind: OperationKind
    target_user_id: UUID | None = None
    intent: dict[str, Any]
    password: str | None = Field(default=None, min_length=8, max_length=MAX_PASSWORD_LENGTH, repr=False, exclude=True)

    @model_validator(mode="after")
    def validate_intent(self):
        self.intent = canonical_user_operation_intent(self.kind, self.intent)
        if self.kind == "create":
            if self.target_user_id is not None or self.password is None:
                raise ValueError("Creation requires a password and no existing target")
        elif self.target_user_id is None or self.password is not None:
            raise ValueError("This operation requires an exact target and no password")
        return self


class UserOperationView(_StrictModel):
    operation_id: UUID
    kind: OperationKind
    target_user_id: UUID | None
    intent: dict[str, Any]
    state: Literal["prepared", "committed", "cancelled"]
    prepared_at: datetime
    expires_at: datetime
    dispatch_eligible: bool
    terminal_audit_event_id: UUID | None = None
    receipt: dict[str, Any] | None = None


class UserOperationList(_StrictModel):
    items: list[UserOperationView]
    next_cursor: str | None


@dataclass(frozen=True)
class PreparedUserOperation:
    operation_id: UUID
    actor_id: UUID
    kind: str
    target_user_id: UUID | None
    intent: dict
    digest: str
    prepared_at: datetime
    expires_at: datetime
    replay_receipt: dict | None = None


def _error(status_code: int, code: str, message: str, **details):
    return HTTPException(status_code, detail={"code": code, "message": message, **details})


def _unavailable():
    return _error(503, "user_operation_unavailable", "User operation recovery is unavailable. Do not resend the change.")


def _uuid(value):
    parsed = UUID(str(value))
    if not isinstance(value, UUID) and value != str(parsed):
        raise ValueError("Noncanonical operation UUID")
    return parsed


def _time(value):
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Operation timestamps must have a timezone")
    return parsed


def _metadata(row):
    data = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
    if not isinstance(data, dict) or type(data.get("user_operation_version")) is not int or data["user_operation_version"] != 1:
        raise ValueError("Unsupported operation metadata")
    return data


def _decode_prepared(row) -> PreparedUserOperation:
    metadata = _metadata(row)
    kind = metadata["user_operation_kind"]
    intent = canonical_user_operation_intent(kind, metadata["user_operation_intent"])
    if intent != metadata["user_operation_intent"]:
        raise ValueError("Noncanonical stored intent")
    target = metadata["user_operation_target_user_id"]
    target = _uuid(target) if target is not None else None
    digest = metadata["user_operation_digest"]
    if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("Invalid stored digest")
    prepared_at = _time(metadata["user_operation_prepared_at"])
    expires_at = _time(metadata["user_operation_expires_at"])
    if expires_at - prepared_at != DISPATCH_WINDOW or (kind == "create") != (target is None):
        raise ValueError("Invalid stored operation scope")
    if row["action"] != PREPARED_ACTION or row["resource_type"] != "user":
        raise ValueError("Wrong preparation provenance")
    resource_id = _uuid(row["resource_id"]) if row["resource_id"] is not None else None
    if resource_id != target:
        raise ValueError("Preparation resource does not match target")
    return PreparedUserOperation(_uuid(row["event_id"]), _uuid(row["actor_id"]), kind, target,
                                 intent, digest, prepared_at, expires_at)


def _digest(actor_id, kind, target_user_id, intent, password, audit_logger):
    payload = {"domain": "admin-prepared-user-operation-v1", "actor_id": str(actor_id),
               "kind": kind, "target_user_id": str(target_user_id) if target_user_id else None,
               "intent": intent, "password": password}
    return hmac.new(audit_logger.signing_key.encode(),
                    json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(), hashlib.sha256).hexdigest()


def _validated_receipt(prepared, receipt, audit_event_id):
    # Lazy import: existing users.py imports these transaction helpers.
    from .users import CreateUserReceipt, DeleteUserReceipt, PrimaryMembershipReceipt, UpdateUserReceipt
    models = {"create": CreateUserReceipt, "update": UpdateUserReceipt,
              "delete": DeleteUserReceipt, "primary_membership": PrimaryMembershipReceipt}
    data = dict(receipt)
    data["audit_event_id"] = str(audit_event_id)
    model = models[prepared.kind].model_validate(data)
    result = model.model_dump(mode="json")
    if prepared.kind != "create" and result["operation_id"] != str(prepared.operation_id):
        raise ValueError("Receipt does not identify the exact prepared operation")
    target = str(prepared.target_user_id)
    if prepared.kind == "create":
        if result["request_id"] != str(prepared.operation_id) or result["intent"] != prepared.intent:
            raise ValueError("Creation receipt does not match preparation")
        if result["user"] is None or result["user"]["id"] != result["user_id"]:
            raise ValueError("Creation must retain its original user snapshot")
        if (any(result["user"][field] != prepared.intent[field] for field in ("name", "email", "role", "tenant_id"))
                or data["user"].get("is_active") is not True or data["user"].get("is_verified") is not True):
            raise ValueError("Created snapshot does not match the prepared active verified account")
    elif prepared.kind in ("update", "delete"):
        if result["id"] != target:
            raise ValueError("Receipt has the wrong target")
        if prepared.kind == "update" and any(result[field] != prepared.intent[field]
                for field in ("name", "role", "is_active") if field in prepared.intent):
            raise ValueError("Updated receipt does not match the requested changes")
    else:
        if (result["user"]["id"] != target or result["tenant_id"] != prepared.intent["tenant_id"]
                or result["user"]["tenant_id"] != prepared.intent["tenant_id"]
                or result["user"]["role"] != prepared.intent["role"]
                or result["membership"]["role"] != prepared.intent["role"]
                or result["membership"]["status"] != "active" or result["membership"]["is_primary"] is not True):
            raise ValueError("Membership receipt does not match preparation")
    return result


def user_operation_terminal_metadata(prepared: PreparedUserOperation, receipt: BaseModel | dict) -> dict:
    """Validate BEFORE the existing mutation audit INSERT; store no circular audit ID.

    Caller merges this into its existing audit metadata in the same transaction.
    Its normal response receives the actual UUID returned by log_in_transaction.
    A replay must return prepared.replay_receipt without another business write.
    """
    value = receipt.model_dump(mode="json") if isinstance(receipt, BaseModel) else dict(receipt)
    value = _validated_receipt(prepared, value, UUID(int=0))
    value.pop("audit_event_id", None)
    # Nested AdminUserItem audit ID is not an independent receipt of this write.
    if isinstance(value.get("user"), dict):
        value["user"].pop("audit_event_id", None)
    return {"user_operation_version": 1, "user_operation_id": str(prepared.operation_id),
            "user_operation_kind": prepared.kind, "user_operation_digest": prepared.digest,
            "user_operation_receipt": value}


async def _preparation(conn, actor_id, operation_id):
    row = await conn.fetchrow("""SELECT event_id,event_time,actor_id,action,resource_type,resource_id,metadata
        FROM audit_logs WHERE event_id=$1::uuid AND actor_id=$2::uuid
        AND action='user_operation_prepared' AND resource_type='user'""", str(operation_id), str(actor_id))
    if row is None:
        raise _error(404, "user_operation_not_found", "No prepared operation exists for this administrator.")
    return _decode_prepared(row)


async def _terminals(conn, actor_id, operation_ids):
    rows = await conn.fetch(f"""SELECT event_id,event_time,actor_id,action,resource_type,resource_id,metadata
        FROM audit_logs WHERE actor_id=$1::uuid AND {_VERSION_SQL}
        AND resource_type='user' AND metadata ? 'user_operation_id'
        AND action=ANY($2::text[]) AND metadata->>'user_operation_id'=ANY($3::text[])""",
        str(actor_id), list(TERMINAL_ACTIONS), [str(value) for value in operation_ids])
    result = {}
    for row in rows:
        operation_id = str(_uuid(_metadata(row)["user_operation_id"]))
        if operation_id in result:
            raise ValueError("Conflicting terminal operation evidence")
        result[operation_id] = row
    return result


def _view(prepared, terminal, now):
    state, receipt, audit_id = "prepared", None, None
    if terminal is not None:
        metadata = _metadata(terminal)
        if (_uuid(terminal["actor_id"]) != prepared.actor_id
                or _uuid(metadata["user_operation_id"]) != prepared.operation_id
                or metadata["user_operation_kind"] != prepared.kind
                or metadata["user_operation_digest"] != prepared.digest):
            raise ValueError("Terminal evidence does not match preparation")
        audit_id = _uuid(terminal["event_id"])
        if terminal["action"] == CANCELLED_ACTION:
            target = _uuid(terminal["resource_id"]) if terminal["resource_id"] is not None else None
            if terminal["resource_type"] != "user" or target != prepared.target_user_id or metadata.get("user_operation_receipt") is not None:
                raise ValueError("Invalid cancellation receipt")
            state = "cancelled"
        else:
            actions = {"create": ("user_created",), "update": ("user_updated",),
                       "delete": ("user_deleted", "user_deactivated"),
                       "primary_membership": ("user_primary_membership_configured",)}
            if terminal["action"] not in actions[prepared.kind] or terminal["resource_type"] != "user":
                raise ValueError("Wrong terminal action")
            receipt = _validated_receipt(prepared, metadata["user_operation_receipt"], audit_id)
            target = receipt["user_id"] if prepared.kind == "create" else str(prepared.target_user_id)
            if str(_uuid(terminal["resource_id"])) != target:
                raise ValueError("Wrong terminal resource")
            if prepared.kind == "delete" and (terminal["action"] == "user_deleted") != (receipt["outcome"] == "deleted"):
                raise ValueError("Wrong deletion outcome")
            state = "committed"
    return UserOperationView(operation_id=prepared.operation_id, kind=prepared.kind,
        target_user_id=prepared.target_user_id, intent=prepared.intent, state=state,
        prepared_at=prepared.prepared_at, expires_at=prepared.expires_at,
        dispatch_eligible=state == "prepared" and prepared.prepared_at <= now < prepared.expires_at,
        terminal_audit_event_id=audit_id, receipt=receipt)


async def require_prepared_operation(conn, *, actor_id, operation_id, kind, target_user_id,
                                     intent, audit_logger, password=None) -> PreparedUserOperation:
    """Call AFTER audit/admin locks and BEFORE any target mutation/provider effect.

    This rechecks current authority. The existing handler still owns its current
    target/review/last-admin validation. A committed replay is returned even after
    expiry; cancelled/missing/mismatched/expired intent never dispatches.
    """
    if not conn.is_in_transaction():
        raise RuntimeError("Prepared operations require the existing mutation transaction")
    await require_current_platform_admin(conn, actor_id)
    try:
        prepared = await _preparation(conn, actor_id, operation_id)
        canonical = canonical_user_operation_intent(kind, intent)
        target = UUID(str(target_user_id)) if target_user_id is not None else None
        digest = _digest(actor_id, kind, target, canonical, password, audit_logger)
        if prepared.kind != kind or prepared.target_user_id != target or not hmac.compare_digest(prepared.digest, digest):
            raise _error(409, "user_operation_mismatch", "This request does not match its prepared operation.")
        terminals = await _terminals(conn, actor_id, [prepared.operation_id])
        now = await conn.fetchval("SELECT clock_timestamp()")
        view = _view(prepared, terminals.get(str(prepared.operation_id)), now)
        if view.state == "cancelled":
            raise _error(409, "user_operation_cancelled", "This operation was cancelled before execution.")
        if view.state == "committed":
            return PreparedUserOperation(**{**prepared.__dict__, "replay_receipt": view.receipt})
        if not view.dispatch_eligible:
            raise _error(409, "user_operation_expired", "The dispatch window expired. Cancel this operation before preparing a new decision.")
        return prepared
    except HTTPException:
        raise
    except (ValueError, KeyError, TypeError, ValidationError) as exc:
        raise _unavailable() from exc


def _cursor(row):
    payload = json.dumps([row["event_time"].isoformat(), str(row["event_id"])], separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def _decode_cursor(value):
    if value is None:
        return None, None
    try:
        data = json.loads(base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True))
        if not isinstance(data, list) or len(data) != 2:
            raise ValueError("Invalid cursor shape")
        return _time(data[0]), str(_uuid(data[1]))
    except (ValueError, TypeError, KeyError) as exc:
        raise _error(422, "user_operation_cursor_invalid", "Invalid recovery pagination cursor.") from exc


async def _list(conn, actor_id, state, limit, cursor):
    cursor_time, cursor_id = _decode_cursor(cursor)
    rows = await conn.fetch(f"""SELECT p.event_id,p.event_time,p.actor_id,p.action,p.resource_type,p.resource_id,p.metadata
        FROM audit_logs p WHERE p.actor_id=$1::uuid AND p.action='user_operation_prepared'
        AND p.resource_type='user' AND p.{_VERSION_SQL}
        AND ($2::timestamptz IS NULL OR (p.event_time,p.event_id)<($2,$3::uuid))
        AND ($4='all' OR ($4='terminal')=EXISTS(SELECT 1 FROM audit_logs t
            WHERE t.actor_id=p.actor_id AND t.{_VERSION_SQL} AND t.action=ANY($5::text[])
            AND t.resource_type='user' AND t.metadata ? 'user_operation_id'
            AND t.metadata->>'user_operation_id'=p.event_id::text))
        ORDER BY p.event_time DESC,p.event_id DESC LIMIT $6""",
        str(actor_id), cursor_time, cursor_id, state, list(TERMINAL_ACTIONS), limit + 1)
    page = rows[:limit]
    prepared = [_decode_prepared(row) for row in page]
    terminals = await _terminals(conn, actor_id, [item.operation_id for item in prepared]) if page else {}
    now = await conn.fetchval("SELECT clock_timestamp()")
    return UserOperationList(items=[_view(item, terminals.get(str(item.operation_id)), now) for item in prepared],
                             next_cursor=_cursor(page[-1]) if len(rows) > limit else None)


@router.post("/users/operations", response_model=UserOperationView, status_code=201)
async def prepare_user_operation(body: PrepareUserOperationRequest,
    admin_user: CurrentUser = Depends(require_platform_admin), db_client: Client = Depends(get_db_client),
    audit_logger: AuditLogger = Depends(get_audit_logger)):
    try:
        async with acquire_with_tenant(db_client.pool, None) as conn:
            await audit_logger.lock_transaction(conn)
            await lock_user_administration(conn)
            await require_current_platform_admin(conn, admin_user.id)
            pending = await _list(conn, admin_user.id, "pending", 1, None)
            if pending.items:
                raise _error(409, "user_operation_pending", "Resolve your existing operation before preparing another change.",
                             operation_id=str(pending.items[0].operation_id))
            now = await conn.fetchval("SELECT clock_timestamp()")
            digest = _digest(admin_user.id, body.kind, body.target_user_id, body.intent, body.password, audit_logger)
            metadata = {"user_operation_version": 1, "user_operation_kind": body.kind,
                "user_operation_target_user_id": str(body.target_user_id) if body.target_user_id else None,
                "user_operation_intent": body.intent, "user_operation_digest": digest,
                "user_operation_prepared_at": now.isoformat(), "user_operation_expires_at": (now + DISPATCH_WINDOW).isoformat()}
            operation_id = await audit_logger.log_in_transaction(conn, event_type=AuditEvent.USER_UPDATED,
                actor_id=admin_user.id, actor_role="platform_admin", resource_type="user",
                resource_id=body.target_user_id, action=PREPARED_ACTION,
                description="Admin prepared an existing user management decision", metadata=metadata)
            prepared = PreparedUserOperation(operation_id, UUID(str(admin_user.id)), body.kind, body.target_user_id,
                body.intent, digest, now, now + DISPATCH_WINDOW)
            result = _view(prepared, None, now)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("User operation preparation unavailable")
        raise _unavailable() from exc


@router.get("/users/operations", response_model=UserOperationList)
async def list_user_operations(state: Literal["pending", "terminal", "all"] = "pending",
    limit: int = Query(25, ge=1, le=100), cursor: str | None = Query(None, max_length=512),
    admin_user: CurrentUser = Depends(require_platform_admin), db_client: Client = Depends(get_db_client)):
    try:
        async with acquire_with_tenant(db_client.pool, None) as conn:
            await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            await require_current_platform_admin(conn, admin_user.id)
            return await _list(conn, admin_user.id, state, limit, cursor)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("User operation discovery unavailable")
        raise _unavailable() from exc


@router.get("/users/operations/{operation_id}", response_model=UserOperationView)
async def get_user_operation(operation_id: UUID, admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client)):
    try:
        async with acquire_with_tenant(db_client.pool, None) as conn:
            await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            await require_current_platform_admin(conn, admin_user.id)
            prepared = await _preparation(conn, admin_user.id, operation_id)
            terminals = await _terminals(conn, admin_user.id, [operation_id])
            return _view(prepared, terminals.get(str(operation_id)), await conn.fetchval("SELECT clock_timestamp()"))
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("User operation read unavailable")
        raise _unavailable() from exc


@router.post("/users/operations/{operation_id}/cancel", response_model=UserOperationView)
async def cancel_user_operation(operation_id: UUID, admin_user: CurrentUser = Depends(require_platform_admin),
    db_client: Client = Depends(get_db_client), audit_logger: AuditLogger = Depends(get_audit_logger)):
    try:
        async with acquire_with_tenant(db_client.pool, None) as conn:
            await audit_logger.lock_transaction(conn)
            await lock_user_administration(conn)
            await require_current_platform_admin(conn, admin_user.id)
            prepared = await _preparation(conn, admin_user.id, operation_id)
            terminals = await _terminals(conn, admin_user.id, [operation_id])
            now = await conn.fetchval("SELECT clock_timestamp()")
            result = _view(prepared, terminals.get(str(operation_id)), now)
            if result.state == "prepared":
                metadata = {"user_operation_version": 1, "user_operation_id": str(operation_id),
                    "user_operation_kind": prepared.kind, "user_operation_digest": prepared.digest,
                    "user_operation_receipt": None}
                event_id = await audit_logger.log_in_transaction(conn, event_type=AuditEvent.USER_UPDATED,
                    actor_id=admin_user.id, actor_role="platform_admin", resource_type="user",
                    resource_id=prepared.target_user_id, action=CANCELLED_ACTION,
                    description="Admin cancelled a prepared user management decision", metadata=metadata)
                result = result.model_copy(update={"state": "cancelled", "dispatch_eligible": False,
                                                    "terminal_audit_event_id": event_id})
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("User operation cancellation unavailable")
        raise _unavailable() from exc
