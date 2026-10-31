"""HAL-2: money and percentages spoken must come from what the agent was given.

grounded_figures.py (deleted 2026-10-07) knew six currencies, so a rupee fare
on the Safar campaign was never checked at all. This replacement matches values
against the turn's own grounding and has no market-specific vocabulary.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.domain.services.voice_pipeline.figure_grounding import (
    UNGROUNDED_FIGURE_LINE,
    guard_figures,
    ungrounded_figures,
)
from tests.unit.test_model_driven_voice_turn import setup_turn

FARES = "Lahore to Karachi. Sleeper: now 8,550 rupees, was 9,500 rupees. 10% off returns."


@pytest.mark.parametrize("sentence", [
    "The sleeper is now 8,550 rupees.",
    "It was Rs. 9,500 before the offer.",
    "Returns are 10 percent off.",
    "That's 10% off a return.",
    "The fare is PKR 8550.",
])
def test_sourced_figures_pass_in_any_currency_spelling(sentence):
    assert ungrounded_figures(sentence, [FARES]) == ([], [])


@pytest.mark.parametrize("sentence, figure", [
    ("The sleeper is now 7,999 rupees.", "7,999"),
    ("Two seats come to 17,100 rupees.", "17,100"),  # derived maths is withheld
    ("You'd save 15% today.", "15"),
    ("It's just £49 a month.", "49"),
    ("That's 950 dirhams off.", "950"),
])
def test_unsourced_or_derived_figures_are_caught(sentence, figure):
    figures, _ = ungrounded_figures(sentence, [FARES])
    assert figures == [figure]


def test_bare_numbers_are_observed_never_blocked():
    session = SimpleNamespace(call_id="c1")
    sentence = "I'll call you back on the 14th at 11 then, and we have 32 routes."
    assert guard_figures(session, sentence, [FARES]) == sentence
    assert ungrounded_figures(sentence, [FARES])[1] == ["11", "32"]


def test_the_callers_own_figure_may_be_repeated_back():
    said = "My budget is about 6,000 rupees."
    assert ungrounded_figures("So about 6,000 rupees, got it.", [FARES, said]) == ([], [])


def test_withheld_sentence_is_replaced_and_counted(monkeypatch):
    session = SimpleNamespace(call_id="c1")
    assert guard_figures(session, "Only 7,999 rupees today!", [FARES]) == UNGROUNDED_FIGURE_LINE
    assert session._ungrounded_figures == 1
    monkeypatch.setenv("VOICE_FIGURE_GROUNDING", "observe")
    assert guard_figures(session, "Only 7,999 rupees today!", [FARES]) == "Only 7,999 rupees today!"
    monkeypatch.setenv("VOICE_FIGURE_GROUNDING", "off")
    assert guard_figures(session, "Only 7,999 rupees today!", [FARES]) == "Only 7,999 rupees today!"


async def _turn(monkeypatch, answer):
    steps = []
    service, session, _ = setup_turn(monkeypatch, "How much is a refund fee?", steps)
    refund = next(row["section_id"] for row in session._knowledge_catalog.nodes if row["id"] == "refund")
    steps.extend([{"section_ids": [refund]}, answer])
    await service._stream_llm_and_tts(session)
    return session


async def test_live_turn_withholds_a_price_the_knowledge_never_stated(monkeypatch):
    session = await _turn(monkeypatch, "Refunds take five working days. The fee is $25.")
    assert session._spoken_sentences == ["Refunds take five working days.", UNGROUNDED_FIGURE_LINE]


async def test_live_turn_speaks_a_price_from_the_prompt(monkeypatch):
    steps = []
    service, session, _ = setup_turn(monkeypatch, "How much is it?", steps)
    session.system_prompt += " The plan costs $25 a month."
    steps.append("It's $25 a month.")
    await service._stream_llm_and_tts(session)
    assert session._spoken_sentences == ["It's $25 a month."]


# Review 2026-10-08: units and acronyms are not money; k/m/million are values.
@pytest.mark.parametrize("sentence", [
    "Delivery in 48 HRS.", "It's a 3 BHK flat.", "We are ISO 9001 certified.",
    "Call NHS 111 for urgent help.", "It suits 150 CRM users.", "We have 30 FOR sale.",
    "Speed limit is 10 MPH.", "We start on 2 FEB.", "Job ref 123 ABC.",
])
def test_units_and_acronyms_beside_numbers_are_not_figures(sentence):
    assert ungrounded_figures(sentence, [FARES])[0] == []


@pytest.mark.parametrize("sentence, source, figure", [
    ("It's just $5k a year.", "Plans start at $4,000 a year.", "5k"),
    ("That's 7,999 PKR.", FARES, "7,999"),
    ("Only 50 AED.", FARES, "50"),
    ("A £1.5m policy.", "Cover up to 1,000,000 pounds.", "1.5m"),
])
def test_scaled_and_coded_prices_are_checked(sentence, source, figure):
    assert ungrounded_figures(sentence, [source])[0] == [figure]


@pytest.mark.parametrize("sentence, source", [
    ("Cover is £1.5 million.", "Cover up to 1,500,000 pounds."),
    ("The setup fee is $1,500.", "Setup is $1.5k."),
    ("The setup fee is $1.5k.", "Setup costs $1,500."),
])
def test_the_same_value_written_differently_passes(sentence, source):
    assert ungrounded_figures(sentence, [source])[0] == []


def test_the_honest_line_is_said_once_per_turn():
    session = SimpleNamespace(call_id="c1", _spoken_sentences=[])
    first = guard_figures(session, "Only 7,999 rupees today!", [FARES])
    assert first == UNGROUNDED_FIGURE_LINE
    session._spoken_sentences.append(first)
    assert guard_figures(session, "Or 6,999 rupees for two!", [FARES]) == ""


def test_sources_are_only_built_for_a_sentence_with_a_number():
    calls = []

    def sources():
        calls.append(1)
        return [FARES]

    session = SimpleNamespace(call_id="c1")
    assert guard_figures(session, "Happy to help with that.", sources) == "Happy to help with that."
    assert calls == []
    guard_figures(session, "It's 8,550 rupees.", sources)
    assert calls == [1]
