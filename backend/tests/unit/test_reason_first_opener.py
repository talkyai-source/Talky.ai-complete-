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

AND THE OPENER MUST BE SHORT (2026-08-05)
-----------------------------------------
A real outbound call, answered 20:21:35:

    20:21:35 -> 20:21:39   recording notice        4.0s
    20:21:39 -> 20:21:47   greeting, 192 chunks    7.7s   interrupted=False
    20:21:47               callee hung up

11.7s of unbroken agent speech, `interrupted=False` (they never even tried to
cut in), then gone. The structure was right and the LENGTH was wrong: the 8-12s
decision window starts at "hello", and the non-negotiable 4.0s recording notice
runs inside it before the greeting says a word. That leaves 4.0s, which at the
measured 2.8 words/second is 11.2 words.

The word budget below is therefore a REGRESSION PIN, not a style preference. It
is the only thing standing between a well-researched opener and a monologue
that hangs up the call.
"""
from __future__ import annotations

import pytest

from app.domain.services.telephony_session_config import (
    _MAX_SPOKEN_CALL_REASON_CHARS,
    _MAX_SPOKEN_CALL_REASON_WORDS,
    _PERSONA_GREETINGS,
    _PERSONA_GREETINGS_WITH_REASON,
    _call_reason_for,
    build_persona_greeting,
)

# Within both caps (24 chars / 4 words) so it survives `_call_reason_for`.
REASON = "cutting your energy bill"

# Budget constants, kept here so a failure message can show the sum.
MAX_OPENER_WORDS = 12
WORDS_PER_SECOND = 2.8


def _spoken_words(text: str) -> list[str]:
    """Words a TTS engine actually voices.

    ``str.split()`` counts a standalone em dash as a word; it is a pause, not a
    word, and pausing is what we WANT the opener to do. Anything with no
    alphanumeric character is dropped.
    """
    return [w for w in text.split() if any(c.isalnum() for c in w)]


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


def _assert_within_budget(label: str, rendered: str) -> None:
    words = len(_spoken_words(rendered))
    assert words <= MAX_OPENER_WORDS, (
        f"{label} opener is {words} words (~{words / WORDS_PER_SECOND:.1f}s "
        f"spoken). Budget is {MAX_OPENER_WORDS} words / "
        f"~{MAX_OPENER_WORDS / WORDS_PER_SECOND:.1f}s = the 8s decision window "
        f"minus the 4.0s recording notice that is spoken first. A call died of "
        f"exactly this on 2026-08-05. Opener: {rendered!r}"
    )


def test_reason_openers_fit_inside_the_decision_window():
    """Every reason-first template, rendered with a reason AT the cap."""
    # The reason a real campaign supplies can be as long as the cap allows, so
    # the budget has to hold at the cap, not at a convenient short example.
    reason_at_cap = "a" * (_MAX_SPOKEN_CALL_REASON_CHARS - 3)
    reason_at_word_cap = " ".join(["word"] * _MAX_SPOKEN_CALL_REASON_WORDS)
    for persona, templates in _PERSONA_GREETINGS_WITH_REASON.items():
        for template in templates:
            for reason in (REASON, reason_at_cap, reason_at_word_cap):
                _assert_within_budget(
                    f"{persona} (reason-first)",
                    template.format(
                        agent_name="Sarah",
                        company_name="Allstate",
                        call_reason=reason,
                    ),
                )


def test_every_outbound_opener_fits_inside_the_decision_window():
    """The no-reason table too — the 2026-08-05 hang-up was one of THESE.

    The campaign had an empty `campaign_slots`, so no call_reason, so the
    reason-first path never fired and the callee got the generic lead_gen
    outbound variant. Pinning only the reason-first templates would have left
    the line that actually killed the call unguarded.
    """
    for persona, by_direction in _PERSONA_GREETINGS.items():
        for template in by_direction.get("outbound", []):
            _assert_within_budget(
                f"{persona} outbound",
                template.format(agent_name="Sarah", company_name="Allstate"),
            )


def test_the_budget_survives_the_public_entry_point():
    """Pin the function callers actually use, not just the tables.

    A future variant added through `build_persona_greeting`'s fallback path
    (unknown persona -> generic builder) is still a real spoken opener.
    """
    for persona in (None, "lead_gen", "customer_support", "receptionist"):
        for call_reason in (None, REASON):
            for _ in range(30):  # variants are chosen at random
                _assert_within_budget(
                    f"build_persona_greeting(persona={persona!r})",
                    build_persona_greeting(
                        persona_type=persona,
                        agent_name="Sarah",
                        company_name="Allstate",
                        direction="outbound",
                        call_reason=call_reason,
                    ),
                )


def test_no_opener_asks_permission_to_proceed_with_no_context_first():
    """"Got a quick second?" / "Do you have a minute?" are the bottom-scoring
    pattern *because they arrive with nothing in front of them*. Shortening the
    openers must not shorten them back into that shape: whatever ask ends the
    line, identity has to come first.
    """
    for persona, by_direction in _PERSONA_GREETINGS.items():
        for template in by_direction.get("outbound", []):
            rendered = template.format(agent_name="Sarah", company_name="Allstate")
            assert "Sarah" in rendered and "Allstate" in rendered, (
                f"{persona} opener drops identity: {rendered}"
            )
            first_question = rendered.find("?")
            if first_question != -1:
                assert rendered.find("Allstate") < first_question, (
                    f"{persona} opener asks before it says who is calling — "
                    f"that is the bare permission-to-proceed ask: {rendered}"
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
        # 24 chars — inside the char cap — but six words. Speaking time tracks
        # words, so the char cap alone would have let this overrun.
        ({"call_reason": "we can cut your bill now"}, None),
    ],
)
def test_call_reason_extraction(slots, expected):
    assert _call_reason_for({"campaign_slots": slots}) == expected


def test_reason_caps_match_the_template_arithmetic():
    """The caps are not taste — they are 12 words minus the template.

        12 word budget - 8 fixed template words = 4 words for the reason
        4 words x ~6.2 chars/word (mean English word + space) ~= 25 chars

    If someone widens a cap without shortening a template, the openers overrun
    again — so pin the relationship, not just the numbers.
    """
    assert _MAX_SPOKEN_CALL_REASON_WORDS == 4
    assert _MAX_SPOKEN_CALL_REASON_CHARS <= 30, (
        "a reason longer than ~30 chars cannot fit the 4.0s greeting budget "
        "alongside the 8-word template"
    )
    # And the templates really do leave that much room.
    for persona, templates in _PERSONA_GREETINGS_WITH_REASON.items():
        for template in templates:
            fixed = len(
                _spoken_words(
                    template.format(
                        agent_name="Sarah", company_name="Allstate", call_reason=""
                    )
                )
            )
            assert fixed + _MAX_SPOKEN_CALL_REASON_WORDS <= MAX_OPENER_WORDS, (
                f"{persona} template spends {fixed} words before the reason; "
                f"only {MAX_OPENER_WORDS - _MAX_SPOKEN_CALL_REASON_WORDS} are "
                f"available: {template}"
            )


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
