"""Tests for the single per-turn prompt assembler (prompts/build.py).

`build_turn_prompt` pins the block ORDER, the skip-when-falsy behaviour, and
where the CAPTURED / LIVE STATE headers land.

TWO ORDERS LIVE HERE (2026-08-13). The original layout prepended LIVE STATE and
CAPTURED above everything; both change every turn, so with them at character 0
no prompt-cache prefix could ever match — production logged
``cache_hit_ratio=0.00`` on 426 of 426 voice LLM calls across 7 days, at a cost
of ~614ms of re-read prompt on every single turn.

The default is now the cache-friendly order: stable-for-the-call blocks first,
per-turn blocks last, compliance floor still dead last. The legacy order is
kept as an executable revert path (``VOICE_PROMPT_CACHE_ORDER=false`` /
``cache_friendly_order=False``) and is tested here too — a revert switch nobody
exercises is not a revert switch.

Both suites are written against explicit ``cache_friendly_order=`` values so
neither can be silently reinterpreted by a change of default.
"""
from __future__ import annotations

from app.services.scripts.call_state_tracker import CallState
from app.services.scripts.prompts.build import build_turn_prompt

_LEGACY = dict(cache_friendly_order=False)
_CACHED = dict(cache_friendly_order=True)


# ── shared behaviour (identical under both orders) ───────────────────────────

def test_no_blocks_no_slots_returns_base_unchanged():
    assert build_turn_prompt("BASE", **_LEGACY) == "BASE"
    assert build_turn_prompt("BASE", **_CACHED) == "BASE"


def test_empty_state_adds_no_captured_header():
    for order in (_LEGACY, _CACHED):
        out = build_turn_prompt("BASE", captured_slots=CallState(), **order)
        assert out == "BASE"
        assert "CAPTURED" not in out


def test_no_live_state_block_leaves_output_unchanged():
    for order in (_LEGACY, _CACHED):
        assert build_turn_prompt("BASE", live_state_block=None, **order) == "BASE"
        assert build_turn_prompt("BASE", live_state_block="", **order) == "BASE"


def test_trailing_block_is_the_final_text_under_both_orders():
    """The compliance floor keeps the last word no matter which order is on —
    the cache change moved the volatile blocks, never the safety invariant."""
    state = CallState(email="bob@acme.com", email_confirmed=True)
    for order in (_LEGACY, _CACHED):
        out = build_turn_prompt(
            "BASE",
            live_state_block="LIVESTATE",
            knowledge_block="KB",
            accent_block="ACCENT",
            trailing_block="FLOOR",
            captured_slots=state,
            **order,
        )
        assert out.rstrip().endswith("FLOOR")


def test_falsy_blocks_are_skipped_under_both_orders():
    for order in (_LEGACY, _CACHED):
        out = build_turn_prompt(
            "BASE",
            ask_ai_block=None,
            knowledge_block="",       # empty string skipped, like `if kb_block`
            end_session_block="ENDSESSION",
            audio_tags_block=None,
            accent_block="ACCENT",
            **order,
        )
        assert "ENDSESSION" in out and "ACCENT" in out
        assert out.count("\n\n\n") == 0, "a skipped block left a blank hole"


# ── the legacy order (the revert path) ───────────────────────────────────────

def test_legacy_blocks_stack_in_the_original_order():
    out = build_turn_prompt(
        "BASE",
        ask_ai_block="ASKAI",
        knowledge_block="KB",
        end_session_block="ENDSESSION",
        audio_tags_block="TAGS",
        accent_block="ACCENT",
        **_LEGACY,
    )
    assert out == "BASE\n\nASKAI\n\nKB\n\nENDSESSION\n\nTAGS\n\nACCENT"


def test_legacy_puts_live_state_and_captured_on_top():
    state = CallState(email="bob@acme.com", email_confirmed=True)
    out = build_turn_prompt(
        "BASE",
        live_state_block="LIVESTATE",
        accent_block="ACCENT",
        trailing_block="FLOOR",
        captured_slots=state,
        **_LEGACY,
    )
    assert (
        out.index("LIVESTATE")
        < out.index("CAPTURED")
        < out.index("BASE")
        < out.index("ACCENT")
        < out.index("FLOOR")
    )
    assert "bob@acme.com" in out


# ── the cache-friendly order (the default) ───────────────────────────────────

def test_base_is_the_very_first_text():
    """THE FIX, in one assertion. Everything the cache can reuse has to come
    before the first byte that changes between turns."""
    state = CallState(email="bob@acme.com", email_confirmed=True)
    out = build_turn_prompt(
        "BASE",
        live_state_block="LIVESTATE",
        knowledge_block="KB",
        accent_block="ACCENT",
        trailing_block="FLOOR",
        captured_slots=state,
        **_CACHED,
    )
    assert out.startswith("BASE")


def test_stable_blocks_all_precede_every_per_turn_block():
    """The property that actually produces cache hits: no per-turn block may
    appear before any stable one. Asserted as a partition, not a fixed string,
    so adding a block later can't quietly slip into the prefix."""
    out = build_turn_prompt(
        "BASE",
        live_state_block="LIVESTATE",
        ask_ai_block="ASKAI",
        knowledge_block="KB",
        end_session_block="ENDSESSION",
        audio_tags_block="TAGS",
        accent_block="ACCENT",
        trailing_block="FLOOR",
        captured_slots=CallState(email="bob@acme.com", email_confirmed=True),
        **_CACHED,
    )
    stable_end = max(out.index(b) for b in ("BASE", "ENDSESSION", "TAGS", "ACCENT"))
    volatile_start = min(out.index(b) for b in ("ASKAI", "KB", "CAPTURED", "LIVESTATE"))
    assert stable_end < volatile_start


def test_live_state_is_the_last_per_turn_block():
    """LIVE STATE moves from position 0 to the end of the volatile tail — a
    promotion in recency terms, immediately before the compliance floor."""
    out = build_turn_prompt(
        "BASE",
        live_state_block="LIVESTATE",
        knowledge_block="KB",
        trailing_block="FLOOR",
        captured_slots=CallState(email="bob@acme.com", email_confirmed=True),
        **_CACHED,
    )
    assert out.index("KB") < out.index("LIVESTATE") < out.index("FLOOR")
    assert out.index("CAPTURED") < out.index("LIVESTATE")


def test_captured_facts_survive_the_move():
    """The header moved; its contents must not have been dropped on the way."""
    out = build_turn_prompt(
        "BASE",
        captured_slots=CallState(email="bob@acme.com", email_confirmed=True),
        **_CACHED,
    )
    assert "CAPTURED" in out
    assert "bob@acme.com" in out
    assert out.index("BASE") < out.index("CAPTURED")


def test_the_two_orders_contain_exactly_the_same_blocks():
    """Reordering must not add or lose content — only move it. This is the
    guard against a reorder that quietly drops a block."""
    kwargs = dict(
        live_state_block="LIVESTATE",
        ask_ai_block="ASKAI",
        knowledge_block="KB",
        end_session_block="ENDSESSION",
        audio_tags_block="TAGS",
        accent_block="ACCENT",
        trailing_block="FLOOR",
        captured_slots=CallState(email="bob@acme.com", email_confirmed=True),
    )
    legacy = build_turn_prompt("BASE", **kwargs, **_LEGACY)
    cached = build_turn_prompt("BASE", **kwargs, **_CACHED)
    for token in ("BASE", "LIVESTATE", "ASKAI", "KB", "ENDSESSION",
                  "TAGS", "ACCENT", "FLOOR", "CAPTURED", "bob@acme.com"):
        assert token in legacy, f"{token} missing from legacy order"
        assert token in cached, f"{token} missing from cache-friendly order"
    assert sorted(legacy.split()) == sorted(cached.split())


# ── the switch itself ────────────────────────────────────────────────────────

def test_env_var_selects_the_order(monkeypatch):
    kwargs = dict(live_state_block="LIVESTATE", trailing_block="FLOOR")

    monkeypatch.delenv("VOICE_PROMPT_CACHE_ORDER", raising=False)
    assert build_turn_prompt("BASE", **kwargs).startswith("BASE"), "default should be cache-friendly"

    monkeypatch.setenv("VOICE_PROMPT_CACHE_ORDER", "false")
    assert build_turn_prompt("BASE", **kwargs).startswith("LIVESTATE")

    monkeypatch.setenv("VOICE_PROMPT_CACHE_ORDER", "true")
    assert build_turn_prompt("BASE", **kwargs).startswith("BASE")


def test_explicit_argument_beats_the_env(monkeypatch):
    """So a caller that needs a specific order — and every test in this file —
    cannot be flipped by ambient configuration."""
    monkeypatch.setenv("VOICE_PROMPT_CACHE_ORDER", "false")
    out = build_turn_prompt("BASE", live_state_block="LIVESTATE", **_CACHED)
    assert out.startswith("BASE")
