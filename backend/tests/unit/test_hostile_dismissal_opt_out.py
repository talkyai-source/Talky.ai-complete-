"""Hostile dismissals ("fuck off") are a do-not-call request (owner decision 2026-10-20)."""
import pytest

from app.domain.services.voice_pipeline.identity_disposition import contains_dnc


@pytest.mark.parametrize("text", [
    "Fuck off.", "fuck off", "It's not Dan, it's fucking Chief of Maltby. Fuck off.",
    "Yeah. Fuck you.", "Fuck you.", "f*** off", "f off", "piss off", "sod off",
])
def test_hostile_dismissal_is_dnc(text):
    assert contains_dnc(text) is True


@pytest.mark.parametrize("text", [
    "that's fucking brilliant", "no fucking way, really?", "he told me to fuck off",
    "fuck, I dropped my phone", "who the fuck is this?",
    "I don't want you to piss off, carry on",
    "don't call me tomorrow", "I'm lost, can you help",
    # "get lost" is not a phrase: it matches "we always get lost in the paperwork".
    "we always get lost in the paperwork", "get lost",
])
def test_non_dismissals_are_not_dnc(text):
    assert contains_dnc(text) is False


@pytest.mark.parametrize("text", ["stop calling me", "Please unsubscribe me", "do not call again"])
def test_existing_phrases_unchanged(text):
    assert contains_dnc(text) is True
