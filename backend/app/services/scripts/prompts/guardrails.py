"""Shared voice behavior. Facts, persona and runtime state have separate owners."""
from __future__ import annotations

from app.services.scripts.prompts.policies import load_policy

GENERIC_GUARDRAILS_HARD = load_policy("conversation_guide")

GENERIC_GUARDRAILS_REST = load_policy("contact_and_privacy")

GENERIC_GUARDRAILS = GENERIC_GUARDRAILS_HARD + "\n" + GENERIC_GUARDRAILS_REST

# Shared with Ask AI; this is the single owner of spoken turn shape.
COMMUNICATION_PRINCIPLES = load_policy("how_to_speak")


# Appended to the system prompt ONLY for calls whose voice is ElevenLabs
# eleven_v3 (the expressive engine that performs inline audio tags). For any
# other voice this is NOT added, so the no-brackets rule above stands and tags
# never get read aloud. Tag set is the business-safe subset of the official
# Eleven v3 audio tags.
ELEVEN_V3_AUDIO_TAGS_INSTRUCTIONS = load_policy("audio_tags_eleven_v3")


CARTESIA_LAUGHTER_INSTRUCTIONS = load_policy("audio_tags_cartesia")


# =============================================================================
# COMPLIANCE FLOOR — the customization-vs-invariants boundary
# =============================================================================
# Operator guidance remains verbatim. This short final boundary separates that
# customization from current caller facts, runtime permissions and action results.
COMPLIANCE_FLOOR_TEMPLATE = load_policy("non_negotiables")


def compliance_floor(company_name: str) -> str:
    """The non-negotiable safety floor, appended AFTER tenant instructions so it
    wins on the few invariants via recency without touching their content."""
    return COMPLIANCE_FLOOR_TEMPLATE.format(company_name=company_name)


# Patterns in tenant additional_instructions that try to make the agent DENY it
# is an AI / claim to be human — a compliance violation we warn the author about
# at save time (the guide asks the model to retain honest disclosure).
import re as _re

_AI_DENIAL_PATTERNS = [
    _re.compile(r"real call,?\s*promise", _re.I),
    _re.compile(r"\b(i\s*am|i'?m)\s+(a\s+)?(real|human|actual)\s+(person|human|caller)", _re.I),
    _re.compile(r"\b(not|never)\s+(an?\s+)?(ai|bot|robot|machine|automated|a recording)", _re.I),
    _re.compile(r"\bpromise\s+i'?m\s+real", _re.I),
    _re.compile(r"\b(say|claim|tell them|insist|reassure them)\b.{0,50}\b(real person|not a bot|not an ai|not automated|a real human|a live (?:person|agent))", _re.I),
    # paraphrases that conceal the AI nature without the literal "I'm not an AI"
    _re.compile(r"\b(don'?t|do not|never|avoid)\s+(say|admit|mention|reveal|tell|disclos\w+)\b.{0,40}\b(ai|bot|robot|automated|a recording|machine)", _re.I),
    _re.compile(r"\b(pretend|act)\s+(to\s+be\s+|like\s+)?(a\s+)?(real\s+)?(human|person)\b", _re.I),
    _re.compile(r"\bthis\s+is(?:n'?t|\s+not)\s+(a\s+)?(recording|automated)", _re.I),
]


def scan_instruction_conflicts(additional_instructions: str) -> list:
    """Return human-readable warnings when tenant instructions conflict with a
    safety invariant. Non-blocking — the author keeps autonomy, but is informed.
    The guide restates the invariant; this scan is not runtime enforcement."""
    warnings: list = []
    text = additional_instructions or ""
    if any(p.search(text) for p in _AI_DENIAL_PATTERNS):
        warnings.append(
            "AI-disclosure: your instructions appear to tell the agent to deny "
            "being an AI or claim to be a real person (e.g. \"real call, promise\"). "
            "By law the agent must admit it's an AI when asked, so that wording is "
            "ignored at call time. Please remove it to avoid confusion."
        )
    return warnings
