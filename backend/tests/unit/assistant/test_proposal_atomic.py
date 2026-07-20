"""Case 4 hardening — atomic proposal consumption.

get_proposal + later clear_proposal left a window where two connections (two
browser tabs) both read the same pending proposal and each fired the INSERT,
double-creating the campaign. pop_proposal consumes exactly once.
"""
from __future__ import annotations

from app.infrastructure.assistant import proposals
from app.infrastructure.assistant.proposals import (
    store_proposal,
    get_proposal,
    pop_proposal,
)


def _store(tenant="t1"):
    p = store_proposal(
        tool="create_campaign",
        args={"name": "AI estimation", "confirm": True},
        result={"campaigns": [{"campaign_id": "new", "changes": []}]},
        tenant_id=tenant,
    )
    return p["proposal_id"]


def setup_function():
    proposals._PENDING.clear()


def test_pop_returns_then_consumes():
    pid = _store()
    first = pop_proposal(pid, "t1")
    assert first is not None and first["tool"] == "create_campaign"
    # Second pop of the SAME id — the double-apply race — gets nothing.
    assert pop_proposal(pid, "t1") is None


def test_pop_strips_confirm_from_stored_args():
    pid = _store()
    p = pop_proposal(pid, "t1")
    assert "confirm" not in p["args"]  # store_proposal drops it; apply re-adds


def test_pop_tenant_mismatch_does_not_consume():
    pid = _store(tenant="owner")
    # A different tenant's pop must fail AND leave the proposal intact.
    assert pop_proposal(pid, "attacker") is None
    assert get_proposal(pid, "owner") is not None  # still there for the owner
    assert pop_proposal(pid, "owner") is not None  # owner can still consume


def test_pop_unknown_id_is_none():
    assert pop_proposal("prop_does_not_exist", "t1") is None
