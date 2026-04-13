"""
Ask AI pure constants — no app imports.

Kept in a separate module so voice_pipeline_service.py can import these
without pulling in ask_ai_session_config, which imports VoiceSessionConfig
from voice_orchestrator, which in turn imports voice_pipeline_service —
a circular dependency that crashes the server at startup.
"""

# Compressed product info injected only when the user asks about pricing/features.
TALKY_PRODUCT_INFO = (
    "Talky.ai automates business phone calls with natural-sounding AI agents "
    "for outbound calls, lead qualification, and appointment booking. "
    "Plans: Basic $29/month (300 minutes, 1 agent), "
    "Professional $79/month (1,500 minutes, 3 agents, custom voices — most popular), "
    "Enterprise $199/month (5,000 minutes, 10 agents, API access, full suite)."
)

# Keywords that signal the user is asking about product/pricing.
PRODUCT_KEYWORDS: frozenset[str] = frozenset({
    "price", "pricing", "cost", "plan", "plans", "package", "packages",
    "subscription", "tier", "basic", "professional", "enterprise",
    "minute", "minutes", "agent", "agents", "feature", "features",
    "how much", "what does", "what can", "what do you", "tell me about",
    "crm", "integration", "api", "analytics",
})
