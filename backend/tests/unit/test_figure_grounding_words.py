"""HAL-2 covers prices spoken as words, not only digits.

Test call f5dcac8e (Safar Coaches, DeepSeek, 2026-10-08): every fare was
spoken as words, "one thousand seven hundred and sixty rupees", and the
figure check read digits only, so it never ran on that model. These fares
happened to be right; a wrong one would have been spoken unchecked.

Quantities are parsed as standard English cardinals (with the British "and",
"point" decimals, and lakh/crore). Digit-by-digit read-backs are digit
strings, not amounts, and year-style readings ("seventeen sixty") are not
guessed at, so neither can be withheld by mistake.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.domain.services.voice_pipeline.figure_grounding import (
    UNGROUNDED_FIGURE_LINE, guard_figures, ungrounded_figures,
)

# The campaign's own knowledge for the route (safar_coaches_fares.md).
ROUTE = ("Safar Coaches has cut fares on all twelve of its intercity routes: 20 percent off Executive "
         "class, 15 percent off Business class and 10 percent off Sleeper class.\n"
         "### Lahore to Islamabad and Islamabad to Lahore\n"
         "Executive: now 1,760 rupees, was 2,200 rupees. Saving 440 rupees per seat.\n"
         "Business: now 2,380 rupees, was 2,800 rupees. Saving 420 rupees per seat.\n"
         "Journey time about 4 hours 30 minutes. Departures every hour from 5:00 am to 1:00 am.")


@pytest.mark.parametrize("sentence", [
    # What the agent actually said on f5dcac8e, sentence by sentence.
    "For Islamabad to Lahore, Executive class is now one thousand seven hundred and sixty rupees, "
    "down from two thousand two hundred — a saving of four hundred and forty rupees per seat.",
    "Business class is two thousand three hundred and eighty rupees, was two thousand eight hundred.",
    "That's twenty percent off the Executive fare.",
    "It's rupees one thousand seven hundred and sixty.",
    "Executive is seventeen hundred and sixty rupees.",
    "Business is twenty-three hundred and eighty rupees.",
])
def test_sourced_prices_spoken_as_words_pass(sentence):
    assert ungrounded_figures(sentence, [ROUTE])[0] == []


@pytest.mark.parametrize("sentence, figure", [
    ("Executive class is now one thousand eight hundred rupees.", "one thousand eight hundred"),
    ("Business is two thousand three hundred and ninety rupees.", "two thousand three hundred and ninety"),
    ("You'd save twenty-five percent today.", "twenty-five"),
    ("Two seats come to three thousand five hundred and twenty rupees.", "three thousand five hundred and twenty"),
    ("The fare is PKR nine hundred.", "nine hundred"),
    ("Cover is two point five million pounds.", "two point five million"),
    ("A family ticket is two lakh rupees.", "two lakh"),
])
def test_unsourced_prices_spoken_as_words_are_caught(sentence, figure):
    assert ungrounded_figures(sentence, [ROUTE])[0] == [figure]


def test_the_guard_withholds_a_wrong_price_spoken_as_words():
    session = SimpleNamespace(call_id="f5dcac8e", _spoken_sentences=[])
    wrong = "Executive class is now one thousand six hundred rupees."
    assert guard_figures(session, wrong, [ROUTE]) == UNGROUNDED_FIGURE_LINE
    right = "Executive class is now one thousand seven hundred and sixty rupees."
    assert guard_figures(session, right, [ROUTE]) == right


@pytest.mark.parametrize("sentence, source", [
    ("Cover is two point five million pounds.", "Cover up to £2.5m."),
    ("A family pass is two lakh fifty thousand rupees.", "Family pass: 250,000 rupees."),
    ("It's a thousand dirhams.", "Fee: AED 1,000."),
    ("That's 8,550 rupees.", "The sleeper costs eight thousand five hundred and fifty rupees."),
    ("That's 2 thousand rupees.", "Fee: 2,000 rupees."),
])
def test_the_same_value_in_words_or_digits_matches(sentence, source):
    assert ungrounded_figures(sentence, [source])[0] == []


def test_a_conjunction_and_splits_two_amounts():
    sentence = "Plans run between one and two thousand rupees."
    assert ungrounded_figures(sentence, ["Plans from 1 to 2,000 rupees."])[0] == []
    assert ungrounded_figures(sentence, ["Plans from 1 to 3,000 rupees."])[0] == ["two thousand"]


@pytest.mark.parametrize("sentence", [
    "Your number is zero three one two, zero seven five, zero four nine six.",
    "That's mike one two three at gmail dot com.",
    "We were founded in nineteen ninety.",
    "The one with WiFi is the Business coach.",
    "Executive is seventeen sixty rupees.",  # year-style reading: not guessed at
])
def test_read_backs_years_and_ordinary_words_are_never_withheld(sentence):
    session = SimpleNamespace(call_id="c1", _spoken_sentences=[])
    assert guard_figures(session, sentence, [ROUTE]) == sentence


def test_sources_are_not_built_for_a_sentence_without_a_candidate_figure():
    calls = []

    def sources():
        calls.append(1)
        return [ROUTE]

    session = SimpleNamespace(call_id="c1", _spoken_sentences=[])
    assert guard_figures(session, "The one you want is the Executive coach.", sources)
    assert calls == []
    guard_figures(session, "It's one thousand seven hundred and sixty rupees.", sources)
    assert calls == [1]
