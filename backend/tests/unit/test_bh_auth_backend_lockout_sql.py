"""auth-sessions-5: blocked attempts must not extend or escalate their own lock.

The lockout SQL is exercised against a real PostgreSQL (the clauses use
IS DISTINCT FROM / timestamptz). Opt in with BH_PG_DSN, e.g.
postgresql://postgres@127.0.0.1:55432/postgres, against a throwaway server;
the test creates and drops its own temporary table. Skipped otherwise.
"""
import os
from datetime import datetime, timedelta, timezone

import pytest

from app.core.security.lockout import (
    check_account_locked,
    get_consecutive_failures,
    record_login_attempt,
    seconds_until_unlocked,
)

DSN = os.environ.get("BH_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="BH_PG_DSN not set (needs a throwaway PostgreSQL)")

EMAIL = "victim@example.com"


@pytest.fixture
async def conn():
    import asyncpg
    c = await asyncpg.connect(DSN)
    # pg_temp shadows any real login_attempts table for this session.
    await c.execute("""
        CREATE TEMP TABLE login_attempts (
            id uuid DEFAULT gen_random_uuid() NOT NULL,
            email text NOT NULL, user_id uuid, ip_address text NOT NULL,
            user_agent text, success boolean NOT NULL, failure_reason text,
            created_at timestamptz DEFAULT now() NOT NULL)""")
    try:
        yield c
    finally:
        await c.close()


async def _row(conn, *, success=False, reason=None, age_s=0):
    await conn.execute(
        "INSERT INTO login_attempts(email,ip_address,success,failure_reason,created_at)"
        " VALUES($1,'203.0.113.1',$2,$3,$4)",
        EMAIL, success, reason, datetime.now(timezone.utc) - timedelta(seconds=age_s))


async def test_blocked_attempts_do_not_count_as_failures(conn):
    for i in range(5):
        await _row(conn, reason="wrong_password", age_s=300 - i)   # 5 real failures, 5 min ago
    assert await get_consecutive_failures(conn, EMAIL) == 5
    for _ in range(30):                                            # user mashes the button
        await record_login_attempt(conn, email=EMAIL, user_id=None, ip_address="203.0.113.1",
                                   success=False, failure_reason="account_locked")
    assert await get_consecutive_failures(conn, EMAIL) == 5        # still 5, not 35


async def test_lock_expires_on_schedule_despite_blocked_retries(conn):
    for i in range(5):
        await _row(conn, reason="wrong_password", age_s=120 - i)   # 60 s lock, failures 2 min old
    for _ in range(10):                                            # retries during the lock
        await _row(conn, reason="account_locked", age_s=0)
    assert await check_account_locked(conn, EMAIL) is None         # lock already over
    assert await seconds_until_unlocked(conn, EMAIL) == 0


async def test_lock_is_active_right_after_real_failures(conn):
    for i in range(5):
        await _row(conn, reason="wrong_password", age_s=5 - i)
    assert await check_account_locked(conn, EMAIL) is not None


async def test_null_failure_reason_still_counts(conn):
    for i in range(5):
        await _row(conn, reason=None, age_s=5 - i)
    assert await get_consecutive_failures(conn, EMAIL) == 5
    assert await check_account_locked(conn, EMAIL) is not None
