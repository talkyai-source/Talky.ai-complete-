"""Shared base model for admin API response schemas.

asyncpg returns native Python types straight from Postgres — ``UUID`` for
``uuid`` columns and ``datetime``/``date`` for timestamp columns. The admin
response models declare these fields as ``str`` (that's the JSON contract the
frontend consumes), but Pydantic v2 does **not** silently coerce ``UUID`` or
``datetime`` into ``str``. The result was that every admin *list* endpoint that
built a model directly from a DB row 500'd with::

    Input should be a valid string [type=string_type, input_value=UUID(...)]

``AdminResponseModel`` fixes that centrally: a ``mode="before"`` validator on
every field turns ``UUID`` and ``datetime``/``date`` into their canonical
string form before field validation runs. It is a no-op for values that are
already strings (or any other type), so inheriting models keep their existing
behaviour and simply stop rejecting raw DB scalars.
"""
from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, field_validator


class AdminResponseModel(BaseModel):
    """Base for admin response models that are built directly from DB rows."""

    @field_validator("*", mode="before")
    @classmethod
    def _coerce_pg_scalars(cls, value):
        if isinstance(value, UUID):
            return str(value)
        # datetime is a subclass of date, so this also covers timestamps.
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        return value
