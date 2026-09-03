"""
Campaign admin tools for the assistant agent.

Provides read access (campaign detail, knowledge tree, live RAG retrieval)
and edit-with-confirm access (campaign config, knowledge nodes, lead management).

All tools follow the standard signature: async def tool(tenant_id, db_client, ...).
Edit tools implement the confirm pattern: if confirm=False, return a preview diff
without writing; if confirm=True, apply and return applied=True with the diff.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.db_utils import acquire_with_tenant
from app.domain.services.campaign_knowledge_access import (
    CampaignKnowledgeAccessBusy,
    CampaignKnowledgeAccessDenied,
    CampaignKnowledgeAccessError,
    CampaignKnowledgeAccessUnavailable,
    CampaignKnowledgeNotFound,
    campaign_knowledge_access_lease,
)
from app.domain.services.phone_number_normalizer import normalize_phone_number
from app.infrastructure.assistant.tools.campaign_direction import (
    inbound_campaign_refusal,
    outbound_campaign_refusal,
)
from app.services.scripts.knowledge.retrieval import (
    retrieve_knowledge as retrieve_knowledge_fn,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_ALLOWED_SCRIPT_CONFIG_KEYS = {
    "persona_type",
    "company_name",
    "agent_names",
    "additional_instructions",
    "knowledge_driven",
}


def _lead_write_conflict(response) -> Dict[str, Any] | None:
    """Turn PostgREST errors/zero-row writes into an honest tool result."""
    if not getattr(response, "error", None) and getattr(response, "data", None):
        return None
    return {
        "applied": False,
        "error": "lead_write_conflict",
        "message": (
            "The lead or campaign changed before the write completed. "
            "Preview the change again."
        ),
    }


_ALLOWED_TOP_LEVEL_KEYS = {"name", "goal"}

# All keys the owner may change via this tool (union of both sets)
_ALL_ALLOWED_KEYS = _ALLOWED_SCRIPT_CONFIG_KEYS | _ALLOWED_TOP_LEVEL_KEYS

_ALLOWED_NODE_KEYS = {
    "heading",
    "content",
    "enabled",
    "priority",
    "summary",
    "voice_answer",
}


def _knowledge_access_failure(
    exc: CampaignKnowledgeAccessError,
    *,
    mutation: bool = False,
) -> Dict[str, Any]:
    """Return a stable model-facing error without leaking DB internals."""

    result: Dict[str, Any] = {"error": exc.code}
    if mutation:
        result["applied"] = False
    if isinstance(exc, CampaignKnowledgeNotFound):
        result["error"] = "campaign not found"
        return result
    if isinstance(exc, CampaignKnowledgeAccessDenied):
        if exc.required_permission:
            result["required"] = exc.required_permission
        result["message"] = (
            "You no longer have permission to access this campaign knowledge."
        )
        return result
    if isinstance(exc, CampaignKnowledgeAccessBusy):
        result["message"] = "Campaign knowledge is busy. Please try again."
        return result
    if isinstance(exc, CampaignKnowledgeAccessUnavailable):
        result["message"] = (
            "Authorization is temporarily unavailable. Please try again."
        )
    return result


async def _verify_campaign_owned(
    db_client, tenant_id: str, campaign_id: str
) -> bool:
    """Return True if campaign exists and belongs to tenant; False otherwise."""
    resp = (
        db_client.table("campaigns")
        .select("id")
        .eq("id", campaign_id)
        .eq("tenant_id", tenant_id)
        .execute()
    )
    return bool(resp.data)


def _build_diff(
    before: Dict[str, Any], after: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Return a list of {field, before, after} for keys that differ."""
    changes = []
    for key in after:
        b = before.get(key)
        a = after[key]
        if b != a:
            changes.append({"field": key, "before": b, "after": a})
    return changes


# ---------------------------------------------------------------------------
# READ TOOLS
# ---------------------------------------------------------------------------


async def get_campaign_detail(
    tenant_id: str,
    db_client,
    campaign_id: Optional[str] = None,
    name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Fetch detail for one campaign scoped to the tenant.
    Resolve by campaign_id or name (campaign_id takes precedence).
    Returns {"campaign": {...}} or {"error": "campaign not found"}.
    """
    try:
        query = db_client.table("campaigns").select(
            "id,name,status,direction,voice_id,tts_provider,knowledge_mode,"
            "knowledge_model,goal,script_config"
        ).eq("tenant_id", tenant_id)

        if campaign_id:
            query = query.eq("id", campaign_id)
        elif name:
            query = query.eq("name", name)
        else:
            return {"error": "Provide campaign_id or name"}

        resp = query.execute()
        if not resp.data:
            return {"error": "campaign not found"}

        return {"campaign": resp.data[0]}
    except Exception as exc:
        logger.error("get_campaign_detail error: %s", exc)
        return {"error": str(exc)}


async def get_knowledge_tree(
    tenant_id: str,
    db_client,
    campaign_id: str,
    actor_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Return all knowledge nodes for a campaign (ordered by path).
    Verifies campaign ownership before querying nodes.
    """
    try:
        async with campaign_knowledge_access_lease(
            db_client.pool,
            tenant_id=tenant_id,
            campaign_id=campaign_id,
            actor_user_id=actor_user_id or "",
            mutate=False,
        ) as lease:
            rows = await lease.conn.fetch(
                """
                SELECT id, depth, heading, summary, enabled, hit_count
                FROM campaign_knowledge_nodes
                WHERE campaign_id = $1 AND tenant_id = $2
                ORDER BY path
                """,
                lease.campaign_id,
                lease.tenant_id,
            )
        return {"nodes": [dict(row) for row in rows]}
    except CampaignKnowledgeAccessError as exc:
        return _knowledge_access_failure(exc)
    except Exception as exc:  # pragma: no cover - defensive classification
        logger.error("get_knowledge_tree error type=%s", type(exc).__name__)
        return _knowledge_access_failure(CampaignKnowledgeAccessUnavailable())


async def retrieve_knowledge(
    tenant_id: str,
    db_client,
    campaign_id: str,
    query: str,
    actor_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run the live RAG retriever for a query against a campaign's knowledge tree.
    Does NOT bump hit_count (read-only diagnostics).
    Verifies campaign ownership before retrieving.
    """
    try:
        async with campaign_knowledge_access_lease(
            db_client.pool,
            tenant_id=tenant_id,
            campaign_id=campaign_id,
            actor_user_id=actor_user_id or "",
            mutate=False,
        ) as lease:
            hits = await retrieve_knowledge_fn(
                db_client.pool,
                lease.tenant_id,
                lease.campaign_id,
                query,
                k=3,
                bump_hits=False,
                conn=lease.conn,
                raise_on_error=True,
            )
        return {
            "query": query,
            "hits": [
                {
                    "heading": h.get("heading"),
                    "voice_answer": h.get("voice_answer"),
                    "summary": h.get("summary"),
                }
                for h in hits
            ],
        }
    except CampaignKnowledgeAccessError as exc:
        return _knowledge_access_failure(exc)
    except Exception as exc:  # pragma: no cover - defensive classification
        logger.error("retrieve_knowledge tool error type=%s", type(exc).__name__)
        return _knowledge_access_failure(CampaignKnowledgeAccessUnavailable())


# ---------------------------------------------------------------------------
# EDIT TOOLS (confirm pattern)
# ---------------------------------------------------------------------------


async def update_campaign_config(
    tenant_id: str,
    db_client,
    campaign_id: str,
    changes: Dict[str, Any],
    confirm: bool = False,
) -> Dict[str, Any]:
    """
    Preview or apply changes to a campaign's config.

    Allowed keys:
      script_config sub-keys: persona_type, company_name, agent_names,
                               additional_instructions, knowledge_driven
      top-level columns:      name, goal

    voice_id / tts_provider are silently dropped with a note (they need
    provider validation handled by the AI Options flow).

    confirm=False → return preview diff without writing.
    confirm=True  → apply and return applied=True with diff.
    """
    try:
        # Fetch current campaign scoped to tenant
        resp = (
            db_client.table("campaigns")
            .select("id,name,goal,script_config,direction")
            .eq("id", campaign_id)
            .eq("tenant_id", tenant_id)
            .execute()
        )
        if not resp.data:
            return {"error": "campaign not found"}

        current = resp.data[0]
        refusal = outbound_campaign_refusal([current])
        if refusal:
            return refusal
        current_sc = current.get("script_config") or {}

        # Split incoming changes by destination
        voice_keys_dropped = [
            k for k in changes if k in {"voice_id", "tts_provider"}
        ]
        filtered = {
            k: v
            for k, v in changes.items()
            if k in _ALL_ALLOWED_KEYS
        }

        top_level_changes = {
            k: v for k, v in filtered.items() if k in _ALLOWED_TOP_LEVEL_KEYS
        }
        sc_changes = {
            k: v for k, v in filtered.items() if k in _ALLOWED_SCRIPT_CONFIG_KEYS
        }

        # Sanitize operator-supplied script_config fields exactly like the HTTP
        # path (campaign_prompt_service) — the assistant-tool path previously wrote
        # them raw, bypassing sanitize_tenant_text/too_long/scan hardening (re-audit MED).
        if sc_changes:
            from app.services.scripts.prompts.prompt_safety import (
                sanitize_tenant_text as _san,
                MAX_COMPANY_NAME as _MC,
                MAX_AGENT_NAME as _MA,
            )
            from app.services.scripts.prompts.guardrails import (
                scan_instruction_conflicts as _scan,
            )
            if sc_changes.get("company_name") is not None:
                sc_changes["company_name"] = _san(str(sc_changes["company_name"]), max_len=_MC)
            if isinstance(sc_changes.get("agent_names"), list):
                sc_changes["agent_names"] = [
                    _san(str(n), max_len=_MA)
                    for n in sc_changes["agent_names"]
                    if n and str(n).strip()
                ]
            if sc_changes.get("additional_instructions") is not None:
                _clean = _san(str(sc_changes["additional_instructions"]))  # uncapped, like composer
                sc_changes["additional_instructions"] = _clean
                for _w in _scan(_clean):
                    logger.warning("update_campaign_config instruction conflict: %s", _w)

        # Build diff for preview / record
        diff_entries: List[Dict[str, Any]] = []

        for key, new_val in top_level_changes.items():
            old_val = current.get(key)
            if old_val != new_val:
                diff_entries.append({"field": key, "before": old_val, "after": new_val})

        for key, new_val in sc_changes.items():
            old_val = current_sc.get(key)
            if old_val != new_val:
                diff_entries.append(
                    {"field": f"script_config.{key}", "before": old_val, "after": new_val}
                )

        notes = []
        if voice_keys_dropped:
            notes.append(
                f"voice/provider changes must use the AI Options flow "
                f"(dropped: {', '.join(sorted(voice_keys_dropped))})"
            )
        if not diff_entries and not notes:
            return {"preview": True, "changes": [], "note": "No recognised changes."}

        if not confirm:
            result: Dict[str, Any] = {
                "preview": True,
                "changes": diff_entries,
                "note": "Not applied yet. Call again with confirm=true to apply.",
            }
            if notes:
                result["warnings"] = notes
            return result

        # Apply ---------------------------------------------------------------
        update_payload: Dict[str, Any] = {**top_level_changes}

        if sc_changes:
            merged_sc = {**current_sc, **sc_changes}
            update_payload["script_config"] = merged_sc

        if update_payload:
            response = (
                db_client.table("campaigns")
                .update(update_payload)
                .eq("id", campaign_id)
                .eq("tenant_id", tenant_id)
                .eq("direction", "outbound")
                .execute()
            )
            if getattr(response, "error", None):
                logger.warning(
                    "update_campaign_config write failed campaign=%s: %s",
                    campaign_id,
                    response.error,
                )
                return {
                    "applied": False,
                    "error": "campaign_update_failed",
                    "message": (
                        "The campaign update could not be confirmed. "
                        "Preview the change again."
                    ),
                }
            if not response.data:
                refusal = inbound_campaign_refusal([campaign_id])
                refusal["applied"] = False
                return refusal

        result = {"applied": True, "changes": diff_entries}
        if notes:
            result["warnings"] = notes
        return result

    except Exception as exc:
        logger.error("update_campaign_config error: %s", exc)
        return {"error": str(exc)}


async def update_knowledge_node(
    tenant_id: str,
    db_client,
    campaign_id: str,
    node_id: str,
    changes: Dict[str, Any],
    confirm: bool = False,
    actor_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Preview or apply changes to a knowledge node.

    Allowed keys: heading, content, enabled, priority, summary, voice_answer.

    When heading or content changes, search_text and search_tsv are recomputed
    using the same logic as the campaign_knowledge PATCH endpoint (FTS stays fresh).

    confirm=False → preview diff without writing.
    confirm=True  → apply via asyncpg (needed for to_tsvector SQL).
    """
    try:
        filtered = {k: v for k, v in changes.items() if k in _ALLOWED_NODE_KEYS}
        if not filtered:
            return {
                "error": (
                    f"No editable fields provided. "
                    f"Allowed: {sorted(_ALLOWED_NODE_KEYS)}"
                )
            }

        applied_result: Optional[Dict[str, Any]] = None
        authorized_campaign_id = str(campaign_id)
        async with campaign_knowledge_access_lease(
            db_client.pool,
            tenant_id=tenant_id,
            campaign_id=campaign_id,
            actor_user_id=actor_user_id or "",
            mutate=True,
        ) as lease:
            authorized_campaign_id = lease.campaign_id
            row = await lease.conn.fetchrow(
                """
                SELECT heading, content, keywords, example_questions,
                       enabled, priority, summary, voice_answer
                FROM campaign_knowledge_nodes
                WHERE id = $1 AND campaign_id = $2 AND tenant_id = $3
                """,
                node_id,
                lease.campaign_id,
                lease.tenant_id,
            )
            if row is None:
                return {"error": "knowledge node not found"}
            current_node = dict(row)

            diff_entries = _build_diff(
                {k: current_node.get(k) for k in filtered},
                filtered,
            )
            if not diff_entries:
                return {
                    "preview": True,
                    "changes": [],
                    "note": "No changes detected.",
                }
            if not confirm:
                return {
                    "preview": True,
                    "changes": diff_entries,
                    "note": "Not applied yet. Call again with confirm=true to apply.",
                }

            # $1/$2/$3 are node, campaign, tenant; edited values begin at $4.
            set_parts = [f"{key} = ${index + 4}" for index, key in enumerate(filtered)]
            params = list(filtered.values())
            if "heading" in filtered or "content" in filtered:
                heading = filtered.get("heading", current_node.get("heading")) or ""
                content = filtered.get("content", current_node.get("content")) or ""
                keywords: List[str] = current_node.get("keywords") or []
                questions: List[str] = current_node.get("example_questions") or []
                search_text = " ".join(
                    part
                    for part in [
                        heading,
                        content,
                        " ".join(keywords),
                        " ".join(questions),
                    ]
                    if part
                ).strip()
                search_index = len(params) + 4
                params.append(search_text)
                set_parts.append(f"search_text = ${search_index}")
                set_parts.append(
                    f"search_tsv = to_tsvector('english', ${search_index})"
                )

            sets = ", ".join(set_parts)
            updated = await lease.conn.fetchval(
                f"UPDATE campaign_knowledge_nodes SET {sets}, updated_at = NOW() "
                "WHERE id = $1 AND campaign_id = $2 AND tenant_id = $3 RETURNING id",
                node_id,
                lease.campaign_id,
                lease.tenant_id,
                *params,
            )
            if not updated:
                return {"error": "knowledge node not found or update failed"}
            applied_result = {"applied": True, "changes": diff_entries}

        # The lease context has committed before cache invalidation. A commit
        # failure therefore never evicts a still-valid cache entry.
        try:
            from app.services.scripts.knowledge import cache as _kb_cache

            _kb_cache.invalidate_campaign(tenant_id, authorized_campaign_id)
        except Exception as exc:
            logger.debug(
                "knowledge cache invalidate failed campaign=%s: %s",
                str(campaign_id)[:12],
                exc,
            )
        return applied_result or {"error": "knowledge node update failed"}

    except CampaignKnowledgeAccessError as exc:
        return _knowledge_access_failure(exc, mutation=True)
    except Exception as exc:  # pragma: no cover - defensive classification
        logger.error("update_knowledge_node error type=%s", type(exc).__name__)
        return _knowledge_access_failure(
            CampaignKnowledgeAccessUnavailable(),
            mutation=True,
        )


async def manage_lead(
    tenant_id: str,
    db_client,
    campaign_id: str,
    action: str,
    name: Optional[str] = None,
    phone_number: Optional[str] = None,
    lead_id: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    email: Optional[str] = None,
    confirm: bool = False,
) -> Dict[str, Any]:
    """
    Add, remove (soft-delete), or update a lead in a campaign (confirm pattern).

    action="add"    — phone_number required; name split first/last on first space.
    action="remove" — lead_id required; sets status="deleted" (soft delete).
    action="update" — lead_id required; any of phone_number/first_name/last_name/email.

    confirm=False → preview diff without writing.
    confirm=True  → apply and return applied=True with diff.
    """
    try:
        if action not in {"add", "remove", "update"}:
            return {"error": f"Unknown action '{action}'. Use 'add', 'remove', or 'update'."}

        campaign_resp = (
            db_client.table("campaigns")
            .select("id,direction")
            .eq("id", campaign_id)
            .eq("tenant_id", tenant_id)
            .execute()
        )
        if not campaign_resp.data:
            return {"error": "campaign not found"}
        refusal = outbound_campaign_refusal([campaign_resp.data[0]])
        if refusal:
            return refusal

        # ---- ADD -------------------------------------------------------------
        if action == "add":
            if not phone_number or not phone_number.strip():
                return {"error": "phone_number is required for action='add'"}
            try:
                normalized_phone = normalize_phone_number(phone_number)
            except ValueError as exc:
                return {"error": str(exc)}

            # name param takes precedence over explicit first_name/last_name
            fn: Optional[str] = first_name
            ln: Optional[str] = last_name
            if name and name.strip():
                parts = name.strip().split(" ", 1)
                fn = parts[0] or None
                ln = parts[1] if len(parts) > 1 else None

            preview_lead = {
                "tenant_id": tenant_id,
                "campaign_id": campaign_id,
                "phone_number": normalized_phone,
                "first_name": fn,
                "last_name": ln,
                "status": "pending",
                "last_call_result": "pending",
                "call_attempts": 0,
            }

            if not confirm:
                return {
                    "preview": True,
                    "changes": [
                        {
                            "field": "lead",
                            "before": None,
                            "after": preview_lead,
                        }
                    ],
                    "note": "Not applied yet. Call again with confirm=true to apply.",
                }

            # Insert
            lead_data = {
                **preview_lead,
                "id": str(uuid.uuid4()),
                "email": None,
                "custom_fields": {},
                "created_at": datetime.utcnow().isoformat(),
            }
            resp = db_client.table("leads").insert(lead_data).execute()
            conflict = _lead_write_conflict(resp)
            if conflict:
                return conflict
            inserted = resp.data[0]
            return {
                "applied": True,
                "changes": [{"field": "lead", "before": None, "after": inserted}],
            }

        # ---- REMOVE ----------------------------------------------------------
        if action == "remove":
            if not lead_id:
                return {"error": "lead_id is required for action='remove'"}

            # Read the lead (scoped to tenant)
            resp = (
                db_client.table("leads")
                .select("id,phone_number,first_name,last_name,status,campaign_id")
                .eq("id", lead_id)
                .eq("tenant_id", tenant_id)
                .eq("campaign_id", campaign_id)
                .execute()
            )
            if not resp.data:
                return {"error": "lead not found"}

            existing_lead = resp.data[0]

            if not confirm:
                return {
                    "preview": True,
                    "changes": [
                        {
                            "field": "lead",
                            "before": existing_lead,
                            "after": {"status": "deleted"},
                        }
                    ],
                    "note": "Not applied yet. Call again with confirm=true to apply.",
                }

            # Soft delete — matches DELETE /campaigns/{id}/contacts/{contact_id}
            write_response = (
                db_client.table("leads")
                .update({"status": "deleted"})
                .eq("id", lead_id)
                .eq("tenant_id", tenant_id)
                .eq("campaign_id", campaign_id)
                .execute()
            )
            conflict = _lead_write_conflict(write_response)
            if conflict:
                return conflict

            return {
                "applied": True,
                "changes": [
                    {"field": "lead", "before": existing_lead, "after": {"status": "deleted"}}
                ],
            }

        # ---- UPDATE ----------------------------------------------------------
        # action == "update"
        if not lead_id:
            return {"error": "lead_id is required for action='update'"}

        # Read current lead (scoped to tenant)
        resp = (
            db_client.table("leads")
            .select("id,phone_number,first_name,last_name,email,status,campaign_id")
            .eq("id", lead_id)
            .eq("tenant_id", tenant_id)
            .eq("campaign_id", campaign_id)
            .execute()
        )
        if not resp.data:
            return {"error": "lead not found"}

        current = resp.data[0]

        # Build set of fields the caller wants to change
        candidate: Dict[str, Any] = {}
        if phone_number is not None:
            try:
                candidate["phone_number"] = normalize_phone_number(phone_number)
            except ValueError as exc:
                return {"error": str(exc)}
        if first_name is not None:
            candidate["first_name"] = first_name
        if last_name is not None:
            candidate["last_name"] = last_name
        if email is not None:
            candidate["email"] = email

        if not candidate:
            return {
                "error": (
                    "No updatable fields provided. "
                    "Supply at least one of: phone_number, first_name, last_name, email."
                )
            }

        diff_entries = _build_diff(
            {k: current.get(k) for k in candidate},
            candidate,
        )

        if not diff_entries:
            return {
                "preview": True,
                "changes": [],
                "note": "No changes detected.",
            }

        if not confirm:
            return {
                "preview": True,
                "changes": diff_entries,
                "note": "Not applied yet. Call again with confirm=true to apply.",
            }

        write_response = (
            db_client.table("leads")
            .update(candidate)
            .eq("id", lead_id)
            .eq("tenant_id", tenant_id)
            .eq("campaign_id", campaign_id)
            .execute()
        )
        conflict = _lead_write_conflict(write_response)
        if conflict:
            return conflict

        return {"applied": True, "changes": diff_entries}

    except Exception as exc:
        logger.error("manage_lead error: %s", exc)
        return {"error": str(exc)}
