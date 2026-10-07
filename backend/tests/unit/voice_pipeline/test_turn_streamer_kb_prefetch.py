"""No serial retrieval/prefetch precedes the model: setup already pinned the catalog."""
from unittest.mock import AsyncMock

from tests.unit.test_model_driven_voice_turn import setup_turn


async def test_model_starts_from_prepared_catalog_without_turn_fetch(monkeypatch):
    load = AsyncMock(side_effect=AssertionError("Catalog loading belongs to call setup"))
    monkeypatch.setattr("app.services.scripts.knowledge.sections.load_section_catalog", load)
    steps = []
    service, session, rounds = setup_turn(monkeypatch, "When is my refund?", steps)
    root = next(row["section_id"] for row in session._knowledge_catalog.nodes if row["id"] == "root")
    child = next(row["section_id"] for row in session._knowledge_catalog.nodes if row["id"] == "refund")
    steps.extend([{"catalog_parent": root}, {"section_ids": [child]}, "Refunds take five working days."])
    response, _, _ = await service._stream_llm_and_tts(session)
    assert response == steps[-1] and len(rounds) == 3
    assert "knowledge_catalog" in rounds[0][1]["system_prompt"]
    assert "Canada" in rounds[0][1]["system_prompt"]
    assert "Refunds" not in rounds[0][1]["system_prompt"]
    assert "five working days" not in rounds[0][1]["system_prompt"]
    assert "Refunds" in rounds[1][1]["extra_messages"][1]["content"]
    assert "five working days" not in rounds[1][1]["extra_messages"][1]["content"]
    assert session._knowledge_evidence["status"] == "available"
    assert "five working days" in session._knowledge_evidence["text"]
    assert "CAD, excluding tax" in session._knowledge_evidence["text"]
    load.assert_not_awaited()
