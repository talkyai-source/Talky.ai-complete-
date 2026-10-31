"""Per-tenant assistant model selection. Default + allowed-list + read helper.

Groq and Cerebras are both offered. Cerebras models appear only when
CEREBRAS_API_KEY is configured — listing a model whose key is missing would
let a user select something that then fails at request time.
"""
from __future__ import annotations
import os
from typing import Any
from app.domain.models.ai_config import (
    CEREBRAS_MODELS,
    CEREBRAS_MODELS_HIDDEN,
    GROQ_MODELS,
    GROQ_MODELS_HIDDEN,
)

# Was llama-3.3-70b-versatile until 2026-08-17. That id 404s on this Groq
# account ("does not exist or you do not have access to it"), which made it the
# default for the in-app assistant AND the value `resolve_model` fell back to
# whenever a tenant's stored choice failed validation — so the failure path led
# straight to a dead model.
#
# GPT-OSS rather than Qwen here, deliberately: the June objection to GPT-OSS
# was about CONVERSATIONAL VOICE behaviour (stacking questions, spelling things
# out), none of which applies to a text assistant, and it is the fastest family
# this account serves with prompt caching on top.
#
# Moved 120B -> 20B on 2026-08-24, when the Groq voice menu narrowed to the 20B.
# The default MUST be something `available_models()` actually offers: otherwise
# the dropdown never shows the value in use, and `normalize_model` "corrects"
# unrecognised choices to an id that is itself not selectable. Asserted by
# test_available_models_includes_default so the two cannot drift apart again.
DEFAULT_ASSISTANT_MODEL = "openai/gpt-oss-20b"

# Validation accepts Cerebras ids unconditionally, even when the key is unset.
# Otherwise a tenant who had selected a Cerebras model would silently fall back
# to the default the moment the key was rotated out — a confusing, invisible
# downgrade. get_assistant_client raises a clear error instead.
#
# THE ASSISTANT IS NOT THE VOICE AGENT (2026-08-24). The voice AI-Options menu
# was narrowed to one model per provider for the MVP, and that narrowing must
# not reach in here: this is a TEXT assistant, none of the voice reasons for
# excluding a model (latency tails, email read-back, spelling things out on a
# phone) apply to it, and DEFAULT_ASSISTANT_MODEL is one of the ids the voice
# menu stopped offering. Validating against the voice menu alone would have
# made the assistant's own default fail its own allow-list — `normalize_model`
# would then "correct" every stored choice to a value it also rejects.
#
# So the allow-list is offered + hidden: everything the accounts can actually
# serve.
ALLOWED_ASSISTANT_MODEL_IDS = (
    {m.id for m in GROQ_MODELS}
    | {m.id for m in CEREBRAS_MODELS}
    | set(GROQ_MODELS_HIDDEN)
    | set(CEREBRAS_MODELS_HIDDEN)
)


def available_models() -> list[dict]:
    """The selectable models for the dashboard assistant (id + display name).

    Cerebras entries are gated on the key being present, matching how the
    AI-Options provider list gates Gemini and Cerebras.
    """
    models = [{"id": m.id, "name": m.name} for m in GROQ_MODELS]
    if os.getenv("CEREBRAS_API_KEY"):
        models.extend({"id": m.id, "name": m.name} for m in CEREBRAS_MODELS)
    return models


def normalize_model(model: str | None) -> str:
    """Return a valid assistant model id, falling back to the default."""
    return model if model in ALLOWED_ASSISTANT_MODEL_IDS else DEFAULT_ASSISTANT_MODEL


async def get_tenant_assistant_model(db_client: Any, tenant_id: str) -> str:
    """Read the tenant's chosen assistant model; default if unset OR the column
    doesn't exist yet (deploy-order safe)."""
    try:
        resp = db_client.table("tenants").select("assistant_model").eq("id", tenant_id).execute()
        rows = getattr(resp, "data", None) or []
        chosen = rows[0].get("assistant_model") if rows else None
    except Exception:
        chosen = None
    return normalize_model(chosen)
