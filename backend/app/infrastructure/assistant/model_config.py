"""Per-tenant assistant model selection (Groq). Default + allowed-list + read helper."""
from __future__ import annotations
from typing import Any
from app.domain.models.ai_config import GROQ_MODELS

DEFAULT_ASSISTANT_MODEL = "llama-3.3-70b-versatile"
ALLOWED_ASSISTANT_MODEL_IDS = {m.id for m in GROQ_MODELS}


def available_models() -> list[dict]:
    """The selectable models for the dashboard assistant (id + display name)."""
    return [{"id": m.id, "name": m.name} for m in GROQ_MODELS]


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
