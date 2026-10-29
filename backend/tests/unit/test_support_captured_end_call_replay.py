"""Replay retained failed-model evidence through the full traditional finisher.

No provider is contacted. This checks caller authorization and control-token
handling only; Q43's unsupported sales-handoff promise remains a quality failure.
Audio, telephony, and downstream provider outcomes are mocked.
"""
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.domain.models.conversation import MessageRole
from tests.unit.test_voice_pipeline_service import _make_service_for_disposition, _make_session


def _captured_response(case_id):
    evidence = Path(__file__).resolve().parents[3] / (
        "docs/sessions/artifacts/groq-sections-20261028/rerun-3dcd2f0f-result.json"
    )
    result = json.loads(evidence.read_text(encoding="utf-8"))
    assert result["production_approved"] is False
    case = next(row for row in result["cases"] if row["id"] == case_id)
    assert case["human_approval"] is None
    responses = []
    for request in result["requests"]:
        if not any(message.get("role") == "user" and case["original_question"] in message.get("content", "")
                   for message in request["body"]["messages"]):
            continue
        chunks = [json.loads(line[6:]) for line in request["response_stream"].splitlines()
                  if line.startswith("data: ") and line != "data: [DONE]"]
        tokens = [choice.get("delta", {}).get("content") or ""
                  for chunk in chunks for choice in chunk.get("choices", [])]
        if "[[END_CALL]]" in "".join(tokens):
            responses.append(tokens)
    assert len(responses) == 1, "The retained unauthorized close must remain identifiable."
    return case, responses[0]


@pytest.mark.parametrize("case_id", ["Q43", "H13"])
async def test_captured_unauthorized_close_cannot_disconnect_in_full_turn_finisher(monkeypatch, case_id):
    case, tokens = _captured_response(case_id)
    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    service = _make_service_for_disposition(tokens)
    service._shutdown_session_for_end_action = AsyncMock()
    session = _make_session()
    session.campaign_id = "campaign-123"
    session.turn_id = 4
    session.current_user_input = case["original_question"]

    await service.handle_turn_end(session, AsyncMock())

    service._shutdown_session_for_end_action.assert_not_awaited()
    assert not getattr(session, "_end_call_requested", False)
    assert not getattr(session, "_caller_opted_out", False)
    spoken = " ".join(message.content for message in session.conversation_history
                      if message.role == MessageRole.ASSISTANT)
    assert "END_CALL" not in spoken
    assert spoken == case["answer"]
    if case_id == "Q43":
        # This replay must not hide or rewrite the model's retained failure.
        assert "connect you with a member of our sales team" in spoken
