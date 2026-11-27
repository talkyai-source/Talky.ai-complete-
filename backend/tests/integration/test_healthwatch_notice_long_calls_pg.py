"""healthwatch `recordings` statement against real PostgreSQL (CI only: needs a disposable *_test database).

Proves the SQL of scripts/healthwatch.py `_RECORDINGS_SQL`: answered_long and
unconfirmed_long count calls where the person stayed on 30 s or more
(ended_at - answered_at), voicemails and never-answered calls stay out; the answered
denominators leave out recordings that need no notice; the *_recent counts cover 4 hours.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio

from app.core.db_utils import acquire_with_tenant
from tests.integration.test_billing_price_options import billing_db  # noqa: F401

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[2]


def _healthwatch():
    spec = importlib.util.spec_from_file_location("healthwatch_pg", BACKEND / "scripts/healthwatch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# (label, answered, seconds from answer to end (None = ended_at NULL, negative = ended before
# answered), outcome, notice_state, hours since the recording was created)
CALLS = [
    ("short_unconfirmed", True, 5, "completed", "unconfirmed", 0),
    ("long_unconfirmed", True, 45, "completed", "unconfirmed", 0),
    ("long_delivered", True, 45, "completed", "delivered", 0),
    ("long_voicemail", True, 45, "voicemail", "unconfirmed", 0),
    ("never_answered", False, 3, "no_answer", "unconfirmed", 0),
    ("s29_unconfirmed", True, 29, "completed", "unconfirmed", 0),
    ("s30_unconfirmed", True, 30, "completed", "unconfirmed", 0),
    ("s31_unconfirmed", True, 31, "completed", "unconfirmed", 0),
    ("no_end_unconfirmed", True, None, "completed", "unconfirmed", 0),
    ("ended_before_answer", True, -10, "completed", "unconfirmed", 0),
    ("long_not_required", True, 45, "completed", "not_required", 0),
    ("old_unconfirmed", True, 45, "completed", "unconfirmed", 5),
]


@pytest_asyncio.fixture
async def hw_db(billing_db):  # noqa: F811 - imported pytest fixture
    fixture = billing_db
    tenant = fixture.tenants[0]
    suffix = uuid4().hex[:10]
    fixture.call_ids = []
    fixture.campaign = uuid4()
    async with acquire_with_tenant(fixture.pool, None) as conn:
        # An outbound call must point at a campaign of its own tenant.
        await conn.execute(
            "INSERT INTO campaigns(id,tenant_id,name,status,direction) VALUES($1,$2,'Synthetic HW','running','outbound')",
            fixture.campaign, tenant,
        )
        for label, answered, seconds, outcome, notice, hours_old in CALLS:
            call_id = uuid4()
            fixture.call_ids.append(call_id)
            await conn.execute(
                """INSERT INTO calls(id,tenant_id,campaign_id,phone_number,status,outcome,direction,is_test,
                   answered_at,ended_at)
                   VALUES($1,$2,$5,'+15550100001','completed',$3,'outbound',false,
                   CASE WHEN $6::boolean THEN now() - interval '2 hours' END,
                   CASE WHEN $4::int IS NULL THEN NULL
                        ELSE now() - interval '2 hours' + make_interval(secs => $4::int) END)""",
                call_id, tenant, outcome, seconds, fixture.campaign, answered,
            )
            await conn.execute(
                """INSERT INTO recordings_s3(call_id,tenant_id,s3_bucket,s3_key,status,notice_state,created_at)
                   VALUES($1,$2,'dummy-bucket',$3,'uploaded',$4, now() - make_interval(hours => $5::int))""",
                call_id, tenant, f"hw-pg/{suffix}/{label}", notice, hours_old,
            )
    try:
        yield fixture
    finally:
        async with acquire_with_tenant(fixture.pool, None) as conn:
            await conn.execute("DELETE FROM recordings_s3 WHERE call_id=ANY($1::uuid[])", fixture.call_ids)
            await conn.execute("DELETE FROM calls WHERE id=ANY($1::uuid[])", fixture.call_ids)
            await conn.execute("DELETE FROM campaigns WHERE id=$1", fixture.campaign)


async def test_long_call_counts_use_ended_minus_answered(hw_db):
    watch = _healthwatch()
    tenant = str(hw_db.tenants[0])
    async with acquire_with_tenant(hw_db.pool, tenant) as conn:
        row = await conn.fetchrow(watch._RECORDINGS_SQL, tenant)
    assert row["recorded"] == 12
    assert row["unconfirmed"] == 10  # every unconfirmed recording, voicemail and never-answered included
    # picked up, not a machine, and a notice was needed: not the voicemail, the never-answered call
    # or the not_required one. Includes the call with no end and the one that ended before it began.
    assert row["recorded_answered"] == 9
    assert row["unconfirmed_answered"] == 8
    # 30 s or more from answer to end: 30 s and 31 s count, 29 s does not, nor do the call with no
    # end or the one ended before it began; not_required stays out of the denominator.
    assert row["answered_long"] == 5   # long_unconfirmed, long_delivered, s30, s31, old_unconfirmed
    assert row["unconfirmed_long"] == 4
    # last 4 hours: the same answered set without the recording created 5 hours ago
    assert row["answered_recent"] == 8
    assert row["unconfirmed_recent"] == 7
