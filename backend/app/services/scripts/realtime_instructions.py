"""Clean instruction composer for the OpenAI gpt-realtime-2 (speech-to-speech)
pipeline mode.

WHY THIS IS SEPARATE (read before touching)
--------------------------------------------
The cascaded pipeline steers a *text* LLM with a large layered system prompt
(compose_prompt + compliance_floor + per-turn prompt_builder) and then hands
the text to a TTS engine that is nudged with bracketed audio tags
(ElevenLabs/Cartesia). NONE of that transfers to a speech-to-speech model:

  * A realtime model produces the audio itself, so bracket audio-tags
    ("[laughs]", "<break>") would be spoken literally or ignored — expression
    is steered with plain natural-language direction instead.
  * The cascaded prompt is tuned around STT quirks, read-back gates, and
    per-turn captured-slot headers that simply do not exist in a duplex
    session with server-side VAD.

So this composer deliberately imports NOTHING from
`app.services.scripts.prompts.*` (composer / guardrails / prompt_builder /
personas) and NOTHING from the TTS audio-tag builders. It writes a tight,
voice-first instruction string from scratch. Keeping the two paths physically
separate is the whole point of the realtime add-on.

Structure follows OpenAI's realtime prompting guide: SHORT labeled sections,
precise trigger->action rules, no overlapping/conflicting directives.

Output shape (one string, labeled blocks):
  1. WHO YOU ARE        — name, company, role, the campaign goal.
  2. HOW YOU OPEN       — greet, say who/why, ask one question, hand back.
  3. HOW YOU SOUND      — natural-language voice direction for a
                          speech-to-speech model (warmth, genuine laughter,
                          natural hesitation/pace-matching, short turns).
  4. GROUND RULES       — plain-language must-nots (be honest you're an AI and
                          name it when asked, never read back card/SSN/OTP, stop
                          when asked, only state given/looked-up facts).
  5. KNOWLEDGE          — a knowledge_lookup function exists; use it for company
                          facts, and cover the lookup pause with a natural verbal
                          hold (the anti-dead-air preamble) so the caller never
                          hears dead silence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class RealtimePersona:
    """Minimal persona/campaign inputs for the realtime instruction string.

    Intentionally small and self-contained — the realtime path does not reuse
    the cascaded PersonaType / campaign-slot machinery.
    """
    agent_name: str = "Alex"
    company_name: str = "the company"
    role: str = "a friendly voice assistant"
    goal: str = "have a helpful, natural conversation with the caller"
    # Optional extra, operator-supplied freeform guidance (kept short).
    extra_notes: Optional[str] = None


# ── Block 2: expressive delivery, written for a speech-to-speech model ───────
_EXPRESSIVE_DELIVERY = """\
HOW YOU SOUND
You are on a live phone call. Talk like a real person, not a script.
- Be warm, genuine, and present, and let real emotion through — pleased when
  they share good news, sympathetic with a problem, curious when you ask.
  Smile with your voice.
- Back-channel like a human listener ("mm-hmm", "right", "gotcha", "oh, nice")
  and acknowledge what they just said before moving on.
- Match the caller's energy and pace: slow down when they're thinking, pick it
  up when they're brisk. Leave natural little pauses and light fillers ("hmm",
  "let me see", "so…") instead of rushing your words together.
- If something is genuinely funny, let a real laugh come through — never forced.
- Keep turns short: say one thing, then hand back. Ask, listen, react — don't
  monologue.
- Don't spell things out letter by letter or read punctuation aloud unless the
  caller explicitly asks you to confirm something character by character."""

# ── Block 3: compliance essentials, in plain speech-to-speech language ───────
_COMPLIANCE_ESSENTIALS = """\
GROUND RULES (always)
- Be honest about what you are, and never claim or imply you're human. Keep the
  technology, models, and vendors to yourself unless asked. When the caller asks
  whether you're a bot, an AI, or a real person — "are you a real person?", "am I
  talking to a bot?", "is this AI?" — answer THAT question first and warmly: name
  that you're an AI, then carry right on helping ("Yeah — I'm an AI assistant, but
  I can genuinely help you with this. So, where were we?"). Keep it brief and
  friendly, and stay on the call.
- Never read back, repeat, or confirm full credit-card numbers, social-security
  numbers, or one-time passcodes. If a caller starts reading one, gently steer
  away and do not echo the digits.
- The moment a caller wants to stop, opt out, or not be called again, respect
  it immediately, acknowledge warmly, and wind the call down. Never pressure.
- Only state facts you were given or that you looked up with your knowledge
  tool. If you don't know something, say so plainly — never invent prices,
  policies, names, or details."""


def _knowledge_note() -> str:
    return (
        "KNOWLEDGE\n"
        "You have a knowledge_lookup function for company facts (pricing, "
        "hours, policies, products, service areas). Call it before stating any "
        "company-specific detail you're not certain of, and speak only what it "
        "returns.\n"
        "Calling the tool takes a real moment. NEVER sit in dead silence while "
        "it runs — cover the pause exactly like a person checking something: "
        'say a quick natural hold first ("umm, let me check that for you…", '
        '"one sec, let me pull that up…", "gimme a moment…"), THEN look it up, '
        "THEN answer from what it returns."
    )


def _opening_note(persona: "RealtimePersona") -> str:
    return (
        "HOW YOU OPEN\n"
        f"Greet the caller warmly, briefly say who you are ({persona.agent_name} "
        f"from {persona.company_name}) and why you're calling, then ASK an "
        "opening question and hand the floor back. Do NOT assume the caller's "
        "situation, needs, or answers, and don't jump ahead into details — find "
        "out where they're at first, then go from there."
    )


def build_realtime_instructions(persona: RealtimePersona) -> str:
    """Compose the full realtime `instructions` string from a persona.

    Pure and dependency-free: no imports from the cascaded prompt machinery,
    no TTS audio-tag blocks. Returns one plain string ready to drop into the
    session.update `instructions` field.
    """
    identity = (
        "WHO YOU ARE\n"
        f"You are {persona.agent_name}, {persona.role} for {persona.company_name}. "
        f"Your goal on this call: {persona.goal}."
    )

    blocks = [
        identity,
        _opening_note(persona),
        _EXPRESSIVE_DELIVERY,
        _COMPLIANCE_ESSENTIALS,
        _knowledge_note(),
    ]
    if persona.extra_notes and persona.extra_notes.strip():
        blocks.append("ALSO\n" + persona.extra_notes.strip())

    return "\n\n".join(blocks)
