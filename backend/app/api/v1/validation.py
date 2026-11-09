"""HTTP input boundaries shared by local business-resource endpoints."""
from typing import Annotated
import unicodedata
from uuid import UUID

from fastapi import HTTPException
from pydantic import AfterValidator, Field, WithJsonSchema


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


def _storable_text(value: str) -> str:
    """QA F2-13: NUL and lone surrogates cannot be stored in PostgreSQL text and
    surfaced as 500s; other control characters are not text either. Tab and
    line breaks stay allowed for multi-line notes."""
    for char in value:
        if char not in "\t\n\r" and unicodedata.category(char) in ("Cc", "Cs"):
            raise ValueError("Text must not contain control characters.")
    return value


def bounded_text(max_length: int, *, min_length: int = 0):
    """A free-text field with a length limit that PostgreSQL can store."""
    return Annotated[str, Field(min_length=min_length, max_length=max_length),
                     AfterValidator(_storable_text)]


def pagination_offset(page: int, page_size: int) -> int:
    offset = (page - 1) * page_size
    if page < 1 or not 1 <= page_size <= MAX_PAGE_SIZE or offset > MAX_OFFSET:
        raise HTTPException(422, "Pagination exceeds the supported range.")
    return offset
