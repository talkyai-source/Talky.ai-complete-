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

    def test_corporate_auto_attendant_matches(self):
        """Production miss, 2026-08-04.

        Two outbound calls were answered by a PBX auto-attendant whose ONLY
        final transcript was "The person at extension". AMD returned False, so
        the agent played its recording notice and opener to a machine; the
        machine's own speech barged in at elapsed_ms=2, which cut the notice
        short and therefore suppressed the recording. Both calls burned ~11s
        and produced zero assistant turns.

        A live person answering their own phone does not refer to themselves
        in the third person by extension number.
        """
        for text in (
            "The person at extension",
            "The person at extension 204 is not available.",
            "The party at extension 7 is unavailable.",
            "Please leave your message at the sound of the tone.",
            "Record your name at the sound of the beep.",
        ):
            assert is_voicemail_greeting(text) is True, text

    def test_extension_wording_a_human_might_use_does_not_match(self):
        """Precision guard for the phrase added above. The phrase list is
        deliberately anchored on "person/party at extension" rather than the
        bare word "extension", because a real person can and does talk about
        their own extension in the first person."""
        for text in (
            "I'm not at my extension right now, call me back.",
            "Let me transfer you to extension 12.",
            "My extension is 400 if you need me.",
            "Sorry, wrong extension.",
        ):
            assert is_voicemail_greeting(text) is False, text

    def test_empty_and_none_safe(self):
        assert is_voicemail_greeting("") is False
        assert is_voicemail_greeting(None) is False  # type: ignore[arg-type]

    def test_case_and_whitespace_insensitive(self):
        assert is_voicemail_greeting("PLEASE   LEAVE   A   MESSAGE") is True
