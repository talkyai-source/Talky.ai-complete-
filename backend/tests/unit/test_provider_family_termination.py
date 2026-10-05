"""Read-only application probe: synthetic DB/ARI ports; no live effects."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

from app.api.v1.dependencies import CurrentUser
from app.api.v1.endpoints import telephony_bridge
from app.api.v1.endpoints.admin import calls
from app.domain.services.telephony import termination
from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter
import pytest
from fastapi import HTTPException
from unittest.mock import AsyncMock
from app.domain.services.telephony import lifecycle


class Conn:
    def __init__(self, provider, legs=()):
        self.legs = list(legs)
        self.row = dict(
            id="00000000-0000-4000-8000-000000000001",
            tenant_id=UUID("00000000-0000-4000-8000-000000000002"),
            external_call_uuid="synthetic-original-provider-leg",
            provider_call_id="synthetic-original-provider-leg",
            provider=provider,
            direction="outbound",
            status="in_call",
            campaign_id=None,
            answered_at=datetime(2026, 10, 5, tzinfo=timezone.utc),
        )
        self.writes = []

    async def fetchrow(self, sql, *args):
        if "SELECT id, tenant_id, external_call_uuid" in sql or "SELECT external_call_uuid" in sql:
            return dict(self.row)
        if "SELECT id::text AS call_id" in sql:
            return {**self.row, "call_id": self.row["id"]}
        if "UPDATE calls" in sql:
            self.writes.append("terminal_update")
            self.row["status"] = "ended"
            return dict(status="ended", outcome="agent_hung_up", duration_seconds=12)
        raise AssertionError(sql)

    async def fetchval(self, sql, *args):
        assert "SELECT status" in sql
        return self.row["status"]

    async def fetch(self, sql, *args):
        assert "FROM call_legs" in sql
        return self.legs

    async def execute(self, sql, *args):
        assert "SET status='termination_pending'" in sql
        self.row["status"] = "termination_pending"
        self.writes.append("termination_pending")
        return "UPDATE 1"


class Audit:
    def __init__(self):
        self.events = []

    async def log(self, **kwargs):
        self.events.append(kwargs["action"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider", ["twilio", "vonage", None, "", "unknown", "sip", "sip/asterisk", "asterisk"]
)
async def test_admin_absence_proof_requires_saved_provider_family(monkeypatch, provider):
    conn, audit, ari_requests = Conn(provider), Audit(), []
    adapter = AsteriskAdapter()

    @asynccontextmanager
    async def acquire(*_args, **_kwargs):
        yield conn

    async def ari(method, path, **_kwargs):
        ari_requests.append((method, path))
        return 404, {}

    adapter._ari = ari
    monkeypatch.setattr(calls, "acquire_with_tenant", acquire)
    monkeypatch.setattr(termination, "acquire_with_tenant", acquire)
    monkeypatch.setattr(telephony_bridge, "_adapter", adapter)
    params = dict(
        admin_user=CurrentUser(
            id="00000000-0000-4000-8000-000000000003",
            email="synthetic@example.com",
            role="platform_admin",
        ),
        db_client=SimpleNamespace(pool=object()),
        audit_logger=audit,
    )
    if provider == "asterisk":
        result = await calls.terminate_call(conn.row["id"], **params)
        assert result["provider_hangup_confirmed"] is True
        assert conn.row["status"] == "ended"
        assert ari_requests
    else:
        with pytest.raises(HTTPException) as exc:
            await calls.terminate_call(conn.row["id"], **params)
        assert exc.value.status_code == 503
        assert conn.row["status"] == "termination_pending"
        assert conn.writes == ["termination_pending"]
        assert ari_requests == []
        assert audit.events == ["admin_call_termination_unconfirmed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("child_provider", ["asterisk", "twilio", "vonage", "sip", "unknown", None])
async def test_actual_context_retains_all_leg_authority_before_any_io(monkeypatch, child_provider):
    # Repeated IDs with conflicting tags must not be deduplicated into proof.
    legs = [
        {"provider_leg_id": "same-child", "provider": "asterisk"},
        {"provider_leg_id": "same-child", "provider": child_provider},
    ]
    conn = Conn("asterisk", legs)

    @asynccontextmanager
    async def acquire(*_args, **_kwargs):
        yield conn

    monkeypatch.setattr(termination, "acquire_with_tenant", acquire)
    context = await termination.mark_termination_pending_and_load_context(
        object(), call_reference=conn.row["id"]
    )
    adapter = AsteriskAdapter()
    adapter._ari = AsyncMock(return_value=(404, {}))
    proof = await termination.request_confirmed_hangup(
        adapter,
        context.provider_call_id,
        expected_provider=context.provider,
        provider_leg_ids=context.provider_leg_ids,
        provider_legs=context.provider_legs,
    )
    assert proof.confirmed is (child_provider == "asterisk")
    if child_provider != "asterisk":
        assert proof.requested is False
        adapter._ari.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["asterisk", "twilio", None, "sip"])
async def test_inbound_lease_loss_does_not_settle_with_unrelated_adapter(monkeypatch, provider):
    monkeypatch.setattr(lifecycle, "_orphan_recovery_in_flight", set())
    monkeypatch.setattr(lifecycle, "_orphan_recovery_contexts_by_call", {})
    conn = Conn(provider)
    conn.row["direction"] = "inbound"

    @asynccontextmanager
    async def acquire(*_args, **_kwargs):
        yield conn

    monkeypatch.setattr(termination, "acquire_with_tenant", acquire)
    adapter = AsteriskAdapter()
    adapter._ari = AsyncMock(return_value=(404, {}))
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: adapter)
    monkeypatch.setattr(
        lifecycle, "_state", lambda: SimpleNamespace(register_cleanup_obligation=AsyncMock())
    )
    ended = AsyncMock(return_value=True)
    monkeypatch.setattr(lifecycle, "_on_call_ended", ended)
    result = await lifecycle._fence_inbound_call_after_lease_loss(
        SimpleNamespace(db_pool=object()),
        pbx_call_id=conn.row["provider_call_id"],
        durable_call_id=conn.row["id"],
        admission={"tenant_id": str(conn.row["tenant_id"]), "provider": provider},
    )
    assert result is (provider == "asterisk")
    if provider != "asterisk":
        adapter._ari.assert_not_awaited()
        ended.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["asterisk", "twilio", None, "sip"])
@pytest.mark.parametrize("endpoint", ["tenant", "raw", "transfer"])
async def test_durable_entrypoints_use_saved_identity(monkeypatch, provider, endpoint):
    from app.api.v1.endpoints import calls as tenant_calls
    from app.core import container as container_module

    conn = Conn(provider)
    adapter = AsteriskAdapter()
    adapter._ari = AsyncMock(return_value=(404, {}))

    @asynccontextmanager
    async def acquire(*_args, **_kwargs):
        yield conn

    monkeypatch.setattr(termination, "acquire_with_tenant", acquire)
    monkeypatch.setattr(tenant_calls, "acquire_with_tenant", acquire)
    monkeypatch.setattr(telephony_bridge, "_adapter", adapter)
    monkeypatch.setattr(
        container_module, "get_container", lambda: SimpleNamespace(db_pool=object(), redis=None)
    )
    finalize = AsyncMock()
    monkeypatch.setattr(telephony_bridge, "finalize_proven_inbound_termination", finalize)
    if endpoint == "tenant":

        async def invoke():
            return await tenant_calls.hangup_live_call(
                conn.row["id"],
                current_user=CurrentUser(
                    id="00000000-0000-4000-8000-000000000003",
                    email="synthetic@example.com",
                    role="admin",
                    tenant_id=str(conn.row["tenant_id"]),
                ),
                db_client=SimpleNamespace(pool=object()),
            )

    elif endpoint == "raw":
        monkeypatch.setattr(
            telephony_bridge,
            "_require_call_control",
            AsyncMock(
                return_value=SimpleNamespace(
                    tenant_id=str(conn.row["tenant_id"]), is_internal=False
                )
            ),
        )
        monkeypatch.setattr(telephony_bridge, "_verify_call_ownership", AsyncMock())

        async def invoke():
            return await telephony_bridge.hangup_call(
                conn.row["provider_call_id"], SimpleNamespace()
            )

    else:

        async def invoke():
            return await telephony_bridge._apply_inbound_transfer_failure_action(
                SimpleNamespace(
                    inbound=True,
                    failure_action="hangup",
                    call_id=conn.row["id"],
                    tenant_id=str(conn.row["tenant_id"]),
                ),
                {"status": "failed"},
            )

    if provider == "asterisk":
        await invoke()
        assert adapter._ari.await_count > 0
    elif endpoint == "transfer":
        result = await invoke()
        assert result["fallback_status"] == "termination_pending"
        finalize.assert_not_awaited()
        adapter._ari.assert_not_awaited()
    else:
        with pytest.raises(HTTPException) as error:
            await invoke()
        assert error.value.status_code == 503
        adapter._ari.assert_not_awaited()
        assert "terminal_update" not in conn.writes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "parent,child",
    [
        ("asterisk", "asterisk"),
        ("twilio", "asterisk"),
        (None, "asterisk"),
        ("sip", "asterisk"),
        ("asterisk", "twilio"),
        ("asterisk", None),
    ],
)
async def test_campaign_batch_holds_entire_mismatched_call(monkeypatch, parent, child):
    from tests.unit.test_confirmation_aware_call_endpoints import _CampaignConn, _Pool
    from app.core import container as container_module

    class BatchConn(_CampaignConn):
        async def fetch(self, query, *_args):
            if "FROM calls c" in query:
                return [
                    {
                        "durable_call_id": "saved-call",
                        "provider_call_id": "parent",
                        "provider": parent,
                    }
                ]
            assert "FROM call_legs" in query
            return [
                {"durable_call_id": "saved-call", "provider_leg_id": "child", "provider": child}
            ]

    conn = BatchConn()
    adapter = AsteriskAdapter()
    adapter._ari = AsyncMock(return_value=(404, {}))
    monkeypatch.setattr(
        container_module, "get_container", lambda: SimpleNamespace(db_pool=_Pool(conn))
    )
    monkeypatch.setattr(telephony_bridge, "_adapter", adapter)
    result = await telephony_bridge.hangup_calls_for_campaign(
        "00000000-0000-4000-8000-000000000004"
    )
    matched = parent == child == "asterisk"
    assert result["confirmed"] == int(matched)
    assert result["requested"] == int(matched)
    assert result["deferred"] == int(not matched)
    if not matched:
        adapter._ari.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["asterisk", "twilio", "sip", None])
async def test_recovery_rejects_conflicting_duplicate_leg_before_dedup(provider):
    conn = Conn(
        "asterisk",
        [
            {"provider_leg_id": "child", "provider": "asterisk"},
            {"provider_leg_id": "child", "provider": provider},
        ],
    )
    if provider == "asterisk":
        assert await termination.fetch_active_provider_leg_ids(
            conn, call_reference=conn.row["id"], expected_provider="asterisk"
        ) == ("child",)
    else:
        with pytest.raises(ValueError, match="linked_leg_provider"):
            await termination.fetch_active_provider_leg_ids(
                conn, call_reference=conn.row["id"], expected_provider="asterisk"
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("family", ["asterisk", "freeswitch", "twilio", "vonage"])
async def test_concrete_matching_family_can_request_its_own_proof(family):
    adapter = SimpleNamespace(name=family, hangup_confirmed=AsyncMock(return_value=True))
    proof = await termination.request_confirmed_hangup(
        adapter, "saved-parent", expected_provider=family
    )
    assert proof.confirmed
    adapter.hangup_confirmed.assert_awaited_once_with("saved-parent")


@pytest.mark.asyncio
async def test_missing_linked_metadata_is_not_an_omission_bypass():
    adapter = SimpleNamespace(name="asterisk", hangup_many_confirmed=AsyncMock(return_value=True))
    proof = await termination.request_confirmed_hangup(
        adapter, "parent", expected_provider="asterisk", provider_leg_ids=("child",)
    )
    assert proof.code == "linked_leg_provider_unconfirmed"
    assert not proof.requested
    adapter.hangup_many_confirmed.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("child_provider", ["asterisk", "twilio", None, "sip"])
async def test_takeover_candidate_rehydrates_leg_provenance_before_proof(
    monkeypatch, child_provider
):
    from tests.unit.test_telephony_orphan_recovery_confirmation import _RetryLedger
    from app.core import container as container_module, db_utils
    from app.domain.services.telephony import transfer_restart_recovery

    durable = "00000000-0000-4000-8000-000000000001"

    class RecoveryConn:
        async def fetchrow(self, query, *_args):
            assert "recovery_match_count" in query
            return {
                "id": durable,
                "tenant_id": "00000000-0000-4000-8000-000000000002",
                "provider": "asterisk",
                "provider_call_id": "recovered-parent",
                "direction": "inbound",
                "status": "termination_pending",
                "recovery_match_count": 1,
            }

        async def fetch(self, query, *_args):
            assert "FROM call_legs" in query
            return [{"provider_leg_id": "persisted-child", "provider": child_provider}]

    @asynccontextmanager
    async def acquire(*_args, **_kwargs):
        yield RecoveryConn()

    ledger = _RetryLedger([])
    ledger.is_telephony_owner = lambda: True
    # The takeover's ID-only claim is deliberately benign; the DB reread must
    # be authoritative, including when the child has a conflicting family.
    claim = SimpleNamespace(provider_leg_ids=("claim-child",))
    claim_takeovers = AsyncMock(return_value=[claim])
    monkeypatch.setattr(
        transfer_restart_recovery, "claim_inbound_transfer_takeovers", claim_takeovers
    )
    monkeypatch.setattr(lifecycle, "_state", lambda: ledger)
    monkeypatch.setattr(
        lifecycle, "_register_unknown_asterisk_cleanup_candidates", AsyncMock(return_value=0)
    )
    monkeypatch.setattr(
        lifecycle,
        "_load_termination_pending_candidates",
        AsyncMock(
            return_value=[
                {
                    "call_id": "recovered-parent",
                    "provider": "asterisk",
                    "durable_call_id": durable,
                    "_termination_pending": True,
                    "_has_redis_ledger": False,
                }
            ]
        ),
    )
    monkeypatch.setattr(lifecycle, "_rotate_deferred_termination_candidate", AsyncMock())
    monkeypatch.setattr(
        container_module,
        "get_container",
        lambda: SimpleNamespace(is_initialized=True, db_pool=object()),
    )
    monkeypatch.setattr(db_utils, "acquire_with_tenant", acquire)
    adapter = SimpleNamespace(name="asterisk", hangup_many_confirmed=AsyncMock(return_value=True))
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: adapter)
    ended = AsyncMock(return_value=True)
    monkeypatch.setattr(lifecycle, "_on_call_ended", ended)
    assert await lifecycle.recover_orphaned_calls() == int(child_provider == "asterisk")
    claim_takeovers.assert_awaited_once()
    if child_provider == "asterisk":
        adapter.hangup_many_confirmed.assert_awaited_once_with(
            ["recovered-parent", "persisted-child"]
        )
        ended.assert_awaited_once()
    else:
        adapter.hangup_many_confirmed.assert_not_awaited()
        ended.assert_not_awaited()
