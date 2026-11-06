"""Tenant-owned call workflow state and its required atomic audit receipt."""
from __future__ import annotations

import hashlib
from typing import Any, Mapping
from uuid import UUID

from app.core.db_utils import acquire_with_tenant
from app.domain.services.audit_logger import AuditEvent, AuditLogger


_COLUMNS = """tenant_id, call_id, lead_type, notes, contact, interest,
              next_step, completed, updated_by, created_at, updated_at"""
_TEXT_COLUMNS = ("notes", "contact", "interest", "next_step")
_STATE_COLUMNS = ("lead_type", *_TEXT_COLUMNS, "completed")


class CallWorkflowNotFound(ValueError):
    """The call does not exist in the authenticated tenant."""


def _public(row: Mapping[str, Any]) -> dict:
    return {
        "leadType": row["lead_type"],
        "notes": row["notes"],
        "form": {"contact": row["contact"], "interest": row["interest"],
                 "nextStep": row["next_step"], "completed": row["completed"]},
        "updatedAt": row["updated_at"].isoformat(),
    }


def _audit_state(row: Mapping[str, Any] | None) -> dict | None:
    if row is None:
        return None
    # The current text lives in call_workflows. The immutable audit trail
    # proves which values changed without duplicating contact/note PII.
    return {
        "lead_type": row["lead_type"], "completed": row["completed"],
        "updated_by": str(row["updated_by"]) if row["updated_by"] else None,
        "text": {column: {"sha256": hashlib.sha256(row[column].encode("utf-8")).hexdigest(),
                          "length": len(row[column])}
                 for column in _TEXT_COLUMNS},
    }


class CallWorkflowService:
    def __init__(self, pool, *, audit: AuditLogger):
        self.pool = pool
        self.audit = audit

    @staticmethod
    async def _require_call(conn, tenant_id: UUID, call_id: UUID, *, lock=False):
        row = await conn.fetchrow(
            "SELECT id FROM calls WHERE tenant_id = $1 AND id = $2"
            + (" FOR UPDATE" if lock else ""),
            tenant_id, call_id,
        )
        if row is None:
            raise CallWorkflowNotFound("Call not found")

    async def get(self, *, tenant_id: str, call_id: str) -> dict | None:
        tenant, call = UUID(str(tenant_id)), UUID(str(call_id))
        async with acquire_with_tenant(self.pool, str(tenant)) as conn:
            await self._require_call(conn, tenant, call)
            row = await conn.fetchrow(
                f"SELECT {_COLUMNS} FROM call_workflows WHERE tenant_id = $1 AND call_id = $2",
                tenant, call,
            )
        return _public(row) if row is not None else None

    async def save(self, *, tenant_id: str, call_id: str, actor_id: str,
                   actor_role: str, workflow: Mapping[str, Any], create_only=False) -> dict:
        """Save validated HTTP data, or import only when no server entry exists.

        The tenant-owned call lock serializes first writes as well as updates.
        A browser import waiting behind a normal save returns the server row.
        """
        tenant, call, actor = UUID(str(tenant_id)), UUID(str(call_id)), UUID(str(actor_id))
        form = workflow["form"]
        state = {"lead_type": workflow["leadType"], "notes": workflow["notes"],
                 "contact": form["contact"], "interest": form["interest"],
                 "next_step": form["nextStep"], "completed": form["completed"]}
        values = (tenant, call, *(state[column] for column in _STATE_COLUMNS), actor)
        async with acquire_with_tenant(self.pool, str(tenant), user_id=str(actor)) as conn:
            await self.audit.lock_transaction(conn)
            await self._require_call(conn, tenant, call, lock=True)
            before = await conn.fetchrow(
                f"SELECT {_COLUMNS} FROM call_workflows "
                "WHERE tenant_id = $1 AND call_id = $2 FOR UPDATE",
                tenant, call,
            )
            if before is not None and (create_only or all(before[key] == value for key, value in state.items())):
                return {"workflow": _public(before), "created": False}

            if before is None:
                row = await conn.fetchrow(
                    f"""INSERT INTO call_workflows
                            (tenant_id, call_id, lead_type, notes, contact, interest,
                             next_step, completed, updated_by)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                        RETURNING {_COLUMNS}""",
                    *values,
                )
            else:
                row = await conn.fetchrow(
                    f"""UPDATE call_workflows SET lead_type = $3, notes = $4,
                            contact = $5, interest = $6, next_step = $7, completed = $8,
                            updated_by = $9, updated_at = NOW()
                        WHERE tenant_id = $1 AND call_id = $2 RETURNING {_COLUMNS}""",
                    *values,
                )
            if row is None:
                raise RuntimeError("Call workflow change was not persisted")
            await self.audit.log_in_transaction(
                conn, event_type=AuditEvent.CONFIG_CHANGED, action="call.workflow.saved",
                actor_id=actor, actor_role=actor_role, tenant_id=tenant,
                resource_type="call_workflow", resource_id=call,
                before_state=_audit_state(before), after_state=_audit_state(row),
                metadata={"create_only": bool(create_only)},
            )
            result = {"workflow": _public(row), "created": before is None}
        return result
