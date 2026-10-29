"""Support request bodies. Unknown fields are rejected, so a client-supplied
``tenant_id`` (or any other field the contract does not name) is a 422."""
from __future__ import annotations

from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator
from pydantic_core import PydanticCustomError

from app.domain.services.support.vocabulary import Category, TicketPriority, TicketStatus

_ALLOWED_CONTROLS = "\n\t"


def _clean_text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    if any((ord(char) < 32 and char not in _ALLOWED_CONTROLS) or ord(char) == 127 for char in value):
        raise PydanticCustomError("invalid_text", "Control characters are not allowed")
    return value


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RequestOnly(_Body):
    request_id: UUID


class PageContext(BaseModel):
    """A hint from the page the customer is on; unknown keys are ignored."""
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    route: Optional[str] = Field(default=None, max_length=200)
    campaign_id: Optional[UUID] = None
    call_id: Optional[UUID] = None
    app_version: Optional[str] = Field(default=None, max_length=40)
    viewport: Optional[str] = Field(default=None, max_length=20)


class StartConversation(_Body):
    request_id: UUID
    page_context: PageContext = Field(default_factory=PageContext)


class ConversationMessage(_Body):
    request_id: UUID
    content: str = Field(min_length=1, max_length=2000)

    @field_validator("content")
    @classmethod
    def _clean(cls, value: str) -> str:
        return _clean_text(value)


class Feedback(_Body):
    request_id: UUID
    solved: StrictBool


class CreateTicket(_Body):
    request_id: UUID
    conversation_id: Optional[UUID] = None
    subject: str = Field(min_length=3, max_length=150)
    category: Category
    description: str = Field(min_length=10, max_length=5000)
    related_campaign_id: Optional[UUID] = None
    related_call_id: Optional[UUID] = None
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=5)

    @field_validator("subject", "description")
    @classmethod
    def _clean(cls, value: str) -> str:
        return _clean_text(value)

    @field_validator("subject")
    @classmethod
    def _one_line(cls, value: str) -> str:
        if "\n" in value or "\t" in value:
            raise PydanticCustomError("invalid_text", "The subject must be one line")
        return value


class TicketMessage(_Body):
    request_id: UUID
    body: str = Field(min_length=1, max_length=5000)
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=5)

    @field_validator("body")
    @classmethod
    def _clean(cls, value: str) -> str:
        return _clean_text(value)


class Csat(_Body):
    request_id: UUID
    score: StrictInt = Field(ge=1, le=5)
    comment: Optional[str] = Field(default=None, max_length=1000)

    @field_validator("comment")
    @classmethod
    def _clean(cls, value: Optional[str]) -> Optional[str]:
        return _clean_text(value) if value is not None else value


class StaffTicketPatch(_Body):
    request_id: UUID
    version: StrictInt = Field(ge=1)
    status: Optional[TicketStatus] = None
    priority: Optional[TicketPriority] = None
    category: Optional[Category] = None
    assignee_user_id: Optional[UUID] = None

    def changes(self) -> dict:
        """Only fields the client sent; ``assignee_user_id: null`` means unassign."""
        sent = self.model_fields_set
        changes = {name: getattr(self, name) for name in ("status", "priority", "category")
                   if name in sent and getattr(self, name) is not None}
        if "assignee_user_id" in sent:
            changes["assignee_user_id"] = self.assignee_user_id
        return changes


class StaffMessage(_Body):
    request_id: UUID
    body: str = Field(min_length=1, max_length=10000)
    internal: StrictBool = False
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=5)

    @field_validator("body")
    @classmethod
    def _clean(cls, value: str) -> str:
        return _clean_text(value)


class ArticleCreate(_Body):
    request_id: UUID
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,158}[a-z0-9]$")
    title: str = Field(min_length=1, max_length=200)
    body_md: str = Field(min_length=1, max_length=50000)
    category: Category
    published: StrictBool = True

    @field_validator("title", "body_md")
    @classmethod
    def _clean(cls, value: str) -> str:
        return _clean_text(value)


class ArticlePatch(_Body):
    request_id: UUID
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    body_md: Optional[str] = Field(default=None, min_length=1, max_length=50000)
    category: Optional[Category] = None
    published: Optional[StrictBool] = None

    @field_validator("title", "body_md")
    @classmethod
    def _clean(cls, value: Optional[str]) -> Optional[str]:
        return _clean_text(value) if value is not None else value


TicketScope = Literal["mine", "team"]
