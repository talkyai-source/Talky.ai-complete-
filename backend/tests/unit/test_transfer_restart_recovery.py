"""PostgreSQL fallback claims for restart-orphaned inbound transfers."""

from __future__ import annotations

import pytest

from app.domain.services.telephony import transfer_restart_recovery as recovery


class _Context:
    def __init__(self, value) -> None:
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_args):
        return False


def _parent(**overrides):
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "tenant_id": "22222222-2222-2222-2222-222222222222",
        "provider": "asterisk",
        "provider_call_id": "inbound-parent-1",
        "status": "in_call",
        **overrides,
    }


class _ClaimConn:
    def __init__(self, *, parents=None, children=None) -> None:
        self.parents = list(parents if parents is not None else [_parent()])
        self.children = list(
            children
            if children is not None
            else [
                {
                    "id": "33333333-3333-3333-3333-333333333333",
                    "provider_leg_id": "talky-xfer-0123456789abcdefabcd",
                }
            ]
        )
        self.events: list[tuple[str, tuple]] = []
        self.claimed_ids: set[str] = set()

    async def fetch(self, query, *args):
        if "FROM calls c" in query:
            self.events.append(("parents", args))
            assert "FOR UPDATE OF c SKIP LOCKED" in query
            assert "c.status <> 'termination_pending'" in query
            assert "eligible_leg.billing_status='reserved'" in query
            assert "unusable_leg.provider_leg_id LIKE 'transfer-%'" in query
            assert "unusable_leg.provider_leg_id = COALESCE" in query
            excluded = set(args[1])
            return [
                row
                for row in self.parents
                if row["id"] not in self.claimed_ids
                and row["status"] != "termination_pending"
                and row["provider_call_id"] not in excluded
            ]
        if "FROM call_legs" in query:
            self.events.append(("children", args))
            assert "FOR UPDATE" in query
            assert args[0] == _parent()["id"]
            return list(self.children)
        raise AssertionError(query)

    async def fetchrow(self, query, *args):
        if "UPDATE calls" not in query:
            raise AssertionError(query)
        self.events.append(("update", args))
        assert "status='termination_pending'" in query
        call_id = args[0]
        if call_id in self.claimed_ids:
            return None
        self.claimed_ids.add(call_id)
        for row in self.parents:
            if row["id"] == call_id:
                row["status"] = "termination_pending"
        return {"id": call_id}


@pytest.mark.asyncio
async def test_claim_locks_children_and_fences_parent_before_return(monkeypatch):
    conn = _ClaimConn(
        children=[
            {
                "id": "33333333-3333-3333-3333-333333333333",
                "provider_leg_id": "talky-xfer-0123456789abcdefabcd",
            },
            {
                "id": "55555555-5555-5555-5555-555555555555",
                "provider_leg_id": "talky-xfer-fedcba9876543210abcd",
            },
        ]
    )
    acquired = []

    def acquire(pool, tenant_id, *, timeout):
        acquired.append((pool, tenant_id, timeout))
        return _Context(conn)

    pool = object()
    monkeypatch.setattr(recovery, "acquire_with_tenant", acquire)

    claims = await recovery.claim_inbound_transfer_takeovers(
        pool,
        exclusive_owner_confirmed=True,
        limit=900,
        timeout_s=99,
    )

    assert acquired == [(pool, None, 5.0)]
    assert [event[0] for event in conn.events] == ["parents", "children", "update"]
    assert conn.events[0][1][3] == 500
    assert len(claims) == 1
    claim = claims[0]
    assert claim.call_id == _parent()["id"]
    assert claim.tenant_id == _parent()["tenant_id"]
    assert claim.provider == "asterisk"
    assert claim.provider_call_id == "inbound-parent-1"
    assert claim.provider_leg_ids == (
        "talky-xfer-0123456789abcdefabcd",
        "talky-xfer-fedcba9876543210abcd",
    )
    assert claim.previous_status == "in_call"
    assert conn.parents[0]["status"] == "termination_pending"


@pytest.mark.asyncio
async def test_local_and_after_hours_exclusions_are_normalized_before_query(
    monkeypatch,
):
    conn = _ClaimConn(
        parents=[
            _parent(provider_call_id="live-ai"),
            _parent(
                id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                provider_call_id="live-after-hours",
            ),
        ]
    )
    monkeypatch.setattr(
        recovery,
        "acquire_with_tenant",
        lambda *_args, **_kwargs: _Context(conn),
    )

    claims = await recovery.claim_inbound_transfer_takeovers(
        object(),
        exclusive_owner_confirmed=True,
        excluded_provider_call_ids=(
            " live-ai ",
            "live-after-hours",
            "live-ai",
            "",
        ),
    )

    assert claims == []
    assert conn.events[0][1][1] == ["live-ai", "live-after-hours"]
    assert all(event[0] == "parents" for event in conn.events)


@pytest.mark.asyncio
async def test_duplicate_claim_is_idempotent(monkeypatch):
    conn = _ClaimConn()
    monkeypatch.setattr(
        recovery,
        "acquire_with_tenant",
        lambda *_args, **_kwargs: _Context(conn),
    )

    first = await recovery.claim_inbound_transfer_takeovers(
        object(),
        exclusive_owner_confirmed=True,
    )
    second = await recovery.claim_inbound_transfer_takeovers(
        object(),
        exclusive_owner_confirmed=True,
    )

    assert len(first) == 1
    assert second == []
    assert [event[0] for event in conn.events].count("update") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider_leg_id",
    [None, "", "   ", "transfer-legacy-placeholder", "bad\nchannel"],
)
async def test_unusable_child_identity_never_partially_claims_parent(
    monkeypatch,
    provider_leg_id,
):
    conn = _ClaimConn(
        children=[
            {
                "id": "33333333-3333-3333-3333-333333333333",
                "provider_leg_id": provider_leg_id,
            }
        ]
    )
    monkeypatch.setattr(
        recovery,
        "acquire_with_tenant",
        lambda *_args, **_kwargs: _Context(conn),
    )

    claims = await recovery.claim_inbound_transfer_takeovers(
        object(),
        exclusive_owner_confirmed=True,
    )

    assert claims == []
    assert "update" not in [event[0] for event in conn.events]
    assert conn.parents[0]["status"] == "in_call"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_call_id", [None, "", "   ", "bad\nparent"])
async def test_unusable_parent_identity_is_not_claimed(
    monkeypatch,
    provider_call_id,
):
    conn = _ClaimConn(parents=[_parent(provider_call_id=provider_call_id)])
    monkeypatch.setattr(
        recovery,
        "acquire_with_tenant",
        lambda *_args, **_kwargs: _Context(conn),
    )

    claims = await recovery.claim_inbound_transfer_takeovers(
        object(),
        exclusive_owner_confirmed=True,
    )

    assert claims == []
    assert "children" not in [event[0] for event in conn.events]
    assert "update" not in [event[0] for event in conn.events]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "child_ids",
    [
        ("inbound-parent-1",),
        (
            "talky-xfer-0123456789abcdefabcd",
            "talky-xfer-0123456789abcdefabcd",
        ),
    ],
)
async def test_aliased_or_duplicate_child_identity_is_not_claimed(
    monkeypatch,
    child_ids,
):
    conn = _ClaimConn(
        children=[
            {
                "id": f"33333333-3333-3333-3333-{index:012d}",
                "provider_leg_id": provider_leg_id,
            }
            for index, provider_leg_id in enumerate(child_ids, start=1)
        ]
    )
    monkeypatch.setattr(
        recovery,
        "acquire_with_tenant",
        lambda *_args, **_kwargs: _Context(conn),
    )

    claims = await recovery.claim_inbound_transfer_takeovers(
        object(),
        exclusive_owner_confirmed=True,
    )

    assert claims == []
    assert "update" not in [event[0] for event in conn.events]
    assert conn.parents[0]["status"] == "in_call"


@pytest.mark.asyncio
async def test_explicit_exclusive_owner_proof_is_required(monkeypatch):
    async def unexpected_acquire(*_args, **_kwargs):
        raise AssertionError("database must not be touched without ownership")

    monkeypatch.setattr(recovery, "acquire_with_tenant", unexpected_acquire)

    with pytest.raises(RuntimeError, match="exclusive telephony ownership"):
        await recovery.claim_inbound_transfer_takeovers(
            object(),
            exclusive_owner_confirmed=False,
        )
