"""Unit tests for the agent END_CALL sentinel (extraction + leniency)."""
from app.domain.services.voice_pipeline.end_call import extract_end_call


def test_exact_token():
    text, end = extract_end_call("Thanks for your time, cheers! [[END_CALL]]")
    assert end and text == "Thanks for your time, cheers!"


def test_token_only():
    text, end = extract_end_call("[[END_CALL]]")
    assert end and text == ""


def test_lenient_variants():
    for raw in ("Bye now [[ end call ]]", "Bye now END_CALL", "Bye now [[END-CALL]]",
                "Bye now [END_CALL]]", "Bye now [[END CALL"):
        text, end = extract_end_call(raw)
        assert end, raw
        assert text == "Bye now", raw


def test_normal_text_untouched():
    text, end = extract_end_call("Right, let me explain how we handle estimating calls.")
    assert not end and text.startswith("Right,")


def test_empty():
    text, end = extract_end_call("")
    assert not end and text == ""
