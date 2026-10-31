"""Final structured reply: schema and strict validation of model output."""
from __future__ import annotations

import json
import re
from typing import Any

from app.domain.services.support.bot.types import Source
from app.domain.services.support.vocabulary import CATEGORY_IDS

ANSWER_MAX_CHARS = 1200
REASON_MAX_CHARS = 200

FINAL_PROPERTIES: dict[str, Any] = {
    "answer": {"type": "string"},
    "sources": {"type": "array", "items": {"type": "string"}},
    "resolution_prompt": {"type": "boolean"},
    "needs_clarification": {"type": "boolean"},
    "propose_escalation": {"type": "boolean"},
    "escalation_reason": {"type": "string"},
    "category": {"type": "string", "enum": sorted(CATEGORY_IDS)},
}

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class InvalidBotOutput(ValueError):
    pass


def _collapse(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return _clip(text, limit)


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    return (cut[:space] if space > limit // 2 else cut).rstrip()


def _answer_text(answer: str) -> str:
    """Keep line breaks (numbered steps) but tidy spaces and drop other control characters."""
    lines = [" ".join(_CONTROL.sub(" ", line).split()) for line in answer.replace("\r\n", "\n").split("\n")]
    text = "\n".join(lines).strip()
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return _clip(text, ANSWER_MAX_CHARS)


def parse_final_result(raw_text: str, *, allowed_sources: dict[str, Source]) -> dict[str, Any]:
    try:
        data = json.loads(raw_text or "")
    except (ValueError, TypeError) as exc:
        raise InvalidBotOutput("not_json") from exc
    if not isinstance(data, dict):
        raise InvalidBotOutput("not_object")
    answer = data.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        raise InvalidBotOutput("answer")
    flags: dict[str, bool] = {}
    for key in ("resolution_prompt", "needs_clarification", "propose_escalation"):
        value = data.get(key, False)
        if not isinstance(value, bool):
            raise InvalidBotOutput(key)
        flags[key] = value
    slugs = data.get("sources", [])
    if not isinstance(slugs, list):
        raise InvalidBotOutput("sources")
    sources: list[Source] = []
    for slug in slugs:
        if isinstance(slug, str) and slug in allowed_sources and allowed_sources[slug] not in sources:
            sources.append(allowed_sources[slug])
    reason = data.get("escalation_reason")
    if reason is not None and not isinstance(reason, str):
        raise InvalidBotOutput("escalation_reason")
    reason = _collapse(_CONTROL.sub(" ", reason or ""), REASON_MAX_CHARS) or None
    category = data.get("category")
    if not isinstance(category, str) or category not in CATEGORY_IDS:
        category = "other"
    return {"answer": _answer_text(answer), "sources": tuple(sources),
            "escalation_reason": reason, "category": category, **flags}
