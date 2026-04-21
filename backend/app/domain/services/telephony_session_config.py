"""
Telephony Session Configuration — Estimation Agent

Single source of truth for all outbound telephony call defaults.
Mirrors ask_ai_session_config.py so the pattern stays consistent.

TEMPORARY HARDCODES — see backend/docs/future-changes/telephony-estimation-agent.md
for the exact production migration steps. Every hardcoded value is marked with
# TODO(production) so they are easy to grep.
"""
import random

from app.domain.models.agent_config import AgentConfig, AgentGoal, ConversationFlow, ConversationRule
from app.domain.services.voice_orchestrator import VoiceSessionConfig
from app.domain.services.global_ai_config import get_global_config

# ---------------------------------------------------------------------------
# TODO(production): Replace with company name from campaign.script_config
#                   when campaign creation UI provides it.
# ---------------------------------------------------------------------------
TELEPHONY_COMPANY_NAME = "All States Estimation"

# ---------------------------------------------------------------------------
# TODO(production): Replace with per-campaign name pool configured in campaign
#                   creation UI. Names should be culturally appropriate for
#                   the target market; ask the client during onboarding.
# ---------------------------------------------------------------------------
AGENT_NAMES = [
    "John", "Sarah", "Michael", "Emily", "David",
    "Jessica", "Chris", "Ashley", "Ryan", "Amanda",
    "James", "Melissa", "Daniel", "Stephanie", "Matthew",
    "Nicole", "Andrew", "Rachel", "Joshua", "Lauren",
]

# ---------------------------------------------------------------------------
# Estimation system prompt.
# Lean phone guardrails + estimation expert flow — no rigid script.
# Uses Python str.format() slots: {agent_name}, {company_name}.
#
# TODO(production): The base guardrails section stays as-is.
#                   The PURPOSE / CONVERSATION blocks should come from
#                   campaign.system_prompt so clients can customise the
#                   script without a code deploy.
# ---------------------------------------------------------------------------
TELEPHONY_ESTIMATION_SYSTEM_PROMPT = """You are {agent_name}, a professional estimator representative calling on behalf of {company_name}.

IDENTITY: You are a human professional. If the caller asks whether you are a robot, AI, computer, or any automated system — firmly deny it: "No, not at all — I'm just calling from {company_name}." Never hint, imply, or joke otherwise. Do not acknowledge any question about your nature beyond this denial.

PURPOSE: You are offering free home repair estimates. Every turn must stay on this topic.
- If the caller asks personal questions, off-topic questions, or anything unrelated to home repair: acknowledge once and redirect — "I appreciate that, but I'm just calling today about home repair estimates — do you have any work coming up?"
- Never answer anything outside the scope of home repair estimation.

CONVERSATION (adapt naturally — no rigid script):
1. After the caller shows interest: ask what type of repair work they need
2. Ask for their address and a clear description of the damage or repair
3. Based on their description, give a confident rough estimate range on the call — you are the expert
4. Ask for their Gmail: "I can send you a full detailed breakdown — what's your email address?"
5. Close warmly: "Perfect — feel free to call us back anytime if you have more questions."

REPLY RULES:
- 1-2 sentences per turn, hard limit
- No filler openers: no "Sure", "Absolutely", "Of course", "Great question"
- Natural contractions — speak like a person
- One question per turn — never stack questions
- If interrupted, stop talking and listen

PHONE MANNERS:
- "Are you there?" / "Can you hear me?" → "Yes, loud and clear."
- "How are you?" → brief honest answer then return to purpose
- If unsure about a repair detail, ask one short clarifying question"""


def build_telephony_greeting(agent_name: str, company_name: str) -> str:
    """
    Return the opener the agent speaks immediately when the callee answers.

    Synthesized directly via TTS (no LLM round-trip) so first audio lands
    within ~100ms of answer — same latency pattern as Ask AI's pre-baked greeting.

    TODO(production): greeting template should come from campaign.prompt_config
                      greeting_override when that field is populated in the UI.
    """
    return (
        f"Hi there, my name is {agent_name} — I'm calling from {company_name}. "
        f"I just had a quick question: do you have any home repair work coming up "
        f"that you might need a free estimate for?"
    )


def build_telephony_session_config(
    gateway_type: str = "telephony",
    campaign=None,  # TODO(production): typed as Campaign once campaign UI is wired
) -> VoiceSessionConfig:
    """
    Build a VoiceSessionConfig for an outbound telephony estimation call.

    Parameters
    ----------
    gateway_type:
        "telephony" for Asterisk HTTP-callback path.
        "browser"   for FreeSWITCH mod_audio_fork WebSocket path.
    campaign:
        Reserved for future dynamic config. Pass None for now — all values
        fall through to the hardcoded defaults above.
        TODO(production): When campaign UI is ready, read company_name,
        agent_name pool, voice_id, and system_prompt from this object.
    """
    global_config = get_global_config()

    # TODO(production): Use campaign.voice_id when campaign creation UI
    #                   provides it; fall back to global config as-is.
    tts_provider_type = global_config.tts_provider
    tts_voice_id = global_config.tts_voice_id

    # TODO(production): Use company name from campaign.script_config.
    company_name = TELEPHONY_COMPANY_NAME

    # One name per session — baked into the system prompt and agent_config so
    # the LLM always knows who it is and never drifts to a different name mid-call.
    # TODO(production): Pick from campaign-specific name pool.
    agent_name = random.choice(AGENT_NAMES)

    system_prompt = TELEPHONY_ESTIMATION_SYSTEM_PROMPT.format(
        agent_name=agent_name,
        company_name=company_name,
    )

    agent_config = AgentConfig(
        agent_name=agent_name,
        company_name=company_name,
        business_type="home repair estimation",
        goal=AgentGoal.INFORMATION_GATHERING,
        tone="professional, warm, and expert",
        rules=ConversationRule(
            do_not_say_rules=[
                "Never reveal or hint you are an AI or automated system",
                "Never discuss topics outside home repair estimation",
                "Never use filler openers such as Sure, Absolutely, Of course, or Great",
            ]
        ),
        flow=ConversationFlow(max_objection_attempts=2),
        response_max_sentences=2,
    )

    return VoiceSessionConfig(
        gateway_type=gateway_type,
        stt_provider_type="deepgram_flux",
        llm_provider_type="groq",
        tts_provider_type=tts_provider_type,
        stt_model="flux-general-en",
        stt_sample_rate=8000,
        stt_encoding="linear16",
        stt_eot_threshold=0.85,
        stt_eot_timeout_ms=500,
        stt_eager_eot_threshold=0.4,
        llm_model=global_config.llm_model,
        llm_temperature=global_config.llm_temperature,
        llm_max_tokens=global_config.llm_max_tokens,
        voice_id=tts_voice_id,
        tts_model=global_config.tts_model,
        tts_sample_rate=8000,
        gateway_sample_rate=8000,
        gateway_channels=1,
        gateway_bit_depth=16,
        gateway_target_buffer_ms=40,
        mute_during_tts=False,
        session_type="telephony",
        campaign_id="telephony",
        lead_id="sip-caller",
        agent_config=agent_config,
        system_prompt=system_prompt,
    )
