"""A hyphenated or comma-split "bye-bye" is the same goodbye as "bye bye".

Production 2026-11-20 15:07: the agent said "Bye-bye, thanks for your time." and the
line then idled 61 s. The speech-to-text service writes the caller's goodbye as
"Bye-bye." (hyphen); the phrase floor only matched "bye bye" with a space.
A bare "bye" stays excluded on purpose (test_identity_disposition.py).
"""
import pytest

from app.domain.services.end_session_action import caller_signaled_end
from app.domain.services.voice_pipeline.identity_disposition import contains_explicit_goodbye

ACCEPTED = [
    "Bye-bye.", "bye-bye", "Bye, bye.", "Okay, bye-bye then.", "Bye bye.", "BYE-BYE!",
    "Bye‑bye.", "Bye – bye.",
]
REJECTED = ["bye", "Bye.", "By the way, call me later", "Bye-bye? what do you mean", "Bye-bye, wait, I have a question"]


@pytest.mark.parametrize("text", ACCEPTED)
def test_hyphen_and_comma_forms_are_an_explicit_goodbye(text):
    assert contains_explicit_goodbye(text) is True


@pytest.mark.parametrize("text", REJECTED)
def test_bare_bye_and_non_goodbyes_stay_rejected(text):
    assert contains_explicit_goodbye(text) is False


@pytest.mark.parametrize("text", ACCEPTED)
def test_model_end_call_path_also_hears_it(text):
    assert caller_signaled_end(text) is True


def test_bye_bye_inside_a_longer_sentence_is_treated_like_the_spaced_form():
    # Consistency: whatever the spaced form does in a sentence, the hyphen does too.
    for sentence in ("the bye{}bye baby shop", "I said bye{}bye to her"):
        assert (contains_explicit_goodbye(sentence.format("-"))
                == contains_explicit_goodbye(sentence.format(" ")))


# ---- "bye-bye" counts only as a standalone farewell (hyphen and spaced alike) ----
NOT_A_FAREWELL = [
    "We said bye-bye to BT in March, so what do you offer?",
    "No, we said bye-bye to them last year.",
    "I work at Bye-Bye Baby.",
    "Say bye-bye to grandma",
    "Hang on. Bye-bye darling. Sorry, go on.",
    "Bye-bye? what do you mean",
    "Bye-bye, wait, I have a question",
    "My kids are waving bye-bye.",
    "Kiss your money bye-bye.",
]
FAREWELL = [
    "Bye-bye.", "bye-bye", "Okay, bye-bye then.", "Bye-bye, thanks.", "Bye, bye.",
    "okay bye-bye thank you very much", "OK bye-bye bye.",
    "Bye-bye, take care", "Bye-bye, thank you very much", "Bye-bye, thanks a lot",
    "Bye-bye, have a good day", "Bye-bye, have a nice evening", "Bye-bye, speak soon",
    "Bye-bye, see you", "Bye-bye, all the best", "Bye-bye, God bless", "Bye-bye and thanks",
    "Thanks for calling, bye-bye",
]


@pytest.mark.parametrize("text", NOT_A_FAREWELL)
def test_sentences_that_merely_contain_bye_bye_are_not_a_goodbye(text):
    assert contains_explicit_goodbye(text) is False
    assert contains_explicit_goodbye(text.replace("-", " ")) is False


@pytest.mark.parametrize("text", FAREWELL)
def test_standalone_farewells_count_in_both_forms(text):
    assert contains_explicit_goodbye(text) is True
    assert contains_explicit_goodbye(text.replace("-", " ")) is True
