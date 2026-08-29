"""Proof-aware restart recovery regression tests.

An orphan's Redis entry is the durable retry record. It may disappear only
after the PBX proves termination and normal logical teardown has run.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.domain.services.telephony import lifecycle


class _RetryLedger:
    def __init__(self, entries=None) -> None:
        self.entries = (
            list(entries)
            if entries is not None
            else [
                {
                    "call_id": "orphan-1",
                    "pod_id": "dead-incarnation",
                    "tenant_id": "tenant-1",
                }
            ]
        )
        self.acknowledged: list[str] = []
        self.sessions: dict[str, object] = {}
        self.recovery_limits: list[int | None] = []

    async def recover_orphans(self, *, limit=None):
        self.recovery_limits.append(limit)
        entries = list(self.entries)
        return entries if limit is None else entries[:limit]

    async def acknowledge_orphan_recovery(self, call_id: str) -> None:
        self.acknowledged.append(call_id)
        self.entries = [e for e in self.entries if e["call_id"] != call_id]

    def clear_ringing_started_at(self, _call_id: str) -> None:
        return None

    def pop_ringing_warmup(self, _call_id: str):
        return None

    def clear_first_speaker(self, _call_id: str) -> None:
        return None

    def pop_voice_session(self, call_id: str):
        return self.sessions.pop(call_id, None)

    def remove_gateway_sessions_for_call(self, _call_id: str) -> None:
        return None


class _ConfirmationAdapter:
    def __init__(self, *results: bool) -> None:
        self._results = list(results)
        self.attempts: list[str] = []

    async def hangup_confirmed(self, call_id: str) -> bool:
        self.attempts.append(call_id)
        return self._results.pop(0)


class _AsyncContext:
    def __init__(self, value) -> None:
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_args):
        return False


class _InverseLedger(_RetryLedger):
    def __init__(self):
        super().__init__([])
        self.registered: list[str] = []
        self.owner = True
        self.voice_ids: set[str] = set()
        self.warmup_ids: set[str] = set()
        self.event_ids: set[str] = set()

    def is_telephony_owner(self):
        return self.owner

    async def claim_cleanup_obligation_if_absent(self, call_id, **kwargs):
        if any(entry["call_id"] == call_id for entry in self.entries):
            return False
        self.registered.append(call_id)
        self.entries.append(
            {
                "call_id": call_id,
                "pod_id": "successor",
                "recovery_source": "inverse_ari_inventory",
                **kwargs,
            }
        )
        return True

    def iter_voice_session_items(self):
        return [(call_id, object()) for call_id in self.voice_ids]

    def iter_ringing_warmup_keys(self):
        return list(self.warmup_ids)

    def iter_ringing_event_keys(self):
        return list(self.event_ids)


class _InverseAdapter:
    name = "asterisk"

    def __init__(self, channel_ids, *, excluded=()):
        self.channel_ids = channel_ids
        self.excluded = set(excluded)
        self.inventory_calls = 0

    async def list_recoverable_application_channel_ids(self):
        self.inventory_calls += 1
        return self.channel_ids

    def recovery_excluded_channel_ids(self):
        return set(self.excluded)


@pytest.mark.asyncio
async def test_bounded_recovery_attempts_database_candidate_before_redis_backlog(
    monkeypatch,
):
    redis_entries = [
        {
            "call_id": f"redis-orphan-{index}",
            "pod_id": "dead-incarnation",
            "provider": "asterisk",
        }
        for index in range(10)
    ]
    ledger = _RetryLedger(redis_entries)
    attempts: list[str] = []

    async def pending():
        return [
            {
                "call_id": "database-reservation",
                "pod_id": "database:termination_pending",
                "provider": "asterisk",
                "durable_call_id": "11111111-1111-1111-1111-111111111111",
                "_termination_pending": True,
                "_has_redis_ledger": False,
            }
        ]

    async def hydrate(call_id, entry):
        return {
            "provider_session_id": call_id,
            "provider_leg_ids": [],
            "durable_call_id": entry.get("durable_call_id"),
            "direction": "outbound",
            "provider": "asterisk",
            "logical_settled": True,
            "ledger_entry": dict(entry),
        }

    async def force(call_id, **_kwargs):
        attempts.append(call_id)
        return True

    monkeypatch.setattr(lifecycle, "_state", lambda: ledger)
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: _ConfirmationAdapter())
    monkeypatch.setattr(lifecycle, "_load_termination_pending_candidates", pending)
    monkeypatch.setattr(lifecycle, "_hydrate_orphan_recovery_context", hydrate)
    monkeypatch.setattr(lifecycle, "_force_end_and_hangup", force)
    lifecycle._orphan_recovery_in_flight.clear()

    recovered = await lifecycle.recover_orphaned_calls()

    assert recovered == 1 + lifecycle._ORPHAN_RECOVERY_SOURCE_BATCH
    assert ledger.recovery_limits == [lifecycle._ORPHAN_RECOVERY_SOURCE_BATCH]
    assert attempts[0] == "database-reservation"
    assert attempts[1:] == [
        f"redis-orphan-{index}" for index in range(lifecycle._ORPHAN_RECOVERY_SOURCE_BATCH)
    ]
    assert len(ledger.entries) == 10 - lifecycle._ORPHAN_RECOVERY_SOURCE_BATCH


@pytest.mark.asyncio
async def test_inverse_recovery_makes_preledger_ari_channel_durable_then_confirms(
    monkeypatch,
):
    ledger = _InverseLedger()
    adapter = _InverseAdapter({"preledger-parent"})
    attempts = []

    async def no_pending():
        return []

    async def hydrate(call_id, entry):
        return {
            "provider_session_id": call_id,
            "provider_leg_ids": [],
            "durable_call_id": None,
            "direction": "unknown",
            "provider": "asterisk",
            "logical_settled": True,
            "ledger_entry": dict(entry),
        }

    async def force(call_id, **_kwargs):
        attempts.append(call_id)
        return True

    monkeypatch.setattr(lifecycle, "_state", lambda: ledger)
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: adapter)
    monkeypatch.setattr(lifecycle, "_load_termination_pending_candidates", no_pending)
    monkeypatch.setattr(lifecycle, "_hydrate_orphan_recovery_context", hydrate)
    monkeypatch.setattr(lifecycle, "_force_end_and_hangup", force)
    lifecycle._unknown_asterisk_channel_ticks.clear()
    lifecycle._orphan_recovery_in_flight.clear()
    try:
        # One observation cannot race and terminate a channel still entering
        # the normal setup path.
        assert await lifecycle.recover_orphaned_calls() == 0
        assert ledger.registered == []
        assert attempts == []

        # The second successful inventory writes Redis first. Normal recovery
        # then hydrates and performs the confirmation-aware PBX path before ack.
        assert await lifecycle.recover_orphaned_calls() == 1
        assert ledger.registered == ["preledger-parent"]
        assert attempts == ["preledger-parent"]
        assert ledger.acknowledged == ["preledger-parent"]
        assert ledger.entries == []
    finally:
        lifecycle._unknown_asterisk_channel_ticks.clear()
        lifecycle._orphan_recovery_in_flight.clear()


@pytest.mark.asyncio
async def test_inverse_recovery_excludes_live_setup_and_fails_closed(monkeypatch):
    ledger = _InverseLedger()
    ledger.voice_ids.add("voice")
    ledger.warmup_ids.add("warmup")
    ledger.event_ids.add("ringing-event")
    adapter = _InverseAdapter(
        {
            "adapter-owned",
            "voice",
            "warmup",
            "ringing-event",
            "admission-flight",
            "admission-pending",
            "recovery-flight",
        },
        excluded={"adapter-owned"},
    )
    lifecycle._inbound_admissions_in_flight["admission-flight"] = {}
    lifecycle._inbound_admissions_pending.add("admission-pending")
    lifecycle._orphan_recovery_in_flight.add("recovery-flight")
    lifecycle._unknown_asterisk_channel_ticks.clear()
    try:
        assert await lifecycle._register_unknown_asterisk_cleanup_candidates(
            ledger, adapter
        ) == 0
        assert await lifecycle._register_unknown_asterisk_cleanup_candidates(
            ledger, adapter
        ) == 0
        assert ledger.registered == []

        # Non-owner and ambiguous inventory results never write Redis or touch
        # PBX. The latter leaves existing debounce state unchanged.
        ledger.owner = False
        adapter.channel_ids = {"unknown"}
        assert await lifecycle._register_unknown_asterisk_cleanup_candidates(
            ledger, adapter
        ) == 0
        ledger.owner = True
        adapter.channel_ids = None
        assert await lifecycle._register_unknown_asterisk_cleanup_candidates(
            ledger, adapter
        ) == 0
        assert ledger.registered == []
    finally:
        lifecycle._inbound_admissions_in_flight.pop("admission-flight", None)
        lifecycle._inbound_admissions_pending.discard("admission-pending")
        lifecycle._orphan_recovery_in_flight.discard("recovery-flight")
        lifecycle._unknown_asterisk_channel_ticks.clear()


@pytest.mark.asyncio
async def test_inverse_atomic_claim_loses_to_normal_answer_without_mutation():
    class RacingLedger(_InverseLedger):
        async def claim_cleanup_obligation_if_absent(self, call_id, **_kwargs):
            # Model normal admission committing answer_pending inside the same
            # Redis serialization window. The inverse atomic claim loses.
            self.entries.append(
                {
                    "call_id": call_id,
                    "pod_id": "live-owner",
                    "state": "answer_pending",
                    "tenant_id": "tenant-1",
                    "campaign_id": "campaign-1",
                    "durable_call_id": "durable-1",
                    "provider": "asterisk",
                    "provider_call_id": call_id,
                    "answer_requested_at": "2026-08-28T12:00:00+00:00",
                }
            )
            return False

    ledger = RacingLedger()
    adapter = _InverseAdapter({"answer-race"})
    lifecycle._unknown_asterisk_channel_ticks.clear()
    try:
        assert await lifecycle._register_unknown_asterisk_cleanup_candidates(
            ledger, adapter
        ) == 0
        assert await lifecycle._register_unknown_asterisk_cleanup_candidates(
            ledger, adapter
        ) == 0
        assert ledger.registered == []
        assert ledger.entries == [
            {
                "call_id": "answer-race",
                "pod_id": "live-owner",
                "state": "answer_pending",
                "tenant_id": "tenant-1",
                "campaign_id": "campaign-1",
                "durable_call_id": "durable-1",
                "provider": "asterisk",
                "provider_call_id": "answer-race",
                "answer_requested_at": "2026-08-28T12:00:00+00:00",
            }
        ]
    finally:
        lifecycle._unknown_asterisk_channel_ticks.clear()


@pytest.mark.asyncio
async def test_inverse_recovery_rechecks_local_setup_after_hydration(monkeypatch):
    ledger = _InverseLedger()
    adapter = _InverseAdapter({"late-stasis-start"})
    pbx_attempts = []

    async def no_pending():
        return []

    async def hydrate(call_id, entry):
        # StasisStart resumes while the database lookup is awaited. The final
        # PBX-boundary snapshot must see the new local owner and defer.
        adapter.excluded.add(call_id)
        return {
            "provider_session_id": call_id,
            "provider_leg_ids": [],
            "durable_call_id": None,
            "direction": "unknown",
            "provider": "asterisk",
            "logical_settled": True,
            "ledger_entry": dict(entry),
        }

    async def force(call_id, **_kwargs):
        pbx_attempts.append(call_id)
        return True

    monkeypatch.setattr(lifecycle, "_state", lambda: ledger)
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: adapter)
    monkeypatch.setattr(lifecycle, "_load_termination_pending_candidates", no_pending)
    monkeypatch.setattr(lifecycle, "_hydrate_orphan_recovery_context", hydrate)
    monkeypatch.setattr(lifecycle, "_force_end_and_hangup", force)
    lifecycle._unknown_asterisk_channel_ticks.clear()
    lifecycle._orphan_recovery_in_flight.clear()
    try:
        assert await lifecycle.recover_orphaned_calls() == 0
        assert await lifecycle.recover_orphaned_calls() == 0
        assert ledger.registered == ["late-stasis-start"]
        assert pbx_attempts == []
        assert ledger.entries[0]["recovery_source"] == "inverse_ari_inventory"
        assert ledger.acknowledged == []
    finally:
        lifecycle._unknown_asterisk_channel_ticks.clear()
        lifecycle._orphan_recovery_in_flight.clear()


@pytest.mark.asyncio
async def test_hydration_uses_durable_inbound_truth_and_all_active_transfer_ids(
    monkeypatch,
):
    """Redis ownership metadata must not decide direction or linked-leg proof."""

    import app.core.container as container_module
    import app.core.db_utils as db_utils

    durable_id = "11111111-1111-1111-1111-111111111111"
    tenant_id = "22222222-2222-2222-2222-222222222222"

    class Conn:
        def __init__(self):
            self.transfer_query = ""

        async def fetchrow(
            self,
            query,
            provider_id,
            durable_call_id,
            provider,
            tenant_id_filter,
        ):
            assert provider_id == "provider-inbound"
            assert durable_call_id is None
            assert provider == "asterisk"
            assert tenant_id_filter is None
            assert "recovery_duration_seconds" in query
            assert "COUNT(*) OVER() AS recovery_match_count" in query
            return {
                "id": durable_id,
                "tenant_id": tenant_id,
                "campaign_id": None,
                "direction": "inbound",
                "provider": "asterisk",
                "provider_call_id": "provider-inbound",
                "external_call_uuid": "external-inbound",
                "status": "termination_pending",
                "outcome": None,
                "started_at": datetime.now(timezone.utc),
                "answered_at": datetime.now(timezone.utc),
                "ended_at": None,
                "duration_seconds": None,
                "admission_status": "admitted",
                "admission_reason": "accepted",
                "processing_status": "active",
                "billing_status": "reserved",
                "reserved_seconds": 60,
                "concurrency_lease_id": None,
                "route_snapshot": {},
                "recovery_duration_seconds": 42,
                "recovery_match_count": 1,
            }

        async def fetch(self, query, actual_durable_id):
            assert actual_durable_id == durable_id
            self.transfer_query = query
            return [
                {"provider_leg_id": "talky-xfer-00000000000000000001"},
                {"provider_leg_id": "talky-xfer-00000000000000000002"},
            ]

    conn = Conn()
    monkeypatch.setattr(
        container_module,
        "get_container",
        lambda: SimpleNamespace(is_initialized=True, db_pool=object()),
    )
    monkeypatch.setattr(
        db_utils,
        "acquire_with_tenant",
        lambda *_args, **_kwargs: _AsyncContext(conn),
    )

    context = await lifecycle._hydrate_orphan_recovery_context(
        "provider-inbound",
        {
            # Direction is only a mirror value; PostgreSQL wins.
            "direction": "outbound",
            "provider": "asterisk",
            "state": "ringing",
        },
    )

    assert context["direction"] == "inbound"
    assert context["provider"] == "asterisk"
    assert context["duration_seconds"] == 42
    assert context["admission"]["allowed"] is True
    assert context["admission"]["billing_status"] == "reserved"
    assert context["logical_settled"] is False
    assert context["provider_leg_ids"] == [
        "talky-xfer-00000000000000000001",
        "talky-xfer-00000000000000000002",
    ]
    assert "'initiated','ringing','answered'" in conn.transfer_query
    assert "provider_leg_id NOT LIKE 'transfer-%'" in conn.transfer_query


@pytest.mark.asyncio
async def test_unresolved_answer_intent_recovers_as_answered_with_nonzero_duration(
    monkeypatch,
):
    import app.core.container as container_module
    import app.core.db_utils as db_utils

    durable_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    tenant_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

    class Conn:
        async def fetchrow(self, _query, provider_id, *identity):
            assert provider_id == "pbx-answer-intent"
            assert identity == (durable_id, "asterisk", tenant_id)
            return {
                "id": durable_id,
                "tenant_id": tenant_id,
                "campaign_id": None,
                "direction": "inbound",
                "provider": "asterisk",
                "provider_call_id": "pbx-answer-intent",
                "external_call_uuid": None,
                "status": "initiated",
                "outcome": None,
                "started_at": datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc),
                "answered_at": None,
                "ended_at": None,
                "duration_seconds": 0,
                "admission_status": "allowed",
                "admission_reason": "accepted",
                "processing_status": "active",
                "billing_status": "reserved",
                "reserved_seconds": 60,
                "concurrency_lease_id": None,
                "route_snapshot": {},
                "terminal_settled_at": None,
                "terminal_retry_payload": None,
                "terminal_retry_enqueued_at": None,
                "recovery_duration_seconds": 0,
                "recovery_match_count": 1,
            }

        async def fetch(self, *_args):
            return []

    monkeypatch.setattr(
        container_module,
        "get_container",
        lambda: SimpleNamespace(is_initialized=True, db_pool=object()),
    )
    monkeypatch.setattr(
        db_utils,
        "acquire_with_tenant",
        lambda *_args, **_kwargs: _AsyncContext(Conn()),
    )

    context = await lifecycle._hydrate_orphan_recovery_context(
        "pbx-answer-intent",
        {
            "state": "answer_pending",
            "answer_requested_at": "2026-08-28T12:00:00+00:00",
            "durable_call_id": durable_id,
            "tenant_id": tenant_id,
            "provider": "asterisk",
            "provider_call_id": "pbx-answer-intent",
            "direction": "inbound",
        },
    )

    assert context["was_answered"] is True
    assert context["answer_ambiguous"] is True
    assert context["duration_seconds"] >= 1
    assert context["admission"]["_recovery_was_answered"] is True
    assert context["admission"]["_terminal_reason"] == "process_restart_answer_ambiguous"


@pytest.mark.asyncio
async def test_pending_scan_includes_termination_and_terminal_reserved_inbound(
    monkeypatch,
):
    import app.core.container as container_module
    import app.core.db_utils as db_utils

    now = datetime.now(timezone.utc)

    class Conn:
        query = ""

        async def fetch(self, query, provider):
            self.query = query
            assert provider == "asterisk"
            return [
                {
                    "id": "11111111-1111-1111-1111-111111111111",
                    "tenant_id": "22222222-2222-2222-2222-222222222222",
                    "provider": "asterisk",
                    "provider_call_id": "termination-parent",
                    "external_call_uuid": None,
                    "direction": "outbound",
                    "status": "termination_pending",
                    "processing_status": "active",
                    "billing_status": None,
                    "ended_at": None,
                    "updated_at": now,
                },
                {
                    "id": "33333333-3333-3333-3333-333333333333",
                    "tenant_id": "44444444-4444-4444-4444-444444444444",
                    "provider": "asterisk",
                    "provider_call_id": "settlement-parent",
                    "external_call_uuid": None,
                    "direction": "inbound",
                    "status": "completed",
                    "processing_status": "completed",
                    "billing_status": "reserved",
                    "billing_hold_reason": None,
                    "ended_at": now,
                    "updated_at": now,
                },
            ]

    conn = Conn()
    monkeypatch.setattr(
        container_module,
        "get_container",
        lambda: SimpleNamespace(is_initialized=True, db_pool=object()),
    )
    monkeypatch.setattr(
        db_utils,
        "acquire_with_tenant",
        lambda *_args, **_kwargs: _AsyncContext(conn),
    )

    candidates = await lifecycle._load_termination_pending_candidates()

    assert [item["call_id"] for item in candidates] == [
        "termination-parent",
        "settlement-parent",
    ]
    assert candidates[0]["_termination_pending"] is True
    assert candidates[1]["_inbound_settlement_pending"] is True
    assert all(item["_has_redis_ledger"] is False for item in candidates)
    assert "status='termination_pending'" in conn.query
    assert "billing_status='reserved'" in conn.query
    assert "ended_at IS NOT NULL" in conn.query
    assert "direction='outbound'" in conn.query
    assert "terminal_settled_at IS NULL" in conn.query
    assert "terminal_retry_payload IS NOT NULL" in conn.query
    assert "terminal_retry_enqueued_at IS NULL" in conn.query
    assert "pending_leg.leg_type='transfer'" in conn.query
    assert "pending_leg.status IN" in conn.query
    assert "NULLIF(BTRIM(provider_call_id),'')" in conn.query
    assert "NULLIF(BTRIM(external_call_uuid),'')" in conn.query
    assert "LOWER(BTRIM(provider))=$1::text" in conn.query


@pytest.mark.asyncio
async def test_deferred_candidate_rotation_only_touches_unresolved_rows(monkeypatch):
    import app.core.container as container_module
    import app.core.db_utils as db_utils

    class Conn:
        query = ""
        args = ()

        async def execute(self, query, *args):
            self.query = query
            self.args = args
            return "UPDATE 1"

    conn = Conn()
    monkeypatch.setattr(
        container_module,
        "get_container",
        lambda: SimpleNamespace(is_initialized=True, db_pool=object()),
    )
    monkeypatch.setattr(
        db_utils,
        "acquire_with_tenant",
        lambda *_args, **_kwargs: _AsyncContext(conn),
    )

    durable_id = "11111111-1111-1111-1111-111111111111"
    await lifecycle._rotate_deferred_termination_candidate(durable_id)

    assert conn.args == (durable_id,)
    assert "SET updated_at=NOW()" in conn.query
    assert "status='termination_pending'" in conn.query
    assert "billing_status='reserved'" in conn.query
    assert "terminal_settled_at IS NULL" in conn.query


def test_outbound_recovery_requires_side_effect_and_retry_outbox_proof():
    base = {
        "direction": "outbound",
        "status": "ended",
        "outcome": "cancelled",
        "ended_at": datetime.now(timezone.utc),
    }

    # An endpoint can win the terminal status before CallService has settled
    # lead/job/campaign effects. Terminal facts alone are therefore not proof.
    assert not lifecycle._is_recovery_row_logically_settled(base)

    settled = {**base, "terminal_settled_at": datetime.now(timezone.utc)}
    assert lifecycle._is_recovery_row_logically_settled(settled)

    retry_pending = {
        **settled,
        "terminal_retry_payload": {"job_id": "job-1"},
        "terminal_retry_enqueued_at": None,
    }
    assert not lifecycle._is_recovery_row_logically_settled(retry_pending)
    assert lifecycle._is_recovery_row_logically_settled(
        {
            **retry_pending,
            "terminal_retry_enqueued_at": datetime.now(timezone.utc),
        }
    )


@pytest.mark.asyncio
async def test_unconfirmed_orphan_is_retained_without_logical_end_then_retried(
    monkeypatch,
):
    ledger = _RetryLedger()
    adapter = _ConfirmationAdapter(False, True)
    ended: list[str] = []

    async def on_call_ended(call_id: str, **_kwargs) -> bool:
        ended.append(call_id)
        return True

    async def hydrate(call_id: str, entry: dict):
        return {
            "provider_session_id": call_id,
            "provider_leg_ids": [],
            "durable_call_id": None,
            "direction": "unknown",
            "provider": "asterisk",
            "duration_seconds": 0,
            "was_answered": True,
            "logical_settled": True,
            "ledger_entry": dict(entry),
        }

    lifecycle._orphan_recovery_in_flight.clear()
    lifecycle._ended_calls_in_flight.clear()
    lifecycle._ended_calls_logically_completed.clear()
    monkeypatch.setattr(lifecycle, "_state", lambda: ledger)
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: adapter)
    monkeypatch.setattr(lifecycle, "_on_call_ended", on_call_ended)
    monkeypatch.setattr(
        lifecycle,
        "_hydrate_orphan_recovery_context",
        hydrate,
    )

    assert await lifecycle.recover_orphaned_calls() == 0
    assert ledger.entries[0]["call_id"] == "orphan-1"
    assert ledger.acknowledged == []
    assert ended == []

    # The next watchdog pass sees the same durable entry and converges after
    # PBX confirmation. A later pass has nothing left to settle twice.
    assert await lifecycle.recover_orphaned_calls() == 1
    assert ledger.entries == []
    assert ledger.acknowledged == ["orphan-1"]
    assert ended == ["orphan-1"]
    assert adapter.attempts == ["orphan-1", "orphan-1"]
    assert await lifecycle.recover_orphaned_calls() == 0
    assert ended == ["orphan-1"]


@pytest.mark.asyncio
async def test_required_confirmation_without_adapter_never_settles(monkeypatch):
    ended: list[str] = []

    async def on_call_ended(call_id: str) -> None:
        ended.append(call_id)

    monkeypatch.setattr(lifecycle, "get_adapter", lambda: None)
    monkeypatch.setattr(lifecycle, "_on_call_ended", on_call_ended)

    assert (
        await lifecycle._force_end_and_hangup(
            "pbx-without-controller",
            require_confirmation=True,
        )
        is False
    )
    assert ended == []


@pytest.mark.asyncio
async def test_legacy_hangup_request_is_not_mistaken_for_required_proof(monkeypatch):
    ended: list[str] = []
    hangups: list[str] = []

    async def on_call_ended(call_id: str) -> None:
        ended.append(call_id)

    async def hangup(call_id: str) -> None:
        hangups.append(call_id)

    monkeypatch.setattr(
        lifecycle,
        "get_adapter",
        lambda: SimpleNamespace(hangup=hangup),
    )
    monkeypatch.setattr(lifecycle, "_on_call_ended", on_call_ended)

    assert (
        await lifecycle._force_end_and_hangup(
            "legacy-pbx",
            require_confirmation=True,
        )
        is False
    )
    assert hangups == ["legacy-pbx"]
    assert ended == []


@pytest.mark.asyncio
async def test_explicit_non_pbx_logical_only_teardown_is_preserved(monkeypatch):
    ended: list[str] = []

    async def on_call_ended(call_id: str) -> None:
        ended.append(call_id)

    monkeypatch.setattr(lifecycle, "get_adapter", lambda: None)
    monkeypatch.setattr(lifecycle, "_on_call_ended", on_call_ended)

    assert (
        await lifecycle._force_end_and_hangup(
            "browser-session",
            require_confirmation=False,
        )
        is True
    )
    assert ended == ["browser-session"]


@pytest.mark.asyncio
async def test_default_missing_adapter_never_runs_logical_teardown(monkeypatch):
    ended: list[str] = []

    async def on_call_ended(call_id: str) -> bool:
        ended.append(call_id)
        return True

    monkeypatch.setattr(lifecycle, "get_adapter", lambda: None)
    monkeypatch.setattr(lifecycle, "_on_call_ended", on_call_ended)

    assert await lifecycle._force_end_and_hangup("missing-adapter") is False
    assert ended == []


@pytest.mark.asyncio
async def test_default_legacy_adapter_request_never_becomes_settlement(monkeypatch):
    ended: list[str] = []
    requested: list[str] = []

    async def on_call_ended(call_id: str) -> bool:
        ended.append(call_id)
        return True

    async def hangup(call_id: str) -> None:
        requested.append(call_id)

    monkeypatch.setattr(
        lifecycle,
        "get_adapter",
        lambda: SimpleNamespace(hangup=hangup),
    )
    monkeypatch.setattr(lifecycle, "_on_call_ended", on_call_ended)

    assert await lifecycle._force_end_and_hangup("legacy-default") is False
    assert requested == ["legacy-default"]
    assert ended == []


@pytest.mark.asyncio
async def test_parent_only_confirmation_cannot_settle_persisted_transfer_leg(
    monkeypatch,
):
    ended = []
    parent_proofs = []

    class ParentOnlyAdapter:
        async def hangup_confirmed(self, call_id):
            parent_proofs.append(call_id)
            return True

    async def on_call_ended(call_id, **_kwargs):
        ended.append(call_id)
        return True

    monkeypatch.setattr(lifecycle, "get_adapter", lambda: ParentOnlyAdapter())
    monkeypatch.setattr(lifecycle, "_on_call_ended", on_call_ended)

    assert (
        await lifecycle._force_end_and_hangup(
            "parent-leg",
            provider_leg_ids=["talky-xfer-0000000000000000000d"],
        )
        is False
    )
    assert parent_proofs == []
    assert ended == []


@pytest.mark.asyncio
async def test_concurrent_recovery_skips_call_already_in_flight(monkeypatch):
    ledger = _RetryLedger()
    lifecycle._orphan_recovery_in_flight.clear()
    lifecycle._orphan_recovery_in_flight.add("orphan-1")
    monkeypatch.setattr(lifecycle, "_state", lambda: ledger)
    monkeypatch.setattr(
        lifecycle,
        "get_adapter",
        lambda: SimpleNamespace(hangup_confirmed=None),
    )

    assert await lifecycle.recover_orphaned_calls() == 0
    assert ledger.entries
    assert ledger.acknowledged == []
    lifecycle._orphan_recovery_in_flight.clear()


@pytest.mark.asyncio
async def test_inbound_orphan_runs_real_logical_teardown_before_ack(
    monkeypatch,
):
    """Recovery must exercise `_on_call_ended`, not a restart-only shortcut."""

    import app.core.container as container_module
    import app.domain.services.telephony.inbound_admission as admission_module
    import app.domain.services.telephony.inbound_transfer as transfer_module

    call_id = "inbound-recovery-real"
    durable_id = "11111111-1111-1111-1111-111111111111"
    tenant_id = "22222222-2222-2222-2222-222222222222"
    ledger = _RetryLedger([{"call_id": call_id, "pod_id": "dead", "state": "active"}])
    order: list[object] = []

    class Adapter:
        async def hangup_many_confirmed(self, call_ids):
            order.append(("pbx", tuple(call_ids)))
            return True

        async def hangup_confirmed(self, actual_call_id):
            order.append(("pbx", (actual_call_id,)))
            return True

        def pop_inbound_admission(self, actual_call_id):
            order.append(("admission_cache_pop", actual_call_id))

    async def hydrate(actual_call_id, entry):
        assert actual_call_id == call_id
        return {
            "provider_session_id": call_id,
            "provider_leg_ids": ["talky-xfer-durable-target"],
            "durable_call_id": durable_id,
            "tenant_id": tenant_id,
            "direction": "inbound",
            "provider": "asterisk",
            "provider_call_id": call_id,
            "duration_seconds": 37,
            "was_answered": True,
            "logical_settled": False,
            "ledger_entry": dict(entry),
            "admission": {
                "allowed": True,
                "call_id": durable_id,
                "tenant_id": tenant_id,
                "provider": "asterisk",
                "provider_call_id": call_id,
                "_terminal_reason": "process_restart_recovery",
            },
        }

    async def finalize(_self, request):
        order.append(("inbound_finalize", request))

    async def finalize_transfers(_pool, **kwargs):
        order.append(("transfer_finalize", kwargs["call_id"]))
        return 1

    async def no_pending():
        return []

    monkeypatch.setattr(lifecycle, "_state", lambda: ledger)
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: Adapter())
    monkeypatch.setattr(lifecycle, "_hydrate_orphan_recovery_context", hydrate)
    monkeypatch.setattr(lifecycle, "_load_termination_pending_candidates", no_pending)
    monkeypatch.setattr(lifecycle, "_release_ended_marker_later", lambda _cid: None)
    monkeypatch.setattr(admission_module.InboundAdmissionService, "finalize", finalize)
    monkeypatch.setattr(
        transfer_module,
        "finalize_connected_inbound_transfers",
        finalize_transfers,
    )
    monkeypatch.setattr(
        container_module,
        "get_container",
        lambda: SimpleNamespace(
            is_initialized=True,
            db_pool=object(),
            db_client=object(),
            redis=None,
        ),
    )
    lifecycle._orphan_recovery_in_flight.clear()
    lifecycle._ended_calls_in_flight.discard(call_id)
    lifecycle._ended_calls_logically_completed.discard(call_id)
    lifecycle._inbound_admissions_finalized.discard(("asterisk", call_id))
    try:
        assert await lifecycle.recover_orphaned_calls() == 1
        assert ledger.acknowledged == [call_id]
        assert order[0] == (
            "pbx",
            (call_id, "talky-xfer-durable-target"),
        )
        transfer_index = next(i for i, item in enumerate(order) if item[0] == "transfer_finalize")
        finalize_index = next(i for i, item in enumerate(order) if item[0] == "inbound_finalize")
        assert transfer_index < finalize_index
        request = order[finalize_index][1]
        assert request.call_id == durable_id
        assert request.duration_seconds == 37
        assert request.terminal_status == "completed"
    finally:
        lifecycle._orphan_recovery_in_flight.clear()
        lifecycle._ended_calls_in_flight.discard(call_id)
        lifecycle._ended_calls_logically_completed.discard(call_id)
        lifecycle._inbound_admissions_finalized.discard(("asterisk", call_id))


@pytest.mark.asyncio
async def test_adapter_terminal_callback_during_recovery_adopts_inbound_context(
    monkeypatch,
):
    """DELETE-triggered callbacks cannot race ahead and guess outbound."""

    import app.core.container as container_module
    import app.domain.services.global_concurrency as concurrency_module
    import app.domain.services.telephony.inbound_admission as admission_module
    import app.domain.services.telephony.inbound_transfer as transfer_module

    call_id = "recovery-callback-race"
    durable_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    tenant_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    ledger = _RetryLedger([{"call_id": call_id, "pod_id": "dead", "state": "active"}])
    finalizations = 0
    proof_results = [False, True]

    class Adapter:
        async def hangup_many_confirmed(self, actual_call_ids):
            assert actual_call_ids == [call_id, "durable-target-leg"]
            # Model StasisEnd being dispatched synchronously by the DELETE
            # before all-leg inventory proof returns.
            assert await lifecycle._on_call_ended(call_id) is False
            return proof_results.pop(0)

        def pop_inbound_admission(self, _call_id):
            return None

    async def hydrate(_call_id, entry):
        return {
            "provider_session_id": call_id,
            "provider_leg_ids": ["durable-target-leg"],
            "durable_call_id": durable_id,
            "tenant_id": tenant_id,
            "direction": "inbound",
            "provider": "asterisk",
            "provider_call_id": call_id,
            "duration_seconds": 14,
            "was_answered": True,
            "logical_settled": finalizations > 0,
            "ledger_entry": dict(entry),
            "admission": {
                "allowed": True,
                "call_id": durable_id,
                "tenant_id": tenant_id,
                "provider": "asterisk",
                "provider_call_id": call_id,
            },
        }

    async def finalize(_self, request):
        nonlocal finalizations
        assert request.call_id == durable_id
        finalizations += 1

    async def no_transfers(*_args, **_kwargs):
        return 0

    async def no_pending():
        return []

    async def release_global(*_args, **_kwargs):
        return True

    monkeypatch.setattr(lifecycle, "_state", lambda: ledger)
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: Adapter())
    monkeypatch.setattr(lifecycle, "_hydrate_orphan_recovery_context", hydrate)
    monkeypatch.setattr(lifecycle, "_load_termination_pending_candidates", no_pending)
    monkeypatch.setattr(lifecycle, "_release_ended_marker_later", lambda _cid: None)
    monkeypatch.setattr(admission_module.InboundAdmissionService, "finalize", finalize)
    monkeypatch.setattr(
        transfer_module,
        "finalize_connected_inbound_transfers",
        no_transfers,
    )
    monkeypatch.setattr(concurrency_module, "release_lease_strict", release_global)
    monkeypatch.setattr(
        container_module,
        "get_container",
        lambda: SimpleNamespace(
            is_initialized=True,
            db_pool=object(),
            db_client=object(),
            redis=None,
        ),
    )
    lifecycle._orphan_recovery_in_flight.clear()
    lifecycle._orphan_recovery_contexts_by_call.pop(call_id, None)
    lifecycle._ended_calls_in_flight.discard(call_id)
    lifecycle._ended_calls_logically_completed.discard(call_id)
    lifecycle._inbound_admissions_finalized.discard(("asterisk", call_id))
    try:
        # A synchronous terminal callback cannot settle anything until the
        # recovery coordinator proves every durable provider leg absent.
        assert await lifecycle.recover_orphaned_calls() == 0
        assert finalizations == 0
        assert ledger.acknowledged == []
        assert ledger.entries

        # The next pass obtains proof, then invokes the logical finalizer once
        # and acknowledges the durable retry ledger at the commit boundary.
        assert await lifecycle.recover_orphaned_calls() == 1
        assert finalizations == 1
        assert ledger.acknowledged == [call_id]
        assert call_id not in lifecycle._orphan_recovery_contexts_by_call
    finally:
        lifecycle._orphan_recovery_in_flight.clear()
        lifecycle._orphan_recovery_contexts_by_call.pop(call_id, None)
        lifecycle._ended_calls_in_flight.discard(call_id)
        lifecycle._ended_calls_logically_completed.discard(call_id)
        lifecycle._inbound_admissions_finalized.discard(("asterisk", call_id))


@pytest.mark.asyncio
async def test_database_only_terminal_reserved_inbound_settles_without_redis_ack(
    monkeypatch,
):
    """The <=30s DB scan closes a crash after PBX end but before billing."""

    import app.core.container as container_module
    import app.domain.services.global_concurrency as concurrency_module
    import app.domain.services.telephony.inbound_admission as admission_module
    import app.domain.services.telephony.inbound_transfer as transfer_module

    call_id = "terminal-reserved-inbound"
    durable_id = "88888888-8888-8888-8888-888888888888"
    tenant_id = "99999999-9999-9999-9999-999999999999"
    ledger = _RetryLedger([])
    finalized = []

    class Adapter:
        async def hangup_confirmed(self, actual_call_id):
            assert actual_call_id == call_id
            return True  # 404/inventory absence is valid proof.

        def pop_inbound_admission(self, _call_id):
            return None

    async def pending():
        return [
            {
                "call_id": call_id,
                "pod_id": "database:termination_pending",
                "tenant_id": tenant_id,
                "provider": "asterisk",
                "durable_call_id": durable_id,
                "_termination_pending": False,
                "_inbound_settlement_pending": True,
                "_has_redis_ledger": False,
            }
        ]

    async def hydrate(_call_id, entry):
        return {
            "provider_session_id": call_id,
            "provider_leg_ids": [],
            "durable_call_id": durable_id,
            "tenant_id": tenant_id,
            "direction": "inbound",
            "provider": "asterisk",
            "provider_call_id": call_id,
            "duration_seconds": 29,
            "was_answered": True,
            "logical_settled": False,
            "ledger_entry": dict(entry),
            "admission": {
                "allowed": True,
                "call_id": durable_id,
                "tenant_id": tenant_id,
                "provider": "asterisk",
                "provider_call_id": call_id,
            },
        }

    async def finalize(_self, request):
        finalized.append(request)

    async def no_transfers(*_args, **_kwargs):
        return 0

    async def release_global(*_args, **_kwargs):
        return True

    monkeypatch.setattr(lifecycle, "_state", lambda: ledger)
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: Adapter())
    monkeypatch.setattr(lifecycle, "_load_termination_pending_candidates", pending)
    monkeypatch.setattr(lifecycle, "_hydrate_orphan_recovery_context", hydrate)
    monkeypatch.setattr(lifecycle, "_release_ended_marker_later", lambda _cid: None)
    monkeypatch.setattr(admission_module.InboundAdmissionService, "finalize", finalize)
    monkeypatch.setattr(
        transfer_module,
        "finalize_connected_inbound_transfers",
        no_transfers,
    )
    monkeypatch.setattr(concurrency_module, "release_lease_strict", release_global)
    monkeypatch.setattr(
        container_module,
        "get_container",
        lambda: SimpleNamespace(
            is_initialized=True,
            db_pool=object(),
            db_client=object(),
            redis=None,
        ),
    )
    lifecycle._orphan_recovery_in_flight.clear()
    lifecycle._ended_calls_in_flight.discard(call_id)
    lifecycle._ended_calls_logically_completed.discard(call_id)
    lifecycle._inbound_admissions_finalized.discard(("asterisk", call_id))
    try:
        assert await lifecycle.recover_orphaned_calls() == 1
        assert ledger.acknowledged == []
        assert len(finalized) == 1
        assert finalized[0].call_id == durable_id
        assert finalized[0].duration_seconds == 29
        assert finalized[0].terminal_status == "completed"
    finally:
        lifecycle._orphan_recovery_in_flight.clear()
        lifecycle._ended_calls_in_flight.discard(call_id)
        lifecycle._ended_calls_logically_completed.discard(call_id)
        lifecycle._inbound_admissions_finalized.discard(("asterisk", call_id))


@pytest.mark.asyncio
async def test_recovered_preanswer_denial_uses_release_after_pbx_proof(monkeypatch):
    import app.core.container as container_module
    import app.domain.services.telephony.inbound_transfer as transfer_module

    call_id = "denied-before-answer"
    durable_id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    ledger = _RetryLedger([])
    finalizer_calls = []

    class Adapter:
        async def hangup_confirmed(self, _call_id):
            return True

    async def pending():
        return [
            {
                "call_id": call_id,
                "pod_id": "database:termination_pending",
                "_termination_pending": True,
                "_has_redis_ledger": False,
            }
        ]

    async def hydrate(_call_id, entry):
        return {
            "provider_session_id": call_id,
            "provider_leg_ids": [],
            "durable_call_id": durable_id,
            "direction": "inbound",
            "provider": "asterisk",
            "provider_call_id": call_id,
            "duration_seconds": 0,
            "was_answered": False,
            "logical_settled": False,
            "ledger_entry": dict(entry),
            "admission": {
                "allowed": True,  # routing marker; durable status is authority
                "admission_status": "denied",
                "call_id": durable_id,
                "provider": "asterisk",
                "provider_call_id": call_id,
            },
        }

    async def finalize(*args, **kwargs):
        finalizer_calls.append((args, kwargs))

    async def no_transfers(*_args, **_kwargs):
        return 0

    monkeypatch.setattr(lifecycle, "_state", lambda: ledger)
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: Adapter())
    monkeypatch.setattr(lifecycle, "_load_termination_pending_candidates", pending)
    monkeypatch.setattr(lifecycle, "_hydrate_orphan_recovery_context", hydrate)
    monkeypatch.setattr(lifecycle, "_finalize_inbound_admission", finalize)
    monkeypatch.setattr(lifecycle, "_release_ended_marker_later", lambda _cid: None)
    monkeypatch.setattr(
        transfer_module,
        "finalize_connected_inbound_transfers",
        no_transfers,
    )
    monkeypatch.setattr(
        container_module,
        "get_container",
        lambda: SimpleNamespace(
            is_initialized=True,
            db_pool=object(),
            db_client=object(),
            redis=None,
        ),
    )
    lifecycle._orphan_recovery_in_flight.clear()
    lifecycle._orphan_recovery_contexts_by_call.pop(call_id, None)
    lifecycle._ended_calls_in_flight.discard(call_id)
    lifecycle._ended_calls_logically_completed.discard(call_id)
    try:
        assert await lifecycle.recover_orphaned_calls() == 1
        assert len(finalizer_calls) == 1
        _, kwargs = finalizer_calls[0]
        assert kwargs["release_only"] is True
        assert kwargs["terminal_status"] == "failed"
        assert kwargs["duration_seconds"] == 0
    finally:
        lifecycle._orphan_recovery_in_flight.clear()
        lifecycle._orphan_recovery_contexts_by_call.pop(call_id, None)
        lifecycle._ended_calls_in_flight.discard(call_id)
        lifecycle._ended_calls_logically_completed.discard(call_id)


@pytest.mark.asyncio
async def test_inbound_settlement_failure_keeps_ledger_and_retries(monkeypatch):
    import app.core.container as container_module
    import app.domain.services.telephony.inbound_admission as admission_module
    import app.domain.services.telephony.inbound_transfer as transfer_module

    call_id = "inbound-recovery-retry"
    durable_id = "33333333-3333-3333-3333-333333333333"
    ledger = _RetryLedger([{"call_id": call_id, "pod_id": "dead", "state": "active"}])
    attempts = 0

    class Adapter:
        async def hangup_confirmed(self, _call_id):
            return True

        def pop_inbound_admission(self, _call_id):
            return None

    async def hydrate(_call_id, entry):
        return {
            "provider_session_id": call_id,
            "provider_leg_ids": [],
            "durable_call_id": durable_id,
            "tenant_id": "44444444-4444-4444-4444-444444444444",
            "direction": "inbound",
            "provider": "asterisk",
            "provider_call_id": call_id,
            "duration_seconds": 21,
            "was_answered": True,
            "logical_settled": False,
            "ledger_entry": dict(entry),
            "admission": {
                "allowed": True,
                "call_id": durable_id,
                "provider": "asterisk",
                "provider_call_id": call_id,
            },
        }

    async def finalize(_self, _request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("database commit failed")

    async def no_transfers(*_args, **_kwargs):
        return 0

    async def no_pending():
        return []

    monkeypatch.setattr(lifecycle, "_state", lambda: ledger)
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: Adapter())
    monkeypatch.setattr(lifecycle, "_hydrate_orphan_recovery_context", hydrate)
    monkeypatch.setattr(lifecycle, "_load_termination_pending_candidates", no_pending)
    monkeypatch.setattr(lifecycle, "_release_ended_marker_later", lambda _cid: None)
    monkeypatch.setattr(admission_module.InboundAdmissionService, "finalize", finalize)
    monkeypatch.setattr(
        transfer_module,
        "finalize_connected_inbound_transfers",
        no_transfers,
    )
    monkeypatch.setattr(
        container_module,
        "get_container",
        lambda: SimpleNamespace(
            is_initialized=True,
            db_pool=object(),
            db_client=object(),
            redis=None,
        ),
    )
    lifecycle._orphan_recovery_in_flight.clear()
    lifecycle._ended_calls_in_flight.discard(call_id)
    lifecycle._ended_calls_logically_completed.discard(call_id)
    lifecycle._inbound_admissions_finalized.discard(("asterisk", call_id))
    try:
        assert await lifecycle.recover_orphaned_calls() == 0
        assert ledger.entries
        assert ledger.acknowledged == []
        assert call_id not in lifecycle._ended_calls_in_flight

        assert await lifecycle.recover_orphaned_calls() == 1
        assert attempts == 2
        assert ledger.entries == []
        assert ledger.acknowledged == [call_id]
    finally:
        lifecycle._orphan_recovery_in_flight.clear()
        lifecycle._ended_calls_in_flight.discard(call_id)
        lifecycle._ended_calls_logically_completed.discard(call_id)
        lifecycle._inbound_admissions_finalized.discard(("asterisk", call_id))


@pytest.mark.asyncio
async def test_ack_failure_after_settlement_retries_without_double_finalizing(
    monkeypatch,
):
    import app.core.container as container_module
    import app.domain.services.global_concurrency as concurrency_module
    import app.domain.services.telephony.inbound_admission as admission_module
    import app.domain.services.telephony.inbound_transfer as transfer_module

    call_id = "crash-before-ledger-ack"
    durable_id = "55555555-5555-5555-5555-555555555555"

    class Ledger(_RetryLedger):
        def __init__(self):
            super().__init__([{"call_id": call_id, "pod_id": "dead"}])
            self.ack_attempts = 0

        async def acknowledge_orphan_recovery(self, actual_call_id):
            self.ack_attempts += 1
            if self.ack_attempts == 1:
                raise ConnectionError("redis failed after DB commit")
            await super().acknowledge_orphan_recovery(actual_call_id)

    ledger = Ledger()
    finalizations = 0
    transfer_finalizations = 0
    gateway_cleanups = 0

    def remove_gateway_sessions(_call_id):
        nonlocal gateway_cleanups
        gateway_cleanups += 1

    ledger.remove_gateway_sessions_for_call = remove_gateway_sessions

    class Adapter:
        async def hangup_confirmed(self, _call_id):
            return True

        def pop_inbound_admission(self, _call_id):
            return None

    async def hydrate(_call_id, entry):
        return {
            "provider_session_id": call_id,
            "provider_leg_ids": [],
            "durable_call_id": durable_id,
            "tenant_id": "77777777-7777-7777-7777-777777777777",
            "direction": "inbound",
            "provider": "asterisk",
            "provider_call_id": call_id,
            "duration_seconds": 18,
            "was_answered": True,
            # On retry PostgreSQL says settlement committed, while the
            # separate local completion marker proves the whole finalizer
            # (including linked-leg release) reached its commit boundary.
            "logical_settled": finalizations > 0,
            "ledger_entry": dict(entry),
            "admission": {
                "allowed": True,
                "call_id": durable_id,
                "provider": "asterisk",
                "provider_call_id": call_id,
            },
        }

    async def finalize(_self, request):
        nonlocal finalizations
        assert request.call_id == durable_id
        finalizations += 1

    async def finalize_transfers(_pool, **_kwargs):
        nonlocal transfer_finalizations
        transfer_finalizations += 1
        return 1

    async def release_global(*_args, **_kwargs):
        return True

    async def no_pending():
        return []

    monkeypatch.setattr(lifecycle, "_state", lambda: ledger)
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: Adapter())
    monkeypatch.setattr(lifecycle, "_hydrate_orphan_recovery_context", hydrate)
    monkeypatch.setattr(lifecycle, "_load_termination_pending_candidates", no_pending)
    monkeypatch.setattr(lifecycle, "_release_ended_marker_later", lambda _cid: None)
    monkeypatch.setattr(admission_module.InboundAdmissionService, "finalize", finalize)
    monkeypatch.setattr(
        transfer_module,
        "finalize_connected_inbound_transfers",
        finalize_transfers,
    )
    monkeypatch.setattr(concurrency_module, "release_lease_strict", release_global)
    monkeypatch.setattr(
        container_module,
        "get_container",
        lambda: SimpleNamespace(
            is_initialized=True,
            db_pool=object(),
            db_client=object(),
            redis=None,
        ),
    )
    lifecycle._orphan_recovery_in_flight.clear()
    lifecycle._ended_calls_in_flight.discard(call_id)
    lifecycle._ended_calls_logically_completed.discard(call_id)
    lifecycle._inbound_admissions_finalized.discard(("asterisk", call_id))
    try:
        assert await lifecycle.recover_orphaned_calls() == 0
        assert ledger.entries
        assert finalizations == 1
        assert transfer_finalizations == 1
        assert gateway_cleanups == 1

        assert await lifecycle.recover_orphaned_calls() == 1
        assert ledger.entries == []
        # The second pass executes real `_on_call_ended`, which recognizes the
        # completed marker and skips every logical side effect before acking.
        assert finalizations == 1
        assert transfer_finalizations == 1
        assert gateway_cleanups == 1
        assert ledger.ack_attempts == 2
    finally:
        lifecycle._orphan_recovery_in_flight.clear()
        lifecycle._ended_calls_in_flight.discard(call_id)
        lifecycle._ended_calls_logically_completed.discard(call_id)
        lifecycle._inbound_admissions_finalized.discard(("asterisk", call_id))


@pytest.mark.asyncio
async def test_duplicate_inflight_finalizer_cannot_be_acked_before_commit(
    monkeypatch,
):
    call_id = "existing-finalizer-race"
    ledger = _RetryLedger([{"call_id": call_id, "pod_id": "dead"}])

    class Adapter:
        async def hangup_confirmed(self, _call_id):
            return True

    async def hydrate(_call_id, entry):
        return {
            "provider_session_id": call_id,
            "provider_leg_ids": [],
            "durable_call_id": "66666666-6666-6666-6666-666666666666",
            "direction": "inbound",
            "provider": "asterisk",
            "duration_seconds": 10,
            "was_answered": True,
            # Model parent billing having committed while transfer lease
            # finalization is still running in the existing callback.
            "logical_settled": True,
            "ledger_entry": dict(entry),
            "admission": {"allowed": True},
        }

    async def no_pending():
        return []

    monkeypatch.setattr(lifecycle, "_state", lambda: ledger)
    monkeypatch.setattr(lifecycle, "get_adapter", lambda: Adapter())
    monkeypatch.setattr(lifecycle, "_hydrate_orphan_recovery_context", hydrate)
    monkeypatch.setattr(lifecycle, "_load_termination_pending_candidates", no_pending)
    lifecycle._orphan_recovery_in_flight.clear()
    lifecycle._ended_calls_in_flight.add(call_id)
    lifecycle._ended_calls_logically_completed.discard(call_id)
    try:
        assert await lifecycle.recover_orphaned_calls() == 0
        assert ledger.acknowledged == []
        assert call_id in lifecycle._ended_calls_in_flight

        lifecycle._ended_calls_logically_completed.add(call_id)
        assert await lifecycle.recover_orphaned_calls() == 1
        assert ledger.acknowledged == [call_id]
    finally:
        lifecycle._orphan_recovery_in_flight.clear()
        lifecycle._ended_calls_in_flight.discard(call_id)
        lifecycle._ended_calls_logically_completed.discard(call_id)
