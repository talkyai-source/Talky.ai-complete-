"""Packet W5: campaign API + website visibility regressions.

* editing a campaign must not wipe ``calling_config`` (line pin, pacing);
* the phantom "campaign is not running" card must clear once it runs;
* the readiness refusal text must say what is wrong in plain words.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

import app.api.v1.endpoints.calls as calls_module
from app.api.v1.dependencies import CurrentUser
from app.api.v1.endpoints import campaigns
from app.api.v1.endpoints.calls import list_call_issues
from app.api.v1.schemas.campaigns import CampaignUpdateRequest
from app.domain.services.telephony import caller_id_guard, outbound_readiness, trunk_resolver

TENANT = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT = "99999999-9999-9999-9999-999999999999"
CAMPAIGN = "22222222-2222-2222-2222-222222222222"


# ---------------------------------------------------------------------------
# 1. update_campaign must merge calling_config, not replace it
# ---------------------------------------------------------------------------
class _Table:
    """Mimics the adapter: a column named in update() is a FULL SET."""

    def __init__(self, db: "_FakeDB") -> None:
        self.db = db
        self.op = "select"
        self.columns: list[str] = []
        self.payload: dict = {}
        self.filters: list[tuple[str, object]] = []

    def select(self, columns, **_kw):
        self.op = "select"
        self.columns = [c.strip() for c in str(columns).split(",")]
        return self

    def update(self, payload):
        self.op = "update"
        self.payload = payload
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def execute(self):
        rows = [r for r in self.db.rows if all(r.get(c) == v for c, v in self.filters)]
        if self.op == "update":
            for row in rows:
                row.update(self.payload)
            return SimpleNamespace(data=[dict(r) for r in rows], error=None)
        self.db.selects.append((tuple(self.columns), tuple(self.filters)))
        return SimpleNamespace(
            data=[{c: r.get(c) for c in self.columns} for r in rows], error=None
        )


class _FakeDB:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.selects: list[tuple[tuple[str, ...], tuple[tuple[str, object], ...]]] = []
        self.pool = object()

    def table(self, name):
        assert name == "campaigns"
        return _Table(self)


def _update_body(**overrides) -> CampaignUpdateRequest:
    base = dict(
        name="Spring outreach",
        persona_type="lead_gen",
        agent_names=["Sarah"],
        company_name="Acme Ltd",
    )
    base.update(overrides)
    return CampaignUpdateRequest(**base)


def _patch_update_dependencies(monkeypatch) -> None:
    @asynccontextmanager
    async def _acquire(_pool, _tenant):
        yield object()

    monkeypatch.setattr(campaigns, "acquire_with_tenant", _acquire)
    monkeypatch.setattr(campaigns, "_reject_inbound_campaign_mutation", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.api.v1.endpoints.ai_options._fetch_tenant_config",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.api.v1.endpoints.campaign_voice_config.build_campaign_voice_config",
        AsyncMock(return_value=({"persona_type": "lead_gen"}, "voice-1")),
    )


def _campaign_row(**calling_config) -> dict:
    return {
        "id": CAMPAIGN,
        "tenant_id": TENANT,
        "direction": "outbound",
        "script_config": {},
        "calling_config": calling_config,
    }


@pytest.mark.asyncio
async def test_schedule_edit_keeps_trunk_pin_batch_size_and_call_gap(monkeypatch):
    _patch_update_dependencies(monkeypatch)
    pin = {"id": "bb717019-0000-0000-0000-000000000000", "caller_id": "+442046132300"}
    row = _campaign_row(
        trunk=pin,
        batch_size=5,
        call_gap_seconds=30,
        timezone="UTC",
        time_window_start="09:00",
        time_window_end="17:00",
        allowed_days=[0, 1, 2, 3, 4],
        ignore_schedule=False,
    )
    db = _FakeDB([row])

    await campaigns.update_campaign(
        CAMPAIGN,
        _update_body(
            calling_schedule={
                "timezone": "Europe/London",
                "time_window_start": "10:00",
                "time_window_end": "16:00",
                "allowed_days": [0, 4],
                "ignore_schedule": False,
            }
        ),
        SimpleNamespace(),
        CurrentUser(id="u1", email="u@example.test", tenant_id=TENANT, role="owner"),
        db,
    )

    cfg = row["calling_config"]
    assert cfg["trunk"] == pin
    assert cfg["batch_size"] == 5
    assert cfg["call_gap_seconds"] == 30
    # ...while the schedule keys are what the client just sent.
    assert cfg["timezone"] == "Europe/London"
    assert cfg["time_window_start"] == "10:00"
    assert cfg["time_window_end"] == "16:00"
    assert cfg["allowed_days"] == [0, 4]
    # The existing config was read tenant-scoped (prod DB role bypasses RLS).
    reads = [s for s in db.selects if "calling_config" in s[0]]
    assert reads, "update must read the existing calling_config"
    assert ("tenant_id", TENANT) in reads[0][1]


@pytest.mark.asyncio
async def test_schedule_edit_drops_cleared_schedule_keys_only(monkeypatch):
    """A schedule key the client no longer sends means 'use the tenant
    default' (the old full-SET behaviour) — it must not linger."""
    _patch_update_dependencies(monkeypatch)
    row = _campaign_row(
        trunk={"id": "t"}, batch_size=2,
        timezone="UTC", time_window_start="09:00", allowed_days=[1],
    )
    db = _FakeDB([row])

    await campaigns.update_campaign(
        CAMPAIGN,
        _update_body(calling_schedule={"time_window_end": "18:00"}),
        SimpleNamespace(),
        CurrentUser(id="u1", email="u@example.test", tenant_id=TENANT, role="owner"),
        db,
    )

    cfg = row["calling_config"]
    assert cfg["trunk"] == {"id": "t"} and cfg["batch_size"] == 2
    assert cfg["time_window_end"] == "18:00"
    assert "timezone" not in cfg and "time_window_start" not in cfg and "allowed_days" not in cfg


@pytest.mark.asyncio
async def test_edit_without_schedule_leaves_calling_config_untouched(monkeypatch):
    _patch_update_dependencies(monkeypatch)
    row = _campaign_row(trunk={"id": "t"}, batch_size=7)
    db = _FakeDB([row])

    await campaigns.update_campaign(
        CAMPAIGN,
        _update_body(),
        SimpleNamespace(),
        CurrentUser(id="u1", email="u@example.test", tenant_id=TENANT, role="owner"),
        db,
    )

    assert row["calling_config"] == {"trunk": {"id": "t"}, "batch_size": 7}


@pytest.mark.asyncio
async def test_schedule_edit_accepts_calling_config_stored_as_json_text(monkeypatch):
    _patch_update_dependencies(monkeypatch)
    row = _campaign_row()
    row["calling_config"] = json.dumps({"trunk": {"id": "t"}, "call_gap_seconds": 12})
    db = _FakeDB([row])

    await campaigns.update_campaign(
        CAMPAIGN,
        _update_body(calling_schedule={"timezone": "UTC"}),
        SimpleNamespace(),
        CurrentUser(id="u1", email="u@example.test", tenant_id=TENANT, role="owner"),
        db,
    )

    assert row["calling_config"]["trunk"] == {"id": "t"}
    assert row["calling_config"]["call_gap_seconds"] == 12
    assert row["calling_config"]["timezone"] == "UTC"


# ---------------------------------------------------------------------------
# 2. phantom 'campaign is not running' card on a running campaign
# ---------------------------------------------------------------------------
def _issue_row(reason: str, *, campaign_status: str, job_id: str = "55555555-5555-5555-5555-555555555555"):
    return {
        "id": UUID(job_id),
        "phone_number": "+15550001111",
        "campaign_id": UUID(CAMPAIGN),
        "lead_id": UUID("66666666-6666-6666-6666-666666666666"),
        "status": "pending",
        "last_outcome": None,
        "last_error": None,
        "failure_category": None,
        "failure_reason": reason,
        "attempt_number": 1,
        "updated_at": datetime.now(timezone.utc),
        "campaign_name": "Outbound",
        "campaign_status": campaign_status,
        "calling_config": None,
    }


async def _issues_for(monkeypatch, rows):
    class Conn:
        async def fetch(self, _query, *_args):
            return rows

        async def fetchval(self, _query, *_args):
            return None

    @asynccontextmanager
    async def acquire(_pool, _tenant):
        yield Conn()

    async def minutes_available(_tenant_id):
        return SimpleNamespace(exhausted=False, state="known")

    monkeypatch.setattr(calls_module, "acquire_with_tenant", acquire)
    monkeypatch.setattr(
        "app.domain.services.minutes_quota.tenant_minutes_status", minutes_available
    )
    return await list_call_issues(
        campaign_id=None,
        window_minutes=60,
        current_user=CurrentUser(id="u1", email="u@example.test", tenant_id=TENANT),
        db_client=SimpleNamespace(pool=object()),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason",
    [
        "campaign_stopped",
        "campaign_not_runnable:paused",
        "campaign_not_runnable:draft",
        "campaign_not_runnable:missing",
        "campaign_paused",
    ],
)
@pytest.mark.parametrize("status", ["running", "active"])
async def test_running_campaign_drops_every_not_running_reason(monkeypatch, reason, status):
    result = await _issues_for(monkeypatch, [_issue_row(reason, campaign_status=status)])
    assert result.items == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason", ["campaign_not_runnable:paused", "campaign_paused", "campaign_stopped"]
)
async def test_paused_campaign_keeps_the_not_running_card(monkeypatch, reason):
    result = await _issues_for(monkeypatch, [_issue_row(reason, campaign_status="paused")])
    assert [i.reason_code for i in result.items] == [reason]


# ---------------------------------------------------------------------------
# 5. readiness copy + environment normalisation
# ---------------------------------------------------------------------------
def _route(*, refused: bool, reason: str = "", endpoint: str | None = "ep"):
    return SimpleNamespace(
        refused=refused, reason=reason, trunk_id="trunk", caller_id="+442046132300",
        endpoint=endpoint,
    )


def _patch_readiness(monkeypatch, route, *, allowed=True):
    monkeypatch.setattr(
        outbound_readiness, "require_production_outbound_selection", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(trunk_resolver, "requires_sip_readiness", AsyncMock(return_value=True))
    monkeypatch.setattr(trunk_resolver, "resolve_outbound_trunk", AsyncMock(return_value=route))
    ownership = AsyncMock(return_value=SimpleNamespace(allowed=allowed))
    monkeypatch.setattr(caller_id_guard, "check_caller_id_ownership", ownership)
    return ownership


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code,sentence",
    [
        (
            "no_ready_trunk",
            "No phone line is ready to dial out. Connect or switch on a line in Settings → Telephony.",
        ),
        (
            "no_own_trunk",
            "No phone line is ready to dial out. Connect or switch on a line in Settings → Telephony.",
        ),
        (
            "selected_trunk_not_ready",
            "The phone line for this campaign is not registered with the carrier right now. Check it in Settings → Telephony.",
        ),
        (
            "campaign_assigned_trunk_not_ready",
            "The phone line for this campaign is not registered with the carrier right now. Check it in Settings → Telephony.",
        ),
        (
            "trunk_caller_id_not_verified",
            "The caller ID on this line is not verified yet. Ask support to verify it.",
        ),
    ],
)
async def test_route_refusals_are_explained_in_plain_words(monkeypatch, code, sentence):
    _patch_readiness(monkeypatch, _route(refused=True, reason=code))
    result = await outbound_readiness.evaluate_outbound_readiness(
        object(), tenant_id=TENANT, campaign_id=CAMPAIGN, campaign={},
        calling_rules={"caller_id": "+442046132300"}, environment="production",
    )
    assert result["ready"] is False
    assert result["reason_code"] == code  # machine code is unchanged
    assert result["reason"] == sentence


@pytest.mark.asyncio
async def test_unknown_refusal_code_still_names_the_code(monkeypatch):
    _patch_readiness(monkeypatch, _route(refused=True, reason="some_new_refusal"))
    result = await outbound_readiness.evaluate_outbound_readiness(
        object(), tenant_id=TENANT, campaign_id=CAMPAIGN, campaign={},
        calling_rules={"caller_id": "+442046132300"}, environment="production",
    )
    assert result["reason_code"] == "some_new_refusal"
    assert "some_new_refusal" in result["reason"]


@pytest.mark.asyncio
async def test_environment_is_normalised_before_the_caller_id_guard(monkeypatch):
    ownership = _patch_readiness(monkeypatch, _route(refused=False))
    await outbound_readiness.evaluate_outbound_readiness(
        object(), tenant_id=TENANT, campaign_id=CAMPAIGN, campaign={},
        calling_rules={"caller_id": "+442046132300"}, environment="  Production \n",
    )
    assert ownership.await_args.kwargs["environment"] == "production"


@pytest.mark.asyncio
async def test_environment_from_process_env_is_normalised_too(monkeypatch):
    ownership = _patch_readiness(monkeypatch, _route(refused=False))
    monkeypatch.setenv("ENVIRONMENT", "Production ")
    await outbound_readiness.evaluate_outbound_readiness(
        object(), tenant_id=TENANT, campaign_id=CAMPAIGN, campaign={},
        calling_rules={"caller_id": "+442046132300"},
    )
    assert ownership.await_args.kwargs["environment"] == "production"
