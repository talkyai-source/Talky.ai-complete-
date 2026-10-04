"""Small platform intake: a committed enquiry is the acceptance receipt.

There is no tenant assignment, mail send or background side effect. Public
callers may submit bounded content; only authorized platform operators may
read/change the intake through the separate admin endpoints.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal
from uuid import UUID

import asyncpg
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from pydantic_core import PydanticCustomError

from app.core.db_utils import acquire_with_tenant

EnquiryStatus = Literal["new", "handled"]


class ContactEnquiryInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    request_id: UUID
    name: str = Field(min_length=1, max_length=120, strict=True)
    email: EmailStr = Field(max_length=254)
    company: str = Field(default="", max_length=120, strict=True)
    message: str = Field(min_length=1, max_length=500, strict=True)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value):
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("message", mode="before")
    @classmethod
    def normalize_newlines(cls, value):
        return value.replace("\r\n", "\n").replace("\r", "\n") if isinstance(value, str) else value

    @field_validator("name", "company", "message")
    @classmethod
    def reject_controls(cls, value: str, info):
        allowed = "\n\t" if info.field_name == "message" else ""
        if any((ord(char) < 32 or ord(char) == 127) and char not in allowed for char in value):
            raise PydanticCustomError("invalid_text", "Control characters are not allowed")
        return value

    def payload_hash(self) -> str:
        canonical = self.model_dump(mode="json", exclude={"request_id"})
        return hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False,
                                         separators=(",", ":")).encode("utf-8")).hexdigest()


class EnquiryReceipt(BaseModel):
    status: Literal["accepted"] = "accepted"
    receipt_id: UUID
    accepted_at: datetime


class EnquiryItem(BaseModel):
    id: UUID
    name: str
    email: str
    company: str
    message: str
    status: EnquiryStatus
    created_at: datetime
    handled_at: datetime | None


class EnquiryPage(BaseModel):
    items: list[EnquiryItem]
    total: int
    limit: int
    offset: int


class EnquiryStatusInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: EnquiryStatus


class EnquiryConflict(Exception):
    """The same request identity was reused for different normalized content."""


_ITEM_COLUMNS = "id, name, email, company, message, status, created_at, handled_at"


async def submit_enquiry(pool: asyncpg.Pool, body: ContactEnquiryInput) -> tuple[dict, bool]:
    payload_hash = body.payload_hash()
    # This narrowly validated insert is the sole anonymous operation over the
    # platform-only table. No tenant or privilege context comes from the input.
    async with acquire_with_tenant(pool, None, timeout=5) as conn:
        row = await conn.fetchrow(
            """INSERT INTO public_contact_enquiries
                   (id, payload_hash, name, email, company, message)
               VALUES ($1, $2, $3, $4, $5, $6)
               ON CONFLICT (id) DO NOTHING
               RETURNING id, created_at, payload_hash""",
            body.request_id, payload_hash, body.name, str(body.email), body.company, body.message,
        )
        created = row is not None
        if not created:
            # INSERT waits for a concurrent conflicting transaction. A new
            # statement then sees its committed receipt at READ COMMITTED.
            row = await conn.fetchrow(
                "SELECT id, created_at, payload_hash FROM public_contact_enquiries WHERE id = $1",
                body.request_id,
            )
            if row is None:
                raise RuntimeError("Enquiry receipt unavailable after conflict")
            if row["payload_hash"] != payload_hash:
                raise EnquiryConflict
        receipt = {"status": "accepted", "receipt_id": row["id"], "accepted_at": row["created_at"]}
    # Return only after the transaction manager has successfully committed.
    return receipt, created


async def list_enquiries(pool: asyncpg.Pool, *, status: EnquiryStatus | None,
                         limit: int, offset: int) -> dict:
    # API dependency has already required platform_admin. RLS admits only
    # explicit platform context, never an ordinary tenant's null ownership.
    async with acquire_with_tenant(pool, None, timeout=5) as conn:
        await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM public_contact_enquiries WHERE ($1::text IS NULL OR status = $1)", status,
        )
        rows = await conn.fetch(
            f"""SELECT {_ITEM_COLUMNS} FROM public_contact_enquiries
                WHERE ($1::text IS NULL OR status = $1)
                ORDER BY created_at DESC, id DESC LIMIT $2 OFFSET $3""", status, limit, offset,
        )
    return {"items": [dict(row) for row in rows], "total": total, "limit": limit, "offset": offset}


async def update_enquiry_status(pool: asyncpg.Pool, enquiry_id: UUID,
                                status: EnquiryStatus, operator_id: UUID) -> dict | None:
    async with acquire_with_tenant(pool, None, timeout=5) as conn:
        row = await conn.fetchrow(
            f"""UPDATE public_contact_enquiries
                SET status = $2,
                    handled_at = CASE WHEN $2 = 'new' THEN NULL
                                      WHEN status = 'handled' THEN handled_at ELSE NOW() END,
                    handled_by = CASE WHEN $2 = 'new' THEN NULL
                                      WHEN status = 'handled' THEN handled_by ELSE $3 END
                WHERE id = $1 RETURNING {_ITEM_COLUMNS}""", enquiry_id, status, operator_id,
        )
    return dict(row) if row else None
