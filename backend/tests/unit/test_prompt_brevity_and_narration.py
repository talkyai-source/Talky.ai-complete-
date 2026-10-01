"""Turn-length and process-narration invariants for the composed voice prompt.

WHY THIS EXISTS (2026-08-06)
----------------------------
Two production signals, both about the agent talking too much and too long:

1. A callee was answered, sat through 11.7 seconds of uninterrupted agent audio
   (a 4s recording notice followed by a 7.7s greeting) and hung up the instant
   it finished — without ever attempting to interrupt. Measured turns from
   earlier production calls reached ``llm_total_ms=11380`` and
   ``tts_total_ms=10774``. An eleven-second agent turn on a phone line is a
   monologue, and the caller has no way in.

2. Transcripts had the agent narrating its own internals out loud:
       "Let me think about the simplest way to point you forward."
       "One sec, let me check the official info so I don't guess."
       "I couldn't find a clear location statement in the company info I
        pulled..."
   — reasoning, retrieval, and knowledge-base MISSES, spoken to the caller.

The prompt text was a cause of both, not just a bystander:

  * HARD RULE 2 named a three-sentence CEILING and the model spent it.
  * COMMUNICATION PRINCIPLES prescribed "answer, then one short reason, then at
    most one question" — literally the statement + explanation + question stack
    that produces a long turn — while HARD RULE 3 was trying to shorten turns.
    Two composed blocks disagreeing on turn shape is worse than either rule
    alone.
  * customer_support and receptionist listed ``"let me see"`` as a recommended
    filler AND demonstrated it in their few-shot exemplars ("Right, let me look
    into that", "Let me check that for you", "let me see what was handed in").
    A few-shot example outweighs the surrounding prose; the personas were
    teaching the exact behaviour the guardrails forbid.
  * The lead_gen opener example ran ~40 words (~14s spoken). An example IS a
    length instruction.

These tests pin the CORRECTED state. They are deliberately intent-based (a
handful of anchor phrases + cross-block consistency), not exact-wording
snapshots — the audit history in these modules shows wording gets compressed
regularly and brittle pins turn a rewrite into a fake regression.
"""
from __future__ import annotations

import re

import pytest

from app.services.scripts.prompts import compose_prompt
from app.services.scripts.prompts.composer import (
    FINAL_RESPONSE_CONTRACT,
    KNOWLEDGE_PRECEDENCE,
)
from app.services.scripts.prompts.guardrails import (
    COMMUNICATION_PRINCIPLES,
    GENERIC_GUARDRAILS_HARD,
)


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
    composed output. Operators care about the sentence, not the wrap."""
    return re.sub(r"\s+", " ", text)


def _composed(persona: str, slots: dict, **kw) -> str:
    return _flat(compose_prompt(persona, "Alex", "Acme", slots, **kw))


# ---------------------------------------------------------------------------
# 1. Brevity: the SHORTEST turn is the default, in every persona and on both
#    the slot-based and the knowledge-driven composition paths.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("persona,slots", ALL_PERSONAS)
def test_fewest_sentences_instruction_reaches_every_persona(persona, slots):
    """HARD RULE 2 is what makes one sentence — not three — the target. It is
    composed for every persona, so no campaign can end up without it."""
    out = _composed(persona, slots)
    assert "Answer in the fewest sentences that actually answer it" in out
    assert "One sentence is the whole turn most of the time" in out


@pytest.mark.parametrize("persona,slots", ALL_PERSONAS)
def test_knowledge_driven_campaigns_get_the_same_brevity_target(persona, slots):
    out = _composed(persona, slots, knowledge_driven=True)
    assert out.count("Answer in the fewest sentences that actually answer it") == 1
    assert "often just a few words" in out


@pytest.mark.parametrize("persona,slots", ALL_PERSONAS)
def test_one_question_then_stop_talking(persona, slots):
    out = _composed(persona, slots)
    assert "Ask at most ONE question per turn" in out
    assert "let it be the last thing you say, then stop talking" in out


@pytest.mark.parametrize("persona,slots", ALL_PERSONAS)
def test_no_statement_explanation_question_stack(persona, slots):
    out = _composed(persona, slots)
    assert "let that be the whole turn; add a question only when useful" in out
    assert "then one short reason if needed" not in out


# ---------------------------------------------------------------------------
# 2. The contradiction that made the brevity rules unenforceable.
# ---------------------------------------------------------------------------


def test_communication_principles_do_not_license_the_three_part_stack():
    """REGRESSION PIN. COMMUNICATION PRINCIPLES used to read "lead with the
    answer ..., then one short reason if needed, then at most one question" —
    a licence for exactly the stack HARD RULE 3 forbids, composed into the same
    prompt a few hundred tokens apart. The block is shared with Ask AI, so the
    contradiction shipped to two products."""
    flat = _flat(COMMUNICATION_PRINCIPLES)
    # The distilled lead-with-the-answer idea survives (Ask AI pins it too)...
    assert "lead with the answer" in flat
    # ...but not the "then a reason, then a question" chain.
    assert "then one short reason if needed" not in flat
    assert "let that be the whole turn" in flat


def test_the_three_turn_shape_blocks_agree_with_each_other():
    # The communication block now owns turn shape once; final contract refers
    # to it instead of duplicating a competing ceiling in three places.
    assert "fewest sentences" in COMMUNICATION_PRINCIPLES
    assert "communication principles above" in FINAL_RESPONSE_CONTRACT
    assert "fewest sentences" not in GENERIC_GUARDRAILS_HARD
    for block in (GENERIC_GUARDRAILS_HARD, COMMUNICATION_PRINCIPLES, FINAL_RESPONSE_CONTRACT):
        assert "up to three" not in block.lower()


@pytest.mark.parametrize("persona,slots", ALL_PERSONAS)
def test_no_composed_block_encourages_length_or_thoroughness(persona, slots):
    """The composed prompt must contain no instruction to be long, thorough, or
    explanatory by default. This is the catch-all that stops a future edit from
    re-introducing the licence that was removed from lead_gen's HOW YOU SOUND
    ("If they're engaged ... open up — to at most the three sentences Hard Rule
    2 allows")."""
    out = _composed(persona, slots)
    low = out.lower()
    banned = (
        "open up — to at most",   # the removed lead_gen licence, verbatim
        "be thorough",
        "be detailed",
        "in detail",
        "explain your reasoning",
        "walk them through",
        "as much detail as",
        "feel free to elaborate",
    )
    for phrase in banned:
        assert phrase not in low, f"{persona} prompt encourages length: {phrase!r}"


# ---------------------------------------------------------------------------
# 3. Process narration: reasoning, lookups and KB misses stay silent.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("persona,slots", ALL_PERSONAS)
def test_thinking_and_lookups_are_silent(persona, slots):
    out = _composed(persona, slots)
    assert "Thinking, reading and tools all happen silently" in out
    assert "the caller hears the answer" in out


@pytest.mark.parametrize("persona,slots", ALL_PERSONAS)
def test_a_knowledge_miss_is_a_next_step_not_a_status_report(persona, slots):
    out = _composed(persona, slots)
    assert "say you cannot confirm it" in out
    assert "Offer only a next step actually available" in out
    assert "I'll get you that exact figure" not in out


def test_never_mention_the_knowledge_base_covers_its_paraphrases():
    flat = _flat(KNOWLEDGE_PRECEDENCE)
    assert "do not narrate searches, the knowledge base or internal systems" in flat


# Checked against the persona BODIES rather than the whole composed prompt:
# these are the modules that were teaching the behaviour, and a whole-prompt
# scan is both noisier and prone to substring false positives (the compliance
# floor's "have it d-one sec-urely" contains "one sec").
_NARRATION_PATTERNS = (
    r"\blet me (see|check|look|have a look)\b",
    r"\bone sec\b",
    r"\bjust a (moment|sec)\b",
    r"\bbear with me\b",
)


@pytest.mark.parametrize(
    "module_name",
    ["customer_support", "receptionist", "lead_gen"],
)
def test_personas_no_longer_teach_process_narration(module_name):
    """REGRESSION PIN, and the sharpest one here. customer_support and
    receptionist were the SOURCE of the narration: NATURAL SPEECH listed
    "let me see" as a filler to USE, and their few-shot exemplars performed it
    ("Right, let me look into that", "Let me check that for you — yes, we
    are open...", "let me see what was handed in"). A few-shot example is the
    strongest instruction in a prompt — the model copies the exemplar long
    after it has stopped weighting the prose around it."""
    import importlib

    mod = importlib.import_module(
        f"app.services.scripts.prompts.personas.{module_name}"
    )
    # Every prompt STRING the module exports (skip the module docstring, which
    # documents the old wording on purpose).
    texts = [
        v for k, v in vars(mod).items()
        if isinstance(v, str) and not k.startswith("__")
    ] + [
        s for v in vars(mod).values() if isinstance(v, dict)
        for s in v.values() if isinstance(s, str)
    ]
    assert texts, f"{module_name} exported no prompt text"
    for text in texts:
        low = _flat(text).lower()
        for pattern in _NARRATION_PATTERNS:
            assert not re.search(pattern, low), (
                f"{module_name} still teaches process narration "
                f"({pattern!r}): {low[:200]!r}"
            )


@pytest.mark.parametrize(
    "persona,slots",
    [("customer_support", SUPPORT_SLOTS), ("receptionist", RECEPTIONIST_SLOTS)],
)
def test_filler_permission_survives_the_narration_fix(persona, slots):
    """The fix was to make the fillers WORDS rather than sentences about
    upcoming work — not to delete them. A persona with no fillers reads as a
    service bot, which T4-A3 added them to prevent."""
    out = _composed(persona, slots)
    assert "Use occasional fillers" in out
    assert "never a sentence" in out.lower()


@pytest.mark.parametrize("persona,slots", ALL_PERSONAS)
def test_few_shot_exemplars_are_short(persona, slots):
    """Every AGENT: exemplar is a length instruction the model will copy. Cap
    them at roughly one spoken breath (~25 words / ~9s at 2.8 words per
    second), which is inside the 8-12s window a caller decides in."""
    raw = compose_prompt(persona, "Alex", "Acme", slots)
    exemplars = re.findall(r"^\s*AGENT:\s*(.+(?:\n(?!\s*(?:USER|AGENT):).+)*)",
                           raw, re.MULTILINE)
    assert exemplars, f"{persona} lost its few-shot exemplars"
    for line in exemplars:
        words = len(_flat(line).split())
        assert words <= 25, (
            f"{persona} exemplar is {words} words (~{words / 2.8:.1f}s spoken) "
            f"— the model copies exemplar LENGTH: {_flat(line)!r}"
        )


# ---------------------------------------------------------------------------
# 4. The opener — the turn the 11.7s hang-up actually measured.
# ---------------------------------------------------------------------------


def test_lead_gen_opener_shape_fits_the_decision_window():
    """The quoted shape in STAGE 1 is what the model imitates when it composes
    its own opener. The old one ran ~40 words (~14s) and the callee hung up at
    11.7s. The spoken greeting templates in telephony_session_config.py are
    capped at <=25 words by test_openers_fit_inside_the_decision_window; the
    prompt-side shape is now held to the same budget so the two agree."""
    from app.services.scripts.prompts.personas.lead_gen import LEAD_GEN_OPENINGS

    for direction, block in LEAD_GEN_OPENINGS.items():
        # Match EVERY quoted span, with no length filter. A minimum length in
        # the pattern silently breaks quote parity — a too-short quoted phrase
        # fails to match, the engine restarts on its CLOSING quote, and every
        # later pair is off by one. (That bug hid the shape entirely here.)
        quoted = re.findall(r'"([^"]*)"', block)
        shapes = [
            q for q in quoted
            if "{agent_name}" in q and "{company_name}" in q
        ]
        assert shapes, f"{direction} opener lost its example shape"
        for shape in shapes:
            rendered = shape.format(
                agent_name="Sarah",
                company_name="Allstate",
                call_reason="cutting your energy bill",
            )
            words = len(rendered.split())
            assert words <= 25, (
                f"lead_gen {direction} opener shape is {words} words "
                f"(~{words / 2.8:.1f}s spoken): {rendered!r}"
            )


@pytest.mark.parametrize("direction", ["outbound", "inbound"])
def test_lead_gen_opener_tells_the_agent_to_stop(direction):
    """The 11.7s call ended with the callee hanging up the moment the agent
    stopped, having never tried to interrupt. An opener instruction that does
    not say STOP leaves the model free to keep going into qualification."""
    from app.services.scripts.prompts.personas.lead_gen import LEAD_GEN_OPENINGS

    block = _flat(LEAD_GEN_OPENINGS[direction])
    # A one-breath budget, and an explicit hand-back of the floor.
    assert "one breath" in block.lower()
    assert "stop" in block.lower()


def test_lead_gen_opener_dropped_the_worst_measured_pattern():
    """CROSS-MODULE CONSISTENCY. On 2026-08-02 the spoken greeting templates
    banned the "bad time / get lost" family — Gong's 300M+ call dataset puts it
    at 2.15%, the worst-converting opener measured, versus 11.18% for
    own-the-cold-call. The persona prompt still instructed the model to say
    exactly that, so the two halves of the product disagreed on the single
    highest-stakes sentence of the call. It also cost ~10 words in the one turn
    where length is measured in lost prospects."""
    from app.services.scripts.prompts.personas.lead_gen import (
        LEAD_GEN_KD_BODY,
        LEAD_GEN_OPENINGS,
    )

    banned = ("get lost", "bad moment")
    for name, block in (
        ("outbound", LEAD_GEN_OPENINGS["outbound"]),
        ("inbound", LEAD_GEN_OPENINGS["inbound"]),
        ("knowledge-driven", LEAD_GEN_KD_BODY),
    ):
        low = _flat(block).lower()
        for phrase in banned:
            assert phrase not in low, f"{name} opener still offers {phrase!r}"
        # "is this a bad time?" survives only as the thing NOT to say.
        if "bad time" in low:
            assert "never" in low or "no " in low, name
