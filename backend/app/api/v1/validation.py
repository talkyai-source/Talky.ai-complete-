"""HTTP input boundaries shared by local business-resource endpoints."""
from typing import Annotated
from uuid import UUID

from fastapi import HTTPException
from pydantic import AfterValidator, WithJsonSchema


MAX_PAGE_SIZE = 200
MAX_OFFSET = 10_000


def _resource_uuid(value: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("Must be a valid UUID.") from exc


# Preserve the string contract of existing service/adapter callers while
# validating HTTP path values before the handler can query the database.
ResourceUUID = Annotated[str, AfterValidator(_resource_uuid),
                         WithJsonSchema({"type": "string", "format": "uuid"})]


def pagination_offset(page: int, page_size: int) -> int:
    offset = (page - 1) * page_size
    if page < 1 or not 1 <= page_size <= MAX_PAGE_SIZE or offset > MAX_OFFSET:
        raise HTTPException(422, "Pagination exceeds the supported range.")
    return offset
