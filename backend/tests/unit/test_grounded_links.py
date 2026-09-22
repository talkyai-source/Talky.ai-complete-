"""The agent must never speak a web address it was not given.

Call 2427af7e, 2026-09-22: "Here's a sample report page:
allstateestimation.co.uk/sample-reports". The campaign's knowledge holds one
URL, the bare domain. The path was invented and handed to a caller as real.
"""
from __future__ import annotations

from pathlib import Path

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


def test_the_guard_sits_on_every_path_to_tts_and_on_history():
    """Wiring guard: the only gate every spoken piece passes is _validate_for_tts,
    and history is written separately, so both must call it."""
    src = Path(__file__).resolve().parents[2].joinpath(
        "app", "domain", "services", "voice_pipeline", "turn_streamer.py"
    ).read_text(encoding="utf-8")
    gate = src[src.index("def _validate_for_tts(") :]
    gate = gate[: gate.index("valid, reason = guardrails.validate_response(")]
    assert "ground_spoken_links(" in gate
    assert src.count("ground_spoken_links(") >= 2
    # tool-returned knowledge counts as grounding
    assert "turn_grounding.append(str(result))" in src
