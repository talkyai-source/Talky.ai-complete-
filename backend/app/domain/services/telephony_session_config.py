"""
Telephony Session Configuration — Estimation Agent

Single source of truth for all outbound telephony call defaults.
Mirrors ask_ai_session_config.py so the pattern stays consistent.

TEMPORARY HARDCODES — see backend/docs/future-changes/telephony-estimation-agent.md
for the exact production migration steps. Every hardcoded value is marked with
# TODO(production) so they are easy to grep.
"""
import logging
import os
import random
import re
from typing import Any, Optional

from app.domain.models.agent_config import AgentConfig, AgentGoal, ConversationFlow, ConversationRule
from app.domain.services.voice_orchestrator import Direction, VoiceSessionConfig
from app.domain.services.global_ai_config import get_global_config
from app.domain.services.voice_tuning import (
    VoiceTuning,
    get_voice_tuning_resolver,
)
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
# Legacy hardcoded estimation + inbound prompts were RETIRED 2026-06-18.
# Every campaign now composes its prompt through the single layered persona
# system (prompts.compose_prompt); a campaign-less / persona-less call falls
# back to a knowledge-driven lead_gen persona (see build_telephony_session_config).
# There is exactly ONE prompt-composition path now.
# ---------------------------------------------------------------------------


def _telephony_mute_during_tts_default() -> bool:
    """Whether to mute STT during AI playback on telephony calls.

    **Default: False.** Muting STT during TTS is the textbook fix for
    carrier-echo cross-contamination, but on Flux it is a binary mute —
    no transcripts arrive during the entire AI reply, which **disables
    barge-in**. For most outbound-dialer use cases barge-in is the more
    important property: a caller cutting in mid-pitch with "I'm not
    interested" must be heard immediately, not after the AI finishes its
    paragraph.

    Operators whose carrier has poor echo cancellation (audible self-echo
    in test recordings) can opt into mute by setting
    ``TELEPHONY_MUTE_DURING_TTS=true``. Doing so trades barge-in for echo
    suppression — a deliberate per-deployment choice, not the default.

    The proper long-term fix is a partial-mute strategy (mute the first
    ~200ms of TTS where echo onset lives, unmute for the rest) but that
    requires orchestrator changes outside the scope of this knob.
    """
    from app.core.telephony_settings import get_telephony_settings
    return get_telephony_settings().mute_during_tts


# Common words that survive the proper-noun heuristic but aren't product names
# (sentence-initial capitals, persona boilerplate). Kept lowercase for compare.
_PRODUCT_TERM_STOPWORDS = frozenset({
    "the", "you", "your", "our", "we", "they", "this", "that", "these", "those",
    "please", "when", "make", "always", "never", "call", "caller", "agent",
    "company", "customer", "client", "team", "hello", "hi", "yes", "no", "i",
    "if", "do", "don", "be", "is", "are", "and", "or", "for", "with", "to",
    "from", "on", "at", "in", "of", "a", "an", "it", "as", "ai", "ask",
})


def _extract_product_terms(script_config: dict, company_name: str) -> list:
    """Pull likely product / brand names out of the campaign's free-text config
    so Flux recognises them instead of garbling (e.g. "Dojo Go" → "Dodge go").

    Conservative, zero-setup heuristic over ``additional_instructions`` (and an
    optional explicit ``products`` list if a campaign ever sets one):
      * multi-word Title-Case phrases  ("Dojo Go", "Pocket Card Reader")
      * quoted phrases                 ('the "Pocket" reader')
      * single tokens with internal caps / digits / ALL-CAPS  (iZettle, G2, SMB)
    Single ordinary Capitalized words (sentence starts like "Please") are
    deliberately NOT matched — that keeps noise out of the keyterm budget.
    """
    if not isinstance(script_config, dict):
        return []

    company_lower = (company_name or "").lower()
    found: list[str] = []
    seen: set[str] = set()

    def _add(term: str) -> None:
        term = (term or "").strip(" \"'.,;:!?()[]{}")
        if len(term) < 2 or len(term) > 40:
            return
        low = term.lower()
        # Skip stopwords, the brand itself (already added), and phrases that are
        # entirely ordinary words.
        if low in seen or low in _PRODUCT_TERM_STOPWORDS or low in company_lower:
            return
        if all(w.lower() in _PRODUCT_TERM_STOPWORDS for w in term.split()):
            return
        seen.add(low)
        found.append(term)

    # Explicit products list (if a campaign ever sets one) — added verbatim,
    # one entry per item; NOT run through the free-text heuristic.
    explicit = script_config.get("products")
    if isinstance(explicit, (list, tuple)):
        for p in explicit:
            _add(str(p))
    elif isinstance(explicit, str):
        _add(explicit)

    # Free-text heuristic over additional_instructions.
    extra = script_config.get("additional_instructions")
    text = extra.strip() if isinstance(extra, str) else ""
    if text:
        # 1) Quoted phrases — users often quote a product name.
        for m in re.findall(r"[\"'“”‘’]([^\"'“”‘’]{2,40})[\"'“”‘’]", text):
            _add(m)
        # 2) Multi-word Title-Case phrases (2+ consecutive capitalised words).
        #    [^\S\n] = a space/tab but NOT a newline, so phrases never span lines.
        for m in re.findall(
            r"\b([A-Z][A-Za-z0-9'&]+(?:[^\S\n]+[A-Z][A-Za-z0-9'&]+)+)\b", text
        ):
            _add(m)
        # 3) Single tokens with internal caps, digits, or ALL-CAPS acronyms.
        for m in re.findall(r"\b([A-Za-z0-9'&]{2,})\b", text):
            has_internal_cap = bool(re.search(r"[a-z][A-Z]|[A-Z][A-Z]", m))
            has_digit = bool(re.search(r"\d", m))
            if has_internal_cap or has_digit:
                _add(m)

    return found[:10]


def _build_call_keyterms(
    company_name: str, agent_name: str, product_terms: Optional[list] = None
) -> list:
    """Bias Deepgram Flux toward the words it most often mis-hears on a call:
    the company/brand name, the agent's name, and the campaign's product names.

    Flux keyterm prompting only biases toward terms it's told about. The static
    providers.yaml base list is empty (email-spelling terms moved to capture
    mode), so a campaign brand like "Dojo" or product "Dojo Go" gets
    transcribed as "Dodge" without this. We add the campaign's own company +
    agent + product names (plus the significant single words of a multi-word
    brand), deduped case-insensitively and capped."""
    from app.domain.services.voice_orchestrator import _default_flux_keyterms

    terms: list[str] = []
    seen: set[str] = set()

    def _add(t: Optional[str]) -> None:
        t = (t or "").strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            terms.append(t)

    # Campaign-specific terms first (highest recognition value).
    _add(company_name)
    for word in re.findall(r"[A-Za-z][A-Za-z0-9'&-]{2,}", company_name or ""):
        _add(word)  # e.g. "Dojo" out of "Dojo Payments Ltd"
    _add(agent_name)
    for t in (product_terms or []):
        _add(t)
    # Then any base defaults from providers.yaml (empty today; email-spelling
    # terms are capture-mode only).
    for t in (_default_flux_keyterms() or []):
        _add(t)
    return terms[:60]


def build_telephony_inbound_greeting(agent_name: str, company_name: str) -> str:
    """
    Canonical first-utterance for genuine INBOUND calls (a customer
    dialing into us). Picks one of a few warm variants so consecutive
    inbound calls don't all open with the same scripted line.

    Note: this is NOT used for caller-first OUTBOUND calls anymore —
    those use the outbound greeting (we dialed them, even though we
    pause 2s before speaking).

    The wording mirrors what a real person picks up the phone with:
    a single short sentence that names the company first (so the
    caller knows they reached the right place), then the agent.
    """
    import random as _random

    variants = [
        f"Hello, {company_name}, this is {agent_name} -- how can I help you?",
        f"Thanks for calling {company_name}. {agent_name} here -- what can I do for you?",
        f"Hi, {company_name} -- {agent_name} speaking. How can I help you?",
    ]
    return _random.choice(variants)


# Per-persona × direction first-turn TTS opener (T4-A2).
#
# Pre-synthesized during the ringing window and played as the AI's
# first audio after pickup. Each entry is a LIST of str.format templates
# taking ``{agent_name}`` and ``{company_name}``. The dispatcher picks
# one randomly per call so consecutive calls don't sound identical.
# Keep variants SHORT (~1.5-2.5 seconds spoken) — the LLM drives every
# turn after this one and a long static opener wastes early air time.
#
# Adding a new persona: drop a key into this dict and the dispatcher
# below picks it up. Adding a direction to an existing persona: same.
# Missing combinations fall through to the generic builders, so a
# half-configured persona still produces a grammatical greeting.
_PERSONA_GREETINGS: dict[str, dict[str, list[str]]] = {
    "lead_gen": {
        "outbound": [
            "Hey, this is {agent_name} from {company_name}. "
            "Got a quick second?",
            "Hi, {agent_name} here from {company_name}. "
            "Do you have a minute to talk?",
            "Hi! This is {agent_name} calling from {company_name}. "
            "Quick question — got a moment?",
        ],
        "inbound": [
            "Hi, this is {agent_name} from {company_name} -- "
            "thanks for reaching out. How can I help?",
            "Hey, {agent_name} here from {company_name}. "
            "What can I help you with today?",
        ],
    },
    "customer_support": {
        "outbound": [
            "Hi, this is {agent_name} from {company_name} support. "
            "Got a quick moment?",
            "Hey, {agent_name} here from {company_name}. "
            "Calling about your recent inquiry — got a sec?",
            "Hi! This is {agent_name} from {company_name}. "
            "Quick follow-up — is now a good time?",
        ],
        "inbound": [
            "Thanks for calling {company_name} -- this is {agent_name}, "
            "how can I help?",
            "Hi, {agent_name} from {company_name} support. "
            "What can I do for you?",
        ],
    },
    "receptionist": {
        "outbound": [
            "Hi, this is {agent_name} from {company_name}. "
            "Quick follow-up — got a moment?",
            "Hey, {agent_name} calling from {company_name}. "
            "Do you have a quick second?",
            "Hi! {agent_name} from {company_name} here. "
            "Just following up — got a minute?",
        ],
        "inbound": [
            "Thank you for calling {company_name}. This is {agent_name} -- "
            "how can I help you today?",
            "Hi, {company_name} -- {agent_name} speaking. "
            "How can I help?",
        ],
    },
}


def build_persona_greeting(
    *,
    persona_type: Optional[str],
    agent_name: str,
    company_name: str,
    direction: str = "outbound",
) -> str:
    """Pick a per-persona × direction TTS opener at random.

    Returns one of the variants in :data:`_PERSONA_GREETINGS` for the
    given persona × direction. Random selection is intentional: it
    keeps consecutive calls from sounding identical, which lifts the
    natural-conversation feel and reduces the "robocall pattern" a
    callee hears when an operator is dialing the same lead twice.

    Falls back to the generic ``build_telephony_greeting`` /
    ``build_telephony_inbound_greeting`` when:

    * ``persona_type`` is ``None`` or unknown — covers the legacy
      estimation campaign (no persona) and any future persona that
      hasn't been given dedicated openers yet.
    * The (persona, direction) pair is missing from the dispatch table —
      same fallback as above; partial configurations still produce a
      grammatical greeting rather than crashing the call.

    Both the persona templates and the fallback builders use the same
    ``{agent_name}`` / ``{company_name}`` slots, so swapping between
    them at runtime is invisible to the TTS synthesiser.
    """
    import random as _random

    direction_key = (direction or "outbound").strip().lower()
    if persona_type and persona_type in _PERSONA_GREETINGS:
        per_persona = _PERSONA_GREETINGS[persona_type]
        variants = per_persona.get(direction_key)
        if variants:
            template = _random.choice(variants)
            return template.format(
                agent_name=agent_name,
                company_name=company_name,
            )
    if direction_key == "inbound":
        return build_telephony_inbound_greeting(agent_name, company_name)
    return build_telephony_greeting(agent_name, company_name)


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
    import random as _random

    del company_name  # reserved for future per-campaign overrides
    # 3 short conversational variants — picked at random per call so
    # consecutive dials don't sound canned. All under ~2s of TTS.
    variants = [
        f"Hi, this is {agent_name}. Do you have a minute to talk?",
        f"Hey, {agent_name} here. Got a quick second?",
        f"Hi! {agent_name} calling — got a moment?",
    ]
    return _random.choice(variants)


def build_telephony_session_config(
    gateway_type: str = "telephony",
    campaign: Optional[Any] = None,
    agent_name_override: Optional[str] = None,
    direction: Direction = Direction.OUTBOUND,
    voice_tuning_override: Optional[VoiceTuning] = None,
) -> VoiceSessionConfig:
    """
    Build a VoiceSessionConfig for a telephony call.

    Parameters
    ----------
    gateway_type:
        "telephony" for Asterisk HTTP-callback path.
        "browser"   for FreeSWITCH mod_audio_fork WebSocket path.
    campaign:
        Optional Campaign row (dict OR pydantic model). The layered
        composer always builds the prompt from `campaign.script_config`'s
        `persona_type`. A campaign with no persona (or no campaign at all)
        defaults to a knowledge-driven `lead_gen` persona — there is no
        hardcoded-script fallback anymore.
    agent_name_override:
        Per-call agent name picked by the dialer worker (see
        campaign_service._create_job_for_lead). Stays stable for the
        whole call.
    direction:
        Whether the call originated from the platform (``OUTBOUND``,
        default) or is being treated as a receiver-style call
        (``INBOUND``). For INBOUND the composer prepends the canonical
        inbound directive at compose time; the bridge also applies it at
        runtime via :func:`select_inbound_base_prompt` for caller-first
        outbound calls — so the LLM is correctly framed without each
        persona template needing two variants.
    """
    global_config = get_global_config()

    # Per-campaign TTS: each campaign runs on its OWN provider + voice (stored on
    # the campaign row), falling back to the tenant global when unset. This is
    # what lets calls honor a campaign's chosen voice/engine independently of the
    # account default (ends the account-wide-switch side effect).
    tts_provider_type = global_config.tts_provider
    tts_voice_id = global_config.tts_voice_id
    tts_model = global_config.tts_model
    _camp_voice = _campaign_attr(campaign, "voice_id")
    _camp_provider = _campaign_attr(campaign, "tts_provider")
    if _camp_voice:
        tts_voice_id = _camp_voice
    if _camp_provider:
        tts_provider_type = _camp_provider
        # A different engine than the global one must not inherit the global's
        # provider-specific model id — blank it so the adapter uses its own
        # default (cartesia→sonic-3, elevenlabs→eleven_flash_v2_5, deepgram→voice).
        if _camp_provider != global_config.tts_provider:
            tts_model = ""

    script_config = _extract_script_config(campaign) or {}
    configured_persona = script_config.get("persona_type")
    # Single composition path. A campaign-less / persona-less call (a bare test
    # dial, or a pre-persona campaign) defaults to a knowledge-driven lead_gen
    # persona instead of a hardcoded script — the layered composer is now the
    # only way a telephony prompt is built.
    persona_type = configured_persona or "lead_gen"
    knowledge_driven = bool(script_config.get("knowledge_driven")) or not configured_persona

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

    def _compose(kd: bool) -> str:
        return compose_prompt(
            persona_type=persona_type,
            agent_name=agent_name,
            company_name=company_name,
            campaign_slots=script_config.get("campaign_slots") or {},
            additional_instructions=script_config.get("additional_instructions"),
            direction=direction.value,
            knowledge_driven=kd,
        )

    try:
        system_prompt = _compose(knowledge_driven)
        logger.info(
            "telephony_prompt_composed persona=%s agent=%s company=%s campaign=%s kd=%s",
            persona_type, agent_name, company_name, _campaign_id(campaign), knowledge_driven,
        )
    except PromptCompositionError as exc:
        # A slot-based persona with incomplete campaign_slots. Strict mode (the
        # default) fails loud so we never ship a half-filled prompt. Otherwise
        # retry the SAME persona in knowledge-driven (slot-free) mode, which
        # always composes — there is no hardcoded-script fallback anymore.
        strict = os.getenv("TELEPHONY_PROMPT_STRICT_MODE", "1").strip().lower() in {
            "1", "true", "yes", "on",
        }
        if strict:
            logger.error(
                "telephony_prompt_compose_failed campaign=%s persona=%s err=%s "
                "— strict mode, refusing to ship a half-filled prompt",
                _campaign_id(campaign), persona_type, exc,
            )
            raise
        logger.warning(
            "telephony_prompt_compose_failed campaign=%s persona=%s err=%s "
            "— retrying knowledge-driven (slot-free)",
            _campaign_id(campaign), persona_type, exc,
        )
        system_prompt = _compose(True)

    # (Brand-accuracy line is now part of the composed base prompt — see
    # prompts.composer.brand_correction_line, appended inside compose_prompt.)

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
                "Never mention AI, bots, automation, models, vendors, prompts, or internal systems in spoken output",
                "Never use filler openers such as Sure, Absolutely, Of course, or Great",
                "Never ask multiple questions in the same turn",
                "Never sound robotic or scripted",
                "Never push too hard — if rejected twice, close politely",
            ]
        ),
        flow=ConversationFlow(max_objection_attempts=2),
        # Per-turn ceiling (not a target). 2 forced every reply terse — no room
        # for consultative discovery, mood-matching, or natural expressiveness.
        # 5 lets the agent open up when it earns it; the persona prompt keeps it
        # SHORT by default and only fuller when warranted, so this is headroom,
        # not a mandate to monologue.
        response_max_sentences=5,
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

    # Per-tenant tuning resolution. T3.9 added the env-driven path; T4-C3
    # added DB-backed overrides — but the DB lookup is async, and this
    # function is sync. Production callers (the bridge) resolve tuning
    # asynchronously upstream and pass the result via
    # ``voice_tuning_override``; sync callers (tests, browser sessions,
    # ask_ai) fall back to the env-only sync path.
    _tenant_id = _campaign_tenant_id(campaign)
    if voice_tuning_override is not None:
        _tuning = voice_tuning_override
    else:
        _tuning = get_voice_tuning_resolver().for_tenant(_tenant_id)

    return VoiceSessionConfig(
        gateway_type=gateway_type,
        stt_provider_type="deepgram_flux",
        llm_provider_type=_llm_provider_type,
        tts_provider_type=tts_provider_type,
        stt_model="flux-general-en",
        stt_sample_rate=16000,
        stt_encoding="linear16",
        # Conversational-rhythm tunables come from the tenant resolver.
        # Defaults match the values this function used pre-T3.9 (0.85 EOT,
        # 500ms timeout, 0.7 eager) so an unset env var is a no-op for
        # every existing tenant.
        stt_eot_threshold=_tuning.stt_eot_threshold,
        stt_eot_timeout_ms=_tuning.stt_eot_timeout_ms,
        stt_eager_eot_threshold=_tuning.stt_eager_eot_threshold,
        # Per-call keyterm prompting: bias Flux toward the campaign's brand,
        # agent name, and product names so "Dojo"/"Dojo Go" isn't heard as
        # "Dodge". Email-spelling terms are gated to capture mode separately.
        stt_keyterms=_build_call_keyterms(
            company_name, agent_name, _extract_product_terms(script_config, company_name)
        ),
        turn_0_min_confidence=_tuning.turn_0_min_confidence,
        turn_0_min_alpha_chars=_tuning.turn_0_min_alpha_chars,
        llm_model=global_config.llm_model,
        llm_temperature=global_config.llm_temperature,
        llm_max_tokens=global_config.llm_max_tokens,
        llm_thinking_budget=0,
        voice_id=tts_voice_id,
        tts_model=tts_model,
        tts_sample_rate=16000,
        gateway_sample_rate=16000,
        gateway_input_sample_rate=16000,
        gateway_channels=1,
        gateway_bit_depth=16,
        gateway_target_buffer_ms=40,
        mute_during_tts=_telephony_mute_during_tts_default(),
        session_type="telephony",
        campaign_id=str(_campaign_id(campaign)) if campaign else "telephony",
        lead_id="sip-caller",
        # T1.1 — propagate tenant context so per-tenant credentials
        # resolve. Pull from the campaign's tenant_id when the campaign
        # row is present; None for legacy / dev paths. Reused from the
        # T3.9 lookup above to keep the call sites consistent.
        tenant_id=_tenant_id,
        agent_config=agent_config,
        system_prompt=system_prompt,
        direction=direction,
        persona_type=persona_type,
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


def _campaign_attr(campaign: Any, key: str) -> str:
    """Read a string field off a campaign dict/model; '' when absent/None."""
    if campaign is None:
        return ""
    val = campaign.get(key) if isinstance(campaign, dict) else getattr(campaign, key, None)
    return str(val).strip() if val else ""


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
    falls back to persona-level defaults. A missing persona is treated as
    lead_gen — the same default the prompt composer uses.
    """
    slots = (script_config or {}).get("campaign_slots") or {}
    default_bt, default_tone = _PERSONA_DEFAULTS.get(
        persona_type or "lead_gen",
        ("general business", "warm, professional, natural"),
    )
    business_type = (
        slots.get("business_type")
        or slots.get("industry")
        or default_bt
    )
    tone = slots.get("tone") or default_tone
    return str(business_type), str(tone)
