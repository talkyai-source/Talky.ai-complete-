"""
AI Provider Configuration Model

Defines the configuration structure for LLM, STT, and TTS providers.
This configuration is used in both the AI Options testing page and actual calls.
"""
from typing import Any, Optional, List, Dict
from pydantic import BaseModel, Field
from enum import Enum


class LLMProvider(str, Enum):
    """Available LLM providers"""
    GROQ = "groq"
    GEMINI = "gemini"
    CEREBRAS = "cerebras"


class STTProvider(str, Enum):
    """Available STT providers"""
    DEEPGRAM = "deepgram"


class TTSProvider(str, Enum):
    """Available TTS providers"""
    CARTESIA = "cartesia"
    GOOGLE = "google"
    DEEPGRAM = "deepgram"
    ELEVENLABS = "elevenlabs"


class GroqModel(str, Enum):
    """Available Groq models — verified against a live /v1/models call.

    MENU REBUILT 2026-08-17. Both Llama entries were removed because they are
    GONE FROM THE ACCOUNT, not because we deselected them:

        $ GET https://api.groq.com/openai/v1/models
        13 models: allam-2-7b, canopylabs/orpheus-*, groq/compound,
        groq/compound-mini, meta-llama/llama-prompt-guard-2-*,
        openai/gpt-oss-120b, openai/gpt-oss-20b,
        openai/gpt-oss-safeguard-20b, qwen/qwen3.6-27b, whisper-large-v3*

        $ POST /chat/completions model=llama-3.1-8b-instant
        404 "The model `llama-3.1-8b-instant` does not exist or you do not
             have access to it."   (same for llama-3.3-70b-versatile)

    Offering an id the account cannot serve is worse than offering nothing: the
    call fails at the first turn, and with LLM failover on it silently runs a
    different model than the tenant chose.

    THE GPT-OSS DECISION HAS BEEN REOPENED, DELIBERATELY. They were removed on
    2026-06-25 as "agentic task-completion reasoners that misbehave on
    conversational voice (stack questions, NATO-spell)". That judgement was made
    when Llama was available, i.e. it was "gpt-oss vs a better-behaved option".
    It no longer is: after the Llama removals the only conversational LLMs this
    account can serve are Qwen 3.6 and the GPT-OSS family, and the latency gap
    between them is not marginal (measured on this account, 7,281-token prompt,
    reasoning_effort=low):

        qwen/qwen3.6-27b      cold TTFT 697ms   warm TTFT 672ms   cached: none
        openai/gpt-oss-120b   cold TTFT 451ms   warm TTFT 102ms   cached 7168/7281
        openai/gpt-oss-20b    cold ---- 475ms   warm ---- 119ms   cached 7168/7281

    Groq supports prompt caching on the GPT-OSS family only, which is why the
    warm number collapses — and our prompt order already puts static content
    first, exactly as Groq's caching docs require, so the 2026-08-13 reorder
    pays off the moment one of these is selected.

    They are listed as PREVIEW and are not the default. The June behavioural
    finding stands until someone re-tests it against the current prompt, which
    has been substantially reworked since (see the prompt-craft audit). Latency
    alone does not overturn a conversation-quality finding.
    """
    # Preview Models
    # qwen3-32b removed 2026-06-27: it dodged the AI-disclosure question and
    # hallucinated prices / leaked a card number in the weakness audit — qwen3.6
    # behaves strictly better, so we keep only that.
    # Qwen 3.6 27B — reasoning toggles between "default" and "none"; we run it
    # with thinking disabled (reasoning_effort="none") for low-latency voice.
    QWEN_3_6_27B = "qwen/qwen3.6-27b"
    GPT_OSS_120B = "openai/gpt-oss-120b"
    GPT_OSS_20B = "openai/gpt-oss-20b"


class GeminiModel(str, Enum):
    """Available Gemini / Gemma models served via the Google AI Studio API.

    Menu refreshed 2026-07-28 against a live ListModels call on the production
    GEMINI_API_KEY, so every id below is confirmed to exist and to support
    generateContent — not copied from docs.
    """
    GEMINI_2_5_FLASH = "gemini-2.5-flash"
    # Released March 2026 (developer preview). ~2.5× faster TTFT and ~64%
    # higher output throughput than 2.5 Flash.
    # KEPT even though `gemini-3.1-flash-lite` is now GA: a tenant still has
    # this exact id stored in tenant_ai_configs, and config.py rejects any
    # llm_model outside this menu — dropping it would lock that tenant out of
    # saving their AI Options. Remove only after migrating that row.
    GEMINI_3_1_FLASH_LITE_PREVIEW = "gemini-3.1-flash-lite-preview"
    # Same model, now generally available. Prefer this id for new configs.
    GEMINI_3_1_FLASH_LITE = "gemini-3.1-flash-lite"
    # GA. Google: "fastest, most cost-effective 3.5 model for high-throughput
    # execution" — the lowest-latency Gemini currently offered.
    GEMINI_3_5_FLASH_LITE = "gemini-3.5-flash-lite"
    # GA, newest Flash. Google: balances speed with intelligence, fewer output
    # tokens and fewer tool calls than 3.5 Flash, at a lower price.
    GEMINI_3_6_FLASH = "gemini-3.6-flash"
    # gemini-3.5-flash itself stays OFF the menu (removed 2026-06-25): it
    # NATO-spells emails (S for Sierra…) 3/3 even after the read-back guardrail
    # fix — a model-level quirk prompt rules don't beat, voice-unsafe for
    # core-field capture. See the warning on the 3.5 Flash-Lite entry below.
    # The provider still handles any gemini-3.x name if one is passed.
    # Gemma 4 is now live on AI Studio (verified 2026-07-28: gemma-4-31b-it and
    # gemma-4-26b-a4b-it both returned by ListModels). Uncomment plus add a
    # matching GEMINI_MODELS entry to offer them; no other code change needed.
    # GEMMA_4_31B = "gemma-4-31b-it"
    # GEMMA_4_26B_A4B = "gemma-4-26b-a4b-it"


class CerebrasModel(str, Enum):
    """Models served by Cerebras Inference (wafer-scale, OpenAI-compatible API).

    Reasoning control differs PER MODEL and the difference matters for voice —
    see CEREBRAS_REASONING_NONE_SUPPORTED below. Source:
    https://inference-docs.cerebras.ai/api-reference/chat-completions
    """
    # reasoning_effort: "none" (default) | low | medium | high  → fully off-able
    GEMMA_4_31B = "gemma-4-31b"
    # reasoning_effort: "none" disables reasoning entirely  → fully off-able
    ZAI_GLM_4_7 = "zai-glm-4.7"
    # reasoning_effort: low | medium (default) | high — "none" is NOT accepted,
    # so thinking cannot be switched off, only minimised to "low".
    GPT_OSS_120B = "gpt-oss-120b"


# Models whose reasoning can be switched OFF outright via reasoning_effort="none".
# Anything not listed here gets the lowest effort the model does accept, so the
# provider never sends a value the API will reject.
CEREBRAS_REASONING_NONE_SUPPORTED = {
    CerebrasModel.GEMMA_4_31B.value,
    CerebrasModel.ZAI_GLM_4_7.value,
}

# Lowest reasoning_effort each model actually accepts. Used when "none" is
# unsupported so we still minimise thinking rather than silently leaving the
# model on its default.
CEREBRAS_MIN_REASONING_EFFORT = {
    CerebrasModel.GPT_OSS_120B.value: "low",
}


class DeepgramModel(str, Enum):
    """Available Deepgram STT models"""
    NOVA_3 = "nova-3"
    NOVA_2 = "nova-2"


class CartesiaModel(str, Enum):
    """Available Cartesia TTS models"""
    SONIC_3 = "sonic-3"
    SONIC_2 = "sonic-2"


class GoogleTTSModel(str, Enum):
    """Available Google TTS models"""
    CHIRP3_HD = "Chirp3-HD"


class DeepgramTTSModel(str, Enum):
    """Available Deepgram TTS models"""
    AURA_2 = "aura-2"


class ModelInfo(BaseModel):
    """Model metadata"""
    id: str
    name: str
    description: str
    speed: Optional[str] = None
    price: Optional[str] = None
    context_window: Optional[int] = None
    is_preview: bool = False
    provider: Optional[str] = None


class VoiceInfo(BaseModel):
    """TTS Voice metadata with preview support"""
    id: str
    name: str
    language: str = "en"
    description: str = ""
    gender: Optional[str] = None
    accent: Optional[str] = None
    accent_color: str = "#6366f1"  # For UI avatar display
    preview_text: str = "Hello, I am your AI voice assistant. How can I help you today?"
    provider: str = "cartesia"
    tags: List[str] = []
    preview_url: Optional[str] = None


class AIProviderConfig(BaseModel):
    """
    AI Provider Configuration
    
    Used for both testing in AI Options and actual voice calls.
    Stored per-tenant in the database.
    """
    # LLM Configuration
    # MVP PAIR, 2026-08-24 — see docs/MODEL-SELECTION.md for the measurements.
    #
    # Was llama-3.1-8b-instant until 2026-08-17, by which time that id 404'd on
    # this account, then Qwen 3.6. Qwen was measured at 640ms p50 first-token
    # against a production-sized prompt — 2.4x the pair below — and it is the
    # reason 49 of 51 production turns on 2026-08-23 were tagged [SLOW].
    #
    # Cerebras gpt-oss-120b: p50 269ms, p95 566ms, 10/10 on the voice
    # correctness battery. Groq serves the SAME model as the fallback, so a
    # mid-call failover does not change how the agent sounds.
    llm_provider: LLMProvider = LLMProvider.CEREBRAS
    llm_model: str = CerebrasModel.GPT_OSS_120B.value
    llm_temperature: float = Field(default=0.6, ge=0.0, le=2.0)
    llm_max_tokens: int = Field(default=90, ge=1, le=5000)  # ceiling raised for consultative replies; per-turn length still governed by the persona + sentence cap
    
    # STT Configuration
    stt_provider: STTProvider = STTProvider.DEEPGRAM
    stt_model: str = DeepgramModel.NOVA_3.value
    # STT engine — which Deepgram speech model drives turn-taking:
    #   "deepgram_flux" — Flux, semantic turn-detection (/v2/listen). Default.
    #   "deepgram_nova" — Nova-3, acoustic VAD + endpointing (/v1/listen).
    # Independent of the failover secondary (Flux always falls back to Nova-3).
    stt_engine: str = "deepgram_flux"
    stt_language: str = "en"
    
    # TTS Configuration - Using Deepgram Aura-2 (fast and high quality)
    tts_provider: TTSProvider = TTSProvider.DEEPGRAM
    tts_model: str = DeepgramTTSModel.AURA_2.value  # Deepgram Aura-2
    tts_voice_id: str = "aura-zeus-en"  # Zeus - professional male voice
    tts_sample_rate: int = 24000  # Deepgram Aura-2 sample rate

    # Per-tenant voice-pipeline tuning (T4-C3). Partial dict matching
    # the VoiceTuning dataclass (app/domain/services/voice_tuning.py).
    # Empty / missing means "use env+code defaults" — operators who
    # don't opt in see zero behaviour change.
    voice_tuning: Optional[Dict[str, Any]] = Field(default=None)

    # ── Pipeline mode (Realtime add-on, Phase 1) ─────────────────────────
    # Selects HOW the voice call is served:
    #   "cascaded"  — the classic three-stage pipeline (STT → LLM → TTS)
    #                 with the full composed system prompt. THE DEFAULT, so
    #                 every existing tenant is byte-for-byte unaffected.
    #   "realtime"  — a single OpenAI gpt-realtime-2 speech-to-speech
    #                 WebSocket session (audio in → audio out, own voice,
    #                 semantic VAD, function calling). Completely separate
    #                 code path; does NOT touch the cascaded prompt machinery.
    # NOTE (Phase 1): the field exists and is validated, but the gateway does
    # NOT yet branch on it — wiring lands in Phase 1b once the bridge is proven.
    pipeline_mode: str = "cascaded"  # "cascaded" | "realtime"
    # Realtime-only knobs (ignored entirely when pipeline_mode == "cascaded").
    realtime_model: str = "gpt-realtime-2"
    realtime_voice: str = "marin"
    # Optional overrides for turn_detection / noise_reduction etc. Sane
    # defaults are applied by the session builder, so this may stay None.
    # Recognised keys: {"turn_detection": {"type","eagerness"},
    #                   "noise_reduction": {"type"},
    #                   "transcription_model": str,
    #                   "provider": "openai" (default) | "xai",
    #                   "model": str (xai-only model override),
    #                   "agent_id": str (xai-only: use a console-built agent
    #                                    instead of a bare model)}
    # "provider" is the opt-in switch for the xAI Grok Voice adapter (see
    # app/infrastructure/realtime/xai_realtime.py) — omitted/"openai" keeps
    # every existing tenant on the OpenAI gpt-realtime-2 path unchanged.
    realtime_settings: Optional[Dict[str, Any]] = Field(default=None)

    class Config:
        use_enum_values = True


class ProviderListResponse(BaseModel):
    """Response for available providers listing"""
    llm: Dict
    stt: Dict
    tts: Dict
    # Realtime (speech-to-speech) pipeline mode. Additive — older frontends
    # that don't know about it simply ignore the extra key. Empty default so
    # the field is always present with a stable shape.
    realtime: Dict = Field(default_factory=dict)


class LatencyTestResult(BaseModel):
    """Result of a latency test"""
    provider: str
    model: str
    latency_ms: float
    first_token_ms: Optional[float] = None
    total_tokens: Optional[int] = None
    success: bool
    error: Optional[str] = None


class LLMTestRequest(BaseModel):
    """Request for LLM testing"""
    model: str = GroqModel.QWEN_3_6_27B.value  # was LLAMA_3_3_70B — 404s here
    message: str
    temperature: float = Field(default=0.6, ge=0.0, le=2.0)
    max_tokens: int = Field(default=150, ge=1, le=5000)


class LLMTestResponse(BaseModel):
    """Response from LLM testing"""
    response: str
    latency_ms: float
    first_token_ms: float
    total_tokens: int
    model: str


class TTSTestRequest(BaseModel):
    """Request for TTS testing"""
    model: str = CartesiaModel.SONIC_3.value
    voice_id: str
    text: str
    sample_rate: int = 24000  # Official Cartesia recommended


class TTSTestResponse(BaseModel):
    """Response from TTS testing"""
    audio_base64: str
    latency_ms: float
    first_audio_ms: float
    duration_seconds: float
    model: str
    voice_id: str


# =============================================================================
# GROQ MODELS - Production + Preview
# =============================================================================

# ── THE MVP MENU (2026-08-24) ────────────────────────────────────────────────
# ONE model per provider, both the same weights on independent infrastructure.
# Full measurements and the argument for that choice: docs/MODEL-SELECTION.md
#
# Removed here, with reasons, so nobody re-adds one from memory:
#   qwen/qwen3.6-27b   640ms p50 first-token on a production-sized prompt —
#                      2.4x the pair below, and no prompt caching on Groq at
#                      all (it returns no cached_tokens field). It is the
#                      measured cause of 49/51 prod turns being tagged [SLOW].
#   openai/gpt-oss-20b 416ms p50. Correct (10/10) but strictly slower than the
#                      120b on the same provider, so it earns no menu slot.
GROQ_MODELS = [
    ModelInfo(
        id=GroqModel.GPT_OSS_20B.value,
        name="GPT-OSS 20B (Groq)",
        description=(
            "MVP fallback, and a DIFFERENT model from the Cerebras primary by "
            "requirement — the two must never be the same weights. Tightest "
            "tail measured on any candidate: p50 416ms, p95 449ms, worst turn "
            "465ms, against a 37k-char prompt. Scored 10/10 on the voice "
            "battery, including repeating a captured email back EXACTLY — the "
            "check qwen fails in practice, since it acknowledges an address "
            "without ever confirming it. Prompt-cached on Groq."
        ),
        speed="416 ms p50 / 449 ms p95 (measured 2026-08-24)",
        price="See console.groq.com billing",
        context_window=131072,
        is_preview=False,
        provider="groq",
    ),
]

# HIDDEN, NOT FORBIDDEN.
#
# These are no longer offered in AI Options, but a tenant who already has one
# stored must still be able to save their settings. Validation accepts this
# list; the UI never shows it. Narrowing the OFFERED menu without this would
# 400 a tenant on a value they never chose to have — locking them out of their
# own settings page to enforce a menu change.
#
# `llama-3.1-8b-instant` is here despite 404ing on the account, for exactly
# that reason: 5 tenants still store it, and blocking their save does not fix
# them, it just traps them. Repairing those rows is a separate, deliberate job.
GROQ_MODELS_HIDDEN = [
    "qwen/qwen3.6-27b",       # 640ms p50, and never repeats a captured email back
    "openai/gpt-oss-120b",    # fast, but the SAME weights as the Cerebras primary
    "llama-3.1-8b-instant",   # DEAD on this account (404). Held for 5 tenants.
    "llama-3.3-70b-versatile",  # DEAD on this account (404).
]

# =============================================================================
# GEMINI MODELS (Google AI Studio)
# =============================================================================
# Ordered FASTEST-FIRST by measurement, not by version number.
# Gemma 4 (31B dense, 26B A4B MoE) is available on AI Studio but not offered
# here yet; see the commented enum members above.
#
# ── MEASURED 2026-07-28, from the prod host, 5 samples per model ────────────
# Request shaped like a real voice turn: ~9.5 KB system prompt (~2.4k tokens),
# 60-token reply, thinkingLevel=minimal. Figures are full round-trip medians.
#
#   gemini-2.5-flash        186 ms   ← fastest by ~3×
#   gemini-3.5-flash-lite   552 ms
#   gemini-3.1-flash-lite   570 ms
#   gemini-3.6-flash        980 ms   ← newest, and the slowest of the four
#
# This INVERTS both Google's marketing ("3.5 Flash-Lite is the fastest 3.5
# model") and the claim previously written here ("3.1 Flash-Lite is ~2.5×
# faster TTFT than 2.5 Flash" — measured false, it is ~3× slower). The likely
# cause is documented in gemini.py::_is_gemini_3: the 3.x family cannot fully
# disable thinking (thinking_level floors at "minimal"), while 2.5 honours
# thinking_budget=0 and genuinely turns it off. Newer ≠ faster here.
#
# CAVEATS — re-measure before treating as settled:
#  - Round-trip to completion, NOT streaming TTFT. Thinking precedes the first
#    token, so the ranking should hold for TTFT, but that is inference.
#  - Single region, single hour, n=5. Google may shift capacity.
#  - Still slower than the Groq default: llama-3.1-8b-instant runs ~90 ms TTFT.
#    None of these entries is "faster than Groq".

GEMINI_MODELS = [
    ModelInfo(
        id=GeminiModel.GEMINI_2_5_FLASH.value,
        name="Gemini 2.5 Flash — fastest",
        description=(
            "FASTEST Gemini on this stack: 186 ms median round-trip on a "
            "voice-shaped request, ~3× quicker than every 3.x model measured "
            "2026-07-28. It is the oldest of the four and still the quickest, "
            "because 2.5 honours thinking_budget=0 and genuinely disables "
            "thinking, while the 3.x family floors at thinking_level=minimal. "
            "GA, ~1M-token context, 65K max output, free-tier grounding."
        ),
        speed="186 ms median (measured)",
        price="$0.30 input / $2.50 output per 1M tokens",
        context_window=1_048_576,
        is_preview=False,
        provider="gemini",
    ),
    ModelInfo(
        id=GeminiModel.GEMINI_3_5_FLASH_LITE.value,
        name="Gemini 3.5 Flash-Lite",
        description=(
            "Google's stated fastest/cheapest 3.5 model, GA. Measured here at "
            "552 ms median — quickest of the 3.x family but ~3× slower than "
            "2.5 Flash. ⚠ NOT cleared for live voice: its sibling "
            "gemini-3.5-flash NATO-spells email addresses (S for Sierra…) and "
            "that quirk has not been retested on Flash-Lite. Run the email "
            "read-back check before putting it on calls."
        ),
        speed="552 ms median (measured)",
        price="Lowest of the 3.5 family",
        context_window=1_048_576,
        is_preview=False,
        provider="gemini",
    ),
    ModelInfo(
        id=GeminiModel.GEMINI_3_1_FLASH_LITE.value,
        name="Gemini 3.1 Flash-Lite",
        description=(
            "Now generally available (was preview). Measured at 570 ms median. "
            "The Gemini this stack has the most production evidence for — it "
            "is what ran the 2026-07-24 session. Prefer this id over the "
            "legacy '-preview' one below."
        ),
        speed="570 ms median (measured)",
        price="Lower than 2.5 Flash",
        context_window=1_048_576,
        is_preview=False,
        provider="gemini",
    ),
    ModelInfo(
        id=GeminiModel.GEMINI_3_1_FLASH_LITE_PREVIEW.value,
        name="Gemini 3.1 Flash-Lite (preview — legacy id)",
        description=(
            "Superseded by the GA 'gemini-3.1-flash-lite' above; same model. "
            "Kept only so tenants already saved on this id can still write "
            "their AI Options config. Prefer the GA id for anything new."
        ),
        speed="570 ms median (measured)",
        price="Lower than 2.5 Flash",
        context_window=1_048_576,
        is_preview=True,
        provider="gemini",
    ),
    ModelInfo(
        id=GeminiModel.GEMINI_3_6_FLASH.value,
        name="Gemini 3.6 Flash — newest",
        description=(
            "Newest Flash, GA, and the SLOWEST of the four here: 980 ms "
            "median, ~5× Gemini 2.5 Flash. Emits fewer output tokens and "
            "fewer tool calls than 3.5 Flash at a lower price, and is the "
            "strongest Gemini for agentic and multimodal work. Choose it when "
            "answer quality outweighs latency — not for real-time voice."
        ),
        speed="980 ms median (measured)",
        price="Below 3.5 Flash",
        context_window=1_048_576,
        is_preview=False,
        provider="gemini",
    ),
]


# =============================================================================
# CEREBRAS MODELS (Cerebras Inference — wafer-scale, OpenAI-compatible API)
# =============================================================================
# Added 2026-07-28. Why this provider exists in the menu at all: Groq's
# free-tier cap is 8K TPM per ORGANISATION, and a single assistant turn is
# ~8.7K tokens, so on Groq that request can never fit. Cerebras pay-as-you-go
# is 500K–1M TPM per model — 60–125x the headroom.
#
# Rate limits (Developer / pay-as-you-go, per official docs):
#   gpt-oss-120b   1M TPM   1K RPM
#   zai-glm-4.7    500K TPM 500 RPM
#   gemma-4-31b    500K TPM 300 RPM
#
# context_window is deliberately left None: the published rate-limit page does
# not state per-model context windows, and guessing one would be worse than
# showing nothing. Fill in only from a verified source.
#
# THINKING: reasoning support is NOT uniform, and the difference is the whole
# reason CEREBRAS_REASONING_NONE_SUPPORTED exists. VERIFIED against the live API
# 2026-07-28 — gpt-oss-120b returns a hard error for "none":
#     "Unsupported reasoning effort: none. Supported values are 'low',
#      'medium', and 'high'."  (type=invalid_request_error)
# so the provider floors that model at "low". Confirmed effective via
# usage.completion_tokens_details.reasoning_tokens:
#     gemma-4-31b  + none -> 0   (thinking genuinely off)
#     zai-glm-4.7  + none -> 0   (thinking genuinely off)
#     gpt-oss-120b + low  -> 16  (cannot reach 0 by design)
# See cerebras.py::_reasoning_effort.
#
# NOTE ON ERROR SHAPE: Cerebras returns errors FLAT — {"message","type","code"} —
# not OpenAI's nested {"error":{...}}. Any hand-rolled probe that checks for an
# "error" key will read a rejection as a success. The SDK raises properly, so
# this only bites ad-hoc curl checks.
#
# MEASURED 2026-07-28, prod host, 5 samples, voice-shaped request (~9.5 KB
# system prompt, 60-token reply). Full round-trip medians:
#     zai-glm-4.7    192 ms   (179-234 — tight, the most consistent of the three)
#     gpt-oss-120b   269 ms   (181-1077 — wide tail)
#     gemma-4-31b    402 ms   (176-1067 — wide tail)
# For comparison on the same harness: gemini-2.5-flash 186 ms, and Groq
# llama-3.1-8b-instant ~90 ms TTFT.

# Removed here, with reasons:
#   gemma-4-31b   Best p50 of any Cerebras model (317ms) and DISQUALIFIED on
#                 spread: p95 1133ms, worst turn 1671ms, stdev 368. Roughly one
#                 turn in ten would feel broken. Judged on p50 alone we would
#                 have shipped it — see docs/MODEL-SELECTION.md §4.
#   zai-glm-4.7   NOT SERVED BY THIS ACCOUNT. A live /v1/models call on
#                 2026-08-24 returned exactly two ids: gemma-4-31b and
#                 gpt-oss-120b. Offering it was the same defect as the dead
#                 Llama entries — selecting it fails at the first turn and
#                 failover then silently runs a model the tenant did not pick.
CEREBRAS_MODELS = [
    ModelInfo(
        id=CerebrasModel.GPT_OSS_120B.value,
        name="GPT-OSS 120B (Cerebras)",
        description=(
            "MVP primary. Fastest first token measured on either provider — "
            "p50 269ms, p95 566ms against a 37k-char prompt — with the second "
            "tightest spread (stdev 118). Scored 10/10 on the voice battery. "
            "Highest limits (1M TPM / 1K RPM) and cheapest of the Cerebras "
            "models. Thinking cannot be disabled on this model: 'none' is "
            "rejected, so it is floored at reasoning_effort='low'."
        ),
        speed="269 ms p50 / 566 ms p95 (measured 2026-08-24)",
        price="~$0.35 in / $0.75 out per 1M tokens",
        is_preview=False,
        provider="cerebras",
    ),
]

# Hidden, not forbidden — same reasoning as GROQ_MODELS_HIDDEN above.
CEREBRAS_MODELS_HIDDEN = [
    "gemma-4-31b",   # p95 1133ms, worst turn 1671ms — too erratic for voice
    "zai-glm-4.7",   # NOT SERVED by this account (live /v1/models, 2026-08-24)
]


# =============================================================================
# OPENAI REALTIME (gpt-realtime-2 speech-to-speech pipeline mode)
# =============================================================================
# Static catalog surfaced by the AI-Options /providers endpoint so the frontend
# can render the realtime card. Voices are OpenAI's gpt-realtime voice set.
# marin + cedar are the newest expressive voices; the rest are the standard
# realtime voices. If OpenAI adds/removes voices, edit this list only.
REALTIME_MODEL = "gpt-realtime-2"

REALTIME_VOICES = [
    {"id": "marin", "name": "Marin", "description": "Warm, natural, expressive — recommended default.", "gender": "female"},
    {"id": "cedar", "name": "Cedar", "description": "Warm, grounded, natural male voice.", "gender": "male"},
    {"id": "alloy", "name": "Alloy", "description": "Neutral, balanced, general-purpose.", "gender": "neutral"},
    {"id": "ash", "name": "Ash", "description": "Clear, measured, professional.", "gender": "male"},
    {"id": "ballad", "name": "Ballad", "description": "Soft, expressive, storytelling tone.", "gender": "male"},
    {"id": "coral", "name": "Coral", "description": "Bright, friendly, upbeat.", "gender": "female"},
    {"id": "sage", "name": "Sage", "description": "Calm, reassuring, thoughtful.", "gender": "female"},
    {"id": "verse", "name": "Verse", "description": "Lively, dynamic, conversational.", "gender": "male"},
]

# Selectable knobs surfaced to the frontend (map 1:1 to session builder options).
REALTIME_TURN_DETECTION = ["low", "medium", "high"]
REALTIME_NOISE_REDUCTION = ["near_field", "far_field", "none"]


DEEPGRAM_MODELS = [
    ModelInfo(
        id=DeepgramModel.NOVA_3.value,
        name="Nova 3",
        description="Best accuracy, real-time optimized, multilingual",
        speed="Real-time",
        provider="deepgram",
    ),
    ModelInfo(
        id=DeepgramModel.NOVA_2.value,
        name="Nova 2",
        description="Fast and cost-effective for batch processing",
        speed="Real-time",
        provider="deepgram",
    ),
]

# Selectable STT engines (the speech model that drives turn-taking). Distinct
# from DEEPGRAM_MODELS — this is the Flux-vs-Nova choice surfaced in AI Options.
STT_ENGINES = [
    ModelInfo(
        id="deepgram_flux",
        name="Deepgram Flux",
        description="Semantic turn-detection — predicts when the caller is conversationally done (confidence-based), for the most natural, low-latency turn-taking. Newest (beta).",
        speed="Turn-based",
        is_preview=True,
        provider="deepgram",
    ),
    ModelInfo(
        id="deepgram_nova",
        name="Deepgram Nova-3",
        description="Acoustic VAD + endpointing — proven and stable; formats emails and numbers natively. Also the automatic failover whenever Flux is unavailable.",
        speed="Streaming",
        is_preview=False,
        provider="deepgram",
    ),
]

CARTESIA_MODELS = [
    ModelInfo(
        id=CartesiaModel.SONIC_3.value,
        name="Sonic 3",
        description="Latest model with best quality and speed",
        speed="~90ms latency",
        provider="cartesia",
    ),
    ModelInfo(
        id=CartesiaModel.SONIC_2.value,
        name="Sonic 2",
        description="Previous generation, still highly capable",
        speed="~100ms latency",
        provider="cartesia",
    ),
]

GOOGLE_TTS_MODELS = [
    ModelInfo(
        id=GoogleTTSModel.CHIRP3_HD.value,
        name="Chirp 3: HD",
        description="Latest generation with realism and emotional resonance",
        speed="~200ms latency",
        provider="google",
    ),
]

DEEPGRAM_TTS_MODELS = [
    ModelInfo(
        id=DeepgramTTSModel.AURA_2.value,
        name="Aura-2",
        description="Deepgram's latest low-latency neural text-to-speech model family.",
        speed="Streaming optimized",
        provider="deepgram",
    ),
]

ELEVENLABS_TTS_MODELS = [
    ModelInfo(
        id="eleven_flash_v2_5",
        name="Flash v2.5",
        description="ElevenLabs real-time model tuned for the lowest latency voice interactions.",
        speed="~75ms latency",
        provider="elevenlabs",
    ),
    ModelInfo(
        id="eleven_multilingual_v2",
        name="Multilingual v2",
        description="Highest-quality ElevenLabs model with strong multilingual support and richer expression.",
        speed="High quality",
        provider="elevenlabs",
    ),
    ModelInfo(
        id="eleven_turbo_v2_5",
        name="Turbo v2.5",
        description="Fast ElevenLabs model with a quality/latency balance suited to production voice agents.",
        speed="Low latency",
        provider="elevenlabs",
    ),
]


# =============================================================================
# CARTESIA VOICES - Curated for Voice Agents
# =============================================================================

# Official Cartesia voice IDs for voice agents
# Reference: https://docs.cartesia.ai/build-with-cartesia/voices

CARTESIA_VOICES = [
    # Professional Female Voices
    VoiceInfo(
        id="f786b574-daa5-4673-aa0c-cbe3e8534c02",
        name="Katie",
        description="Professional, warm female voice. Ideal for business calls.",
        gender="female",
        accent="American",
        accent_color="#ec4899",  # Pink
        tags=["professional", "warm", "business"],
        provider="cartesia"
    ),
    VoiceInfo(
        id="6ccbfb76-1fc6-48f7-b71d-91ac6298247b",
        name="Aurora",
        description="Energetic, friendly female voice. Great for customer service.",
        gender="female",
        accent="American",
        accent_color="#f97316",  # Orange
        tags=["energetic", "friendly", "customer-service"],
        provider="cartesia"
    ),
    VoiceInfo(
        id="a0e99841-438c-4a64-b679-ae501e7d6091",
        name="Sarah",
        description="Calm, reassuring female voice. Perfect for support calls.",
        gender="female",
        accent="American",
        accent_color="#8b5cf6",  # Purple
        tags=["calm", "reassuring", "support"],
        provider="cartesia"
    ),
    # Lily (d46abd1d) removed — returns no audio with current API key (deprecated)
    # Professional Male Voices
    VoiceInfo(
        id="228fca29-3a0a-435c-8728-5cb483251068",
        name="Kiefer",
        description="Confident, authoritative male voice. Great for sales calls.",
        gender="male",
        accent="American",
        accent_color="#3b82f6",  # Blue
        tags=["confident", "authoritative", "sales"],
        provider="cartesia"
    ),
    VoiceInfo(
        id="41534e16-2966-4c6b-9670-111411def906",
        name="Ryan",
        description="Youthful, cool & confident male voice. 20-30 sound.",
        gender="male",
        accent="American",
        accent_color="#10b981",  # Emerald
        tags=["youthful", "cool", "confident"],
        provider="cartesia"
    ),
    VoiceInfo(
        id="b7d50908-b17c-442d-ad8d-810c63997ed9",
        name="James",
        description="British professional male voice. Smooth and articulate.",
        gender="male",
        accent="British",
        accent_color="#6366f1",  # Indigo
        tags=["british", "professional", "articulate"],
        provider="cartesia"
    ),
    VoiceInfo(
        id="c45bc5ec-dc68-4feb-8829-6e6b2748095d",
        name="Adam",
        description="Deep, American male voice. Brooding and tough.",
        gender="male",
        accent="American",
        accent_color="#ef4444",  # Red
        tags=["deep", "tough", "american"],
        provider="cartesia"
    ),
    # Storytelling / Warm Voices
    # Veda Sky (5345cf08) removed — returns no audio with current API key (deprecated)
    VoiceInfo(
        id="bd9120b6-7761-47a6-a446-77ca49132781",
        name="Susie",
        description="Neutral young narrator. Soothing middle-aged female.",
        gender="female",
        accent="American",
        accent_color="#14b8a6",  # Teal
        tags=["narrator", "neutral", "soothing"],
        provider="cartesia"
    ),
]


# =============================================================================
# GOOGLE CHIRP 3 HD VOICES - Ultra-Realistic Streaming TTS
# =============================================================================

# Google Cloud Chirp 3: HD voices optimized for gRPC streaming
# Reference: https://cloud.google.com/text-to-speech/docs/voices

GOOGLE_CHIRP3_VOICES = [
    # Male Voices
    VoiceInfo(
        id="en-US-Chirp3-HD-Orus",
        name="Orus",
        description="Deep, authoritative male voice. Commanding presence for professional calls.",
        gender="male",
        accent="American",
        accent_color="#1e40af",  # Deep Blue
        tags=["authoritative", "deep", "professional"],
        provider="google",
        preview_text="Hello, I'm calling from your voice assistant. How may I help you today?"
    ),
    VoiceInfo(
        id="en-US-Chirp3-HD-Charon",
        name="Charon",
        description="Mature, reassuring male voice. Trustworthy and reliable tone.",
        gender="male",
        accent="American",
        accent_color="#1d4ed8",  # Blue  
        tags=["mature", "reassuring", "trustworthy"],
        provider="google",
        preview_text="Good day, this is your AI assistant. I'm here to assist you."
    ),
    VoiceInfo(
        id="en-US-Chirp3-HD-Fenrir",
        name="Fenrir",
        description="Energetic, confident male voice. Great for sales and outreach.",
        gender="male",
        accent="American",
        accent_color="#2563eb",  # Bright Blue
        tags=["energetic", "confident", "sales"],
        provider="google",
        preview_text="Hi there! I'm reaching out to help you with an exciting opportunity."
    ),
    VoiceInfo(
        id="en-US-Chirp3-HD-Puck",
        name="Puck",
        description="Friendly, approachable male voice. Perfect for customer service.",
        gender="male",
        accent="American",
        accent_color="#3b82f6",  # Sky Blue
        tags=["friendly", "approachable", "service"],
        provider="google",
        preview_text="Hello! Thanks for calling. Let me help you with that right away."
    ),
    # Female Voices
    VoiceInfo(
        id="en-US-Chirp3-HD-Kore",
        name="Kore",
        description="Warm, professional female voice. Ideal for business communications.",
        gender="female",
        accent="American",
        accent_color="#be185d",  # Rose
        tags=["warm", "professional", "business"],
        provider="google",
        preview_text="Hello, I'm your AI assistant. How can I help you today?"
    ),
    VoiceInfo(
        id="en-US-Chirp3-HD-Aoede",
        name="Aoede",
        description="Clear, articulate female voice. Excellent for appointments and reminders.",
        gender="female",
        accent="American",
        accent_color="#db2777",  # Pink
        tags=["clear", "articulate", "appointments"],
        provider="google",
        preview_text="Hi, I'm calling to confirm your appointment. Do you have a moment?"
    ),
    VoiceInfo(
        id="en-US-Chirp3-HD-Leda",
        name="Leda",
        description="Soothing, empathetic female voice. Perfect for support and healthcare.",
        gender="female",
        accent="American",
        accent_color="#ec4899",  # Bright Pink
        tags=["soothing", "empathetic", "support"],
        provider="google",
        preview_text="Hi, I'm here to help. Please tell me what you need assistance with."
    ),
    VoiceInfo(
        id="en-US-Chirp3-HD-Zephyr",
        name="Zephyr",
        description="Youthful, vibrant female voice. Great for engagement and outreach.",
        gender="female",
        accent="American",
        accent_color="#f472b6",  # Light Pink
        tags=["youthful", "vibrant", "engagement"],
        provider="google",
        preview_text="Hey! I wanted to reach out and share some exciting news with you!"
    ),
]

# =============================================================================
# DEEPGRAM AURA-2 VOICES - Official Voice IDs
# =============================================================================
# Source: https://developers.deepgram.com/docs/tts-models
# Includes all currently documented Aura-2 voices across supported languages.
_DEEPGRAM_AURA2_VOICE_SPECS = [
    # English (all available)
    ("aura-2-amalthea-en", "Amalthea", "en", "female"),
    ("aura-2-andromeda-en", "Andromeda", "en", "female"),
    ("aura-2-apollo-en", "Apollo", "en", "male"),
    ("aura-2-arcas-en", "Arcas", "en", "male"),
    ("aura-2-aries-en", "Aries", "en", "male"),
    ("aura-2-asteria-en", "Asteria", "en", "female"),
    ("aura-2-athena-en", "Athena", "en", "female"),
    ("aura-2-atlas-en", "Atlas", "en", "male"),
    ("aura-2-aurora-en", "Aurora", "en", "female"),
    ("aura-2-callista-en", "Callista", "en", "female"),
    ("aura-2-cora-en", "Cora", "en", "female"),
    ("aura-2-cordelia-en", "Cordelia", "en", "female"),
    ("aura-2-delia-en", "Delia", "en", "female"),
    ("aura-2-draco-en", "Draco", "en", "male"),
    ("aura-2-electra-en", "Electra", "en", "female"),
    ("aura-2-harmonia-en", "Harmonia", "en", "female"),
    ("aura-2-helena-en", "Helena", "en", "female"),
    ("aura-2-hera-en", "Hera", "en", "female"),
    ("aura-2-hermes-en", "Hermes", "en", "male"),
    ("aura-2-hyperion-en", "Hyperion", "en", "male"),
    ("aura-2-iris-en", "Iris", "en", "female"),
    ("aura-2-janus-en", "Janus", "en", "female"),
    ("aura-2-juno-en", "Juno", "en", "female"),
    ("aura-2-jupiter-en", "Jupiter", "en", "male"),
    ("aura-2-luna-en", "Luna", "en", "female"),
    ("aura-2-mars-en", "Mars", "en", "male"),
    ("aura-2-minerva-en", "Minerva", "en", "female"),
    ("aura-2-neptune-en", "Neptune", "en", "male"),
    ("aura-2-odysseus-en", "Odysseus", "en", "male"),
    ("aura-2-ophelia-en", "Ophelia", "en", "female"),
    ("aura-2-orion-en", "Orion", "en", "male"),
    ("aura-2-orpheus-en", "Orpheus", "en", "male"),
    ("aura-2-pandora-en", "Pandora", "en", "female"),
    ("aura-2-phoebe-en", "Phoebe", "en", "female"),
    ("aura-2-pluto-en", "Pluto", "en", "male"),
    ("aura-2-saturn-en", "Saturn", "en", "male"),
    ("aura-2-selene-en", "Selene", "en", "female"),
    ("aura-2-thalia-en", "Thalia", "en", "female"),
    ("aura-2-theia-en", "Theia", "en", "female"),
    ("aura-2-vesta-en", "Vesta", "en", "female"),
    ("aura-2-zeus-en", "Zeus", "en", "male"),
    # Spanish (all available)
    ("aura-2-sirio-es", "Sirio", "es", "male"),
    ("aura-2-nestor-es", "Nestor", "es", "male"),
    ("aura-2-carina-es", "Carina", "es", "female"),
    ("aura-2-celeste-es", "Celeste", "es", "female"),
    ("aura-2-alvaro-es", "Alvaro", "es", "male"),
    ("aura-2-diana-es", "Diana", "es", "female"),
    ("aura-2-aquila-es", "Aquila", "es", "male"),
    ("aura-2-selena-es", "Selena", "es", "female"),
    ("aura-2-estrella-es", "Estrella", "es", "female"),
    ("aura-2-javier-es", "Javier", "es", "male"),
    ("aura-2-agustina-es", "Agustina", "es", "female"),
    ("aura-2-antonia-es", "Antonia", "es", "female"),
    ("aura-2-gloria-es", "Gloria", "es", "female"),
    ("aura-2-luciano-es", "Luciano", "es", "male"),
    ("aura-2-olivia-es", "Olivia", "es", "female"),
    ("aura-2-silvia-es", "Silvia", "es", "female"),
    ("aura-2-valerio-es", "Valerio", "es", "male"),
    # Dutch (all available)
    ("aura-2-beatrix-nl", "Beatrix", "nl", "female"),
    ("aura-2-daphne-nl", "Daphne", "nl", "female"),
    ("aura-2-cornelia-nl", "Cornelia", "nl", "female"),
    ("aura-2-sander-nl", "Sander", "nl", "male"),
    ("aura-2-hestia-nl", "Hestia", "nl", "female"),
    ("aura-2-lars-nl", "Lars", "nl", "male"),
    ("aura-2-roman-nl", "Roman", "nl", "male"),
    ("aura-2-rhea-nl", "Rhea", "nl", "female"),
    ("aura-2-leda-nl", "Leda", "nl", "female"),
    # French (all available)
    ("aura-2-agathe-fr", "Agathe", "fr", "female"),
    ("aura-2-hector-fr", "Hector", "fr", "male"),
    # German (all available)
    ("aura-2-elara-de", "Elara", "de", "female"),
    ("aura-2-aurelia-de", "Aurelia", "de", "female"),
    ("aura-2-lara-de", "Lara", "de", "female"),
    ("aura-2-julius-de", "Julius", "de", "male"),
    ("aura-2-fabian-de", "Fabian", "de", "male"),
    ("aura-2-kara-de", "Kara", "de", "female"),
    ("aura-2-viktoria-de", "Viktoria", "de", "female"),
    # Italian (all available)
    ("aura-2-melia-it", "Melia", "it", "female"),
    ("aura-2-elio-it", "Elio", "it", "male"),
    ("aura-2-flavio-it", "Flavio", "it", "male"),
    ("aura-2-maia-it", "Maia", "it", "female"),
    ("aura-2-cinzia-it", "Cinzia", "it", "female"),
    ("aura-2-cesare-it", "Cesare", "it", "male"),
    ("aura-2-livia-it", "Livia", "it", "female"),
    ("aura-2-perseo-it", "Perseo", "it", "male"),
    ("aura-2-dionisio-it", "Dionisio", "it", "male"),
    ("aura-2-demetra-it", "Demetra", "it", "female"),
    # Japanese (all available)
    ("aura-2-uzume-ja", "Uzume", "ja", "female"),
    ("aura-2-ebisu-ja", "Ebisu", "ja", "male"),
    ("aura-2-fujin-ja", "Fujin", "ja", "male"),
    ("aura-2-izanami-ja", "Izanami", "ja", "female"),
    ("aura-2-ama-ja", "Ama", "ja", "female"),
]

_DEEPGRAM_LANGUAGE_LABELS = {
    "en": "English",
    "es": "Spanish",
    "nl": "Dutch",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "ja": "Japanese",
}


def _deepgram_accent_color(gender: Optional[str]) -> str:
    if gender == "female":
        return "#db2777"
    if gender == "male":
        return "#1d4ed8"
    return "#64748b"


DEEPGRAM_AURA2_VOICES = [
    VoiceInfo(
        id=voice_id,
        name=name,
        language=language_code,
        description=f"Deepgram Aura-2 {(_DEEPGRAM_LANGUAGE_LABELS.get(language_code, language_code)).title()} voice.",
        gender=gender,
        accent="Global",
        accent_color=_deepgram_accent_color(gender),
        tags=["aura-2", language_code],
        provider="deepgram",
    )
    for voice_id, name, language_code, gender in _DEEPGRAM_AURA2_VOICE_SPECS
]
