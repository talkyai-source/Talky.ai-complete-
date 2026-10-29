"""Tool schemas and argument validation. The model never supplies a tenant."""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchHelpArticlesArgs(_Args):
    query: str = Field(min_length=2, max_length=200)


class NoArgs(_Args):
    pass


class CampaignArgs(_Args):
    campaign: str = Field(min_length=1, max_length=100)


class CallArgs(_Args):
    call_id: UUID


ARG_MODELS: dict[str, type[_Args]] = {
    "search_help_articles": SearchHelpArticlesArgs,
    "get_account_overview": NoArgs,
    "get_campaign_status": CampaignArgs,
    "get_recent_call_issues": NoArgs,
    "get_call_details": CallArgs,
    "get_phone_numbers_status": NoArgs,
    "get_connectors_status": NoArgs,
    "find_open_tickets": NoArgs,
}
TOOL_NAMES: frozenset[str] = frozenset(ARG_MODELS)


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required,
                       "additionalProperties": False}}}


TOOL_SCHEMAS: list[dict[str, Any]] = [
    _tool("search_help_articles", "Search the help centre articles.",
          {"query": {"type": "string", "minLength": 2, "maxLength": 200}}, ["query"]),
    _tool("get_account_overview", "Plan, subscription, account status and minutes balance.", {}, []),
    _tool("get_campaign_status", "Status and current blocker of one campaign, by id or exact name.",
          {"campaign": {"type": "string", "minLength": 1, "maxLength": 100}}, ["campaign"]),
    _tool("get_recent_call_issues", "Failed call attempts in the last 24 hours, grouped by reason.", {}, []),
    _tool("get_call_details", "Outcome and failure reason of one call (no transcript).",
          {"call_id": {"type": "string", "format": "uuid"}}, ["call_id"]),
    _tool("get_phone_numbers_status", "Caller-ID numbers and whether they are verified.", {}, []),
    _tool("get_connectors_status", "Connected integrations and their status.", {}, []),
    _tool("find_open_tickets", "The customer's own open support tickets.", {}, []),
]


def validate_tool_call(name: str, raw_arguments: str | dict | None) -> tuple[_Args | None, str | None]:
    """(arguments, None) or (None, "unknown_tool" | "invalid_arguments")."""
    model = ARG_MODELS.get(name)
    if model is None:
        return None, "unknown_tool"
    raw: Any = raw_arguments
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raw = {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None, "invalid_arguments"
    if not isinstance(raw, dict):
        return None, "invalid_arguments"
    try:
        return model.model_validate(raw), None
    except ValidationError:
        return None, "invalid_arguments"
