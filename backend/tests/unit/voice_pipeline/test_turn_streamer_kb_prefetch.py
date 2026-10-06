"""No serial retrieval/prefetch precedes the model: setup already pinned the catalog."""
from unittest.mock import AsyncMock

from tests.unit.test_model_driven_voice_turn import setup_turn


async def test_model_starts_from_prepared_catalog_without_turn_fetch(monkeypatch):
    load = AsyncMock(side_effect=AssertionError("Catalog loading belongs to call setup"))
    monkeypatch.setattr("app.services.scripts.knowledge.sections.load_section_catalog", load)
    service, session, rounds = setup_turn(monkeypatch, "Hello.", ["Hello, how can I help?"])
    response, _, _ = await service._stream_llm_and_tts(session)
    assert response == "Hello, how can I help?" and len(rounds) == 1
    assert "knowledge_catalog" in rounds[0][1]["system_prompt"]
    assert "Refunds" in rounds[0][1]["system_prompt"]
    assert "five working days" not in rounds[0][1]["system_prompt"]
    assert session._knowledge_evidence == {"status": "unavailable", "passages": []}
    load.assert_not_awaited()
