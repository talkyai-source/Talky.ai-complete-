"""SMALL-DIALOGUE invariants for the composed voice prompt.

WHY THIS EXISTS (2026-08-07)
----------------------------
The owner's words, verbatim: "cut off all the monologues make the conversation
real like hi hello like from small dialogues nature not like this monologues
they are useless."

The evidence behind it is the same call the 2026-08-06 pass was written from —
a callee sat through 11.7s of unbroken agent audio (4s recording notice + a
7.7s greeting) and hung up the instant it ended, without ever trying to
interrupt; production turns measured ``llm_total_ms=11380`` /
``tts_total_ms=10774``. But the ASK is different from "be brief", and so is
this test file. 2026-08-06 lowered the CEILING (three sentences -> "the fewest
that actually answer it") and killed process narration. Those tests live in
``test_prompt_brevity_and_narration.py`` and still hold. This file pins the
SHAPE the ceiling never described:

  * A default turn of ONE SHORT BEAT — often a fragment, not a sentence.
    Measured voice dialogues run ~14 turns / ~800 words TOTAL, so the natural
    shape is MANY SHORT turns, not few long ones. Human turn gaps are ~200ms
    and listeners expect a reply inside ~300ms; long turns break that rhythm.
  * ACKNOWLEDGE then ask, rather than explain then ask.
  * The floor handed back constantly instead of held.

DELIBERATELY NOT PINNED — a numeric per-turn word cap. Emails, phone numbers
and prices are CORE fields where one wrong character fails the task, and the
whole-value read-back that guarantees them is legitimately long. A per-turn
word cap would be obeyed exactly where it hurts (read-backs) and ignored where
it matters (prose). The opener is the one place a number IS used, because it is
one-shot and measurable — and it IS pinned below, across all three blocks that
size it.

The one lever this file leans on hardest is the FEW-SHOT EXEMPLARS. The
2026-08-06 pass established, from the transcripts, that the exemplars were
teaching narration and outweighing the prose that told the model to be brief.
That finding cuts both ways: applied positively, a six-word ``AGENT:`` line
teaches more than any rule sentence can. So the exemplars are held to a real
budget here, with a POSITIVE CONTROL that feeds the pre-2026-08-07 exemplars
to the same assertions and proves they would have failed.
"""
from __future__ import annotations

import re

import pytest

from app.services.scripts.prompts import compose_prompt
from app.services.scripts.prompts.composer import FINAL_RESPONSE_CONTRACT
from app.services.scripts.prompts.direction import inbound_directive_block
from app.services.scripts.prompts.guardrails import (
    GENERIC_GUARDRAILS_HARD,
    GENERIC_GUARDRAILS_REST,
)
from app.services.scripts.prompts.live_state import build_live_state_block


LEAD_GEN_SLOTS = {
    "industry": "roofing",
    "services_description": "residential roofing",
    "coverage_area": "greater Austin",
    "value_proposition": "replace your roof without upfront cost",
    "call_reason": "we noticed homes in your area upgrading",
    "qualification_questions": ["Are you the homeowner?"],
    "disqualifying_answers": ["renting"],
    "calendar_booking_type": "a free home assessment",
}

SUPPORT_SLOTS = {
    "business_hours": "M-F 9-6",
    "website": "cloudco.io",
    "support_email": "help@cloudco.io",
    "refund_policy": "30 days",
    "cancellation_policy": "anytime",
    "complaint_policy": "reviewed in 48h",
    "support_topics": ["billing", "tech"],
    "common_issues": [{"issue": "cannot login", "solution": "send password reset"}],
    "escalate_triggers": ["data breach"],
    "escalate_to": "technical team",
    "escalation_wait_time": "30 minutes",
}

RECEPTIONIST_SLOTS = {
    "business_type": "dental practice",
    "business_address": "123 Main St",
    "business_phone": "555-0100",
    "business_email": "hello@bright.com",
    "website": "bright.com",
    "opening_hours": {"Mon-Fri": "9-6"},
    "services": ["cleaning"],
    "emergency_protocol": "same-day slots",
    "new_patient_info_needed": ["full name"],
}

ALL_PERSONAS = (
    ("lead_gen", LEAD_GEN_SLOTS),
    ("customer_support", SUPPORT_SLOTS),
    ("receptionist", RECEPTIONIST_SLOTS),
)


def _flat(text: str) -> str:
    """Collapse whitespace runs. Prompt blocks are hard-wrapped at ~78 chars,
    so any asserted phrase longer than a few words crosses a newline in the
    composed output."""
    return re.sub(r"\s+", " ", text)


def _composed(persona: str, slots: dict, **kw) -> str:
    return _flat(compose_prompt(persona, "Alex", "Acme", slots, **kw))


# ---------------------------------------------------------------------------
# 0. Exemplar measurement helpers — shared by the real assertions AND by the
#    positive controls, so a control cannot pass by using a softer ruler.
# ---------------------------------------------------------------------------

_EXEMPLAR_RE = re.compile(
    r"^\s*AGENT:\s*(.+(?:\n(?!\s*(?:USER|AGENT):).+)*)", re.MULTILINE
)


def _agent_exemplars(text: str) -> list[str]:
    """Every ``AGENT:`` few-shot line in ``text``, wrapped lines rejoined."""
    return [_flat(m).strip() for m in _EXEMPLAR_RE.findall(text)]


def _spoken_words(line: str) -> int:
    """Words the caller would actually HEAR.

    Punctuation-only tokens are dropped — an em dash is written, not spoken,
    and counting "—" as a word would let a rewrite pass the budget by adding
    dashes. This is the honest unit for "how long is this turn out loud"; at
    ~2.8 words/sec, 12 words is ~4.3s and 6 words is ~2.1s.
    """
    return len([t for t in line.split() if re.search(r"[A-Za-z0-9]", t)])


# The exemplars EXACTLY as they shipped before this pass, kept verbatim as
# positive-control fixtures. Every budget below is asserted to REJECT these.
_OLD_LEAD_GEN_EXEMPLARS = """\
  USER: What's this about?
  AGENT: Yeah, fair question — quick version, we help folks like you stop missing calls. Worth thirty seconds?
  USER: We already use someone for that.
  AGENT: Ah, got it — yeah, most people we talk to do. What's the one thing you wish they did better?
  USER: We're slammed right now, honestly.
  AGENT: Mm, totally fair — sounds full-on. Want me to catch you another time, or fire over a quick note instead?
  USER: We lose a fair bit to slow payouts.
  AGENT: Oof, yeah — that one stings on a busy week. So if the money just landed same-day, what would that change for you?
"""

_OLD_SUPPORT_EXEMPLARS = """\
USER: My order has not arrived yet.
AGENT: Right — what is your order number?

USER: I have been on hold for an hour, this is ridiculous.
AGENT: Yeah, that is not how this should go — sorry. What were you calling
about?

USER: I want to cancel my account.
AGENT: Got it. Cancel everything, or just the one plan?
"""


# ---------------------------------------------------------------------------
# 1. The exemplars ARE the length instruction. Hold them to a spoken budget.
# ---------------------------------------------------------------------------

# 14 words is ~5.0s of speech at 2.8 words/sec; the mean, ~3.6s. Chosen so the
# SHAPE is enforced and not just the ceiling: test_prompt_brevity_and_narration
# already caps exemplars at 25 words (~9s), and the old monologue-teaching set
# passed that comfortably — its worst line was 22 words.
_MAX_EXEMPLAR_WORDS = 14
_MAX_MEAN_EXEMPLAR_WORDS = 10
# "A fragment is a whole turn" is a rule in the prompt; it is only believable
# if the exemplars demonstrate it. At least a third must be one short beat.
_BEAT_WORDS = 7
_MIN_BEAT_FRACTION = 1 / 3


@pytest.mark.parametrize("persona,slots", ALL_PERSONAS)
def test_no_exemplar_is_longer_than_one_short_beat(persona, slots):
    """A 6-word exemplar teaches more than any rule sentence. The model copies
    exemplar LENGTH — that is the mechanism the 2026-08-06 transcripts pinned
    down — so the ceiling that actually binds is this one, not the prose."""
    raw = compose_prompt(persona, "Alex", "Acme", slots)
    exemplars = _agent_exemplars(raw)
    assert exemplars, f"{persona} lost its few-shot exemplars"
    for line in exemplars:
        words = _spoken_words(line)
        assert words <= _MAX_EXEMPLAR_WORDS, (
            f"{persona} exemplar is {words} spoken words "
            f"(~{words / 2.8:.1f}s): {line!r}"
        )


@pytest.mark.parametrize("persona,slots", ALL_PERSONAS)
def test_the_average_exemplar_is_a_short_beat_not_a_paragraph(persona, slots):
    """A per-line cap alone lets every exemplar sit AT the cap, which is the
    exact failure mode 2026-08-06 found in HARD RULE 2 ("the model read the
    ceiling as the target and filled it"). The mean is what stops that."""
    raw = compose_prompt(persona, "Alex", "Acme", slots)
    exemplars = _agent_exemplars(raw)
    mean = sum(_spoken_words(e) for e in exemplars) / len(exemplars)
    assert mean <= _MAX_MEAN_EXEMPLAR_WORDS, (
        f"{persona} exemplars average {mean:.1f} spoken words "
        f"(~{mean / 2.8:.1f}s a turn)"
    )


@pytest.mark.parametrize("persona,slots", ALL_PERSONAS)
def test_some_exemplars_are_a_bare_fragment(persona, slots):
    """"Hmm — unreliable how?" is the whole point. Models emit grammatically
    complete sentences by default, and a complete sentence every turn is what
    reads as software; the prompt's permission to use a fragment is only
    credible if the examples beside it actually are one."""
    raw = compose_prompt(persona, "Alex", "Acme", slots)
    exemplars = _agent_exemplars(raw)
    beats = [e for e in exemplars if _spoken_words(e) <= _BEAT_WORDS]
    assert len(beats) / len(exemplars) >= _MIN_BEAT_FRACTION, (
        f"{persona}: only {len(beats)}/{len(exemplars)} exemplars are a short "
        f"beat (<={_BEAT_WORDS} words) — {exemplars}"
    )


def test_exemplar_budgets_control_flags_the_old_exemplars():
    """POSITIVE CONTROL, and the one that makes this whole file non-vacuous.
    The pre-2026-08-07 exemplars — the ones that shipped while the owner was
    watching a callee hang up on a monologue — must FAIL every budget above.
    A budget the old text already satisfied would pin nothing."""
    old = _agent_exemplars(_OLD_LEAD_GEN_EXEMPLARS)
    assert len(old) == 4                       # the fixture parsed at all

    # ...too long line by line,
    assert max(_spoken_words(e) for e in old) > _MAX_EXEMPLAR_WORDS
    # ...too long on average,
    mean = sum(_spoken_words(e) for e in old) / len(old)
    assert mean > _MAX_MEAN_EXEMPLAR_WORDS
    # ...and not one of them was a short beat.
    beats = [e for e in old if _spoken_words(e) <= _BEAT_WORDS]
    assert len(beats) / len(old) < _MIN_BEAT_FRACTION


# ---------------------------------------------------------------------------
# 2. Register: an exemplar teaches HOW to talk, not just how much.
# ---------------------------------------------------------------------------

# Uncontracted auxiliaries. Nobody says "that is not how this should go" or
# "what is your order number" on a phone. Word-boundary anchored so "I am" is
# caught but "I ambled" is not.
#
# "we are" is deliberately ABSENT: "We are, yeah." is how a person actually
# answers "are you open Saturdays?" — contracting it to "We're, yeah." would be
# wrong, and a detector that forces a wrong rewrite is worse than no detector.
# The test is for documentary register, not for apostrophe count.
_UNCONTRACTED = tuple(
    re.compile(p, re.IGNORECASE) for p in (
        r"\bI am\b", r"\byou are\b", r"\bthat is\b", r"\bit is\b",
        r"\bwhat is\b", r"\bI will\b", r"\byou will\b", r"\bwe will\b",
        r"\bdo not\b", r"\bdoes not\b", r"\bdid not\b", r"\bcannot\b",
        r"\bwill not\b", r"\bhas not\b", r"\bhave not\b", r"\bI have got\b",
    )
)


@pytest.mark.parametrize("persona,slots", ALL_PERSONAS)
def test_exemplars_speak_in_contractions(persona, slots):
    """The generic guardrails have said "contractions always" for months, but
    customer_support and receptionist wrote every spoken line WITHOUT them
    ("I am calling about", "you will hear from", "that is not how this should
    go"). It matters most in the exemplars, because a model copies an
    exemplar's REGISTER long after it stops weighting the rule — so the two
    halves of the prompt were teaching opposite voices, and the demonstrated
    one wins. This is the "sound like a person, not a document" half of the
    owner's ask; brevity alone does not get there."""
    raw = compose_prompt(persona, "Alex", "Acme", slots)
    for line in _agent_exemplars(raw):
        for pattern in _UNCONTRACTED:
            assert not pattern.search(line), (
                f"{persona} exemplar is written like a document, not speech "
                f"({pattern.pattern}): {line!r}"
            )


def test_contraction_control_flags_the_old_support_exemplars():
    """POSITIVE CONTROL — the pre-2026-08-07 customer_support exemplars ARE
    caught by the detector above. Two of the three: "what is your order
    number?" and "that is not how this should go"."""
    hits = [
        line for line in _agent_exemplars(_OLD_SUPPORT_EXEMPLARS)
        if any(p.search(line) for p in _UNCONTRACTED)
    ]
    assert len(hits) == 2, hits


# ---------------------------------------------------------------------------
# 3. The shape the exemplars demonstrate is also stated, positively, in prose.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("persona,slots", ALL_PERSONAS)
def test_a_fragment_is_licensed_as_a_whole_turn(persona, slots):
    """Stated in the guardrails, so every persona and both composition paths
    inherit it. Framed as permission with worked examples rather than as a
    prohibition on long turns — per the 2026-06-27 Pink-Elephant finding a
    prohibition leaves the model with no move to make, and it keeps writing
    the tidy full sentence because that is its default."""
    out = _composed(persona, slots)
    assert "a fragment is a whole turn" in out.lower()
    # The worked examples matter more than the rule; without them "fragment"
    # is a word the model can agree with and ignore.
    assert "Got it — when?" in out


@pytest.mark.parametrize("persona,slots", ALL_PERSONAS)
def test_acknowledge_then_ask_is_named_as_the_move(persona, slots):
    """HARD RULE 3 used to be purely subtractive — "an answer, then the
    reasoning, then a question is one part too many — cut the middle". That
    says what to delete, not what to say. The replacement shape is now named,
    which is what makes the deletion actionable."""
    out = _composed(persona, slots)
    assert "Acknowledge in a word or two, then ask" in out
    # ...and the subtractive half is still there — this replaced nothing.
    assert "one part too many" in out


@pytest.mark.parametrize("persona,slots", ALL_PERSONAS)
def test_the_agent_is_told_to_hand_the_floor_back(persona, slots):
    """Measured voice dialogues are ~14 turns / ~800 words TOTAL — the natural
    shape is many short exchanges. "Be brief" describes one turn; this
    describes the CALL, which is the thing the owner was actually complaining
    about ("small dialogues nature")."""
    out = _composed(persona, slots)
    assert "many quick exchanges traded back and forth" in out
    assert "hand the floor back early and often" in out


def test_the_recency_slot_names_the_sub_sentence_turn():
    """FINAL RESPONSE CONTRACT is the last turn-shape text before the tenant
    block and the compliance floor. It said "fewest sentences" — but a model
    reading "sentence" writes a full clause, so the unit itself was the
    ceiling. All three turn-shape blocks now name the smaller unit."""
    contract = _flat(FINAL_RESPONSE_CONTRACT)
    hard = _flat(GENERIC_GUARDRAILS_HARD)
    rest = _flat(GENERIC_GUARDRAILS_REST)

    assert "often just a few words" in contract
    assert "a few words is often the whole sentence" in hard
    # ...and none of them re-opens the door by naming a bigger allowance.
    for name, block in (("contract", contract), ("hard", hard), ("rest", rest)):
        assert "a full sentence every turn" not in block.lower(), name


# ---------------------------------------------------------------------------
# 4. The opener: three separate blocks size the SAME turn. They must agree.
# ---------------------------------------------------------------------------


def test_every_block_that_sizes_the_opener_states_the_same_budget():
    """CROSS-BLOCK CONSISTENCY, and the highest-stakes one in the prompt —
    this is the exact turn the 11.7s hang-up measured.

    Three blocks size it, at three different attention positions:
      * lead_gen STAGE 1     — "ONE breath ... under twenty words"
      * inbound directive    — position 0, and it literally says "this
                               overrides any timing or opening instructions
                               below"
      * LIVE STATE           — prepended ABOVE everything, every single turn

    Before 2026-08-07 they said "under twenty words", "1-2 short sentences",
    and "one or two sentences" respectively. The two loosest ones held the two
    strongest positions, and one of them claimed override authority — so the
    tight number was the one with no leverage. The codebase's own finding is
    that contradictory blocks are worse than a missing rule, because the model
    is free to follow whichever it weighs highest."""
    from app.services.scripts.prompts.personas.lead_gen import (
        LEAD_GEN_KD_BODY,
        LEAD_GEN_OPENINGS,
    )

    blocks = {
        "lead_gen outbound": LEAD_GEN_OPENINGS["outbound"],
        "lead_gen inbound": LEAD_GEN_OPENINGS["inbound"],
        "lead_gen knowledge-driven": LEAD_GEN_KD_BODY,
        "inbound directive": inbound_directive_block(
            agent_name="Alex", company_name="Acme"
        ),
        "live state": build_live_state_block(
            agent_name="Alex", company_name="Acme", has_introduced=False
        ),
    }
    for name, block in blocks.items():
        low = _flat(block).lower()
        assert "one breath" in low, f"{name} does not budget the opener"
        assert "twenty words" in low, f"{name} does not state the word budget"


def test_no_opener_block_offers_a_looser_sentence_budget():
    """REGRESSION PIN for the specific wording that lost. "1-2 sentences" is
    not a smaller claim than "under twenty words" — spoken, two sentences runs
    well past twenty — so it read as a licence, not a limit."""
    loose = (r"1-2 (short )?sentences", r"one or two sentences")
    blocks = {
        "inbound directive": inbound_directive_block(
            agent_name="Alex", company_name="Acme"
        ),
        "live state (not yet introduced)": build_live_state_block(
            agent_name="Alex", company_name="Acme", has_introduced=False
        ),
    }
    for name, block in blocks.items():
        low = _flat(block).lower()
        for pattern in loose:
            assert not re.search(pattern, low), f"{name} matched {pattern!r}"


def test_live_state_still_asks_for_the_opening_it_is_there_to_trigger():
    """NON-VACUITY. Shrinking the budget must not delete the instruction — the
    has_introduced=False branch exists to make the agent open at all, and an
    agent that opens with nothing is a worse bug than one that opens long."""
    block = _flat(build_live_state_block(
        agent_name="Alex", company_name="Acme", has_introduced=False
    ))
    assert "have not introduced yourself yet" in block
    assert "why you're calling" in block
    assert "stop and let them answer" in block

    # ...and the already-introduced branch is untouched by this pass.
    after = _flat(build_live_state_block(
        agent_name="Alex", company_name="Acme", has_introduced=True
    ))
    assert "Do NOT introduce yourself again" in after


# ---------------------------------------------------------------------------
# 5. The spoken "thinking" filler — the last hard-coded narration in the
#    product, and the one place the LLM rules could not reach.
# ---------------------------------------------------------------------------

_NARRATION_FILLER = (
    r"\blet me\b", r"\bone sec\b", r"\bjust a (moment|sec)\b",
    r"\bone moment\b", r"\bhave a look\b", r"\bcheck\b",
)


def test_thinking_fillers_are_a_filled_pause_not_a_lookup_announcement():
    """These strings are SPOKEN, and they were the last place still saying the
    exact process narration the prompts were scrubbed of on 2026-08-06 ("Okay,
    let me check...", "Let me have a look...", "One moment, please..."). HARD
    RULE 8 tells the model that thinking and lookups happen silently — while
    hard-coded audio announced a lookup out loud. That half always won, since
    it never passes through the LLM at all.

    A human bridges a hesitation with a filled pause, not with a sentence
    about what they are about to go and do — and the filled pause is also
    ~1s shorter, on a line where 11.7s of agent audio lost the call."""
    from app.services.scripts.prompts.accent_fillers import _THINKING_FILLERS

    for accent, pool in _THINKING_FILLERS.items():
        assert pool, accent
        for phrase in pool:
            low = phrase.lower()
            for pattern in _NARRATION_FILLER:
                assert not re.search(pattern, low), (
                    f"{accent} filler narrates a lookup ({pattern}): {phrase!r}"
                )


def test_thinking_filler_control_flags_the_old_pool():
    """POSITIVE CONTROL — every phrase the old pools used IS flagged."""
    old = ("Let me see...", "Okay, let me check...", "One moment, please...",
           "Let me have a look...", "Erm, one sec...")
    for phrase in old:
        assert any(
            re.search(p, phrase.lower()) for p in _NARRATION_FILLER
        ), phrase


def test_thinking_fillers_still_cover_the_gap_and_match_their_dialect():
    """NON-VACUITY + cross-block consistency. The filler exists to stop dead
    air, so deleting it is not the fix — it must still be a real utterance.
    And it is spoken in the SAME dialect the accent block prescribes: the
    British block says to write "er"/"erm" and NEVER "um"/"uh" (Tottie 2011),
    which the old British pool itself honoured but the American-flavoured
    NEUTRAL fallback did not have to."""
    from app.services.scripts.prompts.accent_fillers import (
        BRITISH,
        IRISH,
        _THINKING_FILLERS,
        thinking_filler,
    )

    for accent, pool in _THINKING_FILLERS.items():
        for phrase in pool:
            assert phrase.strip(" ."), f"{accent} has an empty filler"
            # Something a voice can actually say, not just punctuation.
            assert re.search(r"[A-Za-z]", phrase), phrase
        assert thinking_filler(accent) in pool

    # British hesitations are "er"/"erm", never the American "um"/"uh".
    for phrase in _THINKING_FILLERS[BRITISH]:
        assert not re.search(r"\b(um|uh)\b", phrase.lower()), phrase
    # Irish is "em" (Hiberno-English), per the dialect block above it.
    assert any("em" in p.lower() for p in _THINKING_FILLERS[IRISH])
