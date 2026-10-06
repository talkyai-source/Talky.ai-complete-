"""The retired full-body injection budget is now catalog + atomic section reads."""
from types import SimpleNamespace

from app.domain.services.voice_pipeline.knowledge_tool import knowledge_system_addendum, run_knowledge_lookup
from app.services.scripts.knowledge.sections import (
    CATALOG_MAX_CHARS, SECTIONS_MAX_CHARS, build_section_catalog, render_catalog_page,
)


async def test_large_catalog_is_paged_without_injecting_source_bodies():
    catalog = build_section_catalog([
        {"id": str(i), "source_id": "guide", "source_version": 1, "version": 1,
         "heading": f"Topic {i} " + "navigation " * 8, "content": "PRIVATE SOURCE BODY"}
        for i in range(150)
    ], tenant_id="t1", campaign_id="c1", source_policy="call_snapshot")
    session = SimpleNamespace(tenant_id="t1", campaign_id="c1", _knowledge_catalog=catalog)
    first = render_catalog_page(catalog)
    assert first["status"] == "catalog" and first["next_offset"] is not None
    guide = knowledge_system_addendum(session)
    assert len(guide) <= CATALOG_MAX_CHARS + 1500
    assert "PRIVATE SOURCE BODY" not in guide
    await run_knowledge_lookup(session, {"catalog_offset": first["next_offset"]})
    assert session._knowledge_evidence["status"] == "catalog"
    assert session._knowledge_evidence["passages"] == []
    assert session._knowledge_grounding == []


async def test_oversized_authored_section_is_withheld_instead_of_truncating_conditions():
    content = "Details. " * SECTIONS_MAX_CHARS + "Excludes installation and tax."
    catalog = build_section_catalog([
        {"id": "price", "source_id": "guide", "source_version": 1, "version": 1,
         "heading": "Price", "content": content},
    ], tenant_id="t1", campaign_id="c1", source_policy="call_snapshot")
    session = SimpleNamespace(tenant_id="t1", campaign_id="c1", _knowledge_catalog=catalog)
    result = await run_knowledge_lookup(session, {"section_ids": [catalog.nodes[0]["section_id"]]})
    assert session._knowledge_evidence["status"] == "too_large"
    assert session._knowledge_evidence["passages"] == []
    assert session._knowledge_grounding == []
    assert "Details." not in result and "Excludes installation" not in result
