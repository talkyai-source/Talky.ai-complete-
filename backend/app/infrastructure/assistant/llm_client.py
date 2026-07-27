"""Provider-aware client factory for the dashboard assistant.

The assistant used to hardcode ``AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))``
in two places. That tied the assistant to one vendor and, more urgently, to one
vendor's rate limit: Groq caps the free tier at 8K tokens/minute per
organisation while a single assistant turn is ~8.7K tokens, so the request
could never fit — it failed 100% of the time regardless of traffic.

Both Groq and Cerebras expose an OpenAI-shaped ``chat.completions.create``, so
the only real differences are the client class and a few request parameters.
This module isolates exactly that, returning:

    (client, adapt)

where ``adapt`` rewrites a completion-args dict for the chosen provider. The
call sites keep building one dict the way they always did.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Callable, Dict, Tuple

from app.domain.models.ai_config import (
    CEREBRAS_MIN_REASONING_EFFORT,
    CEREBRAS_MODELS,
    CEREBRAS_REASONING_NONE_SUPPORTED,
    CerebrasModel,
)

logger = logging.getLogger(__name__)

_CEREBRAS_MODEL_IDS = {m.id for m in CEREBRAS_MODELS}


def provider_for_model(model_id: str | None) -> str:
    """Which vendor serves this model id. Defaults to groq — the historical
    behaviour, and what every existing stored assistant model id implies."""
    return "cerebras" if (model_id or "") in _CEREBRAS_MODEL_IDS else "groq"


def _identity(args: Dict[str, Any]) -> Dict[str, Any]:
    return args


def _adapt_for_cerebras(args: Dict[str, Any]) -> Dict[str, Any]:
    """Translate OpenAI/Groq-shaped completion args to Cerebras.

    Two real differences, both of which are errors rather than no-ops if
    ignored:

      * Cerebras takes ``max_completion_tokens``; ``max_tokens`` is the older
        OpenAI spelling.
      * ``reasoning_effort`` is model-specific. Sending "none" to a model that
        does not accept it is rejected, so we only send what each model allows —
        "none" where thinking can be switched off, otherwise the lowest value
        that model does accept.
    """
    out = dict(args)

    if "max_tokens" in out:
        out["max_completion_tokens"] = out.pop("max_tokens")

    model = out.get("model", "")
    if model in CEREBRAS_REASONING_NONE_SUPPORTED:
        out["reasoning_effort"] = "none"
    elif model in CEREBRAS_MIN_REASONING_EFFORT:
        out["reasoning_effort"] = CEREBRAS_MIN_REASONING_EFFORT[model]

    # zai-glm-4.7 only: without this the model's own thinking from previous
    # turns is replayed into the prompt, so context grows every turn.
    if model == CerebrasModel.ZAI_GLM_4_7.value:
        out["clear_thinking"] = True

    return out


def get_assistant_client(
    model_id: str | None,
) -> Tuple[Any, Callable[[Dict[str, Any]], Dict[str, Any]]]:
    """Return an async chat client for ``model_id`` and its args adapter.

    Raises ValueError when the selected provider has no API key configured,
    rather than constructing a client that will fail later with a confusing
    upstream auth error.
    """
    provider = provider_for_model(model_id)

    if provider == "cerebras":
        from cerebras.cloud.sdk import AsyncCerebras

        api_key = os.getenv("CEREBRAS_API_KEY")
        if not api_key:
            raise ValueError(
                "CEREBRAS_API_KEY is not set but a Cerebras model "
                f"({model_id}) was selected."
            )
        return AsyncCerebras(api_key=api_key), _adapt_for_cerebras

    from groq import AsyncGroq

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY is not set.")
    return AsyncGroq(api_key=api_key), _identity
