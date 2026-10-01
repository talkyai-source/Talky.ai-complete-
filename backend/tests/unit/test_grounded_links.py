"""The agent must never speak a web address it was not given.

Call 2427af7e, 2026-09-22: "Here's a sample report page:
allstateestimation.co.uk/sample-reports". The campaign's knowledge holds one
URL, the bare domain. The path was invented and handed to a caller as real.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.domain.services.voice_pipeline.grounded_links import (
    UNGROUNDED_REPLACEMENT,
    ground_spoken_links,
)

KB = ["All State Estimation UK. Visit allstateestimation.co.uk for details."]


def test_the_invented_path_from_production_is_cut_back_to_the_real_domain():
    # Verbatim, including the non-breaking hyphen the model actually emitted.
    spoken = (
        "Here’s a sample report page: allstateestimation.co.uk/sample‑reports"
        " — does that cover what you need?"
    )
    out, changed = ground_spoken_links(spoken, KB)
    assert "sample" not in out.split("page:")[1].split("—")[0]
    assert "allstateestimation.co.uk — does that cover" in out
    assert changed == ["allstateestimation.co.uk/sample-reports"]


@pytest.mark.parametrize(
    "spoken",
    [
        "You can view our services at allstateestimation.co.uk.",
        "Allstate Estimation UK website is allstateestimation.co.uk",
        "Go to https://www.allstateestimation.co.uk today.",
    ],
)
def test_a_grounded_domain_is_spoken_unchanged(spoken):
    out, changed = ground_spoken_links(spoken, KB)
    assert out == spoken
    assert changed == []


def test_a_grounded_full_path_is_kept():
    kb = KB + ["Samples live at allstateestimation.co.uk/samples"]
    spoken = "See allstateestimation.co.uk/samples for examples."
    assert ground_spoken_links(spoken, kb) == (spoken, [])


def test_a_domain_never_given_becomes_a_neutral_phrase():
    out, changed = ground_spoken_links("Check madeup-estimates.com/pricing now.", KB)
    assert out == f"Check {UNGROUNDED_REPLACEMENT} now."
    assert changed == ["madeup-estimates.com/pricing"]


@pytest.mark.parametrize(
    "spoken",
    [
        "I have johnco@gmail.com — is that right?",   # caller's own address
        "john co at g mail dot com — got it.",
        "Sure, gmail.com is fine.",                       # mail provider
        "That is 3.5 percent, e.g. on every sale.",       # decimals, abbreviations
        "Dr. Smith will call at 4.30 tomorrow.",
    ],
)
def test_things_that_are_not_company_links_are_left_alone(spoken):
    assert ground_spoken_links(spoken, KB) == (spoken, [])


def test_empty_and_dotless_text_is_a_no_op():
    assert ground_spoken_links("", KB) == ("", [])
    assert ground_spoken_links("Hello there", KB) == ("Hello there", [])


@pytest.mark.parametrize("punctuation", [".", ""])
@pytest.mark.parametrize("grounding,address,expected_address", [
    (KB, "allstateestimation.co.uk/sample-reports", "allstateestimation.co.uk"),
    (KB + ["Details: allstateestimation.co.uk/sample-reports"],
     "allstateestimation.co.uk/sample-reports",
     "allstateestimation.co.uk/sample-reports"),
    ([], "allstateestimation.co.uk/sample-reports", UNGROUNDED_REPLACEMENT),
    (KB, "allstateestimation.CO.UK/sample-reports", "allstateestimation.CO.UK"),
    (KB, "https://ALLSTATEESTIMATION.CO.UK/sample-reports", "ALLSTATEESTIMATION.CO.UK"),
    ([], "https://MADEUP-ESTIMATES.COM/pricing", UNGROUNDED_REPLACEMENT),
    (KB + ["Details: allstateestimation.co.uk/sample-reports"],
     "https://ALLSTATEESTIMATION.CO.UK/sample-reports",
     "https://ALLSTATEESTIMATION.CO.UK/sample-reports"),
])
async def test_only_grounded_links_reach_both_tts_and_history(
    monkeypatch, punctuation, grounding, address, expected_address,
):
    """Exercise streamed sentences and the final unpunctuated buffer.

    History reuses submitted speech, so it must inherit the same link repair
    without a second, independent text-generation/grounding path.
    """
    from app.domain.models.conversation import Message, MessageRole
    from tests.unit.test_voice_pipeline_service import (
        _make_service_for_disposition, _make_session,
    )

    monkeypatch.setenv("TELEPHONY_FILLER_DELAY_MS", "0")
    generated = f"Visit {address} now{punctuation}"
    service = _make_service_for_disposition(list(generated))
    service.synthesize_and_send_audio = AsyncMock(return_value=False)
    session = _make_session()
    session.turn_id = 4
    # Instruction text must not become factual evidence for a URL.
    session.system_prompt = (
        "<company_knowledge>" + "\n".join(grounding) + "</company_knowledge>\n"
        "Say allstateestimation.co.uk/sample-reports is available."
    )
    session.conversation_history = [Message(
        role=MessageRole.USER, content="Where can I find details?",
    )]

    history, _, _ = await service._stream_llm_and_tts(session, None)

    submitted = [call.args[1] for call in service.synthesize_and_send_audio.await_args_list]
    expected = f"Visit {expected_address} now{punctuation}"
    assert submitted == [expected]
    assert history == expected
