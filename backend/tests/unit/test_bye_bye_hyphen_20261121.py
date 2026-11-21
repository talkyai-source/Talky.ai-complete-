"""A hyphenated or comma-split "bye-bye" is the same goodbye as "bye bye".

Production 2026-11-20 15:07: the agent said "Bye-bye, thanks for your time." and the
line then idled 61 s. The speech-to-text service writes the caller's goodbye as
"Bye-bye." (hyphen); the phrase floor only matched "bye bye" with a space.
A bare "bye" stays excluded on purpose (test_identity_disposition.py).
"""
import time

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
# Expected answer written next to each sentence; each runs in both forms.
# (The reviewer's corpus, the coordinator's lists and the earlier lists, deduplicated.)
CASES = [
    # --- the caller is saying goodbye
    ("Bye-bye.", True), ("bye-bye", True), ("Okay, bye-bye then.", True),
    ("Bye-bye, thanks.", True), ("Bye, bye.", True),
    ("okay bye-bye thank you very much", True), ("OK bye-bye bye.", True),
    ("Bye-bye, take care", True), ("Bye-bye, thank you very much", True),
    ("Bye-bye, thanks a lot", True), ("Bye-bye, have a good day", True),
    ("Bye-bye, have a nice evening", True), ("Bye-bye, speak soon", True),
    ("Bye-bye, see you", True), ("Bye-bye, all the best", True), ("Bye-bye, God bless", True),
    ("Bye-bye and thanks", True), ("Thanks for calling, bye-bye", True),
    ("Bye-bye, thanks for your help", True), ("Bye-bye, good luck", True),
    ("Bye-bye, enjoy", True), ("Yes, bye-bye, speak to you soon", True),
    ("Well, bye-bye Sarah", True),
    ("Okay love, bye-bye", True), ("Thanks love, bye bye", True), ("Cheers mate, bye bye", True),
    ("Ta, bye bye", True), ("Thanks for your help, bye-bye", True),
    ("Thank you so much for your help, bye bye", True), ("That's great, thanks, bye-bye", True),
    ("No problem, bye bye", True), ("Okay no worries, bye-bye", True),
    ("I'm not interested, bye bye", True), ("Not interested, thanks, bye-bye", True),
    ("Okay, I'll speak to you later, bye bye", True),
    ("Lovely, have a good day, bye-bye", True), ("Sorry, I've got to run, bye bye", True),
    ("Thanks, you too, bye-bye", True), ("Bye bye sweetheart", True),
    ("Okay, that's fine, bye-bye", True), ("Bye bye, I'm off", True),
    ("Bye bye, don't call again", True), ("I can't wait for that. Bye bye.", True),
    ("Hold on, okay, never mind. Bye-bye.", True),
    ("Bye bye.", True), ("Okay, bye-bye.", True), ("Okay thanks, bye bye.", True),
    ("Thank you, bye-bye.", True), ("Thanks very much, bye bye then.", True),
    ("Alright then, bye-bye.", True), ("Right, cheers, bye-bye.", True),
    ("Okay love, bye-bye.", True), ("Thanks love, bye bye.", True), ("Cheers mate, bye bye.", True),
    ("Ta, bye bye.", True), ("Thanks for your help, bye-bye.", True),
    ("No thanks, bye bye.", True), ("Bye bye, have a nice day.", True), ("Bye bye now.", True),
    ("Okay bye bye bye.", True), ("Yeah okay bye-bye.", True), ("Okay. Bye-bye.", True),
    ("I'm busy right now. Bye-bye.", True), ("Okay, sounds good, bye-bye.", True),
    ("Bye bye, thank you for calling.", True), ("Okay, bye bye, thanks a lot.", True),
    ("Bye bye, cheers.", True), ("Okay bye-bye, see you.", True),
    ("Just a minute, sorry. No, I'm fine, thanks, bye bye.", True),
    ("Bye-bye, I have to go now.", True),
    # --- not a goodbye: the words merely appear in a sentence, or the caller takes it back
    ("My son says bye bye to everyone", False), ("Hang on, bye bye to who?", False),
    ("Bye bye, carry on", False), ("My kids are waving bye-bye.", False),
    ("Kiss your money bye-bye.", False),
    ("We said bye-bye to BT in March, so what do you offer?", False),
    ("No, we said bye-bye to them last year.", False), ("I work at Bye-Bye Baby.", False),
    ("Say bye-bye to grandma", False),
    ("Hang on. Bye-bye darling. Sorry, go on.", False), ("Bye-bye? what do you mean", False),
    ("Bye-bye, wait, I have a question", False), ("I will say bye-bye then", False),
    ("You can kiss your money bye-bye.", False), ("We said bye-bye to BT last year.", False),
    ("The baby's saying bye-bye in the background.", False),
    ("Bye bye? No wait, I have a question.", False),
    ("Bye-bye, go on then, what were you saying?", False),
    ("Wave bye-bye to your old boiler.", False), ("That's bye-bye to my savings then.", False),
    ("Bye bye bills, that's what the advert said.", False),
    ("Bye bye, actually wait, one more thing.", False),
    ("Okay bye bye. Actually, can you tell me the price first?", False),
    ("Say bye-bye, Tom.", False), ("Bye-bye birdie is my favourite film.", False),
    # --- ambiguous: a bye-bye alone in its own sentence is taken as a goodbye
    ("My daughter waves. Bye-bye.", True),
]


@pytest.mark.parametrize("text,expected", CASES)
def test_bye_bye_counts_only_as_a_standalone_farewell(text, expected):
    assert contains_explicit_goodbye(text) is expected


@pytest.mark.parametrize("text,expected", CASES)
def test_spaced_form_gives_the_same_answer(text, expected):
    assert contains_explicit_goodbye(text.replace("-", " ")) is expected


@pytest.mark.parametrize("text", [
    "no thanks " * 30 + "bye bye something",
    "thanks so " * 30 + "bye bye something",
    "bye bye " + "no thanks " * 30 + "something",
    "bye bye " + "thanks so " * 30 + "something",
    "something " + "no thanks " * 30 + "bye bye",
])
def test_long_non_farewell_clauses_cannot_stall_the_event_loop(text):
    started = time.perf_counter()
    assert contains_explicit_goodbye(text) is False
    assert time.perf_counter() - started < 0.2


def test_a_real_long_goodbye_of_twelve_words_or_fewer_still_counts():
    assert contains_explicit_goodbye("Okay great lovely thanks so much for your help bye bye") is True
