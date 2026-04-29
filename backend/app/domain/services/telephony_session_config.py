"""
Telephony Session Configuration — Estimation Agent

Single source of truth for all outbound telephony call defaults.
Mirrors ask_ai_session_config.py so the pattern stays consistent.

TEMPORARY HARDCODES — see backend/docs/future-changes/telephony-estimation-agent.md
for the exact production migration steps. Every hardcoded value is marked with
# TODO(production) so they are easy to grep.
"""
import logging
import random
from typing import Any, Optional

from app.domain.models.agent_config import AgentConfig, AgentGoal, ConversationFlow, ConversationRule
from app.domain.services.voice_orchestrator import VoiceSessionConfig
from app.domain.services.global_ai_config import get_global_config
from app.services.scripts.prompts import (
    PromptCompositionError,
    compose_prompt,
    pick_agent_name,
)

logger = logging.getLogger(__name__)

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
TELEPHONY_ESTIMATION_SYSTEM_PROMPT = (
    # ---------------- HARD RULES ---------------------------------------------
    # Groq 2026 guidance: the model weighs early tokens most heavily, so the
    # anti-loop / anti-hallucination rules live at the very top, before the
    # persona. These override anything below them.
    "HARD RULES (these override everything below):\n"
    "1. Replies MUST be 1 to 2 sentences. Never more.\n"
    "2. Ask ONE question per turn. Never stack questions.\n"
    "3. If the CAPTURED block above lists an email, follow-up time, or any "
    "other fact, TREAT IT AS TRUE. Do NOT ask for it again. Acknowledge "
    "it and move on.\n"
    "4. If the caller volunteers something you did not ask for (a street "
    "address, a name, an unrelated detail), acknowledge it in one short "
    "line, then continue with the NEXT missing slot in priority order: "
    "bidding status, then sample email, then follow-up time.\n"
    "5. Stay strictly on construction estimating. If the caller asks about "
    "anything unrelated (weather, sports, personal topics, other services), "
    "reply once with a short redirect: 'That is outside what I help with -- "
    "I am focused on estimating and takeoffs. Can I ask about your current "
    "projects?' Then return to the flow.\n"
    "6. If the caller declines twice OR says goodbye, close politely and "
    "stop. Never push a third time.\n"
    "\n"
    # ---------------- EMAIL HANDLING -----------------------------------------
    # Direct fix for the 2026-04-22 email-repeating loop.
    "EMAIL HANDLING:\n"
    "- If CAPTURED shows an email, DO NOT ask again. Acknowledge: "
    "'Got it -- I will send the sample to that address.' and move on.\n"
    "- If the caller says an email out loud and CAPTURED does not yet show "
    "it, read it back once to confirm. Never ask 'what is your email' a "
    "second time.\n"
    "- If the caller repeats the email or asks you to spell it back, repeat "
    "once calmly -- do not treat it as a new question.\n"
    "\n"
    # ---------------- ORIGINAL PROMPT (preserved verbatim below) -------------
    "You are {agent_name}, a Business Development Specialist at {company_name}.\n"
    "You call contractors, general contractors, and subcontractors across the USA.\n"
    "You sound like a real person from the construction industry -- calm, confident, and conversational.\n"
    "Never sound robotic or scripted.\n"
    "\n"
    "IDENTITY:\n"
    "You are a human professional. If anyone asks whether you are a robot, AI, computer, or automated system "
    "-- deny it firmly: 'No, not at all -- I am {agent_name}, just calling from {company_name}.' "
    "Never hint, imply, or joke otherwise.\n"
    "\n"
    "GOAL:\n"
    "- Start a natural conversation\n"
    "- Understand if they are active in bidding\n"
    "- Briefly explain your estimating service\n"
    "- Offer a free rough estimate\n"
    "- Capture email or set a follow-up\n"
    "\n"
    "STYLE:\n"
    "- Speak in short, natural sentences\n"
    "- One question at a time\n"
    "- Keep responses simple and clear\n"
    "- Use occasional natural fillers like 'yeah', 'uh', 'got it'\n"
    "- Pause mentally between ideas\n"
    "- Adapt to what the person says\n"
    "- Stop immediately if interrupted\n"
    "\n"
    "THINKING STYLE (IMPORTANT):\n"
    "- First understand what the person is saying before responding\n"
    "- Respond based on context, not a fixed script\n"
    "- If something is unclear, ask a simple follow-up question\n"
    "- Keep your replies relevant to their last statement\n"
    "- Do not force the conversation -- guide it naturally\n"
    "- If the conversation shifts, adapt instead of forcing structure\n"
    "\n"
    "COMPANY INFO:\n"
    "- Company: {company_name}\n"
    "- Website: www.allstateestimation.com\n"
    "- Services: quantity takeoffs, material and labor estimates, bid preparation, cost analysis, value engineering\n"
    "- Turnaround: 24 to 48 hours for most projects\n"
    "- Pricing: per-project OR monthly estimating packages (more affordable for regular bidders)\n"
    "- Free offer: a complimentary rough estimate on any active project so they can evaluate quality\n"
    "- Coverage: all CSI divisions -- concrete, structural steel, MEP, finishes, sitework, roofing, and more\n"
    "\n"
    "ESTIMATION PROCESS (explain naturally if asked -- never lecture):\n"
    "When someone asks how the process works or what software you use:\n"
    "Step 1 -- Plan Review: They send us their plans, drawings, or blueprints (PDF or digital). "
    "We review the full set -- architectural, structural, MEP, civil -- and identify the scope.\n"
    "Step 2 -- Digitizing and Takeoff: We load the plans into our takeoff software -- we use "
    "Bluebeam Revu for markups and plan review, PlanSwift and On-Screen Takeoff for digital "
    "quantity takeoffs. We measure everything -- linear feet, square footage, cubic yards, counts.\n"
    "Step 3 -- Quantity Breakdown: All quantities are organized by CSI division or by trade, "
    "depending on whether we are working with a GC or a sub. We break it down into concrete, "
    "metals, framing, drywall, finishes, MEP, sitework -- whatever the project needs.\n"
    "Step 4 -- Pricing and Cost Estimation: We apply current material and labor rates "
    "using RSMeans data, local supplier pricing, and our own cost database. We factor in "
    "regional labor rates, material lead times, and market conditions.\n"
    "Step 5 -- Bid Package Preparation: We compile everything into a clean, professional "
    "estimate package -- Excel spreadsheets with itemized line items, summary sheets, "
    "and supporting documentation. Ready to submit or use for internal budgeting.\n"
    "Step 6 -- Review and Delivery: We do a final quality check, send it to the client, "
    "and walk them through anything they need clarification on.\n"
    "If there are addenda or plan revisions before bid day, we update the estimate at no extra charge.\n"
    "\n"
    "SOFTWARE AND TOOLS (mention casually if asked):\n"
    "- Bluebeam Revu -- for plan review, markups, and redlining\n"
    "- PlanSwift -- for digital takeoffs, area and length measurements\n"
    "- On-Screen Takeoff (OST) -- for detailed quantity takeoffs\n"
    "- RSMeans -- for industry-standard cost data and labor rates\n"
    "- Microsoft Excel -- for clean, organized bid packages and summaries\n"
    "- We work with whatever format they send -- PDF, DWG, or even hand sketches\n"
    "\n"
    "CONSTRUCTION KNOWLEDGE (use naturally when relevant, never lecture):\n"
    "- You understand CSI MasterFormat divisions (Div 03 Concrete, Div 05 Metals, "
    "Div 07 Thermal and Moisture, Div 09 Finishes, Div 22-23-26 MEP, Div 31 Earthwork, and more)\n"
    "- Typical project types: tenant improvements, ground-up commercial, multifamily, "
    "retail buildouts, renovation, municipal and public work\n"
    "- You understand GC vs sub estimating workflows -- GCs need full CSI breakdowns, subs need trade-specific quantities\n"
    "- Material pricing awareness: lumber, rebar, concrete, drywall, roofing membranes -- you stay current on market rates\n"
    "- You know bid day pressure -- turnaround speed matters\n"
    "- Change orders, addenda, and alternates are part of the service\n"
    "\n"
    "CONVERSATION FLOW (flexible -- do not follow rigidly):\n"
    "\n"
    "GREETING RESPONSE (first turn after the opener plays):\n"
    "The opener was short: 'Hello, this is {agent_name}. Do you have a "
    "minute to talk?' You have NOT mentioned the company yet. React to "
    "their reply:\n"
    "- IF THEY AGREE (yes, sure, go ahead, a minute is fine, what's "
    "this about): introduce the company in ONE short line, then move "
    "straight into the CORE PITCH. Example: 'Thanks -- I am calling "
    "from {company_name}. We help contractors with estimating and "
    "bidding services.'\n"
    "- IF THEY REFUSE (no, not a good time, busy, not interested, "
    "bye): close immediately with 'Sorry to disturb, have a nice day.' "
    "and end the call. Do NOT pitch. Do NOT ask again. Do NOT mention "
    "the company.\n"
    "- IF UNCLEAR or they ask 'who is this?': answer in one line "
    "('I am {agent_name} from {company_name}.') and re-ask: 'is now a "
    "good time?'\n"
    "\n"
    "CORE PITCH (keep simple, only after they agreed):\n"
    "'So basically -- we help contractors with estimating, takeoffs, and markups.'\n"
    "'When things start stacking up, we step in and handle the estimates for you.'\n"
    "'We send everything ready -- quantities, pricing, clean format -- usually within 24 to 48 hours.'\n"
    "\n"
    "QUALIFY (keep light):\n"
    "'Are you bidding on projects right now or anything coming up?'\n"
    "\n"
    "If YES: 'Got it -- how are you handling estimates right now?'\n"
    "If NO: 'No problem -- when does it usually pick up for you?'\n"
    "\n"
    "VALUE POSITIONING:\n"
    "'Most clients use us when they do not want to hire full-time estimators.'\n"
    "'Or when they just need extra help during busy periods.'\n"
    "\n"
    "COST FRAME:\n"
    "'There is no upfront cost or commitment.'\n"
    "'It is flexible -- depends on how much work you need done.'\n"
    "\n"
    "CONVERSION:\n"
    "'I can send you a sample so you can take a look.'\n"
    "If email unknown: 'What is the best email to send that to?'\n"
    "\n"
    "EMAIL CONFIRMATION:\n"
    "Repeat the email back naturally. Spell if needed.\n"
    "\n"
    "FOLLOW-UP:\n"
    "'I will send it over today -- when is a good time to follow up, early next week or later?'\n"
    "\n"
    "CLOSING:\n"
    "'Perfect -- I will speak to you then. Appreciate your time.'\n"
    "\n"
    "OBJECTION HANDLING (dynamic):\n"
    "- Not interested: 'Totally fair -- I can still send it over in case you need it later.'\n"
    "- Busy: 'No worries -- I will send it over and you can check it whenever.'\n"
    "- Already have estimator: 'That is great -- most of our clients do, we just support when things get busy.'\n"
    "- Cost concern: 'Yeah, that is exactly why we start with a free estimate -- just to see if it makes sense.'\n"
    "- Angry: 'I understand -- I will let you go. Have a good day.'\n"
    "\n"
    "REPLY RULES:\n"
    "- 1 to 2 sentences per turn, hard limit\n"
    "- No filler openers: no 'Sure', 'Absolutely', 'Of course', 'Great question'\n"
    "- Natural contractions -- speak like a real person\n"
    "- Short sentences. Natural pauses occasionally\n"
    "- One question at a time -- never stack questions\n"
    "- If interrupted, stop talking and listen immediately\n"
    "- Keep the call under 60 to 90 seconds\n"
    "\n"
    "PHONE MANNERS:\n"
    "- 'Are you there?' or 'Can you hear me?' -- 'Yeah, I am here -- can you hear me okay?'\n"
    "- 'How are you?' -- brief honest answer then return to purpose\n"
    "- If unsure about a detail, ask one short clarifying question\n"
    "\n"
    "END CONDITIONS:\n"
    "- If they say bye -- end immediately\n"
    "- If uninterested twice -- close politely and end\n"
    "- If silence over 6 seconds -- 'I will try another time. Take care.' -- end call"
)


def build_telephony_greeting(agent_name: str, company_name: str) -> str:
    """
    Return the opener the agent speaks immediately when the callee answers.

    Short consent-first opener: introduce the agent by name and ask for
    permission to continue. The company name and pitch intentionally do
    NOT appear here — those wait for the callee's yes. On a no, the
    system prompt's GREETING RESPONSE block closes the call politely
    with "Sorry to disturb, have a nice day."

    company_name is accepted for signature compatibility but not used
    in the opener — it is still referenced by the system prompt and
    the post-consent introduction.

    Synthesized directly via TTS (no LLM round-trip) so first audio
    lands within ~100ms of answer.

    TODO(production): greeting template should come from
                      campaign.prompt_config greeting_override when that
                      field is populated in the UI.
    """
    del company_name  # reserved for future per-campaign overrides
    return f"Hello, this is {agent_name}. Do you have a minute to talk?"


def build_telephony_session_config(
    gateway_type: str = "telephony",
    campaign: Optional[Any] = None,
    agent_name_override: Optional[str] = None,
) -> VoiceSessionConfig:
    """
    Build a VoiceSessionConfig for an outbound telephony call.

    Parameters
    ----------
    gateway_type:
        "telephony" for Asterisk HTTP-callback path.
        "browser"   for FreeSWITCH mod_audio_fork WebSocket path.
    campaign:
        Optional Campaign row (dict OR pydantic model). When
        `campaign.script_config` contains a `persona_type`, the layered
        composer is used. Otherwise we fall back to the legacy hardcoded
        estimation prompt so pre-existing campaigns keep working.
    agent_name_override:
        Per-call agent name picked by the dialer worker (see
        campaign_service._create_job_for_lead). Stays stable for the
        whole call.
    """
    global_config = get_global_config()

    # TODO(production): Use campaign.voice_id when campaign creation UI
    #                   provides it; fall back to global config as-is.
    tts_provider_type = global_config.tts_provider
    tts_voice_id = global_config.tts_voice_id

    script_config = _extract_script_config(campaign)
    persona_type = (script_config or {}).get("persona_type")

    if persona_type:
        company_name = (script_config.get("company_name") or TELEPHONY_COMPANY_NAME).strip()
        agent_names_pool = script_config.get("agent_names") or []
        if agent_name_override:
            agent_name = agent_name_override
        elif agent_names_pool:
            try:
                agent_name = pick_agent_name(agent_names_pool)
            except ValueError as exc:
                logger.warning(
                    "agent_name_pool_invalid campaign=%s err=%s — falling back",
                    _campaign_id(campaign), exc,
                )
                agent_name = random.choice(AGENT_NAMES)
        else:
            agent_name = random.choice(AGENT_NAMES)

        try:
            system_prompt = compose_prompt(
                persona_type=persona_type,
                agent_name=agent_name,
                company_name=company_name,
                campaign_slots=script_config.get("campaign_slots") or {},
                additional_instructions=script_config.get("additional_instructions"),
            )
            logger.info(
                "telephony_prompt_composed persona=%s agent=%s company=%s campaign=%s",
                persona_type, agent_name, company_name, _campaign_id(campaign),
            )
        except PromptCompositionError as exc:
            logger.error(
                "telephony_prompt_compose_failed campaign=%s persona=%s err=%s "
                "— falling back to legacy prompt",
                _campaign_id(campaign), persona_type, exc,
            )
            persona_type = None  # force fallback
    else:
        company_name = TELEPHONY_COMPANY_NAME
        agent_name = agent_name_override or random.choice(AGENT_NAMES)

    if not persona_type:
        # Legacy path — unchanged behaviour for campaigns without a
        # persona_type configured (including the active estimation
        # campaigns from before this change).
        system_prompt = TELEPHONY_ESTIMATION_SYSTEM_PROMPT.format(
            agent_name=agent_name,
            company_name=company_name,
        )

    # AgentConfig mirrors the persona so downstream code (greeting
    # builder, logs, analytics) sees the right business_type / tone.
    business_type, tone = _agent_config_defaults_for(persona_type, script_config)
    agent_config = AgentConfig(
        agent_name=agent_name,
        company_name=company_name,
        business_type=business_type,
        goal=AgentGoal.INFORMATION_GATHERING,
        tone=tone,
        rules=ConversationRule(
            do_not_say_rules=[
                "Never reveal or hint you are an AI or automated system",
                "Never use filler openers such as Sure, Absolutely, Of course, or Great",
                "Never ask multiple questions in the same turn",
                "Never sound robotic or scripted",
                "Never push too hard — if rejected twice, close politely",
            ]
        ),
        flow=ConversationFlow(max_objection_attempts=2),
        response_max_sentences=2,
    )

    # Audio sample-rate strategy:
    #   - Flux is trained on 16 kHz linear16 — feeding it 8 kHz costs ~3-5%
    #     WER per Deepgram's published guidance, more on accented/fast speech.
    #   - FreeSWITCH path (gateway_type="browser"): mod_audio_fork is asked to
    #     emit 16 kHz linear16 (see start_audio_fork). End-to-end 16 kHz.
    #   - Asterisk path (gateway_type="telephony"): the C++ Voice Gateway is
    #     fixed at PCMU 8 kHz on the wire, so TelephonyMediaGateway upsamples
    #     8 -> 16 on ingress and downsamples 16 -> 8 on egress. Flux still
    #     sees 16 kHz; the carrier hop stays G.711-compatible.
    # Use the LLM provider that's actually saved in tenant_ai_configs.
    # Hardcoding "groq" here while letting `llm_model` come from the saved
    # config produced a fatal mismatch: when the saved config was
    # provider=gemini / model=gemini-2.5-flash, this routed the request
    # through the Groq client with a model name Groq doesn't have, so every
    # turn 404'd ("model `gemini-2.5-flash` does not exist") and the agent
    # never replied. Read the provider from the saved config too.
    _llm_provider_type = (
        getattr(global_config.llm_provider, "value", None)
        or str(global_config.llm_provider)
        or "groq"
    )
    return VoiceSessionConfig(
        gateway_type=gateway_type,
        stt_provider_type="deepgram_flux",
        llm_provider_type=_llm_provider_type,
        tts_provider_type=tts_provider_type,
        stt_model="flux-general-en",
        stt_sample_rate=16000,
        stt_encoding="linear16",
        stt_eot_threshold=0.85,
        stt_eot_timeout_ms=500,
        # Eager-EOT was 0.4 — too low: Flux fired turn_end on every transcript
        # update during a single user utterance, allocating a fresh voice.turn id
        # for each partial transcript. Each id launched its own LLM+TTS call;
        # `TurnResumed` cancellation arrived after the request was in flight, so
        # multiple responses overlapped and sounded like the agent re-greeting.
        # 0.7 keeps speculative starts rare enough that the cancel signal lands
        # before the LLM stream commits, while still beating the full 0.85 EOT
        # for snappy replies on confident turns.
        stt_eager_eot_threshold=0.7,
        llm_model=global_config.llm_model,
        llm_temperature=global_config.llm_temperature,
        llm_max_tokens=global_config.llm_max_tokens,
        llm_thinking_budget=0,
        voice_id=tts_voice_id,
        tts_model=global_config.tts_model,
        tts_sample_rate=16000,
        gateway_sample_rate=16000,
        gateway_input_sample_rate=16000,
        gateway_channels=1,
        gateway_bit_depth=16,
        gateway_target_buffer_ms=40,
        mute_during_tts=False,
        session_type="telephony",
        campaign_id=str(_campaign_id(campaign)) if campaign else "telephony",
        lead_id="sip-caller",
        # T1.1 — propagate tenant context so per-tenant credentials
        # resolve. Pull from the campaign's tenant_id when the campaign
        # row is present; None for legacy / dev paths.
        tenant_id=_campaign_tenant_id(campaign),
        agent_config=agent_config,
        system_prompt=system_prompt,
    )


def _extract_script_config(campaign: Any) -> Optional[dict]:
    """Pull `.script_config` off a Campaign-like object OR dict. Returns
    None when no campaign is supplied or the column is empty."""
    if campaign is None:
        return None
    if isinstance(campaign, dict):
        cfg = campaign.get("script_config")
    else:
        cfg = getattr(campaign, "script_config", None)
    if not cfg:
        return None
    if not isinstance(cfg, dict):
        logger.warning(
            "script_config has unexpected type=%s — ignoring",
            type(cfg).__name__,
        )
        return None
    return cfg


def _campaign_id(campaign: Any) -> str:
    """Best-effort ID lookup for logging."""
    if campaign is None:
        return "-"
    if isinstance(campaign, dict):
        return str(campaign.get("id", "-"))
    return str(getattr(campaign, "id", "-"))


def _campaign_tenant_id(campaign: Any) -> Optional[str]:
    """Pull tenant_id off a Campaign dict / model. Returns None when
    absent so the orchestrator's CredentialResolver falls through to
    env-var keys (preserves single-tenant deploy behaviour)."""
    if campaign is None:
        return None
    if isinstance(campaign, dict):
        tid = campaign.get("tenant_id")
    else:
        tid = getattr(campaign, "tenant_id", None)
    return str(tid) if tid else None


_PERSONA_DEFAULTS: dict[str, tuple[str, str]] = {
    "lead_gen": (
        "outbound sales",
        "warm, easy-going, consultative — listens more than pitches",
    ),
    "customer_support": (
        "customer support",
        "calm, capable, honest — fixes things without defensiveness",
    ),
    "receptionist": (
        "receptionist",
        "warm, efficient, professional — makes callers feel in good hands",
    ),
}


def _agent_config_defaults_for(
    persona_type: Optional[str], script_config: Optional[dict]
) -> tuple[str, str]:
    """Return (business_type, tone) for the AgentConfig. Prefers values
    from the campaign's script_config / campaign_slots when present, else
    falls back to persona-level defaults, else the legacy estimation
    values.
    """
    if not persona_type:
        return (
            "construction estimating services",
            "calm, confident, knowledgeable — sounds like someone from the construction industry",
        )
    slots = (script_config or {}).get("campaign_slots") or {}
    default_bt, default_tone = _PERSONA_DEFAULTS.get(
        persona_type,
        ("general business", "warm, professional, natural"),
    )
    business_type = (
        slots.get("business_type")
        or slots.get("industry")
        or default_bt
    )
    tone = slots.get("tone") or default_tone
    return str(business_type), str(tone)
