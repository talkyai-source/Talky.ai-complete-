"""QA F1-15 / D4-2: pending team invites in Redis.

Owner decisions, 11 Nov 2026: invites last 7 days; inviting the same email
again issues a new link. Only a hash of the token is stored.
"""
from datetime import datetime, timedelta, timezone

import fakeredis
import fakeredis.aioredis
import pytest

from app.domain.services import team_invites

TENANT = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
ADMIN = "33333333-3333-4333-8333-333333333333"
NOW = datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc)


@pytest.fixture(params=[True, False], ids=["str", "bytes"])
def redis(request):
    return fakeredis.aioredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=request.param)


async def invite(redis, email="sam@example.test", tenant=TENANT, now=NOW):
    return await team_invites.issue(redis, tenant_id=tenant, email=email, role_name="agent",
                                    invited_by=ADMIN, now=now)


async def test_an_invite_is_found_by_its_token_and_listed_for_its_workspace_only(redis):
    sent, token, previous = await invite(redis)
    assert previous is None
    assert await team_invites.by_token(redis, token, now=NOW) == sent
    assert await team_invites.pending(redis, TENANT, now=NOW) == [sent]
    assert await team_invites.pending(redis, OTHER, now=NOW) == []
    assert sent.expires_at == (NOW + timedelta(days=7)).isoformat()


async def test_only_a_hash_of_the_token_is_stored(redis):
    _, token, _ = await invite(redis)
    stored = []
    for key in await redis.keys("*"):
        stored.append(str(key))
        value = await redis.get(key) if await redis.type(key) in ("string", b"string") else await redis.hgetall(key)
        stored.append(str(value))
    assert token not in " ".join(stored)


async def test_inviting_again_replaces_the_earlier_link(redis):
    first, first_token, _ = await invite(redis)
    second, second_token, previous = await invite(redis, now=NOW + timedelta(hours=1))
    assert previous == first
    assert await team_invites.by_token(redis, first_token, now=NOW) is None
    assert await team_invites.by_token(redis, second_token, now=NOW) == second
    assert await team_invites.pending(redis, TENANT, now=NOW) == [second]


async def test_a_withdrawn_invite_stops_working_at_once(redis):
    sent, token, _ = await invite(redis)
    assert await team_invites.withdraw(redis, sent) is True
    assert await team_invites.by_token(redis, token, now=NOW) is None
    assert await team_invites.pending(redis, TENANT, now=NOW) == []
    assert await team_invites.find(redis, TENANT, sent.id, now=NOW) is None


async def test_a_token_is_spent_only_once(redis):
    sent, _, _ = await invite(redis)
    assert await team_invites.spend(redis, sent) is True
    assert await team_invites.spend(redis, sent) is False


async def test_an_invite_expires_after_seven_days(redis):
    sent, token, _ = await invite(redis)
    later = NOW + timedelta(days=7, seconds=1)
    assert await team_invites.by_token(redis, token, now=NOW + timedelta(days=6)) == sent
    assert await team_invites.by_token(redis, token, now=later) is None
    assert await team_invites.pending(redis, TENANT, now=later) == []


async def test_invites_for_different_emails_are_independent_and_newest_first(redis):
    first, _, _ = await invite(redis, email="a@example.test")
    second, _, previous = await invite(redis, email="b@example.test", now=NOW + timedelta(minutes=5))
    assert previous is None
    assert await team_invites.pending(redis, TENANT, now=NOW) == [second, first]


async def test_the_per_person_workspace_cap_resets_each_day_however_busy(redis):
    # Security review: a sliding expiry let ten busy workspaces keep the
    # set alive for ever. Each UTC day starts empty.
    email = "sam@example.test"
    for n in range(10):
        assert await team_invites.admit_workspace(redis, email, f"ws-{n}", limit=10, now=NOW) is True
    assert await team_invites.admit_workspace(redis, email, "ws-late", limit=10, now=NOW) is False
    assert await team_invites.admit_workspace(redis, email, "ws-0", limit=10, now=NOW + timedelta(hours=10)) is True
    assert await team_invites.admit_workspace(redis, email, "ws-late", limit=10, now=NOW + timedelta(days=1)) is True
