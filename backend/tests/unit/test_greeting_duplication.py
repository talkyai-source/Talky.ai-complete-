"""Regression tests for the "multiple hi and hello" bug on live calls.

Three independent sources stacked greetings onto the start of a call:

  #2  ``lead_gen.py`` repeated "Open with who you are, your company, and the
      honest reason you called..." inside HOW YOU SOUND **(every turn)** — so
      every single turn re-asserted the opener, directly contradicting the
      per-turn LIVE STATE block's "Do NOT introduce yourself again"
      (prompts/live_state.py) and duplicating Stage 1.

  #3  the persona's first few-shot exemplar was ``USER: Hello?`` ->
      ``AGENT: Hi — it's {agent_name} from {company_name}...``. On an
      agent-first call the callee's first utterance *after* the opener is very
      often literally "Hello?", so the exemplar trained a full re-greeting at
      exactly the wrong moment.

  #4  ``turn_director.OPENING_PHRASES`` was ``["Hello?", "Hi, can you hear me
      okay?"]``. Those fire on a caller-first call BEFORE the real opener, so a
      silent pickup heard "Hello?" then "Hi, can you hear me okay?" then
      "Hi, it's James from Allstate..." — three greetings.

Every assertion below is paired with a POSITIVE CONTROL that feeds the
detector the exact pre-fix text and asserts it *is* flagged. That is what
makes these tests non-vacuous: a detector that always passes would fail its
own control.
"""
from __future__ import annotations

import re

from app.domain.services.voice_pipeline.turn_director import (
    OPENING_PHRASES,
    choose_silence_phrase,
)
from app.services.scripts.prompts.personas.lead_gen import (
    LEAD_GEN_KD_BODY,
    LEAD_GEN_OPENINGS,
    LEAD_GEN_PLAYBOOK,
)

# ── the exact pre-fix text, kept verbatim as positive-control fixtures ──────

_OLD_EVERY_TURN_BULLET = (
    "- Open with who you are, your company, and the honest reason you called "
    "— get to\n  the reason in your first breath, then hand them the floor.\n"
)

_OLD_FEW_SHOT = """\
  USER: Hello?
  AGENT: Hi — it's {agent_name} from {company_name}. Quick one: I'm calling
    about one specific thing I reckon could help your business — feel free to
    tell me to get lost if it's a bad moment, but got a second?
  USER: I'm kind of in the middle of something.
  AGENT: Oh — no worries at all. Want me to try you later today, or tomorrow?
"""

_OLD_OPENING_PHRASES = ["Hello?", "Hi, can you hear me okay?"]


# ── helpers ────────────────────────────────────────────────────────────────

def _section(text: str, heading: str) -> str:
    """Return the body of a top-level persona section.

    A section heading is an unindented line opening with two consecutive
    capitals ("HOW YOU SOUND...", "EXAMPLES — ...", "THE CALL — ..."). The
    body runs to the next such heading, so an unindented *continuation* of the
    heading's own sentence ("/ ah / mm / right).", "Don't recite these...")
    stays inside the section.
    """
    lines = text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.startswith(heading))
    except StopIteration:  # pragma: no cover - guarded by the tests below
        raise AssertionError(f"section {heading!r} not found in persona text")

    def _is_heading(ln: str) -> bool:
        return len(ln) >= 2 and ln[:2].isalpha() and ln[:2].isupper()

    body: list[str] = []
    for ln in lines[start + 1:]:
        if _is_heading(ln):
            break
        body.append(ln)
    return "\n".join(body)


_INTRO_INSTRUCTION_PATTERNS = (
    r"\bwho you are\b",          # "open with / lead with who you are"
    r"your name,\s*your company",
    r"give your name",
    r"introduce yourself",
    r"reason you called",
    r"first breath",
)


def _introduction_instructions(text: str) -> list[str]:
    """Every "open with who you are / state your name + company + reason"
    style instruction found in ``text``. Non-empty == the text tells the agent
    to deliver an introduction."""
    return [
        p for p in _INTRO_INSTRUCTION_PATTERNS
        if re.search(p, text, re.IGNORECASE)
    ]


_BARE_GREETING = re.compile(
    r"^(hi|hello|hey|yo|hiya|good (morning|afternoon|evening))[\s?!.,]*$",
    re.IGNORECASE,
)


def _bare_greeting_user_lines(text: str) -> list[str]:
    """Few-shot ``USER:`` lines whose entire content is a bare greeting."""
    return [
        m.group(1).strip()
        for m in re.finditer(r"^\s*USER:\s*(.+)$", text, re.MULTILINE)
        if _BARE_GREETING.match(m.group(1).strip())
    ]


def _reintroducing_agent_lines(text: str) -> list[str]:
    """Few-shot ``AGENT:`` lines that state name AND company — i.e. a full
    self-introduction, which must never appear in a mid-call exemplar."""
    return [
        m.group(1).strip()
        for m in re.finditer(r"^\s*AGENT:\s*(.+)$", text, re.MULTILINE)
        if "{agent_name}" in m.group(1) and "{company_name}" in m.group(1)
    ]


_GREETING_RUNG = re.compile(
    r"^\s*(hi|hello|hey|hiya|good (morning|afternoon|evening))\b", re.IGNORECASE
)


def _greeting_rung_indexes(phrases: list[str]) -> list[int]:
    return [i for i, p in enumerate(phrases) if _GREETING_RUNG.match(p)]


def _greeting_word(phrase: str) -> str:
    """The greeting word a rung opens with, normalised — '' if it doesn't."""
    m = _GREETING_RUNG.match(phrase or "")
    if not m:
        return ""
    # Collapse an escalated spelling to its root so "Helloooo" == "Hello".
    word = m.group(1).lower()
    return re.sub(r"(.)\1+", r"\1", word)


def _fresh_greeting_rung_indexes(phrases: list[str]) -> list[int]:
    """Rungs that open a NEW greeting rather than escalating the first one.

    REFINED 2026-08-11. The original rule was "only rung 0 may greet at all",
    which caught the real bug — the old ladder's second rung was
    "Hi, can you hear me okay?", a DIFFERENT greeting word that reads as
    starting the conversation over, as though the first attempt never
    happened.

    But it also banned the thing a human actually does on a silent line:
    call out the SAME word, louder. "Hello?" -> "Hello??" -> "Helloooo, are
    you there?" is one person escalating one call-out; it is not two
    introductions stacked. Rung 0's word may repeat (in any escalated
    spelling); a different greeting word may not appear.
    """
    if not phrases:
        return []
    first = _greeting_word(phrases[0])
    out = []
    for i, p in enumerate(phrases[1:], start=1):
        w = _greeting_word(p)
        if w and w != first:
            out.append(i)
    return out


# ── #2: the every-turn section must not re-assert the opener ───────────────

def test_every_turn_section_has_no_opening_instruction():
    every_turn = _section(LEAD_GEN_PLAYBOOK, "HOW YOU SOUND (every turn)")
    assert _introduction_instructions(every_turn) == []


def test_every_turn_section_control_flags_the_old_bullet():
    """POSITIVE CONTROL — the deleted bullet, re-inserted, IS detected."""
    every_turn = _section(LEAD_GEN_PLAYBOOK, "HOW YOU SOUND (every turn)")
    assert _introduction_instructions(every_turn + _OLD_EVERY_TURN_BULLET)


def test_every_turn_section_is_real_and_still_intact():
    """Non-vacuity — the section exists, was actually parsed, and the bullets
    that are supposed to survive are still there (we deleted two lines, not
    the section)."""
    every_turn = _section(LEAD_GEN_PLAYBOOK, "HOW YOU SOUND (every turn)")
    assert every_turn.count("\n- ") >= 4, every_turn
    assert "Ask at most ONE question per turn" in every_turn
    assert "Read the room and match your length to it" in every_turn
    assert "Acknowledge what they just said" in every_turn
    # ...and the parse stopped at the right place.
    assert "EXAMPLES" not in every_turn


# ── the opener instruction must still live where it belongs (Stage 1) ──────

def test_opening_instruction_still_present_in_every_stage_one():
    """Deleting the every-turn copy must not lose the instruction: all three
    Stage-1 blocks (the two direction-aware openings and the knowledge-driven
    body) still carry it. Every composer path prepends one of these."""
    for name, block in (
        ("outbound", LEAD_GEN_OPENINGS["outbound"]),
        ("inbound", LEAD_GEN_OPENINGS["inbound"]),
        ("knowledge-driven", LEAD_GEN_KD_BODY),
    ):
        assert _introduction_instructions(block), f"{name} lost its opener"
        assert "{agent_name}" in block and "{company_name}" in block


def test_stage_one_still_covers_reason_first_and_the_easy_out():
    """The two specific ideas carried by the deleted bullet — "reason in your
    first breath" and "hand them the floor" — are preserved upstream.

    2026-08-11: the exact assertion on ordering changed here, deliberately.
    It used to read `"Lead with the REASON straight after your name" in
    outbound` — i.e. reason immediately after the name, nothing between them.
    That is no longer the shape: the owner's explicit ask was name -> a light
    permission ask -> reason, all in the same breath (see the "TURN 2
    PERMISSION-ASK REORDER" note above LEAD_GEN_OPENINGS in
    prompts/personas/lead_gen.py for the evidence — the earlier ban on
    opening on availability conflated a forward permission ask, which
    measures near the BEST converting openers, with asking whether now is an
    inconvenient moment for them, which is the worst). The concept this test
    guards — reason lands early, in the first breath, not deferred — still
    holds; only its exact position relative to the permission ask moved.
    """
    outbound = LEAD_GEN_OPENINGS["outbound"]
    assert "FIRST breath" in outbound
    assert "Lead with your name, then the permission ask, then the reason" in outbound
    assert "easy way to say no" in outbound
    # The knowledge-driven body now IS the shared opening + playbook (its
    # private agent-first copy was removed 2026-09-02), so it carries the same
    # two ideas in the same words.
    assert "first breath" in LEAD_GEN_KD_BODY
    assert "easy way to say no" in LEAD_GEN_KD_BODY


# ── #3: the few-shot must not key on a bare "Hello?" ───────────────────────

def test_few_shot_does_not_key_on_a_bare_hello():
    examples = _section(LEAD_GEN_PLAYBOOK, "EXAMPLES")
    assert _bare_greeting_user_lines(examples) == []


def test_few_shot_has_no_reintroducing_agent_line():
    """No exemplar may model saying name + company again mid-call."""
    examples = _section(LEAD_GEN_PLAYBOOK, "EXAMPLES")
    assert _reintroducing_agent_lines(examples) == []


def test_few_shot_controls_flag_the_old_exemplar():
    """POSITIVE CONTROL — the removed exemplar IS caught by both detectors."""
    assert _bare_greeting_user_lines(_OLD_FEW_SHOT) == ["Hello?"]
    assert len(_reintroducing_agent_lines(_OLD_FEW_SHOT)) == 1


def test_few_shot_keeps_its_teaching_value():
    """Non-vacuity — the block still exists and still teaches tone + brevity:
    the same number of exemplar pairs as before, the little spoken sounds, and
    a short acknowledge-then-one-question turn."""
    examples = _section(LEAD_GEN_PLAYBOOK, "EXAMPLES")
    users = re.findall(r"^\s*USER:", examples, re.MULTILINE)
    agents = re.findall(r"^\s*AGENT:", examples, re.MULTILINE)
    assert len(users) == 7 and len(agents) == 7
    # the spoken-sound palette the block exists to demonstrate
    for sound in ("Oh", "Hmm", "Ah", "Mm", "Right", "yeah"):
        assert sound in examples, sound
    # the replacement exemplar: mid-call, acknowledges, asks exactly one thing
    assert "Right, yeah — what's kept it sitting on the list?" in examples
    assert examples.count("?") >= 5


# ── #4: the opening silence ladder must not stack greetings ────────────────

def test_opening_ladder_never_starts_a_FRESH_greeting_after_the_first():
    """Escalating the same call-out is human; opening a new greeting is the
    bug. See _fresh_greeting_rung_indexes for why this rule was refined."""
    assert _fresh_greeting_rung_indexes(OPENING_PHRASES) == [], OPENING_PHRASES


def test_opening_ladder_control_flags_the_old_second_rung():
    """POSITIVE CONTROL — the pre-fix ladder IS still flagged: its rung 1
    ("Hi, can you hear me okay?") uses a DIFFERENT greeting word, which is
    what made it read as starting over."""
    assert _fresh_greeting_rung_indexes(_OLD_OPENING_PHRASES) == [1]


def test_escalating_the_same_call_out_is_allowed():
    """The shape the owner actually asked for, and what a person really does
    on a silent line — one call-out, repeated louder."""
    assert _fresh_greeting_rung_indexes(
        ["Hello?", "Hello??", "Helloooo, are you there?"]
    ) == []


def test_a_second_DIFFERENT_greeting_is_still_rejected():
    """Non-vacuity — the refinement must not have made the rule toothless."""
    assert _fresh_greeting_rung_indexes(["Hello?", "Hi there?"]) == [1]
    assert _fresh_greeting_rung_indexes(["Hello?", "Good morning?"]) == [1]


def test_opening_ladder_still_escalates_and_never_repeats_itself():
    """Guards the naive "just delete rung 2" fix: choose_silence_phrase clamps
    to the last rung, so a one-rung ladder would say "Hello?" twice — the same
    duplicated-greeting bug in a different coat."""
    assert len(OPENING_PHRASES) >= 2
    assert len(set(OPENING_PHRASES)) == len(OPENING_PHRASES)
    first = choose_silence_phrase(is_opening=True, nudge_index=0)
    second = choose_silence_phrase(is_opening=True, nudge_index=1)
    assert first != second
    assert first == "Hello?"


def test_opening_ladder_control_flags_a_collapsed_single_rung_ladder():
    """POSITIVE CONTROL — a one-rung ladder does duplicate, as claimed."""
    collapsed = ["Hello?"]
    idx0 = min(0, len(collapsed) - 1)
    idx1 = min(1, len(collapsed) - 1)
    assert collapsed[idx0] == collapsed[idx1]
