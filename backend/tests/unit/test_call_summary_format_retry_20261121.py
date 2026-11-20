"""A summary must not be lost because an OPTIONAL part is missing (2026-11-20).

Production: Groq answered 400 json_validate_failed ("missing properties:
'business_details'") for a draft that said the caller wanted a callback; the
summary was thrown away. Now the schema only requires headline/outcome, and a
json_validate_failed gets exactly ONE retry (failed_generation salvaged locally,
else one second LLM call).
"""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.domain.services.call_summary import summarizer as mod
from app.domain.services.call_summary.summarizer import (
    SUMMARY_UNAVAILABLE_HEADLINE,
    summarize_transcript,
)

_DRAFT = {
    "headline": "No clear interest — user requested a callback",
    "outcome": "callback",
    "qualification_status": "unknown",
    "next_step": "Call back tomorrow",
}


class _ValidateFailed(Exception):
    """Shaped like groq.BadRequestError for the production 400."""

    def __init__(self, failed_generation: str):
        self.body = {
            "error": {
                "message": "Generated JSON does not match the expected schema. "
                "missing properties: 'business_details'",
                "type": "invalid_request_error",
                "code": "json_validate_failed",
                "failed_generation": failed_generation,
            }
        }
        super().__init__(f"Error code: 400 - {self.body}")


def _ok(content: dict | str):
    text = content if isinstance(content, str) else json.dumps(content)
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


def _client(*effects):
    create = AsyncMock(side_effect=list(effects))
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return client, create


async def _run(*effects, call_id="call-1"):
    client, create = _client(*effects)
    with patch.object(mod, "AsyncGroq", return_value=client):
        result = await summarize_transcript("Agent: Hi\nCaller: call me back", call_id=call_id)
    return result, create


async def test_failed_generation_without_business_details_is_salvaged_without_second_call():
    result, create = await _run(_ValidateFailed(json.dumps(_DRAFT)))
    assert create.await_count == 1
    assert result["headline"] != SUMMARY_UNAVAILABLE_HEADLINE
    assert result["outcome"] == "callback"
    assert result["business_details"] == []  # default filled


async def test_unusable_failed_generation_makes_exactly_one_second_call_with_validator_message():
    result, create = await _run(_ValidateFailed("{not json"), _ok(_DRAFT))
    assert create.await_count == 2
    second_user = create.await_args_list[1].kwargs["messages"][1]["content"]
    assert "missing properties: 'business_details'" in second_user
    assert result["outcome"] == "callback"


async def test_both_attempts_fail_logs_final_once_and_makes_no_third_call(caplog):
    with caplog.at_level(logging.WARNING):
        result, create = await _run(_ValidateFailed("{bad"), _ValidateFailed("{bad"), _ok(_DRAFT))
    assert create.await_count == 2
    assert result["headline"] == SUMMARY_UNAVAILABLE_HEADLINE
    finals = [r for r in caplog.records if r.getMessage().startswith("call_summary_failed_final call=call-1 reason=")]
    assert len(finals) == 1


async def test_normal_success_is_one_call_and_unchanged():
    full = {**_DRAFT, "business_details": [{"field_key": "timeline", "value": "asap", "source_quote": "asap"}]}
    result, create = await _run(_ok(full))
    assert create.await_count == 1
    assert result["business_details"] == full["business_details"]
    assert result["headline"] == full["headline"]


async def test_other_provider_errors_are_not_retried():
    result, create = await _run(RuntimeError("network"), _ok(_DRAFT))
    assert create.await_count == 1
    assert result["headline"] == SUMMARY_UNAVAILABLE_HEADLINE


async def test_request_response_format_is_unchanged_strict_with_every_key_required():
    from app.infrastructure.llm.structured_output import response_format_for, summariser_model

    expected = response_format_for(
        summariser_model(), properties=mod._schema_properties([]), name="call_summary"
    )
    _, create = await _run(_ok(_DRAFT))
    sent = create.await_args.kwargs["response_format"]
    assert json.dumps(sent, sort_keys=True) == json.dumps(expected, sort_keys=True)
    assert sent["json_schema"]["strict"] is True
    assert sent["json_schema"]["schema"]["required"] == list(mod.EMPTY_SUMMARY)


async def test_store_persists_the_salvaged_summary():
    """Through generate_and_store: the callback summary is written, not dropped."""
    from tests.unit.call_summary import test_store as ts

    conn = ts._make_conn({"transcript": "Agent: Hi\nCaller: call me back", "summary_json": None})
    client, create = _client(_ValidateFailed(json.dumps(_DRAFT)))
    with ts._patch_acquire(conn), patch.object(mod, "AsyncGroq", return_value=client):
        result = await ts.generate_and_store(None, ts._TENANT_ID, ts._CALL_ID)
    assert create.await_count == 1
    assert result["outcome"] == "callback"
    write = next(c for c in conn.execute.await_args_list if "UPDATE calls" in c.args[0] and "summary_json" in c.args[0])
    assert json.loads(write.args[2])["outcome"] == "callback"
    assert json.loads(write.args[2])["business_details"] == []
