"""Answered outbound calls must never settle at 0s just because the in-memory
monotonic Answer time is gone (restart / consumed during a failed teardown).
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.api.v1.endpoints import calls
from app.domain.services.telephony import lifecycle

_T0 = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)


class _Ctx:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_a):
        return False


def _row(**over):
    row = {
        "id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        "tenant_id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
        "campaign_id": None,
        "direction": "outbound",
        "provider": "asterisk",
        "provider_call_id": "pbx-out",
        "external_call_uuid": None,
        "status": "in_call",
        "outcome": None,
        "started_at": _T0 - timedelta(seconds=10),
        "answered_at": _T0,
        "ended_at": None,
        "provider_terminated_at": _T0 + timedelta(minutes=7),
        "duration_seconds": None,
        "admission_status": None,
        "admission_reason": None,
        "processing_status": None,
        "billing_status": None,
        "reserved_seconds": None,  # inbound-only field: NULL for outbound
        "concurrency_lease_id": None,
        "route_snapshot": {},
        "terminal_settled_at": None,
        "terminal_retry_payload": None,
        "terminal_retry_enqueued_at": None,
        "recovery_duration_seconds": 0,  # what the SQL CASE yields for outbound
        "recovery_match_count": 1,
    }
    row.update(over)
    return row


async def _hydrate(monkeypatch, row):
    import app.core.container as container_module
    import app.core.db_utils as db_utils

    class Conn:
        async def fetchrow(self, *_a):
            return dict(row)

        async def fetch(self, *_a):
            return []

    monkeypatch.setattr(
        container_module,
        "get_container",
        lambda: SimpleNamespace(is_initialized=True, db_pool=object()),
    )
    monkeypatch.setattr(db_utils, "acquire_with_tenant", lambda *_a, **_k: _Ctx(Conn()))
    return await lifecycle._hydrate_orphan_recovery_context(
        "pbx-out", {"state": "active", "provider": "asterisk"}
    )


@pytest.mark.asyncio
async def test_outbound_recovery_derives_duration_from_answered_at(monkeypatch):
    ctx = await _hydrate(monkeypatch, _row())
    assert ctx["duration_seconds"] == 420


@pytest.mark.asyncio
async def test_outbound_recovery_without_terminal_proof_uses_ended_at(monkeypatch):
    ctx = await _hydrate(
        monkeypatch,
        _row(provider_terminated_at=None, ended_at=_T0 + timedelta(seconds=90)),
    )
    assert ctx["duration_seconds"] == 90


@pytest.mark.asyncio
async def test_outbound_recovery_without_any_end_uses_now_capped(monkeypatch):
    ctx = await _hydrate(
        monkeypatch,
        _row(
            provider_terminated_at=None,
            ended_at=None,
            answered_at=datetime.now(timezone.utc) - timedelta(hours=3),
        ),
    )
    assert ctx["duration_seconds"] == lifecycle._SESSION_MAX_DURATION_S


@pytest.mark.asyncio
async def test_outbound_recovery_caps_at_max_call_duration(monkeypatch):
    ctx = await _hydrate(
        monkeypatch, _row(provider_terminated_at=_T0 + timedelta(hours=2))
    )
    assert ctx["duration_seconds"] == lifecycle._SESSION_MAX_DURATION_S


@pytest.mark.asyncio
async def test_outbound_never_answered_stays_zero(monkeypatch):
    ctx = await _hydrate(
        monkeypatch, _row(answered_at=None, status="ringing", provider_terminated_at=None)
    )
    assert ctx["duration_seconds"] == 0


@pytest.mark.asyncio
async def test_inbound_recovery_duration_unchanged(monkeypatch):
    ctx = await _hydrate(
        monkeypatch,
        _row(
            direction="inbound",
            reserved_seconds=600,
            recovery_duration_seconds=42,
            duration_seconds=42,
        ),
    )
    assert ctx["duration_seconds"] == 42


def test_operator_hangup_never_writes_zero_when_answered_at_missing():
    src = " ".join(inspect.getsource(calls.hangup_live_call).split())
    assert "ELSE 0 END" not in src
    assert "ELSE NULL END" in src
