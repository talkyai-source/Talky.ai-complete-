"""Read-only telephony preflight shared by campaign launch and single-call redial."""
from __future__ import annotations

import json
import os

from app.core.db_utils import acquire_with_tenant
from app.domain.models.calling_rules import CallingRules
from app.domain.services.dialer.campaign_schedule import effective_rules
from app.domain.services.telephony import caller_id_guard, trunk_resolver


def _object(raw) -> dict:
    value = json.loads(raw) if isinstance(raw, str) else raw
    return value if isinstance(value, dict) else {}


def _rules_caller_id(calling_rules, campaign: dict) -> str:
    return effective_rules(CallingRules.from_dict(_object(calling_rules)), _object(campaign.get("calling_config"))).caller_id or os.getenv("DEFAULT_CALLER_ID", "1001")


async def evaluate_outbound_readiness(
    db_pool, *, tenant_id: str, campaign_id: str, campaign: dict,
    calling_rules: dict | str | None = None, environment: str | None = None,
) -> dict:
    """Inspect current route/ownership without warming providers or dialing."""
    result = {
        "campaign_id": str(campaign_id), "ready": True,
        "reason_code": "sip_not_required", "reason": None, "trunk_id": None,
        "caller_id": _rules_caller_id(calling_rules, campaign) if calling_rules is not None else None,
    }
    if not await trunk_resolver.requires_sip_readiness(db_pool, tenant_id=tenant_id, campaign=campaign):
        return result
    environment = environment or os.getenv("ENVIRONMENT", "development")
    route = await trunk_resolver.resolve_outbound_trunk(
        db_pool, tenant_id=tenant_id, environment=environment, campaign_id=campaign_id,
    )
    result.update(trunk_id=route.trunk_id, caller_id=route.caller_id or result["caller_id"])
    if route.refused or not route.endpoint:
        code = route.reason if route.refused else "trunk_endpoint_missing"
        result.update(ready=False, reason_code=code,
                      reason=f"Selected SIP trunk is not ready ({code}). Check its live status in Settings.")
        return result
    if not result["caller_id"]:
        if calling_rules is None:
            async with acquire_with_tenant(db_pool, tenant_id) as conn:
                calling_rules = await conn.fetchval("SELECT calling_rules FROM tenants WHERE id=$1::uuid", tenant_id)
        result["caller_id"] = _rules_caller_id(calling_rules, campaign)
    ownership = await caller_id_guard.check_caller_id_ownership(
        db_pool, tenant_id=tenant_id, caller_id=result["caller_id"], environment=environment,
    )
    if not ownership.allowed:
        result.update(ready=False, reason_code="caller_id_not_verified",
                      reason="Verify the selected trunk's caller ID before dialing.")
    else:
        result["reason_code"] = "ready"
    return result
