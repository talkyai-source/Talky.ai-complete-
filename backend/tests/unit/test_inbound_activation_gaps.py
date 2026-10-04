"""Three platform gaps found by the 2026-09-09 inbound live test (report of 2026-09-10).

1. A tenant could never become 'active' without Stripe: every new tenant sat on
   the default subscription_status 'inactive' (11 of 12 on prod) and the only
   activation path was a Stripe checkout prod does not have. A $0 plan now
   activates server-side.
2. Nothing ever created a tenant concurrency policy, yet readiness and admission
   both require one. The first inbound config now seeds 'inbound-default'.
3. Two inbound calls were fenced with 'lifecycle_handoff_cancelled' and no log
   said which terminal event caused it. The adapter now logs event + cause.
"""
from __future__ import annotations

import logging
import uuid

import pytest

from app.domain.services.inbound_campaign_service import InboundCampaignService
from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter

# Explicit free-price checkout now has transactional coverage in
# tests/integration/test_billing_checkout.py; a legacy plan_id no longer
# bypasses the approved price-option contract.

# ── 2. default concurrency policy ────────────────────────────────────────────

class _PolicyConn:
    def __init__(self, existing_active: bool):
        self.existing_active = existing_active
        self.calls = []

    async def fetchrow(self, sql, *args):
        self.calls.append((" ".join(sql.split()), args))
        return None if self.existing_active else {"id": uuid.uuid4()}


@pytest.mark.asyncio
async def test_first_inbound_config_seeds_an_active_default_policy():
    conn = _PolicyConn(existing_active=False)
    tenant, actor = uuid.uuid4(), uuid.uuid4()
    created = await InboundCampaignService(None)._ensure_default_concurrency_policy(conn, tenant_id=tenant, actor_id=actor)
    assert created is True
    (sql, args), = conn.calls
    assert sql.startswith("INSERT INTO tenant_telephony_concurrency_policies")
    assert "WHERE NOT EXISTS" in sql and "is_active = TRUE" in sql
    assert "GREATEST(1, COALESCE((SELECT p.concurrent_calls" in sql, "limit follows the plan entitlement, floor 1"
    assert args == (tenant, "inbound-default", actor)


@pytest.mark.asyncio
async def test_existing_active_policy_is_left_alone():
    conn = _PolicyConn(existing_active=True)
    created = await InboundCampaignService(None)._ensure_default_concurrency_policy(conn, tenant_id=uuid.uuid4(), actor_id=uuid.uuid4())
    assert created is False


# ── 3. the fence names its cause ─────────────────────────────────────────────

def _adapter():
    a = AsteriskAdapter.__new__(AsteriskAdapter)
    a._inbound_setup_tasks = {}
    a._inbound_handoff_tasks = {}
    a._inbound_handoff_accepted = set()
    a._preanswer_hangup_tasks = {}
    a._inbound_admissions = {}
    a._active_sessions = {}
    a._inbound_cleanup_pending = set()
    a._hangup_causes = {"chan-1": "Normal Clearing"}
    return a


@pytest.mark.asyncio
async def test_setup_terminal_logs_event_and_cause(caplog):
    a = _adapter()
    with caplog.at_level(logging.WARNING, logger="app.infrastructure.telephony.asterisk_adapter"):
        await a._cancel_inbound_setup_for_terminal("chan-1", reason="StasisEnd:external_media_leg")
    line = next(r.getMessage() for r in caplog.records if "inbound_setup_terminal" in r.getMessage())
    assert "event=StasisEnd:external_media_leg" in line
    assert "cause=Normal Clearing" in line
    assert "handoff_pending=False" in line and "setup_inflight=False" in line
