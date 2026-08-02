"""An internal action envelope must never reach TTS.

WHY THIS EXISTS (production, 2026-07-08)
----------------------------------------
A caller heard this read aloud, verbatim, by the agent:

    {"action":"endsession","reason":"conversationcomplete",
     "farewell:"Message left, I'll try again another time. Cheers."}

Two independent defects combined:

1. The model emitted MALFORMED JSON — note `farewell:"` with the closing quote
   on the key missing, and `endsession`/`conversationcomplete` with the
   underscores dropped. `json.loads` raised, `parse_end_session_action`
   returned None, so the text was treated as ordinary speech and spoken.

2. The streaming guard only suppressed a buffer that STARTED with `{`. The
   contract says "no spoken text outside JSON", but small models routinely
   emit a sentence and THEN the envelope — which sailed straight past it.

Constrained decoding would prevent (1) at the token level, but Groq's strict
mode is only available on gpt-oss-20b/120b and this codebase deliberately does
not run gpt-oss for conversational voice. So both fixes live in our code and
work for every provider.
"""
from __future__ import annotations

import pytest

from app.domain.services.end_session_action import parse_end_session_action
from app.domain.services.voice_pipeline.turn_streamer import (
    _find_action_envelope_start,
)

# The exact string that was spoken on the live call.
PRODUCTION_LEAK = (
    '{"action":"endsession","reason":"conversationcomplete",'
    '"farewell:"Message left, I\'ll try again another time. Cheers."}'
)


def test_the_exact_production_string_is_now_recognised():
    """If this parses, it is swallowed as an action instead of being spoken."""
    parsed = parse_end_session_action(PRODUCTION_LEAK)
    assert parsed is not None, "still unparsed — it would be read aloud again"
    assert parsed["reason"] == "conversation_complete"
    assert "Message left" in parsed["farewell"]


@pytest.mark.parametrize(
    "envelope",
    [
        '{"action":"end_session","reason":"user_goodbye","farewell":"Bye."}',
        '{"action":"endsession","reason":"user_done","farewell":"Bye."}',
        '{"action":"end_session","reason":"user_done",}',
    ],
)
def test_mangled_variants_still_parse(envelope):
    assert parse_end_session_action(envelope) is not None


def test_fully_single_quoted_json_is_DELIBERATELY_not_repaired():
    """A limit of the repair, encoded so nobody "fixes" it later.

    Converting single-quoted VALUES to double quotes would have to rewrite
    apostrophes — and the farewell in the real production leak was
    "I'll try again another time". A naive quote conversion corrupts exactly
    that text. Repairing keys is safe (they are identifiers); repairing values
    is not, so this stays unparsed and the envelope is instead caught by the
    streaming guard, which suppresses on the brace without needing to parse.
    """
    single = "{'action':'end_session','reason':'user_goodbye'}"
    assert parse_end_session_action(single) is None
    # ...but it never reaches TTS, because the splitter still sees it.
    assert _find_action_envelope_start(single) >= 0


@pytest.mark.parametrize(
    "speech",
    [
        "Sure, I can help with that.",
        "The price is {} for the year.",
        '{"foo":"bar"}',
        "",
        "No JSON here at all, just talking.",
    ],
)
def test_ordinary_speech_is_never_mistaken_for_an_action(speech):
    """The repair must not become a general JSON fixer that swallows speech.
    A false positive here means the agent goes silent mid-call."""
    assert parse_end_session_action(speech) is None


def test_prose_then_envelope_splits_correctly():
    """The failure the streaming guard missed. The goodbye is spoken; the
    envelope is suppressed."""
    buf = 'Alright, take care! {"action":"end_session","reason":"user_goodbye"}'
    idx = _find_action_envelope_start(buf)
    assert idx > 0
    assert buf[:idx].strip() == "Alright, take care!"
    assert "action" not in buf[:idx]


@pytest.mark.parametrize(
    "buf",
    [
        '{"action":"end_session"}',
        '{ "action" : "end_session" }',
        "{'action':'end_session'}",
        '  {"ACTION":"end_session"}',
    ],
)
def test_envelope_start_is_found_in_all_spacings(buf):
    assert _find_action_envelope_start(buf) >= 0


@pytest.mark.parametrize(
    "speech",
    [
        "the price is {x} for the year",
        "No braces here at all",
        "we use {placeholders} in our templates",
        "",
    ],
)
def test_a_brace_in_ordinary_speech_does_not_trip_the_splitter(speech):
    """Truncating real speech at a stray brace would cut the agent off
    mid-sentence — worse than the bug being fixed."""
    assert _find_action_envelope_start(speech) == -1


def test_streaming_guard_handles_the_mid_buffer_case():
    """Structural pin: the guard must consider more than the first character."""
    import inspect

    from app.domain.services.voice_pipeline import turn_streamer

    src = inspect.getsource(turn_streamer.TurnStreamer.stream)
    assert "_find_action_envelope_start" in src, (
        "the streaming guard only checks whether the buffer STARTS with '{', "
        "so a model that emits prose before the envelope reads it aloud"
    )
