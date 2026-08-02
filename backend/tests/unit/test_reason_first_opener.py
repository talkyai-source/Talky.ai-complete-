"""The spoken opener must state WHY we are calling.

WHY THIS EXISTS (2026-08-02)
----------------------------
The lead_gen persona prompt has always instructed:

    "In your FIRST breath give your name, your company, and the honest reason
     you called ... Lead with the REASON straight after your name (stating the
     reason early has the biggest lift), then hand them an easy way to say no
     rather than asking permission to proceed ... never open with 'is this a
     bad time?'"

The model never got the chance to follow it. On an agent-first call the
PRE-SYNTHESISED greeting speaks first and flips `_has_introduced`, so what the
callee actually heard was:

    "Hey, this is Sarah from All-state. Got a quick second?"

— no reason, and a permission-to-PROCEED ask, which is the exact pattern the
prompt says lands worse. The reason then arrived several turns later, or never
if they declined the "quick second".

`call_reason` is a REQUIRED campaign slot, so the data was always there; it just
never reached the spoken template. Activation is by DATA, not a feature flag: a
campaign with no usable reason keeps the previous templates byte-for-byte.
"""
from __future__ import annotations

import pytest

from app.domain.services.telephony_session_config import (
    _MAX_SPOKEN_CALL_REASON_CHARS,
    _PERSONA_GREETINGS_WITH_REASON,
    _call_reason_for,
    build_persona_greeting,
)

REASON = "we help UK contractors price tenders faster"


def _opener(**kw) -> str:
    return build_persona_greeting(
        persona_type="lead_gen",
        agent_name="Sarah",
        company_name="All-state",
        direction="outbound",
        **kw,
    )


def test_the_reason_is_spoken_in_the_opener():
    out = _opener(call_reason=REASON)
    assert REASON in out
    assert "Sarah" in out and "All-state" in out


def test_reason_openers_follow_the_measured_11pct_structure():
    """Structure is evidence-based, not taste.

    Gong, 300M+ calls:
        "Did I catch you at a bad time?"            2.15%   <- worst
        "How's your day going?"                     7.6%
        context -> own the cold call -> permission  11.18%
        context-first                              11.24%   <- best
    plus a 2.1x lift for stating the reason for calling.

    An earlier draft of these templates ended with "tell me to get lost if it's
    a bad moment" — which IS the 2.15% bad-time out, just phrased warmly. The
    persona prompt's "prefer permission-to-decline" is only correct in its
    11.18% form: context FIRST, then owning the cold call, then asking.
    """
    for template in _PERSONA_GREETINGS_WITH_REASON["lead_gen"]:
        rendered = template.format(
            agent_name="S", company_name="C", call_reason=REASON
        ).lower()
        # Owns the cold call — the disarming move in the 11.18% structure, and
        # a natural fit for an agent that must never pretend to be otherwise.
        assert "cold call" in rendered, f"must own the cold call: {rendered}"
        # Earns the next 30 seconds with a question, rather than offering an out.
        assert rendered.rstrip().endswith("?"), f"must end in a question: {rendered}"


def test_no_opener_offers_a_bad_time_escape():
    """The 2.15% pattern, in any of its phrasings."""
    banned = ("bad time", "bad moment", "get lost", "cut me off", "no good")
    for persona, templates in _PERSONA_GREETINGS_WITH_REASON.items():
        for template in templates:
            low = template.lower()
            for phrase in banned:
                assert phrase not in low, (
                    f"{persona} opener contains the worst-performing "
                    f"bad-time out ({phrase!r}): {template}"
                )


def test_openers_fit_inside_the_decision_window():
    """Prospects decide in 8-12s. At ~2.8 words/sec an opener must stay well
    under that or it talks straight through the judgement it exists to shape.
    """
    for persona, templates in _PERSONA_GREETINGS_WITH_REASON.items():
        for template in templates:
            rendered = template.format(
                agent_name="Sarah", company_name="Allstate", call_reason=REASON
            )
            words = len(rendered.split())
            assert words <= 25, (
                f"{persona} opener is {words} words (~{words/2.8:.1f}s) — past "
                f"the 8-12s decision window: {rendered}"
            )


def test_no_reason_variant_asks_is_this_a_bad_time():
    """The prompt explicitly forbids opening with it."""
    for template in _PERSONA_GREETINGS_WITH_REASON["lead_gen"]:
        assert "bad time" not in template.lower()


def test_without_a_reason_the_previous_opener_is_unchanged():
    """Activation is by data. A campaign that supplies no reason must get
    exactly what it got before — this change carries no blast radius for them."""
    out = _opener()
    assert REASON not in out
    assert "Sarah" in out and "All-state" in out


@pytest.mark.parametrize(
    "slots,expected",
    [
        ({"call_reason": REASON}, REASON),
        ({"goal": REASON}, REASON),                       # falls back to goal
        ({"call_reason": "  spaced   out  reason "}, "spaced out reason"),
        ({"call_reason": "ends with punctuation."}, "ends with punctuation"),
        ({}, None),
        ({"call_reason": ""}, None),
        ({"call_reason": "x" * 500}, None),               # too long to speak
    ],
)
def test_call_reason_extraction(slots, expected):
    assert _call_reason_for({"campaign_slots": slots}) == expected


def test_overlong_reason_falls_back_rather_than_monologuing():
    """The reason is free text an operator types. Past the cap the opener stops
    being a pattern-interrupt and becomes something the callee talks over."""
    long_reason = "because " * 60
    assert len(long_reason) > _MAX_SPOKEN_CALL_REASON_CHARS
    assert _call_reason_for({"campaign_slots": {"call_reason": long_reason}}) is None


def test_inbound_never_states_the_reason():
    """They rang us — telling them why we're calling is nonsense."""
    out = build_persona_greeting(
        persona_type="lead_gen",
        agent_name="Sarah",
        company_name="All-state",
        direction="inbound",
        call_reason=REASON,
    )
    assert REASON not in out


def test_unknown_persona_still_produces_a_grammatical_opener():
    out = build_persona_greeting(
        persona_type="not_a_persona",
        agent_name="Sarah",
        company_name="All-state",
        direction="outbound",
        call_reason=REASON,
    )
    assert out and "Sarah" in out


def test_reason_reaches_the_greeting_builder_from_agent_config():
    """Structural pin on the wiring — the plumbing existed for two days while
    the template ignored it, which is how this shipped half-done once already."""
    import inspect

    from app.domain.services.telephony import config as tconfig

    src = inspect.getsource(tconfig._build_call_greeting)
    assert "call_reason" in src, (
        "_build_call_greeting must read call_reason off agent_config and pass "
        "it to build_persona_greeting, or the opener silently ignores it"
    )
