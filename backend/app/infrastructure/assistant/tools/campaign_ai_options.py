"""
AI-options tools for the assistant agent.

Provides voice/provider editing per campaign (apply_campaign_voice).

NOTE — set_ai_model is intentionally NOT implemented here.

Investigation finding (2026-06-05):
  backend/app/domain/services/global_ai_config.py stores the LLM model in a
  module-level process-global singleton (`_global_config`).  The
  `set_global_config()` function at line 59 of that module writes to
  `global _global_config`, which is shared across ALL tenants served by the
  same uvicorn worker process.  The POST /config endpoint (config.py line 319)
  also calls `set_global_config(config)` immediately after persisting to the
  tenant DB row, meaning the last-writer-wins in memory for every tenant's
  calls.

  A tenant-scoped assistant tool calling set_global_config() would silently
  override every other tenant's active voice-pipeline LLM model.  This is
  unsafe.  The LLM model tool will remain unimplemented until a per-tenant
  in-memory config store (e.g. tenant_id keyed dict or per-request fetch from
  DB) replaces the singleton — at that point the tool can persist directly via
  _upsert_tenant_config and skip set_global_config entirely.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


async def apply_campaign_voice(
    tenant_id: str,
    db_client,
    campaign_ids: List[str],
    tts_provider: str,
    voice_id: str,
    confirm: bool = False,
) -> Dict[str, Any]:
    """
    Preview or apply a TTS provider + voice change across one or more campaigns.

    Validates voice_id against the provider's live catalog before writing
    anything.  Mirrors the apply_tts_config endpoint (campaigns.py:301).

    confirm=False → return preview diff (before/after per campaign) without
                    writing.
    confirm=True  → update each campaign's tts_provider + voice_id, scoped to
                    tenant.

    Returns {"error": ...} without writing if:
      - voice_id is not valid for the given provider
      - a campaign_id is not found under this tenant
    """
    try:
        # Validate voice against provider using the campaigns.py helper
        from app.api.v1.endpoints.campaigns import _valid_voice_ids_for_provider

        valid: set[str] = await _valid_voice_ids_for_provider(tts_provider)
        if voice_id not in valid:
            return {
                "error": (
                    f"Voice '{voice_id}' is not valid for provider '{tts_provider}'. "
                    f"Obtain valid voice IDs via the AI Options voice catalog."
                )
            }

        if not campaign_ids:
            return {"error": "campaign_ids must be a non-empty list."}

        # Fetch current state for each campaign (scoped to tenant)
        previews: List[Dict[str, Any]] = []
        for cid in campaign_ids:
            resp = (
                db_client.table("campaigns")
                .select("id,name,tts_provider,voice_id")
                .eq("id", cid)
                .eq("tenant_id", tenant_id)
                .execute()
            )
            if not resp.data:
                return {"error": f"Campaign '{cid}' not found for this tenant."}

            row = resp.data[0]
            previews.append(
                {
                    "campaign_id": cid,
                    "name": row.get("name"),
                    "changes": [
                        {
                            "field": "tts_provider",
                            "before": row.get("tts_provider"),
                            "after": tts_provider,
                        },
                        {
                            "field": "voice_id",
                            "before": row.get("voice_id"),
                            "after": voice_id,
                        },
                    ],
                }
            )

        if not confirm:
            return {
                "preview": True,
                "campaigns": previews,
                "note": "Not applied yet. Call again with confirm=true to apply.",
            }

        # Apply — mirror campaigns.py apply_tts_config behaviour
        updated: List[str] = []
        for cid in campaign_ids:
            try:
                db_client.table("campaigns").update(
                    {"tts_provider": tts_provider, "voice_id": voice_id}
                ).eq("id", cid).eq("tenant_id", tenant_id).execute()
                updated.append(cid)
            except Exception as exc:
                logger.warning(
                    "apply_campaign_voice failed for campaign=%s: %s", cid, exc
                )

        return {
            "applied": True,
            "updated_campaign_ids": updated,
            "tts_provider": tts_provider,
            "voice_id": voice_id,
        }

    except Exception as exc:
        logger.error("apply_campaign_voice error: %s", exc)
        return {"error": str(exc)}
