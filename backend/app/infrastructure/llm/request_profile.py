"""Content-free evidence of the final traditional LLM request dispatch attempt."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from dataclasses import asdict, is_dataclass
from enum import Enum
from types import SimpleNamespace

logger = logging.getLogger(__name__)


def _json_default(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="python", exclude_none=True)
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, SimpleNamespace):
        return vars(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    raise TypeError("Unsupported LLM request value")


def record_traditional_request(
    *, provider: str, request: dict, instructions: str | None,
    configured_temperature: float, configured_max_tokens: int,
) -> dict:
    """Hash full input privately, then emit only the fixed settings allowlist.

    Called immediately before provider I/O, including continuation requests.
    It proves preparation/attempt, never provider acceptance or spoken output.
    """
    profile = {
        "event": "traditional_llm_request",
        "request_state": "dispatch_attempt",
        "provider": provider,
        "model": request.get("model"),
        "instructions_sha256": hashlib.sha256((instructions or "").encode("utf-8")).hexdigest(),
        "request_envelope_sha256": None,
        "digest_status": "unavailable",
        "configured_temperature": configured_temperature,
        "configured_visible_token_target": configured_max_tokens,
    }
    try:
        encoded = json.dumps(request, default=_json_default, sort_keys=True, separators=(",", ":"))
        normalized = json.loads(encoded)
        config = normalized.get("config") or normalized
        thinking = config.get("thinking_config") or {}
        tools = config.get("tools") or []
        tool_names = []
        for tool in tools:
            declarations = tool.get("function_declarations") or [tool.get("function") or tool]
            tool_names.extend(item["name"] for item in declarations if item.get("name"))
        profile.update({
            "request_envelope_sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            "digest_status": "complete",
            "effective_temperature": config.get("temperature"),
            "seed": config.get("seed"),
            "reasoning_effort": config.get("reasoning_effort"),
            "thinking_level": thinking.get("thinking_level"),
            "thinking_budget": thinking.get("thinking_budget"),
            "wire_token_ceiling": config.get("max_completion_tokens", config.get("max_output_tokens")),
            "tool_names": sorted(set(tool_names)),
        })
    except Exception:
        # SDK additions must not break inference because diagnostic encoding
        # changed. Never print the object or exception (either may contain PII).
        pass
    logger.info("traditional_llm_request %s", json.dumps(profile, sort_keys=True))
    return profile
