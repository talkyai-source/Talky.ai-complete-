"""
Lead query tools for the assistant agent.
"""
import json
import logging
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
from app.core.postgres_adapter import Client

logger = logging.getLogger(__name__)

# Columns returned for a lead in list views.
_LEAD_LIST_COLS = (
    "id, phone_number, first_name, last_name, email, status, priority, "
    "call_attempts, last_call_result, is_lead, follow_up_note, qualified_at"
)


def _full_name(r: Dict[str, Any]) -> Optional[str]:
    n = f"{r.get('first_name') or ''} {r.get('last_name') or ''}".strip()
    return n or None


class GetLeadsInput(BaseModel):
    """Input for get_leads tool"""
    campaign_id: Optional[str] = Field(None, description="Filter by campaign ID")
    status: Optional[str] = Field(None, description="Filter by status (pending, completed, failed)")
    only_leads: bool = Field(
        False,
        description="Only return contacts flagged as qualified leads (is_lead=true)",
    )
    limit: int = Field(25, description="Maximum number of leads to return (max 100)")


async def get_leads(
    tenant_id: str,
    db_client: Client,
    campaign_id: Optional[str] = None,
    status: Optional[str] = None,
    only_leads: bool = False,
    limit: int = 25,
) -> Dict[str, Any]:
    """
    Get leads for the tenant with optional filters. Excludes soft-deleted
    contacts. Each lead includes is_lead / follow_up_note / qualified_at so the
    assistant can talk about which contacts are qualified leads and what the
    follow-up is.
    """
    try:
        capped = max(1, min(int(limit or 25), 100))
        query = db_client.table("leads").select(
            _LEAD_LIST_COLS, count="exact"
        ).eq("tenant_id", tenant_id).neq("status", "deleted")

        if campaign_id:
            query = query.eq("campaign_id", campaign_id)
        if status:
            query = query.eq("status", status)
        if only_leads:
            query = query.eq("is_lead", True)

        response = query.order("created_at", desc=True).limit(capped).execute()

        return {
            "total_count": response.count,
            "returned_count": len(response.data),
            "leads": response.data,
        }
    except Exception as e:
        logger.error(f"Error getting leads: {e}")
        return {"error": str(e)}


class GetLeadFollowupInput(BaseModel):
    """Input for get_lead_followup tool. Provide ONE of the identifiers."""
    lead_id: Optional[str] = Field(None, description="Lead/contact id")
    phone_number: Optional[str] = Field(None, description="Lead phone number (exact or partial)")
    name: Optional[str] = Field(None, description="Lead name (first or last, partial match)")


async def _resolve_leads_by_name(
    tenant_id: str, db_client: Client, name: str
) -> List[Dict[str, Any]]:
    """Match a name against first_name OR last_name (the adapter has no or_)."""
    by_id: Dict[str, Dict[str, Any]] = {}
    for col in ("first_name", "last_name"):
        try:
            r = (
                db_client.table("leads")
                .select(_LEAD_LIST_COLS + ", qualified_call_id")
                .eq("tenant_id", tenant_id)
                .neq("status", "deleted")
                .ilike(col, f"%{name}%")
                .limit(10)
                .execute()
            )
            for row in (r.data or []):
                by_id[row["id"]] = row
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_lead_followup name match failed on %s: %s", col, exc)
    return list(by_id.values())


async def get_lead_followup(
    tenant_id: str,
    db_client: Client,
    lead_id: Optional[str] = None,
    phone_number: Optional[str] = None,
    name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Return follow-up info for ONE lead: is_lead, follow_up_note, qualified_at,
    plus the qualified call's summary (headline, outcome, next_step,
    follow_up_tips, action_items). Resolve by lead_id, phone_number, or name.

    If more than one contact matches, returns {"ambiguous": True, candidates:[...]}
    so the assistant can ask which one.
    """
    try:
        select_cols = _LEAD_LIST_COLS + ", qualified_call_id"
        if lead_id:
            resp = (
                db_client.table("leads").select(select_cols)
                .eq("tenant_id", tenant_id).eq("id", lead_id).execute()
            )
            rows = resp.data or []
        elif phone_number:
            resp = (
                db_client.table("leads").select(select_cols)
                .eq("tenant_id", tenant_id).neq("status", "deleted")
                .ilike("phone_number", f"%{phone_number}%").limit(10).execute()
            )
            rows = resp.data or []
        elif name:
            rows = await _resolve_leads_by_name(tenant_id, db_client, name)
        else:
            return {"error": "Provide lead_id, phone_number, or name."}

        if not rows:
            return {"error": "No matching contact found."}
        if len(rows) > 1:
            return {
                "ambiguous": True,
                "candidates": [
                    {
                        "id": r["id"],
                        "name": _full_name(r),
                        "phone_number": r.get("phone_number"),
                        "is_lead": r.get("is_lead", False),
                    }
                    for r in rows[:10]
                ],
                "hint": "Multiple contacts matched — ask the user which one and call again with that lead_id.",
            }

        lead = rows[0]
        result: Dict[str, Any] = {
            "lead": {
                "id": lead["id"],
                "name": _full_name(lead),
                "phone_number": lead.get("phone_number"),
                "email": lead.get("email"),
                "is_lead": lead.get("is_lead", False),
                "follow_up_note": lead.get("follow_up_note"),
                "qualified_at": lead.get("qualified_at"),
                "last_call_result": lead.get("last_call_result"),
            }
        }

        call_id = lead.get("qualified_call_id")
        if call_id:
            try:
                cresp = (
                    db_client.table("calls").select("summary_json")
                    .eq("id", call_id).eq("tenant_id", tenant_id).execute()
                )
                crows = cresp.data or []
                sj = crows[0].get("summary_json") if crows else None
                if isinstance(sj, str):
                    try:
                        sj = json.loads(sj)
                    except json.JSONDecodeError:
                        sj = None
                if isinstance(sj, dict):
                    result["call_summary"] = {
                        "headline": sj.get("headline"),
                        "outcome": sj.get("outcome"),
                        "what_happened": sj.get("what_happened"),
                        "next_step": sj.get("next_step"),
                        "follow_up_tips": sj.get("follow_up_tips") or [],
                        "action_items": sj.get("action_items") or [],
                    }
            except Exception as ce:  # noqa: BLE001
                logger.warning("get_lead_followup summary fetch failed: %s", ce)

        if not lead.get("is_lead"):
            result["note"] = (
                "This contact is not flagged as a qualified lead yet, so there is "
                "no AI follow-up note. Showing the latest known state."
            )
        return result
    except Exception as e:
        logger.error(f"get_lead_followup error: {e}")
        return {"error": str(e)}
