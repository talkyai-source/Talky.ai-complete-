"""Whole-catalog outline in the prompt (test call c8df9107, 2026-10-08).

The prompt held only the catalog roots: one heading for a one-document
campaign. Reaching "Lahore to Karachi" took four tool calls (roots, document,
city group, read) against a three-round budget, so the agent said "let me
check" twice and then spoke the section id it was about to open.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.domain.services.voice_pipeline import knowledge_tool as kt
from app.domain.services.voice_pipeline.speech_guard import guard_spoken_sentence
from app.services.scripts.knowledge.sections import build_section_catalog, run_section_request


def _node(i, path, heading, content="", parent=None):
    return {"id": str(i), "source_id": "fares", "source_version": 1, "version": 1,
            "path": path, "depth": path.count(".") + 1, "parent_id": parent,
            "heading": heading, "content": content}


def _fare_sheet_session():
    nodes = [
        _node(1, "1", "Safar Coaches discounted fares offer", "Fares are one way, per seat."),
        _node(2, "1.1", "Discounted fares from Lahore", "Routes departing Lahore.", parent="1"),
        _node(3, "1.1.1", "Lahore to Karachi and Karachi to Lahore",
              "Sleeper: now 8,550 rupees, was 9,500 rupees.", parent="2"),
        _node(4, "1.2", "Discounted fares from Karachi", "Routes departing Karachi.", parent="1"),
        _node(5, "1.2.1", "Karachi to Multan and Multan to Karachi",
              "Sleeper: now 6,120 rupees, was 6,800 rupees.", parent="4"),
    ]
    catalog = build_section_catalog(nodes, tenant_id="t", campaign_id="c", source_policy="call_snapshot")
    return SimpleNamespace(tenant_id="t", campaign_id="c", _knowledge_catalog=catalog), catalog


def _id_for(catalog, heading):
    return next(row["section_id"] for row in catalog.nodes if row["heading"] == heading)


def test_outline_lists_every_section_with_its_path_once():
    _, catalog = _fare_sheet_session()
    outline = kt.section_outline(catalog)
    route = _id_for(catalog, "Lahore to Karachi and Karachi to Lahore")
    assert f"{route}: Discounted fares from Lahore > Lahore to Karachi and Karachi to Lahore" in outline
    assert len(outline.splitlines()) == 5
    # The single document title is not repeated on every line.
    assert outline.count("Safar Coaches discounted fares offer") == 1


def test_the_prompt_shows_the_whole_outline_so_one_read_reaches_the_fare():
    session, catalog = _fare_sheet_session()
    addendum = kt.knowledge_system_addendum(session)
    route = _id_for(catalog, "Lahore to Karachi and Karachi to Lahore")
    assert route in addendum and "no browsing is needed" in addendum
    assert "Never say a section_id aloud" in addendum
    # The id the model sees is exactly readable in one tool call.
    result = run_section_request(catalog, {"section_ids": [route]})
    assert result["status"] == "available" and "8,550 rupees" in result["text"]


def test_a_catalog_too_big_for_the_outline_keeps_root_browsing():
    nodes = [{"id": str(i), "source_id": "manual", "source_version": 1, "version": "1",
              "heading": f"Section {i}: " + "label " * 30, "content": f"Body {i}."} for i in range(500)]
    catalog = build_section_catalog(nodes, tenant_id="t", campaign_id="c", source_policy="call_snapshot")
    session = SimpleNamespace(tenant_id="t", campaign_id="c", _knowledge_catalog=catalog)
    assert kt.section_outline(catalog) is None
    addendum = kt.knowledge_system_addendum(session)
    assert "no browsing is needed" not in addendum
    assert '"status": "catalog"' in addendum or "next_offset" in addendum


def test_a_section_id_is_never_spoken():
    session = SimpleNamespace(_voice_action_results={}, conversation_history=[])
    assert guard_spoken_sentence(session, "Let me check that now. k11a07977_13") == "Let me check that now."
    assert guard_spoken_sentence(session, "k11a07977_13") == ""
    assert guard_spoken_sentence(session, "The Sleeper fare is 8,550 rupees.") == "The Sleeper fare is 8,550 rupees."
