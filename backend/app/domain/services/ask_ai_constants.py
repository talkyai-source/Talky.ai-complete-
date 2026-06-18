"""
Ask AI pure constants — no app imports.

Kept in a separate module so voice_pipeline_service.py can import these
without pulling in ask_ai_session_config, which imports VoiceSessionConfig
from voice_orchestrator, which in turn imports voice_pipeline_service —
a circular dependency that crashes the server at startup.
"""

# Full product knowledge, injected only when the user asks about the product,
# pricing, or features (keyword-gated, see PRODUCT_KEYWORDS). Tessa paraphrases
# it naturally for the phone — never reads it like a list.
TALKY_PRODUCT_INFO = (
    "Talk-Lee is an AI voice-calling platform for businesses: natural-sounding "
    "AI agents that make and answer phone calls — for outbound sales, lead "
    "qualification, appointment booking, customer support, reception, and "
    "follow-up calls.\n"
    "How it works: you create a campaign, upload your own business knowledge, "
    "pick a voice, and add contacts (type them, upload a CSV, or paste a list). "
    "The agents then call or pick up, talk naturally, answer from your "
    "knowledge, capture leads, and book follow-ups.\n"
    "Key features: lifelike voices with different accents plus custom voice "
    "cloning; a per-campaign knowledge base so agents answer accurately from "
    "your own info; smart dialing with timezone-aware calling windows, automatic "
    "retries, and do-not-call / opt-out compliance; and a dashboard with live "
    "call status, recordings, full transcripts, lead tracking, and analytics. "
    "Bring your own phone number; Enterprise adds API access.\n"
    "Plans: Basic $29/month (300 minutes, 1 agent); Professional $79/month "
    "(1,500 minutes, 3 agents, custom voices — most popular); Enterprise "
    "$199/month (5,000 minutes, 10 agents, API access, full suite)."
)

# Keywords that signal the user is asking about product/pricing.
PRODUCT_KEYWORDS: frozenset[str] = frozenset({
    "price", "pricing", "cost", "plan", "plans", "package", "packages",
    "subscription", "tier", "basic", "professional", "enterprise",
    "minute", "minutes", "agent", "agents", "feature", "features",
    "how much", "what does", "what can", "what do you", "tell me about",
    "crm", "integration", "api", "analytics",
})
