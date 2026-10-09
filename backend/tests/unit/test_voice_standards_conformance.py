"""Conformance index for docs/standards/voice-agent-standards.md.

Standards with their own test files: CMP-1 test_model_verified_opt_out.py ·
HAL-2 test_figure_grounding.py · HAL-4, HAL-5, LAT-7 test_knowledge_outline.py ·
HAL-3 test_speech_guard.py · ARC-1, LAT-3 (policy budgets) test_prompt_policies.py.
This file covers the rest that can be checked offline.
"""
from __future__ import annotations

from app.domain.services.voice_tuning import VoiceTuning
from app.infrastructure.stt import deepgram_flux
from app.services.scripts.prompts import compose_prompt
from app.services.scripts.prompts.build import build_turn_prompt
from app.services.scripts.prompts.policies import load_policy
from app.services.scripts.knowledge.sections import build_section_catalog
from app.domain.services.voice_pipeline.knowledge_tool import knowledge_system_addendum
from app.domain.services.voice_pipeline.action_tools import action_tool_system_addendum
from tests.unit.test_prompt_composer import LEAD_GEN_SLOTS
from tests.unit.test_model_driven_voice_turn import setup_turn


def test_tt1_default_turn_thresholds_are_inside_deepgrams_ranges():
    t = VoiceTuning()
    assert 0.5 <= t.stt_eot_threshold <= 1.0
    assert 500 <= t.stt_eot_timeout_ms <= 60000
    assert t.stt_eager_eot_threshold is None or 0.3 <= t.stt_eager_eot_threshold <= t.stt_eot_threshold


def test_tt2_capture_mode_waits_through_spelled_details():
    assert deepgram_flux.CAPTURE_EOT_TIMEOUT_MS >= 7000
    assert deepgram_flux.CAPTURE_EOT_THRESHOLD >= 0.85


def test_tt5_audio_frames_are_within_the_recommended_size():
    assert 20 <= deepgram_flux.FLUX_OPTIMAL_CHUNK_MS <= 80


def _standard_turn_prompt() -> str:
    base = compose_prompt("lead_gen", "Alex", "Acme Co", LEAD_GEN_SLOTS, direction="outbound")
    nodes = [{"id": str(i), "source_id": "s1", "source_version": 1, "version": "1", "path": path,
              "parent_id": None if i == 0 else "0", "heading": heading, "content": "x" * 300}
             for i, (path, heading) in enumerate([
                 ("1", "Company overview"), ("1.1", "Services and pricing"), ("1.2", "Coverage area"),
                 ("1.3", "FAQ"), ("1.4", "Guarantees"), ("1.5", "Booking")])]
    catalog = build_section_catalog(nodes, tenant_id="t", campaign_id="c", source_policy="call_snapshot")
    session = type("S", (), {"_knowledge_catalog": catalog, "tenant_id": "t", "campaign_id": "c"})()
    return build_turn_prompt(base, knowledge_block=knowledge_system_addendum(session),
                             end_session_block=action_tool_system_addendum(["end_call"]))


def test_lat3_a_standard_turn_prompt_stays_within_budget():
    prompt = _standard_turn_prompt()
    assert len(prompt) / 4 <= 2600, len(prompt) / 4


def test_arc2_the_compliance_floor_is_the_last_word():
    prompt = _standard_turn_prompt()
    assert prompt.rstrip().endswith(load_policy("non_negotiables").format(company_name="Acme Co").rstrip())


def test_rel_ic_hal_guidance_is_in_the_policies():
    def flat(name):  # line wrapping in the .md is irrelevant
        return " ".join(load_policy(name).split())

    speak = flat("how_to_speak")
    assert "answer what they asked first" in speak                      # REL-1
    assert "one useful question at a time, then stop" in speak         # REL-2
    assert "return kindly to how you can help" in speak                # REL-3
    contact = flat("contact_and_privacy")
    assert "small groups" in contact and "after two tries" in contact  # IC-4
    knowledge = flat("company_knowledge")
    assert "never calculate new ones" in knowledge                     # HAL-1/HAL-2
    assert "conceal that you are an AI" in flat("non_negotiables")  # CMP-3


async def test_lat4_lookups_are_bounded_and_the_last_round_must_answer(monkeypatch):
    steps = []
    service, session, rounds = setup_turn(monkeypatch, "Tell me everything.", steps)
    refund = next(row["section_id"] for row in session._knowledge_catalog.nodes if row["id"] == "refund")
    # The model would keep looking things up; three rounds are allowed, then
    # the fourth call must answer: tools stay defined (the history holds tool
    # calls) but tool_choice is "none" and there is no tool channel, and the
    # last result says the lookups are over (test call 6b9cd4c4 answered "0"
    # when the tools were dropped from that request).
    steps.extend([{"section_ids": [refund]}] * 3 + ["Refunds take five working days."])
    await service._stream_llm_and_tts(session)
    assert len(rounds) == 4
    assert all(r[1].get("tools") and r[1].get("tool_choice") == "auto" for r in rounds[:3])
    final = rounds[-1][1]
    assert final.get("tools") and final.get("tool_choice") == "none" and "tool_calls_sink" not in final
    assert "No more tool calls are available" in final["extra_messages"][-1]["content"]
    assert session._spoken_sentences == ["Refunds take five working days."]


def test_tt3_callee_first_calls_keep_a_tenants_longer_turn_timeout():
    from app.domain.services.telephony.prewarm import callee_first_eot_timeout
    assert callee_first_eot_timeout(500) == 1000     # default tightness relaxed for "Hello?"
    assert callee_first_eot_timeout(1800) == 1800    # tenant tuning is no longer overwritten
    assert callee_first_eot_timeout(None) == 1000


def test_arc4_empty_receptionist_slots_never_invent_business_facts():
    from tests.unit.test_prompt_composer import RECEPTIONIST_SLOTS
    slots = {k: v for k, v in RECEPTIONIST_SLOTS.items()
             if k not in {"client_term", "cancellation_notice", "service_details"}}
    prompt = compose_prompt("receptionist", "Alex", "Acme Co", slots, direction="inbound")
    assert "24 hours" not in prompt and "See website" not in prompt and "patient" not in prompt
