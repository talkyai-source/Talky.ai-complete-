"""Tests for the single per-turn prompt assembler (prompts/build.py).

`build_turn_prompt` is a behaviour-preserving extraction of the assembly that
used to live inline in turn_streamer. These pin the block ORDER, the
skip-when-falsy behaviour, and the CAPTURED-facts-on-top prepend so the
extraction can't silently drift from the original wiring.
"""
from __future__ import annotations

from app.services.scripts.call_state_tracker import CallState
from app.services.scripts.prompts.build import build_turn_prompt


def test_no_blocks_no_slots_returns_base_unchanged():
    assert build_turn_prompt("BASE") == "BASE"


def test_blocks_stack_in_fixed_order():
    out = build_turn_prompt(
        "BASE",
        ask_ai_block="ASKAI",
        knowledge_block="KB",
        end_session_block="ENDSESSION",
        audio_tags_block="TAGS",
        accent_block="ACCENT",
    )
    # base -> ask_ai -> knowledge -> end_session -> audio_tags -> accent,
    # joined by a blank line (matches the old "\n\n" concatenation exactly).
    assert out == "BASE\n\nASKAI\n\nKB\n\nENDSESSION\n\nTAGS\n\nACCENT"


def test_falsy_blocks_are_skipped():
    out = build_turn_prompt(
        "BASE",
        ask_ai_block=None,
        knowledge_block="",          # empty string is skipped, like the old `if kb_block`
        end_session_block="ENDSESSION",
        audio_tags_block=None,
        accent_block="ACCENT",
    )
    assert out == "BASE\n\nENDSESSION\n\nACCENT"


def test_captured_block_prepended_on_top():
    state = CallState(email="bob@acme.com", email_confirmed=True)
    out = build_turn_prompt("BASE", accent_block="ACCENT", captured_slots=state)
    # CAPTURED header lands ABOVE the base (highest-attention position).
    assert "CAPTURED" in out
    assert out.index("CAPTURED") < out.index("BASE") < out.index("ACCENT")
    assert "bob@acme.com" in out


def test_empty_state_adds_no_captured_header():
    # An all-None CallState yields no CAPTURED header (compose_system_prompt
    # returns the prompt unchanged) but must not crash or alter the output.
    out = build_turn_prompt("BASE", captured_slots=CallState())
    assert out == "BASE"
    assert "CAPTURED" not in out


def test_live_state_prepended_above_captured_and_base():
    state = CallState(email="bob@acme.com", email_confirmed=True)
    out = build_turn_prompt(
        "BASE", live_state_block="LIVESTATE", accent_block="ACCENT", captured_slots=state,
    )
    # Top-of-prompt order: LIVE STATE -> CAPTURED -> BASE -> accent.
    assert out.index("LIVESTATE") < out.index("CAPTURED") < out.index("BASE") < out.index("ACCENT")


def test_no_live_state_block_leaves_output_unchanged():
    assert build_turn_prompt("BASE", live_state_block=None) == "BASE"
    assert build_turn_prompt("BASE", live_state_block="") == "BASE"


def test_trailing_block_is_the_final_text():
    # The trailing block (per-model addendum + compliance floor) must sit AFTER
    # every optional block so it keeps the recency slot on the live path.
    out = build_turn_prompt("BASE", accent_block="ACCENT", trailing_block="FLOOR")
    assert out == "BASE\n\nACCENT\n\nFLOOR"


def test_trailing_block_stays_last_under_live_state_and_captured():
    state = CallState(email="bob@acme.com", email_confirmed=True)
    out = build_turn_prompt(
        "BASE",
        live_state_block="LIVESTATE",
        accent_block="ACCENT",
        trailing_block="FLOOR",
        captured_slots=state,
    )
    # LIVE STATE / CAPTURED prepend on top; FLOOR remains the very last text.
    assert (
        out.index("LIVESTATE")
        < out.index("CAPTURED")
        < out.index("BASE")
        < out.index("ACCENT")
        < out.index("FLOOR")
    )
    assert out.rstrip().endswith("FLOOR")
