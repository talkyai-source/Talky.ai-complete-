"""Tests for real-time voicemail (answering-machine) detection heuristic."""
from app.domain.services.voice_pipeline.transcript_heuristics import (
    is_voicemail_greeting,
)


class TestIsVoicemailGreeting:
    def test_classic_voicemail_greetings_match(self):
        for text in (
            "Please leave a message after the tone.",
            "The person you are trying to reach is not available.",
            "You've reached the voicemail of John. Please leave your name and number.",
            "Your call has been forwarded to an automated voice messaging system.",
            "Hi, I can't take your call right now, please leave a message after the beep.",
            "Please record your message. When you are finished, hang up.",
        ):
            assert is_voicemail_greeting(text) is True, text

    def test_live_human_answers_do_not_match(self):
        for text in (
            "Hello?",
            "Hi, who's this?",
            "Yeah, speaking.",
            "Hello, this is Sarah.",
            "I'm not interested, thanks.",
            "Can you tell me what this is about?",
            "Sorry, now's not a good time.",
            # A live person / business answerer must NEVER be hung up on. These
            # phrasings were deliberately dropped from the phrase list because a
            # real human says them (regression guard for the false-positive fix).
            "You've reached Acme Plumbing, how can I help you?",
            "You have reached the front desk, one moment.",
            "He's not available, can I take a message?",
            "Please leave me alone, I'm not interested.",
        ):
            assert is_voicemail_greeting(text) is False, text

    def test_empty_and_none_safe(self):
        assert is_voicemail_greeting("") is False
        assert is_voicemail_greeting(None) is False  # type: ignore[arg-type]

    def test_case_and_whitespace_insensitive(self):
        assert is_voicemail_greeting("PLEASE   LEAVE   A   MESSAGE") is True
